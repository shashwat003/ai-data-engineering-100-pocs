import duckdb
import pytest
from parquet_compaction_lab import lab
from parquet_compaction_lab.lab import (
    compact,
    layout,
    prunable_row_groups,
    table_digest,
    time_query,
    write_small_files,
)


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    p = tmp_path_factory.mktemp("lab") / "small"
    write_small_files(p, 60, 1_000)
    return p


def test_small_file_layout(small):
    lay = layout(small)
    assert lay["files"] == 60 and lay["row_groups"] == 60
    assert table_digest(small)[0] == 60_000


def test_digest_detects_changed_data(small, tmp_path):
    other = tmp_path / "other"
    write_small_files(other, 60, 1_000, seed=2)
    assert table_digest(other)[0] == 60_000 and table_digest(other) != table_digest(small)


def test_compaction_preserves_rows_and_checksum_and_reduces_files(small, tmp_path):
    dst = tmp_path / "c"
    out = compact(small, dst, target_rows_per_file=30_000, row_group_rows=10_000)
    assert out == {"files": 2, "rows": 60_000}
    assert table_digest(dst) == table_digest(small)
    assert layout(dst)["files"] == 2 < layout(small)["files"]
    assert (dst / "_manifest.json").exists()


def test_sorting_by_the_filter_column_enables_row_group_pruning(small, tmp_path):
    unsorted_, sorted_ = tmp_path / "u", tmp_path / "s"
    compact(small, unsorted_, sort_by=None, target_rows_per_file=60_000, row_group_rows=5_000)
    compact(small, sorted_, sort_by="day", target_rows_per_file=60_000, row_group_rows=5_000)
    assert prunable_row_groups(unsorted_, "day", 10, 14)[0] == 0  # every group spans all days
    sk, tot = prunable_row_groups(sorted_, "day", 10, 14)
    assert tot >= 8 and sk >= 0.6 * tot


def test_query_results_are_identical_across_layouts(small, tmp_path):
    a = tmp_path / "a"
    compact(small, a, sort_by="day", target_rows_per_file=60_000)
    assert time_query(small, 1)["result_digest"] == time_query(a, 1)["result_digest"]


def test_failed_verification_aborts_before_swapping(small, tmp_path, monkeypatch):
    dst = tmp_path / "live"
    compact(small, dst, target_rows_per_file=60_000)
    before = sorted(p.name for p in dst.iterdir())
    calls = {"n": 0}
    real = lab.table_digest

    def lying(path):
        calls["n"] += 1
        return (
            real(path) if calls["n"] == 1 else (0, "corrupt")
        )  # second call = verification of the new copy

    monkeypatch.setattr(lab, "table_digest", lying)
    with pytest.raises(RuntimeError, match="verification failed"):
        compact(small, dst, target_rows_per_file=20_000)
    assert (
        sorted(p.name for p in dst.iterdir()) == before and not dst.with_name("live.tmp").exists()
    )
    assert table_digest(dst) == real(small)


def test_swap_keeps_the_previous_version_for_rollback(small, tmp_path):
    dst = tmp_path / "live"
    compact(small, dst, target_rows_per_file=60_000)
    compact(small, dst, target_rows_per_file=30_000)
    old = dst.with_name("live.old")
    assert old.exists() and layout(old)["files"] == 1 and layout(dst)["files"] == 2


def test_compression_codecs_trade_size(small, tmp_path):
    sizes = {}
    for codec in ("zstd", "snappy", "uncompressed"):
        d = tmp_path / codec
        compact(small, d, compression=codec, target_rows_per_file=60_000)
        sizes[codec] = layout(d)["total_mb"]
    assert sizes["zstd"] <= sizes["snappy"] < sizes["uncompressed"]


def test_empty_source_directory_is_handled(tmp_path):
    (tmp_path / "e").mkdir()
    with pytest.raises(duckdb.Error):
        compact(tmp_path / "e", tmp_path / "out")
