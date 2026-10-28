"""Create a new data product from the templates directory (sandboxed Jinja2, strict undefined)."""

from __future__ import annotations

import re
from pathlib import Path

from data_contracts_ci_gate.contract import parse_contract
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from .dbtgen import range_tests_sql, to_dbt_schema
from .product import NAME, load_product

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def new_product(
    name: str, domain: str, owner_team: str, oncall: str, out_dir: str | Path, description: str = ""
) -> Path:
    if not NAME.match(name):
        raise ValueError("name must be snake_case, 3-41 characters")
    for label, v in (("domain", domain), ("owner_team", owner_team), ("oncall", oncall)):
        if not re.fullmatch(r"[A-Za-z0-9_.@\- ]{1,60}", v):
            raise ValueError(f"{label} contains unsupported characters")
    root = Path(out_dir) / name
    if root.exists():
        raise FileExistsError(f"{root} already exists; refusing to overwrite")
    env = SandboxedEnvironment(
        undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True
    )
    ctx = {
        "name": name,
        "domain": domain,
        "owner_team": owner_team,
        "oncall": oncall,
        "description": description or f"Output of the {name} data product.",
    }

    def render(tpl: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(env.from_string((TEMPLATES / tpl).read_text()).render(**ctx))

    render("product.yaml.j2", root / "product.yaml")
    render("contract.yaml.j2", root / "contract.yaml")
    render("README.md.j2", root / "README.md")
    render("product-ci.yml.j2", root / ".github" / "workflows" / "product-ci.yml")
    render("dbt_project.yml.j2", root / "dbt_project" / "dbt_project.yml")
    render("profiles.yml.j2", root / "dbt_project" / "profiles.yml")
    render("model.sql.j2", root / "dbt_project" / "models" / f"{name}.sql")
    regenerate_dbt_files(root)
    return root


def regenerate_dbt_files(root: Path) -> None:
    product = load_product(root / "product.yaml")
    contract = parse_contract((root / product.contract).read_text())
    (root / "dbt_project" / "models" / "schema.yml").write_text(to_dbt_schema(contract, product))
    tdir = root / "dbt_project" / "tests"
    tdir.mkdir(exist_ok=True)
    for old in tdir.glob("assert_*_valid.sql"):
        old.unlink()
    for n, sql in range_tests_sql(contract, product).items():
        (tdir / f"{n}.sql").write_text(sql)
