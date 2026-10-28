import numpy as np
import pytest
import yaml
from canary_model_serving.controller import STEPS, big_bang, rollout
from canary_model_serving.gates import GateConfig, evaluate
from canary_model_serving.main import run
from canary_model_serving.manifests import inference_service, lint, render_stage
from canary_model_serving.sim import BASELINE, SCENARIOS, Variant, draw

DIGEST = "registry.example.com/model@sha256:" + "a" * 64
CFG = GateConfig()


def samples(cand: Variant, n=20000, seed=0):
    rng = np.random.default_rng(seed)
    return draw(cand, n, rng), draw(BASELINE, n, rng)


def test_gate_waits_when_data_is_insufficient():
    c, b = samples(Variant("x", error_rate=0.2), n=100)
    assert evaluate(CFG, c, b).action == "continue"


def test_gate_rolls_back_clear_regressions_and_names_the_reason():
    assert "error rate" in evaluate(CFG, *samples(Variant("x", error_rate=0.03))).reason
    assert "p95" in evaluate(CFG, *samples(Variant("x", latency_median_ms=120.0))).reason
    assert "accuracy" in evaluate(CFG, *samples(Variant("x", accuracy=0.85))).reason


def test_gate_advances_identical_candidate_with_enough_data():
    assert evaluate(CFG, *samples(BASELINE, n=60000, seed=3)).action == "advance"


def test_tolerable_difference_is_not_rolled_back():
    # +0.3pp is real but inside the 0.5pp margin: must never produce a rollback
    verdicts = {
        evaluate(CFG, *samples(Variant("x", error_rate=0.013), n=40000, seed=s)).action
        for s in range(10)
    }
    assert "rollback" not in verdicts


def test_false_rollback_rate_on_identical_candidate_is_low():
    rng = np.random.default_rng(7)
    outs = [rollout(BASELINE, np.random.default_rng(int(rng.integers(1e9)))) for _ in range(60)]
    assert sum(o.result == "rolled_back" for o in outs) <= 3


@pytest.mark.parametrize(
    "name",
    [
        "latency +35%",
        "error rate 1% -> 3%",
        "silent accuracy drop 92% -> 87%",
        "latency tail blow-up (sigma 0.3 -> 0.7)",
    ],
)
def test_regressions_are_rolled_back_early(name):
    outs = [rollout(SCENARIOS[name], np.random.default_rng(s)) for s in range(15)]
    assert all(o.result == "rolled_back" for o in outs)
    assert max(o.minutes for o in outs) <= 120


def test_canary_limits_blast_radius_versus_big_bang():
    cand = SCENARIOS["error rate 1% -> 3%"]
    outs = [rollout(cand, np.random.default_rng(s)) for s in range(20)]
    assert np.mean([o.excess_errors for o in outs]) < big_bang(cand)["excess_errors"] / 5


def test_rollout_never_exceeds_planned_steps_and_records_timeline():
    o = rollout(BASELINE, np.random.default_rng(1))
    assert {t[1] for t in o.timeline} <= set(STEPS) and o.timeline[0][1] == 5
    assert [t[0] for t in o.timeline] == sorted(t[0] for t in o.timeline)


def test_undecidable_candidate_fails_closed():
    o = rollout(
        Variant("edge", error_rate=0.013),
        np.random.default_rng(2),
        cfg=GateConfig(max_error_increase=0.0031),
    )
    assert o.result == "rolled_back" and "fail closed" in o.reason


def test_scenario_table_end_to_end():
    r = run(seeds=30)
    assert r["healthy (identical)"]["rollback_rate"] <= 0.1
    for k in ("latency +35%", "error rate 1% -> 3%", "silent accuracy drop 92% -> 87%"):
        assert r[k]["rollback_rate"] >= 0.95


# --- manifests ---------------------------------------------------------------------------------------------
def test_generated_manifest_passes_lint_and_round_trips():
    text = render_stage("fraud", "ml-serving", DIGEST, DIGEST.replace("a", "b"), 20)
    doc = yaml.safe_load(text)
    assert (
        lint(doc) == []
        and doc["spec"]["predictor"]["canaryTrafficPercent"] == 20
        and doc["kind"] == "InferenceService"
    )


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda d: d["spec"]["predictor"].update(canaryTrafficPercent=37), "canaryTrafficPercent"),
        (
            lambda d: d["spec"]["predictor"]["containers"][0].update(
                image="registry.example.com/model:latest"
            ),
            "sha256 digest",
        ),
        (lambda d: d["spec"]["predictor"]["containers"][0].pop("resources"), "requests and limits"),
        (lambda d: d["spec"]["predictor"]["containers"][0].pop("readinessProbe"), "readinessProbe"),
        (
            lambda d: d["metadata"]["annotations"].update({"rollout/previous-image": ""}),
            "rollback has a target",
        ),
        (lambda d: d["metadata"].update(namespace="default"), "default namespace"),
        (lambda d: d["spec"]["predictor"].update(containers=[]), "no containers"),
    ],
)
def test_lint_catches_each_unsafe_manifest(mutate, needle):
    doc = inference_service("fraud", "ml-serving", DIGEST, 20, DIGEST.replace("a", "b"))
    mutate(doc)
    assert any(needle in m for m in lint(doc))
