import copy
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml
from data_contracts_ci_gate.cli import check_repo
from data_contracts_ci_gate.cli import main as cli
from data_contracts_ci_gate.contract import ContractError, load_contract, parse_contract
from data_contracts_ci_gate.evolution import (
    actual_bump,
    check_evolution,
    diff_contracts,
    required_bump,
)
from data_contracts_ci_gate.feeds import DEFECTS, build_feeds
from data_contracts_ci_gate.main import EXPECTED_RULE
from data_contracts_ci_gate.main import main as demo
from data_contracts_ci_gate.validate import parse_value, validate_dataset

POC = Path(__file__).resolve().parent.parent
CONTRACT_FILE = POC / "contracts" / "orders_feed.yaml"

BASE = {
    "contract": "t",
    "version": "1.0.0",
    "owner": "me",
    "format": "csv",
    "columns": [
        {"name": "id", "type": "integer", "required": True, "unique": True},
        {"name": "status", "type": "string", "enum": ["a", "b"]},
        {"name": "amt", "type": "number", "min": 0, "max": 100},
        {"name": "mail", "type": "string", "pii": True, "pattern": "@"},
        {"name": "d", "type": "date"},
    ],
}


def make(**changes):
    doc = copy.deepcopy(BASE)
    doc.update(changes)
    return parse_contract(yaml.safe_dump(doc))


def with_col(name, **attrs):
    doc = copy.deepcopy(BASE)
    col = next(c for c in doc["columns"] if c["name"] == name)
    for key, value in attrs.items():
        if value is None:
            col.pop(key, None)  # None means: remove the constraint
        else:
            col[key] = value
    return parse_contract(yaml.safe_dump(doc))


def csv_file(tmp_path, text, name="d.csv"):
    path = tmp_path / name
    path.write_text(text)
    return path


# ---- contract parsing


def test_repository_contracts_parse():
    c = load_contract(CONTRACT_FILE)
    assert c.id == "orders_feed" and c.version == "1.0.0"
    assert c.column("email").pii and c.freshness.max_age_days == 7
    for example in (POC / "contracts" / "examples").glob("*.yaml"):
        load_contract(example)


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        ({"version": "1.0"}, "MAJOR.MINOR.PATCH"),
        ({"surprise": 1}, "unknown key 'surprise'"),
        ({"format": "xml"}, "format must be"),
        ({"columns": [{"name": "a", "type": "decimal"}]}, "type must be one of"),
        (
            {"columns": [{"name": "a", "type": "string"}, {"name": "a", "type": "string"}]},
            "duplicate column",
        ),
        ({"columns": [{"name": "a", "type": "string", "min": 1}]}, "numeric columns only"),
        ({"columns": [{"name": "a", "type": "integer", "pattern": "x"}]}, "text-like"),
        ({"columns": [{"name": "a", "type": "string", "pattern": "("}]}, "invalid pattern"),
        ({"columns": [{"name": "a", "type": "string", "enum": []}]}, "enum must be"),
        ({"quality": {"min_rows": -1}}, "non-negative"),
        ({"quality": {"max_null_rate": {"nope": 0.1}}}, "unknown column"),
        ({"quality": {"freshness": {"column": "id", "max_age_days": 1}}}, "date/timestamp"),
        ({"quality": {"rules": [{"type": "magic"}]}}, "type must be one of"),
        (
            {
                "quality": {
                    "rules": [{"type": "column_compare", "left": "d", "op": "~", "right": "d"}]
                }
            },
            "op must be",
        ),
    ],
)
def test_malformed_contracts_are_rejected_with_reasons(mutation, fragment):
    doc = copy.deepcopy(BASE)
    doc.update(mutation)
    with pytest.raises(ContractError, match=fragment):
        parse_contract(yaml.safe_dump(doc))


def test_non_mapping_and_bad_yaml_rejected():
    for text in ("- a\n- b\n", "a: [unclosed"):
        with pytest.raises(ContractError):
            parse_contract(text)


def test_yaml_is_loaded_safely():
    with pytest.raises(ContractError):
        parse_contract("!!python/object/apply:os.system ['echo hi']")


# ---- value parsing


