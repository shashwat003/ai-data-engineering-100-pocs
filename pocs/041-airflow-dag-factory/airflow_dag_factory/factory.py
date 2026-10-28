"""Build Airflow DAGs from validated pipeline configs (requires Apache Airflow 3)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG

from .config import PipelineCfg, load_all
from .tasks import CALLABLES, COMMANDS

START = datetime(2026, 1, 1)


def build_dag(cfg: PipelineCfg) -> DAG:
    with DAG(
        dag_id=cfg.dag_id, description=cfg.description, schedule=cfg.schedule, start_date=START, catchup=False, max_active_runs=cfg.max_active_runs, tags=list(cfg.tags),
        default_args={"owner": cfg.owner, "retries": cfg.retries, "retry_delay": timedelta(minutes=cfg.retry_delay_minutes), "execution_timeout": timedelta(minutes=cfg.task_timeout_minutes)},
    ) as dag:  # fmt: skip
        ops = {}
        for t in cfg.tasks:
            if t.type == "python":
                ops[t.id] = PythonOperator(
                    task_id=t.id, python_callable=CALLABLES[t.target], op_kwargs=t.params
                )
            else:
                ops[t.id] = BashOperator(task_id=t.id, bash_command=COMMANDS[t.target])
        for t in cfg.tasks:
            for d in t.depends_on:
                ops[d] >> ops[t.id]
    return dag


def load_dags(pipelines_dir: str | Path) -> dict[str, DAG]:
    return {
        c.dag_id: build_dag(c)
        for c in load_all(pipelines_dir, callables=CALLABLES, commands=COMMANDS)
    }
