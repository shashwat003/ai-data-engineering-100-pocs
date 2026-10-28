import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from dbt_scd2_snapshots.main import EXPECTED_CHECK, EXPECTED_TS, chains
from dbt_scd2_snapshots.runner import FAR, Warehouse, scenario

pytestmark = pytest.mark.skipif(
    shutil.which("dbt") is None and not Path(sys.executable).with_name("dbt").exists(),
    reason="dbt-duckdb not installed",
)


@pytest.fixture(scope="module")
def wh():
    d = Path(tempfile.mkdtemp())
    w = Warehouse(d)
    scenario(w)
    yield w
    w.cleanup()


def test_check_strategy_matches_hand_written_oracle(wh):
    got = {k: [(a, b) for a, b, _ in v] for k, v in chains(wh, "customers_check").items()}
    assert got == EXPECTED_CHECK


def test_untracked_column_change_creates_no_version_under_check_but_does_under_timestamp(wh):
    assert len(chains(wh, "customers_check")[2]) == 1  # Bob: login only
    assert len(chains(wh, "customers_ts")[2]) == 2


def test_timestamp_strategy_version_counts(wh):
    assert {k: len(v) for k, v in chains(wh, "customers_ts").items()} == EXPECTED_TS


def test_every_key_has_exactly_one_open_version_and_chains_are_contiguous(wh):
    rows = wh.sql(
        "SELECT customer_id, dbt_valid_from, dbt_valid_to FROM main_snapshots.customers_check ORDER BY customer_id, dbt_valid_from"
    )
    by = {}
    for cid, f, t in rows:
        by.setdefault(cid, []).append((f, t))
    for cid, vs in by.items():
        assert sum(t == FAR for _, t in vs) == 1, cid
        gaps = [a[1] != b[0] for a, b in zip(vs, vs[1:], strict=False)]
        assert not any(gaps) if cid != 3 else gaps == [True], (
            cid
        )  # only the deleted-then-reinserted key has a gap
        assert all(a[1] <= b[0] for a, b in zip(vs, vs[1:], strict=False)), cid  # never overlapping


def test_deleted_then_reinserted_key_gets_a_closed_and_a_new_version(wh):
    cy = chains(wh, "customers_check")[3]
    assert len(cy) == 2 and cy[0][2] != FAR and cy[1][2] == FAR


def test_rerun_without_changes_writes_nothing(wh):
    before = wh.sql("SELECT count(*) FROM main_snapshots.customers_check")[0][0]
    wh.snapshot()
    wh.snapshot()
    assert wh.sql("SELECT count(*) FROM main_snapshots.customers_check")[0][0] == before


def test_dbt_data_tests_pass_on_good_data(wh):
    assert wh.dbt("test", check=False).returncode == 0


def test_dbt_data_tests_catch_a_corrupted_snapshot(wh):
    wh.sql(
        "INSERT INTO main_snapshots.customers_check SELECT * REPLACE (cast('9999-12-31' as timestamp) AS dbt_valid_to, 'dup-' || dbt_scd_id AS dbt_scd_id) FROM main_snapshots.customers_check WHERE customer_id=2 LIMIT 1"
    )
    p = wh.dbt("test", check=False)
    assert p.returncode != 0 and "one_current_row_per_key" in (p.stdout + p.stderr)
