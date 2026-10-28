"""Generate dbt schema.yml (enforced model contract + tests + docs) from the data contract: one source of truth."""

from __future__ import annotations

import yaml
from data_contracts_ci_gate.contract import Contract

from .product import Product

DBT_TYPES = {
    "string": "varchar",
    "integer": "integer",
    "number": "double",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp",
}


def column_tests(c) -> list:
    tests: list = []
    if c.required:
        tests.append("not_null")
    if c.unique:
        tests.append("unique")
    if c.enum:
        tests.append({"accepted_values": {"arguments": {"values": list(c.enum)}}})
    return tests


def to_dbt_schema(contract: Contract, product: Product) -> str:
    cols = []
    for c in contract.columns:
        entry: dict = {
            "name": c.name,
            "data_type": DBT_TYPES[c.type],
            "description": c.description or c.name,
        }
        t = column_tests(c)
        if t:
            entry["data_tests"] = t
        meta = {
            k: v
            for k, v in (
                ("pii", True if c.pii else None),
                ("min", c.min),
                ("max", c.max),
                ("pattern", c.pattern),
            )
            if v is not None
        }
        if meta:
            entry["meta"] = meta
        cols.append(entry)
    doc = {
        "version": 2,
        "models": [
            {
                "name": product.output_table,
                "description": contract.description,
                "config": {
                    "contract": {"enforced": True},
                    "meta": {
                        "owner": product.owner_team,
                        "tier": product.tier,
                        "classification": product.classification,
                    },
                },
                "columns": cols,
            }
        ],
    }
    return (
        "# GENERATED from contract.yaml by data_product_template.dbtgen. Do not edit.\n"
        + yaml.safe_dump(doc, sort_keys=False, width=120)
    )


def range_tests_sql(contract: Contract, product: Product) -> dict[str, str]:
    """Singular dbt tests for ranges and patterns, which have no built-in generic test."""
    out = {}
    for c in contract.columns:
        conds = []
        if c.min is not None:
            conds.append(f'"{c.name}" < {c.min}')
        if c.max is not None:
            conds.append(f'"{c.name}" > {c.max}')
        if c.pattern:
            conds.append(
                f'"{c.name}" is not null and not regexp_matches("{c.name}", \'{c.pattern.replace(chr(39), chr(39) * 2)}\')'
            )
        if conds:
            out[f"assert_{product.output_table}_{c.name}_valid"] = (
                f"select * from {{{{ ref('{product.output_table}') }}}} where "
                + " or ".join(f"({x})" for x in conds)
                + "\n"
            )
    return out
