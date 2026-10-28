# Results: retention offer targeting on 20000 held-out customers from a randomised experiment (synthetic, true effects known)

Randomisation check: largest standardised mean difference between arms 0.032. Gain = profit per customer per year versus offering nobody, where profit is retained annual value minus the offer cost (60) if treated. 'IPW' estimates it from the randomised test outcomes with a 95% bootstrap interval, 'true' uses the generator's probabilities. Profit with no offers (IPW): 577.9; offering everyone: 586.21 (true 595.8).

| ranking | Qini / 1000 | rank corr. with true effect | gain, top 20% budget (IPW, 95% CI) | same (true) | gain, treat if expected gain > cost (IPW, 95% CI) | true | share treated by value rule | share of budget that goes to sleeping dogs |
|---|---|---|---|---|---|---|---|---|
| churn risk (naive) | 16.560 | 0.562 | 11.31 [4.02, 19.84] | 12.770 | 11.31 [4.02, 19.84] | 12.770 | 0.200 | 0.000 |
| S-learner | 27.000 | 0.915 | 20.31 [12.62, 28.3] | 26.040 | 30.41 [16.56, 43.13] | 42.300 | 0.505 | 0.000 |
| T-learner | 26.440 | 0.884 | 19.62 [11.69, 28.64] | 25.700 | 28.1 [14.21, 40.96] | 41.710 | 0.501 | 0.000 |
| X-learner | 27.330 | 0.917 | 16.0 [9.29, 24.04] | 24.910 | 28.93 [14.63, 43.09] | 42.490 | 0.539 | 0.000 |
| oracle (true effect) | 29.660 | 1.000 | 21.15 [14.57, 29.37] | 29.350 | 30.25 [17.79, 42.53] | 44.560 | 0.484 | 0.000 |
| random | 0.070 | 0.007 | -0.49 [-8.44, 8.36] | 4.380 | -0.49 [-8.44, 8.36] | 4.380 | 0.200 | 0.044 |

X-learner calibration by predicted-uplift decile (reduction in churn probability):

| decile | predicted | observed (randomised) | true |
|---|---|---|---|
| 1 | 0.266 | 0.261 | 0.279 |
| 2 | 0.211 | 0.237 | 0.232 |
| 3 | 0.180 | 0.209 | 0.205 |
| 4 | 0.151 | 0.155 | 0.164 |
| 5 | 0.122 | 0.125 | 0.116 |
| 6 | 0.092 | 0.064 | 0.077 |
| 7 | 0.063 | 0.033 | 0.040 |
| 8 | 0.039 | 0.020 | 0.020 |
| 9 | 0.015 | 0.012 | 0.008 |
| 10 | -0.023 | -0.012 | -0.016 |
