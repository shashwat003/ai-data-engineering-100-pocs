"""POC 020: speculative decoding is lossless (exact distribution check) and fast only when the draft is good."""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .decoding import autoregressive, expected_tokens_per_call, speculative, speedup
from .lm import BigramDraft, ExactDraft, NoisyCopyDraft, TrigramLM

POC_DIR = Path(__file__).resolve().parent.parent
COST_RATIO = 0.08  # modelled draft cost / target cost per forward pass (MODELLED, not measured)


def exact_joint(target: TrigramLM, n: int) -> dict[tuple[int, ...], float]:
    out = {}
    for seq in itertools.product(range(target.v), repeat=n):
        p = 1.0
        for i in range(n):
            p *= target.probs(list(seq[:i]))[seq[i]]
        out[seq] = p
    return out


def tv_distance(emp: dict, exact: dict) -> float:
    keys = set(emp) | set(exact)
    return 0.5 * sum(abs(emp.get(k, 0.0) - exact.get(k, 0.0)) for k in keys)


def lossless_check(
    target, draft, k: int, n_tokens: int = 2, runs: int = 60_000, seed: int = 0
) -> float:
    """Total-variation distance between the empirical distribution of the first tokens under speculative sampling and the exact target joint."""
    rng = np.random.default_rng(seed)
    counts: dict = {}
    for _ in range(runs):
        toks, _ = speculative(target, draft, n_tokens, k, rng)
        counts[tuple(toks)] = counts.get(tuple(toks), 0) + 1
    return tv_distance({s: c / runs for s, c in counts.items()}, exact_joint(target, n_tokens))


def run(quick: bool = False) -> dict:
    target = TrigramLM(vocab=20 if quick else 30, seed=0)
    drafts = {
        "exact copy": ExactDraft(target),
        "noisy copy (sigma 0.2)": NoisyCopyDraft(target, 0.2),
        "noisy copy (sigma 0.6)": NoisyCopyDraft(target, 0.6),
        "bigram summary": BigramDraft(target, mix=0.0),
        "near-uniform bigram": BigramDraft(target, mix=0.97, noise=0.5),
    }
    res: dict = {
        "vocab": target.v,
        "cost_ratio_modelled": COST_RATIO,
        "lossless_tv": {},
        "sweep": [],
    }
    for name, d in drafts.items():
        res["lossless_tv"][name] = round(
            lossless_check(target, d, k=4, runs=8000 if quick else 40_000), 4
        )
    # sampling noise floor: TV between two independent samples of the TRUE distribution of the same size
    rng = np.random.default_rng(5)
    exact = exact_joint(target, 2)
    keys, probs = list(exact), np.array(list(exact.values()))
    runs = 8000 if quick else 40_000
    emp = np.bincount(rng.choice(len(keys), runs, p=probs), minlength=len(keys)) / runs
    res["tv_noise_floor"] = round(0.5 * float(np.abs(emp - probs).sum()), 4)
    for name, d in drafts.items():
        for k in (1, 2, 4, 8):
            rng = np.random.default_rng(k)
            sts = [speculative(target, d, 400, k, rng)[1] for _ in range(20 if quick else 40)]
            alpha = float(np.mean([s.acceptance for s in sts]))
            tpc = float(np.mean([s.emitted_per_target_call for s in sts]))
            res["sweep"].append(
                {
                    "draft": name,
                    "k": k,
                    "alpha": round(alpha, 3),
                    "tokens_per_target_call": round(tpc, 2),
                    "theory_tokens_per_call": round(expected_tokens_per_call(alpha, k), 2),
                    "modelled_speedup": round(speedup(alpha, k, COST_RATIO), 2),
                }
            )
    # greedy decoding must reproduce the target's greedy output exactly
    rng = np.random.default_rng(0)
    base, _ = autoregressive(target, 80, rng, greedy=True)
    res["greedy_identical_to_target"] = all(
        speculative(target, d, 80, 4, np.random.default_rng(1), greedy=True)[0] == base
        for d in drafts.values()
    )
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = ["# Results (measured by `python -m speculative_decoding_demo.main`; toy trigram target and bigram drafts in NumPy; speed-up is MODELLED from call counts)", "",
          f"Vocabulary {r['vocab']}; draft pass cost assumed {r['cost_ratio_modelled']} of a target pass (modelled).", "", "## Losslessness: TV distance of the first-2-token distribution from the exact target (k=4)", "",
          markdown_table(["draft", "TV distance"], [[k, v] for k, v in r["lossless_tv"].items()]), "", f"Sampling noise floor for the same number of samples: {r['tv_noise_floor']} (a lossless sampler should sit at about this level).", "",
          f"Greedy speculative decoding identical to greedy target decoding for every draft: **{r['greedy_identical_to_target']}**", "", "## Acceptance, tokens per target call and modelled speed-up", "",
          markdown_table(["draft", "k", "acceptance", "tokens / target call", "theory", "modelled speed-up"], [[s["draft"], s["k"], s["alpha"], s["tokens_per_target_call"], s["theory_tokens_per_call"], s["modelled_speedup"]] for s in r["sweep"]])]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
