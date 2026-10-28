import numpy as np
import pytest
from batch_inference_scheduler.sched import (
    CAPACITY,
    HORIZON,
    ON_DEMAND,
    Job,
    lp_lower_bound,
    make_jobs,
    simulate,
    spot_prices,
)

POLICIES = ["fifo_on_demand", "edf_on_demand", "fifo_spot", "edf_cost_aware"]


def test_prices_are_bounded_and_diurnal():
    p = spot_prices(np.random.default_rng(1))
    assert len(p) == HORIZON and p.min() >= 0.3 and p.max() <= ON_DEMAND * 0.95
    night = np.mean([p[h] for h in range(48) if h % 24 in (22, 23, 0, 1, 2)])
    day = np.mean([p[h] for h in range(48) if h % 24 in (10, 11, 12, 13, 14)])
    assert night < day


def test_lp_picks_the_cheapest_hours_on_a_hand_solved_case():
    price = np.array([5.0, 1.0, 1.0, 5.0] + [5.0] * (HORIZON - 4))
    job = Job(0, work=4, par=2, release=0, deadline=4)
    assert lp_lower_bound([job], price) == pytest.approx(
        2 * 1.0 + 2 * 1.0 + 0 * 5
    )  # 2 slots in hour 1 and 2 only
    tight = Job(0, work=8, par=2, release=0, deadline=4)  # needs all four hours at full parallelism
    assert lp_lower_bound([tight], price) == pytest.approx(2 * (5 + 1 + 1 + 5))


def test_lp_respects_capacity_and_reports_infeasible():
    price = np.ones(HORIZON)
    jobs = [
        Job(i, work=CAPACITY, par=CAPACITY, release=0, deadline=1) for i in range(2)
    ]  # 2 x capacity in one hour
    assert lp_lower_bound(jobs, price) == float("inf")
    assert lp_lower_bound(jobs[:1], price) == pytest.approx(CAPACITY)


def test_generated_jobs_are_feasible_on_their_own():
    for seed in range(20):
        for j in make_jobs(np.random.default_rng(seed)):
            assert j.deadline > j.release and -(-j.work // j.par) <= j.deadline - j.release


@pytest.mark.parametrize("policy", POLICIES)
def test_capacity_is_never_exceeded_and_runs_are_reproducible(policy):
    rng = np.random.default_rng(3)
    price, jobs = spot_prices(rng), make_jobs(rng)
    a = simulate(policy, jobs, price, np.random.default_rng(9))
    assert a["peak_slots"] <= CAPACITY
    assert a == simulate(policy, jobs, price, np.random.default_rng(9))


def test_on_demand_never_loses_work_and_edf_meets_feasible_deadlines():
    for seed in range(15):
        rng = np.random.default_rng(seed)
        price, jobs = spot_prices(rng), make_jobs(rng)
        r = simulate("edf_on_demand", jobs, price, np.random.default_rng(0), q_interrupt=0.9)
        assert r["lost_slot_hours"] == 0
        if np.isfinite(lp_lower_bound(jobs, price)):
            assert r["missed"] == 0


def test_cost_floor_the_lp_is_a_true_lower_bound_on_spot_only_cost_without_interruptions():
    for seed in range(15):
        rng = np.random.default_rng(seed)
        price, jobs = spot_prices(rng), make_jobs(rng)
        lb = lp_lower_bound(jobs, price)
        if np.isfinite(lb):
            r = simulate("fifo_spot", jobs, price, np.random.default_rng(0), q_interrupt=0.0)
            assert r["cost"] >= lb - 1e-6


def test_cost_aware_beats_on_demand_on_cost_and_beats_plain_spot_on_deadlines():
    cost_ca, cost_od, miss_ca, miss_spot = [], [], [], []
    for seed in range(25):
        rng = np.random.default_rng(seed)
        price, jobs = spot_prices(rng), make_jobs(rng)
        if not np.isfinite(lp_lower_bound(jobs, price)):
            continue
        ca = simulate("edf_cost_aware", jobs, price, np.random.default_rng(100 + seed))
        od = simulate("edf_on_demand", jobs, price, np.random.default_rng(100 + seed))
        sp = simulate("fifo_spot", jobs, price, np.random.default_rng(100 + seed))
        (
            cost_ca.append(ca["cost"]),
            cost_od.append(od["cost"]),
            miss_ca.append(ca["missed"]),
            miss_spot.append(sp["missed"]),
        )
    assert np.mean(cost_ca) < 0.75 * np.mean(cost_od)
    assert np.mean(miss_ca) < np.mean(miss_spot)


def test_no_interruptions_means_no_lost_work():
    rng = np.random.default_rng(4)
    price, jobs = spot_prices(rng), make_jobs(rng)
    assert (
        simulate("edf_cost_aware", jobs, price, np.random.default_rng(0), q_interrupt=0.0)[
            "lost_slot_hours"
        ]
        == 0
    )
