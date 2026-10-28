"""Flatten a Terraform plan or state (JSON) into resources the rules can inspect."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Res:
    address: str
    type: str
    name: str
    index: str  # for_each key or "" for singletons
    module: str
    values: dict
    unknown: dict = field(default_factory=dict)  # attribute -> True when "known after apply"

    @property
    def group(self) -> str:
        """Resources that belong to one logical bucket share module + name + index."""
        return f"{self.module}|{self.name}|{self.index}"

    def get(self, key: str, default=None):
        return default if self.unknown.get(key) is True else self.values.get(key, default)

    def is_unknown(self, key: str) -> bool:
        return self.unknown.get(key) is True


_ADDR = re.compile(
    r"^(?P<module>(?:module\.[^.]+(?:\[[^\]]*\])?\.)*)(?P<type>[a-z0-9_]+)\.(?P<name>[A-Za-z0-9_-]+)(?:\[(?P<idx>.*)\])?$"
)


def _parse(address: str, rtype: str, name: str, index) -> tuple[str, str]:
    m = _ADDR.match(address)
    module = (m.group("module") if m else "").rstrip(".")
    return module, "" if index is None else str(index)


def from_plan(plan: dict) -> list[Res]:
    out = []
    for rc in plan.get("resource_changes", []):
        if rc.get("mode") != "managed" or "delete" in rc["change"]["actions"]:
            continue
        after = rc["change"].get("after") or {}
        module, idx = _parse(rc["address"], rc["type"], rc["name"], rc.get("index"))
        out.append(Res(rc["address"], rc["type"], rc["name"], idx, module, after,
                       rc["change"].get("after_unknown") or {}))  # fmt: skip
    return out


def from_state(state: dict) -> list[Res]:
    out = []

    def walk(mod: dict) -> None:
        for r in mod.get("resources", []):
            if r.get("mode") == "managed":
                module, idx = _parse(r["address"], r["type"], r["name"], r.get("index"))
                out.append(
                    Res(r["address"], r["type"], r["name"], idx, module, r.get("values") or {})
                )
        for child in mod.get("child_modules", []):
            walk(child)

    walk((state.get("values") or {}).get("root_module", {}))
    return out
