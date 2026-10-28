#!/usr/bin/env python3
"""Generate docs/STATUS.md (maturity and verification per POC) and docs/FLAGSHIPS.md from the repository itself (stdlib only).

Reuses the reading logic of POC 100 (poc_portfolio_site.model), so the numbers here and the portfolio site agree.
`maturity` is a structural level (README sections, Mermaid, tests, results, limitations). It is NOT a claim that the
integration was exercised against real external services: that is what the `labels` column and each POC's evidence table say.
"""

import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pocs/100-poc-portfolio-site"))
from poc_portfolio_site.model import MATURITY, load_repo  # noqa: E402

BLOCKED: dict[int, str] = {}  # POCs that cannot be implemented even as a local variant
FLAGSHIPS = [
    (
        3,
        "Governed natural-language-to-SQL with a semantic layer: the broadest guard test-suite in the portfolio (131 parametrised test cases).",
    ),
    (
        98,
        "Reference enterprise RAG that composes earlier POCs (citations, PII, audit log, erasure) with 14 end-to-end scenarios.",
    ),
    (
        35,
        "Medallion lakehouse on DuckDB with SCD2, quarantine of known injected defects, replay-safety and a data-contract gate.",
    ),
    (
        39,
        "Change data capture from a real PostgreSQL 16 logical replication slot into a Parquet lake: snapshot/stream handoff, atomic transactions, crash-safe replay.",
    ),
    (
        97,
        "Platform seam testing: lakehouse + contract gate + incremental vector index + MLflow promotion gate + tracing, with replay and tamper tests.",
    ),
    (
        79,
        "Prompt-injection red team with measurable attack success rates against layered defences (76 parametrised test cases).",
    ),
    (
        27,
        "Sandbox for agent-generated code with per-layer attack outcomes measured and honestly scoped as defence in depth.",
    ),
    (
        65,
        "MLflow registry promotion through a bootstrap-interval gate with subgroup checks, signature checks and rollback.",
    ),
    (
        51,
        "Terraform data-lake foundation validated by plan and apply against a local AWS mock (no cloud account touched).",
    ),
    (
        77,
        "Data contracts as code with semver-checked evolution and a CI gate that blocks breaking changes (70 parametrised test cases).",
    ),
]
VENUE = {
    ".own-env": "CI: airflow job (own venv)",
    ".heavy-tests": "CI: heavy workflow (path-filtered)",
}


def is_pending(p) -> bool:
    return not p.built and p.dir is not None and (p.dir / ".pending-approval").exists()


def venue_pending(p) -> str:
    for m, v in VENUE.items():
        if m in p.markers:
            return v
    return "CI: fast (py3.12 + py3.13)"


def venue(p) -> str:
    if not p.built:
        return "-"
    for m, v in VENUE.items():
        if m in p.markers:
            return v
    return "CI: fast (py3.12 + py3.13)"


BLURB = {
    "LLM & RAG": "Retrieval, grounding, citations, evaluation and cost control for LLM applications.",
    "SLM": "Small and local models: quantisation, distillation, adaptation, serving trade-offs.",
    "Agents & Automation": "Tool-using agents with policy gates, sandboxing, human approval and evaluation.",
    "Data Engineering": "Lakehouse, CDC, streaming, orchestration, data products and quality gates.",
    "AWS & Cloud": "Infrastructure as code and AWS-shaped architectures validated locally, never deployed.",
    "MLOps & LLMOps": "Registries, promotion gates, drift, rollouts, tracing and cost.",
    "Governance & Security": "Data contracts, privacy, safety filters, red-teaming, audit and policy as code.",
    "ML & Analytics": "Forecasting, causal inference, recommendation, entity resolution, fairness.",
    "Capstone": "Reference architectures composed from earlier POCs, and the portfolio tooling itself.",
}


def anchor(title: str) -> str:
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def inject(path: Path, marker: str, body: str) -> None:
    text = path.read_text()
    pat = re.compile(rf"(<!-- {marker}:START -->\n).*?(<!-- {marker}:END -->)", re.DOTALL)
    if not pat.search(text):
        raise SystemExit(f"{path}: marker {marker} missing")
    path.write_text(pat.sub(lambda m: f"{m.group(1)}{body}\n{m.group(2)}", text))


def build_readme(pocs, fid, by_id) -> None:
    readme = ROOT / "README.md"
    cats: dict[str, list] = {}
    for p in pocs:
        cats.setdefault(p.category, []).append(p)
    n_full = sum(1 for p in pocs if fid[p.id] == "full")
    summary = [
        f"**{len(pocs)} POCs: {n_full} full-scope, {len(pocs) - n_full} approved local variants.**",
        "",
        "| Area | POCs | Full / variant | Focus |",
        "|---|---|---|---|",
    ]
    for c, ps in cats.items():
        f = sum(1 for p in ps if fid[p.id] == "full")
        summary.append(
            f"| [{c}](#{anchor(c)}) | {len(ps)} | {f} / {len(ps) - f} | {BLURB.get(c, '')} |"
        )
    inject(readme, "SUMMARY", "\n".join(summary) + "\n")

    fl = ["| # | Project | Fidelity | What it demonstrates |", "|---|---|---|---|"]
    for pid, why in FLAGSHIPS:
        p = by_id[pid]
        fl.append(f"| {pid:03d} | [{p.slug}](pocs/{p.dir.name}) | {fid[pid]} | {why} |")
    inject(readme, "FLAGSHIPS", "\n".join(fl) + "\n")

    var = ["| # | Local variant | Category |", "|---|---|---|"]
    for p in pocs:
        if fid[p.id] == "local-variant":
            var.append(f"| {p.id:03d} | [{p.title}](pocs/{p.dir.name}) | {p.category} |")
    inject(readme, "VARIANTS", "\n".join(var) + "\n")

    cat = ["Jump to: " + " · ".join(f"[{c}](#{anchor(c)})" for c in cats), ""]
    for c, ps in cats.items():
        cat += [
            f"### {c}",
            "",
            f"{BLURB.get(c, '')} ({len(ps)} POCs)",
            "",
            "| # | POC | What it shows | Fidelity |",
            "|---|---|---|---|",
        ]
        for p in ps:
            cat.append(f"| {p.id:03d} | [{p.slug}](pocs/{p.dir.name}) | {p.title} | {fid[p.id]} |")
        cat.append("")
    inject(readme, "CATALOGUE", "\n".join(cat))


