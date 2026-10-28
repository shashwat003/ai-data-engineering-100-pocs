from __future__ import annotations

import statistics
import time

from .detectors import AdwinShift, EwmaZ, Hst, MissingRun, RobustZ, SeasonalEwma
from .streams import PERIOD, Event, make_stream

FACTORIES = {
    "EWMA z-score": EwmaZ,
    "Rolling median/MAD": RobustZ,
    "Seasonal EWMA": SeasonalEwma,
    "river HalfSpaceTrees": Hst,
    "river ADWIN (drift)": AdwinShift,
    "Missing-data run rule": MissingRun,
}
FA_PER_1000 = 1.0  # false-alarm budget used to set every threshold on clean data


def scores(factory, xs) -> list[float]:
    d = factory()
    return [d.update(x, t) for t, x in enumerate(xs)]


def calibrate(factory, seeds=(101, 102, 103)) -> float:
    """Threshold giving at most FA_PER_1000 alarm EVENTS per 1000 points on anomaly-free streams (after the clean warm-up days)."""
    allscores = []
    for s in seeds:
        xs, _ = make_stream(s, anomalies=False)
        allscores.append(scores(factory, xs)[2 * PERIOD :])
    flat = sorted(v for sc in allscores for v in sc)
    if hasattr(
        factory, "fixed_threshold"
    ):  # rules with a meaningful natural threshold (calibration would give none)
        return factory.fixed_threshold
    if set(flat) <= {0.0, 1.0}:  # binary detector: alarm whenever it fires
        return 0.5
    for q in (0.99, 0.995, 0.998, 0.999, 0.9995, 1.0):
        thr = flat[min(len(flat) - 1, int(q * len(flat)))]
        ev = sum(len(alarm_events([i for i, v in enumerate(sc) if v > thr])) for sc in allscores)
        if ev / (sum(len(sc) for sc in allscores) / 1000) <= FA_PER_1000:
            return thr
    return flat[-1]


def alarm_events(idx: list[int], gap: int = 12) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for i in idx:
        if out and i - out[-1][1] <= gap:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(a, b) for a, b in out]


def match(events: list[Event], alarms: list[tuple[int, int]], tol: int = 24) -> dict:
    """An event is detected if an alarm overlaps [start - tol, end + tol]. Alarms matching no event are false."""
    used, delays, found = set(), [], {}
    for ev in events:
        hit = next(
            (k for k, (a, b) in enumerate(alarms) if a <= ev.end + tol and b >= ev.start - 0), None
        )
        found[ev] = hit is not None
        if hit is not None:
            used.add(hit)
            delays.append(max(0, alarms[hit][0] - ev.start))
    false = [
        k
        for k in range(len(alarms))
        if k not in used
        and not any(alarms[k][0] <= e.end + tol and alarms[k][1] >= e.start for e in events)
    ]
    return {"found": found, "delays": delays, "false_alarms": len(false)}


def run(seeds=(1, 2, 3, 4, 5)) -> dict:
    out = {}
    for name, fac in FACTORIES.items():
        thr = calibrate(fac)
        per_kind: dict[str, list[bool]] = {}
        delays, fa, n_pts, tp, n_events, secs = [], 0, 0, 0, 0, 0.0
        for s in seeds:
            xs, events = make_stream(s)
            t0 = time.perf_counter()
            sc = scores(fac, xs)
            secs += time.perf_counter() - t0
            alarms = alarm_events([i for i, v in enumerate(sc) if v > thr and i >= 2 * PERIOD])
            m = match(events, alarms)
            for ev, ok in m["found"].items():
                per_kind.setdefault(ev.kind, []).append(ok)
                tp += ok
            n_events += len(events)
            delays += m["delays"]
            fa += m["false_alarms"]
            n_pts += len(xs) - 2 * PERIOD
        out[name] = {"threshold": round(thr, 3), "event_recall": round(tp / n_events, 3), "false_alarms_per_1000": round(fa / (n_pts / 1000), 2),
                     "median_delay_points": statistics.median(delays) if delays else None, "recall_by_kind": {k: round(sum(v) / len(v), 2) for k, v in sorted(per_kind.items())},
                     "points_per_second": round(len(seeds) * len(xs) / secs)}  # fmt: skip
    return {
        "seeds": list(seeds),
        "n_events": n_events,
        "false_alarm_budget_per_1000": FA_PER_1000,
        "detectors": out,
    }
