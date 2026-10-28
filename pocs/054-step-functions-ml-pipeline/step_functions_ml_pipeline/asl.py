"""A small Amazon States Language (ASL) validator and interpreter.

Supported: Task, Pass, Choice, Wait, Succeed, Fail, Map, Parallel; Retry/Catch; InputPath, Parameters (`.$`), ResultPath,
OutputPath with a JSONPath subset (`$`, `$.a.b`, `$.a[0]`); Choice comparison operators and And/Or/Not.
Not supported (rejected by `validate`): intrinsic functions, ItemReader/distributed Map, callbacks, `.waitForTaskToken`.
Semantics follow the AWS documentation; they have NOT been compared with the real service.
"""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

TYPES = {"Task", "Pass", "Choice", "Wait", "Succeed", "Fail", "Map", "Parallel"}
MAX_STEPS = 500
PATH = re.compile(r"^\$(\.[A-Za-z_][A-Za-z0-9_-]*|\[\d+\])*$")


class AslError(ValueError):
    """The state machine definition is invalid."""


class StateError(Exception):
    """A runtime error with an ASL error name (e.g. 'States.TaskFailed' or a custom one)."""

    def __init__(self, error: str, cause: str = ""):
        super().__init__(f"{error}: {cause}")
        self.error, self.cause = error, cause


# --- JSONPath subset -------------------------------------------------------------------------------------------
def _steps(path: str) -> list[str | int]:
    if not PATH.match(path):
        raise StateError("States.Runtime", f"unsupported path {path!r}")
    return [
        int(m[1:-1]) if m.startswith("[") else m[1:]
        for m in re.findall(r"\[\d+\]|\.[A-Za-z_][A-Za-z0-9_-]*", path)
    ]


def get_path(doc: Any, path: str | None) -> Any:
    if path is None:
        return None
    cur = doc
    for step in _steps(path):
        try:
            cur = cur[step]
        except (KeyError, IndexError, TypeError) as e:
            raise StateError("States.Runtime", f"path {path!r} not found") from e
    return cur


def has_path(doc: Any, path: str) -> bool:
    try:
        get_path(doc, path)
        return True
    except StateError:
        return False


def set_path(doc: Any, path: str, value: Any) -> Any:
    if path == "$":
        return value
    out = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    cur, steps = out, _steps(path)
    for s in steps[:-1]:
        cur = cur.setdefault(s, {})
    cur[steps[-1]] = value
    return out


def build_params(template: Any, ctx: Any) -> Any:
    if isinstance(template, dict):
        out = {}
        for k, v in template.items():
            if k.endswith(".$"):
                out[k[:-2]] = get_path(ctx, v)
            else:
                out[k] = build_params(v, ctx)
        return out
    if isinstance(template, list):
        return [build_params(x, ctx) for x in template]
    return template


# --- choice rules ----------------------------------------------------------------------------------------------
def _cmp(rule: dict, data: Any) -> bool:
    if "And" in rule:
        return all(_cmp(r, data) for r in rule["And"])
    if "Or" in rule:
        return any(_cmp(r, data) for r in rule["Or"])
    if "Not" in rule:
        return not _cmp(rule["Not"], data)
    var = rule["Variable"]
    if "IsPresent" in rule:
        return has_path(data, var) == rule["IsPresent"]
    if not has_path(data, var):
        return False
    v = get_path(data, var)
    for op, test in (("StringEquals", lambda a, b: a == b), ("NumericEquals", lambda a, b: a == b), ("NumericGreaterThan", lambda a, b: a > b), ("NumericGreaterThanEquals", lambda a, b: a >= b),
                     ("NumericLessThan", lambda a, b: a < b), ("NumericLessThanEquals", lambda a, b: a <= b), ("BooleanEquals", lambda a, b: a is b), ("StringMatches", lambda a, b: re.fullmatch(re.escape(b).replace(r"\*", ".*"), a) is not None)):  # fmt: skip
        if op in rule:
            if op.startswith("Numeric") and (
                isinstance(v, bool) or not isinstance(v, (int, float))
            ):
                return False
            if op.startswith("String") and not isinstance(v, str):
                return False
            return bool(test(v, rule[op]))
    raise StateError("States.Runtime", f"unsupported choice rule {sorted(rule)}")


