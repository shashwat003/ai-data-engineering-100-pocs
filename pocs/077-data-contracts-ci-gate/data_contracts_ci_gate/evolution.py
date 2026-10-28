"""Compare two versions of a contract and require a matching semantic-version bump.

Policy (explicit, so it can be argued with):
- BREAKING (needs MAJOR): can invalidate data that passed the old contract or removes a guarantee
  consumers rely on: column removed, type changed, required/unique/pii changed, a constraint added
  or narrowed (min up, max down, enum values removed, pattern changed, max_length down), a new
  required column, a stricter quality threshold or a new error-severity rule.
- MINOR: relaxes a constraint or adds optional surface: new optional column, enum values added,
  min down, max up, required/unique relaxed, looser thresholds, warn-level rule added, rule removed.
  Consumers should be told, but old data stays valid.
- PATCH: documentation only (description, owner).
Note: relaxing `required` or `unique` is MINOR here although it removes a guarantee from consumers;
a team that treats that as breaking should change the level in `_column_changes`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contract import EXTRA_POLICIES, Column, Contract, parse_semver

LEVELS = {"none": 0, "patch": 1, "minor": 2, "major": 3}
_FROM_LEVEL = {"breaking": "major", "minor": "minor", "patch": "patch"}


@dataclass(frozen=True)
class Change:
    kind: str
    target: str
    detail: str
    level: str  # breaking | minor | patch


@dataclass
class EvolutionResult:
    changes: list[Change]
    required_bump: str
    actual_bump: str
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.messages


def _bound(name: str, old, new, lower_is_tighter: bool) -> list[Change]:
    """min/max style bounds: adding or narrowing is breaking, removing or widening is minor."""
    if old == new:
        return []
    if old is None:
        return [Change("constraint_added", name, f"{name} set to {new}", "breaking")]
    if new is None:
        return [Change("constraint_removed", name, f"{name} {old} removed", "minor")]
    tighter = new < old if lower_is_tighter else new > old
    # for `min`, raising it is tighter; for `max`, lowering it is tighter
    return [Change("constraint_tightened" if tighter else "constraint_loosened", name,
                   f"{name} {old} -> {new}", "breaking" if tighter else "minor")]  # fmt: skip


def _column_changes(old: Column, new: Column) -> list[Change]:
    t, out = old.name, []
    if old.type != new.type:
        out.append(Change("type_changed", t, f"{old.type} -> {new.type}", "breaking"))
    for flag in ("required", "unique"):
        a, b = getattr(old, flag), getattr(new, flag)
        if a != b:
            out.append(
                Change(f"{flag}_changed", t, f"{flag} {a} -> {b}", "breaking" if b else "minor")
            )
    if old.pii != new.pii:
        out.append(
            Change(
                "pii_changed", t, f"pii {old.pii} -> {new.pii} (needs privacy review)", "breaking"
            )
        )
    if old.enum != new.enum:
        if old.enum is None:
            out.append(Change("constraint_added", t, f"enum {list(new.enum)}", "breaking"))
        elif new.enum is None:
            out.append(Change("constraint_removed", t, "enum removed", "minor"))
        else:
            removed, added = set(old.enum) - set(new.enum), set(new.enum) - set(old.enum)
            if removed:
                out.append(
                    Change(
                        "enum_values_removed", t, f"removed {sorted(map(str, removed))}", "breaking"
                    )
                )
            if added:
                out.append(
                    Change("enum_values_added", t, f"added {sorted(map(str, added))}", "minor")
                )
    # min: raising is tighter; max/max_length: lowering is tighter
    out += [Change(c.kind, f"{t}.min", c.detail, c.level)
            for c in _bound("min", old.min, new.min, lower_is_tighter=False)]  # fmt: skip
    out += [Change(c.kind, f"{t}.max", c.detail, c.level)
            for c in _bound("max", old.max, new.max, lower_is_tighter=True)]  # fmt: skip
    out += [Change(c.kind, f"{t}.max_length", c.detail, c.level)
            for c in _bound("max_length", old.max_length, new.max_length, lower_is_tighter=True)]  # fmt: skip
    if old.pattern != new.pattern:
        level = "minor" if new.pattern is None else "breaking"
        out.append(
            Change("pattern_changed", t, f"pattern {old.pattern!r} -> {new.pattern!r}", level)
        )
    if old.description != new.description:
        out.append(Change("description_changed", t, "description updated", "patch"))
    return out


def diff_contracts(old: Contract, new: Contract) -> list[Change]:
    if old.id != new.id:
        return [Change("id_changed", "contract", f"{old.id} -> {new.id}", "breaking")]
    out: list[Change] = []
    if old.format != new.format:
        out.append(Change("format_changed", "format", f"{old.format} -> {new.format}", "breaking"))
    old_cols, new_cols = {c.name: c for c in old.columns}, {c.name: c for c in new.columns}
    for name in old_cols.keys() - new_cols.keys():
        out.append(Change("column_removed", name, "column removed", "breaking"))
    for name in new_cols.keys() - old_cols.keys():
        col = new_cols[name]
        out.append(Change("column_added", name, f"{'required' if col.required else 'optional'} {col.type}",
                          "breaking" if col.required else "minor"))  # fmt: skip
    for name in old_cols.keys() & new_cols.keys():
        out += _column_changes(old_cols[name], new_cols[name])
    removed = {n for n in old_cols if n not in new_cols}
    added = {n: c for n, c in new_cols.items() if n not in old_cols}
    for r in sorted(removed):
        for a, col in added.items():
            if col.type == old_cols[r].type:
                out.append(Change("possible_rename", r, f"{r} -> {a} (same type)", "patch"))

    rank = {p: i for i, p in enumerate(reversed(EXTRA_POLICIES))}  # allow=0 < warn=1 < error=2
    if old.extra_columns != new.extra_columns:
        stricter = rank[new.extra_columns] > rank[old.extra_columns]
        out.append(Change("extra_columns_changed", "extra_columns",
                          f"{old.extra_columns} -> {new.extra_columns}", "breaking" if stricter else "minor"))  # fmt: skip
    for field_, lower_is_tighter in (("min_rows", False), ("max_rows", True)):
        out += [Change(c.kind, f"quality.{field_}", c.detail, c.level)
                for c in _bound(field_, getattr(old, field_), getattr(new, field_), lower_is_tighter)]  # fmt: skip
    o_null, n_null = dict(old.max_null_rate), dict(new.max_null_rate)
    for col in o_null.keys() | n_null.keys():
        out += [Change(c.kind, f"quality.max_null_rate.{col}", c.detail, c.level)
                for c in _bound("max_null_rate", o_null.get(col), n_null.get(col), True)]  # fmt: skip
    if old.freshness != new.freshness:
        if old.freshness and new.freshness and old.freshness.column == new.freshness.column:
            out += [Change(c.kind, "quality.freshness", c.detail, c.level) for c in _bound(
                "max_age_days", old.freshness.max_age_days, new.freshness.max_age_days, True)]  # fmt: skip
        elif new.freshness is None:
            out.append(
                Change("constraint_removed", "quality.freshness", "freshness rule removed", "minor")
            )
        else:
            out.append(
                Change(
                    "constraint_added",
                    "quality.freshness",
                    "freshness rule added/changed",
                    "breaking",
                )
            )
    old_rules, new_rules = (
        {(r.type, r.params): r for r in old.rules},
        {(r.type, r.params): r for r in new.rules},
    )
    for key in old_rules.keys() - new_rules.keys():
        out.append(Change("rule_removed", key[0], dict(key[1]).__repr__(), "minor"))
    for key, rule in new_rules.items():
        if key not in old_rules:
            out.append(Change("rule_added", key[0], f"{rule.severity}: {dict(key[1])}",
                              "breaking" if rule.severity == "error" else "minor"))  # fmt: skip
        elif old_rules[key].severity != rule.severity:
            out.append(Change("rule_severity_changed", key[0], f"{old_rules[key].severity} -> {rule.severity}",
                              "breaking" if rule.severity == "error" else "minor"))  # fmt: skip
    if (old.owner, old.description) != (new.owner, new.description):
        out.append(Change("metadata_changed", "contract", "owner/description updated", "patch"))
    return out


def required_bump(changes: list[Change]) -> str:
    levels = [_FROM_LEVEL[c.level] for c in changes]
    return max(levels, key=LEVELS.__getitem__, default="none")


def actual_bump(old_version: str, new_version: str) -> str:
    o, n = parse_semver(old_version), parse_semver(new_version)
    if n < o:
        return "decrease"
    if n[0] > o[0]:
        return "major"
    if n[1] > o[1]:
        return "minor"
    if n[2] > o[2]:
        return "patch"
    return "none"


def check_evolution(old: Contract, new: Contract) -> EvolutionResult:
    changes = diff_contracts(old, new)
    need, got = required_bump(changes), actual_bump(old.version, new.version)
    messages = []
    if got == "decrease":
        messages.append(f"version went backwards: {old.version} -> {new.version}")
    elif LEVELS[got] < LEVELS[need]:
        messages.append(f"changes require a {need.upper()} bump but version went {old.version} -> "
                        f"{new.version} ({got})")  # fmt: skip
    return EvolutionResult(changes, need, got, messages)
