"""POC 013: SLM benchmark harness. Demo runs against a FAKE Ollama server (see README)."""

from __future__ import annotations

from pathlib import Path

from llm_client import OllamaClient
from pocs_common import write_json
from pocs_common.report import markdown_table

from .fake_ollama import PROFILES, serve
from .harness import by_category, run_benchmark, summarize
from .tasks import build_tasks

POC_DIR = Path(__file__).resolve().parent.parent


def run(repeats: int = 3, models=None) -> dict:
    tasks = build_tasks()
    srv, url = serve({t.prompt: t.gold for t in tasks})
    try:
        rep = run_benchmark(
            OllamaClient(url, timeout=10),
            models or [*PROFILES, "llama3.2:3b"],
            tasks,
            repeats=repeats,
        )
    finally:
        srv.shutdown()
    df = summarize(rep)
    return {
        "n_tasks": len(tasks),
        "repeats": repeats,
        "skipped": rep.skipped,
        "summary": df.to_dict("records"),
        "by_category": by_category(rep).to_dict("index"),
    }


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [
        (
            r["model"],
            r["pass_rate"],
            f"{r['ci_lo']}-{r['ci_hi']}",
            r["p50_ms"],
            r["p95_ms"],
            r["tok_per_s"],
            r["memory_mb"],
            "yes" if r["pareto"] else "no",
        )
        for r in res["summary"]
    ]
    md = (
        "# Results: SLM benchmark harness run against a FAKE Ollama server\n\n"
        "WARNING: the backend here is a local fake HTTP server with scripted accuracy, latency and memory. "
        "These numbers demonstrate that the harness works; they say nothing about any real model. "
        "No real Ollama server or model was available, so the real-model path is UNVERIFIED.\n\n"
        f"{res['n_tasks']} tasks x {res['repeats']} repeats per model. Skipped: {res['skipped']}.\n\n"
        + markdown_table(
            [
                "model",
                "pass rate",
                "95% CI",
                "p50 ms",
                "p95 ms",
                "tokens/s",
                "memory MB",
                "Pareto-optimal",
            ],
            rows,
        )
        + "\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
