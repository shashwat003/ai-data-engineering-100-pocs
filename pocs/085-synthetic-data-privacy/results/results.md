# Results: synthetic data fidelity, utility and privacy (2000 real training rows, 1000 holdout)

Train-on-real test-on-holdout AUC (ceiling): 0.6409. TSTR = train on synthetic, test on real holdout. DCR ratio = median distance to the nearest synthetic row for training members divided by the same for non-members (about 1 is fine, well below 1 means members are closer than strangers). MIA AUC is a distance-based membership attack (0.5 = no signal).

| synthesizer | KS mean | TVD mean | corr diff | TSTR AUC | DCR ratio | exact copies | MIA AUC |
|---|---|---|---|---|---|---|---|
| independent | 0.023 | 0.023 | 0.269 | 0.528 | 0.952 | 0.000 | 0.538 |
| copula | 0.022 | 0.013 | 0.027 | 0.596 | 0.984 | 0.000 | 0.506 |
| memoriser | 0.024 | 0.012 | 0.013 | 0.632 | 0.049 | 0.000 | 0.816 |
| copula (eps=0.1 marginals) | 0.052 | 0.026 | 0.037 | 0.626 | 0.975 | 0.000 | 0.518 |
| copula (eps=1.0 marginals) | 0.021 | 0.014 | 0.033 | 0.631 | 0.984 | 0.000 | 0.510 |
| copula (eps=10.0 marginals) | 0.021 | 0.013 | 0.033 | 0.631 | 0.988 | 0.000 | 0.506 |

Epsilon applies to the marginal histograms only; the copula correlation matrix is not privatised, so these runs are NOT end-to-end differentially private.
