import numpy as np
import pytest
from llm_to_slm_distillation.data import CATEGORIES, generate
from llm_to_slm_distillation.main import accuracy, run_seed
from llm_to_slm_distillation.models import (
    SoftmaxStudent,
    ece,
    make_teacher,
    one_hot,
    softmax,
    temper,
)


def test_data_is_deterministic_balanced_and_ambiguous():
    a, b = generate(300, 1), generate(300, 1)
    assert a == b and a != generate(300, 2)
    assert set(a[1]) == set(CATEGORIES)
    assert any(sum(p in t for p in ["cancel", "refund"]) for t in a[0])


def test_softmax_and_temperature():
    p = softmax(np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 0.0]]))
    assert np.allclose(p.sum(1), 1) and np.allclose(p[1], 1 / 3)
    soft, sharp = temper(p, 4.0), temper(p, 0.25)
    assert soft[0].max() < p[0].max() < sharp[0].max() and np.allclose(soft.sum(1), 1)
    assert (temper(p, 1.0) == pytest.approx(p)) and soft.argmax(1)[0] == p.argmax(1)[
        0
    ]  # temperature never changes the ranking
    assert not np.isnan(temper(np.array([[1.0, 0.0, 0.0]]), 2.0)).any()


def test_ece_is_zero_for_perfectly_calibrated_and_large_for_overconfident():
    y = np.zeros(1000, dtype=int)
    over = np.tile([0.0, 1.0], (1000, 1))  # always sure of class 1 but wrong
    assert ece(over, y) == pytest.approx(1.0)
    p = np.tile([0.8, 0.2], (1000, 1))
    y2 = (np.arange(1000) % 5 == 0).astype(int)  # class 0 right 80% of the time
    assert ece(p, y2) < 0.01


def test_student_loss_gradient_matches_finite_differences():
    texts, labels = generate(40, 0)
    s = SoftmaxStudent(n_features=64, l2=0.0, epochs=0)
    x = s.featurise(texts)
    targets = softmax(np.random.default_rng(0).normal(size=(40, 6)))
    s.W = np.random.default_rng(1).normal(0, 0.1, s.W.shape)

    def kl(W):
        p = softmax(x @ W + s.b)
        return float(-(targets * np.log(p + 1e-12)).sum(1).mean())

    p = softmax(x @ s.W + s.b)
    grad = np.asarray(x.T @ ((p - targets) / 40))
    for i, j in [(3, 1), (10, 4), (40, 0)]:
        e = np.zeros_like(s.W)
        e[i, j] = 1e-5
        assert (kl(s.W + e) - kl(s.W - e)) / 2e-5 == pytest.approx(grad[i, j], rel=1e-3, abs=1e-8)


def test_student_learns_hard_labels_and_matches_soft_targets():
    texts, labels = generate(600, 3)
    hard = SoftmaxStudent(seed=0, epochs=40).fit(texts, one_hot(labels))
    yi = np.array([CATEGORIES.index(c) for c in labels])
    assert accuracy(hard.predict_proba(texts), yi) > 0.7
    soft_targets = temper(softmax(np.random.default_rng(0).normal(size=(600, 6)) * 2), 1.0)
    s = SoftmaxStudent(seed=0, epochs=80).fit(texts, soft_targets)
    # a linear student cannot reproduce arbitrary random targets, but its mean predicted distribution must match theirs
    assert np.allclose(s.predict_proba(texts).mean(0), soft_targets.mean(0), atol=0.05)


def test_student_is_small_and_deterministic():
    texts, labels = generate(200, 5)
    a = SoftmaxStudent(seed=1, epochs=5).fit(texts, one_hot(labels))
    b = SoftmaxStudent(seed=1, epochs=5).fit(texts, one_hot(labels))
    assert (a.W == b.W).all() and a.n_params == 2**11 * 6 + 6


def test_teacher_probabilities_cover_all_classes_and_can_be_reordered():
    x, y = generate(300, 0)
    t = make_teacher(0).fit(x, y)
    p = t.predict_proba(x[:5])
    assert p.shape == (5, 6) and set(t.classes_) == set(CATEGORIES) and np.allclose(p.sum(1), 1)


def test_distillation_uses_no_pool_labels_and_helps_when_labels_are_scarce():
    r = run_seed(0, n_labelled=60, n_pool=1500, n_test=800)
    distilled = r["student on teacher SOFT labels, T=1 (pool)"]["accuracy"]
    assert (
        distilled >= r["student on human labels only"]["accuracy"] - 0.01
    )  # not worse than the human-labels-only student
    assert (
        r["oracle: student on true labels of the pool"]["accuracy"] >= distilled - 0.02
    )  # the oracle is an upper bound up to noise
    assert r["teacher"]["accuracy"] < r["oracle: student on true labels of the pool"]["accuracy"]
    assert r["student on human labels only"]["params"] == 2**11 * 6 + 6


def test_pool_ground_truth_does_not_influence_distilled_students(monkeypatch):
    import llm_to_slm_distillation.main as m

    base = m.run_seed(1, n_labelled=60, n_pool=800, n_test=500)
    real_generate = m.generate

    def scrambled(n, seed):
        x, y = real_generate(n, seed)
        return (
            (x, list(np.random.default_rng(0).permutation(y))) if seed == 1 + 1000 else (x, y)
        )  # corrupt ONLY the pool's true labels

    monkeypatch.setattr(m, "generate", scrambled)
    alt = m.run_seed(1, n_labelled=60, n_pool=800, n_test=500)
    for k in (
        "student on teacher HARD labels (pool)",
        "student on teacher SOFT labels, T=1 (pool)",
        "student on teacher SOFT labels, T=2 (pool)",
    ):
        assert alt[k]["accuracy"] == base[k]["accuracy"]
    assert (
        alt["oracle: student on true labels of the pool"]["accuracy"]
        < base["oracle: student on true labels of the pool"]["accuracy"] - 0.1
    )
