"""POC 091: who should get the retention offer? Churn risk vs uplift learners, judged on randomised data."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .evaluate import run

POC_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "results.json", r)
    rows = [
        (
            n,
            m["qini_per_1000"],
            m["rank_corr_with_true_effect"],
            f"{m['gain_budget_ipw'][0]} [{m['gain_budget_ipw'][1]}, {m['gain_budget_ipw'][2]}]",
            m["gain_budget_true"],
            f"{m['gain_value_rule_ipw'][0]} [{m['gain_value_rule_ipw'][1]}, {m['gain_value_rule_ipw'][2]}]",
            m["gain_value_rule_true"],
            m["treated_share_value_rule"],
            m["sleeping_dogs_treated_budget"],
        )
        for n, m in r["models"].items()
    ]
    md = (
        f"# Results: retention offer targeting on {r['n_test']} held-out customers from a randomised experiment (synthetic, true effects known)\n\n"
        f"Randomisation check: largest standardised mean difference between arms {r['balance_smd']}. Gain = profit per customer per year versus offering nobody, where profit is retained annual value minus the offer cost (60) if treated. "
        f"'IPW' estimates it from the randomised test outcomes with a 95% bootstrap interval, 'true' uses the generator's probabilities. Profit with no offers (IPW): {r['baselines_ipw_profit_per_customer']['treat none']}; offering everyone: {r['baselines_ipw_profit_per_customer']['treat everyone']} (true {r['baselines_true_profit_per_customer']['treat everyone']}).\n\n"
        + markdown_table(
            [
                "ranking",
                "Qini / 1000",
                "rank corr. with true effect",
                f"gain, top {int(r['budget_fraction'] * 100)}% budget (IPW, 95% CI)",
                "same (true)",
                "gain, treat if expected gain > cost (IPW, 95% CI)",
                "true",
                "share treated by value rule",
                "share of budget that goes to sleeping dogs",
            ],
            rows,
        )
        + "\n\nX-learner calibration by predicted-uplift decile (reduction in churn probability):\n\n"
        + markdown_table(
            ["decile", "predicted", "observed (randomised)", "true"],
            [
                (d["decile"], d["predicted"], d["observed"], d["true"])
                for d in r["x_learner_deciles"]
            ],
        )
        + "\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
