"""Plan checks and a cost model for the Redshift Serverless Terraform. PRICES ARE ASSUMED (check the current price list)."""

from __future__ import annotations

RPU_HOUR_USD = 0.375  # ASSUMED on-demand price per RPU-hour
BILLING_MIN_SECONDS = 60  # ASSUMED: queries are billed for at least 60 seconds of compute


def run_cost(rpu: int, seconds: float) -> float:
    """Modelled cost of keeping `rpu` RPUs busy for `seconds` (60 s minimum)."""
    if rpu < 8 or seconds < 0:
        raise ValueError("base capacity starts at 8 RPU")
    return round(rpu * RPU_HOUR_USD * max(seconds, BILLING_MIN_SECONDS) / 3600, 4)


def monthly_cost(rpu: int, runs_per_day: int, seconds_per_run: float, days: int = 30) -> dict:
    compute = run_cost(rpu, seconds_per_run) * runs_per_day * days
    return {
        "compute_usd": round(compute, 2),
        "idle_usd": 0.0,
        "rpu_hours": round(
            rpu * max(seconds_per_run, BILLING_MIN_SECONDS) / 3600 * runs_per_day * days, 2
        ),
    }


def check_plan(plan: dict, *, max_base_rpu: int = 32) -> list[str]:
    """Findings over `terraform show -json` output (empty list = pass)."""
    out: list[str] = []
    rc = {
        f"{r['type']}.{r['name']}": r["change"]["after"]
        for r in plan["resource_changes"]
        if "delete" not in r["change"]["actions"]
    }
    wg = next((v for k, v in rc.items() if k.startswith("aws_redshiftserverless_workgroup.")), None)
    ns = next((v for k, v in rc.items() if k.startswith("aws_redshiftserverless_namespace.")), None)
    if wg is None or ns is None:
        return ["plan lacks a Redshift Serverless namespace/workgroup"]
    if wg.get("publicly_accessible"):
        out.append("workgroup is publicly accessible")
    if not wg.get("enhanced_vpc_routing"):
        out.append("enhanced VPC routing is off: COPY/UNLOAD traffic leaves the VPC")
    if int(wg.get("base_capacity") or 0) > max_base_rpu:
        out.append(f"base capacity {wg['base_capacity']} RPU exceeds {max_base_rpu}")
    if not wg.get("max_capacity"):
        out.append("no max capacity: a runaway query can scale without a ceiling")
    if not ns.get("manage_admin_password"):
        out.append("admin password is not managed by Secrets Manager")
    if not ns.get("kms_key_id") and "aws_kms_key.warehouse" not in rc:
        out.append("namespace is not encrypted with a customer-managed key")
    if not any(k.startswith("aws_redshiftserverless_usage_limit.") for k in rc):
        out.append("no usage limit: there is no hard cost cap")
    for k, v in rc.items():
        if k.startswith("aws_iam_role_policy."):
            doc = v["policy"] if isinstance(v["policy"], str) else str(v["policy"])
            if '"Action":"*"' in doc.replace(" ", "") or '"Resource":"*"' in doc.replace(" ", ""):
                out.append(f"{k}: wildcard in IAM policy")
        if k.startswith("aws_iam_role.") and "SourceAccount" not in str(
            v.get("assume_role_policy")
        ):
            out.append(f"{k}: trust policy lacks aws:SourceAccount")
    if "aws_s3_bucket_public_access_block.landing" not in rc:
        out.append("landing bucket lacks a public access block")
    return out
