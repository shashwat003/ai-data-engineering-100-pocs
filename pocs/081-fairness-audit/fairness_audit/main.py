"""POC 081: audit a lending model for group bias, check proxies, mitigate, report the trade-off."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .audit import (
    bootstrap_ci,
    dp_stat,
    group_report,
    mitigate,
    passes_four_fifths,
    proxy_scan,
    train_eval,
)
from .data import FEATURES, make_lending

POC_DIR = Path(__file__).resolve().parent.parent


def run(seed: int = 7) -> dict:
    df = make_lending(seed=seed)
    out: dict = {"n": len(df), "approval_rate_by_group_labels": {}}
    out["approval_rate_by_group_labels"] = {
        g: round(float(x), 4) for g, x in df.groupby("group").approved.mean().items()
    }
    out["proxy_auc"] = proxy_scan(df[FEATURES], df.group)

    model, tr, te, cols = train_eval(df, FEATURES)
    pred = model.predict(te[cols])
    base = group_report(te.approved, pred, te.group)
    base["dp_ci95"] = bootstrap_ci(te.approved, pred, te.group, dp_stat)
    base["passes_four_fifths"] = passes_four_fifths(base)
    out["baseline"] = base

    # dropping the proxy shows how much of the gap it carries
    m2, tr2, te2, cols2 = train_eval(df, [c for c in FEATURES if c != "postcode_risk"])
    out["without_proxy"] = group_report(te2.approved, m2.predict(te2[cols2]), te2.group)

    # ground truth check: with identical ability the *true* qualified rate is the same
    qual = df.assign(q=(df.ability > 0).astype(int)).groupby("group").q.mean()
    out["true_qualified_rate"] = {g: round(float(x), 4) for g, x in qual.items()}

    for c in ("demographic_parity", "equalized_odds"):
        p = mitigate(model, tr, te, cols, c)
        r = group_report(te.approved, p, te.group)
        r["passes_four_fifths"] = passes_four_fifths(r)
        out[f"mitigated_{c}"] = r
    return out


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [
        (
            name,
            r["overall_accuracy"],
            r["demographic_parity_diff"],
            r["equalized_odds_diff"],
            r["disparate_impact_ratio"],
            "yes" if passes_four_fifths(r) else "NO",
        )
        for name, r in [
            ("baseline model", res["baseline"]),
            ("without proxy feature", res["without_proxy"]),
            ("threshold-optimised for demographic parity", res["mitigated_demographic_parity"]),
            ("threshold-optimised for equalized odds", res["mitigated_equalized_odds"]),
        ]
    ]
    md = (
        "# Results: lending model fairness audit (synthetic, planted bias)\n\n"
        f"Historical approval rate by group (the labels the model learns from): "
        f"{res['approval_rate_by_group_labels']}. True share of qualified applicants by group "
        f"(known from the generator): {res['true_qualified_rate']}. "
        f"Proxy scan (one-feature AUC for predicting group, 0.5 = no leak): {res['proxy_auc']}. "
        f"Baseline demographic-parity gap 95% bootstrap CI: {res['baseline']['dp_ci95']}.\n\n"
        + markdown_table(
            ["variant", "accuracy", "dp diff", "eq. odds diff", "impact ratio", "passes 4/5"], rows
        )
        + "\n\nAccuracy is measured against the *biased* historical labels, so a fairer model is "
        "expected to score lower on it; that gap is the visible cost of the intervention, not a defect.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
