"""POC 030: raw mail -> validated, routed tickets with threading, idempotence and safety flags."""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .parse import parse
from .synth import corpus, hard_emails, make_email
from .tickets import Store, ingest

POC_DIR = Path(__file__).resolve().parent.parent


def run(n: int = 150) -> dict:
    mails = corpus(n)
    store = Store()
    rows = []
    for raw, label in mails:
        m = parse(raw)
        res = ingest(store, m)
        rows.append((label, store.tickets[res["ticket"]]))
    cat_ok = sum(t["category"] == lab["category"] for lab, t in rows)
    pri_ok = sum(
        (t["priority"] == "urgent") == (lab["priority"] == "urgent" or lab["vip"])
        for lab, t in rows
    )
    oid_exp = [(lab["order_id"], t["order_id"]) for lab, t in rows if lab["order_id"]]
    oid_ok = sum(a == b for a, b in oid_exp)
    wrong_unknown = sum(t["category"] == "unknown" for _, t in rows)
    # idempotence: replay the whole mailbox
    before = (len(store.tickets), sum(t["messages"] for t in store.tickets.values()))
    replay = Counter(ingest(store, parse(raw))["action"] for raw, _ in mails)
    after = (len(store.tickets), sum(t["messages"] for t in store.tickets.values()))
    # threading: replies attach to the original ticket
    raw0, _ = mails[0]
    reply, _ = make_email(random.Random(1), 9999, "billing")
    reply = reply.replace(
        b"Message-ID: <msg9999@",
        b"In-Reply-To: <msg0@" + parse(raw0).domain.encode() + b">\r\nMessage-ID: <msg9999@",
    )
    thr = ingest(store, parse(reply))
    attachments = [t for _, t in rows if t["attachments"]]
    hard = hard_emails()
    hs = Store()
    h_rows = [(lab, hs.tickets[ingest(hs, parse(raw))["ticket"]]) for raw, lab in hard]
    h_correct = sum(t["category"] == lab["category"] for lab, t in h_rows)
    h_conf = [(lab, t) for lab, t in h_rows if not t["needs_human"]]
    h_conf_ok = sum(t["category"] == lab["category"] for lab, t in h_conf)
    return {"hard_n": len(hard), "hard_accuracy": round(h_correct / len(hard), 3), "hard_sent_to_human": len(hard) - len(h_conf), "hard_accuracy_when_auto_routed": round(h_conf_ok / max(1, len(h_conf)), 3), "n": n, "category_accuracy": round(cat_ok / n, 3), "urgent_accuracy": round(pri_ok / n, 3), "order_id_extracted": f"{oid_ok}/{len(oid_exp)}", "unknown_category": wrong_unknown,
            "tickets": len(store.tickets), "replay": dict(replay), "replay_changed_state": before != after, "thread_action": thr["action"],
            "needs_human": sum(t["needs_human"] for _, t in rows), "risky_attachment_tickets": sum("risky_attachment" in t["flags"] for t in attachments),
            "queues": dict(Counter(t["queue"] for _, t in rows)), "escalated_vip": sum(t["escalated"] for _, t in rows)}  # fmt: skip


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    md = (
        f"# Results: {res['n']} synthetic emails to tickets (rule-based classifier, no LLM)\n\n"
        + markdown_table(["metric", "value"], [("category accuracy", res["category_accuracy"]), ("urgent flag accuracy (incl. VIP escalation)", res["urgent_accuracy"]), ("order ids extracted", res["order_id_extracted"]),
                                              ("HARD set (paraphrase, mixed signals, Spanish, quoted threads): category accuracy", res["hard_accuracy"]), ("HARD set: sent to a human instead of auto-routed", f"{res['hard_sent_to_human']}/{res['hard_n']}"), ("HARD set: accuracy among auto-routed", res["hard_accuracy_when_auto_routed"]),
                                              ("tickets created", res["tickets"]), ("tickets needing a human (low confidence or safety flag)", res["needs_human"]), ("tickets with a risky attachment flagged", res["risky_attachment_tickets"]),
                                              ("replay of the whole mailbox", res["replay"]), ("replay changed state", res["replay_changed_state"]), ("reply threaded onto the original ticket", res["thread_action"])])
        + f"\n\nQueues: {res['queues']}. Categories come from keyword scoring on templated bodies, so accuracy here is optimistic for real customer mail.\n"
    )  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
