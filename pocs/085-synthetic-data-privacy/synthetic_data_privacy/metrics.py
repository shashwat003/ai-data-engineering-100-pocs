from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial import cKDTree
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score


def fidelity(real: pd.DataFrame, syn: pd.DataFrame, num: list[str], cat: list[str]) -> dict:
    ks = {c: float(stats.ks_2samp(real[c], syn[c]).statistic) for c in num}
    tvd = {}
    for c in cat:
        a, b = real[c].value_counts(normalize=True), syn[c].value_counts(normalize=True)
        keys = a.index.union(b.index)
        tvd[c] = float(
            0.5 * (a.reindex(keys, fill_value=0) - b.reindex(keys, fill_value=0)).abs().sum()
        )
    cr = real[num].corr(method="spearman").to_numpy()
    cs = syn[num].corr(method="spearman").to_numpy()
    iu = np.triu_indices(len(num), 1)
    return {
        "ks_mean": round(float(np.mean(list(ks.values()))), 4),
        "tvd_mean": round(float(np.mean(list(tvd.values()))), 4),
        "corr_abs_diff": round(float(np.abs(cr[iu] - cs[iu]).mean()), 4),
    }


def encoder(ref: pd.DataFrame, num: list[str], cat: list[str]):
    mu, sd = ref[num].mean(), ref[num].std().replace(0, 1)
    levels = {c: sorted(ref[c].unique()) for c in cat}

    def enc(df: pd.DataFrame) -> np.ndarray:
        parts = [((df[num] - mu) / sd).to_numpy()]
        for c in cat:
            parts.append(np.column_stack([(df[c] == lv).astype(float) for lv in levels[c]]))
        return np.hstack(parts)

    return enc


def tstr_auc(train: pd.DataFrame, test: pd.DataFrame, num, cat, label) -> float:
    """Train on `train`, evaluate on real `test`: does the synthetic data carry the signal?"""
    enc = encoder(train, num, cat)
    m = LogisticRegression(max_iter=500).fit(enc(train), train[label])
    return round(float(roc_auc_score(test[label], m.predict_proba(enc(test))[:, 1])), 4)


def privacy(train, holdout, syn, num, cat) -> dict:
    """Distance to closest record (DCR) and a distance-based membership-inference attack.

    For each of the `train` rows (members) and `holdout` rows (non-members) the attacker scores
    -distance to the nearest synthetic row. AUC 0.5 = no leakage; near 1 = members are identifiable.
    """
    enc = encoder(train, num, cat)
    tree = cKDTree(enc(syn))
    d_mem = tree.query(enc(train.sample(len(holdout), random_state=0)))[0]
    d_non = tree.query(enc(holdout))[0]
    auc = roc_auc_score(np.r_[np.ones(len(d_mem)), np.zeros(len(d_non))], -np.r_[d_mem, d_non])
    exact = float(np.mean(d_mem < 1e-9))
    return {
        "dcr_median_members": round(float(np.median(d_mem)), 4),
        "dcr_median_nonmembers": round(float(np.median(d_non)), 4),
        "dcr_ratio": round(float(np.median(d_mem) / max(np.median(d_non), 1e-12)), 4),
        "exact_copy_rate": round(exact, 4),
        "mia_auc": round(float(auc), 4),
    }
