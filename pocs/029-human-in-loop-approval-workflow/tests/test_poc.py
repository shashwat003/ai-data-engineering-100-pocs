import base64
import json
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest
from human_in_loop_approval_workflow.main import POLICIES, make, tok
from human_in_loop_approval_workflow.server import serve
from human_in_loop_approval_workflow.workflow import TERMINAL, TRANSITIONS, Workflow, WorkflowError


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    c = Clock()
    wf, ran = make(c)
    return wf, ran, c


def test_transition_table_is_closed_and_terminal_states_are_final():
    assert set(TRANSITIONS) == set(TRANSITIONS) | {s for v in TRANSITIONS.values() for s in v}
    assert TERMINAL == {"rejected", "expired", "cancelled", "executed", "failed"}


def test_single_approver_executes_exactly_once(env):
    wf, ran, _ = env
    r = wf.submit("restart_pipeline", {"p": 1}, "erin")
    assert wf.decide(tok(wf, "alice"), "approve") == "executed"
    assert ran == [("restart_pipeline", {"p": 1})] and wf.status(r)["state"] == "executed"


def test_quorum_requires_distinct_approvers(env):
    wf, ran, _ = env
    r = wf.submit("delete_dataset", {"d": "x"}, "erin")
    assert wf.decide(tok(wf, "alice"), "approve") == "pending" and not ran
    with pytest.raises(WorkflowError, match="already used"):
        wf.decide(tok(wf, "alice"), "approve")  # same token cannot be replayed to reach quorum
    wf.decide(
        wf.issue_token(r, "alice"), "approve"
    )  # a fresh token for the same person still counts once
    assert not ran and wf.status(r)["state"] == "pending"
    assert wf.decide(tok(wf, "bob"), "approve") == "executed" and len(ran) == 1


def test_reject_is_final_and_blocks_later_approvals(env):
    wf, ran, _ = env
    r = wf.submit("restart_pipeline", {}, "erin")
    assert wf.decide(tok(wf, "bob"), "reject") == "rejected"
    with pytest.raises(WorkflowError, match="already rejected"):
        wf.decide(tok(wf, "alice"), "approve")
    assert not ran and wf.status(r)["state"] == "rejected"


def test_separation_of_duties(env):
    wf, ran, _ = env
    r = wf.submit("restart_pipeline", {}, "alice")
    assert "alice" not in {
        m["to"] for m in wf.outbox if m["request"] == r
    }  # requester is not even asked
    with pytest.raises(WorkflowError, match="separation of duties"):
        wf.decide(wf.issue_token(r, "alice"), "approve")
    assert not ran


def test_ineligible_person_with_a_valid_token_is_refused(env):
    wf, ran, _ = env
    r = wf.submit("restart_pipeline", {}, "erin")
    with pytest.raises(WorkflowError, match="not an eligible approver"):
        wf.decide(wf.issue_token(r, "mallory"), "approve")
    with pytest.raises(WorkflowError, match="not an eligible approver"):
        wf.decide(
            wf.issue_token(r, "carol"), "approve"
        )  # escalation group is not eligible before escalation
    assert not ran


def test_token_tampering_expiry_and_garbage(env):
    wf, _, c = env
    r = wf.submit("restart_pipeline", {}, "erin")
    t = tok(wf, "alice")
    body, sig = base64.urlsafe_b64decode(t).decode().rsplit("|", 1)
    forged = base64.urlsafe_b64encode(
        (body.replace('"a": "alice"', '"a": "bob"') + "|" + sig).encode()
    ).decode()
    with pytest.raises(WorkflowError, match="signature"):
        wf.decide(forged, "approve")
    with pytest.raises(WorkflowError, match="malformed"):
        wf.decide("not-a-token", "approve")
    c.t += 86_401
    with pytest.raises(WorkflowError, match="expired"):
        wf.decide(t, "approve")
    assert wf.status(r)["state"] == "pending"
    with pytest.raises(WorkflowError, match="verdict"):
        wf.decide(wf.issue_token(r, "alice"), "maybe")


