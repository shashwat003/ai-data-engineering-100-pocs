"""CLI: generate tasks, run all routing policies, write results/.

Default backend is a fast, deterministic simulation (writes results.*). `--backend ollama`
measures two real local models and writes results_ollama.* without touching the simulation files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from llm_client import LLMError, OllamaClient
from pocs_common import get_setting, load_prices, write_json

from .evaluate import render_markdown, run_experiment
from .models import LLM_PROFILE, SLM_PROFILE, OllamaModel, preflight
from .tasks import generate_tasks

POC_DIR = Path(__file__).resolve().parent.parent
SIM_TASKS, OLLAMA_TASKS = 400, 60  # real models are slow on CPU, so default to far fewer tasks


def main(argv: list[str] | None = None) -> None:
    env = POC_DIR / ".env"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", choices=["sim", "ollama"], default="sim")
    p.add_argument(
        "--n-tasks", type=int, default=None, help=f"default {SIM_TASKS} sim / {OLLAMA_TASKS} ollama"
    )
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--max-acc-drop", type=float, default=0.02)
    p.add_argument("--small-model", default=get_setting("OLLAMA_SMALL_MODEL", "llama3.2:3b", env))
    p.add_argument("--large-model", default=get_setting("OLLAMA_LARGE_MODEL", "llama3.1:8b", env))
    p.add_argument("--num-predict", type=int, default=64, help="max output tokens per reply")
    p.add_argument("--timeout", type=float, default=300.0, help="seconds per Ollama request")
    p.add_argument("--no-warmup", action="store_true")
    p.add_argument("--prices", type=Path, default=POC_DIR / "config" / "prices.json")
    p.add_argument("--out-dir", type=Path, default=POC_DIR / "results")
    args = p.parse_args(argv)

    prices = load_prices(args.prices)
    small = large = None
    meta: dict = {"backend": args.backend, "seed": args.seed}
    if args.backend == "ollama":
        if args.small_model == args.large_model:
            sys.exit("--small-model and --large-model must differ")
        missing = [m for m in (args.small_model, args.large_model) if m not in prices]
        if missing:
            sys.exit(
                f"add hypothetical prices for {missing} to {args.prices} (USD per million tokens)"
            )
        client = OllamaClient(
            get_setting("OLLAMA_URL", "http://localhost:11434", env), args.timeout
        )
        try:
            meta["ollama"] = preflight(
                client, [args.small_model, args.large_model], warmup=not args.no_warmup
            )
        except LLMError as exc:
            sys.exit(str(exc))
        small = OllamaModel(client, args.small_model, max_tokens=args.num_predict, seed=args.seed)
        large = OllamaModel(client, args.large_model, max_tokens=args.num_predict, seed=args.seed)
        meta |= {"small": args.small_model, "large": args.large_model}
    else:
        meta |= {"small": SLM_PROFILE.name, "large": LLM_PROFILE.name}

    n_tasks = args.n_tasks or (OLLAMA_TASKS if args.backend == "ollama" else SIM_TASKS)
    tasks = generate_tasks(n_tasks, args.seed)
    meta["n_tasks"] = len(tasks)
    try:
        results = run_experiment(
            tasks,
            prices,
            small=small,
            large=large,
            max_drop=args.max_acc_drop,
            study_calibration=args.backend == "sim",
        )
    except LLMError as exc:
        sys.exit(f"Ollama request failed mid-run (no results written): {exc}")
    if args.backend == "ollama":
        meta["malformed_rate"] = {m.name: m.malformed_rate for m in (small, large)}

    stem = "results_ollama" if args.backend == "ollama" else "results"
    write_json(args.out_dir / f"{stem}.json", {"meta": meta, **results})
    report = render_markdown(results, meta)
    (args.out_dir / f"{stem}.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
