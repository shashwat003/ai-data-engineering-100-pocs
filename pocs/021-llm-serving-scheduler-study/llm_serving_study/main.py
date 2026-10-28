"""POC 021: how scheduling and KV-cache allocation shape LLM serving throughput and latency (discrete-event model, no GPU, no vLLM)."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .engine import Model, simulate, workload

POC_DIR = Path(__file__).resolve().parent.parent
CONFIGS = {
    "no batching": dict(policy="none"),
    "static batching (8, 250 ms fill)": dict(policy="static", static_batch=8),
    "continuous, reserved KV": dict(policy="continuous", kv="reserved"),
    "continuous, paged KV": dict(policy="continuous", kv="paged"),
}


def run(rates=(0.5, 2.0, 6.0, 12.0), duration: float = 240.0, seed: int = 0) -> dict:
    model = Model()
    out: dict = {"model": model.__dict__, "rates": {}}
    for rate in rates:
        reqs = workload(rate, duration, seed)
        out["rates"][rate] = {
            "requests": len(reqs),
            "configs": {
                name: simulate(reqs, model, **kw).summary() for name, kw in CONFIGS.items()
            },
        }
    return out


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = ["# Results (measured by `python -m llm_serving_study.main`; discrete-event MODEL with assumed GPU cost parameters; no GPU, vLLM or real model)", "",
          f"Assumed cost model: decode iteration {r['model']['a'] * 1000:g} ms + {r['model']['b'] * 1000:g} ms per sequence, prefill {r['model']['c'] * 1000:g} ms per prompt token, KV cache {r['model']['kv_tokens']:,} tokens, max batch {r['model']['max_batch']}. Prompt median 300 tokens, output median 120 tokens, Poisson arrivals, 240 s."]  # fmt: skip
    for rate, v in r["rates"].items():
        md += ["", f"## {rate} requests/s ({v['requests']} requests)", "", markdown_table(["configuration", "tokens/s", "TTFT p50 s", "TTFT p95 s", "TPOT p50 ms", "E2E p95 s", "max concurrent", "KV peak %", "preemptions"], [[n, s["throughput_tok_s"], s["ttft_p50_s"], s["ttft_p95_s"], s["tpot_p50_ms"], s["e2e_p95_s"], s["max_concurrency"], s["kv_peak_pct"], s["preemptions"]] for n, s in v["configs"].items()])]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
