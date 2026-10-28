"""Isolated Airflow environment for tests/demo: temp AIRFLOW_HOME (SQLite metadata DB), POC dags folder, no examples."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def setup() -> Path:
    """Call BEFORE importing airflow. Returns the temp AIRFLOW_HOME."""
    home = Path(tempfile.mkdtemp(prefix="poc041-airflow-"))
    os.environ.update({"AIRFLOW_HOME": str(home), "AIRFLOW__CORE__LOAD_EXAMPLES": "False", "AIRFLOW__CORE__DAGS_FOLDER": str(ROOT / "dags"),
                       "AIRFLOW__CORE__UNIT_TEST_MODE": "True", "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": f"sqlite:///{home}/airflow.db"})  # fmt: skip
    exe = Path(sys.executable).with_name("airflow")
    subprocess.run([str(exe), "db", "migrate"], check=True, capture_output=True, timeout=300)
    return home


def load_dagbag(dags_dir: Path | str):
    """DagBag across Airflow 3.1 (airflow.models.dagbag, include_examples) and 3.3 (airflow.dag_processing.dagbag).
    Examples are disabled through AIRFLOW__CORE__LOAD_EXAMPLES in setup(), so no keyword is needed."""
    try:
        from airflow.dag_processing.dagbag import DagBag
    except ImportError:  # Airflow 3.1
        from airflow.models.dagbag import DagBag
    return DagBag(str(dags_dir))
