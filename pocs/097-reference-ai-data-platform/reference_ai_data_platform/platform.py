"""End-to-end orchestration: landing batch -> lakehouse (POC 035 + 077 contract gate) -> vector index (047) -> model gate (065), traced (071).

The change source is file batches from POC 035's generator; POC 039 (Debezium CDC) is NOT used.
Every stage is idempotent: processing a batch twice changes nothing.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import mlflow
from idempotent_vector_ingestion.store import HashingEmbedder, VectorStore
from llm_tracing_otel.tracing import make_tracer
from medallion_lakehouse_duckdb.lakehouse import Lakehouse, LakehouseError
from mlflow_registry_promotion.registry import MODEL_NAME

from . import mlstep


@dataclass
class BatchResult:
    batch: str
    status: str  # ok | failed
    error: str | None = None
    ingested: int = 0
    skipped: int = 0
    gates_passed: int = 0
    gates_total: int = 0
    vector: dict = field(default_factory=dict)
    models: list = field(default_factory=list)


def customer_docs(con) -> dict[str, str]:
    """One short profile document per current customer; it changes only when the SCD2 dimension changes."""
    rows = con.execute(
        """
        SELECT d.customer_id, d.segment, d.country, year(min(f.order_date))
        FROM dim_customer d LEFT JOIN gold_fct_orders f ON f.customer_sk = d.customer_sk
        WHERE d.is_current GROUP BY ALL ORDER BY d.customer_id"""
    ).fetchall()
    return {
        f"customer-{cid}": f"Customer {cid} is a {seg} account in {ctry}, first active in {yr}."
        for cid, seg, ctry, yr in rows
    }


class Platform:
    def __init__(self, root: str | Path, seed: int = 0):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self.lake = Lakehouse(self.root / "lake")
        self.db = sqlite3.connect(self.root / "vectors.db")
        self.embedder = HashingEmbedder()
        self.store = VectorStore(self.db, self.embedder)
        self.client = setup_registry(self.root / "mlflow")
        self.tracer, self.exporter = make_tracer()
        self.staging = self.root / "staging"
        self._pending_accept: tuple[Path, Path] = (self.staging, self.staging)
        self.state_path = self.root / "state.json"
        self.state = (
            json.loads(self.state_path.read_text()) if self.state_path.exists() else {"models": {}}
        )

    def close(self) -> None:
        self.lake.close()
        self.db.close()

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=1))

    def process_batch(self, src: Path, *, broken_candidate: bool = False) -> BatchResult:
        """Stage one landing batch directory and run every downstream stage. Fails closed at the first error."""
        name = Path(src).name
        res = BatchResult(name, "ok")
        with self.tracer.start_as_current_span("platform.batch") as root:
            root.set_attribute("batch", name)
            try:
                with self.tracer.start_as_current_span("lakehouse.run") as sp:
                    rep = self.lake.run(self._view(src, name))
                    res.ingested, res.skipped = len(rep.ingested), len(rep.skipped)
                    res.gates_total = len(rep.checks)
                    res.gates_passed = sum(c.passed for c in rep.checks)
                    sp.set_attribute("ingested", res.ingested)
                    sp.set_attribute("skipped", res.skipped)
                    sp.set_attribute("gates_passed", res.gates_passed)
                with self.tracer.start_as_current_span("vector.sync") as sp:
                    res.vector = self.store.sync(customer_docs(self.lake.con))
                    sp.set_attribute("embedded", res.vector["embedded"])
                    sp.set_attribute("unchanged", res.vector["unchanged"])
                k = int(name[1:])
                with self.tracer.start_as_current_span("model.gate") as sp:
                    key = name  # batches are immutable (POC 035 rejects changed bytes), so the batch id is a sound key
                    if k >= 2 and key not in self.state["models"]:
                        outs = mlstep.train_and_gate(
                            self.client,
                            self.lake.con,
                            k,
                            self.seed,
                            broken_candidate=broken_candidate,
                        )
                        res.models = [o.__dict__ for o in outs]
                        self.state["models"][key] = [o.__dict__ for o in outs]
                        self._save()
                        sp.set_attribute("promoted", sum(o.promoted for o in outs))
                    elif k >= 2:
                        sp.set_attribute("skipped_replay", True)
                if not self._pending_accept[1].exists():
                    shutil.copytree(*self._pending_accept)
            except (LakehouseError, OSError, ValueError) as e:
                res.status, res.error = "failed", f"{type(e).__name__}: {e}"
                root.set_attribute("error", res.error)
        return res

    def _view(self, src: Path, name: str) -> Path:
        """A landing directory of all accepted batches plus the incoming one. The incoming batch is promoted to
        `accepted` only after the whole run succeeds, so a rejected (e.g. tampered) batch can never contaminate later runs."""
        incoming, accepted, view = (self.staging / d for d in ("incoming", "accepted", "view"))
        for d in (incoming, view):
            shutil.rmtree(d, ignore_errors=True)
        accepted.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, incoming / name)
        view.mkdir(parents=True)
        for d in sorted(p for p in accepted.iterdir() if p.is_dir() and p.name != name):
            (view / d.name).symlink_to(d)
        (view / name).symlink_to(incoming / name)
        self._pending_accept = (incoming / name, accepted / name)
        return view

    def champion(self) -> str | None:
        try:
            return self.client.get_model_version_by_alias(MODEL_NAME, "champion").version
        except Exception:
            return None


def setup_registry(path: Path):
    """SQLite-backed MLflow tracking + registry with artifacts kept inside the platform root (never the CWD)."""
    path.mkdir(parents=True, exist_ok=True)
    uri = f"sqlite:///{path}/mlflow.db"
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    client = mlflow.MlflowClient()
    exp = client.get_experiment_by_name("platform")
    exp_id = (
        exp.experiment_id
        if exp
        else client.create_experiment("platform", artifact_location=(path / "artifacts").as_uri())
    )
    mlflow.set_experiment(experiment_id=exp_id)
    try:
        client.get_registered_model(MODEL_NAME)
    except mlflow.exceptions.MlflowException:
        client.create_registered_model(MODEL_NAME)
    return client
