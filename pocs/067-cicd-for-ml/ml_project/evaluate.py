import json
import pickle

import numpy as np
from sklearn.metrics import accuracy_score, roc_auc_score

bundle = pickle.load(open("model.pkl", "rb"))
m, feats = bundle["model"], bundle["features"]
d = np.genfromtxt("data/test.csv", delimiter=",", skip_header=1)
X, y = d[:, 1:6][:, feats], d[:, 6].astype(int)
p = m.predict_proba(X)[:, 1]
json.dump(
    {
        "auc": round(float(roc_auc_score(y, p)), 6),
        "accuracy": round(float(accuracy_score(y, p > 0.5)), 6),
        "n_test": len(y),
    },
    open("metrics.json", "w"),
    indent=2,
)
