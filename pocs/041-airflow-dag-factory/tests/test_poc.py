import pytest
from airflow_dag_factory.config import ConfigError, parse_pipeline
from airflow_dag_factory.tasks import CALLABLES, COMMANDS

BASE = """dag_id: demo_flow
schedule: "@daily"
owner: team
tasks:
  - {id: a, type: python, callable: extract_orders, params: {work_dir: /tmp/x}}
  - {id: b, type: bash, command: echo_done, depends_on: [a]}
"""


def parse(text):
    return parse_pipeline(text, callables=CALLABLES, commands=COMMANDS)


def test_valid_config_parses():
    c = parse(BASE)
    assert (
        c.dag_id == "demo_flow"
        and [t.id for t in c.tasks] == ["a", "b"]
        and c.tasks[1].depends_on == ("a",)
    )


@pytest.mark.parametrize(
    "old,new,needle",
    [
        ("dag_id: demo_flow", "dag_id: Bad-Id", "dag_id"),
        ('"@daily"', '"61 * * * *"', "schedule"),
        ('"@daily"', '"* * *"', "schedule"),
        ('"@daily"', '"*/0 * * * *"', "schedule"),
        ("owner: team", "owner: ", "owner"),
        ("callable: extract_orders", "callable: os_system", "registry"),
        ("command: echo_done", "command: rm -rf /", "allow-listed"),
        ("depends_on: [a]", "depends_on: [zzz]", "unknown task"),
        ("depends_on: [a]", "depends_on: [b]", "itself"),
        ("params: {work_dir: /tmp/x}", "params: {work_dir: /tmp/x, evil: 1}", "not accepted"),
        ("params: {work_dir: /tmp/x}", "params: {}", "missing params"),
        ("params: {work_dir: /tmp/x}", "params: {work_dir: [1]}", "scalars"),
        ("owner: team", "owner: team\nretries: 99", "retries"),
        ("owner: team", "owner: team\nsurprise: 1", "unknown key"),
    ],
)
def test_invalid_configs_rejected(old, new, needle):
    with pytest.raises(ConfigError, match=needle):
        parse(BASE.replace(old, new))


def test_cycle_and_duplicate_ids_detected():
    cyc = BASE.replace(
        "{id: a, type: python, callable: extract_orders, params: {work_dir: /tmp/x}}",
        "{id: a, type: python, callable: extract_orders, params: {work_dir: /tmp/x}, depends_on: [b]}",
    )
    with pytest.raises(ConfigError, match="cycle"):
        parse(cyc)
    with pytest.raises(ConfigError, match="duplicate id"):
        parse(BASE.replace("id: b", "id: a"))


def test_bad_yaml_and_wrong_shape():
    for bad in ("a: [", "- 1", ""):
        with pytest.raises(ConfigError):
            parse(bad)


def test_all_errors_reported_together():
    with pytest.raises(ConfigError) as e:
        parse(BASE.replace("dag_id: demo_flow", "dag_id: X").replace('"@daily"', '"bad"'))
    assert "dag_id" in str(e.value) and "schedule" in str(e.value)


def test_dagbag_loads_generated_dags(bag):
    assert bag.import_errors == {}
    assert set(bag.dags) == {"orders_daily", "strict_quality", "fan_in"}
    d = bag.dags["orders_daily"]
    assert [t.task_id for t in d.tasks] == ["extract", "validate", "load", "done"]
    assert (
        d.task_dict["validate"].upstream_task_ids == {"extract"}
        and d.task_dict["extract"].retries == 2
    )
    assert d.owner == "sales-data" and "orders" in d.tags
    assert bag.dags["fan_in"].task_dict["validate_a"].upstream_task_ids == {
        "extract_a",
        "extract_b",
    }
    assert bag.dags["strict_quality"].task_dict["extract"].retries == 0


def test_bash_tasks_use_only_allowlisted_commands(bag):
    cmds = {
        t.bash_command for d in bag.dags.values() for t in d.tasks if t.task_type == "BashOperator"
    }
    assert cmds <= set(COMMANDS.values())


def test_good_pipeline_runs_end_to_end(bag):
    import json
    from pathlib import Path

    run = bag.dags["orders_daily"].test()
    assert str(run.state) == "success"
    s = json.loads(Path("/tmp/poc041/orders_daily/summary.json").read_text())
    assert s["rows"] == 200 and s["revenue"] > 0


def test_quality_gate_blocks_load(bag):
    from pathlib import Path

    Path("/tmp/poc041/strict_quality/summary.json").unlink(missing_ok=True)
    run = bag.dags["strict_quality"].test()
    states = {ti.task_id: str(ti.state) for ti in run.get_task_instances()}
    assert (
        str(run.state) == "failed"
        and states["validate"] == "failed"
        and states["load"] != "success"
    )
    assert not Path("/tmp/poc041/strict_quality/summary.json").exists()


def test_duplicate_dag_id_across_files(tmp_path):
    from airflow_dag_factory.config import load_all

    for n in ("a", "b"):
        (tmp_path / f"{n}.yaml").write_text(BASE)
    with pytest.raises(ConfigError, match="duplicate dag_id"):
        load_all(tmp_path, callables=CALLABLES, commands=COMMANDS)
