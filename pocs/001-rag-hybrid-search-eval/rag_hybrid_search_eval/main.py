"""CLI: generate the synthetic knowledge base, run the experiment, write results/.

Default run is deterministic and needs only numpy. `--neural` additionally evaluates a local
neural embedding model and writes results_neural.* (the default results files are not touched).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from data_gen import generate_knowledge_base
from llm_client import LLMError, OllamaClient
from pocs_common import get_setting, write_json
from retrieval_kit import OllamaEmbedder, SentenceTransformerEmbedder

from .pipeline import render_markdown, run_experiment

POC_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MODELS = {"sentence-transformers": "BAAI/bge-small-en-v1.5", "ollama": "nomic-embed-text"}
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def make_neural(kind: str, model: str | None):
    """Build the optional neural embedder. Imports/downloads happen lazily on first use."""
    model = model or DEFAULT_MODELS[kind]
    if kind == "ollama":
        url = get_setting("OLLAMA_URL", "http://localhost:11434", POC_DIR / ".env")
        return OllamaEmbedder(OllamaClient(url), model)
    prefix = BGE_QUERY_PREFIX if "bge" in model.lower() else ""
    return SentenceTransformerEmbedder(model, query_prefix=prefix)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-systems", type=int, default=24, help="documents = 5 x n-systems")
    p.add_argument("--queries-per-kind", type=int, default=50)
    p.add_argument("--dims", type=int, default=64, help="LSA dimensions")
    p.add_argument("--neural", choices=["sentence-transformers", "ollama"], default=None)
    p.add_argument("--neural-model", default=None, help="default depends on --neural")
    p.add_argument("--out-dir", type=Path, default=POC_DIR / "results")
    args = p.parse_args(argv)

    kb = generate_knowledge_base(
        n_systems=args.n_systems, queries_per_kind=args.queries_per_kind, seed=args.seed
    )
    neural = make_neural(args.neural, args.neural_model) if args.neural else None
    try:
        results = run_experiment(kb, neural=neural, dims=args.dims)
    except (ImportError, LLMError, OSError) as exc:  # missing package, server down, no model
        sys.exit(f"neural embedder unavailable: {exc}")

    stem = "results_neural" if neural else "results"
    write_json(args.out_dir / f"{stem}.json", results)
    report = render_markdown(results)
    (args.out_dir / f"{stem}.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
