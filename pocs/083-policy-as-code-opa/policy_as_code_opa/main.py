"""POC 083: evaluate an RBAC+ABAC Rego policy with real OPA; check it against a Python reference."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .engine import decide_many, opa_version, reference
from .scenarios import SCENARIOS, random_requests

POC_DIR = Path(__file__).resolve().parent.parent
FIELDS = [("user", "active"), ("user", "roles"), ("user", "clearance"), ("user", "region"),
          ("user", "department"), ("resource", "classification"), ("resource", "region"),
          ("resource", "owner_dept"), ("resource", "pii"), ("context", "purpose"), ("context", "mfa")]  # fmt: skip


def missing_attribute_report() -> dict:
    """Drop each attribute from an otherwise-allowed request: every one must flip it to deny.

    Two baselines: a PII write with a valid purpose (exercises all attributes but the PII flag) and
    a non-PII write with a bad purpose (allowed only because pii is explicitly false).
    """
    from .scenarios import req

    pii_write = req(
        {"roles": ["engineer"]}, {"classification": "confidential", "pii": True}, "write"
    )
    no_pii = req(
        {"roles": ["engineer"]},
        {"classification": "confidential"},
        "write",
        {"purpose": "marketing"},
    )
    cases = [(pii_write, sec, key) for sec, key in FIELDS if key != "pii"] + [
        (no_pii, "resource", "pii")
    ]
    reqs, names = [], []
    for base, sec, key in cases:
        r = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
        r[sec].pop(key, None)
        reqs.append(r)
        names.append(f"{sec}.{key}")
    out = decide_many([pii_write, no_pii] + reqs)
    assert out[0]["allow"] and out[1]["allow"], "baselines must be allowed"
    return {n: o["allow"] for n, o in zip(names, out[2:], strict=True)}


def run(n_random: int = 1500) -> dict:
    ds = decide_many([s[1] for s in SCENARIOS])
    scen = [
        {
            "name": name, "allow": d["allow"], "reasons": d["reasons"],
            "expected_allow": exp, "expected_reasons": reasons,
            "ok": d["allow"] == exp and d["reasons"] == reasons,
        }
        for (name, _, exp, reasons), d in zip(SCENARIOS, ds, strict=True)
    ]  # fmt: skip
    rr = random_requests(n_random)
    got = decide_many(rr)
    diffs = [i for i, (r, g) in enumerate(zip(rr, got, strict=True)) if reference(r) != g]
    allowed = sum(g["allow"] for g in got)
    return {
        "opa_version": opa_version(),
        "scenarios": scen,
        "scenarios_ok": sum(s["ok"] for s in scen),
        "random_requests": n_random,
        "random_allowed": allowed,
        "differential_mismatches": len(diffs),
        "missing_attribute_allowed": [k for k, v in missing_attribute_report().items() if v],
    }


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [(s["name"], "allow" if s["allow"] else "deny", ", ".join(s["reasons"]) or "-", "ok" if s["ok"] else "FAIL")
            for s in res["scenarios"]]  # fmt: skip
    md = (
        f"# Results: RBAC+ABAC data access policy evaluated by real OPA {res['opa_version']}\n\n"
        f"{res['scenarios_ok']}/{len(res['scenarios'])} named scenarios match expectations. "
        f"Differential test: {res['random_requests']} seeded random requests "
        f"({res['random_allowed']} allowed), Rego vs independent Python reference: "
        f"{res['differential_mismatches']} mismatches. Missing-attribute check (each attribute removed "
        f"from an otherwise-allowed request): attributes that still allowed access = "
        f"{res['missing_attribute_allowed'] or 'none'}.\n\n"
        + markdown_table(["scenario", "decision", "reasons", "vs expected"], rows)
        + "\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
