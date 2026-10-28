import pandas as pd
from entity_resolution.data import make, true_pairs
from entity_resolution.resolve import (
    baseline_exact,
    baseline_fuzzy,
    bcubed,
    clusters_from_pairs,
    evaluate,
    pair_metrics,
    pairs_at,
    run_splink,
    scaling,
)

DF = make(1500, seed=2)
R = evaluate(DF)
M = R["methods"]


def test_synthetic_data_is_deterministic_and_truth_matches_entity_sizes():
    assert make(300, seed=1).equals(make(300, seed=1))
    sizes = DF.groupby("entity").size()
    assert len(true_pairs(DF)) == int((sizes * (sizes - 1) // 2).sum()) == R["true_pairs"]
    assert (sizes > 1).mean() > 0.25 and DF.unique_id.is_unique


def test_duplicates_are_noisy_but_originals_are_clean():
    first = DF.sort_values("unique_id").groupby("entity").first()
    assert DF.isna().any().any() and first.shape[0] == R["n_entities"]


def test_splink_beats_both_baselines_on_f1_and_keeps_precision_high():
    s = M["Splink @ 0.5"]
    assert s["f1"] > M["fuzzy rules"]["f1"] > M["exact name+dob"]["f1"]
    assert s["precision"] >= 0.98 and s["recall"] > M["exact name+dob"]["recall"] + 0.3


def test_raising_the_threshold_trades_recall_for_precision():
    ths = [0.5, 0.8, 0.95, 0.99]
    rec = [M[f"Splink @ {t}"]["recall"] for t in ths]
    pre = [M[f"Splink @ {t}"]["precision"] for t in ths]
    assert rec == sorted(rec, reverse=True) and pre == sorted(pre)


def test_blocking_cuts_candidate_pairs_by_orders_of_magnitude_and_bounds_recall():
    assert R["splink_scored_pairs"] * 500 < R["all_possible_pairs"]
    assert (
        M["Splink @ 0.5"]["recall"] <= R["blocking_pair_recall"] + 1e-9
        and R["blocking_pair_recall"] > 0.8
    )


def test_bcubed_scores_the_resulting_entities_and_is_at_least_pair_f1_here():
    assert M["Splink @ 0.5"]["bcubed_f1"] >= M["Splink @ 0.5"]["f1"]


def test_pair_metrics_and_bcubed_on_toy_cases():
    truth = {(0, 1), (2, 3)}
    assert pair_metrics({(0, 1), (0, 2)}, truth) == {
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
        "pairs": 2,
    }
    assert pair_metrics(set(), truth)["f1"] == 0.0
    df = pd.DataFrame({"unique_id": [0, 1, 2, 3], "entity": [0, 0, 1, 1]})
    assert bcubed(df, {0: 0, 1: 0, 2: 2, 3: 2})["f1"] == 1.0
    assert bcubed(df, {0: 0, 1: 0, 2: 0, 3: 0}) == {
        "precision": 0.5,
        "recall": 1.0,
        "f1": 0.667,
    }  # everything merged
    assert bcubed(df, {0: 0, 1: 1, 2: 2, 3: 3})["recall"] == 0.5  # nothing merged


def test_connected_components_chain_merges_people_who_were_never_linked_directly():
    cl = clusters_from_pairs([0, 1, 2, 3], {(0, 1), (1, 2)})
    assert (
        cl[0] == cl[1] == cl[2] != cl[3]
    )  # 0 and 2 never matched each other, but transitivity merges them


def test_exact_baseline_needs_complete_equal_keys():
    df = pd.DataFrame(
        {
            "unique_id": [0, 1, 2, 3],
            "first_name": ["Ana", "ana", "Ana", "Ben"],
            "surname": ["Lee", "LEE", "Lee", "Lee"],
            "dob": ["1990-01-01", "1990-01-01", None, "1990-01-01"],
            "postcode": [None] * 4,
        }
    )
    assert baseline_exact(df) == {(0, 1)}


def test_fuzzy_baseline_accepts_one_typo_and_a_swap_but_not_two_edits():
    df = pd.DataFrame(
        {
            "unique_id": [0, 1, 2],
            "first_name": ["Anna", "Ana", "Anxx"],
            "surname": ["Smith", "Smith", "Smith"],
            "dob": ["2000-01-01"] * 3,
            "postcode": [None] * 3,
        }
    )
    assert baseline_fuzzy(df) == {(0, 1)}


def test_splink_run_is_deterministic():
    a = pairs_at(run_splink(make(600, seed=3))["pred"], 0.5)
    b = pairs_at(run_splink(make(600, seed=3))["pred"], 0.5)
    assert a == b


def test_missing_values_do_not_crash_the_linker():
    d = make(300, seed=4)
    d.loc[:, "city"] = None
    d.loc[::3, "dob"] = None
    assert len(run_splink(d)["pred"]) > 0


def test_candidate_pairs_grow_far_slower_than_all_pairs():
    s = scaling((500, 1000))
    assert s[1]["scored_pairs"] / s[0]["scored_pairs"] < 0.8 * (
        s[1]["possible_pairs"] / s[0]["possible_pairs"]
    )
    assert s[1]["scored_pairs"] * 100 < s[1]["possible_pairs"]
