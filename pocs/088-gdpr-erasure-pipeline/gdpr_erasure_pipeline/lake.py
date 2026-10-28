"""Delta Lake (delta-rs) events table: erase a data subject, physically (DELETE + VACUUM) and verifiably."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from deltalake import DeltaTable, write_deltalake


def create(path: Path, rows: list[dict], batches: int = 6) -> DeltaTable:
    """Write rows in several appends (so several table versions and files exist), then compact."""
    size = -(-len(rows) // batches)
    for i in range(0, len(rows), size):
        write_deltalake(str(path), pa.Table.from_pylist(rows[i : i + size]), mode="append")
    dt = DeltaTable(str(path))
    dt.optimize.compact()  # rewrites files: old versions keep the previous files until VACUUM
    return DeltaTable(str(path))


def erase_subject(path: Path, user_id: str, vacuum: bool = True) -> dict:
    dt = DeltaTable(str(path))
    if not user_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError(
            "unsafe subject id"
        )  # the id is interpolated into a predicate, so allow only simple tokens
    res = dt.delete(f"user_id = '{user_id}'")
    removed_files = 0
    if vacuum:
        dt = DeltaTable(str(path))
        removed_files = len(
            dt.vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)
        )
    return {"rows_deleted": res["num_deleted_rows"], "files_vacuumed": removed_files}


def count(path: Path, user_id: str | None = None) -> int:
    t = DeltaTable(str(path)).to_pyarrow_table()
    return (
        t.num_rows
        if user_id is None
        else sum(1 for v in t.column("user_id").to_pylist() if v == user_id)
    )


def residue(path: Path, needles: list[str]) -> list[tuple[str, str]]:
    """Look for the needles in EVERY parquet file under the table directory, referenced by the current version or not."""
    found = []
    for f in sorted(Path(path).rglob("*.parquet")):
        tbl = pq.read_table(f)
        for col in tbl.column_names:
            if not pa.types.is_string(tbl.schema.field(col).type):
                continue
            vals = {v for v in tbl.column(col).to_pylist() if v}
            for n in needles:
                if any(n in v for v in vals):
                    found.append((str(f.relative_to(path)), n))
    return found


def time_travel_exposes(path: Path, user_id: str) -> list[int]:
    """Versions of the table that can still be read and still contain the subject."""
    dt = DeltaTable(str(path))
    out = []
    for v in range(dt.version() + 1):
        try:
            t = DeltaTable(str(path), version=v).to_pyarrow_table()
        except Exception:  # noqa: BLE001 (a version whose files were vacuumed cannot be read; that is the point)
            continue
        if user_id in t.column("user_id").to_pylist():
            out.append(v)
    return out
