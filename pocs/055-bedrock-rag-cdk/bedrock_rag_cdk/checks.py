"""Offline architecture checks over a synthesised CloudFormation template (a small, explicit policy set, not a full scanner)."""

from __future__ import annotations

import json


def _statements(template: dict):
    for lid, r in template["Resources"].items():
        if r["Type"] == "AWS::IAM::Policy":
            for s in r["Properties"]["PolicyDocument"]["Statement"]:
                yield lid, s


def _as_list(x):
    return x if isinstance(x, list) else [x]


def check_template(t: dict) -> list[str]:
    """Return findings (empty list = pass)."""
    out: list[str] = []
    res = t["Resources"]
    types = {k: v["Type"] for k, v in res.items()}
    for lid, s in _statements(t):
        for a in _as_list(s["Action"]):
            if a == "*" or a.endswith(":*"):
                out.append(f"{lid}: wildcard action {a}")
        for r in _as_list(s["Resource"]):
            if r == "*":
                out.append(f"{lid}: wildcard resource for {s['Action']}")
    for lid, r in res.items():
        if r["Type"] == "AWS::S3::Bucket":
            p = r["Properties"]
            if "BucketEncryption" not in p:
                out.append(f"{lid}: bucket not encrypted")
            pab = p.get("PublicAccessBlockConfiguration", {})
            if not all(
                pab.get(k)
                for k in (
                    "BlockPublicAcls",
                    "BlockPublicPolicy",
                    "IgnorePublicAcls",
                    "RestrictPublicBuckets",
                )
            ):
                out.append(f"{lid}: public access not fully blocked")
        if r["Type"] == "AWS::IAM::Role":
            for st in r["Properties"]["AssumeRolePolicyDocument"]["Statement"]:
                pr = st.get("Principal", {}).get("Service")
                if pr == "bedrock.amazonaws.com" and "SourceAccount" not in json.dumps(
                    st.get("Condition", {})
                ):
                    out.append(
                        f"{lid}: bedrock trust policy lacks aws:SourceAccount (confused deputy)"
                    )
        if (
            r["Type"] == "AWS::Lambda::Function"
            and "ReservedConcurrentExecutions" not in r["Properties"]
        ):
            out.append(f"{lid}: no reserved concurrency (cost/abuse ceiling)")
        if r["Type"] == "AWS::OpenSearchServerless::AccessPolicy":
            if '"*"' in json.dumps(r["Properties"]["Policy"]) and '"Principal":["*"]' in json.dumps(
                r["Properties"]["Policy"]
            ).replace(" ", ""):
                out.append(f"{lid}: data access policy grants everyone")
    if not any(v == "AWS::S3::BucketPolicy" for v in types.values()):
        out.append("no bucket policy (SSL enforcement)")
    colls = [k for k, v in types.items() if v == "AWS::OpenSearchServerless::Collection"]
    pols = [
        r["Properties"]["Type"]
        for r in res.values()
        if r["Type"] == "AWS::OpenSearchServerless::SecurityPolicy"
    ]
    if colls and not {"encryption", "network"} <= set(pols):
        out.append("collection lacks encryption and network security policies")
    # the vector dimension of the index must match the embedding model
    idx = next(
        (v for v in res.values() if v["Type"] == "AWS::OpenSearchServerless::CollectionIndex"), None
    )
    if idx:
        dim = json.loads(idx["Properties"]["IndexSchema"])["mappings"]["properties"]["vector"][
            "dimension"
        ]
        kb = next(v for v in res.values() if v["Type"] == "AWS::Bedrock::KnowledgeBase")
        arn = json.dumps(kb["Properties"]["KnowledgeBaseConfiguration"])
        if "titan-embed-text-v2" in arn and dim not in (256, 512, 1024):
            out.append(f"index dimension {dim} is not a Titan v2 output size")
    ds = next(v for v in res.values() if v["Type"] == "AWS::Bedrock::DataSource")
    fixed = ds["Properties"]["VectorIngestionConfiguration"]["ChunkingConfiguration"][
        "FixedSizeChunkingConfiguration"
    ]
    if not 20 <= fixed["MaxTokens"] <= 8192 or not 1 <= fixed["OverlapPercentage"] <= 99:
        out.append("chunking configuration outside the documented range")
    return out


# ASSUMED list prices (verify against the current AWS price list); this is a model, not a quote.
OCU_HOUR_USD = 0.24
HOURS_PER_MONTH = 730


def monthly_cost_estimate(standby_replicas: bool = False, queries_per_month: int = 100_000) -> dict:
    ocus = (
        4 if standby_replicas else 2
    )  # ASSUMED minimums: (indexing + search) x (2 if standby replicas else 1)
    aoss = ocus * OCU_HOUR_USD * HOURS_PER_MONTH
    lam = (
        queries_per_month * 0.0000002 + queries_per_month * 3 * 0.0000000167 * 0.25 * 1
    )  # request + 3 s x 256 MB, rough
    return {
        "opensearch_serverless_usd": round(aoss, 2),
        "lambda_usd": round(lam, 2),
        "total_excl_model_tokens_usd": round(aoss + lam, 2),
    }
