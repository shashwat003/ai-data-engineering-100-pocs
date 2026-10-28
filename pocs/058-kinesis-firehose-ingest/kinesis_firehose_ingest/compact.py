"""Compact many small JSONL objects into one object per prefix, dropping duplicate event_ids (Firehose/Kinesis are at-least-once)."""

from __future__ import annotations

import json

from .consumer import hour_prefix


def list_prefix(s3, bucket: str, prefix: str) -> list[str]:
    out, token = [], None
    while True:
        r = s3.list_objects_v2(
            Bucket=bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {})
        )
        out += [o["Key"] for o in r.get("Contents", [])]
        token = r.get("NextContinuationToken")
        if not token:
            return sorted(out)


def read_events(s3, bucket: str, keys: list[str]) -> list[dict]:
    ev = []
    for k in keys:
        for ln in s3.get_object(Bucket=bucket, Key=k)["Body"].read().decode().splitlines():
            if ln.strip():
                ev.append(json.loads(ln))
    return ev


def compact(s3, bucket: str, src_prefix: str, dst_prefix: str) -> dict:
    """Merge every object under src_prefix into one object per EVENT-time hour (dt=/hh=), dropping duplicate event_ids.

    Sources are deleted only after every destination write succeeded.
    """
    keys = list_prefix(s3, bucket, src_prefix)
    events = read_events(s3, bucket, keys)
    parts: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for e in sorted(events, key=lambda e: (e["ts"], e["event_id"])):
        if e["event_id"] in seen:
            continue
        seen.add(e["event_id"])
        parts.setdefault(hour_prefix(e["ts"]), []).append(e)
    for part, es in parts.items():
        s3.put_object(
            Bucket=bucket,
            Key=f"{dst_prefix}/{part}/part-0.jsonl",
            Body="".join(json.dumps(e, separators=(",", ":")) + "\n" for e in es).encode(),
        )
    for k in keys:
        s3.delete_object(Bucket=bucket, Key=k)
    return {
        "objects_before": len(keys),
        "objects_after": len(parts),
        "events_in": len(events),
        "events_out": len(seen),
        "duplicates_dropped": len(events) - len(seen),
    }
