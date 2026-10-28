"""Static policy: reject code before it runs (layer 1)."""

from __future__ import annotations

import ast

ALLOWED_IMPORTS = frozenset(
    {
        "math",
        "json",
        "re",
        "statistics",
        "itertools",
        "collections",
        "functools",
        "datetime",
        "random",
        "decimal",
        "fractions",
        "string",
        "heapq",
        "bisect",
        "operator",
    }
)
DENIED_NAMES = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "open",
        "__import__",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "memoryview",
        "help",
        "exit",
        "quit",
    }
)


class PolicyViolation(Exception):
    pass


def check(code: str, max_chars: int = 20_000) -> None:
    if len(code) > max_chars:
        raise PolicyViolation(f"code too long ({len(code)} > {max_chars})")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise PolicyViolation(f"syntax error: {e.msg}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                _import(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise PolicyViolation("relative import")
            _import(node.module or "")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise PolicyViolation(f"dunder attribute access: {node.attr}")
        elif isinstance(node, ast.Name) and (
            node.id in DENIED_NAMES or (node.id.startswith("__") and node.id.endswith("__"))
        ):
            raise PolicyViolation(f"forbidden name: {node.id}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            continue


def _import(name: str) -> None:
    if name.split(".")[0] not in ALLOWED_IMPORTS:
        raise PolicyViolation(f"import of '{name}' is not allowed")
