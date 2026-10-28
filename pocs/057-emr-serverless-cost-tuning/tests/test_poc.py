import shutil
import tempfile
from pathlib import Path

import pytest
from emr_serverless_cost_tuning.cost import (
    ARM_DISCOUNT,
    GB_HOUR,
    MIN_BILLED_S,
    STARTUP_S,
    VCPU_HOUR,
    Worker,
    cheapest,
    check_plan,
    idle_cost_per_month,
    job_cost,
)
from emr_serverless_cost_tuning.experiments import job, make_data, session, timed
from emr_serverless_cost_tuning.main import price, terraform_plan


def test_worker_price_arithmetic():
    w = Worker(vcpu=4, memory_gb=16, arm=False)
    assert w.hourly == pytest.approx(4 * VCPU_HOUR + 16 * GB_HOUR)
    assert Worker(arm=True).hourly == pytest.approx(Worker(arm=False).hourly * ARM_DISCOUNT)
    assert Worker(disk_gb=120, arm=False).hourly > Worker(disk_gb=20, arm=False).hourly


def test_minimum_billing_dominates_short_jobs():
    tiny, small = job_cost(2.0, 1), job_cost(10.0, 1)
    assert (
        tiny["billed_s"] == small["billed_s"] == MIN_BILLED_S
        and tiny["cost_usd"] == small["cost_usd"]
    )
    long = job_cost(600.0, 1)
    assert long["billed_s"] == 600 + STARTUP_S and long["cost_usd"] > tiny["cost_usd"] * 9
    assert job_cost(600.0, 1, preinit=True)["billed_s"] == 600


def test_cost_scales_with_workers_and_vcpu_seconds():
    assert job_cost(300, 4)["cost_usd"] == pytest.approx(4 * job_cost(300, 1)["cost_usd"], rel=1e-3)
    assert job_cost(300, 2)["vcpu_seconds"] == pytest.approx(2 * 4 * (300 + STARTUP_S))


def test_cheapest_prefers_fewer_workers_when_speedup_is_sublinear():
    ms = [
        {"runtime_s": 1000.0, "workers": 1},
        {"runtime_s": 700.0, "workers": 2},
        {"runtime_s": 600.0, "workers": 4},
    ]
    assert cheapest(ms)["workers"] == 1
    ms2 = [{"runtime_s": 1000.0, "workers": 1}, {"runtime_s": 480.0, "workers": 2}]
    assert cheapest(ms2)["workers"] == 2  # super-linear speed-up pays for the extra worker


def test_idle_cost_of_warm_capacity():
    assert (
        idle_cost_per_month(0) == 0
        and idle_cost_per_month(2) == pytest.approx(2 * idle_cost_per_month(1), rel=1e-6)
        and idle_cost_per_month(1) > 50
    )


def plan(**app):
    base = {
        "architecture": "ARM64",
        "auto_stop_configuration": [{"enabled": True, "idle_timeout_minutes": 10}],
        "maximum_capacity": [{"cpu": "64 vCPU"}],
        "initial_capacity": [],
        "tags": {"cost-centre": "x"},
    }
    base.update(app)
    return {
        "resource_changes": [
            {
                "type": "aws_emrserverless_application",
                "change": {"actions": ["create"], "after": base},
            }
        ]
    }


def test_plan_checks_on_handmade_plans():
    assert check_plan(plan()) == []
    assert any(
        "auto-stop is disabled" in m
        for m in check_plan(plan(auto_stop_configuration=[{"enabled": False}]))
    )
    assert any(
        "idle timeout 60" in m
        for m in check_plan(
            plan(auto_stop_configuration=[{"enabled": True, "idle_timeout_minutes": 60}])
        )
    )
    assert any("no maximum capacity" in m for m in check_plan(plan(maximum_capacity=[])))
    assert any(
        "exceeds the 128 vCPU" in m
        for m in check_plan(plan(maximum_capacity=[{"cpu": "512 vCPU"}]))
    )
    assert any("x86_64" in m for m in check_plan(plan(architecture="X86_64")))
    assert any(
        "pre-initialised" in m
        for m in check_plan(
            plan(initial_capacity=[{"initial_capacity_config": [{"worker_count": 5}]}])
        )
    )
    assert any("cost-centre" in m for m in check_plan(plan(tags={})))
    assert check_plan({"resource_changes": []}) == ["no EMR Serverless application in the plan"]


needs_tf = pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform required")


@needs_tf
def test_terraform_default_plan_is_clean_and_variants_are_flagged():
    assert check_plan(terraform_plan()) == []
    assert any("exceeds" in m for m in check_plan(terraform_plan(["-var", "max_vcpu=512"])))
    assert any("x86_64" in m for m in check_plan(terraform_plan(["-var", "architecture=X86_64"])))
    assert any(
        "pre-initialised" in m for m in check_plan(terraform_plan(["-var", "preinit_executors=3"]))
    )


@pytest.fixture(scope="module")
def data():
    with tempfile.TemporaryDirectory() as t:
        yield make_data(Path(t), 40_000, small_files=20)


def test_every_configuration_returns_the_same_result(data):
    digests, tasks = set(), {}
    for label, cores, sp, aqe in [
        ("sp1", 2, 1, False),
        ("sp8", 2, 8, False),
        ("sp64", 2, 64, False),
        ("aqe", 2, 64, True),
    ]:
        spark = session(cores, sp, aqe, memory="1g")
        m = timed(spark, data["one_file"], data["customers"], repeats=1)
        digests.add(m["digest"])
        tasks[label] = m["tasks"]
    assert len(digests) == 1  # tuning must never change answers
    assert (
        tasks["sp64"] > tasks["sp8"] > tasks["sp1"] and tasks["aqe"] < tasks["sp64"]
    )  # AQE coalesces the 64 shuffle partitions


def test_small_files_create_more_tasks_than_one_file(data):
    spark = session(2, 8, False, memory="1g")
    one = job(spark, data["one_file"], data["customers"])
    many = job(spark, data["small_files"], data["customers"])
    assert one[0] == many[0] and many[1] > one[1]


def test_pricing_attaches_costs_and_projection():
    fake = {
        "experiments": [
            {"group": "g", "config": "c", "workers_vcpu": 4, "runtime_s": 3.0, "tasks": 10}
        ],
        "n_orders": 1,
    }
    p = price(fake)["experiments"][0]
    assert (
        p["workers_billed"] == 1
        and p["cost_small_usd"] == job_cost(3.0, 1)["cost_usd"]
        and p["projected_runtime_s"] == 600.0
        and p["projected_cost_usd"] > p["cost_small_usd"] * 9
    )
