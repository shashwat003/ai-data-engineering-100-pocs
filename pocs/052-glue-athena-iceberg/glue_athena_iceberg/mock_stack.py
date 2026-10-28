"""Apply infra/ to a local moto server (via the POC 63 harness) and read the result back. Never touches real AWS."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra"


def terraform_available() -> bool:
    return shutil.which("terraform") is not None


def _tf(args, cwd, env, check=True):
    p = subprocess.run(
        ["terraform", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if check and p.returncode != 0:
        raise RuntimeError(
            f"terraform {' '.join(args)} failed:\n{p.stdout[-1200:]}\n{p.stderr[-1200:]}"
        )
    return p


def apply_and_inspect(aws) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    cache = Path(tempfile.gettempdir()) / "tf-plugin-cache"
    cache.mkdir(exist_ok=True)
    env.update(
        {
            "TF_IN_AUTOMATION": "1",
            "TF_INPUT": "0",
            "TF_PLUGIN_CACHE_DIR": str(cache),
            "AWS_ACCESS_KEY_ID": "mock",
            "AWS_SECRET_ACCESS_KEY": "mock",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    out: dict = {}
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "infra"
        shutil.copytree(INFRA, d, ignore=shutil.ignore_patterns(".terraform*", "*.tfstate*"))
        _tf(["init", "-backend=false", "-no-color"], d, env)
        v = ["-var", f"mock_port={aws.port}", "-no-color", "-lock=false"]
        _tf(["plan", "-out=full.tfplan", *v], d, env)  # plan WITH the workgroup: inspected below
        plan = json.loads(_tf(["show", "-json", "full.tfplan"], d, env).stdout)
        wg = next(
            r["change"]["after"]
            for r in plan["resource_changes"]
            if r["type"] == "aws_athena_workgroup"
        )["configuration"][0]
        out["planned_workgroup"] = {
            "enforced": wg["enforce_workgroup_configuration"],
            "scan_cutoff_bytes": wg["bytes_scanned_cutoff_per_query"],
            "result_encryption": wg["result_configuration"][0]["encryption_configuration"][0][
                "encryption_option"
            ],
            "engine": wg["engine_version"][0]["selected_engine_version"],
        }
        av = [*v, "-var", "create_workgroup=false"]
        _tf(["apply", "-auto-approve", *av], d, env)
        out["replan_exit_code"] = _tf(
            ["plan", "-detailed-exitcode", *av], d, env, check=False
        ).returncode
    glue, s3 = aws.client("glue"), aws.client("s3")
    tbl = glue.get_table(DatabaseName="lake", Name="orders")["Table"]
    out["glue_table_params"] = tbl.get("Parameters", {})
    out["glue_table_location"] = tbl["StorageDescriptor"]["Location"]
    out["results_bucket_encrypted"] = bool(
        s3.get_bucket_encryption(Bucket="demo-athena-results")["ServerSideEncryptionConfiguration"][
            "Rules"
        ]
    )
    out["lake_public_access_blocked"] = all(
        s3.get_public_access_block(Bucket="demo-iceberg-lake")[
            "PublicAccessBlockConfiguration"
        ].values()
    )
    return out