@pytest.mark.parametrize(
    "raw,kind,ok",
    [
        ("12", "integer", True), ("12.5", "integer", False), (True, "integer", False),
        ("1e3", "number", True), ("nan", "number", False), ("inf", "number", False), ("12,50", "number", False),
        ("2024-02-30", "date", False), ("2024-02-29", "date", True), ("2024-01-01T10:00:00", "timestamp", True),
        ("yes", "boolean", False), ("TRUE", "boolean", True), (5, "string", False),
    ],
)  # fmt: skip
def test_parse_value(raw, kind, ok):
    assert parse_value(raw, kind)[0] is ok


# ---- validation rules


def test_every_column_rule_is_detected(tmp_path):
    c = make()
    data = csv_file(
        tmp_path,
        "id,status,amt,mail,d\n"
        "1,a,5,x@y,2024-01-01\n"
        "1,c,-1,nope,2024-01-02\n"  # dup id, bad enum, below min, bad pattern
        ",a,500,x@y,not-a-date\n"  # null required, above max, bad date
        "4,b,abc,x@y,2024-01-04\n",  # non-numeric
    )
    counts = validate_dataset(c, data).counts()
    assert counts == {
        "column.enum:status": 1, "column.max:amt": 1, "column.min:amt": 1, "column.pattern:mail": 1,
        "column.required:id": 1, "column.type:amt": 1, "column.type:d": 1, "column.unique:id": 1,
    }  # fmt: skip


def test_pii_values_never_appear_in_reports(tmp_path):
    c = make()
    data = csv_file(tmp_path, "id,status,amt,mail,d\n1,a,5,secret-no-at-sign,2024-01-01\n")
    report = validate_dataset(c, data)
    assert report.counts() == {"column.pattern:mail": 1}
    assert "secret" not in json.dumps([v.__dict__ for v in report.violations])


def test_schema_drift_missing_and_extra_columns(tmp_path):
    c = make()
    missing = validate_dataset(c, csv_file(tmp_path, "id,status\n1,a\n"))
    assert {
        "schema.missing_column:amt",
        "schema.missing_column:mail",
        "schema.missing_column:d",
    } <= set(missing.counts())
    extra = validate_dataset(
        c, csv_file(tmp_path, "id,status,amt,mail,d,zzz\n1,a,5,@,2024-01-01\n", "e.csv")
    )
    assert extra.passed and extra.violations[0].severity == "warn"
    strict = make(extra_columns="error")
    assert not validate_dataset(strict, tmp_path / "e.csv").passed
    assert validate_dataset(make(extra_columns="allow"), tmp_path / "e.csv").violations == []


def test_dataset_level_rules(tmp_path):
    doc = copy.deepcopy(BASE)
    doc["quality"] = {
        "min_rows": 3,
        "max_rows": 3,
        "max_null_rate": {"d": 0.2},
        "freshness": {"column": "d", "max_age_days": 2},
        "rules": [
            {"type": "column_compare", "left": "d", "op": "<", "right": "d", "severity": "warn"},
            {"type": "unique_together", "columns": ["status", "amt"]},
        ],
    }
    c = parse_contract(yaml.safe_dump(doc))
    two = csv_file(tmp_path, "id,status,amt,mail,d\n1,a,5,@,2024-01-01\n2,a,5,@,\n")
    counts = validate_dataset(c, two, as_of=date(2024, 1, 10)).counts()
    assert counts["quality.min_rows"] == 1 and counts["quality.max_null_rate:d"] == 1
    assert counts["quality.freshness:d"] == 1 and counts["quality.unique_together:status+amt"] == 1
    assert counts["quality.column_compare:d<d"] == 1  # 2024-01-01 < 2024-01-01 is false
    four = csv_file(
        tmp_path,
        "id,status,amt,mail,d\n" + "\n".join(f"{i},a,{i},@,2024-01-09" for i in range(1, 5)),
        "f.csv",
    )
    assert validate_dataset(c, four, as_of=date(2024, 1, 10)).counts()["quality.max_rows"] == 1
    with pytest.raises(ValueError, match="as_of"):
        validate_dataset(c, two)


