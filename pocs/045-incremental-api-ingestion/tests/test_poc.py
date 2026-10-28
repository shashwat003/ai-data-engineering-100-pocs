import sqlite3

import pytest
from incremental_api_ingestion.client import FatalApiError, RetriesExhausted, Syncer
from incremental_api_ingestion.main import changes, table, truth
from incremental_api_ingestion.mock_api import TOKEN, ApiState, MockApi, make_records


def run(state, db=None, **kw):
    db = db or sqlite3.connect(":memory:")
    naps = []
    with MockApi(state) as api:
        s = Syncer(api.url, kw.pop("token", TOKEN), db, sleep=naps.append, **kw)
        s.run()
    return s, db, naps


def test_full_sync_matches_source_and_paginates():
    st = ApiState(make_records(230, 1000))
    s, db, _ = run(st, page_size=50)
    assert table(db) == truth(st) and s.stats.pages == 5 and s.stats.retries == 0


def test_watermark_advances_and_second_run_reads_nothing_new():
    st = ApiState(make_records(100, 1000))
    s1, db, _ = run(st)
    assert s1.watermark() == max(r["updated_at"] for r in st.records)
    s2, _, _ = run(st, db)
    assert s2.stats.rows_seen == 0 and s2.stats.requests == 1


def test_incremental_reads_only_changes_including_delete_and_insert():
    st = ApiState(make_records(500, 5000))
    _, db, _ = run(st)
    changes(st)
    s2, _, _ = run(st, db)
    assert s2.stats.rows_seen == 22 and table(db) == truth(st)
    assert (
        db.execute("SELECT deleted FROM target WHERE id=?", (st.records[20]["id"],)).fetchone()[0]
        == 1
    )


def test_429_honours_retry_after_and_500_uses_backoff_with_jitter():
    st = ApiState(make_records(400, 1000), rate_limit_every=3, error_every=5)
    s, db, naps = run(st, page_size=50)
    assert table(db) == truth(st)
    assert s.stats.rate_limited >= 2 and s.stats.retries == len(naps) > 0
    assert 2.0 in naps  # Retry-After: 2 from the server
    backoffs = [n for n in naps if n != 2.0]
    assert backoffs and all(0.5 <= n < 40 for n in backoffs)


def test_gives_up_after_max_retries():
    st = ApiState(make_records(10, 100), error_every=1)  # every request fails
    with pytest.raises(RetriesExhausted):
        run(st, max_retries=3)


def test_bad_token_fails_fast_without_retrying():
    st = ApiState(make_records(10, 100))
    db = sqlite3.connect(":memory:")
    with MockApi(st) as api:
        s = Syncer(api.url, "nope", db, sleep=lambda _s: pytest.fail("must not sleep"))
        with pytest.raises(FatalApiError):
            s.run()
    assert s.stats.requests == 1 and table(db) == {}


@pytest.mark.parametrize("crash_after", [1, 2, 5])
def test_crash_mid_run_then_resume_is_exactly_complete(crash_after):
    st = ApiState(make_records(400, 1000))
    db = sqlite3.connect(":memory:")
    with MockApi(st) as api:
        with pytest.raises(KeyboardInterrupt):
            Syncer(api.url, TOKEN, db, sleep=lambda _s: None, page_size=50).run(
                crash_after_pages=crash_after
            )
        assert 0 < len(table(db)) < 400  # partial but consistent
        s = Syncer(api.url, TOKEN, db, sleep=lambda _s: None, page_size=50)
        s.run()
    assert table(db) == truth(st)
    assert s.stats.pages == 8 - crash_after  # resumed from the checkpoint, did not start over


def test_failed_page_commit_is_atomic(monkeypatch):
    st = ApiState(make_records(100, 1000))
    db = sqlite3.connect(":memory:")
    with MockApi(st) as api:
        s = Syncer(api.url, TOKEN, db, sleep=lambda _s: None, page_size=50)
        orig = s.db.execute
        calls = {"n": 0}

        class Boom(sqlite3.Connection):
            pass

        def flaky(sql, *a):
            if sql.startswith("INSERT OR REPLACE INTO sync_state") and calls["n"] == 0:
                calls["n"] += 1
                raise sqlite3.OperationalError("disk full")
            return orig(sql, *a)

        class Wrapper:
            def __init__(self, c):
                self.c = c

            def execute(self, *a):
                return flaky(*a)

            def __enter__(self):
                return self.c.__enter__()

            def __exit__(self, *e):
                return self.c.__exit__(*e)

            def executescript(self, *a):
                return self.c.executescript(*a)

        s.db = Wrapper(db)
        with pytest.raises(sqlite3.OperationalError):
            s.run()
    assert (
        db.execute("SELECT COUNT(*) FROM target").fetchone()[0] == 0
    )  # rows rolled back with the checkpoint


def test_overlapping_pages_do_not_duplicate_rows():
    st = ApiState(make_records(300, 1000), overlap=10)
    s, db, _ = run(st, page_size=50)
    assert table(db) == truth(st) and s.stats.rows_seen > 300


def test_late_update_is_lost_without_overlap_and_recovered_with_it():
    for overlap, ok in ((0, False), (50, True)):
        st = ApiState(make_records(300, 5000))
        _, db, _ = run(st, overlap_ticks=overlap)
        top = max(r["updated_at"] for r in st.records)
        st.records[100].update(status="paid-late", updated_at=top - 10)  # behind the watermark
        run(st, db, overlap_ticks=overlap)
        assert (table(db) == truth(st)) is ok


def test_stale_replay_never_overwrites_newer_row():
    db = sqlite3.connect(":memory:")
    s = Syncer("http://unused", TOKEN, db)
    s._commit_page(
        [{"id": 1, "updated_at": 10, "amount": 2.0, "status": "paid", "deleted": False}], None, 0
    )
    s._commit_page(
        [{"id": 1, "updated_at": 5, "amount": 1.0, "status": "new", "deleted": False}], None, 0
    )
    assert db.execute("SELECT updated_at, status FROM target").fetchone() == (10, "paid")


def test_unknown_fields_are_preserved_not_fatal():
    st = ApiState(make_records(60, 1000), drift_after_request=0)
    _, db, _ = run(st, page_size=50)
    assert db.execute("SELECT extra FROM target LIMIT 1").fetchone()[0] == '{"currency": "EUR"}'
