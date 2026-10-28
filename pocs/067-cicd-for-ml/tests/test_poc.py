import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

DVC = Path(sys.executable).with_name("dvc")
pytestmark = pytest.mark.skipif(
    not DVC.exists() and shutil.which("dvc") is None,
    reason="dvc not installed (requirements-dvc.txt)",
)
from cicd_for_ml.gates import data_gate, model_gate, workflow_gate  # noqa: E402
from cicd_for_ml.harness import Pipeline  # noqa: E402
from cicd_for_ml.main import WORKFLOW  # noqa: E402

POC = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def pl(tmp_path_factory):
    p = Pipeline(tmp_path_factory.mktemp("dvc"))
    p.first = p.repro()
    return p


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_first_run_executes_every_stage_and_produces_artifacts(pl):
    assert pl.first == {"prepare": "ran", "train": "ran", "evaluate": "ran"}
    for f in ("data/train.csv", "data/test.csv", "model.pkl", "metrics.json", "dvc.lock"):
        assert pl.file(f).exists(), f
    assert pl.metrics()["auc"] > 0.9 and pl.metrics()["n_test"] == 1000


def test_second_run_is_fully_cached(pl):
    assert pl.repro() == {"prepare": "cached", "train": "cached", "evaluate": "cached"}


def test_changing_a_train_param_reruns_only_downstream_stages(pl):
    before = sha(pl.file("data/train.csv"))
    pl.set_params(train={"C": 0.05})
    assert pl.repro() == {"prepare": "cached", "train": "ran", "evaluate": "ran"}
    assert sha(pl.file("data/train.csv")) == before
    pl.set_params(train={"C": 1.0})
    pl.repro()


def test_forced_rerun_is_bit_for_bit_reproducible(pl):
    pl.repro()
    data = (sha(pl.file("data/train.csv")), sha(pl.file("data/test.csv")))
    metrics = pl.metrics()
    pl._dvc("repro", "--force")
    assert (
        sha(pl.file("data/train.csv")),
        sha(pl.file("data/test.csv")),
    ) == data and pl.metrics() == metrics


def test_changing_data_params_reruns_everything(pl):
    pl.set_params(prepare={"n_rows": 2500})
    assert pl.repro() == {"prepare": "ran", "train": "ran", "evaluate": "ran"}
    assert pl.metrics()["n_test"] == 625
    pl.set_params(prepare={"n_rows": 4000})
    pl.repro()


def test_gates_pass_on_the_normal_pipeline(pl):
    pl.repro()
    assert data_gate(pl.dir) == [] and model_gate(pl.dir, pl.dir / "metrics_baseline.json") == []


def test_data_gate_catches_leakage_bad_labels_and_schema_changes(pl, tmp_path):
    pl.repro()
    proj = tmp_path / "p"
    shutil.copytree(pl.dir, proj, ignore=shutil.ignore_patterns(".dvc"))
    train = (proj / "data/train.csv").read_text().splitlines()
    test = (proj / "data/test.csv").read_text().splitlines()
    (proj / "data/test.csv").write_text("\n".join(test + train[1:4]) + "\n")
    assert any("leakage" in p for p in data_gate(proj))
    (proj / "data/test.csv").write_text("\n".join(test) + "\n")
    (proj / "data/train.csv").write_text(
        "\n".join(train[:1] + [ln.rsplit(",", 1)[0] + ",7" for ln in train[1:]]) + "\n"
    )
    assert any("non-binary" in p for p in data_gate(proj))
    (proj / "data/train.csv").write_text("id,a,b\n1,2,3\n")
    assert any("unexpected columns" in p for p in data_gate(proj))


def test_model_gate_floor_and_baseline_regression(tmp_path):
    proj = tmp_path
    (proj / "params.yaml").write_text(
        yaml.safe_dump({"gates": {"min_auc": 0.90, "max_regression_vs_baseline": 0.01}})
    )
    base = proj / "b.json"
    base.write_text(json.dumps({"auc": 0.95}))

    def run(auc):
        (proj / "metrics.json").write_text(json.dumps({"auc": auc}))
        return model_gate(proj, base)

    assert run(0.95) == [] and run(0.945) == []
    assert any("regressed" in p for p in run(0.93)) and any("floor" in p for p in run(0.85))


def test_bad_model_change_fails_the_quality_gate_end_to_end(pl):
    pl.set_params(train={"features": [3]})
    pl.repro()
    problems = model_gate(pl.dir, pl.dir / "metrics_baseline.json")
    assert any("floor" in p for p in problems) and any("regressed" in p for p in problems)
    pl.set_params(train={"features": [0, 1, 2, 3, 4]})
    pl.repro()


def test_check_cli_exit_codes(pl):
    ok = subprocess.run(
        [sys.executable, "-m", "cicd_for_ml.main", "--check", str(pl.dir)],
        cwd=POC,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": f"{POC}:{POC.parent.parent / 'libs'}", "PATH": "/usr/bin:/bin"},
    )
    assert ok.returncode == 0, ok.stderr
    pl.file("metrics.json").write_text(json.dumps({"auc": 0.5}))
    bad = subprocess.run(
        [sys.executable, "-m", "cicd_for_ml.main", "--check", str(pl.dir)],
        cwd=POC,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": f"{POC}:{POC.parent.parent / 'libs'}", "PATH": "/usr/bin:/bin"},
    )
    assert bad.returncode == 1 and "GATE FAILED" in bad.stderr
    pl.repro()


def test_real_workflow_passes_hygiene_and_mutations_fail(tmp_path):
    assert workflow_gate(WORKFLOW) == []
    wf = yaml.safe_load(WORKFLOW.read_text())

    def variant(mutate):
        w = yaml.safe_load(yaml.safe_dump(wf))
        mutate(w)
        p = tmp_path / "w.yml"
        p.write_text(yaml.safe_dump(w))
        return workflow_gate(p)

    step = lambda w: w["jobs"]["reproduce-and-gate"]["steps"]  # noqa: E731
    assert any(
        "unpinned" in x
        for x in variant(lambda w: step(w).__setitem__(0, {"uses": "actions/checkout"}))
    )
    assert any(
        "moving ref" in x
        for x in variant(lambda w: step(w).__setitem__(0, {"uses": "actions/checkout@main"}))
    )
    assert any(
        "permissions" in x for x in variant(lambda w: w.update(permissions={"contents": "write"}))
    )
    assert any(
        "pull_request_target" in x
        for x in variant(lambda w: w[True].update(pull_request_target={}))
    )
