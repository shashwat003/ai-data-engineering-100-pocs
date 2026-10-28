import csv
import pathlib

import numpy as np
import yaml

p = yaml.safe_load(open("params.yaml"))["prepare"]
rng = np.random.default_rng(p["seed"])
n = p["n_rows"]
X = rng.normal(0, 1, (n, 5))
y = (X @ np.array([1.5, -1.0, 0.5, 0.0, 0.8]) + rng.normal(0, 0.7, n) > 0).astype(int)
ids = np.arange(n)
order = rng.permutation(n)
cut = int(n * (1 - p["test_fraction"]))
pathlib.Path("data").mkdir(exist_ok=True)
for name, idx in (("train", order[:cut]), ("test", order[cut:])):
    with open(f"data/{name}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", *[f"x{i}" for i in range(5)], "y"])
        for i in sorted(idx):
            w.writerow([int(ids[i]), *np.round(X[i], 6), int(y[i])])
