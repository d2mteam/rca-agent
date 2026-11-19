"""Exclusive time per component, after CRISP's critical-path analysis (Zhang et al., USENIX ATC 2022).

CRISP attributes a request's latency along its critical path and keeps, for every span, only its
exclusive time: the part not spent waiting on the spans it calls. A caller waiting on a slow
callee is then not blamed for the callee's time.

On Bank the critical path is the whole trace: calls are serial (about 4 % of spans have children
overlapping in time) and on 2021-03-23 to 03-25 start times are kept to the second only, too coarse
to order children. A span's exclusive time is therefore its duration minus its children's
durations, at least 0. Each duration is measured on one host, so the clock skew CRISP corrects for
does not arise.

Every traced component gets two minute series over the hour before the window and the window:
  processing  mean exclusive time of its server spans: work done inside it
  network in  mean exclusive time of the call spans that reach it, i.e. the caller's call minus
              the component's server span: time spent on the network and in its queue
A call whose callee leaves no span but is named by the trace (Telecom's JDBC calls name their
database) counts whole as the callee's network in: the database's own work cannot be told apart.
Telecom traces are taken to be serial too; that is not checked.
A series scores (x - median) / IQR per minute against the hour before; durations are whole
milliseconds, so an IQR below 1 ms counts as 1 ms.
Its change is the run of consecutive minutes scoring 3 or more that holds its peak. Components
rank by their highest-scoring change. Components outside the answer space (Bank's docker) are
left out, as are calls whose callee is neither in the trace nor named by it.
"""

from dataclasses import dataclass

import duckdb
import numpy as np

from rca import systems
from rca.cases import Case

THRESHOLD = 3.0
MIN_REFERENCE_MINUTES = 10
RESOLUTION_MS = 1.0

SERIES = """
WITH s AS (SELECT trace_id, span_id, parent_id, cmdb_id, is_call, call_target, duration, ts
           FROM trace WHERE ts >= ? AND ts < ?),
     children AS (SELECT trace_id, parent_id AS span_id, sum(duration) AS busy, any_value(cmdb_id) AS callee
                  FROM s WHERE parent_id <> span_id GROUP BY ALL)
SELECT CASE WHEN s.is_call THEN coalesce(c.callee, s.call_target) ELSE s.cmdb_id END AS component,
       CASE WHEN s.is_call THEN 'network in' ELSE 'processing' END AS kind,
       CAST(floor(s.ts / 60) * 60 AS BIGINT) AS minute,
       avg(greatest(s.duration - coalesce(c.busy, 0), 0)) AS exclusive_ms
FROM s LEFT JOIN children c USING (trace_id, span_id)
WHERE NOT s.is_call OR coalesce(c.callee, s.call_target) IS NOT NULL
GROUP BY ALL
ORDER BY component, kind, minute
"""


@dataclass(frozen=True)
class Change:
    component: str
    kind: str  # "processing" or "network in"
    normal_ms: float  # median of the hour before
    peak_ms: float
    score: float
    start: int  # first and last minute of the change, epoch seconds
    end: int


def changes(con: duckdb.DuckDBPyConnection, case: Case) -> list[Change]:
    """Every series whose exclusive time rose 3 or more, strongest first."""
    series: dict[tuple[str, str], list[tuple[int, float]]] = {}
    components = systems.get(case.system).components
    for component, kind, minute, value in con.execute(SERIES, [case.start - 3600, case.end]).fetchall():
        if component in components:
            series.setdefault((component, kind), []).append((minute, value))
    found = []
    for (component, kind), points in series.items():
        reference = np.array([v for m, v in points if m < case.start])
        window = [(m, v) for m, v in points if m >= case.start]
        if len(reference) < MIN_REFERENCE_MINUTES or not window:
            continue
        centre = float(np.median(reference))
        iqr = max(float(np.percentile(reference, 75) - np.percentile(reference, 25)), RESOLUTION_MS)
        scores = [(v - centre) / iqr for _, v in window]
        peak = int(np.argmax(scores))
        if scores[peak] < THRESHOLD:
            continue
        first = last = peak
        while first > 0 and scores[first - 1] >= THRESHOLD and window[first][0] - window[first - 1][0] == 60:
            first -= 1
        while last + 1 < len(window) and scores[last + 1] >= THRESHOLD and window[last + 1][0] - window[last][0] == 60:
            last += 1
        found.append(Change(component, kind, centre, window[peak][1], scores[peak], window[first][0], window[last][0]))
    return sorted(found, key=lambda c: c.score, reverse=True)


def rank_components(con: duckdb.DuckDBPyConnection, case: Case) -> list[tuple[str, float]]:
    best: dict[str, float] = {}
    for change in changes(con, case):
        best.setdefault(change.component, change.score)
    return list(best.items())
