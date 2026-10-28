"""Windowed drift monitor with a Bonferroni-corrected, persistence-based alert policy."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .detectors import chi2_homogeneity, ks_2samp, proportion_z, psi_categorical, psi_numeric


@dataclass
class Policy:
    alpha: float = 0.01  # family-wise level per window, split across features (Bonferroni)
    psi_threshold: float = 0.2  # conventional "significant shift" cut-off
    persistence: int = 2  # consecutive flagged windows needed to alert


@dataclass
class Monitor:
    reference: dict[
        str, np.ndarray
    ]  # feature -> values (numeric floats with NaN for missing, or str)
    policy: Policy = field(default_factory=Policy)
    _streak: dict[str, int] = field(default_factory=dict)

    def check_window(self, window: dict[str, np.ndarray]) -> dict[str, dict]:
        per_feature_alpha = self.policy.alpha / len(self.reference)
        out = {}
        for name, ref in self.reference.items():
            cur = window[name]
            if ref.dtype.kind in "fi":
                r, c = ref[~np.isnan(ref)], cur[~np.isnan(cur)]
                ks, p = ks_2samp(r, c)
                psi = psi_numeric(r, c)
                miss_z, miss_p = proportion_z(
                    float(np.isnan(ref).mean()), len(ref), float(np.isnan(cur).mean()), len(cur)
                )
                flagged = (
                    (p < per_feature_alpha and psi > self.policy.psi_threshold / 2)
                    or psi > self.policy.psi_threshold
                    or miss_p < per_feature_alpha
                )
                out[name] = {
                    "ks": round(ks, 4),
                    "p": p,
                    "psi": round(psi, 4),
                    "missing_p": miss_p,
                    "flagged": flagged,
                }
            else:
                stat, p = chi2_homogeneity(ref, cur)
                psi = psi_categorical(ref, cur)
                flagged = p < per_feature_alpha or psi > self.policy.psi_threshold
                out[name] = {
                    "chi2": round(stat, 3),
                    "p": p,
                    "psi": round(psi, 4),
                    "flagged": flagged,
                }
            self._streak[name] = self._streak.get(name, 0) + 1 if out[name]["flagged"] else 0
            out[name]["alert"] = self._streak[name] >= self.policy.persistence
        return out
