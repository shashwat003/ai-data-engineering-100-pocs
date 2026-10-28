"""Bronze -> silver -> gold lakehouse on DuckDB and Parquet.

* bronze: immutable Parquet per (entity, batch), every column raw text plus lineage columns.
* silver: typed, validated, deduplicated DuckDB tables; bad rows go to `silver_quarantine`.
  Orders are upserted by key (latest batch wins); customers are SCD type 2.
* gold: rebuilt from silver on every run inside one transaction, exported to Parquet only if the
  quality gates pass (row reconciliation, POC 077 contract on silver, orphan check).
* A ledger keyed by (entity, batch, sha256) makes every step idempotent.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from data_contracts_ci_gate.contract import load_contract
from data_contracts_ci_gate.validate import validate_dataset

BATCH_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ORDER_COLS = ["order_id", "customer_id", "order_date", "ship_date", "status", "amount", "email"]
CUSTOMER_COLS = ["customer_id", "segment", "country"]
ENTITY_COLS = {"orders": ORDER_COLS, "customers": CUSTOMER_COLS}
OPEN_END = date(9999, 12, 31)
INITIAL_START = date(1900, 1, 1)  # a customer's first version covers all earlier history
CONTRACT = (
    Path(__import__("data_contracts_ci_gate").__file__).resolve().parent.parent
    / "contracts"
    / "orders_feed.yaml"
)


class LakehouseError(Exception):
    pass


class SchemaError(LakehouseError):
    """A landing file is missing required columns or has an unsafe batch id."""


class BatchConflict(LakehouseError):
    """Same batch id arrived again with different bytes: sources must be immutable."""


class BatchOrderError(LakehouseError):
    """SCD2 snapshots must be applied in effective-date order."""


class GateFailure(LakehouseError):
    """A quality gate failed; gold was not published."""


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class RunReport:
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _q(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


class Lakehouse:
    def __init__(self, root: str | Path, *, now=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(UTC).replace(tzinfo=None, microsecond=0))
        self.con = duckdb.connect(str(self.root / "lakehouse.duckdb"))
        self._init_schema()

    def close(self) -> None:
        self.con.close()

    def _init_schema(self) -> None:
        self.con.execute("""
        CREATE TABLE IF NOT EXISTS ledger (
            entity VARCHAR, batch_id VARCHAR, sha256 VARCHAR, bronze_rows BIGINT,
            effective_date DATE, ingested_at TIMESTAMP, silver_at TIMESTAMP,
            accepted BIGINT, quarantined BIGINT, PRIMARY KEY (entity, batch_id));
        CREATE TABLE IF NOT EXISTS silver_orders (
            order_id BIGINT PRIMARY KEY, customer_id BIGINT NOT NULL, order_date DATE NOT NULL,
            ship_date DATE, status VARCHAR NOT NULL, amount DECIMAL(12,2) NOT NULL,
            email VARCHAR, _batch_id VARCHAR NOT NULL);
        CREATE TABLE IF NOT EXISTS silver_quarantine (
            entity VARCHAR, batch_id VARCHAR, row_num BIGINT, reason VARCHAR, raw VARCHAR);
        CREATE TABLE IF NOT EXISTS dim_customer (
            customer_sk BIGINT PRIMARY KEY, customer_id BIGINT NOT NULL, segment VARCHAR NOT NULL,
            country VARCHAR NOT NULL, valid_from DATE NOT NULL, valid_to DATE NOT NULL,
            is_current BOOLEAN NOT NULL, _batch_id VARCHAR NOT NULL);
        """)

    # ------------------------------------------------------------------ bronze

    def _bronze_path(self, entity: str, batch_id: str) -> Path:
        return self.root / "bronze" / entity / f"batch_id={batch_id}" / "data.parquet"

    def ingest_bronze(self, entity: str, batch_id: str, csv_path: Path, effective: date) -> bool:
        """Copy a landing CSV into immutable bronze Parquet. Returns False if already ingested."""
        if not BATCH_ID.match(batch_id):
            raise SchemaError(f"unsafe batch id {batch_id!r}")
        digest = _sha256(csv_path)
        row = self.con.execute(
            "SELECT sha256 FROM ledger WHERE entity=? AND batch_id=?", [entity, batch_id]
        ).fetchone()
        if row:
            if row[0] != digest:
                raise BatchConflict(f"{entity}/{batch_id} already ingested with different content")
            return False
        with csv_path.open(newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), [])
        missing = [c for c in ENTITY_COLS[entity] if c not in header]
        if missing:
            raise SchemaError(f"{entity}/{batch_id}: missing columns {missing}")
        cols = ENTITY_COLS[entity]  # undeclared extra columns are dropped, never trusted
        select = ", ".join(f'"{c}"' for c in cols)
        out = self._bronze_path(entity, batch_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        self.con.execute(
            f"""COPY (SELECT {select}, ? AS _batch_id, ? AS _source_sha256,
                       row_number() OVER () AS _row_num
                FROM read_csv(?, header=true, all_varchar=true, nullstr='', quote='"'))
                TO {_q(tmp)} (FORMAT parquet)""",
            [batch_id, digest, str(csv_path)],
        )
        tmp.replace(out)  # atomic publish: readers never see a half-written file
        n = self.con.execute(f"SELECT count(*) FROM read_parquet({_q(out)})").fetchone()[0]
        self.con.execute(
            "INSERT INTO ledger (entity,batch_id,sha256,bronze_rows,effective_date,ingested_at)"
            " VALUES (?,?,?,?,?,?)",
            [entity, batch_id, digest, n, effective, self._now()],
        )
        return True

    def _pending(self, entity: str) -> list[str]:
        rows = self.con.execute(
            "SELECT batch_id FROM ledger WHERE entity=? AND silver_at IS NULL ORDER BY batch_id",
            [entity],
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ silver: orders

    def promote_orders(self, batch_id: str) -> tuple[int, int]:
        bronze = _q(self._bronze_path("orders", batch_id))
        self.con.execute("BEGIN")
        try:
            self.con.execute(f"""
            CREATE OR REPLACE TEMP TABLE stage AS
            WITH t AS (
              SELECT _row_num, order_id AS r_order_id, customer_id AS r_customer_id,
                     order_date AS r_order_date, status AS r_status, amount AS r_amount,
                     email AS r_email, ship_date AS r_ship_date,
                     TRY_CAST(order_id AS BIGINT) AS order_id,
                     TRY_CAST(customer_id AS BIGINT) AS customer_id,
                     TRY_CAST(order_date AS DATE) AS order_date,
                     TRY_CAST(ship_date AS DATE) AS ship_date, status,
                     TRY_CAST(amount AS DECIMAL(12,2)) AS amount, email
              FROM read_parquet({bronze})),
            r AS (
              SELECT *, CASE
                WHEN r_order_id IS NULL THEN 'required:order_id'
                WHEN order_id IS NULL THEN 'type:order_id'
                WHEN r_customer_id IS NULL THEN 'required:customer_id'
                WHEN customer_id IS NULL THEN 'type:customer_id'
                WHEN r_order_date IS NULL THEN 'required:order_date'
                WHEN order_date IS NULL THEN 'type:order_date'
                WHEN r_ship_date IS NOT NULL AND ship_date IS NULL THEN 'type:ship_date'
                WHEN r_status IS NULL OR status NOT IN
                     ('completed','shipped','cancelled','returned') THEN 'enum:status'
                WHEN r_amount IS NULL THEN 'required:amount'
                WHEN amount IS NULL THEN 'type:amount'
                WHEN amount < 0 THEN 'range:amount_min'
                WHEN amount > 1000000 THEN 'range:amount_max'
                WHEN email IS NOT NULL AND NOT regexp_matches(email,
                     '^[^@\\s]+@[^@\\s]+\\.[A-Za-z]{{2,}}$') THEN 'pattern:email'
                WHEN ship_date IS NOT NULL AND ship_date < order_date THEN 'rule:ship_before_order'
              END AS reason FROM t),
            d AS (
              SELECT *, CASE WHEN reason IS NULL AND row_number() OVER (
                         PARTITION BY order_id, (reason IS NULL) ORDER BY _row_num) > 1
                         THEN 'unique:order_id' ELSE reason END AS final_reason FROM r)
            SELECT * FROM d""")
            self.con.execute("DELETE FROM silver_quarantine WHERE entity='orders' AND batch_id=?",
                             [batch_id])  # fmt: skip
            self.con.execute(
                """INSERT INTO silver_quarantine
                SELECT 'orders', ?, _row_num, final_reason,
                       to_json({'order_id': r_order_id, 'customer_id': r_customer_id,
                                'order_date': r_order_date, 'status': r_status,
                                'amount': r_amount})::VARCHAR
                FROM stage WHERE final_reason IS NOT NULL ORDER BY _row_num""",
                [batch_id],
            )  # raw excludes email on purpose: quarantine must not become a PII copy
            self.con.execute(
                """INSERT INTO silver_orders
                SELECT order_id, customer_id, order_date, ship_date, status, amount, email, ?
                FROM stage WHERE final_reason IS NULL
                ON CONFLICT (order_id) DO UPDATE SET
                  customer_id=EXCLUDED.customer_id, order_date=EXCLUDED.order_date,
                  ship_date=EXCLUDED.ship_date, status=EXCLUDED.status, amount=EXCLUDED.amount,
                  email=EXCLUDED.email, _batch_id=EXCLUDED._batch_id
                WHERE EXCLUDED._batch_id >= silver_orders._batch_id""",
                [batch_id],
            )
            bad, total = self.con.execute(
                "SELECT count(*) FILTER (WHERE final_reason IS NOT NULL), count(*) FROM stage"
            ).fetchone()
            self._mark_promoted("orders", batch_id, total - bad, bad)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        return total - bad, bad

    def _mark_promoted(self, entity: str, batch_id: str, accepted: int, quarantined: int) -> None:
        self.con.execute(
            "UPDATE ledger SET silver_at=?, accepted=?, quarantined=? WHERE entity=? AND batch_id=?",
            [self._now(), accepted, quarantined, entity, batch_id],
        )

    # ------------------------------------------------------------------ silver: customers (SCD2)

    def promote_customers(self, batch_id: str) -> tuple[int, int]:
        bronze = _q(self._bronze_path("customers", batch_id))
        eff = self.con.execute(
            "SELECT effective_date FROM ledger WHERE entity='customers' AND batch_id=?",
            [batch_id],
        ).fetchone()[0]
        latest = self.con.execute(
            "SELECT max(effective_date) FROM ledger WHERE entity='customers' AND silver_at IS NOT NULL"
        ).fetchone()[0]
        if latest is not None and eff <= latest:
            raise BatchOrderError(
                f"customers/{batch_id} effective {eff} is not after the latest applied {latest}"
            )
        self.con.execute("BEGIN")
        try:
            self.con.execute(f"""
            CREATE OR REPLACE TEMP TABLE cstage AS
            WITH t AS (SELECT _row_num, TRY_CAST(customer_id AS BIGINT) AS customer_id,
                              customer_id AS r_id, segment, country FROM read_parquet({bronze}))
            SELECT *, CASE
                WHEN r_id IS NULL THEN 'required:customer_id'
                WHEN customer_id IS NULL THEN 'type:customer_id'
                WHEN segment IS NULL OR segment NOT IN ('consumer','smb','enterprise')
                     THEN 'enum:segment'
                WHEN country IS NULL THEN 'required:country'
                WHEN row_number() OVER (PARTITION BY customer_id, (customer_id IS NOT NULL
                     AND segment IN ('consumer','smb','enterprise') AND country IS NOT NULL)
                     ORDER BY _row_num) > 1
                     THEN 'unique:customer_id' END AS reason FROM t""")
            self.con.execute("DELETE FROM silver_quarantine WHERE entity='customers' AND batch_id=?",
                             [batch_id])  # fmt: skip
            self.con.execute(
                """INSERT INTO silver_quarantine SELECT 'customers', ?, _row_num, reason,
                to_json({'customer_id': r_id, 'segment': segment, 'country': country})::VARCHAR
                FROM cstage WHERE reason IS NOT NULL ORDER BY _row_num""",
                [batch_id],
            )
            self.con.execute("""CREATE OR REPLACE TEMP TABLE changed AS
                SELECT s.customer_id, s.segment, s.country, d.customer_id IS NULL AS is_new FROM cstage s
                LEFT JOIN dim_customer d ON d.customer_id = s.customer_id AND d.is_current
                WHERE s.reason IS NULL
                  AND (d.customer_id IS NULL OR d.segment <> s.segment OR d.country <> s.country)""")
            self.con.execute(
                """UPDATE dim_customer SET valid_to=?, is_current=false
                WHERE is_current AND customer_id IN (SELECT customer_id FROM changed)""",
                [eff],
            )
            base = self.con.execute(
                "SELECT coalesce(max(customer_sk), 0) FROM dim_customer"
            ).fetchone()[0]
            self.con.execute(
                """INSERT INTO dim_customer
                SELECT ? + row_number() OVER (ORDER BY customer_id), customer_id, segment, country,
                       CASE WHEN is_new THEN ? ELSE ? END, ?, true, ? FROM changed""",
                [base, INITIAL_START, eff, OPEN_END, batch_id],
            )
            bad, total = self.con.execute(
                "SELECT count(*) FILTER (WHERE reason IS NOT NULL), count(*) FROM cstage"
            ).fetchone()
            self._mark_promoted("customers", batch_id, total - bad, bad)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        return total - bad, bad

    # ------------------------------------------------------------------ gold + gates

    def build_gold(self, as_of: date | None = None) -> list[Check]:
        """Rebuild gold in one transaction; publish (commit + Parquet) only if every gate passes."""
        self.con.execute("BEGIN")
        try:
            self.con.execute("""
            CREATE OR REPLACE TABLE gold_fct_orders AS
            SELECT o.order_id, coalesce(d.customer_sk, -1) AS customer_sk, o.order_date,
                   o.status, o.amount, coalesce(d.segment, 'unknown') AS segment_at_order,
                   coalesce(d.country, 'unknown') AS country_at_order
            FROM silver_orders o
            LEFT JOIN dim_customer d ON d.customer_id = o.customer_id
                 AND o.order_date >= d.valid_from AND o.order_date < d.valid_to
            ORDER BY o.order_id;
            CREATE OR REPLACE TABLE gold_daily_revenue AS
            SELECT order_date, count(*) AS orders,
                   sum(amount) FILTER (WHERE status IN ('completed','shipped')) AS net_revenue
            FROM gold_fct_orders GROUP BY order_date ORDER BY order_date;
            CREATE OR REPLACE TABLE gold_revenue_by_segment AS
            SELECT segment_at_order AS segment, count(*) AS orders,
                   sum(amount) FILTER (WHERE status IN ('completed','shipped')) AS net_revenue
            FROM gold_fct_orders GROUP BY 1 ORDER BY 1;""")
            checks = self.run_gates(as_of)
            failed = [c for c in checks if not c.passed]
            if failed:
                raise GateFailure("; ".join(f"{c.name}: {c.detail}" for c in failed))
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        gold_dir = self.root / "gold"
        gold_dir.mkdir(exist_ok=True)
        for t in ("gold_fct_orders", "gold_daily_revenue", "gold_revenue_by_segment"):
            self.con.execute(f"COPY {t} TO {_q(gold_dir / (t + '.parquet'))} (FORMAT parquet)")
        return checks

    def run_gates(self, as_of: date | None = None) -> list[Check]:
        one = lambda sql: self.con.execute(sql).fetchone()[0]  # noqa: E731
        checks: list[Check] = []
        bad = self.con.execute(
            "SELECT entity, batch_id, bronze_rows, accepted, quarantined FROM ledger "
            "WHERE silver_at IS NOT NULL AND bronze_rows <> accepted + quarantined"
        ).fetchall()
        checks.append(
            Check("reconcile bronze = accepted + quarantined", not bad, f"mismatches: {bad}")
        )
        q_rows = one("SELECT count(*) FROM silver_quarantine")
        q_ledger = one("SELECT coalesce(sum(quarantined), 0) FROM ledger")
        checks.append(
            Check("quarantine table matches ledger", q_rows == q_ledger, f"{q_rows} vs {q_ledger}")
        )
        n_silver, n_gold = (
            one("SELECT count(*) FROM silver_orders"),
            one("SELECT count(*) FROM gold_fct_orders"),
        )
        checks.append(
            Check("gold rows = silver rows", n_silver == n_gold, f"{n_silver} vs {n_gold}")
        )
        s_amt, g_amt = (
            one("SELECT coalesce(sum(amount),0) FROM silver_orders"),
            one("SELECT coalesce(sum(amount),0) FROM gold_fct_orders"),
        )
        checks.append(Check("gold amount = silver amount", s_amt == g_amt, f"{s_amt} vs {g_amt}"))
        d_amt = one("SELECT coalesce(sum(net_revenue),0) FROM gold_daily_revenue")
        s_net = one(
            "SELECT coalesce(sum(amount),0) FROM silver_orders WHERE status IN ('completed','shipped')"
        )
        checks.append(
            Check("daily revenue mart = silver net revenue", d_amt == s_net, f"{d_amt} vs {s_net}")
        )
        orphans = one("SELECT count(*) FROM gold_fct_orders WHERE customer_sk = -1")
        checks.append(
            Check("no orders without a customer version", orphans == 0, f"{orphans} orphans")
        )
        open_rows = one("SELECT count(*) FROM (SELECT customer_id FROM dim_customer WHERE is_current "
                        "GROUP BY 1 HAVING count(*) > 1)")  # fmt: skip
        checks.append(
            Check("one current version per customer", open_rows == 0, f"{open_rows} violations")
        )
        checks.append(self._contract_gate(as_of))
        return checks

    def _contract_gate(self, as_of: date | None) -> Check:
        """Export silver orders and validate them with the POC 077 contract (errors fail the gate)."""
        contract = load_contract(CONTRACT)
        if as_of is None:
            newest = self.con.execute("SELECT max(order_date) FROM silver_orders").fetchone()[0]
            as_of = (newest or date.today()) + timedelta(days=1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "silver_orders.csv"
            cols = ", ".join(ORDER_COLS)
            self.con.execute(
                f"COPY (SELECT {cols} FROM silver_orders ORDER BY order_id) TO {_q(path)} (HEADER)"
            )
            report = validate_dataset(contract, path, as_of=as_of)
        detail = f"contract {report.contract}@{report.version}, {report.rows} rows, " + (
            "no errors" if report.passed else f"errors: {report.counts()}"
        )
        return Check("silver orders satisfy data contract (POC 077)", report.passed, detail)

    # ------------------------------------------------------------------ orchestration

    def run(self, landing: str | Path, *, as_of: date | None = None) -> RunReport:
        """Process every batch directory in order. Safe to call repeatedly."""
        report = RunReport()
        landing = Path(landing)
        for d in sorted(p for p in landing.iterdir() if p.is_dir()):
            meta = json.loads((d / "_meta.json").read_text())
            if meta["batch_id"] != d.name or not BATCH_ID.match(d.name):
                raise SchemaError(f"{d.name}: _meta.json batch_id mismatch")
            eff = date.fromisoformat(meta["effective_date"])
            for entity in ("customers", "orders"):  # dimension first so facts can join to it
                new = self.ingest_bronze(entity, d.name, d / f"{entity}.csv", eff)
                (report.ingested if new else report.skipped).append(f"{entity}/{d.name}")
        # promote in batch order so SCD2 history is built chronologically
        for batch_id in sorted(set(self._pending("customers")) | set(self._pending("orders"))):
            if batch_id in self._pending("customers"):
                self.promote_customers(batch_id)
            if batch_id in self._pending("orders"):
                self.promote_orders(batch_id)
        report.checks = self.build_gold(as_of)
        return report

    def rebuild_from_bronze(self) -> None:
        """Drop silver/gold state and replay all bronze files (proves bronze is the source of truth)."""
        self.con.execute("""
            DELETE FROM silver_orders; DELETE FROM dim_customer; DELETE FROM silver_quarantine;
            UPDATE ledger SET silver_at=NULL, accepted=NULL, quarantined=NULL;""")
        for batch_id in sorted(set(self._pending("customers")) | set(self._pending("orders"))):
            self.promote_customers(batch_id)
            self.promote_orders(batch_id)
        self.build_gold()

    def content_hash(self) -> str:
        """Order-independent hash of silver + gold content (excludes timestamps)."""
        h = hashlib.sha256()
        for sql in (
            "SELECT * FROM silver_orders ORDER BY order_id",
            "SELECT * FROM dim_customer ORDER BY customer_sk",
            "SELECT * FROM silver_quarantine ORDER BY entity, batch_id, row_num",
            "SELECT * FROM gold_fct_orders ORDER BY order_id",
            "SELECT * FROM gold_daily_revenue ORDER BY order_date",
            "SELECT * FROM gold_revenue_by_segment ORDER BY segment",
        ):
            h.update(repr(self.con.execute(sql).fetchall()).encode())
        return h.hexdigest()[:16]
