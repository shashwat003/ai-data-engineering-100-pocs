import pytest
import yaml
from ai_risk_register_generator.classify import classify, load_inventory, summarize
from ai_risk_register_generator.main import run
from ai_risk_register_generator.render import render

R = run()
BY = {i["id"]: i for i in R["items"]}


def sysd(**kw):
    base = {"id": "X", "name": "n", "purpose": "p", "controls": []}
    return {**base, **kw}


def test_expected_tiers_for_the_inventory():
    assert {k: v["tier"] for k, v in BY.items()} == {
        "SYS-01": "high", "SYS-02": "high", "SYS-03": "limited", "SYS-04": "minimal",
        "SYS-05": "minimal", "SYS-06": "prohibited", "SYS-07": "limited",
    }  # fmt: skip


def test_gaps_are_the_obligations_without_evidence():
    assert BY["SYS-01"]["gaps"] == []
    assert set(BY["SYS-02"]["gaps"]) == {
        "risk_management", "data_governance", "human_oversight", "accuracy_monitoring",
    }  # fmt: skip
    assert BY["SYS-07"]["gaps"] == ["content_labelling"]


def test_prohibited_overrides_everything_and_says_stop():
    c = classify(sysd(domains=["emotion_recognition_workplace", "credit_scoring"]))
    assert c["tier"] == "prohibited" and c["action"].startswith("STOP") and c["obligations"] == {}


def test_every_classification_cites_a_rule():
    for i in R["items"]:
        assert i["reasons"] and (
            i["tier"] == "minimal" or "Art" in i["reasons"][0] or "Annex" in i["reasons"][0]
        )


def test_minimal_has_no_obligations_and_no_action_needed():
    c = classify(sysd())
    assert c["tier"] == "minimal" and c["gaps"] == [] and "no open gaps" in c["action"]


def test_chatbot_without_disclosure_has_a_gap():
    c = classify(sysd(interacts_with_humans=True))
    assert c["tier"] == "limited" and c["gaps"] == ["disclosure_to_users"]


def test_summary_counts():
    s = summarize(R["items"])
    assert s["systems"] == 7 and s["by_tier"]["prohibited"] == 1 and s["open_gaps"] == 5


def test_register_orders_most_severe_first():
    reg = R["register"]
    assert reg.index("SYS-06") < reg.index("SYS-01") < reg.index("SYS-03") < reg.index("SYS-04")
    assert "Not legal advice" in reg


def test_inventory_text_cannot_inject_template_code_or_table_breaks(tmp_path):
    items = [
        classify(
            sysd(
                name="{{ 7*7 }} {% for x in y %}",
                purpose="a | b\n# pwn",
                domains=["credit_scoring"],
            )
        )
    ]
    out = render(items, summarize(items))
    assert "{{ 7*7 }}" in out and "49" not in out
    assert "a \\| b # pwn" in out


def test_unsafe_yaml_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- !!python/object/apply:os.system ['echo hi']\n")
    with pytest.raises(yaml.YAMLError):
        load_inventory(p)


@pytest.mark.parametrize(
    "doc,msg",
    [
        ("{}", "list"),
        ("- 5", "mapping"),
        ("- {id: a, name: n}", "missing"),
        ("- {id: a, name: n, purpose: p, evil: 1}", "unknown fields"),
        ("- {id: a, name: n, purpose: p, domains: [bogus]}", "unknown domain"),
        ("- {id: a, name: n, purpose: p}\n- {id: a, name: m, purpose: q}", "duplicate"),
    ],
)
def test_invalid_inventories_fail_loudly(tmp_path, doc, msg):
    p = tmp_path / "i.yaml"
    p.write_text(doc)
    with pytest.raises(ValueError, match=msg):
        load_inventory(p)


def test_unmapped_controls_are_surfaced_not_credited():
    c = classify(sysd(domains=["credit_scoring"], controls=["magic_compliance"]))
    assert c["unmapped_controls"] == ["magic_compliance"] and len(c["gaps"]) == 6


def test_run_is_deterministic():
    assert run()["register"] == R["register"]