# --- validation ------------------------------------------------------------------------------------------------
def validate(defn: dict, resources: set[str] | None = None) -> list[str]:
    errs: list[str] = []
    states = defn.get("States")
    if not isinstance(states, dict) or not states:
        return ["States must be a non-empty object"]
    if defn.get("StartAt") not in states:
        errs.append(f"StartAt {defn.get('StartAt')!r} is not a state")
    for name, st in states.items():
        t = st.get("Type")
        if t not in TYPES:
            errs.append(f"{name}: unknown Type {t!r}")
            continue
        nxt = [st["Next"]] if "Next" in st else []
        if t == "Choice":
            nxt = [c["Next"] for c in st.get("Choices", []) if "Next" in c] + (
                [st["Default"]] if "Default" in st else []
            )
            if not st.get("Choices"):
                errs.append(f"{name}: Choice needs Choices")
            if "Default" not in st:
                errs.append(
                    f"{name}: Choice without Default can fail at runtime with States.NoChoiceMatched"
                )
        elif t in ("Succeed", "Fail"):
            if "Next" in st or "End" in st:
                errs.append(f"{name}: {t} is terminal and cannot have Next/End")
        elif not st.get("End") and "Next" not in st:
            errs.append(f"{name}: needs Next or End")
        if "Next" in st and st.get("End"):
            errs.append(f"{name}: has both Next and End")
        for c in st.get("Catch", []):
            nxt.append(c.get("Next"))
        for target in nxt:
            if target not in states:
                errs.append(f"{name}: Next {target!r} is not a state")
        if t == "Task":
            res = st.get("Resource")
            if not res:
                errs.append(f"{name}: Task needs Resource")
            elif resources is not None and res not in resources:
                errs.append(f"{name}: Resource {res!r} is not registered")
            if ".waitForTaskToken" in str(res):
                errs.append(f"{name}: callback tasks are not supported")
        if t == "Map":
            sub = st.get("ItemProcessor") or st.get("Iterator")
            if not sub:
                errs.append(f"{name}: Map needs ItemProcessor")
            else:
                errs += [f"{name}.{e}" for e in validate(sub, resources)]
            if "ItemReader" in st:
                errs.append(f"{name}: distributed Map (ItemReader) is not supported")
        if t == "Parallel":
            for i, br in enumerate(st.get("Branches", [])):
                errs += [f"{name}.branch{i}.{e}" for e in validate(br, resources)]
        for r in st.get("Retry", []):
            if (
                r.get("MaxAttempts", 3) < 0
                or r.get("BackoffRate", 2.0) < 1.0
                or r.get("IntervalSeconds", 1) < 1
            ):
                errs.append(f"{name}: invalid Retry settings")
        if "Parameters" in st and any(
            isinstance(v, str) and v.startswith("States.") for v in st["Parameters"].values()
        ):
            errs.append(f"{name}: intrinsic functions are not supported")
        for key in ("InputPath", "OutputPath"):
            if isinstance(st.get(key), str) and not PATH.match(st[key]):
                errs.append(f"{name}: unsupported {key} {st[key]!r}")
    # reachability and termination
    seen, stack = set(), [defn.get("StartAt")]
    while stack:
        s = stack.pop()
        if s in seen or s not in states:
            continue
        seen.add(s)
        st = states[s]
        stack += [st["Next"]] if "Next" in st else []
        stack += (
            [c["Next"] for c in st.get("Choices", [])]
            + ([st["Default"]] if "Default" in st else [])
            + [c["Next"] for c in st.get("Catch", [])]
        )
    errs += [f"{n}: unreachable" for n in states if n not in seen]
    if not any(
        states[s].get("Type") in ("Succeed", "Fail") or states[s].get("End")
        for s in seen
        if s in states
    ):
        errs.append("no terminal state reachable")
    return errs


# --- interpreter -----------------------------------------------------------------------------------------------
@dataclass
class Execution:
    status: str = "RUNNING"  # SUCCEEDED | FAILED
    output: Any = None
    error: str = ""
    cause: str = ""
    history: list[tuple[str, str]] = field(default_factory=list)  # (state, event)
    slept: float = 0.0


