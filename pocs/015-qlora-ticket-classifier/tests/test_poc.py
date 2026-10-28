import copy

import numpy as np
import pytest
from qlora_ticket_classifier import experiment as ex
from qlora_ticket_classifier import optional_peft
from qlora_ticket_classifier.lora_net import Adam, LoraNet, featurize, fit, softmax


def to64(net):
    for ly in net.layers:
        for a in ("W", "b", "A", "B"):
            v = getattr(ly, a)
            if v is not None:
                setattr(ly, a, v.astype(np.float64))
        ly._deq = None
    net.head_W, net.head_b = net.head_W.astype(np.float64), net.head_b.astype(np.float64)
    return net


def numeric_grad(net, x, y, name, eps=1e-6):
    p = net.trainable()[name]
    g = np.zeros_like(p)
    it = np.nditer(p, flags=["multi_index"])
    for _ in it:
        i = it.multi_index
        old = p[i]
        p[i] = old + eps
        lp, _ = net.loss_and_grads(x, y)
        p[i] = old - eps
        lm, _ = net.loss_and_grads(x, y)
        p[i] = old
        g[i] = (lp - lm) / (2 * eps)
    return g


@pytest.mark.parametrize("mode", ["full", "lora", "qlora", "linear_probe"])
def test_manual_gradients_match_finite_differences(mode):
    rng = np.random.default_rng(0)
    net = LoraNet([32, 12, 8], 4, seed=1)
    if mode == "qlora":
        net.quantise_base("Q4_0")
    if mode in ("lora", "qlora"):
        net.add_lora(3, 6.0, seed=2)
        for ly in net.layers:  # non-zero B so the adapter path carries gradient into A
            ly.B = rng.normal(0, 0.3, ly.B.shape).astype(np.float32)
    net.mode = mode
    net = to64(net)
    x, y = rng.normal(size=(10, 32)), rng.integers(0, 4, 10)
    _, g = net.loss_and_grads(x, y)
    assert set(g) == set(net.trainable())
    for name in g:
        num = numeric_grad(net, x, y, name)
        assert np.allclose(g[name], num, rtol=1e-4, atol=1e-7), name


def test_adapter_is_an_exact_noop_at_initialisation():
    net = LoraNet([16, 8], 3, seed=0)
    x = np.random.default_rng(0).normal(size=(5, 16)).astype(np.float32)
    before = net.forward(x)
    net.add_lora(4, 8.0, seed=1)
    assert np.array_equal(before, net.forward(x))


def test_only_declared_parameters_are_trainable():
    net = LoraNet([16, 8], 3, seed=0)
    net.add_lora(2, 4.0, seed=0)
    net.mode = "linear_probe"
    assert set(net.trainable()) == {"head_W", "head_b"}
    net.mode = "lora"
    assert set(net.trainable()) == {"head_W", "head_b", "A0", "B0"}
    net.mode = "full"
    assert set(net.trainable()) == {"head_W", "head_b", "W0", "b0"}


def test_quantised_base_is_much_smaller_and_close_to_fp32():
    net = LoraNet([256, 128, 64], 3, seed=0)
    dense = net.n_base() * 4
    w = net.layers[0].W.copy()
    packed = net.quantise_base("Q4_0")
    assert dense / packed > 5  # 4.5 bits per weight vs 32
    rel = np.linalg.norm(net.layers[0].weight - w) / np.linalg.norm(w)
    assert 0 < rel < 0.2


def test_optimizer_moves_parameters_downhill():
    p = {"w": np.array([5.0])}
    opt = Adam(p, lr=0.5)
    for _ in range(50):
        opt.step({"w": 2 * p["w"]})
    assert abs(p["w"][0]) < 0.5


def test_featurize_is_deterministic_normalised_and_order_sensitive():
    a, b = featurize(["card was declined"]), featurize(["declined card was"])
    assert np.array_equal(a, featurize(["card was declined"])) and abs(np.linalg.norm(a) - 1) < 1e-6
    assert not np.array_equal(a, b)  # bigrams differ
    assert featurize([""]).sum() == 0


def test_softmax_rows_sum_to_one():
    assert np.allclose(softmax(np.random.default_rng(0).normal(size=(4, 5)) * 50).sum(1), 1)


# ---------- end-to-end behaviour (small budgets) ----------


@pytest.fixture(scope="module")
def base():
    return ex.pretrain(n=2500, epochs=10)


def test_pretraining_learns_the_coarse_task(base):
    assert base[1] > 0.9


def test_lora_training_leaves_the_pretrained_base_byte_identical(base):
    net = base[0]
    h = net.base_hash()
    texts, y = ex.fine_data(60, 1)
    ex.adapt(net, "lora", texts, y, 0, epochs=10)
    ex.adapt(net, "qlora", texts, y, 0, epochs=10)
    assert net.base_hash() == h


def test_merged_lora_net_reproduces_the_adapter_forward_pass(base):
    texts, y = ex.fine_data(60, 1)
    m = ex.adapt(base[0], "lora", texts, y, 0, epochs=15)
    x = featurize(ex.fine_data(40, 5)[0])
    assert np.abs(m.forward(x) - m.merge().forward(x)).max() < 1e-4
    assert (m.predict(x) == m.merge().predict(x)).all()


def test_adapting_beats_a_frozen_encoder_with_the_same_labels(base):
    tx, ty = ex.fine_data(1000, 99)
    xt = featurize(tx)
    probe, lora = [], []
    for s in range(3):
        texts, y = ex.fine_data(120, 1000 + s)
        probe.append((ex.adapt(base[0], "linear_probe", texts, y, s).predict(xt) == ty).mean())
        lora.append((ex.adapt(base[0], "lora", texts, y, s).predict(xt) == ty).mean())
    assert np.mean(lora) > np.mean(probe) + 0.05


def test_qlora_tracks_lora_within_a_few_points(base):
    tx, ty = ex.fine_data(1000, 99)
    xt = featurize(tx)
    texts, y = ex.fine_data(120, 1001)
    a = (ex.adapt(base[0], "lora", texts, y, 1).predict(xt) == ty).mean()
    b = (ex.adapt(base[0], "qlora", texts, y, 1).predict(xt) == ty).mean()
    assert abs(a - b) < 0.06


def test_early_stopping_keeps_the_best_validation_weights():
    net = LoraNet([32, 8], 2, seed=0)
    net.mode = "full"
    rng = np.random.default_rng(0)
    x, y = rng.normal(size=(40, 32)).astype(np.float32), rng.integers(0, 2, 40)
    r = fit(net, x, y, epochs=30, lr=0.05, seed=0, val=(x, y), patience=3)
    assert r["best_val_acc"] == float((net.predict(x) == y).mean())


def test_deepcopy_models_do_not_share_state(base):
    a = copy.deepcopy(base[0])
    a.layers[0].W[0, 0] += 1
    assert base[0].layers[0].W[0, 0] != a.layers[0].W[0, 0]


# ---------- optional real-framework recipe ----------


def test_optional_peft_recipe_fails_closed_with_a_clear_message_when_dependencies_are_missing():
    missing = optional_peft.missing_dependencies()
    if missing:
        with pytest.raises(optional_peft.MissingDependency, match="not run in this repository"):
            optional_peft.run_recipe()
    cfg = optional_peft.lora_config()
    assert cfg["r"] == ex.RANK and cfg["lora_alpha"] == ex.ALPHA