def test_jsonl_and_native_types(tmp_path):
    c = make(format="jsonl")
    path = csv_file(
        tmp_path,
        '{"id": 1, "status": "a", "amt": 2.5, "mail": "@", "d": "2024-01-01"}\n\n'
        '{"id": "x", "status": "a", "amt": true, "mail": "@", "d": "2024-01-01"}\n',
        "d.jsonl",
    )
    assert validate_dataset(c, path).counts() == {"column.type:amt": 1, "column.type:id": 1}
    bad = csv_file(tmp_path, "not json\n", "bad.jsonl")
    with pytest.raises(ValueError, match="line 1"):
        validate_dataset(c, bad)


# ---- detection against injected defects (the measurable claim)


@pytest.fixture(scope="module")
def feeds(tmp_path_factory):
    out = tmp_path_factory.mktemp("feeds")
    return out, build_feeds(out)


def test_clean_feed_passes_and_defects_are_found_exactly(feeds):
    out, manifest = feeds
    contract = load_contract(CONTRACT_FILE)
    as_of = date.fromisoformat(manifest["newest_order_date"]).replace(day=31)  # within 7 days
    assert validate_dataset(contract, out / "orders_clean.csv", as_of=as_of).violations == []
    counts = validate_dataset(contract, out / "orders_defective.csv", as_of=as_of).counts()
    for defect, rule in EXPECTED_RULE.items():
        assert counts[rule] == DEFECTS[defect], (defect, rule, counts)
    unexplained = set(counts) - set(EXPECTED_RULE.values())
    assert unexplained == {
        "quality.max_null_rate:customer_id"
    }  # the dataset-level echo of null ids


def test_schema_drift_and_stale_feeds_are_caught(feeds):
    out, manifest = feeds
    contract = load_contract(CONTRACT_FILE)
    as_of = date.fromisoformat(manifest["newest_order_date"])
    assert (
        "schema.missing_column:amount"
        in validate_dataset(contract, out / "orders_missing_column.csv", as_of=as_of).counts()
    )
    assert validate_dataset(contract, out / "orders_extra_column.csv", as_of=as_of).passed
    assert (
        "quality.freshness:order_date"
        in validate_dataset(contract, out / "orders_stale.csv", as_of=as_of).counts()
    )


def test_feed_generation_is_deterministic(tmp_path):
    a, b = build_feeds(tmp_path / "a"), build_feeds(tmp_path / "b")
    assert a == b
    assert (tmp_path / "a" / "orders_defective.csv").read_bytes() == (
        tmp_path / "b" / "orders_defective.csv"
    ).read_bytes()


# ---- schema evolution policy


@pytest.mark.parametrize(
    "new,kind,level",
    [
        (lambda: with_col("amt", type="integer"), "type_changed", "breaking"),
        (lambda: with_col("status", required=True), "required_changed", "breaking"),
        (lambda: with_col("id", required=False), "required_changed", "minor"),
        (lambda: with_col("id", unique=False), "unique_changed", "minor"),
        (lambda: with_col("mail", pii=False), "pii_changed", "breaking"),
        (lambda: with_col("status", enum=["a"]), "enum_values_removed", "breaking"),
        (lambda: with_col("status", enum=["a", "b", "c"]), "enum_values_added", "minor"),
        (lambda: with_col("status", enum=None), "constraint_removed", "minor"),
        (lambda: with_col("amt", min=10), "constraint_tightened", "breaking"),
        (lambda: with_col("amt", min=-5), "constraint_loosened", "minor"),
        (lambda: with_col("amt", max=50), "constraint_tightened", "breaking"),
        (lambda: with_col("amt", max=500), "constraint_loosened", "minor"),
        (lambda: with_col("mail", pattern="@x"), "pattern_changed", "breaking"),
        (lambda: with_col("mail", pattern=None), "pattern_changed", "minor"),
        (lambda: with_col("d", description="now documented"), "description_changed", "patch"),
        (
            lambda: make(columns=[c for c in BASE["columns"] if c["name"] != "d"]),
            "column_removed",
            "breaking",
        ),
        (
            lambda: make(columns=[*BASE["columns"], {"name": "n", "type": "string"}]),
            "column_added",
            "minor",
        ),
        (
            lambda: make(
                columns=[*BASE["columns"], {"name": "n", "type": "string", "required": True}]
            ),
            "column_added",
            "breaking",
        ),
        (lambda: make(format="jsonl"), "format_changed", "breaking"),
        (lambda: make(owner="someone-else"), "metadata_changed", "patch"),
        (lambda: make(extra_columns="error"), "extra_columns_changed", "breaking"),
        (lambda: make(extra_columns="allow"), "extra_columns_changed", "minor"),
        (lambda: make(contract="other"), "id_changed", "breaking"),
    ],
)
def test_change_classification(new, kind, level):
    changes = diff_contracts(make(), new())
    assert (kind, level) in {(c.kind, c.level) for c in changes}, changes


