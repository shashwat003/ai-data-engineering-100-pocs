"""Checkpointing Kinesis consumer with an idempotent S3 sink (at-least-once reads, effectively-once writes)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from botocore.exceptions import ClientError

LATE_AFTER_SECONDS = 3600


class Crash(RuntimeError):
    """Injected failure used to prove checkpoint/replay behaviour."""


def hour_prefix(ts: str) -> str:
    d = datetime.fromisoformat(ts)
    return f"dt={d:%Y-%m-%d}/hh={d:%H}"


class Consumer:
    def __init__(
        self,
        kinesis,
        ddb,
        s3,
        *,
        stream: str,
        table: str,
        bucket: str,
        group: str = "g1",
        batch_limit: int = 100,
    ):
        self.k, self.ddb, self.s3 = kinesis, ddb, s3
        self.stream, self.table, self.bucket, self.group, self.limit = (
            stream,
            table,
            bucket,
            group,
            batch_limit,
        )

    def _checkpoint(self, shard: str) -> tuple[str | None, str | None]:
        r = self.ddb.get_item(
            TableName=self.table, Key={"pk": {"S": f"{self.group}/{self.stream}/{shard}"}}
        ).get("Item")
        return (r["seq"]["S"], r["wm"]["S"]) if r else (None, None)

    def _commit(self, shard: str, seq: str, wm: str | None) -> None:
        item = {"pk": {"S": f"{self.group}/{self.stream}/{shard}"}, "seq": {"S": seq}}
        if wm:
            item["wm"] = {"S": wm}
        self.ddb.put_item(TableName=self.table, Item=item)

    def _write(self, shard: str, records: list[dict], wm: str | None) -> str | None:
        """Group by event-time hour; key derives from the shard and the batch's first/last sequence, so a replay overwrites, not duplicates.

        A record is late when it is more than an hour older than the watermark (max event time seen so far, persisted with the
        checkpoint), which keeps the classification identical on replay. Returns the new watermark.
        """
        by_hour: dict[str, list[dict]] = {}
        mark = datetime.fromisoformat(wm) if wm else None
        for r in records:
            t = datetime.fromisoformat(r["ts"])
            mark = t if mark is None or t > mark else mark
            late = (mark - t).total_seconds() > LATE_AFTER_SECONDS
            by_hour.setdefault(("late/" if late else "events/") + hour_prefix(r["ts"]), []).append(
                r
            )
        first, last = records[0]["_seq"], records[-1]["_seq"]
        for prefix, recs in by_hour.items():
            body = "".join(
                json.dumps({k: v for k, v in r.items() if k != "_seq"}, separators=(",", ":"))
                + "\n"
                for r in recs
            )
            self.s3.put_object(
                Bucket=self.bucket, Key=f"{prefix}/{shard}-{first}-{last}.jsonl", Body=body.encode()
            )
        return mark.isoformat() if mark else wm

    def poll(
        self, *, crash_before_commit: Callable[[int], bool] | None = None, max_batches: int = 1000
    ) -> dict:
        stats = {"records": 0, "batches": 0, "poison_skipped": 0}
        for shard in [s["ShardId"] for s in self.k.list_shards(StreamName=self.stream)["Shards"]]:
            seq, wm = self._checkpoint(shard)
            kw = (
                {"ShardIteratorType": "AFTER_SEQUENCE_NUMBER", "StartingSequenceNumber": seq}
                if seq
                else {"ShardIteratorType": "TRIM_HORIZON"}
            )
            it = self.k.get_shard_iterator(StreamName=self.stream, ShardId=shard, **kw)[
                "ShardIterator"
            ]
            for _ in range(max_batches):
                try:
                    resp = self.k.get_records(ShardIterator=it, Limit=self.limit)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ExpiredIteratorException":
                        break  # restart from the checkpoint on the next poll
                    raise
                recs = []
                for r in resp["Records"]:
                    try:
                        ev = json.loads(r["Data"])
                        datetime.fromisoformat(ev["ts"])
                        ev["_seq"] = r["SequenceNumber"]
                        recs.append(ev)
                    except (ValueError, KeyError, TypeError):
                        stats["poison_skipped"] += (
                            1  # unparseable record: counted, not allowed to block the shard
                        )
                new_wm = self._write(shard, recs, wm) if recs else wm
                if resp["Records"]:
                    stats["batches"] += 1
                    stats["records"] += len(resp["Records"])
                    if crash_before_commit and crash_before_commit(stats["batches"]):
                        raise Crash("injected crash after sink write, before checkpoint")
                    self._commit(shard, resp["Records"][-1]["SequenceNumber"], new_wm)
                    wm = new_wm
                it = resp.get("NextShardIterator")
                if not resp["Records"] and not resp.get("MillisBehindLatest"):
                    break
                if not it:
                    break
        return stats