def test_sla_escalation_widens_approvers_then_expires(env):
    wf, ran, c = env
    r = wf.submit("restart_pipeline", {}, "erin")
    assert wf.tick() == []
    c.t += 601
    assert wf.tick() == [(r, "escalated")] and any(
        m["to"] == "carol" and m["kind"] == "escalated" for m in wf.outbox
    )
    assert wf.decide(tok(wf, "carol"), "approve") == "executed" and len(ran) == 1
    r2 = wf.submit("restart_pipeline", {}, "erin")
    c.t += 601
    wf.tick()
    c.t += 601
    assert wf.tick() == [(r2, "expired")]
    with pytest.raises(WorkflowError, match="already expired"):
        wf.decide(tok(wf, "alice"), "approve")


def test_cancel_only_by_requester_and_only_when_open(env):
    wf, _, _ = env
    r = wf.submit("restart_pipeline", {}, "erin")
    with pytest.raises(WorkflowError, match="only the requester"):
        wf.cancel(r, "alice")
    wf.cancel(r, "erin")
    with pytest.raises(WorkflowError, match="illegal transition"):
        wf.cancel(r, "erin")


def test_failed_execution_is_recorded_and_not_retried_silently():
    c = Clock()

    def boom(a, p):
        raise RuntimeError("downstream down")

    wf, _ = make(c, boom)
    r = wf.submit("restart_pipeline", {}, "erin")
    assert wf.decide(tok(wf, "alice"), "approve") == "failed"
    assert wf.status(r)["state"] == "failed" and TRANSITIONS["failed"] == set()


def test_unknown_action_and_unknown_request(env):
    wf, _, _ = env
    with pytest.raises(WorkflowError, match="no policy"):
        wf.submit("format_disk", {}, "erin")
    with pytest.raises(WorkflowError, match="unknown request"):
        wf.status(999)


def test_simultaneous_approvals_execute_exactly_once(env):
    wf, ran, _ = env
    for _ in range(10):  # repeat to give a race a chance
        r = wf.submit("restart_pipeline", {"race": _}, "erin")
        before = len(ran)
        ts = [tok(wf, "alice"), tok(wf, "bob")]
        out = []

        def go(t, out=out):
            try:
                out.append(wf.decide(t, "approve"))
            except WorkflowError as e:
                out.append(str(e))

        th = [threading.Thread(target=go, args=(t,)) for t in ts]
        [x.start() for x in th]
        [x.join() for x in th]
        assert len(ran) - before == 1 and wf.status(r)["state"] == "executed"


def test_audit_chain_covers_every_transition_and_detects_tampering(env):
    wf, _, _ = env
    wf.submit("restart_pipeline", {}, "erin")
    wf.decide(tok(wf, "alice"), "approve")
    events = [e["event"] for e in wf.audit.events()]
    assert events.count("state") == 2 and "decision" in events and wf.audit.verify() == (True, None)
    wf.db.execute("DROP TRIGGER audit_no_update")
    wf.db.execute("UPDATE audit_log SET payload='{}' WHERE seq=2")
    assert wf.audit.verify()[0] is False


def test_http_end_to_end():
    c = Clock()
    wf, ran = make(c)
    srv, url = serve(wf)
    try:

        def call(method, path, body=None):
            req = urllib.request.Request(
                url + path,
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
            )
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.load(r)
            except urllib.error.HTTPError as e:
                return e.code, json.load(e)

        code, body = call(
            "POST",
            "/requests",
            {"action": "restart_pipeline", "payload": {"p": "http"}, "requester": "erin"},
        )
        assert code == 201
        rid = body["id"]
        assert call("GET", f"/requests/{rid}")[1]["state"] == "pending"
        assert call("POST", "/decisions", {"token": "garbage", "verdict": "approve"})[0] == 409
        assert call("POST", "/decisions", {"verdict": "approve"})[0] == 400
        code, body = call("POST", "/decisions", {"token": tok(wf, "alice"), "verdict": "approve"})
        assert (code, body["state"]) == (200, "executed") and ran == [
            ("restart_pipeline", {"p": "http"})
        ]
        assert call("GET", "/requests/999")[0] == 404 and call("GET", "/nope")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_policies_are_consistent():
    for p in POLICIES.values():
        assert p.required <= len(p.approvers) and not (p.approvers & p.escalation)
    assert isinstance(sqlite3.connect(":memory:"), sqlite3.Connection) and Workflow
