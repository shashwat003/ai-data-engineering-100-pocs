# Results (measured by `python -m pit_feature_store.main`)

Train 400 / test 200 customers. AUC of a logistic regression on [orders_30d, tickets_7d].

| training join | AUC (test, same join: looks great) | AUC at serving time (PIT features) |
|---|---|---|
| leaky_join | 0.976 | 0.623 |
| point_in_time | 0.779 | 0.779 |

- online store materialised 600 entities; parity mismatches vs offline PIT join: 0/50
- stale online record returns None: True; unknown entity returns None: True