def main() -> None:
    pocs = load_repo(ROOT)
    fid = {
        int(r["id"]): r.get("fidelity", "full")
        for r in csv.DictReader((ROOT / "catalogue/pocs.csv").open())
    }
    built = [p for p in pocs if p.built]
    lv = Counter("pending approval" if is_pending(p) else MATURITY[p.maturity] for p in pocs)
    lab = Counter(label for p in built for label in p.labels)
    out = [
        "# Maturity and verification status (generated by `scripts/build_status.py` - do not edit by hand)\n",
        f"{len(built)} of {len(pocs)} POCs built ({sum(1 for p in built if fid[p.id] == 'full')} `full`: implemented as briefed with the named open-source components running locally; {sum(1 for p in built if fid[p.id] == 'local-variant')} `local-variant`: a service, model or hardware from the brief is replaced by a local or simulated stand-in, named in the POC title). Maturity levels: **{', '.join(f'{k} {v}' for k, v in lv.items())}**.\n",
        "`maturity`: planned (nothing) < scaffold (directory only) < tested (tests exist) < verified (tests + results + Mermaid + required README sections + limitations). "
        "This is a **structural** level checked by POC 100; it does not mean the POC was exercised against real external services. "
        "That is stated per POC in the `labels` column (vocabulary found in its README *Evidence status* table: simulated, mock, modelled, tested; **unverified** = the table lists at least one claim or integration as not run / not tested) and in the POC's own table, which is the authority.\n",
        f"Evidence labels across built POCs: {', '.join(f'{k} ({v})' for k, v in sorted(lab.items()))}.\n",
        "| # | POC | category | fidelity | maturity | tests (static count) | labels | runs where | catalogue `runs_local` (planned) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for p in pocs:
        if is_pending(p):
            lab = ", ".join(sorted(p.labels)) or "-"
            out.append(
                f"| {p.id} | [{p.slug}](../pocs/{p.dir.name}) | {p.category} | {fid[p.id]} | **implemented variant, pending approval** | {p.n_tests} | {lab} | {venue_pending(p)} | {p.runs_local} |"
            )
        elif p.built:
            out.append(
                f"| {p.id} | [{p.slug}](../pocs/{p.dir.name}) | {p.category} | {fid[p.id]} | {MATURITY[p.maturity]} | {p.n_tests} | "
                f"{', '.join(sorted(p.labels)) or '-'} | {venue(p)} | {p.runs_local} |"
            )
        else:
            why = BLOCKED.get(p.id, "not built")
            out.append(
                f"| {p.id} | {p.slug} | {p.category} | {fid[p.id]} | planned | 0 | - | **blocked**: {why} | {p.runs_local} |"
            )
    total_tests = sum(p.n_tests for p in built)
    out.append(
        f"\nStatic count of test functions in built POCs: {total_tests} (parametrised tests count once; shared libraries are tested separately in `libs/tests`).\n"
    )
    (ROOT / "docs/STATUS.md").write_text("\n".join(out) + "\n")

    fl = [
        "# Ten flagship projects (generated by `scripts/build_status.py`)\n",
        "Chosen for breadth (RAG, agents, data engineering, MLOps, security, cloud-as-code) and for the strength of their evidence, not for size. "
        "Each runs locally with `make -C pocs/<dir> demo` (the demo writes `results/`, which the README embeds) and `make -C pocs/<dir> test`.\n",
        "| # | POC | fidelity | why it is a flagship | test functions | labels | needs |",
        "|---|---|---|---|---|---|---|",
    ]
    by_id = {p.id: p for p in pocs}
    for pid, why in FLAGSHIPS:
        p = by_id[pid]
        fl.append(
            f"| {pid} | [{p.title}](../pocs/{p.dir.name}) | {fid[pid]} | {why} | {p.n_tests} | {', '.join(sorted(p.labels)) or '-'} | {venue(p)} |"
        )
    fl.append("")
    for pid, _ in FLAGSHIPS:
        p = by_id[pid]
        fl.append(
            f"## {pid} {p.slug}\n\n**Evidence:** {p.evidence}\n\n```bash\nmake -C pocs/{p.dir.name} test PY=python\nmake -C pocs/{p.dir.name} demo PY=python\n```\n"
        )
    (ROOT / "docs/FLAGSHIPS.md").write_text("\n".join(fl) + "\n")
    build_readme(pocs, fid, by_id)
    print(f"OK: status for {len(pocs)} POCs; {total_tests} tests; levels {dict(lv)}")


if __name__ == "__main__":
    main()
