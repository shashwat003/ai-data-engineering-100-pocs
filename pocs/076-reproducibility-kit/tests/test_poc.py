import hashlib
import json
import random

import numpy as np
import pytest
from reproducibility_kit.check import check, diagnose, run_once
from reproducibility_kit.cli import main as cli_main
from reproducibility_kit.main import run
from reproducibility_kit.manifest import (
    build_manifest,
    canonical,
    diff_manifests,
    packages,
    sha256_file,
    sha256_tree,
)
from reproducibility_kit.seed import set_all_seeds

R = run()
E = R["experiments"]


def test_flawed_experiments_are_detected_and_fixed_versions_pass():
    for name in ("seeded", "linear", "hash_order_fixed", "thread_fixed"):
        assert E[name]["reproducible"], name
    for name in ("unseeded", "hash_order", "thread_race", "clock_id"):
        assert not E[name]["reproducible"], name


def test_diagnosis_names_the_right_cause():
    assert any("PYTHONHASHSEED" in c for c in E["hash_order"]["likely_causes"])
    for name in ("unseeded", "clock_id"):
        assert any("run-to-run" in c for c in E[name]["likely_causes"])
    assert E["seeded"]["likely_causes"] == []


def test_manifest_drift_is_reported_with_severity():
    d = R["drift"]
    assert d["nothing changed"] == []
    assert [x["field"] for x in d["data file edited"]] == ["data"] and d["data file edited"][0][
        "severity"
    ] == "high"
    assert [x["field"] for x in d["config edited"]] == ["config_hash"]
    assert [x["field"] for x in d["seed changed"]] == ["seed"]
    assert d["package version differs"][0]["severity"] == "medium" and d["package version differs"][
        0
    ]["changed"] == ["numpy"]


def test_set_all_seeds_makes_in_process_randomness_repeatable():
    set_all_seeds(5)
    a = (random.random(), np.random.rand())  # noqa: NPY002
    st = set_all_seeds(5)
    assert (random.random(), np.random.rand()) == a and "PYTHONHASHSEED" in st  # noqa: NPY002


def test_hash_helpers(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    h1 = sha256_tree(tmp_path)
    (tmp_path / "c.txt").write_text("ignored")  # other suffixes ignored
    assert sha256_tree(tmp_path) == h1
    (tmp_path / "b.py").write_text("y = 3\n")
    assert sha256_tree(tmp_path) != h1
    assert sha256_file(tmp_path / "a.py") == hashlib.sha256(b"x = 1\n").hexdigest()
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_manifest_id_depends_on_relevant_fields_only(tmp_path):
    data = tmp_path / "d.csv"
    data.write_text("a\n1\n")
    code = tmp_path / "code"
    code.mkdir()
    (code / "m.py").write_text("pass\n")
    mk = lambda cfg, seed=1: build_manifest(cfg, seed, [data], code, ["numpy"], None)  # noqa: E731
    base = mk({"lr": 0.1})
    assert (
        base["manifest_id"]
        == mk({"lr": 0.1})["manifest_id"]
        != mk({"lr": 0.2})["manifest_id"]
        != mk({"lr": 0.1}, seed=2)["manifest_id"]
    )
    (code / "m.py").write_text("pass  # edited\n")
    assert mk({"lr": 0.1})["manifest_id"] != base["manifest_id"]
    assert {"platform", "env", "git"} <= set(base) and packages(["definitely-not-installed"]) == {
        "definitely-not-installed": "NOT INSTALLED"
    }


def test_diff_flags_dirty_git_tree_and_ranks_fields():
    old = {
        "python": "3.12",
        "packages": {"numpy": "1"},
        "data": {"a": "x"},
        "seed": 1,
        "config_hash": "c",
        "code_hash": "k",
        "env": {},
        "machine": "x86",
        "platform": "p",
    }
    new = {**old, "data": {"a": "y"}, "machine": "arm", "git": {"dirty": True}}
    fields = {d["field"]: d["severity"] for d in diff_manifests(old, new)}
    assert fields == {"data": "high", "machine": "low", "git.dirty": "medium"}


def test_cli_manifest_and_verify_round_trip(tmp_path, capsys):
    data = tmp_path / "train.csv"
    data.write_text("a,b\n1,2\n")
    m = tmp_path / "m.json"
    assert cli_main(["manifest", "--out", str(m), "--seed", "3", "--data", str(data)]) == 0
    assert json.loads(m.read_text())["seed"] == 3
    capsys.readouterr()
    assert (
        cli_main(["verify", "--manifest", str(m), "--data", str(data)]) == 0
    )  # medium-severity notices (dirty git tree) do not fail verification
    data.write_text("a,b\n1,999\n")
    assert cli_main(["verify", "--manifest", str(m), "--data", str(data)]) == 1
    err = capsys.readouterr().err
    assert "high" in err and "data" in err


def test_cli_check_reports_exit_code_for_reproducibility():
    assert cli_main(["check", "seeded"]) == 0
    assert cli_main(["check", "clock_id"]) == 1


def test_child_process_failure_surfaces_as_error():
    with pytest.raises(RuntimeError, match="failed"):
        run_once("no_such_experiment", {"seed": 0}, {})


def test_direct_check_and_diagnose_agree_on_a_fast_pair():
    assert check("hash_order_fixed", {"seed": 1}, hashseeds=("0", "5"), threads=("1",))[
        "reproducible"
    ]
    d = diagnose("hash_order", {"seed": 1})
    assert d["hashseed_sensitive"] is True and d["repeat_identical"] is True
