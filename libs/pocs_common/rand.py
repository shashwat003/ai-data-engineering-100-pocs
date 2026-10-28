"""Process-independent deterministic randomness (Python's built-in hash() is salted per process)."""

from __future__ import annotations

import hashlib
from statistics import NormalDist

_NORMAL = NormalDist()


def stable_unit(*parts: object) -> float:
    """Deterministic float in [0, 1) derived from the parts."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def stable_normal(*parts: object) -> float:
    """Deterministic standard-normal draw derived from the parts."""
    u = min(max(stable_unit(*parts), 1e-9), 1 - 1e-9)
    return _NORMAL.inv_cdf(u)
