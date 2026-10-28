import os

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
import numpy as np  # noqa: E402
import pytest  # noqa: E402
import yaml  # noqa: E402
from jinja2.exceptions import SecurityError, UndefinedError  # noqa: E402
from mlflow_registry_promotion.registry import make_data  # noqa: E402
from model_card_generator.card import (  # noqa: E402
    REQUIRED_CONTEXT,
    CardError,
    bootstrap_auc,
    check_context,
    expected_calibration_error,
    render,
)
from model_card_generator.main import POC_DIR  # noqa: E402

CTX = yaml.safe_load((POC_DIR / "model_card_generator" / "context.yaml").read_text())
rng = np.random.default_rng(0)
Y = rng.integers(0, 2, 600)
P = np.clip(Y * 0.6 + rng.normal(0.2, 0.2, 600), 0.01, 0.99)
G = rng.integers(0, 2, 600)


def card(**kw):
    args = dict(
        name="m",
        version="3",
        run_id="abc12345",
        params={"C": "1.0", "seed": "0"},
        y=Y,
        p=P,
        group=G,
        ctx=CTX,
        generated="2026-01-01",
    )
    args.update(kw)
    return render(**args)


def test_card_contains_every_required_section_in_order():
    c = card()
    heads = [ln for ln in c.splitlines() if ln.startswith("#")]
    assert heads[0] == "# Model card: m v3"
    for h in (
        "## Overview",
        "## Intended use",
        "### Out of scope",
        "## Training data",
        "## Training procedure",
        "## Evaluation",
        "### Performance by subgroup",
        "## Limitations and risks",
        "## Contact",
    ):
        assert h in heads
    assert (
        heads.index("## Intended use")
        < heads.index("## Evaluation")
        < heads.index("## Limitations and risks")
    )


def test_card_is_deterministic_and_facts_come_from_the_inputs():
    assert card() == card()
    c = card()
    assert (
        "| C | 1.0 |" in c
        and "`abc12345`" in c
        and "Held-out set of 600 examples" in c
        and "2026-01-01" in c
    )


@pytest.mark.parametrize("field", REQUIRED_CONTEXT)
def test_missing_or_empty_required_section_is_refused_by_name(field):
    for bad in (
        {k: v for k, v in CTX.items() if k != field},
        {**CTX, field: [] if isinstance(CTX[field], list) else ""},
    ):
        with pytest.raises(CardError, match=field):
            card(ctx=bad)


def test_lists_must_be_lists():
    with pytest.raises(CardError, match="must be a list"):
        check_context({**CTX, "limitations": "just a string"})


def test_template_syntax_in_user_text_is_rendered_literally_not_executed():
    evil = {
        **CTX,
        "intended_use": "{{ 7 * 7 }} and {% for i in range(3) %}x{% endfor %}",
        "contact": "{{ ''.__class__.__mro__ }}",
    }
    c = card(ctx=evil)
    assert (
        "{{ 7 * 7 }} and {% for i in range(3) %}x{% endfor %}" in c
        and "49" not in c.split("## Intended use")[1].split("###")[0]
    )
    assert "{{ ''.__class__.__mro__ }}" in c


def test_sandbox_blocks_dangerous_template_constructs_if_a_template_is_ever_user_controlled():
    from model_card_generator.card import ENV

    with pytest.raises(SecurityError):
        ENV.from_string("{{ ''.__class__.__mro__ }}").render()
    with pytest.raises(UndefinedError):
        ENV.from_string(
            "{{ nope }}"
        ).render()  # strict undefined: no silent blanks in a compliance document


def test_metrics_and_intervals():
    m = bootstrap_auc(Y, P)
    assert m.lo <= m.value <= m.hi and 0.5 < m.value <= 1.0
    assert bootstrap_auc(Y, P) == m  # seeded


def test_expected_calibration_error_known_values():
    y = np.array([0, 0, 1, 1])
    assert expected_calibration_error(y, np.array([0.0, 0.0, 1.0, 1.0])) == pytest.approx(0.0)
    assert expected_calibration_error(y, np.array([0.5, 0.5, 0.5, 0.5])) == pytest.approx(
        0.0
    )  # calibrated on average
    assert expected_calibration_error(y, np.array([1.0, 1.0, 0.0, 0.0]), bins=2) == pytest.approx(
        1.0
    )


def test_subgroup_gap_flag_appears_only_above_threshold():
    X, y, g = make_data(3000, 4)
    p = np.clip(
        0.5 + 0.3 * np.where(g == 0, 2 * y - 1, 0) + np.random.default_rng(1).normal(0, 0.15, 3000),
        0,
        1,
    )  # informative only for group 0
    flagged = render(
        name="m", version="1", run_id="r", params={}, y=y, p=p, group=g, ctx=CTX, generated="t"
    )
    assert "above the 0.03 review threshold" in flagged
    assert "above the" not in card(
        group=np.zeros(600, int) + (np.arange(600) % 2)
    )  # similar groups: no flag


def test_change_list_between_versions():
    prev = {"version": "2", "params": {"C": "1.0", "seed": "0"}, "auc": 0.80}
    c = card(params={"C": "0.5", "seed": "0", "extra": "x"}, prev=prev)
    assert (
        "## Changes since v2" in c
        and "parameter `C`: 1.0 -> 0.5" in c
        and "parameter `extra`: None -> x" in c
        and "AUC: 0.800 ->" in c
    )
    assert "Changes since" not in card()
