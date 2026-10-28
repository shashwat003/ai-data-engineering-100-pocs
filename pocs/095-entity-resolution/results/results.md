# Results: 5366 synthetic person records, 3000 true entities, 3919 true duplicate pairs

Splink (probabilistic, DuckDB) trained without labels: u-probabilities from random sampling, m-probabilities by EM. Blocking scored 3,681 candidate pairs instead of 14,394,295 possible (blocking pair recall 0.892). Training and prediction took 6.1 s.

| method | pair precision | pair recall | pair F1 | B-cubed F1 | clusters | pairs linked |
|---|---|---|---|---|---|---|
| Splink @ 0.5 | 0.997 | 0.829 | 0.905 | 0.973 | 3193 | 3257 |
| Splink @ 0.8 | 0.999 | 0.800 | 0.889 | 0.967 | 3257 | 3137 |
| Splink @ 0.95 | 1.000 | 0.777 | 0.875 | 0.963 | 3290 | 3046 |
| Splink @ 0.99 | 1.000 | 0.714 | 0.833 | 0.944 | 3429 | 2799 |
| exact name+dob | 1.000 | 0.289 | 0.448 | 0.816 | 4462 | 1131 |
| fuzzy rules | 1.000 | 0.701 | 0.824 | 0.943 | 3446 | 2746 |

Scaling of blocking:

| records | all possible pairs | pairs scored after blocking | seconds |
|---|---|---|---|
| 1422 | 1,010,331 | 966 | 3.400 |
| 2862 | 4,094,091 | 2,012 | 5.900 |
| 5730 | 16,413,585 | 4,064 | 5.800 |

Clusters are connected components of the linked pairs; B-cubed scores the resulting entities, which punishes chains that merge different people.