def test_quality_threshold_changes():
    def q(**quality):
        doc = copy.deepcopy(BASE)
        doc["quality"] = quality
        return parse_contract(yaml.safe_dump(doc))

    fresh = lambda n: {"column": "d", "max_age_days": n}  # noqa: E731
    cases = [
        (q(min_rows=10), q(min_rows=20), "breaking"), (q(min_rows=10), q(min_rows=5), "minor"),
        (q(), q(min_rows=1), "breaking"), (q(max_rows=10), q(max_rows=5), "breaking"),
        (q(freshness=fresh(5)), q(freshness=fresh(2)), "breaking"), (q(freshness=fresh(5)), q(freshness=fresh(9)), "minor"),
        (q(freshness=fresh(5)), q(), "minor"), (q(), q(freshness=fresh(5)), "breaking"),
        (q(max_null_rate={"d": 0.1}), q(max_null_rate={"d": 0.05}), "breaking"),
        (q(max_null_rate={"d": 0.1}), q(max_null_rate={"d": 0.5}), "minor"),
        (q(rules=[{"type": "unique_together", "columns": ["id"]}]), q(), "minor"),
        (q(), q(rules=[{"type": "unique_together", "columns": ["id"]}]), "breaking"),
        (q(), q(rules=[{"type": "unique_together", "columns": ["id"], "severity": "warn"}]), "minor"),
    ]  # fmt: skip
    for old, new, level in cases:
        assert (
            required_bump(diff_contracts(old, new))
            == {"breaking": "major", "minor": "minor"}[level]
        )


def test_rename_is_flagged_as_breaking_with_hint():
    renamed = make(
        columns=[{**c, "name": "ident"} if c["name"] == "id" else c for c in BASE["columns"]]
    )
    changes = diff_contracts(make(), renamed)
    kinds = {c.kind for c in changes}
    assert {"column_removed", "column_added", "possible_rename"} <= kinds
    assert required_bump(changes) == "major"


def test_version_bump_policy():
    old = make()
    broken = with_col("amt", type="integer")
    assert not check_evolution(
        old,
        parse_contract(
            yaml.safe_dump(
                {
                    **BASE,
                    "version": "1.0.1",
                    "columns": [
                        {**c, "type": "integer"} if c["name"] == "amt" else c
                        for c in BASE["columns"]
                    ],
                }
            )
        ),
    ).ok
    assert check_evolution(
        old,
        parse_contract(
            yaml.safe_dump(
                {
                    **BASE,
                    "version": "2.0.0",
                    "columns": [
                        {**c, "type": "integer"} if c["name"] == "amt" else c
                        for c in BASE["columns"]
                    ],
                }
            )
        ),
    ).ok
    assert check_evolution(old, old).ok and check_evolution(old, old).required_bump == "none"
    backwards = parse_contract(yaml.safe_dump({**BASE, "version": "0.9.0"}))
    assert "backwards" in check_evolution(old, backwards).messages[0]
    assert broken.version == "1.0.0"
    assert actual_bump("1.2.3", "1.2.4") == "patch" and actual_bump("1.2.3", "1.3.0") == "minor"
    assert actual_bump("1.2.3", "2.0.0") == "major" and actual_bump("1.2.3", "1.2.3") == "none"


def test_repository_examples_behave_as_documented():
    base = load_contract(CONTRACT_FILE)
    ex = POC / "contracts" / "examples"
    assert check_evolution(base, load_contract(ex / "orders_feed.v1.1.0_compatible.yaml")).ok
    major = check_evolution(base, load_contract(ex / "orders_feed.v2.0.0_breaking.yaml"))
    assert major.ok and major.required_bump == "major"
    bad = check_evolution(base, load_contract(ex / "orders_feed.v1.0.1_bad_bump.yaml"))
    assert not bad.ok and bad.required_bump == "major"


