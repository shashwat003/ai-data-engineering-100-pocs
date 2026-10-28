"""Train candidates, register them in a real MLflow registry (SQLite), and promote through a statistical gate.

Promotion is an alias move (`champion`), not a stage: aliases are atomic, reversible and recorded.
The gate refuses promotion unless the candidate (1) is signature-compatible, (2) survives a smoke
inference test, (3) beats the champion on a held-out set with a bootstrap interval above a minimum
margin, and (4) does not regress any protected subgroup by more than a tolerance.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import mlflow
import numpy as np
from mlflow import MlflowClient
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

MODEL_NAME = "churn-model"
FEATURES = 6


def make_data(n: int, seed: int, shift: float = 0.0):
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, (n, FEATURES)) + shift
    group = (rng.random(n) < 0.3).astype(int)  # protected subgroup membership
    logit = (
        X @ np.array([1.2, -0.8, 0.5, 0.0, 0.3, -0.4])
        + 0.5 * group * X[:, 0]
        + rng.normal(0, 0.5, n)
    )
    y = (logit > 0).astype(int)
    return X, y, group


@dataclass
class Gate:
    min_delta: float = 0.002  # required AUC improvement (lower CI bound)
    max_subgroup_drop: float = 0.02
    n_boot: int = 300


@dataclass
class Decision:
    promote: bool
    reasons: list[str]
    delta_ci: tuple[float, float]
    subgroup_auc: dict


def train_and_register(
    client: MlflowClient,
    X,
    y,
    *,
    C: float,
    seed: int,
    drop_feature: int | None = None,
    note: str = "",
) -> str:
    """Log a run, store the pickled model as an artifact, register a model version; returns the version."""
    Xt = np.delete(X, drop_feature, axis=1) if drop_feature is not None else X
    model = LogisticRegression(C=C, max_iter=500, random_state=seed).fit(Xt, y)
    with mlflow.start_run(run_name=f"C={C}") as run:
        mlflow.log_params({"C": C, "seed": seed, "n_features": Xt.shape[1], "note": note})
        mlflow.log_metric("train_auc", float(roc_auc_score(y, model.predict_proba(Xt)[:, 1])))
        art = Path(mlflow.get_artifact_uri().replace("file://", "")) / "model"
        art.mkdir(parents=True, exist_ok=True)
        (art / "model.pkl").write_bytes(pickle.dumps(model))
        (art / "signature.json").write_text(json.dumps({"n_features": Xt.shape[1]}))
        mv = client.create_model_version(
            MODEL_NAME, f"{run.info.artifact_uri}/model", run.info.run_id, tags={"note": note}
        )
    return mv.version


def load(client: MlflowClient, version: str):
    mv = client.get_model_version(MODEL_NAME, version)
    p = Path(mv.source.replace("file://", ""))
    return pickle.loads((p / "model.pkl").read_bytes()), json.loads(
        (p / "signature.json").read_text()
    )


def evaluate_gate(
    client: MlflowClient, candidate: str, X, y, group, gate: Gate | None = None, seed: int = 0
) -> Decision:
    gate = gate or Gate()
    reasons: list[str] = []
    cand, csig = load(client, candidate)
    if csig["n_features"] != X.shape[1]:
        return Decision(
            False,
            [
                f"signature mismatch: candidate expects {csig['n_features']} features, serving sends {X.shape[1]}"
            ],
            (0.0, 0.0),
            {},
        )
    try:
        champ_mv = client.get_model_version_by_alias(MODEL_NAME, "champion")
    except Exception:
        champ_mv = None
    try:
        p_c = cand.predict_proba(X)[:, 1]
        assert np.isfinite(p_c).all() and p_c.min() >= 0 and p_c.max() <= 1
    except Exception as e:
        return Decision(False, [f"smoke inference failed: {type(e).__name__}"], (0.0, 0.0), {})
    if champ_mv is None:
        return Decision(True, ["no champion yet: first version passes smoke test"], (0.0, 0.0), {})
    champ, _ = load(client, champ_mv.version)
    p_h = champ.predict_proba(X)[:, 1]
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(gate.n_boot):
        idx = rng.integers(0, len(y), len(y))
        if len(set(y[idx])) < 2:
            continue
        deltas.append(roc_auc_score(y[idx], p_c[idx]) - roc_auc_score(y[idx], p_h[idx]))
    lo, hi = float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5))
    sub = {}
    for g in (0, 1):
        m = group == g
        sub[g] = (float(roc_auc_score(y[m], p_h[m])), float(roc_auc_score(y[m], p_c[m])))
        if sub[g][0] - sub[g][1] > gate.max_subgroup_drop:
            reasons.append(f"subgroup {g} AUC regressed {sub[g][0]:.3f} -> {sub[g][1]:.3f}")
    if lo < gate.min_delta:
        reasons.append(
            f"AUC improvement not demonstrated: 95% interval [{lo:+.4f}, {hi:+.4f}] vs required lower bound {gate.min_delta}"
        )
    return Decision(
        not reasons,
        reasons or [f"improvement {lo:+.4f}..{hi:+.4f} above margin; no subgroup regression"],
        (lo, hi),
        sub,
    )


def promote(client: MlflowClient, version: str, who: str, why: str) -> None:
    prev = None
    try:
        prev = client.get_model_version_by_alias(MODEL_NAME, "champion").version
    except Exception:
        pass
    if (
        prev is not None and prev != version
    ):  # re-promoting the current champion must not erase the rollback target
        client.set_registered_model_alias(MODEL_NAME, "previous", prev)  # one-step rollback target
    client.set_registered_model_alias(MODEL_NAME, "champion", version)
    client.set_model_version_tag(MODEL_NAME, version, "promoted_by", who)
    client.set_model_version_tag(MODEL_NAME, version, "promotion_reason", why)


def rollback(client: MlflowClient, who: str) -> str:
    prev = client.get_model_version_by_alias(MODEL_NAME, "previous").version
    cur = client.get_model_version_by_alias(MODEL_NAME, "champion").version
    client.set_registered_model_alias(MODEL_NAME, "champion", prev)
    client.set_registered_model_alias(MODEL_NAME, "previous", cur)
    client.set_model_version_tag(MODEL_NAME, prev, "rolled_back_by", who)
    return prev


def setup(tmp: Path) -> MlflowClient:
    mlflow.set_tracking_uri(f"sqlite:///{tmp}/mlflow.db")
    mlflow.set_registry_uri(f"sqlite:///{tmp}/mlflow.db")
    mlflow.set_experiment("promotion-demo")
    c = MlflowClient()
    c.create_registered_model(MODEL_NAME)
    return c
