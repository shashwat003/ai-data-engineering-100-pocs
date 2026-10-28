"""POC 094: classical sentiment baselines vs an optional local SLM, with accuracy, hard-case accuracy, speed and cost."""

from __future__ import annotations

import random
import time
from pathlib import Path

from sklearn.metrics import accuracy_score, f1_score

from llm_client import LLMError, OllamaClient
from pocs_common import write_json
from pocs_common.report import markdown_table

from .data import generate
from .models import PromptSlm, classical_models

POC_DIR = Path(__file__).resolve().parent.parent
# MODELLED cost assumptions (not measured): a small hosted model priced per 1k tokens, ~60 prompt tokens per review.
ASSUMED_USD_PER_1K_TOKENS = 0.0005
TOKENS_PER_REVIEW = 70


def boot_ci(y, p, hard, n=300, seed=1):
    rng = random.Random(seed)
    idx = list(range(len(y)))
    accs = []
    for _ in range(n):
        s = [rng.choice(idx) for _ in idx]
        accs.append(sum(y[i] == p[i] for i in s) / len(s))
    accs.sort()
    return accs[int(0.025 * n)], accs[int(0.975 * n)]


def score(model, test, name):
    texts, y = [r["text"] for r in test], [r["label"] for r in test]
    t0 = time.perf_counter()
    p = model.predict(texts)
    dt = time.perf_counter() - t0
    hard = [i for i, r in enumerate(test) if r["hard"]]
    easy = [i for i, r in enumerate(test) if not r["hard"]]
    acc = lambda ix: accuracy_score([y[i] for i in ix], [p[i] for i in ix])  # noqa: E731
    lo, hi = boot_ci(y, p, None)
    return {"model": name, "accuracy": round(accuracy_score(y, p), 3), "ci95": [round(lo, 3), round(hi, 3)], "macro_f1": round(f1_score(y, p, average="macro"), 3),
            "acc_easy": round(acc(easy), 3), "acc_hard": round(acc(hard), 3), "ms_per_1k": round(dt / len(texts) * 1e6, 1), "n": len(test)}  # fmt: skip


def run(n_train: int = 1500, n_test: int = 600, slm_model: str = "llama3.2:1b") -> dict:
    train, test = generate(n_train, 1), generate(n_test, 2)
    rows = []
    for name, m in classical_models().items():
        if hasattr(m, "fit"):
            m.fit([r["text"] for r in train], [r["label"] for r in train])
        rows.append(score(m, test, name))
    slm: dict = {"status": "NOT RUN", "reason": ""}
    try:
        client = OllamaClient(timeout=3)
        client.generate("ping", model=slm_model, max_tokens=1)
        s = PromptSlm(client, slm_model)
        row = score(s, test[:100], f"slm:{slm_model} (first 100)")
        slm = {"status": "RAN", "row": row, "unparsable": s.unparsable}
    except (LLMError, OSError) as e:
        slm["reason"] = f"no local Ollama reachable ({type(e).__name__}); SLM arm unverified"
    usd_per_1k = ASSUMED_USD_PER_1K_TOKENS * TOKENS_PER_REVIEW
    return {"rows": rows, "slm": slm, "modelled_slm_usd_per_1k_reviews": round(usd_per_1k, 3),
            "classical_cpu_usd_per_1k_reviews": "~0 (CPU time above)", "note": "Cost of the SLM arm is MODELLED from assumed token price; classical times are measured."}  # fmt: skip


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = ["# Results (measured by `python -m sentiment_slm_vs_classical.main`; synthetic reviews, 5% label noise)", "",
          markdown_table(["model", "accuracy", "95% CI", "macro-F1", "acc easy", "acc hard", "ms / 1k reviews"], [[x["model"], x["accuracy"], f"{x['ci95'][0]}-{x['ci95'][1]}", x["macro_f1"], x["acc_easy"], x["acc_hard"], x["ms_per_1k"]] for x in r["rows"]]),
          "", f"- SLM arm: **{r['slm']['status']}** {r['slm'].get('reason', '')}", f"- Modelled SLM cost: ${r['modelled_slm_usd_per_1k_reviews']} per 1k reviews (assumed ${ASSUMED_USD_PER_1K_TOKENS}/1k tokens x {TOKENS_PER_REVIEW} tokens); classical models cost ~nothing at inference."]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
