"""Pipeline configuration: strict YAML schema, cron check, dependency-cycle detection. Pure Python (no Airflow import)."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DAG_ID = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
TASK_ID = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
PRESETS = {"@hourly", "@daily", "@weekly", "@monthly", "@yearly"}
TOP = {
    "dag_id",
    "description",
    "schedule",
    "owner",
    "tags",
    "retries",
    "retry_delay_minutes",
    "max_active_runs",
    "task_timeout_minutes",
    "tasks",
}
TASK_KEYS = {"id", "type", "callable", "command", "params", "depends_on"}
FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
MAX_TASKS = 50


class ConfigError(ValueError):
    """Lists every problem found in one config document."""


@dataclass(frozen=True)
class TaskCfg:
    id: str
    type: str
    target: str  # callable name or allow-listed command key
    params: dict
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class PipelineCfg:
    dag_id: str
    description: str
    schedule: str | None
    owner: str
    tags: tuple[str, ...]
    retries: int
    retry_delay_minutes: int
    max_active_runs: int
    task_timeout_minutes: int
    tasks: tuple[TaskCfg, ...] = field(default_factory=tuple)


def _cron_ok(expr: str) -> bool:
    parts = expr.split()
    if len(parts) != 5:
        return False
    for part, (lo, hi) in zip(parts, FIELD_RANGES, strict=True):
        for item in part.split(","):
            m = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", item)
            if not m:
                return False
            base, step = m.groups()
            if step is not None and int(step) < 1:
                return False
            if base != "*":
                a, _, b = base.partition("-")
                if not lo <= int(a) <= hi or (b and not (int(a) <= int(b) <= hi)):
                    return False
    return True


def _has_cycle(tasks: list[TaskCfg]) -> list[str]:
    indeg = {t.id: len([d for d in t.depends_on if d in {x.id for x in tasks}]) for t in tasks}
    children: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for d in t.depends_on:
            if d in children:
                children[d].append(t.id)
    ready = [k for k, v in indeg.items() if v == 0]
    done = 0
    while ready:
        n = ready.pop()
        done += 1
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
    return sorted(k for k, v in indeg.items() if v > 0) if done != len(tasks) else []


def parse_pipeline(
    text: str, *, callables: dict[str, Callable], commands: dict[str, str]
) -> PipelineCfg:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("pipeline must be a mapping")
    errs: list[str] = []
    for k in sorted(set(raw) - TOP):
        errs.append(f"unknown key {k!r}")
    dag_id = str(raw.get("dag_id", ""))
    if not DAG_ID.match(dag_id):
        errs.append("dag_id must be snake_case, 3-61 chars")
    sched = raw.get("schedule")
    if sched is not None and not (isinstance(sched, str) and (sched in PRESETS or _cron_ok(sched))):
        errs.append(
            f"schedule {sched!r} is neither a valid 5-field cron expression nor one of {sorted(PRESETS)} (use null for manual)"
        )
    if not raw.get("owner") or not isinstance(raw.get("owner"), str):
        errs.append("owner is required")

    def intv(key, default, lo, hi):
        v = raw.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            errs.append(f"{key} must be an integer in [{lo}, {hi}]")
            return default
        return v

    retries, delay = intv("retries", 1, 0, 5), intv("retry_delay_minutes", 5, 1, 120)
    active, timeout = intv("max_active_runs", 1, 1, 10), intv("task_timeout_minutes", 30, 1, 360)
    tags = raw.get("tags") or []
    if (
        not isinstance(tags, list)
        or len(tags) > 5
        or not all(isinstance(t, str) and re.fullmatch(r"[a-z0-9_-]{1,30}", t) for t in tags)
    ):
        errs.append("tags must be at most 5 lowercase slugs")
        tags = []
    tasks: list[TaskCfg] = []
    rawtasks = raw.get("tasks")
    if not isinstance(rawtasks, list) or not rawtasks:
        errs.append("tasks must be a non-empty list")
        rawtasks = []
    if len(rawtasks) > MAX_TASKS:
        errs.append(f"at most {MAX_TASKS} tasks")
        rawtasks = rawtasks[:MAX_TASKS]
    seen: set[str] = set()
    for i, t in enumerate(rawtasks):
        w = f"tasks[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{w}: must be a mapping")
            continue
        for k in sorted(set(t) - TASK_KEYS):
            errs.append(f"{w}: unknown key {k!r}")
        tid = str(t.get("id", ""))
        if not TASK_ID.match(tid):
            errs.append(f"{w}: bad id")
        if tid in seen:
            errs.append(f"{w}: duplicate id {tid!r}")
        seen.add(tid)
        typ, params = t.get("type"), t.get("params") or {}
        deps = t.get("depends_on") or []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            errs.append(f"{w}: depends_on must be a list of ids")
            deps = []
        if typ == "python":
            fn = callables.get(t.get("callable"))
            if fn is None:
                errs.append(
                    f"{w}: callable {t.get('callable')!r} is not in the registry {sorted(callables)}"
                )
            elif isinstance(params, dict):
                sig = inspect.signature(fn)
                extra = set(params) - set(sig.parameters)
                missing = {p.name for p in sig.parameters.values() if p.default is p.empty} - set(
                    params
                )
                if extra:
                    errs.append(f"{w}: params {sorted(extra)} not accepted by {t['callable']}")
                if missing:
                    errs.append(f"{w}: missing params {sorted(missing)} for {t['callable']}")
            target = str(t.get("callable"))
        elif typ == "bash":
            if t.get("command") not in commands:
                errs.append(
                    f"{w}: command {t.get('command')!r} is not allow-listed {sorted(commands)} (arbitrary shell is never accepted from config)"
                )
            target = str(t.get("command"))
        else:
            errs.append(f"{w}: type must be python or bash")
            target = ""
        if not isinstance(params, dict) or not all(
            isinstance(v, (str, int, float, bool)) for v in params.values()
        ):
            errs.append(f"{w}: params must be a flat mapping of scalars")
            params = {}
        tasks.append(TaskCfg(tid, str(typ), target, dict(params), tuple(deps)))
    ids = {t.id for t in tasks}
    for t in tasks:
        for d in t.depends_on:
            if d not in ids:
                errs.append(f"task {t.id}: depends_on unknown task {d!r}")
            if d == t.id:
                errs.append(f"task {t.id}: depends on itself")
    cyc = _has_cycle(tasks) if not errs else []
    if cyc:
        errs.append(f"dependency cycle among {cyc}")
    if errs:
        raise ConfigError("; ".join(errs))
    return PipelineCfg(
        dag_id,
        str(raw.get("description", "")),
        sched,
        raw["owner"],
        tuple(tags),
        retries,
        delay,
        active,
        timeout,
        tuple(tasks),
    )


def load_all(directory: str | Path, *, callables, commands) -> list[PipelineCfg]:
    out, ids = [], set()
    for f in sorted(Path(directory).glob("*.yaml")):
        try:
            cfg = parse_pipeline(f.read_text(), callables=callables, commands=commands)
        except ConfigError as e:
            raise ConfigError(f"{f.name}: {e}") from e
        if cfg.dag_id in ids:
            raise ConfigError(f"{f.name}: duplicate dag_id {cfg.dag_id}")
        ids.add(cfg.dag_id)
        out.append(cfg)
    return out
