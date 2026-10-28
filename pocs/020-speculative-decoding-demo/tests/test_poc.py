import numpy as np
import pytest
from speculative_decoding_demo.decoding import (
    autoregressive,
    expected_tokens_per_call,
    sample,
    speculative,
    speedup,
)
from speculative_decoding_demo.lm import BigramDraft, ExactDraft, NoisyCopyDraft, TrigramLM
from speculative_decoding_demo.main import exact_joint, lossless_check, run, tv_distance


@pytest.fixture(scope="module")
def target():
    return TrigramLM(vocab=12, seed=3)


def test_target_probabilities_are_distributions(target):
    for ctx in ([], [1], [1, 2], [5, 5, 5]):
        p = target.probs(ctx)
        assert p.shape == (12,) and p.min() >= 0 and p.sum() == pytest.approx(1)


def test_parallel_verification_is_one_call_for_many_positions(target):
    before = target.calls
    out = target.probs_batch([1, 2], [3, 4, 5])
    assert len(out) == 4 and target.calls == before + 1
    assert np.allclose(out[2], target.probs([1, 2, 3, 4]))


@pytest.mark.parametrize(
    "mk",
    [
        lambda t: ExactDraft(t),
        lambda t: NoisyCopyDraft(t, 0.5),
        lambda t: BigramDraft(t),
        lambda t: BigramDraft(t, mix=0.97, noise=0.5),
    ],
)
def test_greedy_speculative_equals_greedy_autoregressive(target, mk):
    base, _ = autoregressive(target, 60, np.random.default_rng(0), greedy=True)
    for k in (1, 3, 7):
        out, _ = speculative(target, mk(target), 60, k, np.random.default_rng(1), greedy=True)
        assert out == base


@pytest.mark.parametrize(
    "mk,k",
    [
        (lambda t: NoisyCopyDraft(t, 0.5), 3),
        (lambda t: BigramDraft(t, mix=0.9, noise=0.5), 4),
        (lambda t: ExactDraft(t), 2),
    ],
)
def test_sampling_is_lossless_up_to_noise(target, mk, k):
    runs = 20_000
    tv = lossless_check(target, mk(target), k=k, n_tokens=2, runs=runs, seed=7)
    rng = np.random.default_rng(99)
    exact = exact_joint(target, 2)
    keys, probs = list(exact), np.array(list(exact.values()))
    floor = (
        0.5
        * np.abs(
            np.bincount(rng.choice(len(keys), runs, p=probs), minlength=len(keys)) / runs - probs
        ).sum()
    )
    assert tv < floor * 1.5 + 0.005


def test_single_step_residual_sampling_matches_target_for_arbitrary_p_and_q():
    class Fixed:
        v = 4

        def __init__(self, p):
            self.p, self.calls = np.array(p), 0

        def probs(self, ctx):
            return self.p

        def probs_batch(self, ctx, extra):
            self.calls += 1
            return [self.p for _ in range(len(extra) + 1)]

    p, q = [0.1, 0.6, 0.2, 0.1], [0.7, 0.1, 0.1, 0.1]
    rng = np.random.default_rng(0)
    toks = [speculative(Fixed(p), Fixed(q), 1, 1, rng)[0][0] for _ in range(40_000)]
    emp = np.bincount(toks, minlength=4) / len(toks)
    assert tv_distance(dict(enumerate(emp)), dict(enumerate(p))) < 0.012


def test_draft_proposing_a_zero_probability_token_is_always_rejected():
    class Zero:
        v = 3
        calls = 0

        def probs_batch(self, ctx, extra):
            return [np.array([0.0, 0.5, 0.5])] * (len(extra) + 1)

    class Draft:
        def probs(self, ctx):
            return np.array([1.0, 0.0, 0.0])  # always proposes token 0, which the target forbids

    rng = np.random.default_rng(0)
    out, st = speculative(Zero(), Draft(), 200, 3, rng)
    assert 0 not in out and st.accepted == 0


def test_k_zero_is_plain_decoding(target):
    out, st = speculative(target, ExactDraft(target), 30, 0, np.random.default_rng(0))
    assert len(out) == 30 and st.target_calls == 30 and st.draft_calls == 0


def test_exact_draft_accepts_everything(target):
    _, st = speculative(target, ExactDraft(target), 200, 5, np.random.default_rng(0))
    assert st.acceptance == 1.0 and st.emitted_per_target_call == pytest.approx(6, abs=0.5)


def test_measured_tokens_per_call_matches_geometric_theory(target):
    d = NoisyCopyDraft(target, 0.5)
    sts = [speculative(target, d, 600, 4, np.random.default_rng(s))[1] for s in range(15)]
    alpha = np.mean([s.acceptance for s in sts])
    measured = np.mean([s.emitted_per_target_call for s in sts])
    assert measured == pytest.approx(expected_tokens_per_call(alpha, 4), rel=0.06)


def test_theory_helpers():
    assert expected_tokens_per_call(1.0, 4) == 5 and expected_tokens_per_call(0.0, 4) == 1
    assert (
        speedup(0.9, 4, 0.1) > 1 > speedup(0.2, 8, 0.1)
    )  # a weak draft with a long lookahead is slower than no speculation
    assert max(range(1, 16), key=lambda k: speedup(0.8, k, 0.1)) not in (
        1,
        15,
    )  # an interior optimum exists


def test_length_prompt_and_determinism(target):
    a, _ = speculative(
        target, NoisyCopyDraft(target, 0.3), 7, 4, np.random.default_rng(5), prompt=[1, 2]
    )
    b, _ = speculative(
        target, NoisyCopyDraft(target, 0.3), 7, 4, np.random.default_rng(5), prompt=[1, 2]
    )
    assert a == b and len(a) == 7
    assert sample(np.array([0.0, 1.0]), np.random.default_rng(0)) == 1


def test_demo_summary_claims():
    r = run(quick=True)
    assert r["greedy_identical_to_target"]
    assert all(v < r["tv_noise_floor"] * 2 + 0.01 for v in r["lossless_tv"].values())
    by = {(s["draft"], s["k"]): s for s in r["sweep"]}
    assert by[("noisy copy (sigma 0.2)", 4)]["modelled_speedup"] > 2.5
    assert by[("bigram summary", 8)]["modelled_speedup"] < 1.0
    assert all(
        abs(s["tokens_per_target_call"] - s["theory_tokens_per_call"]) < 0.25 for s in r["sweep"]
    )
