"""POC 041: validate configs, load generated DAGs, execute them with dag.test() against a temp SQLite metadata DB."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json

from . import env

POC_DIR = Path(__file__).resolve().parent.parent


def run() -> dict:
    env.setup()
    bag = env.load_dagbag(POC_DIR / "dags")
    out = {"import_errors": bag.import_errors, "dags": {}}
    for dag_id in sorted(bag.dags):
        d = bag.dags[dag_id]
        out["dags"][dag_id] = {
            "tasks": len(d.tasks),
            "schedule": str(d.schedule),
            "tags": sorted(d.tags),
            "edges": sorted(f"{t.task_id}>{c}" for t in d.tasks for c in t.downstream_task_ids),
        }
    for dag_id in ("orders_daily", "strict_quality", "fan_in"):
        try:
            state = bag.dags[dag_id].test().state
        except Exception as e:  # dag.test raises when a task fails; record it
            state = f"failed ({type(e).__name__})"
        out["dags"][dag_id]["test_run_state"] = str(state)
    return out


def airflow_version() -> str:
    import airflow

    return airflow.__version__


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        f"# Results (measured by `python -m airflow_dag_factory.main`; Airflow {airflow_version()}, dag.test() in-process, SQLite metadata DB)",
        "",
        f"- import errors: {r['import_errors'] or 'none'}",
    ]
    for k, v in r["dags"].items():
        md.append(
            f"- **{k}**: {v['tasks']} tasks, schedule `{v['schedule']}`, tags {v['tags']}, test run: **{v['test_run_state']}**"
        )
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
