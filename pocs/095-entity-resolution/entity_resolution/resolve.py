"""Probabilistic record linkage with Splink (DuckDB backend) and two simple baselines."""

from __future__ import annotations

import time

import pandas as pd
import splink.comparison_library as cl
from splink import DuckDBAPI, Linker, SettingsCreator, block_on

from .data import true_pairs


def settings() -> SettingsCreator:
    return SettingsCreator(
        link_type="dedupe_only",
        comparisons=[
            cl.NameComparison("first_name"),
            cl.NameComparison("surname"),
            cl.DateOfBirthComparison("dob", input_is_string=True),
            cl.PostcodeComparison("postcode"),
            cl.LevenshteinAtThresholds("city", [1]),
        ],
        blocking_rules_to_generate_predictions=[
            block_on("surname"),
            block_on("dob"),
            block_on("first_name", "postcode"),
            block_on("postcode"),
        ],
    )


def run_splink(df: pd.DataFrame, threshold: float = 0.5, seed: int = 1) -> dict:
    cols = ["unique_id", "first_name", "surname", "dob", "postcode", "city"]
    t0 = time.perf_counter()
    db = DuckDBAPI()
    linker = Linker(db.register(df[cols].copy()), settings())
    linker.training.estimate_probability_two_random_records_match(
        [block_on("first_name", "surname", "dob")], recall=0.7
    )
    linker.training.estimate_u_using_random_sampling(max_pairs=2_000_000, seed=seed)
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("dob"))
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("surname"))
    pred = linker.inference.predict(threshold_match_probability=0.01).as_pandas_dataframe()
    train_s = time.perf_counter() - t0
    return {
        "pred": pred[["unique_id_l", "unique_id_r", "match_probability"]],
        "seconds": round(train_s, 1),
        "n_scored_pairs": len(pred),
        "linker": linker,
    }


def pair_metrics(pairs: set[tuple[int, int]], truth: set[tuple[int, int]]) -> dict:
    tp = len(pairs & truth)
    p = tp / len(pairs) if pairs else 0.0
    r = tp / len(truth) if truth else 0.0
    return {
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
        "pairs": len(pairs),
    }


def pairs_at(pred: pd.DataFrame, threshold: float) -> set[tuple[int, int]]:
    s = pred[pred.match_probability >= threshold]
    return {(min(a, b), max(a, b)) for a, b in zip(s.unique_id_l, s.unique_id_r, strict=True)}


def clusters_from_pairs(ids, pairs: set[tuple[int, int]]) -> dict[int, int]:
    """Connected components by union-find; returns record id -> cluster id."""
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {i: find(i) for i in ids}


def bcubed(df: pd.DataFrame, clusters: dict[int, int]) -> dict:
    d = df.assign(cl=df["unique_id"].map(clusters))
    prec = rec = 0.0
    ent_sizes = d.groupby("entity").size()
    cl_sizes = d.groupby("cl").size()
    both = d.groupby(["entity", "cl"]).size()
    for (e, c), n in both.items():
        prec += n * n / cl_sizes[c]
        rec += n * n / ent_sizes[e]
    p, r = prec / len(d), rec / len(d)
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3)}


def baseline_exact(df: pd.DataFrame) -> set[tuple[int, int]]:
    """Exact match on lower-cased first name, surname and date of birth."""
    d = df.dropna(subset=["dob"]).assign(
        k=lambda x: x.first_name.str.lower() + "|" + x.surname.str.lower() + "|" + x.dob
    )
    out = set()
    for _, g in d.groupby("k"):
        ids = sorted(g.unique_id)
        out |= {(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]}
    return out


def baseline_fuzzy(df: pd.DataFrame) -> set[tuple[int, int]]:
    """Same surname initial block, then name edit distance <= 1 on both names and equal dob or postcode."""
    import itertools

    def lev1(a, b):
        if a is None or b is None:
            return False
        if a == b:
            return True
        if abs(len(a) - len(b)) > 1:
            return False
        if len(a) == len(b):
            return sum(x != y for x, y in zip(a, b, strict=True)) <= 1 or (
                sum(x != y for x, y in zip(a, b, strict=True)) == 2 and sorted(a) == sorted(b)
            )
        s, lg = (a, b) if len(a) < len(b) else (b, a)
        return any(lg[:i] + lg[i + 1 :] == s for i in range(len(lg)))

    out = set()
    d = df.assign(b=df.surname.str[:1].str.lower())
    for _, g in d.groupby("b"):
        rows = g.to_dict("records")
        for x, y in itertools.combinations(rows, 2):
            if (
                lev1(x["first_name"], y["first_name"])
                and lev1(x["surname"], y["surname"])
                and (
                    (x["dob"] and x["dob"] == y["dob"])
                    or (x["postcode"] and x["postcode"] == y["postcode"])
                )
            ):
                out.add((min(x["unique_id"], y["unique_id"]), max(x["unique_id"], y["unique_id"])))
    return out


def evaluate(df: pd.DataFrame, thresholds=(0.5, 0.8, 0.95, 0.99)) -> dict:
    truth = true_pairs(df)
    res = run_splink(df)
    out: dict = {"n_records": len(df), "n_entities": int(df.entity.nunique()), "true_pairs": len(truth), "splink_seconds": res["seconds"], "splink_scored_pairs": res["n_scored_pairs"], "all_possible_pairs": len(df) * (len(df) - 1) // 2,
                 "blocking_pair_recall": round(len({(min(a, b), max(a, b)) for a, b in zip(res["pred"].unique_id_l, res["pred"].unique_id_r, strict=True)} & truth) / len(truth), 3), "methods": {}}  # fmt: skip
    for th in thresholds:
        pairs = pairs_at(res["pred"], th)
        cl = clusters_from_pairs(df.unique_id, pairs)
        out["methods"][f"Splink @ {th}"] = {
            **pair_metrics(pairs, truth),
            "bcubed_f1": bcubed(df, cl)["f1"],
            "clusters": len(set(cl.values())),
        }
    for name, fn in (("exact name+dob", baseline_exact), ("fuzzy rules", baseline_fuzzy)):
        pairs = fn(df)
        cl = clusters_from_pairs(df.unique_id, pairs)
        out["methods"][name] = {
            **pair_metrics(pairs, truth),
            "bcubed_f1": bcubed(df, cl)["f1"],
            "clusters": len(set(cl.values())),
        }
    return out


def scaling(sizes=(800, 1600, 3200)) -> list[dict]:
    """Records vs candidate pairs and time: blocking should keep the number of scored pairs far below n^2."""
    from .data import make

    out = []
    for n in sizes:
        df = make(n, seed=9)
        res = run_splink(df)
        out.append(
            {
                "records": len(df),
                "possible_pairs": len(df) * (len(df) - 1) // 2,
                "scored_pairs": res["n_scored_pairs"],
                "seconds": res["seconds"],
            }
        )
    return out