# ---- CLI and git-based gate


def test_cli_validate_diff_lint_exit_codes(feeds, tmp_path, capsys):
    out, manifest = feeds
    as_of = ["--as-of", manifest["newest_order_date"]]
    c = str(CONTRACT_FILE)
    assert cli(["validate", c, str(out / "orders_clean.csv"), *as_of]) == 0
    js = tmp_path / "r.json"
    assert cli(["validate", c, str(out / "orders_defective.csv"), *as_of, "--json", str(js)]) == 1
    assert json.loads(js.read_text())["contract"] == "orders_feed"
    assert cli(["validate", c, str(tmp_path / "missing.csv"), *as_of]) == 2
    ex = POC / "contracts" / "examples"
    assert cli(["diff", c, str(ex / "orders_feed.v1.1.0_compatible.yaml")]) == 0
    assert cli(["diff", c, str(ex / "orders_feed.v1.0.1_bad_bump.yaml")]) == 1
    assert cli(["lint", c, str(ex / "orders_feed.v2.0.0_breaking.yaml")]) == 0
    broken = tmp_path / "broken.yaml"
    broken.write_text("contract: x\n")
    assert cli(["lint", str(broken)]) == 2
    assert "missing required key" in capsys.readouterr().err


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   check=True, capture_output=True)  # fmt: skip


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "orders_feed.yaml").write_text(CONTRACT_FILE.read_text())
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def test_git_gate_accepts_correct_bumps_and_rejects_hidden_breaking_changes(repo):
    ok, lines = check_repo(repo, "contracts", "HEAD")
    assert ok and "required bump **none**" in lines[0]

    target = repo / "contracts" / "orders_feed.yaml"
    text = CONTRACT_FILE.read_text()
    breaking_patch = text.replace("version: 1.0.0", "version: 1.0.1").replace(
        "max: 1000000", "max: 10"
    )
    target.write_text(breaking_patch)
    ok, lines = check_repo(repo, "contracts", "HEAD")
    assert not ok and "REJECTED" in lines[0] and "MAJOR" in lines[0]

    target.write_text(breaking_patch.replace("version: 1.0.1", "version: 2.0.0"))
    assert check_repo(repo, "contracts", "HEAD")[0]


def test_git_gate_new_invalid_and_deleted_contracts(repo):
    new = repo / "contracts" / "fresh.yaml"
    new.write_text(CONTRACT_FILE.read_text().replace("orders_feed", "fresh_feed"))
    ok, lines = check_repo(repo, "contracts", "HEAD")
    assert ok and any("new contract fresh_feed" in line for line in lines)
    (repo / "contracts" / "broken.yaml").write_text("contract: nope\n")
    ok, lines = check_repo(repo, "contracts", "HEAD")
    assert not ok and any(line.startswith("FAIL") and "broken.yaml" in line for line in lines)
    (repo / "contracts" / "broken.yaml").unlink()
    (repo / "contracts" / "orders_feed.yaml").unlink()
    ok, lines = check_repo(repo, "contracts", "HEAD")
    assert not ok and any("contract deleted" in line for line in lines)


def test_cli_check_subcommand(repo, capsys):
    assert cli(["check", "--repo", str(repo), "--base-ref", "HEAD"]) == 0
    (repo / "contracts" / "orders_feed.yaml").write_text("garbage: [")
    assert cli(["check", "--repo", str(repo), "--base-ref", "HEAD"]) == 1


def test_demo_end_to_end(tmp_path):
    demo(out_dir=tmp_path / "out", work_dir=tmp_path / "work")
    data = json.loads((tmp_path / "out" / "results.json").read_text())
    assert all(row["exact"] for row in data["detection"])
    assert (
        data["feeds"]["orders_clean"]["passed"] and not data["feeds"]["orders_defective"]["passed"]
    )
    assert data["evolution"]["orders_feed.v1.0.1_bad_bump"]["ok"] is False
    assert (tmp_path / "out" / "results.md").read_text().startswith("# Results")
