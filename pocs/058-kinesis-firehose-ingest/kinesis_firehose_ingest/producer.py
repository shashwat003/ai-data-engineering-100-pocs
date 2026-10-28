"""Kinesis producer: batching limits, partial-failure retry with backoff, oversize rejection."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_RECORDS = 500
MAX_RECORD_BYTES = 1_000_000
MAX_BATCH_BYTES = 5_000_000


@dataclass
class SendStats:
    sent: int = 0
    retries: int = 0
    rejected_oversize: list[str] = field(default_factory=list)
    gave_up: list[str] = field(default_factory=list)


class Producer:
    def __init__(
        self,
        client,
        stream: str,
        *,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client, self.stream, self.max_attempts, self.sleep = (
            client,
            stream,
            max_attempts,
            sleep,
        )

    def send(self, events: list[dict]) -> SendStats:
        st = SendStats()
        batch: list[dict] = []
        size = 0
        for e in events:
            data = (json.dumps(e, separators=(",", ":")) + "\n").encode()
            if len(data) > MAX_RECORD_BYTES:
                st.rejected_oversize.append(e.get("event_id", "?"))
                continue
            if len(batch) >= MAX_RECORDS or size + len(data) > MAX_BATCH_BYTES:
                self._flush(batch, st)
                batch, size = [], 0
            batch.append({"Data": data, "PartitionKey": str(e["user_id"]), "_id": e["event_id"]})
            size += len(data)
        if batch:
            self._flush(batch, st)
        return st

    def _flush(self, batch: list[dict], st: SendStats) -> None:
        pending = batch
        for attempt in range(self.max_attempts):
            resp = self.client.put_records(
                StreamName=self.stream,
                Records=[{"Data": r["Data"], "PartitionKey": r["PartitionKey"]} for r in pending],
            )
            failed = [
                r for r, res in zip(pending, resp["Records"], strict=True) if "ErrorCode" in res
            ]
            st.sent += len(pending) - len(failed)
            if not failed:
                return
            st.retries += len(failed)
            pending = failed  # only the failed records are retried; resending the whole batch would duplicate the rest
            self.sleep(min(2.0, 0.05 * 2**attempt))
        st.gave_up += [r["_id"] for r in pending]
