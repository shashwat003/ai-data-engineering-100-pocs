# Results: 6 detectors on 5 synthetic streams (5-minute points, daily seasonality; 70 injected events in total)

Every threshold was set on separate anomaly-free streams to give at most 1.0 false alarm event per 1000 points; no threshold was tuned on the evaluation streams. An event counts as found if an alarm overlaps it (24 points of tolerance after its end).

| detector | event recall | false alarms / 1000 pts | median delay (points) | spike | level_shift | variance | dropout | seasonal_break | points/s |
|---|---|---|---|---|---|---|---|---|---|
| EWMA z-score | 0.586 | 0.690 | 1 | 0.800 | 0.800 | 0.930 | 0.070 | 0.200 | 2224351 |
| Rolling median/MAD | 0.500 | 0.830 | 9 | 0.470 | 0.800 | 0.800 | 0.130 | 0.200 | 24535 |
| Seasonal EWMA | 0.786 | 0.280 | 0 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 968485 |
| river HalfSpaceTrees | 0.643 | 0.490 | 0 | 0.730 | 0.930 | 1.000 | 0.070 | 0.400 | 10708 |
| river ADWIN (drift) | 0.329 | 0.900 | 25 | 0.000 | 1.000 | 0.130 | 0.000 | 0.600 | 1076559 |
| Missing-data run rule | 0.214 | 0.000 | 2 | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 6223324 |
