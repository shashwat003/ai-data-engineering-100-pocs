"""The ML pipeline state machine and its real Python task implementations (scikit-learn on synthetic data)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

DEFINITION = {
    "Comment": "Prepare -> quality gate -> tune (Map) -> pick best -> evaluate gate -> register or notify",
    "StartAt": "Prepare",
    "States": {
        "Prepare": {
            "Type": "Task",
            "Resource": "prepare",
            "Parameters": {
                "seed.$": "$.seed",
                "n_rows.$": "$.n_rows",
                "label_noise.$": "$.label_noise",
            },
            "ResultPath": "$.data",
            "Retry": [
                {
                    "ErrorEquals": ["TransientStorageError"],
                    "IntervalSeconds": 2,
                    "MaxAttempts": 3,
                    "BackoffRate": 2.0,
                }
            ],
            "Catch": [
                {"ErrorEquals": ["States.ALL"], "Next": "NotifyFailure", "ResultPath": "$.error"}
            ],
            "Next": "QualityGate",
        },
        "QualityGate": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.data.positive_rate",
                    "NumericLessThan": 0.02,
                    "Next": "DataQualityFail",
                },
                {"Variable": "$.data.n_valid", "NumericLessThan": 200, "Next": "DataQualityFail"},
            ],
            "Default": "Tune",
        },
        "DataQualityFail": {
            "Type": "Fail",
            "Error": "DataQualityError",
            "Cause": "too few positives or rows",
        },
        "Tune": {
            "Type": "Map",
            "ItemsPath": "$.grid",
            "ItemSelector": {
                "c.$": "$.value",
                "seed.$": "$.context.seed",
                "n_rows.$": "$.context.n_rows",
                "label_noise.$": "$.context.label_noise",
            },
            "ItemProcessor": {
                "StartAt": "TrainOne",
                "States": {
                    "TrainOne": {
                        "Type": "Task",
                        "Resource": "train",
                        "Retry": [
                            {
                                "ErrorEquals": ["TrainingTimeout", "States.Timeout"],
                                "IntervalSeconds": 1,
                                "MaxAttempts": 2,
                                "BackoffRate": 2.0,
                            }
                        ],
                        "End": True,
                    }
                },
            },
            "ResultPath": "$.candidates",
            "Catch": [
                {"ErrorEquals": ["States.ALL"], "Next": "NotifyFailure", "ResultPath": "$.error"}
            ],
            "Next": "SelectBest",
        },
        "SelectBest": {
            "Type": "Task",
            "Resource": "select_best",
            "ResultPath": "$.best",
            "Next": "EvaluateGate",
        },
        "EvaluateGate": {
            "Type": "Choice",
            "Choices": [
                {"Variable": "$.best.auc", "NumericGreaterThanEquals": 0.8, "Next": "Register"}
            ],
            "Default": "NotifyFailure",
        },
        "Register": {
            "Type": "Task",
            "Resource": "register",
            "ResultPath": "$.registered",
            "End": True,
        },
        "NotifyFailure": {
            "Type": "Task",
            "Resource": "notify",
            "ResultPath": "$.notified",
            "Next": "Failed",
        },
        "Failed": {"Type": "Fail", "Error": "PipelineFailed", "Cause": "see notification"},
    },
}


class TransientStorageError(Exception):
    pass


class TrainingTimeout(Exception):
    pass


def make_data(seed: int, n_rows: int, label_noise: float):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n_rows, 6))
    logit = x @ np.array([1.5, -1.0, 0.8, 0.0, 0.0, 0.5]) - 1.0
    y = (rng.random(n_rows) < 1 / (1 + np.exp(-logit))).astype(int)
    flip = rng.random(n_rows) < label_noise
    return x, np.where(flip, 1 - y, y)


class Tasks:
    """Task implementations plus failure injection hooks used by the demo and tests."""

    def __init__(
        self,
        registry: Path,
        *,
        storage_failures: int = 0,
        timeouts_for_c: dict[float, int] | None = None,
    ):
        self.registry, self.storage_failures, self.timeouts = (
            registry,
            storage_failures,
            dict(timeouts_for_c or {}),
        )
        self.notifications: list[dict] = []

    def prepare(self, p: dict) -> dict:
        if self.storage_failures > 0:
            self.storage_failures -= 1
            raise TransientStorageError("S3 SlowDown")
        _, y = make_data(p["seed"], p["n_rows"], p["label_noise"])
        return {"n_valid": int(len(y)), "positive_rate": round(float(y.mean()), 4)}

    def train(self, p: dict) -> dict:
        c = p["c"]
        if self.timeouts.get(c, 0) > 0:
            self.timeouts[c] -= 1
            raise TrainingTimeout(f"c={c} exceeded the budget")
        x, y = make_data(p["seed"], p["n_rows"], p["label_noise"])
        xtr, xte, ytr, yte = train_test_split(x, y, test_size=0.3, random_state=0, stratify=y)
        m = LogisticRegression(C=c, max_iter=500).fit(xtr, ytr)
        return {"c": c, "auc": round(float(roc_auc_score(yte, m.predict_proba(xte)[:, 1])), 4)}

    def select_best(self, state: dict) -> dict:
        return max(state["candidates"], key=lambda r: (r["auc"], -r["c"]))

    def register(self, state: dict) -> dict:
        self.registry.mkdir(parents=True, exist_ok=True)
        entry = {"model": "churn-lr", "c": state["best"]["c"], "auc": state["best"]["auc"]}
        (self.registry / "latest.json").write_text(json.dumps(entry))
        return entry

    def notify(self, state: dict) -> dict:
        msg = {
            "reason": state.get("error", {}).get("Error") or "metric below threshold",
            "best": state.get("best"),
        }
        self.notifications.append(msg)
        return msg

    def resources(self) -> dict:
        return {
            "prepare": self.prepare,
            "train": self.train,
            "select_best": self.select_best,
            "register": self.register,
            "notify": self.notify,
        }
