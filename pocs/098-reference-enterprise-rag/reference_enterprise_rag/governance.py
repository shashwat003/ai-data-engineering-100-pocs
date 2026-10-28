"""Governance side-flows around the request path: a PII classification gate (078) and cross-store erasure (088 + cache + index)."""

from __future__ import annotations

from datetime import date

import duckdb
from gdpr_erasure_pipeline.erasure import Pipeline
from pii_scanner_classifier.scanner import scan

from .app import EnterpriseRag

# What the data owners have declared. Anything the scanner finds beyond this is an unclassified PII column.
DECLARED = {
    ("customer_notes", "contact_email"): "email",
    ("customer_notes", "note"): "free text, treated as personal",
}


def build_catalog(extra_phone_column: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE customer_notes (id INTEGER, subject_id VARCHAR, note VARCHAR, contact_email VARCHAR)"
    )
    con.execute("CREATE TABLE kb_documents (doc_id VARCHAR, body VARCHAR)")
    for i in range(200):
        con.execute(
            "INSERT INTO customer_notes VALUES (?,?,?,?)",
            [
                i,
                f"u{i % 40:03d}",
                "customer reports a delayed delivery and asks for an update",
                f"person{i}@example.com",
            ],
        )
        con.execute(
            "INSERT INTO kb_documents VALUES (?,?)",
            [f"doc-{i}", "This policy describes retention handling for the Atlas platform."],
        )
    if extra_phone_column:  # a schema change nobody told governance about
        con.execute("ALTER TABLE customer_notes ADD COLUMN backup_phone VARCHAR")
        con.execute(
            "UPDATE customer_notes SET backup_phone = '555-' || lpad(CAST((id * 7919) % 900 + 100 AS VARCHAR), 3, '0') || '-' || lpad(CAST((id * 104729) % 9000 + 1000 AS VARCHAR), 4, '0')"
        )
    return con


def pii_gate(con: duckdb.DuckDBPyConnection, min_confidence: float = 0.5) -> dict:
    """Deploy gate: fail if a column that looks like PII is not in the declared classification."""
    found = [f for f in scan(con, sample=200) if f.pii_class and f.confidence >= min_confidence]
    undeclared = [
        (f.table, f.column, f.pii_class) for f in found if (f.table, f.column) not in DECLARED
    ]
    return {
        "passed": not undeclared,
        "undeclared_pii_columns": undeclared,
        "declared_found": sorted(
            (f.table, f.column) for f in found if (f.table, f.column) in DECLARED
        ),
    }


def erase_subject(
    rag: EnterpriseRag,
    pipe: Pipeline,
    subject: str,
    identifiers: list[str],
    today: date,
    purge_cache: bool = True,
) -> dict:
    """Erase a subject from the lake and ticket store (088), the in-memory index and, unless disabled, derived cached answers."""
    res = pipe.erase(subject, today, identifiers)
    gone = {d.doc_id for d in rag.docs.values() if d.subject == subject}
    rag.reindex()
    purged = rag.cache.invalidate_docs(gone) if purge_cache else 0
    leftovers = [n for n in identifiers if n in rag.index_text() or n in rag.cache.all_text()]
    return {
        "pipeline_status": res["status"],
        "docs_removed_from_index": len(gone),
        "cache_entries_purged": purged,
        "identifiers_still_visible_in_index_or_cache": leftovers,
    }
