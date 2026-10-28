"""POC 097: reference AI data platform. REAL: DuckDB lakehouse + contract gate (035/077), idempotent vector index (047),
MLflow registry gate (065), OpenTelemetry spans (071). SIMULATED: the change source (file batches, not Debezium/POC 039),
the return-risk LABEL (rule-generated), the embedder (hashing stand-in). Nothing runs on AWS."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from medallion_lakehouse_duckdb.generate import generate_landing

from pocs_common import write_json
from pocs_common.report import markdown_table

from .platform import Platform, customer_docs

POC_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def n_versions(p: Platform) -> int:
    return len(p.client.search_model_versions("name='churn-model'"))


def span_summary(p: Platform) -> dict:
    spans = p.exporter.get_finished_spans()
    return {
        "spans": len(spans),
        "by_name": dict(Counter(s.name for s in spans)),
        "root_spans_with_error": sum(
            1 for s in spans if s.name == "platform.batch" and "error" in s.attributes
        ),
    }


def run() -> dict:
    out: dict = {}
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        landing = root / "landing"
        generate_landing(landing, n_orders=3000)
        batches = sorted(p for p in landing.iterdir() if p.is_dir())
        p = Platform(root / "platform")
        rows = []
        for b in batches:
            r = p.process_batch(b, broken_candidate=(b.name == "b004"))
            rows.append(
                {"batch": r.batch, "status": r.status, "gates": f"{r.gates_passed}/{r.gates_total}",
                 "embedded": r.vector["embedded"], "unchanged": r.vector["unchanged"], "models": r.models}
            )  # fmt: skip
        out["batches"] = rows
        out["champion_version"] = p.champion()
        out["versions_registered"] = n_versions(p)
        out["embedded_incremental"] = sum(r["embedded"] for r in rows)
        out["embedded_full_rebuild_each_batch"] = None  # filled below
        n_docs = p.db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        out["customers_indexed"] = n_docs

        hash_before, versions_before = p.lake.content_hash(), n_versions(p)
        replay = [p.process_batch(b) for b in batches]
        out["replay"] = {
            "all_ok": all(r.status == "ok" for r in replay),
            "ingested": sum(r.ingested for r in replay),
            "embedded": sum(r.vector["embedded"] for r in replay),
            "models_trained": sum(len(r.models) for r in replay),
            "lake_hash_unchanged": p.lake.content_hash() == hash_before,
            "model_versions_unchanged": n_versions(p) == versions_before,
        }

        tampered = root / "tampered" / "b002"
        shutil.copytree(batches[1], tampered)
        orders = tampered / "orders.csv"
        orders.write_text(orders.read_text().replace("completed", "shipped", 1))
        before = (
            p.lake.content_hash(),
            p.store.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0],
            n_versions(p),
        )
        r = p.process_batch(tampered)
        after = (
            p.lake.content_hash(),
            p.store.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0],
            n_versions(p),
        )
        out["tampered_batch"] = {
            "status": r.status,
            "error": r.error,
            "state_unchanged": before == after,
        }

        docs = customer_docs(p.lake.con)
        hits = sum(
            1 for d, text in docs.items() if (p.store.search(text, k=1) or [("",)])[0][0] == d
        )
        out["retrieval_self_hit_rate"] = round(hits / len(docs), 3)
        out["trace"] = span_summary(p)
        p.close()
    out["embedded_full_rebuild_each_batch"] = out["customers_indexed"] * 4
    return out


def render(res: dict) -> str:
    md = [
        "# Results (POC 097: real DuckDB/MLflow/OTel/vector-index components wired together; change source is file batches, labels are rule-simulated; nothing on AWS)",
        f"{res['customers_indexed']} customers indexed; champion model version after four batches: **v{res['champion_version']}** "
        f"({res['versions_registered']} versions registered). Incremental embedding: {res['embedded_incremental']} chunks vs "
        f"about {res['embedded_full_rebuild_each_batch']} if the index were rebuilt after every batch.",
        "\n## Batch by batch\n",
        markdown_table(
            [
                "batch",
                "status",
                "lakehouse gates passed",
                "chunks embedded",
                "chunks unchanged",
                "model decisions",
            ],
            [
                [
                    r["batch"],
                    r["status"],
                    r["gates"],
                    r["embedded"],
                    r["unchanged"],
                    "; ".join(
                        f"{m['candidate']}: {'PROMOTED' if m['promoted'] else 'rejected'} ({m['reasons'][0][:70]})"
                        for m in r["models"]
                    )
                    or "-",
                ]
                for r in res["batches"]
            ],
        ),  # fmt: skip
        "\n## Replaying every batch (idempotency)\n",
        markdown_table(["check", "result"], [[k, v] for k, v in res["replay"].items()]),
        "\n## Tampered batch (same id, different bytes)\n",
        markdown_table(["check", "result"], [[k, v] for k, v in res["tampered_batch"].items()]),
        f"\nSelf-retrieval (each customer's profile text finds its own document at rank 1): {res['retrieval_self_hit_rate']:.1%}.\n",
        "\n## Trace\n",
        markdown_table(
            ["span", "count"], [[k, v] for k, v in sorted(res["trace"]["by_name"].items())]
        ),
        f"\nBatches whose root span carries an error attribute: {res['trace']['root_spans_with_error']}.\n",
    ]
    return "\n".join(md)


def main() -> None:
    res = run()
    out = POC_DIR / "results"
    out.mkdir(exist_ok=True)
    write_json(out / "metrics.json", res)
    (out / "results.md").write_text(render(res))
    print(render(res))


if __name__ == "__main__":
    main()
