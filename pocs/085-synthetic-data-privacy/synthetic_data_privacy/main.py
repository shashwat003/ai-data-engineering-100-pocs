"""POC 085: three synthesizers, fidelity / utility / privacy metrics, and an epsilon sweep."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .data import CAT, LABEL, NUM, make_real
from .metrics import fidelity, privacy, tstr_auc
from .synth import Synth

POC_DIR = Path(__file__).resolve().parent.parent


def evaluate(kind: str, train, holdout, eps=None, n=None) -> dict:
    s = Synth(kind, NUM, CAT, LABEL, eps=eps, seed=3).fit(train)
    syn = s.sample(n or len(train))
    r = {"kind": kind, "eps": eps, **fidelity(train, syn, NUM, CAT)}
    r["tstr_auc"] = tstr_auc(syn, holdout, NUM, CAT, LABEL)
    r.update(privacy(train, holdout, syn, NUM, CAT))
    return r


def run(n_train: int = 2000, n_holdout: int = 1000, eps_list=(0.1, 1.0, 10.0)) -> dict:
    real = make_real(n_train + n_holdout)
    train, holdout = (
        real.iloc[:n_train].reset_index(drop=True),
        real.iloc[n_train:].reset_index(drop=True),
    )
    base_auc = tstr_auc(train, holdout, NUM, CAT, LABEL)  # train-on-real ceiling
    rows = [evaluate(k, train, holdout) for k in ("independent", "copula", "memoriser")]
    rows += [evaluate("copula", train, holdout, eps=e) for e in eps_list]
    return {"n_train": n_train, "n_holdout": n_holdout, "real_to_real_auc": base_auc, "rows": rows}


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    table = [
        (r["kind"] + (f" (eps={r['eps']} marginals)" if r["eps"] else ""), r["ks_mean"], r["tvd_mean"],
         r["corr_abs_diff"], r["tstr_auc"], r["dcr_ratio"], r["exact_copy_rate"], r["mia_auc"])
        for r in res["rows"]
    ]  # fmt: skip
    md = (
        f"# Results: synthetic data fidelity, utility and privacy ({res['n_train']} real training rows, "
        f"{res['n_holdout']} holdout)\n\n"
        f"Train-on-real test-on-holdout AUC (ceiling): {res['real_to_real_auc']}. "
        "TSTR = train on synthetic, test on real holdout. DCR ratio = median distance to the nearest synthetic "
        "row for training members divided by the same for non-members (about 1 is fine, well below 1 means "
        "members are closer than strangers). MIA AUC is a distance-based membership attack (0.5 = no signal).\n\n"
        + markdown_table(
            [
                "synthesizer",
                "KS mean",
                "TVD mean",
                "corr diff",
                "TSTR AUC",
                "DCR ratio",
                "exact copies",
                "MIA AUC",
            ],
            table,
        )
        + "\n\nEpsilon applies to the marginal histograms only; the copula correlation matrix is not "
        "privatised, so these runs are NOT end-to-end differentially private.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
