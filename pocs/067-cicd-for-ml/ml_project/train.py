import hashlib
import pickle

import numpy as np
import yaml
from sklearn.linear_model import LogisticRegression

p = yaml.safe_load(open("params.yaml"))["train"]
d = np.genfromtxt("data/train.csv", delimiter=",", skip_header=1)
X, y = d[:, 1:6][:, p["features"]], d[:, 6].astype(int)
m = LogisticRegression(C=p["C"], random_state=p["seed"], max_iter=500).fit(X, y)
open("model.pkl", "wb").write(pickle.dumps({"model": m, "features": p["features"]}))
print("model sha256", hashlib.sha256(np.round(m.coef_, 10).tobytes()).hexdigest()[:16])
