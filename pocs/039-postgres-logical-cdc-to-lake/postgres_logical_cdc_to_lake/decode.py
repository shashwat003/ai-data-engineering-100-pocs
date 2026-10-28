"""Parse PostgreSQL `test_decoding` output into typed change events and Debezium-style envelopes.

test_decoding lines look like:
  table public.orders: INSERT: id[integer]:1 status[text]:'new' amount[numeric]:10.50 note[text]:null
  table public.orders: UPDATE: old-key: id[integer]:1 ... new-tuple: id[integer]:1 ...
  table public.orders: DELETE: id[integer]:2
This is a deliberately small parser for the scalar types in the demo; anything it cannot parse raises UnsupportedChange
(it never guesses), which makes the consumer stop instead of corrupting the lake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

COL = re.compile(r"""([A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]:""")
TABLE = re.compile(r"^table ([\w.]+): (INSERT|UPDATE|DELETE): (.*)$", re.S)
OPS = {"INSERT": "c", "UPDATE": "u", "DELETE": "d"}


class UnsupportedChange(ValueError):
    pass


@dataclass(frozen=True)
class Change:
    table: str
    op: str  # c | u | d | r (snapshot read)
    before: dict | None
    after: dict | None
    types: dict = field(default_factory=dict, compare=False)


def _scalar(text: str, i: int, typ: str):
    """Parse one value starting at text[i]; return (value, next index)."""
    if text[i] == "'":
        j, out = i + 1, []
        while True:
            k = text.find("'", j)
            if k < 0:
                raise UnsupportedChange("unterminated string")
            if text[k : k + 2] == "''":
                out.append(text[j : k + 1])
                j = k + 2
                continue
            out.append(text[j:k])
            return "".join(out), k + 1
    m = re.compile(r"[^ ]+").match(text, i)
    tok = m.group(0)
    if tok == "null":
        return None, m.end()
    if tok == "unchanged-toast-datum":
        raise UnsupportedChange(
            "unchanged TOAST datum (use REPLICA IDENTITY FULL or avoid large values)"
        )
    return tok, m.end()


def _convert(v, typ: str):
    if v is None:
        return None
    if typ in ("integer", "bigint", "smallint"):
        return int(v)
    if typ == "numeric":
        return Decimal(v)
    if typ == "boolean":
        return v == "true"
    if typ in ("double precision", "real"):
        return float(v)
    if typ in ("text", "character varying", "character", "date") or typ.startswith("timestamp"):
        return v
    raise UnsupportedChange(f"unsupported column type {typ!r}")


def parse_tuple(text: str) -> tuple[dict, dict]:
    vals, types, i = {}, {}, 0
    text = text.strip()
    while i < len(text):
        m = COL.match(text, i)
        if not m:
            raise UnsupportedChange(f"cannot parse near {text[i : i + 30]!r}")
        name, typ = m.group(1), m.group(2)
        raw, i = _scalar(text, m.end(), typ)
        vals[name], types[name] = _convert(raw, typ), typ
        while i < len(text) and text[i] == " ":
            i += 1
    return vals, types


def parse_change(data: str) -> Change:
    m = TABLE.match(data)
    if not m:
        raise UnsupportedChange(f"not a row change: {data[:60]!r}")
    table, verb, rest = m.groups()
    op = OPS[verb]
    if verb == "INSERT":
        after, types = parse_tuple(rest)
        return Change(table, op, None, after, types)
    if verb == "DELETE":
        before, types = parse_tuple(rest)
        return Change(table, op, before, None, types)
    if (
        "new-tuple:" not in rest
    ):  # key unchanged and default replica identity: only the new tuple is logged
        after, types = parse_tuple(rest)
        return Change(table, op, None, after, types)
    old_part, new_part = rest.split("new-tuple:", 1)
    before = None
    types = {}
    if old_part.strip().startswith("old-key:"):
        before, types = parse_tuple(old_part.strip()[len("old-key:") :])
    after, t2 = parse_tuple(new_part)
    return Change(table, op, before, after, {**types, **t2})


def envelope(ch: Change, *, lsn: int, xid: int, ordinal: int, commit_ts: str) -> dict:
    """A Debezium-shaped event (before/after/source/op), so downstream code does not care where it came from."""
    return {
        "op": ch.op,
        "before": ch.before,
        "after": ch.after,
        "source": {
            "db": "postgres",
            "table": ch.table,
            "lsn": lsn,
            "txId": xid,
            "ordinal": ordinal,
        },
        "ts": commit_ts,
    }
