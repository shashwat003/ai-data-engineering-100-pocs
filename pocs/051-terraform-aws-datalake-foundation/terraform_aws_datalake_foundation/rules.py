"""Security rules evaluated on a Terraform plan or on applied state.

Each rule returns findings with status PASS, FAIL or DEFERRED. DEFERRED means the attribute is
"known after apply" in a plan, so the rule cannot judge it yet; it is never counted as a pass and
must be re-checked on the applied state (see `main.py`).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from .inventory import Res

PASS, FAIL, DEFERRED = "PASS", "FAIL", "DEFERRED"
REQUIRED_TAGS = ("Project", "Environment", "ManagedBy", "DataClassification")
BUCKET_SIBLINGS = {
    "public_access_block": "aws_s3_bucket_public_access_block",
    "encryption": "aws_s3_bucket_server_side_encryption_configuration",
    "versioning": "aws_s3_bucket_versioning",
    "ownership": "aws_s3_bucket_ownership_controls",
    "logging": "aws_s3_bucket_logging",
    "lifecycle": "aws_s3_bucket_lifecycle_configuration",
    "policy": "aws_s3_bucket_policy",
}
# Exact action sets each role may be granted (least privilege, golden values).
ROLE_ALLOWED = {
    "ingest": {
        "s3:PutObject",
        "s3:AbortMultipartUpload",
        "s3:ListBucket",
        "kms:GenerateDataKey",
        "kms:Encrypt",
        "kms:DescribeKey",
    },  # fmt: skip
    "transform": {
        "s3:GetObject",
        "s3:PutObject",
        "s3:AbortMultipartUpload",
        "s3:ListBucket",
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:Encrypt",
        "kms:DescribeKey",
    },  # fmt: skip
    "analyst": {"s3:GetObject", "s3:ListBucket", "kms:Decrypt", "kms:DescribeKey"},
}


@dataclass(frozen=True)
class Finding:
    rule: str
    status: str
    address: str
    detail: str


def _list(x) -> list:
    return x if isinstance(x, list) else ([] if x is None else [x])


def statements(policy_json: str | dict) -> list[dict]:
    doc = json.loads(policy_json) if isinstance(policy_json, str) else policy_json
    return [s for s in _list(doc.get("Statement")) if isinstance(s, dict)]


def _buckets(res: list[Res]) -> list[Res]:
    return [r for r in res if r.type == "aws_s3_bucket"]


def _sibling(res: list[Res], bucket: Res, kind: str) -> Res | None:
    want = BUCKET_SIBLINGS[kind]
    return next((r for r in res if r.type == want and r.group == bucket.group), None)


def _first(v):
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, dict) else {})


def is_logs_bucket(b: Res) -> bool:
    return (b.values.get("tags") or {}).get("Layer") == "logs"


# ---------------------------------------------------------------------------------------- rules


def s3_public_access_blocked(res: list[Res]) -> list[Finding]:
    out = []
    for b in _buckets(res):
        p = _sibling(res, b, "public_access_block")
        flags = (
            "block_public_acls",
            "block_public_policy",
            "ignore_public_acls",
            "restrict_public_buckets",
        )
        ok = p is not None and all(p.get(f) is True for f in flags)
        out.append(Finding("s3-public-access-blocked", PASS if ok else FAIL, b.address,
                           "all four public-access-block flags set" if ok else "missing or incomplete public access block"))  # fmt: skip
    return out


def s3_encrypted_with_cmk(res: list[Res]) -> list[Finding]:
    out = []
    for b in _buckets(res):
        e = _sibling(res, b, "encryption")
        rule = _first(e.get("rule")) if e else {}
        default = _first(rule.get("apply_server_side_encryption_by_default"))
        algo, key = default.get("sse_algorithm"), default.get("kms_master_key_id")
        if e is None:
            out.append(
                Finding("s3-encrypted-with-cmk", FAIL, b.address, "no encryption configuration")
            )
        elif is_logs_bucket(
            b
        ):  # S3 access-log targets cannot use KMS; SSE-S3 is the documented exception
            out.append(
                Finding(
                    "s3-encrypted-with-cmk",
                    PASS if algo == "AES256" else FAIL,
                    b.address,
                    f"log bucket uses {algo}",
                )
            )
        elif algo == "aws:kms" and (
            key or '"kms_master_key_id": true' in json.dumps(e.unknown)
        ):  # key id known after apply
            out.append(
                Finding(
                    "s3-encrypted-with-cmk",
                    PASS,
                    b.address,
                    "SSE-KMS with a customer-managed key, bucket key on"
                    if rule.get("bucket_key_enabled")
                    else "SSE-KMS",
                )
            )
        else:
            out.append(
                Finding(
                    "s3-encrypted-with-cmk", FAIL, b.address, f"algorithm {algo!r}, key {key!r}"
                )
            )
    return out


def s3_versioned(res: list[Res]) -> list[Finding]:
    out = []
    for b in _buckets(res):
        v = _sibling(res, b, "versioning")
        ok = v is not None and _first(v.get("versioning_configuration")).get("status") == "Enabled"
        out.append(
            Finding(
                "s3-versioning-enabled",
                PASS if ok else FAIL,
                b.address,
                "versioning enabled" if ok else "versioning not enabled",
            )
        )
    return out


def s3_owner_enforced(res: list[Res]) -> list[Finding]:
    out = []
    for b in _buckets(res):
        o = _sibling(res, b, "ownership")
        ok = (
            o is not None and _first(o.get("rule")).get("object_ownership") == "BucketOwnerEnforced"
        )
        out.append(
            Finding(
                "s3-acls-disabled",
                PASS if ok else FAIL,
                b.address,
                "BucketOwnerEnforced" if ok else "ACLs not disabled",
            )
        )
    return out


def s3_access_logging(res: list[Res]) -> list[Finding]:
    out = []
    for b in _buckets(res):
        if is_logs_bucket(b):
            continue
        lg = _sibling(res, b, "logging")
        out.append(
            Finding(
                "s3-access-logging",
                PASS if lg else FAIL,
                b.address,
                "server access logging configured" if lg else "no access logging",
            )
        )
    return out


def s3_lifecycle_cleanup(res: list[Res]) -> list[Finding]:
    out = []
    for b in _buckets(res):
        lc = _sibling(res, b, "lifecycle")
        rules = _list(lc.get("rule")) if lc else []
        ok = any(_first(r.get("abort_incomplete_multipart_upload")) for r in rules)
        out.append(
            Finding(
                "s3-lifecycle-cleanup",
                PASS if ok else FAIL,
                b.address,
                "aborts incomplete multipart uploads" if ok else "no lifecycle cleanup",
            )
        )
    return out


def s3_policy_denies_insecure_transport(res: list[Res]) -> list[Finding]:
    out = []
    for b in _buckets(res):
        p = _sibling(res, b, "policy")
        if p is None:
            out.append(Finding("s3-tls-only", FAIL, b.address, "no bucket policy"))
        elif p.is_unknown("policy"):
            out.append(Finding("s3-tls-only", DEFERRED, b.address, "policy known after apply"))
        else:
            ok = any(s.get("Effect") == "Deny" and "false" in map(str.lower, map(str, _list(s.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport"))))
                     for s in statements(p.get("policy")))  # fmt: skip
            out.append(
                Finding(
                    "s3-tls-only",
                    PASS if ok else FAIL,
                    b.address,
                    "denies aws:SecureTransport=false" if ok else "no TLS-only deny statement",
                )
            )
    return out


def s3_policy_denies_unencrypted_puts(res: list[Res]) -> list[Finding]:
    out = []
    for b in _buckets(res):
        if is_logs_bucket(b):
            continue
        p = _sibling(res, b, "policy")
        if p is None:
            out.append(Finding("s3-deny-unencrypted-put", FAIL, b.address, "no bucket policy"))
        elif p.is_unknown("policy"):
            out.append(
                Finding("s3-deny-unencrypted-put", DEFERRED, b.address, "policy known after apply")
            )
        else:
            ok = any(
                s.get("Effect") == "Deny"
                and "s3:x-amz-server-side-encryption" in json.dumps(s.get("Condition", {}))
                for s in statements(p.get("policy"))
            )
            out.append(
                Finding(
                    "s3-deny-unencrypted-put",
                    PASS if ok else FAIL,
                    b.address,
                    "denies PutObject without aws:kms" if ok else "unencrypted uploads allowed",
                )
            )
    return out


def kms_rotation(res: list[Res]) -> list[Finding]:
    return [Finding("kms-rotation-enabled", PASS if k.get("enable_key_rotation") is True else FAIL, k.address,
                    "automatic rotation on" if k.get("enable_key_rotation") is True else "key rotation disabled")
            for k in res if k.type == "aws_kms_key"]  # fmt: skip


def kms_deletion_window(res: list[Res]) -> list[Finding]:
    return [Finding("kms-deletion-window", PASS if (k.get("deletion_window_in_days") or 0) >= 7 else FAIL, k.address,
                    f"{k.get('deletion_window_in_days')} days") for k in res if k.type == "aws_kms_key"]  # fmt: skip


def iam_trust_not_open(res: list[Res]) -> list[Finding]:
    out = []
    for r in (x for x in res if x.type == "aws_iam_role"):
        if r.is_unknown("assume_role_policy"):
            out.append(Finding("iam-trust-not-open", DEFERRED, r.address, "known after apply"))
            continue
        bad = []
        for s in statements(r.get("assume_role_policy")):
            principal = s.get("Principal")
            vals = _list(principal.get("AWS")) if isinstance(principal, dict) else _list(principal)
            if "*" in vals or any(str(v).endswith(":root") and "123456789012" == "" for v in vals):
                bad.append(s)
        out.append(
            Finding(
                "iam-trust-not-open",
                FAIL if bad else PASS,
                r.address,
                "wildcard principal" if bad else "named principals only",
            )
        )
    return out


def iam_no_wildcards(res: list[Res]) -> list[Finding]:
    out = []
    for r in (x for x in res if x.type in ("aws_iam_role_policy", "aws_iam_policy")):
        if r.is_unknown("policy"):
            out.append(Finding("iam-no-wildcards", DEFERRED, r.address, "policy known after apply"))
            continue
        bad = []
        for s in statements(r.get("policy")):
            if s.get("Effect") != "Allow":
                continue
            actions, resources = _list(s.get("Action")), _list(s.get("Resource"))
            if any(a == "*" or a.endswith(":*") for a in actions) or "*" in resources:
                bad.append(s.get("Sid", "?"))
        out.append(
            Finding(
                "iam-no-wildcards",
                FAIL if bad else PASS,
                r.address,
                f"wildcard allow in {bad}" if bad else "no wildcard Allow statements",
            )
        )
    return out


def iam_least_privilege(res: list[Res]) -> list[Finding]:
    out = []
    for r in (x for x in res if x.type == "aws_iam_role_policy"):
        role = next((k for k in ROLE_ALLOWED if r.index == k), None)
        if role is None or r.is_unknown("policy"):
            continue
        granted = {
            a
            for s in statements(r.get("policy"))
            if s.get("Effect") == "Allow"
            for a in _list(s.get("Action"))
        }
        extra, missing = sorted(granted - ROLE_ALLOWED[role]), sorted(ROLE_ALLOWED[role] - granted)
        out.append(Finding("iam-least-privilege", FAIL if extra else PASS, r.address,
                           f"{role}: unexpected {extra}" if extra else f"{role}: {len(granted)} actions, none beyond the allowed set" + (f" (unused: {missing})" if missing else "")))  # fmt: skip
    return out


def iam_explicit_denies(res: list[Res]) -> list[Finding]:
    out = []
    for r in (x for x in res if x.type == "aws_iam_role_policy"):
        if r.is_unknown("policy"):
            continue
        denied = {
            a
            for s in statements(r.get("policy"))
            if s.get("Effect") == "Deny"
            for a in _list(s.get("Action"))
        }
        need = {"s3:DeleteObject", "s3:PutBucketPolicy", "kms:ScheduleKeyDeletion"}
        out.append(
            Finding(
                "iam-explicit-denies",
                PASS if need <= denied else FAIL,
                r.address,
                "destructive actions explicitly denied"
                if need <= denied
                else f"missing denies {sorted(need - denied)}",
            )
        )
    return out


def required_tags(res: list[Res]) -> list[Finding]:
    out = []
    for r in res:
        if r.type in ("aws_s3_bucket", "aws_kms_key", "aws_iam_role"):
            tags = r.get("tags") or {}
            missing = [t for t in REQUIRED_TAGS if t not in tags]
            out.append(
                Finding(
                    "required-tags",
                    FAIL if missing else PASS,
                    r.address,
                    f"missing {missing}" if missing else "all required tags",
                )
            )
    return out


def budget_guard(res: list[Res]) -> list[Finding]:
    ok = any(r.type == "aws_budgets_budget" and _list(r.get("notification")) for r in res)
    return [
        Finding(
            "budget-guard",
            PASS if ok else FAIL,
            "(stack)",
            "budget with alert notifications present" if ok else "no cost budget with alerts",
        )
    ]


PLAN_RULES: list[Callable[[list[Res]], list[Finding]]] = [
    s3_public_access_blocked, s3_encrypted_with_cmk, s3_versioned, s3_owner_enforced,
    s3_access_logging, s3_lifecycle_cleanup, s3_policy_denies_insecure_transport,
    s3_policy_denies_unencrypted_puts, kms_rotation, kms_deletion_window, iam_trust_not_open,
    iam_no_wildcards, iam_least_privilege, iam_explicit_denies, required_tags, budget_guard,
]  # fmt: skip


def evaluate(res: list[Res]) -> list[Finding]:
    return [f for rule in PLAN_RULES for f in rule(res)]


def summarise(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for f in findings:
        counts[f.status] += 1
    return {k: counts.get(k, 0) for k in (PASS, FAIL, DEFERRED)}
