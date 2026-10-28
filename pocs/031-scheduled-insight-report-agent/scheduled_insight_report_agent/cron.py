"""Minimal 5-field cron (minute hour day-of-month month day-of-week) with catch-up of missed ticks."""

from __future__ import annotations

from datetime import datetime, timedelta

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]  # day-of-week: 0 = Sunday


def _field(text: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise ValueError("step must be >= 1")
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
            if "/" in text and step > 1:
                b = hi
        if not (lo <= a <= b <= hi):
            raise ValueError(f"field value out of range {lo}-{hi}: {text}")
        out.update(range(a, b + 1, step))
    return out


class Cron:
    def __init__(self, expr: str):
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError("cron needs 5 fields")
        self.sets = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, RANGES, strict=True)]
        self.dom_star, self.dow_star = parts[2] == "*", parts[4] == "*"
        self.expr = expr

    def matches(self, t: datetime) -> bool:
        mi, hr, dom, mon, dow = self.sets
        if t.minute not in mi or t.hour not in hr or t.month not in mon:
            return False
        dom_ok, dow_ok = t.day in dom, (t.isoweekday() % 7) in dow
        if self.dom_star or self.dow_star:  # an unrestricted day field does not widen the other
            return dom_ok and dow_ok
        return dom_ok or dow_ok  # both restricted: either may match (Vixie cron)

    def ticks(self, after: datetime, upto: datetime, limit: int = 1000) -> list[datetime]:
        """Scheduled instants t with after < t <= upto, at minute resolution."""
        t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        out = []
        while t <= upto and len(out) < limit:
            if self.matches(t):
                out.append(t)
            t += timedelta(minutes=1)
        return out
