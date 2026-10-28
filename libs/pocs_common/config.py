"""Minimal, dependency-free configuration helpers. Values come from the environment or a .env file."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (ignores blanks and # comments). Returns {} if the file is absent."""
    p = Path(path)
    if not p.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def get_setting(
    name: str, default: str | None = None, env_file: str | Path | None = None
) -> str | None:
    """Resolve a setting: process environment first, then env_file, then default."""
    if name in os.environ:
        return os.environ[name]
    if env_file is not None:
        return load_env_file(env_file).get(name, default)
    return default
