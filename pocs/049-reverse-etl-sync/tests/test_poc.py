import json

import pytest
from reverse_etl_sync import warehouse
from reverse_etl_sync.crm import CRM, MAX_BATCH
from reverse_etl_sync.engine import (
    BATCH,
    MAPPING,
    MassDeleteBlocked,
    SyncAborted,
    Syncer,
    h,
    make_plan,
    read_source,
)
from reverse_etl_sync.main import fresh, run

R = run()
S = R["steps"]


def mk(n=300, rpw=1000):
    con, crm, client, state = fresh(n, rpw)
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        crm.advance(s)

    return con, crm, client, state, Syncer(client, state, sleep=sleep), sleeps


def test_initial_load_creates_everything_and_a_rerun_makes_no_api_calls():
    k = next(iter(S))
    assert (
        S[k]["created"] == 1000
        and S["2. nothing changed"]["api_calls"] == 0
        and S["5. re-run after the outage"]["api_calls"] == 0
    )


def test_daily_change_set_sends_only_what_changed_and_far_fewer_bytes_than_a_full_refresh():
    d = S["3. next day: 5% updated, 10 deleted, 20 new, 1 bad email"]
    assert (d["created"], d["updated"], d["archived"], d["dead_lettered"]) == (20, 50, 10, 1)
    assert d["bytes_sent"] * 20 < R["naive_full_refresh_second_run"]["bytes_sent"]


def test_changed_records_carry_only_the_changed_fields():
    con, crm, client, state, sync, _ = mk(50)
    sync.run(read_source(con))
    con.execute("UPDATE customers_360 SET churn_risk = 0.99 WHERE customer_id = 'c00001'")
    plan = make_plan(read_source(con), state)
    assert (
        plan.update == {"c00001": {"churn_risk": 0.99}}
        and plan.unchanged == 49
        and not plan.create
        and not plan.archive
    )


def test_rate_limits_are_honoured_by_waiting_the_advertised_time():
    con, crm, client, state, sync, sleeps = mk(500, rpw=2)
    _, rep = sync.run(read_source(con))
    assert rep.created == 500 and rep.rate_limited >= 2 and all(s >= 1 for s in sleeps)


def test_transient_errors_are_retried_with_growing_backoff_then_succeed():
    con, crm, client, state, sync, sleeps = mk(20)
    crm.fail_next(3, 503)
    _, rep = sync.run(read_source(con))
    assert rep.created == 20 and rep.retries == 3
    assert (
        sleeps[0] < sleeps[2] * 2 and sleeps[2] > sleeps[0] * 0.9
    )  # exponential with jitter: later waits are larger on average
    assert max(sleeps) <= 30 * 1.5


def test_persistent_failure_aborts_after_max_attempts_and_a_rerun_completes_without_duplicates():
    con, crm, client, state, sync, _ = mk(250)
    orig, calls = client.post, {"n": 0}

    def flaky(path, **kw):  # the first batch succeeds, then the CRM keeps failing for every attempt
        calls["n"] += 1
        if calls["n"] >= 2:
            crm.fail_next(1, 500)
        return orig(path, **kw)

    client.post = flaky
    with pytest.raises(SyncAborted):
        sync.run(read_source(con))
    client.post = orig
    crm.fail_queue.clear()
    done = len(state.rows())
    assert 0 < done < 250  # progress was committed per record
    _, rep = Syncer(client, state, sleep=crm.advance).run(read_source(con))
    assert rep.created == 250 - done and len(crm.contacts) == 250


def test_non_retryable_errors_are_not_retried():
    con, crm, client, state, sync, _ = mk(10)

    class Bad:
        def post(self, *a, **k):
            return client.post(
                "/v1/contacts/batch-upsert",
                content=json.dumps({"records": []}),
                headers={"Content-Type": "application/json"},
            )

    with pytest.raises(SyncAborted, match="422"):
        Syncer(Bad(), state, sleep=lambda s: None).run(read_source(con))


def test_idempotency_key_stops_a_resent_batch_from_applying_twice():
    con, crm, client, state, sync, _ = mk(5)
    body = json.dumps({"records": [{"external_id": "x1", "fields": {"email": "a@example.com"}}]})
    for _ in range(2):
        r = client.post(
            "/v1/contacts/batch-upsert",
            content=body,
            headers={"Idempotency-Key": "k1", "Content-Type": "application/json"},
        )
        assert r.status_code == 200
    assert crm.calls["upsert"] == 1 and crm.calls["records_written"] == 1


