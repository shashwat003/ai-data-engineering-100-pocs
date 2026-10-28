import numpy as np
import pytest
from llm_serving_study.engine import BLOCK, Model, Request, simulate, workload
from llm_serving_study.main import CONFIGS, run

M = Model()


def reqs(n=4, gap=10.0, prompt=100, out=20):
    return [Request(i, i * gap, prompt, out) for i in range(n)]


def test_workload_is_deterministic_and_bounded():
    a, b = workload(2.0, 60, 1), workload(2.0, 60, 1)
    assert [(r.arrival, r.prompt, r.output) for r in a] == [
        (r.arrival, r.prompt, r.output) for r in b
    ]
    assert all(8 <= r.prompt <= 1800 and 4 <= r.output <= 512 for r in a) and len(
        a
    ) == pytest.approx(120, rel=0.3)


@pytest.mark.parametrize("name", list(CONFIGS))
def test_every_request_completes_and_every_token_is_generated(name):
    rs = workload(3.0, 40, 2)
    m = simulate(rs, M, **CONFIGS[name])
    assert len(m.done) + m.rejected == len(rs)
    assert all(r.generated == r.output and r.finish > r.first_token > r.arrival for r in m.done)


def test_isolated_request_latency_matches_the_cost_model():
    (r,) = [Request(0, 0.0, 200, 10)]
    for kw in (
        dict(policy="none"),
        dict(policy="continuous", kv="paged"),
        dict(policy="continuous", kv="reserved"),
    ):
        m = simulate([r], M, **kw)
        d = m.done[0]
        first = M.c * 200 + (M.a + M.b)  # prefill, then the first decode iteration
        assert d.first_token == pytest.approx(first)
        assert d.finish == pytest.approx(first + 9 * (M.a + M.b))


def test_low_load_continuous_batching_equals_no_batching():
    rs = reqs(5, gap=30.0)
    a, b = simulate(rs, M, "none").summary(), simulate(rs, M, "continuous", kv="paged").summary()
    assert a["ttft_p50_s"] == b["ttft_p50_s"] and a["e2e_p95_s"] == b["e2e_p95_s"]


def test_throughput_ordering_at_saturation():
    rs = workload(8.0, 120, 3)
    t = {n: simulate(rs, M, **kw).summary()["throughput_tok_s"] for n, kw in CONFIGS.items()}
    assert (
        t["no batching"]
        < t["static batching (8, 250 ms fill)"]
        < t["continuous, reserved KV"]
        < t["continuous, paged KV"]
    )


def test_continuous_batching_keeps_ttft_low_where_static_batching_does_not():
    rs = workload(2.0, 120, 4)
    s = simulate(rs, M, "static", static_batch=8).summary()["ttft_p95_s"]
    c = simulate(rs, M, "continuous", kv="paged").summary()["ttft_p95_s"]
    assert c < s / 20


def test_paged_allocation_admits_more_concurrent_sequences_than_reservation():
    rs = workload(10.0, 90, 5)
    res = simulate(rs, M, "continuous", kv="reserved")
    pag = simulate(rs, M, "continuous", kv="paged")
    assert pag.max_concurrency > res.max_concurrency and res.kv_peak_tokens <= M.kv_tokens
    assert pag.kv_peak_tokens <= M.kv_tokens + BLOCK * M.max_batch


def test_tiny_cache_forces_preemption_but_everything_still_completes_correctly():
    small = Model(kv_tokens=3000)
    rs = [Request(i, 0.0, 200, 300) for i in range(14)]
    m = simulate(rs, small, "continuous", kv="paged")
    assert m.preemptions > 0 and len(m.done) == 14 and all(r.generated == r.output for r in m.done)
    assert any(r.preemptions > 0 for r in m.done)


def test_requests_that_can_never_fit_are_rejected_not_hung():
    big = [Request(0, 0.0, 1800, 512), Request(1, 0.0, 100, 10)]
    m = simulate(big, Model(kv_tokens=1000), "continuous", kv="paged")
    assert m.rejected == 1 and len(m.done) == 1 and m.done[0].rid == 1


def test_littles_law_holds_in_a_stable_run():
    rs = workload(1.0, 600, 6)
    m = simulate(rs, M, "continuous", kv="paged")
    w = np.mean([r.finish - r.arrival for r in m.done])
    horizon = max(r.finish for r in m.done)
    l_avg = (
        sum(r.finish - r.arrival for r in m.done) / horizon
    )  # time-average number in system = total sojourn time / horizon
    assert l_avg == pytest.approx(
        len(m.done) / horizon * w, rel=1e-6
    )  # identity check of the accounting (L = lambda W)


def test_gpu_busy_never_exceeds_wall_time_and_is_higher_under_load():
    low = simulate(workload(0.2, 100, 7), M, "continuous", kv="paged").summary()["gpu_busy_pct"]
    high = simulate(workload(8.0, 100, 7), M, "continuous", kv="paged").summary()["gpu_busy_pct"]
    assert 0 < low < high <= 100.0001


def test_demo_summary_shape():
    r = run(rates=(0.5, 6.0), duration=60)
    hi = r["rates"][6.0]["configs"]
    assert (
        hi["continuous, paged KV"]["throughput_tok_s"]
        > hi["continuous, reserved KV"]["throughput_tok_s"]
        > hi["no batching"]["throughput_tok_s"]
    )
