"""TraceRCA-style localisation: blame components that abnormal traces pass and normal traces avoid.

A simplified take on "Practical Root Cause Localization for Microservice
Systems via Trace Analysis" (TraceRCA):
1. A span is slow when its duration exceeds mean + 3 std of the same
   component and span side (outgoing call or server-side work) during the
   hour before the query window.
2. A trace in the window is abnormal when any of its spans is slow.
3. A component scores the Jaccard index between the abnormal traces and the
   traces passing it: abnormal_with / (abnormal_total + normal_with).

Only components that appear in traces can be ranked; on Bank that leaves out
apache, Mysql and Redis.
"""

from dataclasses import dataclass

import duckdb

from rca.cases import Case


@dataclass(frozen=True)
class Suspect:
    component: str
    score: float
    abnormal_traces: int
    normal_traces: int


def rank_components(con: duckdb.DuckDBPyConnection, case: Case, sigmas: float = 3.0) -> list[Suspect]:
    rows = con.execute(
        """
        WITH ref AS (
            SELECT cmdb_id, span_id LIKE '369-%' AS outgoing,
                   avg(duration) AS mean, stddev_pop(duration) AS std
            FROM trace WHERE ts >= ? AND ts < ?
            GROUP BY ALL
        ),
        spans AS (
            SELECT t.trace_id, t.cmdb_id,
                   t.duration > r.mean + ? * greatest(r.std, 1) AS slow
            FROM trace t JOIN ref r ON r.cmdb_id = t.cmdb_id AND r.outgoing = (t.span_id LIKE '369-%')
            WHERE t.ts >= ? AND t.ts < ?
        ),
        traces AS (
            SELECT trace_id, bool_or(slow) AS abnormal FROM spans GROUP BY trace_id
        ),
        passes AS (
            SELECT DISTINCT s.trace_id, s.cmdb_id, tr.abnormal
            FROM spans s JOIN traces tr USING (trace_id)
        )
        SELECT cmdb_id,
               count(*) FILTER (WHERE abnormal) AS abnormal_with,
               count(*) FILTER (WHERE NOT abnormal) AS normal_with,
               (SELECT count(*) FROM traces WHERE abnormal) AS abnormal_total
        FROM passes GROUP BY cmdb_id
        """,
        [case.start - 3600, case.start, sigmas, case.start, case.end],
    ).fetchall()
    suspects = [
        Suspect(component, ab / (total + normal) if total + normal else 0.0, ab, normal)
        for component, ab, normal, total in rows
    ]
    return sorted(suspects, key=lambda s: s.score, reverse=True)
