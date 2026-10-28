"""End-to-end flow: question -> plan/SQL -> validate -> execute read-only -> answer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .executor import ExecutionError, SafeExecutor
from .guard import Policy, validate
from .planner import LLMSQLWriter, RulePlanner, Unplannable
from .schema import connect_readonly, introspect
from .semantic import Plan, compile_plan


@dataclass
class Answer:
    question: str
    status: str  # ok | unplannable | rejected | denied | timeout | error
    interpretation: str | None = None
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    reasons: list[str] = field(default_factory=list)
    rewrites: list[str] = field(default_factory=list)


def describe_plan(plan: Plan) -> str:
    parts = [f"metric={plan.metric}"]
    if plan.dimensions:
        parts.append("by " + ", ".join(plan.dimensions))
    parts += [f"{f.dimension} in {list(f.values)}" for f in plan.filters]
    if plan.order:
        parts.append(f"order={plan.order}" + (f" limit={plan.limit}" if plan.limit else ""))
    return "; ".join(parts)


class QueryService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        planner=None,
        writer: LLMSQLWriter | None = None,
        policy: Policy | None = None,
        max_rows: int = 1000,
        timeout_s: float = 2.0,
    ):
        self.schema = introspect(db_path)
        self.policy = policy or Policy()
        self.executor = SafeExecutor(
            db_path, self.schema, self.policy, max_rows=max_rows, timeout_s=timeout_s
        )
        self.planner = planner or RulePlanner(self._known_values(db_path))
        self.writer = writer

    @staticmethod
    def _known_values(db_path: str | Path) -> dict[str, set[str]]:
        con = connect_readonly(db_path)
        try:
            query = {
                "country": "SELECT DISTINCT country FROM customers",
                "segment": "SELECT DISTINCT segment FROM customers",
                "category": "SELECT DISTINCT category FROM products",
                "status": "SELECT DISTINCT status FROM orders",
            }
            return {dim: {r[0] for r in con.execute(sql)} for dim, sql in query.items()}
        finally:
            con.close()

    def run_sql(self, sql: str, params: tuple = (), question: str = "") -> Answer:
        """Every SQL string, whoever wrote it, goes through the validator and the executor."""
        check = validate(sql, self.schema, self.policy)
        if not check.ok:
            return Answer(question, "rejected", sql=sql, reasons=check.reasons)
        try:
            result = self.executor.run(check.sql, params)
        except ExecutionError as exc:
            return Answer(
                question,
                exc.kind if exc.kind != "error" else "error",
                sql=check.sql,
                reasons=[str(exc)],
                rewrites=check.rewrites,
            )
        return Answer(
            question,
            "ok",
            sql=check.sql,
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
            rewrites=check.rewrites,
        )

    def ask(self, question: str) -> Answer:
        try:
            plan = self.planner.plan(question)
        except Unplannable as exc:
            return Answer(question, "unplannable", reasons=[str(exc)])
        compiled = compile_plan(plan)
        answer = self.run_sql(compiled.sql, compiled.params, question)
        answer.interpretation = describe_plan(plan)
        return answer

    def ask_with_writer(self, question: str) -> Answer:
        if self.writer is None:
            raise RuntimeError("no SQL writer configured")
        return self.run_sql(self.writer.write(question, self.schema), (), question)
