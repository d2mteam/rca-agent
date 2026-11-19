"""When one component started to deviate, and whether the components calling it deviated later.

A verification tool for a hypothesis, after OpenRCA 2.0 (Fang et al., 2026), which checks a candidate
propagation path by severity levels against a pre-fault baseline and by temporal order: a downstream
anomaly must not start before its upstream cause. It is not a detector: scanned over every series of
every component, the same test is swamped by near-constant and drifting series.

For each series of the component (its metrics, its exclusive times, the mean duration of its spans):
- a series that never decreases in the hour before is a counter and is tested on its differences;
- the baseline of a point is the 15 samples before it (at least 8); a baseline sitting on one value
  for half its samples or more is tested with percentile levels (critical > P99.9 or 2 x P99, warning
  > P99 or 1.5 x P99, moderate > P90 or 1.2 x P99), any other with z levels (3, 2, 1.5), upwards;
- a deviation is 2 or more consecutive samples at warning or above against the baseline of the
  first; its onset is moved back over the moderate samples just before it (FChain, ICDCS 2013).
Callers come from the traces, transitively; each is judged on the mean duration of its spans.
"""

from collections import defaultdict
from dataclasses import dataclass

import duckdb
import numpy as np

from rca import exclusive_time
from rca.cases import Case

BASELINE, MIN_BASELINE = 15, 8
LEVELS = ("", "moderate", "warning", "critical")

SPAN_DURATION = """
SELECT cmdb_id, CAST(floor(ts / 60) * 60 AS BIGINT) AS minute, avg(duration)
FROM trace WHERE ts >= ? AND ts < ? GROUP BY ALL ORDER BY minute
"""


@dataclass(frozen=True)
class Deviation:
    series: str
    onset: int  # epoch seconds
    samples: int  # consecutive samples at warning or above
    level: int  # 2 warning, 3 critical
    baseline: float  # median of the baseline
    peak: float
    differenced: bool


def _leveller(base: np.ndarray):
    values, counts = np.unique(base, return_counts=True)
    if counts.max() >= len(base) / 2:
        usual = values[counts.argmax()]
        p90, p99, p999 = np.percentile(base, [90, 99, 99.9])

        def by_percentile(x: float) -> int:
            if x == usual:
                return 0
            if x > p999 or x > 2 * p99:
                return 3
            if x > p99 or x > 1.5 * p99:
                return 2
            return 1 if x > p90 or x > 1.2 * p99 else 0
        return by_percentile
    mean, std = base.mean(), base.std()
    if std == 0:
        return lambda x: 0
    return lambda x: 3 if (x - mean) / std > 3 else 2 if (x - mean) / std > 2 else 1 if (x - mean) / std > 1.5 else 0


def deviation(name: str, points: list[tuple[int, float]], start: int, end: int) -> Deviation | None:
    """The longest deviation of one series inside [start, end), or None."""
    points = sorted(points)
    times = np.array([t for t, _ in points])
    values = np.array([v for _, v in points], dtype=float)
    before = values[times < start]
    differenced = len(before) > 1 and bool(np.all(np.diff(before) >= 0) and np.any(np.diff(before) > 0))
    if differenced:
        values, times = np.diff(values), times[1:]
    best = None
    for i in np.nonzero((times >= start) & (times < end))[0]:
        base = values[max(0, i - BASELINE):i]
        if len(base) < MIN_BASELINE or i + 1 >= len(values) or times[i + 1] >= end:
            continue
        level = _leveller(base)
        if level(values[i]) < 2 or level(values[i + 1]) < 2:
            continue
        j = i + 1
        while j + 1 < len(values) and times[j + 1] < end and level(values[j + 1]) >= 2:
            j += 1
        k = i
        while k - 1 >= 0 and times[k - 1] >= start and level(values[k - 1]) >= 1:
            k -= 1
        found = Deviation(name, int(times[k]), j - i + 1, max(level(v) for v in values[i:j + 1]),
                          float(np.median(base)), float(values[i:j + 1].max()), differenced)
        if best is None or (found.samples, found.level) > (best.samples, best.level):
            best = found
    return best


def component_series(con: duckdb.DuckDBPyConnection, case: Case, component: str) -> dict[str, list]:
    series: dict[str, list] = defaultdict(list)
    for kpi, minute, value in con.execute(
        """SELECT kpi_name, CAST(floor(ts / 60) * 60 AS BIGINT), avg(value) FROM metric_container
           WHERE cmdb_id = ? AND ts >= ? AND ts < ? GROUP BY ALL""",
        [component, case.start - 3600, case.end],
    ).fetchall():
        series[kpi].append((minute, value))
    for comp, kind, minute, value in con.execute(exclusive_time.SERIES, [case.start - 3600, case.end]).fetchall():
        if comp == component:
            series[f"exclusive {kind} (traces)"].append((minute, value))
    for comp, minute, value in con.execute(SPAN_DURATION, [case.start - 3600, case.end]).fetchall():
        if comp == component:
            series["span duration (traces)"].append((minute, value))
    return series


def deviations(con: duckdb.DuckDBPyConnection, case: Case, component: str, pattern: str | None = None) -> list[Deviation]:
    found = []
    for name, points in component_series(con, case, component).items():
        if pattern and pattern.lower() not in name.lower():
            continue
        d = deviation(name, points, case.start, case.end)
        if d:
            found.append(d)
    return sorted(found, key=lambda d: (d.onset, -d.samples))


def callers(con: duckdb.DuckDBPyConnection, case: Case, component: str) -> dict[str, int]:
    """Components calling `component` in the window, directly or through others, with their distance."""
    direct: dict[str, set] = defaultdict(set)
    for caller, callee in con.execute(
        """WITH s AS (SELECT trace_id, span_id, parent_id, cmdb_id, call_target FROM trace WHERE ts >= ? AND ts < ?)
           SELECT DISTINCT p.cmdb_id, c.cmdb_id FROM s p JOIN s c
           ON c.trace_id = p.trace_id AND c.parent_id = p.span_id AND c.span_id <> c.parent_id
           WHERE c.cmdb_id <> p.cmdb_id
           UNION SELECT DISTINCT cmdb_id, call_target FROM s WHERE call_target IS NOT NULL""",
        [case.start, case.end],
    ).fetchall():
        direct[callee].add(caller)
    distance, frontier = {}, [component]
    hops = 0
    while frontier:
        hops += 1
        nxt = []
        for node in frontier:
            for up in direct.get(node, ()):
                if up != component and up not in distance:
                    distance[up] = hops
                    nxt.append(up)
        frontier = nxt
    return distance


def caller_onsets(con: duckdb.DuckDBPyConnection, case: Case, component: str) -> list[tuple[str, int, Deviation | None]]:
    by_component: dict[str, list] = defaultdict(list)
    for comp, minute, value in con.execute(SPAN_DURATION, [case.start - 3600, case.end]).fetchall():
        by_component[comp].append((minute, value))
    return [(caller, hops, deviation("span duration (traces)", by_component.get(caller, []), case.start, case.end))
            for caller, hops in sorted(callers(con, case, component).items(), key=lambda kv: (kv[1], kv[0]))]
