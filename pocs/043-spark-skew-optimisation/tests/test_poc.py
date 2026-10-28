import pytest

pytest.importorskip(
    "pyspark", reason="install requirements-spark.txt (and Java 17+) to run POC 043"
)

from pyspark.sql import functions as F  # noqa: E402
from spark_skew_optimisation import bench, joins, skew  # noqa: E402
from spark_skew_optimisation.datagen import generate  # noqa: E402
from spark_skew_optimisation.main import KEY, build, result_digest  # noqa: E402
from spark_skew_optimisation.session import get_spark  # noqa: E402

P = 8  # shuffle partitions for the small test data


@pytest.fixture(scope="module")
def spark():
    s = get_spark(cores=2, memory="1g", shuffle_partitions=P)
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


@pytest.fixture(scope="module")
def data(spark, tmp_path_factory):
    d = tmp_path_factory.mktemp("skew")
    manifest = generate(spark, d, events=200_000, customers=5_000, hot_share=0.5, seed=7)
    return (
        d,
        manifest,
        spark.read.parquet(str(d / "events")),
        spark.read.parquet(str(d / "customers")),
    )


# ---- pure-Python statistics ---------------------------------------------------------------------


def test_stats_from_counts_known_values():
    s = skew.stats_from_counts([100, 10, 10, 10], 4)
    assert (s.rows, s.max, s.median, s.mean) == (130, 100, 10, 32.5)
    assert s.max_over_median == 10 and round(s.largest_share, 3) == 0.769


def test_empty_partitions_count_towards_skew():
    s = skew.stats_from_counts([100], 10)  # 9 idle partitions
    assert s.partitions == 10 and s.median == 0 and s.max_over_median == float("inf")
    uniform = skew.stats_from_counts([10] * 10)
    assert uniform.cv == 0 and uniform.max_over_median == 1


def test_choose_salts_is_bounded():
    assert joins.choose_salts(0.5, 32) == 16
    assert joins.choose_salts(0.001, 32) == 2
    assert joins.choose_salts(1.0, 32) == 32
    assert joins.dim_rows_after_salting(1000, 1, 16) == 1015


# ---- data and diagnosis ---------------------------------------------------------------------------


def test_generated_data_matches_the_requested_skew(data):
    _, m, _, _ = data
    assert m["events"] == 200_000 and m["customers"] == 5_000
    assert abs(m["hot_key_share"] - 0.5) < 0.01


def test_generation_is_deterministic(spark, tmp_path):
    a = generate(spark, tmp_path / "a", events=20_000, customers=500, seed=3)
    b = generate(spark, tmp_path / "b", events=20_000, customers=500, seed=3)
    assert a == b


def test_skew_is_measured_before_it_is_fixed(data):
    _, _, ev, _ = data
    s = skew.shuffle_partition_stats(ev, P, KEY)
    assert s.largest_share >= 0.5 and s.max_over_median >= 5
    hitters = skew.heavy_hitters(ev, KEY, partitions=P)
    assert [h["key"] for h in hitters] == [0] and 0.45 < hitters[0]["share"] < 0.55


def test_balanced_keys_are_not_flagged(spark):
    even = spark.range(100_000).select((F.col("id") % 1000).alias(KEY))
    assert skew.heavy_hitters(even, KEY, partitions=P) == []
    assert skew.shuffle_partition_stats(even, P, KEY).max_over_median < 1.5


# ---- every strategy computes the same answer --------------------------------------------------------


def run(spark, data, variant, hot_keys=(0,), n_salt=4):
    d, _, _, _ = data
    joins.configure(
        spark, variant, P, skew_bytes=32 * 1024
    )  # tiny thresholds: exercise AQE splitting
    ev, dim = spark.read.parquet(str(d / "events")), spark.read.parquet(str(d / "customers"))
    rows = joins.workload(build(spark, ev, dim, variant, n_salt, list(hot_keys))).collect()
    return rows


