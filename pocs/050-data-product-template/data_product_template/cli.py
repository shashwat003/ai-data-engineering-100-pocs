"""CLI: new | validate | dbt-schema [--check] | slo | registry."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from data_contracts_ci_gate.contract import ContractError, parse_contract

from .dbtgen import to_dbt_schema
from .product import ProductError, load_product
from .registry import check_registry, load_all
from .scaffold import new_product


def validate(root: Path) -> list[str]:
    errs: list[str] = []
    try:
        product = load_product(root / "product.yaml")
        contract = parse_contract((root / product.contract).read_text())
    except (ProductError, ContractError, OSError) as e:
        return [str(e)]
    if contract.id != product.name:
        errs.append(f"contract id {contract.id!r} does not match product {product.name!r}")
    if contract.owner != product.owner_team:
        errs.append(
            f"contract owner {contract.owner!r} does not match product owner {product.owner_team!r}"
        )
    if any(c.pii for c in contract.columns) and product.classification == "public":
        errs.append("contract has PII columns but product is classified public")
    fresh = contract.freshness
    if fresh and fresh.max_age_days * 24 < product.slos.freshness_hours / 4:
        errs.append("contract freshness is much stricter than the freshness SLO (inconsistent)")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="data_product_template")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("name")
    n.add_argument("--domain", required=True)
    n.add_argument("--owner", required=True)
    n.add_argument("--oncall", required=True)
    n.add_argument("--out", default=".")  # noqa: E702
    for c in ("validate", "registry"):
        sub.add_parser(c).add_argument("path")
    d = sub.add_parser("dbt-schema")
    d.add_argument("path")
    d.add_argument("--check", action="store_true")  # noqa: E702
    a = ap.parse_args(argv)
    try:
        if a.cmd == "new":
            print(new_product(a.name, a.domain, a.owner, a.oncall, a.out))
            return 0  # noqa: E702
        if a.cmd == "validate":
            errs = validate(Path(a.path))
            for e in errs:
                print("ERROR", e)
            return 1 if errs else 0
        if a.cmd == "dbt-schema":
            root = Path(a.path)
            product = load_product(root / "product.yaml")
            want = to_dbt_schema(parse_contract((root / product.contract).read_text()), product)
            f = root / "dbt_project" / "models" / "schema.yml"
            if a.check:
                if not f.exists() or f.read_text() != want:
                    print("ERROR schema.yml is out of date with contract.yaml; regenerate it")
                    return 1
                return 0
            f.write_text(want)
            return 0  # noqa: E702
        products, errs = load_all(a.path)
        errs += check_registry(products, date.today())
        for e in errs:
            print("ERROR", e)
        return 1 if errs else 0
    except (ProductError, ContractError, ValueError, OSError) as e:
        print("ERROR", e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
