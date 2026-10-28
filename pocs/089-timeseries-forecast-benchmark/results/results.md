# Results: 48 synthetic monthly series x 2 rolling origins, horizon 12

MASE is scaled by the in-sample seasonal-naive error (lower is better; 1.0 equals seasonal naive in-sample). The global LightGBM model is trained only on data before each cut. Foundation models: not run (no weights or network).

| model | sMAPE % | MASE | mean rank | trend_seasonal | stable_seasonal | changing_seasonality | level_shift | random_walk | intermittent |
|---|---|---|---|---|---|---|---|---|---|
| ETS (AICc) | 31.970 | 0.905 | 2.670 | 0.440 | 0.740 | 1.090 | 1.420 | 0.760 | 0.990 |
| combo: ETS+Theta+LightGBM | 32.260 | 1.016 | 3.540 | 0.530 | 0.820 | 1.440 | 1.540 | 0.810 | 0.960 |
| Theta (simplified) | 33.110 | 1.090 | 3.810 | 0.760 | 0.750 | 1.840 | 1.480 | 0.810 | 0.910 |
| seasonal-diff ARMA | 23.960 | 1.165 | 4.270 | 0.520 | 1.020 | 1.000 | 2.330 | 1.040 | 1.080 |
| seasonal naive | 18.940 | 1.245 | 4.750 | 1.040 | 1.020 | 1.050 | 2.180 | 1.120 | 1.060 |
| LightGBM global | 33.370 | 1.272 | 4.930 | 0.600 | 1.280 | 1.750 | 2.010 | 0.980 | 1.010 |
| naive | 27.280 | 2.383 | 5.790 | 1.440 | 3.940 | 4.880 | 1.960 | 0.770 | 1.300 |
| drift | 27.750 | 2.456 | 6.240 | 1.350 | 4.080 | 5.050 | 2.040 | 0.860 | 1.350 |

Best by MASE: ETS (AICc). Paired bootstrap 95% interval for its MASE minus seasonal naive's: [-0.444, -0.243] (negative = better).
