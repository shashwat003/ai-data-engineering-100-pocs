import numpy as np
import pytest
from endpoint_autoscaling.main import BURSTS, CFG, rate, run, validate_against_erlang_c
from endpoint_autoscaling.policies import Scheduled, StepScaling, TargetTracking, fixed
from endpoint_autoscaling.sim import Config, arrivals, erlang_c_wait, lognormal_params, simulate


def state(**kw):
    s = {
        "t": 0,
        "rate": 0.0,
        "rate_per_instance": 0.0,
        "util": 0.0,
        "instances": 2,
        "starting": 0,
        "workers": 4,
    }
    s.update(kw)
    return s


def test_erlang_c_known_values():
    assert erlang_c_wait(1.0, 1.0, 1) == float("inf")  # rho = 1
    assert erlang_c_wait(0.5, 1.0, 1) == pytest.approx(
        1.0
    )  # M/M/1: W_q = rho / (mu - lambda) = 0.5 / 0.5
    assert erlang_c_wait(2.0, 1.0, 4) < erlang_c_wait(3.0, 1.0, 4)


def test_simulator_matches_erlang_c():
    for v in validate_against_erlang_c().values():
        assert v["simulated_mean_wait_s"] == pytest.approx(
            v["erlang_c_mean_wait_s"], rel=0.12, abs=0.01
        )


def test_lognormal_service_has_the_requested_mean():
    mu, sg = lognormal_params(0.4, 0.4)
    assert np.exp(mu + sg**2 / 2) == pytest.approx(0.4)


def test_arrival_rate_follows_the_profile():
    arr = arrivals(rate, 86400, 3)
    noon = ((arr >= 11.5 * 3600) & (arr < 12.5 * 3600)).sum() / 3600
    night = ((arr >= 2 * 3600) & (arr < 3 * 3600)).sum() / 3600
    assert noon == pytest.approx(rate(12 * 3600), rel=0.1) and night == pytest.approx(
        rate(2.5 * 3600), rel=0.15
    )
    start = BURSTS[0][0]
    assert rate(start + 10) == pytest.approx(2.5 * rate(start - 700), rel=0.05) or rate(
        start + 10
    ) > 2 * rate(start - 700)


def test_accounting_invariants():
    cfg = Config(queue_timeout_s=2.0)
    arr = arrivals(lambda t: 30.0, 600, 1)
    r = simulate(cfg, arr, fixed(2), 600, initial_instances=2)
    assert len(r.latencies) + r.dropped == r.total == len(arr)
    assert (
        (r.latencies > 0).all()
        and r.latencies.max() <= cfg.queue_timeout_s + 30 * cfg.service_mean_s * 5
    )  # waits are capped by the timeout
    assert r.instance_seconds == pytest.approx(2 * 600)  # billed per instance-second


def test_more_capacity_means_lower_latency_and_higher_cost():
    arr = arrivals(lambda t: 14.0, 1800, 2)
    res = {n: simulate(CFG, arr, fixed(n), 1800, initial_instances=n) for n in (2, 4, 8)}
    p95 = {
        n: np.percentile(r.latencies, 95) if len(r.latencies) else np.inf for n, r in res.items()
    }
    assert (
        p95[2] > p95[4] >= p95[8] * 0.95
        and res[2].instance_seconds < res[4].instance_seconds < res[8].instance_seconds
    )


def test_new_instances_help_only_after_the_startup_delay_but_are_billed_from_launch():
    arr = arrivals(lambda t: 30.0, 1200, 4)
    cfg = Config(service_mean_s=0.4, startup_s=300.0, queue_timeout_s=1e9)
    launched = []

    def scale_out_once(t, s):
        if not launched:
            launched.append(t)
            return 8
        return s["instances"] + s["starting"]

    r = simulate(cfg, arr, scale_out_once, 1200, initial_instances=1)
    early = r.latencies[arr[: len(r.latencies)] < 280]
    late = r.latencies[arr[: len(r.latencies)] > 600]
    assert early.mean() > 10 * late.mean()  # the backlog drains once capacity arrives
    assert (
        r.instance_seconds > (1 * 300 + 8 * 900) * 0.99 - 8 * 10
    )  # eight instances billed from the launch tick (t=10 s)... conservative bound


def test_scale_in_releases_capacity_and_cost():
    arr = arrivals(lambda t: 2.0, 3600, 5)
    pol = lambda t, s: 1 if t > 600 else 6  # noqa: E731
    r = simulate(CFG, arr, pol, 3600, initial_instances=6)
    assert r.instance_seconds < 6 * 3600 * 0.35 and r.scale_events >= 1


def test_target_tracking_scales_out_fast_and_in_slowly():
    tt = TargetTracking(6.0, out_cooldown=60, in_cooldown=300)
    assert tt(10, state(rate=30.0, instances=2)) == 5  # ceil(30 / 6)
    assert tt(40, state(rate=60.0, instances=5)) == 5  # still inside the scale-out cooldown
    assert tt(100, state(rate=60.0, instances=5)) == 10
    assert (
        tt(200, state(rate=3.0, instances=10)) == 10
    )  # scale-in blocked by cooldown and by the recent peak
    assert (
        tt(900, state(rate=3.0, instances=10)) < 10 and tt(900, state(rate=3.0, instances=10)) >= 1
    )


def test_target_tracking_never_goes_below_min_or_above_max():
    tt = TargetTracking(1.0, min_instances=2, max_instances=7, out_cooldown=0, in_cooldown=0)
    assert tt(1000, state(rate=1000.0, instances=2)) == 7
    tt2 = TargetTracking(10.0, min_instances=2, in_cooldown=0, out_cooldown=0)
    assert tt2(5000, state(rate=0.0, instances=9)) == 2


def test_step_scaling_thresholds_and_cooldown():
    ss = StepScaling(high=0.7, low=0.3, step_out=2, step_in=1, cooldown=120)
    assert ss(0, state(util=0.9, instances=3)) == 5
    assert ss(60, state(util=0.9, instances=5)) == 5  # cooldown
    assert ss(200, state(util=0.1, instances=5)) == 4
    assert ss(400, state(util=0.5, instances=4)) == 4


def test_scheduled_floor_applies_in_window_only():
    pol = Scheduled([(8 * 3600, 18 * 3600, 5)], fixed(1))
    assert (
        pol(9 * 3600, state()) == 5
        and pol(2 * 3600, state()) == 1
        and pol(86400 + 9 * 3600, state()) == 5
    )  # repeats daily


def test_demo_policy_ranking():
    r = run()
    p = r["policies"]
    assert p["fixed 2 instances"]["slo_violation_pct"] > 30
    assert (
        p["target tracking (6 rps/instance)"]["cost_usd"]
        < p["fixed 6 (peak-sized)"]["cost_usd"] / 2
    )
    assert (
        p["scheduled floor + target tracking"]["slo_violation_pct"]
        < p["target tracking (6 rps/instance)"]["slo_violation_pct"]
    )
    assert (
        p["fixed 12 (burst-proof)"]["cost_usd"]
        > p["scheduled floor + target tracking"]["cost_usd"] * 3
    )
