"""POC 055: Bedrock knowledge-base RAG as CDK. REAL: CDK synthesis (offline), cfn-lint, template checks, local mirror pipeline.
MOCK: S3 via moto. STAND-INS: hashing embedder, extractive generator. NOT RUN: any AWS service, deployment, Titan, Claude."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import boto3
from moto import mock_aws

from data_gen import generate_knowledge_base
from pocs_common import write_json
from pocs_common.report import markdown_table

from .checks import check_template, monthly_cost_estimate
from .local_kb import LocalKnowledgeBase, settings_from_template
from .stack import RagSettings, synth

POC_DIR = Path(__file__).resolve().parent.parent


def lint(template: dict) -> list[str]:
    from cfnlint.api import lint as cfn_lint

    return [
        f"{m.rule.id}: {m.message}"
        for m in cfn_lint(json.dumps(template))
        if m.rule.id.startswith("E")
    ]


def evaluate(kb, template_settings, cfgs, n_systems: int) -> list[dict]:
    rows = []
    for name, (mt, ov) in cfgs.items():
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="docs")
            for d in kb.docs:
                s3.put_object(Bucket="docs", Key=f"{d.doc_id}.txt", Body=d.indexed_text.encode())
            local = LocalKnowledgeBase(type(template_settings)(mt, ov, template_settings.top_k))
            ing = local.ingest_from_s3(s3)
            hit1 = hit5 = cite = ans = 0
            for q in kb.qa:
                refs = local.retrieve(q.question, 5)
                uris = [
                    r["location"]["s3Location"]["uri"].rsplit("/", 1)[-1].removesuffix(".txt")
                    for r in refs
                ]
                hit1 += bool(uris) and uris[0] == q.gold_doc_id
                hit5 += q.gold_doc_id in uris
                r = local.retrieve_and_generate(q.question)
                c = r["citations"]
                cite += bool(c) and c[0]["retrievedReferences"][0]["location"]["s3Location"][
                    "uri"
                ].endswith(f"{q.gold_doc_id}.txt")
                ans += q.gold_answer.lower() in r["output"]["text"].lower()
            n = len(kb.qa)
            rows.append({"config": name, "max_tokens": mt, "overlap_pct": ov, "chunks": local.db.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0],
                         "hit@1": round(hit1 / n, 3), "hit@5": round(hit5 / n, 3), "citation_correct": round(cite / n, 3), "answer_contains_gold": round(ans / n, 3), "embedded": ing["embedded"]})  # fmt: skip
    return rows


def run(quick: bool = False) -> dict:
    t = synth()
    ts = settings_from_template(t)
    kb = generate_knowledge_base(
        n_systems=6 if quick else 12, queries_per_kind=5 if quick else 10, seed=7
    )
    cfgs = {
        "stack default": (ts.max_tokens, ts.overlap_percentage),
        "small chunks": (40, 0),
        "tiny chunks + overlap": (15, 30),
    }
    return {
        "resources": sorted({v["Type"] for v in t["Resources"].values()}),
        "n_resources": len(t["Resources"]),
        "template_settings": ts.__dict__,
        "check_findings": check_template(t),
        "cfn_lint_errors": lint(t),
        "retrieval": evaluate(kb, ts, cfgs, 12),
        "n_docs": len(kb.docs),
        "n_questions": len(kb.qa),
        "cost_model": {
            "standby_replicas_off": monthly_cost_estimate(False),
            "standby_replicas_on": monthly_cost_estimate(True),
        },
        "standby_default": RagSettings().standby_replicas,
    }


def render(res: dict) -> str:
    return "\n".join(
        [
            "# Results (POC 055: REAL offline CDK synthesis + checks + local mirror pipeline; S3 MOCKED with moto; nothing deployed or run on AWS)",
            f"Synthesised {res['n_resources']} CloudFormation resources offline ({', '.join(t.split('::', 1)[1] for t in res['resources'])}). "
            f"Template checks: **{len(res['check_findings'])} findings**; cfn-lint errors: **{len(res['cfn_lint_errors'])}**. "
            f"Settings read back from the template: {res['template_settings']}.",
            f"\n## Local mirror retrieval ({res['n_docs']} synthetic documents, {res['n_questions']} questions; hashing embedder and extractive generator are stand-ins)\n",
            markdown_table(
                [
                    "config",
                    "chunks",
                    "hit@1",
                    "hit@5",
                    "citation correct",
                    "answer contains gold",
                    "chunks embedded",
                ],
                [
                    [
                        r["config"] + f" ({r['max_tokens']} tokens, {r['overlap_pct']}% overlap)",
                        r["chunks"],
                        r["hit@1"],
                        r["hit@5"],
                        r["citation_correct"],
                        r["answer_contains_gold"],
                        r["embedded"],
                    ]
                    for r in res["retrieval"]
                ],
            ),  # fmt: skip
            "\n## Modelled monthly cost (ASSUMED list prices; why this stack is never deployed by the repository)\n",
            markdown_table(
                [
                    "configuration",
                    "OpenSearch Serverless USD",
                    "Lambda USD",
                    "total excl. model tokens USD",
                ],
                [
                    [
                        k.replace("_", " "),
                        v["opensearch_serverless_usd"],
                        v["lambda_usd"],
                        v["total_excl_model_tokens_usd"],
                    ]
                    for k, v in res["cost_model"].items()
                ],
            ),  # fmt: skip
            "",
        ]
    )


def main() -> None:
    res = run(quick=bool(os.environ.get("DEMO_QUICK")))
    out = POC_DIR / "results"
    out.mkdir(exist_ok=True)
    write_json(out / "metrics.json", res)
    (out / "results.md").write_text(render(res))
    print(render(res))


if __name__ == "__main__":
    tempfile.tempdir = None
    main()
