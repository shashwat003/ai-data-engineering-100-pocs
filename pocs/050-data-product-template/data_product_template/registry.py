"""Catalogue-level governance checks over many product manifests (what a mesh platform team runs in CI)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .product import Product, ProductError, load_product


def load_all(root: str | Path) -> tuple[list[Product], list[str]]:
    products, errors = [], []
    for f in sorted(Path(root).glob("*/product.yaml")):
        try:
            products.append(load_product(f))
        except ProductError as e:
            errors.append(f"{f.parent.name}: {e}")
    return products, errors


def check_registry(products: list[Product], today: date) -> list[str]:
    msgs: list[str] = []
    names: dict[str, Product] = {}
    for p in products:
        if p.name in names:
            msgs.append(f"duplicate product name {p.name}")
        names[p.name] = p
    outputs = {}
    for p in products:
        if p.output_table in outputs:
            msgs.append(
                f"{p.name}: output table {p.output_table} already owned by {outputs[p.output_table]}"
            )
        outputs[p.output_table] = p.name
    for p in products:
        for c in p.consumers:
            tgt = names.get(c)
            if tgt is None:
                msgs.append(f"{p.name}: consumer {c} is not a registered product")
            elif tgt.status == "draft" and p.status == "active":
                msgs.append(f"{p.name}: active product depends on draft consumer {c}")
        if p.status == "deprecated":
            if p.sunset_date and p.sunset_date < today and p.consumers:
                msgs.append(
                    f"{p.name}: past sunset {p.sunset_date} but still has consumers {list(p.consumers)}"
                )
            elif p.sunset_date and (p.sunset_date - today).days < 30 and p.consumers:
                msgs.append(
                    f"{p.name}: sunset in {(p.sunset_date - today).days} days with consumers {list(p.consumers)} (migrate now)"
                )
        if p.status == "active" and p.tier == "gold" and not p.oncall.strip():
            msgs.append(f"{p.name}: gold product needs an on-call contact")
    return msgs