def test_bad_record_is_dead_lettered_not_blocking_and_not_retried_until_fixed():
    con, crm, client, state, sync, _ = mk(30)
    con.execute("UPDATE customers_360 SET email = 'nope' WHERE customer_id = 'c00005'")
    _, rep = sync.run(read_source(con))
    assert rep.dead_lettered == 1 and rep.created == 29 and "c00005" in state.dead()
    _, rep2 = sync.run(read_source(con))
    assert rep2.api_calls == 0  # same bad row: skipped
    con.execute("UPDATE customers_360 SET email = 'ok@example.com' WHERE customer_id = 'c00005'")
    _, rep3 = sync.run(read_source(con))
    assert (
        rep3.created == 1
        and "c00005" not in state.dead()
        and crm.contacts["c00005"]["email"] == "ok@example.com"
    )


def test_mass_delete_guard_blocks_an_emptied_source_and_can_be_overridden():
    con, crm, client, state, sync, _ = mk(100)
    sync.run(read_source(con))
    con.execute("DELETE FROM customers_360")
    with pytest.raises(MassDeleteBlocked):
        sync.run(read_source(con))
    assert len(crm.archived) == 0
    _, rep = sync.run(read_source(con), allow_mass_delete=True)
    assert rep.archived == 100 and len(crm.archived) == 100


def test_archived_customer_who_returns_is_restored():
    con, crm, client, state, sync, _ = mk(50)
    sync.run(read_source(con))
    con.execute("DELETE FROM customers_360 WHERE customer_id = 'c00049'")
    sync.run(read_source(con))
    assert "c00049" in crm.archived
    con.execute(
        "INSERT INTO customers_360 VALUES ('c00049','back@example.com','Back','User',10.0,0.1,'smb','x','0000')"
    )
    sync.run(read_source(con))
    assert "c00049" not in crm.archived and crm.contacts["c00049"]["email"] == "back@example.com"


def test_dry_run_reports_the_plan_and_changes_nothing():
    con, crm, client, state, sync, _ = mk(40)
    plan, rep = sync.run(read_source(con), dry_run=True)
    assert len(plan.create) == 40 and rep.api_calls == 0 and not crm.contacts and not state.rows()


def test_only_mapped_columns_leave_the_warehouse_and_transforms_apply():
    assert set(R["crm_fields_ever_sent"]) == set(MAPPING)
    con = warehouse.build(":memory:", 5)
    con.execute("UPDATE customers_360 SET email = 'MiXeD@Example.COM' WHERE customer_id = 'c00000'")
    assert (
        read_source(con)["c00000"]["email"] == "mixed@example.com"
        and "internal_note" not in read_source(con)["c00000"]
    )


def test_batches_respect_the_crm_limit_and_the_end_state_matches_the_source():
    assert BATCH <= MAX_BATCH
    assert (
        R["crm_in_sync_with_source"] == R["source_rows"] - 1
        and R["dead_letters"] == 1
        and R["mass_delete_blocked"]
    )


def test_mock_crm_rejects_readonly_fields_and_paginates():
    crm = CRM()
    from fastapi.testclient import TestClient

    c = TestClient(crm.app)
    r = c.post(
        "/v1/contacts/batch-upsert",
        json={
            "records": [
                {"external_id": "a", "fields": {"created_at": "x"}},
                {"external_id": "b", "fields": {"email": "b@example.com"}},
            ]
        },
    ).json()
    assert [x["status"] for x in r["results"]] == ["error", "created"]
    c.post(
        "/v1/contacts/batch-upsert",
        json={
            "records": [{"external_id": f"id{i}", "fields": {"segment": "smb"}} for i in range(5)]
        },
    )
    page = c.get("/v1/contacts?limit=3").json()
    assert (
        len(page["items"]) == 3
        and page["next"] == 3
        and c.get("/v1/contacts?cursor=3&limit=3").json()["next"] is None
    )
    assert (
        c.post(
            "/v1/contacts/batch-upsert",
            json={"records": [{"external_id": str(i), "fields": {}} for i in range(101)]},
        ).status_code
        == 422
    )


def test_hash_is_order_independent():
    assert h({"a": 1, "b": 2}) == h({"b": 2, "a": 1})
