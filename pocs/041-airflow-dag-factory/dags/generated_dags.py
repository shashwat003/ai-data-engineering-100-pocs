# Airflow DAG file (the words "airflow" and "dag" appear so DagBag safe-mode parses it). Registers every pipeline in ../pipelines.
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from airflow_dag_factory.factory import load_dags  # noqa: E402

globals().update(load_dags(ROOT / "pipelines"))
