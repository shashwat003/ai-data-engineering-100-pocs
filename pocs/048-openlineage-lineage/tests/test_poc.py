import json
import threading
import urllib.error
import urllib.request
import uuid

import pytest
from openlineage_lineage.main import run
from openlineage_lineage.receiver import MAX_BODY, make_server
from openlineage_lineage.store import InvalidEvent, LineageStore, validate


def ev(
    job="j", typ="COMPLETE", run_id=None, ins=(), outs=(), t="2026-03-01T10:00:00+00:00", cols=None
):
    o = []
    for n in outs:
        d = {"namespace": "ns", "name": n}
        if cols:
            d["facets"] = {"columnLineage": {"fields": cols}}
        o.append(d)
    return {"eventTime": t, "eventType": typ, "producer": "p", "schemaURL": "s", "run": {"runId": run_id or str(uuid.uuid4())},
            "job": {"namespace": "ns", "name": job}, "inputs": [{"namespace": "ns", "name": n} for n in ins], "outputs": o}  # fmt: skip


@pytest.mark.parametrize("mut", [lambda e: e.pop("producer"), lambda e: e.update(eventType="DONE"), lambda e: e["run"].update(runId="nope"),
                                 lambda e: e["job"].pop("name"), lambda e: e.update(eventTime="yesterday"), lambda e: e.update(inputs=[{"name": "x"}]), lambda e: e.update(outputs=["x"])])  # fmt: skip
def test_validation_rejects(mut):
    e = ev(ins=["a"], outs=["b"])
    mut(e)
    with pytest.raises(InvalidEvent):
        validate(e)


def test_validation_rejects_non_object():
    with pytest.raises(InvalidEvent):
        validate([1])


def test_graph_queries_and_cycles_terminate():
    s = LineageStore()
    s.ingest(ev("j1", ins=["a"], outs=["b"]))
    s.ingest(ev("j2", ins=["b"], outs=["c"]))
    s.ingest(ev("j3", ins=["c"], outs=["b"]))  # cycle b -> c -> b
    assert s.upstream("ns:c") == {"ns:a", "ns:b"} and s.downstream("ns:a") == {"ns:b", "ns:c"}
    assert s.sources() == {"ns:a"}


def test_idempotent_and_late_events():
    s = LineageStore()
    rid = str(uuid.uuid4())
    start = ev("j", "START", rid, t="2026-03-01T10:00:00+00:00")
    assert s.ingest(start) and not s.ingest(start)
    s.ingest(ev("j", "COMPLETE", rid, ins=["a"], outs=["b"], t="2026-03-01T10:05:00+00:00"))
    s.ingest(ev("j", "RUNNING", rid, t="2026-03-01T10:06:00+00:00"))  # late
    assert s.runs[rid]["state"] == "COMPLETE" and s.stuck_runs() == []
    with pytest.raises(InvalidEvent):
        s.ingest(ev("other", "START", rid, t="2026-03-01T10:07:00+00:00"))


def test_fail_and_hang_detection_and_recovery():
    s = LineageStore()
    s.ingest(ev("j1", ins=["a"], outs=["b"], t="2026-03-01T10:00:00+00:00"))
    s.ingest(ev("j2", ins=["b"], outs=["c"], t="2026-03-01T10:01:00+00:00"))
    r = str(uuid.uuid4())
    s.ingest(ev("j1", "START", r, t="2026-03-02T10:00:00+00:00"))
    s.ingest(ev("j1", "FAIL", r, t="2026-03-02T10:01:00+00:00"))
    assert s.stale_datasets() == {"ns:b": "ns:j1", "ns:c": "ns:j1"}
    r2 = str(uuid.uuid4())
    s.ingest(ev("j1", "START", r2, t="2026-03-03T10:00:00+00:00"))
    assert s.stuck_runs() == ["ns:j1"]
    s.ingest(ev("j1", "COMPLETE", r2, ins=["a"], outs=["b"], t="2026-03-03T10:01:00+00:00"))
    assert s.stale_datasets() == {} and s.stuck_runs() == []


def test_column_lineage_transitive():
    s = LineageStore()
    s.ingest(
        ev(
            "j1",
            ins=["a"],
            outs=["b"],
            cols={"x": {"inputFields": [{"namespace": "ns", "name": "a", "field": "raw"}]}},
        )
    )
    s.ingest(
        ev(
            "j2",
            ins=["b"],
            outs=["c"],
            cols={"y": {"inputFields": [{"namespace": "ns", "name": "b", "field": "x"}]}},
        )
    )
    assert s.column_upstream("ns:c.y") == {"ns:b.x", "ns:a.raw"}


def post(url, body, path="/api/v1/lineage", headers=None):
    req = urllib.request.Request(url + path, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_http_receiver_contract():
    s = LineageStore()
    srv = make_server(s)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert srv.server_address[0] == "127.0.0.1"
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        assert post(url, json.dumps(ev("j", ins=["a"], outs=["b"])).encode()) == 201
        assert post(url, b"{not json") == 400
        assert post(url, json.dumps({"eventType": "START"}).encode()) == 400
        assert post(url, b"{}", path="/other") == 404
        try:
            assert post(url, b"x" * (MAX_BODY + 1)) == 413
        except (urllib.error.URLError, ConnectionError):
            pass  # the server rejects from the headers and may close before the client finishes sending: also a rejection
        assert s.rejected == 2 and len(s.seen) == 1
    finally:
        srv.shutdown()
        srv.server_close()


def test_demo_uses_real_client_and_matches_ground_truth():
    r = run()
    assert r["events_accepted"] == 8 and r["rejected"] == 0
    assert r["upstream_of_mart_revenue"] == [
        f"warehouse:{n}"
        for n in ("fct_orders", "raw_customers", "raw_orders", "stg_customers", "stg_orders")
    ]
    assert r["column_upstream_mart_revenue.revenue"][1] == "warehouse:raw_orders.amount_cents"
    assert set(r["stale_after_failure"]) == {"stg_orders", "fct_orders", "mart_revenue"} and r[
        "stuck_runs"
    ] == ["stg_customers"]
