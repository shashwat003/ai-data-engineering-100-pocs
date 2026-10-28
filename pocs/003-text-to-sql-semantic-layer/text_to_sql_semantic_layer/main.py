"""CLI: build the synthetic warehouse, run the measurements, show sample answers.

python -m text_to_sql_semantic_layer.main                 # demo + results/
python -m text_to_sql_semantic_layer.main --ask "Revenue by country"
python -m text_to_sql_semantic_layer.main --ask "..." --planner llm --model llama3.2:3b
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from data_gen.warehouse import build_warehouse
from llm_client import LLMError, OllamaClient
from pocs_common import get_setting, markdown_table, write_json

from .evaluate import evaluate_guard, evaluate_questions, render_markdown
from .planner import LLMPlanner, LLMSQLWriter
from .service import Answer, QueryService

POC_DIR = Path(__file__).resolve().parent.parent
DEMO_QUESTIONS = ["Top 5 products by revenue", "Revenue by country", "Show me all customer emails"]


def show(answer: Answer) -> str:
    out = [f"Q: {answer.question}", f"   status: {answer.status}"]
    if answer.interpretation:
        out.append(f"   interpreted as: {answer.interpretation}")
    if answer.sql:
        out.append(f"   sql: {answer.sql}")
    if answer.reasons:
        out.append(f"   reasons: {answer.reasons}")
    if answer.rows:
        out.append(markdown_table(answer.columns, answer.rows[:5]))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--ask", help="answer one question instead of running the full demo")
    p.add_argument("--planner", choices=["rules", "llm", "llm-sql"], default="rules")
    p.add_argument("--model", default=get_setting("OLLAMA_MODEL", "llama3.2:3b", POC_DIR / ".env"))
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--db", type=Path, default=POC_DIR / "data" / "generated" / "warehouse.db")
    p.add_argument("--out-dir", type=Path, default=POC_DIR / "results")
    args = p.parse_args(argv)

    build_warehouse(args.db, seed=args.seed)
    kwargs = {}
    if args.planner != "rules":
        client = OllamaClient(get_setting("OLLAMA_URL", "http://localhost:11434", POC_DIR / ".env"))
        kwargs = (
            {"planner": LLMPlanner(client, args.model)}
            if args.planner == "llm"
            else {"writer": LLMSQLWriter(client, args.model)}
        )
    service = QueryService(args.db, **kwargs)

    if args.ask:
        try:
            answer = (
                service.ask_with_writer(args.ask)
                if args.planner == "llm-sql"
                else service.ask(args.ask)
            )
        except LLMError as exc:
            sys.exit(f"language model unavailable: {exc}")
        print(show(answer))
        return

    for question in DEMO_QUESTIONS:
        print(show(service.ask(question)), end="\n\n")
    service = QueryService(args.db)  # measurements always use the deterministic planner
    acc, guard = evaluate_questions(service), evaluate_guard(service)
    write_json(args.out_dir / "results.json", {"accuracy": acc, "guard": guard})
    report = render_markdown(acc, guard)
    (args.out_dir / "results.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
