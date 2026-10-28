"""POC 075: an ETL run on an isolated branch, validated before it can reach main."""

from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

from pocs_common import write_json

from .repo import DataRepo, HookRejected, MergeConflict

POC_DIR = Path(__file__).resolve().parent.parent


def csv_bytes(rows: list[dict]) -> bytes:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return out.getvalue().encode()


def validate_orders(files: dict[str, bytes]) -> None:
    rows = list(csv.DictReader(io.StringIO(files["orders/2026-03.csv"].decode())))
    bad = [r for r in rows if float(r["amount"]) < 0]
    if bad:
        raise ValueError(f"{len(bad)} orders with negative amount")


def run() -> dict:
    res: dict = {}
    with tempfile.TemporaryDirectory() as t:
        tick = iter(range(1, 1000))
        repo = DataRepo(Path(t) / "repo", clock=lambda: f"2026-03-10T00:{next(tick):02d}:00Z")
        good = [{"order_id": i, "amount": 10 + i} for i in range(1, 101)]
        repo.commit(
            "main",
            {"orders/2026-03.csv": csv_bytes(good), "ref/countries.csv": b"code\nUS\nDE\n"},
            "load March orders",
        )
        v1 = repo.head("main").id
        # 1) a bad ETL run on a branch is rejected and main is untouched
        repo.create_branch("etl/run-1")
        bad = good[:50] + [{"order_id": 51, "amount": -5}] + good[51:]
        repo.commit("etl/run-1", {"orders/2026-03.csv": csv_bytes(bad)}, "etl run 1")
        try:
            repo.merge("etl/run-1", "main", pre_merge=validate_orders)
            res["bad_run_merged"] = True
        except HookRejected:
            res["bad_run_merged"] = False
        res["main_unchanged_after_rejection"] = repo.head("main").id == v1
        # 2) a corrected run merges (fast-forward) and gives a new version; old version still readable
        fixed = [{**r, "amount": abs(r["amount"])} for r in bad]
        repo.commit("etl/run-1", {"orders/2026-03.csv": csv_bytes(fixed)}, "etl run 1 (fixed)")
        m = repo.merge("etl/run-1", "main", pre_merge=validate_orders)
        res["fast_forward"] = m.id == repo.head("etl/run-1").id
        res["time_travel_old_version_readable"] = repo.read(v1, "orders/2026-03.csv") == csv_bytes(
            good
        )
        # 3) two branches touching different files merge cleanly; same file conflicts
        repo.create_branch("feat/countries")
        repo.create_branch("feat/orders-extra")
        repo.commit("feat/countries", {"ref/countries.csv": b"code\nUS\nDE\nIN\n"}, "add IN")
        repo.commit("feat/orders-extra", {"orders/2026-04.csv": csv_bytes(good[:10])}, "add April")
        repo.merge("feat/countries", "main")
        c = repo.merge("feat/orders-extra", "main")
        res["three_way_merge_parents"] = len(c.parents)
        repo.create_branch("a")
        repo.create_branch("b")
        repo.commit("a", {"ref/countries.csv": b"code\nUS\n"}, "a edits")
        repo.commit("b", {"ref/countries.csv": b"code\nDE\n"}, "b edits")
        repo.merge("a", "main")
        try:
            repo.merge("b", "main")
            res["conflict_detected"] = False
        except MergeConflict as e:
            res["conflict_detected"] = e.paths == ["ref/countries.csv"]
        # 4) revert, dedup, integrity, gc
        prev = repo.read("main", "ref/countries.csv")
        mistake = repo.commit("main", {"ref/countries.csv": b"code\nXX\n"}, "bad edit")
        repo.commit("main", {"orders/notes.txt": b"unrelated later change"}, "later work")
        res["revert_restores_previous"] = (
            repo.read(repo.revert("main", mistake.id).id, "ref/countries.csv") == prev
        )
        res["later_work_kept_after_revert"] = (
            repo.read("main", "orders/notes.txt") == b"unrelated later change"
        )
        repo.create_branch("scratch")
        repo.commit("scratch", {"tmp/big.bin": b"x" * 1000}, "experiment")
        repo.delete_branch("scratch")
        res["gc_removed_unreachable"] = repo.gc()
        res["objects_stored"] = len(list((repo.root / "objects").iterdir()))
        res["unique_file_versions"] = len(
            {o for br in repo.branches() for c in repo.log(br) for o in c.tree.values()}
        )
        res["verify_clean"] = repo.verify() == []
        victim = next((repo.root / "objects").iterdir())
        victim.write_bytes(b"tampered")
        res["tamper_detected"] = len(repo.verify()) >= 1
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = ["# Results (measured by `python -m branching_data_store.main`)", ""] + [
        f"- {k}: {v}" for k, v in r.items()
    ]
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
