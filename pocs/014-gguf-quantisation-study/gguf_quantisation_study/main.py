"""POC 014: how many bits can a language model's weights lose? Block-quantisation formats vs perplexity and size."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .model import evaluate, model_size_gb, quantised_copy, train
from .quant import FORMATS, bits_per_weight, dequantise, quantise, sqnr_db

POC_DIR = Path(__file__).resolve().parent.parent


def weight_distribution_study(seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    dists = {
        "gaussian": rng.normal(0, 0.05, 200_000),
        "laplace (heavy tail)": rng.laplace(0, 0.03, 200_000),
    }
    out = rng.normal(0, 0.05, 200_000)
    out[rng.choice(len(out), 400, replace=False)] *= 25  # 0.2% outlier weights
    dists["gaussian + 0.2% outliers (x25)"] = out
    return {
        d: {f: round(sqnr_db(w, dequantise(quantise(w, f))), 1) for f in FORMATS}
        for d, w in dists.items()
    }


def run(seed: int = 0) -> dict:
    m, _, (xte, yte) = train(seed)
    base = evaluate(m, xte, yte)
    res: dict = {
        "params": int(sum(w.size for w in m.coefs_)),
        "fp32": base,
        "weight_sqnr_db": weight_distribution_study(seed),
        "uniform": {},
        "per_layer_q4_0": {},
        "mixed": {},
    }
    n_layers = len(m.coefs_)
    for fmt in FORMATS:
        q, nbytes, nw = quantised_copy(m, [fmt] * n_layers)
        e = evaluate(q, xte, yte)
        res["uniform"][fmt] = {
            **e,
            "bits_per_weight": round(bits_per_weight(fmt), 2),
            "bytes": nbytes,
            "perplexity_vs_fp32_pct": round((e["perplexity"] / base["perplexity"] - 1) * 100, 2),
        }
    for i in range(n_layers):
        plan = ["F32"] * n_layers
        plan[i] = "Q4_0"
        q, _, _ = quantised_copy(m, plan)
        res["per_layer_q4_0"][f"layer {i}"] = evaluate(q, xte, yte)["perplexity"]
    for name, plan in {
        "uniform Q4_0": ["Q4_0"] * n_layers,
        "output layer Q8_0, rest Q4_0": ["Q4_0"] * (n_layers - 1) + ["Q8_0"],
        "uniform Q8_0": ["Q8_0"] * n_layers,
    }.items():
        q, nbytes, nw = quantised_copy(m, plan)
        res["mixed"][name] = {
            **evaluate(q, xte, yte),
            "bytes": nbytes,
            "effective_bits_per_weight": round(nbytes * 8 / nw, 2),
        }
    res["modelled_sizes_gb"] = {
        f"{p / 1e9:g}B params": {
            f: round(model_size_gb(p, f), 2) for f in ("F16", "Q8_0", "Q5_0", "Q4_0", "Q4_1", "Q2S")
        }
        for p in (7e9, 13e9)
    }
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        "# Results (measured by `python -m gguf_quantisation_study.main`; llama.cpp-style block formats implemented in NumPy on a small char-level MLP language model, NOT an LLM)",
        "",
        f"Model: {r['params']:,} weights; FP32 held-out perplexity {r['fp32']['perplexity']}, accuracy {r['fp32']['accuracy']}.",
        "",
        "## Uniform quantisation of all weight matrices",
        "",
        markdown_table(
            ["format", "bits/weight", "perplexity", "vs FP32", "accuracy"],
            [
                [
                    f,
                    v["bits_per_weight"],
                    v["perplexity"],
                    f"{v['perplexity_vs_fp32_pct']:+.2f}%",
                    v["accuracy"],
                ]
                for f, v in r["uniform"].items()
            ],
        ),
        "",
        "## Weight reconstruction SQNR (dB, higher is better)",
        "",
        markdown_table(
            ["weights", *FORMATS],
            [[d, *[s[f] for f in FORMATS]] for d, s in r["weight_sqnr_db"].items()],
        ),
        "",
        "## Sensitivity: only one layer at Q4_0 (perplexity)",
        "",
    ] + [f"- {k}: {v}" for k, v in r["per_layer_q4_0"].items()]
    md += ["", "## Mixed precision", "", markdown_table(["plan", "effective bits/weight", "perplexity", "accuracy"], [[k, v["effective_bits_per_weight"], v["perplexity"], v["accuracy"]] for k, v in r["mixed"].items()]), "", "## Modelled file sizes (GB) for hypothetical models, from bits per weight only", "",
           markdown_table(["model", "F16", "Q8_0", "Q5_0", "Q4_0", "Q4_1", "Q2S"], [[k, *v.values()] for k, v in r["modelled_sizes_gb"].items()])]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
