# Results: 16 series (12 stores) x 6 rolling origins, horizon 8

Base forecasts are damped-trend seasonal Holt-Winters fitted to every series separately. RMSSE is scaled by the in-sample one-step naive error (lower is better). Coherence gap = largest violation of 'parent equals sum of children' (0 means coherent).

| method | total | region | store | mean over levels | max coherence gap |
|---|---|---|---|---|---|
| base (incoherent) | 0.764 | 0.754 | 0.652 | 0.723 | 1.4e+01 |
| bottom-up | 0.782 | 0.752 | 0.652 | 0.729 | 0.0e+00 |
| top-down | 0.764 | 1.254 | 1.323 | 1.114 | 2.3e-13 |
| OLS | 0.758 | 0.755 | 0.654 | 0.722 | 6.8e-13 |
| WLS (structural) | 0.760 | 0.751 | 0.652 | 0.721 | 9.1e-13 |
| MinT (shrink) | 0.768 | 0.750 | 0.649 | 0.722 | 4.5e-13 |

Across 6 different random hierarchies (60 observations, 3 origins), change in mean RMSSE versus the base forecasts (negative = better):

| method | mean change % | 95% bootstrap CI | better than base in |
|---|---|---|---|
| bottom-up | -0.500 | [-2.73, 1.21] | 4/6 |
| top-down | 35.770 | [31.94, 40.77] | 0/6 |
| OLS | 0.060 | [-0.2, 0.33] | 3/6 |
| WLS (structural) | -0.390 | [-1.32, 0.26] | 3/6 |
| MinT (shrink) | -0.820 | [-2.38, 0.15] | 4/6 |

Best reconciled method on the first table: WLS (structural) (-0.3% versus the incoherent base forecasts on the mean RMSSE).
