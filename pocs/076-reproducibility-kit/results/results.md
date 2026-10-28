# Results: reproducibility checks (each experiment run in fresh processes, varying PYTHONHASHSEED and thread count; diagnosis repeats each run and varies one factor)

| experiment | identical across 4 runs | distinct outputs | diagnosed cause |
|---|---|---|---|
| seeded | yes | 1 | - |
| linear | yes | 1 | - |
| unseeded | NO | 4 | run-to-run randomness with everything fixed (unseeded RNG, clock, thread race) |
| hash_order | NO | 2 | PYTHONHASHSEED (set/dict-of-str iteration order) |
| hash_order_fixed | yes | 1 | - |
| thread_race | NO | 3 | run-to-run randomness with everything fixed (unseeded RNG, clock, thread race) |
| thread_fixed | yes | 1 | - |
| clock_id | NO | 4 | run-to-run randomness with everything fixed (unseeded RNG, clock, thread race) |

Manifest drift detection:

| change made | drift reported |
|---|---|
| nothing changed | none |
| data file edited | high:data |
| config edited | high:config_hash |
| seed changed | high:seed |
| package version differs | medium:packages |

Manifest id of the baseline: e2a2e621925b1c90. Tracked packages: {'numpy': '2.5.4', 'scikit-learn': '1.9.1', 'pandas': '3.0.6', 'scipy': '1.18.1'}.