class Machine:
    def __init__(
        self,
        definition: dict,
        resources: dict[str, Callable[[Any], Any]],
        *,
        sleep: Callable[[float], None] | None = None,
    ):
        errs = validate(definition, set(resources))
        if errs:
            raise AslError("; ".join(errs))
        self.defn, self.resources, self.sleep = definition, resources, sleep or (lambda s: None)

    def start(self, payload: Any) -> Execution:
        ex = Execution()
        try:
            ex.output = self._run(self.defn, payload, ex, depth=0)
            ex.status = "SUCCEEDED"
        except StateError as e:
            ex.status, ex.error, ex.cause = "FAILED", e.error, e.cause
        return ex

    def _run(self, defn: dict, data: Any, ex: Execution, depth: int) -> Any:
        name, steps = defn["StartAt"], 0
        while True:
            steps += 1
            if steps > MAX_STEPS:
                raise StateError("States.Runtime", "step limit exceeded (possible loop)")
            st = defn["States"][name]
            ex.history.append((name, "Entered"))
            t = st["Type"]
            if t == "Fail":
                raise StateError(st.get("Error", "States.Fail"), st.get("Cause", ""))
            if t == "Succeed":
                return self._out(st, data, data)
            if t == "Choice":
                nxt = next((c["Next"] for c in st["Choices"] if _cmp(c, data)), st.get("Default"))
                if nxt is None:
                    raise StateError("States.NoChoiceMatched", name)
                name = nxt
                continue
            try:
                result = self._execute(st, name, data, ex, depth)
            except StateError as e:
                catch = next(
                    (c for c in st.get("Catch", []) if _matches(c["ErrorEquals"], e.error)), None
                )
                if catch is None:
                    raise
                data = set_path(
                    data, catch.get("ResultPath", "$"), {"Error": e.error, "Cause": e.cause}
                )
                ex.history.append((name, f"Caught {e.error}"))
                name = catch["Next"]
                continue
            data = self._out(st, data, result)
            if st.get("End"):
                return data
            name = st["Next"]

    def _out(self, st: dict, raw_in: Any, result: Any) -> Any:
        """ResultPath merges (or discards, when null) the state's result into its input; OutputPath then filters."""
        combined = raw_in
        if st["Type"] in ("Task", "Pass", "Map", "Parallel"):
            combined = (
                raw_in
                if ("ResultPath" in st and st["ResultPath"] is None)
                else set_path(raw_in, st.get("ResultPath", "$"), result)
            )
        return get_path(combined, st["OutputPath"]) if "OutputPath" in st else combined

    def _execute(self, st: dict, name: str, data: Any, ex: Execution, depth: int) -> Any:
        eff = get_path(data, st["InputPath"]) if "InputPath" in st else data
        eff = build_params(st["Parameters"], eff) if "Parameters" in st else eff
        t = st["Type"]
        if t == "Pass":
            return st.get("Result", eff)
        if t == "Wait":
            secs = st.get("Seconds", 0)
            ex.slept += secs
            self.sleep(secs)
            return None  # Wait passes its input through unchanged (_out ignores the result for this state type)
        if t == "Map":
            sub = st.get("ItemProcessor") or st["Iterator"]
            items = get_path(eff, st.get("ItemsPath", "$"))
            if not isinstance(items, list):
                raise StateError("States.Runtime", "ItemsPath did not resolve to an array")
            return [
                self._run(
                    sub,
                    build_params(st["ItemSelector"], {"value": it, "index": i, "context": eff})
                    if "ItemSelector" in st
                    else it,
                    ex,
                    depth + 1,
                )
                for i, it in enumerate(items)
            ]
        if t == "Parallel":
            return [self._run(br, eff, ex, depth + 1) for br in st["Branches"]]
        return self._task(st, name, eff, ex)

    def _task(self, st: dict, name: str, eff: Any, ex: Execution) -> Any:
        attempts: dict[int, int] = {}
        while True:
            try:
                try:
                    return self.resources[st["Resource"]](eff)
                except StateError:
                    raise
                except TimeoutError as e:
                    raise StateError("States.Timeout", str(e)) from e
                except (
                    Exception
                ) as e:  # custom error name = exception class name, like Lambda error types
                    raise StateError(type(e).__name__, str(e)) from e
            except StateError as e:
                idx = next(
                    (
                        i
                        for i, r in enumerate(st.get("Retry", []))
                        if _matches(r["ErrorEquals"], e.error)
                    ),
                    None,
                )
                if idx is None:
                    raise
                r = st["Retry"][idx]
                attempts[idx] = attempts.get(idx, 0) + 1
                if attempts[idx] > r.get("MaxAttempts", 3):
                    raise
                wait = r.get("IntervalSeconds", 1) * (
                    r.get("BackoffRate", 2.0) ** (attempts[idx] - 1)
                )
                ex.history.append((name, f"Retry {attempts[idx]} after {wait:g}s on {e.error}"))
                ex.slept += wait
                self.sleep(wait)


def _matches(names: list[str], error: str) -> bool:
    """States.Runtime can never be retried or caught. States.ALL matches everything else; States.TaskFailed matches any task error except timeouts."""
    if error == "States.Runtime":
        return False
    return (
        "States.ALL" in names
        or error in names
        or ("States.TaskFailed" in names and error != "States.Timeout")
    )


def real_sleep(s: float) -> None:
    time.sleep(s)