def test_all_variants_return_identical_results(spark, data):
    digests = {
        v: result_digest(run(spark, data, v))
        for v in ("baseline", "aqe", "broadcast", "salt_hot", "salt_all")
    }
    assert len(set(digests.values())) == 1, digests


def test_result_is_correct_against_a_plain_reference(spark, data):
    _, m, _, _ = data
    rows = run(spark, data, "salt_hot")
    assert sum(r["events"] for r in rows) == m["events"]  # every event matches exactly one customer


def test_events_without_a_matching_customer_are_dropped_like_an_inner_join(spark):
    joins.configure(spark, "baseline", P)
    ev = spark.createDataFrame(
        [(i, 0 if i % 2 else 99, f"p{i}", 1.0) for i in range(100)],
        "event_id long, customer_id long, payload string, amount double",
    )
    dim = spark.createDataFrame(
        [(0, "consumer", "US")], "customer_id long, segment string, country string"
    )
    for variant in ("baseline", "broadcast", "salt_hot", "salt_all"):
        out = build(spark, ev, dim, variant, 4, [0, 99])
        assert out.count() == 50, variant


def test_salting_with_no_hot_keys_equals_the_plain_join(spark, data):
    d, _, ev, dim = data
    plain = joins.join_baseline(ev, dim, KEY).count()
    assert joins.join_salted(ev, dim, KEY, n_salt=4, hot_keys=[], spark=spark).count() == plain
    assert joins.join_salted(ev, dim, KEY, n_salt=1, hot_keys=[0], spark=spark).count() == plain


def test_selective_salting_replicates_only_hot_dimension_rows(spark, data):
    _, _, ev, dim = data
    n_salt = 6
    hot = F.col(KEY) == 0
    salts = spark.range(n_salt)
    replicated = dim.filter(hot).crossJoin(salts).count() + dim.filter(~hot).count()
    assert replicated == joins.dim_rows_after_salting(dim.count(), 1, n_salt)
    assert replicated < dim.count() * n_salt  # salting everything would cost far more


# ---- does the fix actually balance the shuffle? ------------------------------------------------------


def test_salting_flattens_the_shuffle_partitions(spark, data):
    _, _, ev, _ = data
    before = skew.shuffle_partition_stats(ev, P, KEY)
    salted = ev.withColumn(
        "_salt", F.when(F.col(KEY) == 0, F.pmod(F.hash("event_id"), F.lit(8))).otherwise(F.lit(0))
    )
    after = skew.shuffle_partition_stats(salted, P, KEY, "_salt")
    assert after.largest_share < before.largest_share / 2
    assert after.max_over_median < before.max_over_median / 2


def test_aqe_splits_the_skewed_partition(spark, data):
    _, _, ev, dim = data

    def final_plan():
        df = joins.join_baseline(ev, dim, KEY).groupBy("country").agg(F.sum(F.length("payload")))
        df.collect()  # the plan is only final after execution
        return df._jdf.queryExecution().executedPlan().toString()

    joins.configure(spark, "aqe", P, skew_bytes=32 * 1024)
    on = final_plan()
    assert "SortMergeJoin(skew=true)" in on and "AQEShuffleRead skewed" in on
    joins.configure(spark, "baseline", P)
    assert "skew=true" not in final_plan()


def test_broadcast_variant_has_no_shuffle_of_the_fact_table(spark, data):
    d, _, ev, dim = data
    joins.configure(spark, "broadcast", P)
    plan = joins.join_broadcast(ev, dim, KEY)._jdf.queryExecution().executedPlan().toString()
    assert "BroadcastHashJoin" in plan and "SortMergeJoin" not in plan


def test_task_stats_are_reported_from_the_ui(spark, data):
    res = bench.time_runs(spark, "unit", lambda: run(spark, data, "baseline"), repeats=2, warmup=0)
    assert res["repeats"] == 2 and len(res["seconds"]) == 2 and res["median_s"] > 0
    if res["tasks"] is not None:  # the UI may be disabled in some environments
        assert (
            res["tasks"]["tasks"] >= 1
            and res["tasks"]["records_max"] >= res["tasks"]["records_median"]
        )
