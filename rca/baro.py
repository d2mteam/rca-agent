"""BARO (Pham et al., FSE'24) on one OpenRCA case, with the authors' fse-baro package.

1. Build a one-minute table over the hour before the window plus the window:
   every metric_container series of a candidate component, and for every
   business service its mean response time as `<service>_latency` and its
   failure rate (100 - sr) as `<service>_error`.
2. Multivariate BOCPD over the latency/error columns finds change points; the
   first one inside the query window is the root cause time. Without one, the
   window start splits normal from abnormal.
3. RobustScorer ranks every column by max((x - median) / IQR) after the split.
   This follows the authors' fse-baro package: increases only, and a series
   constant before the split is dropped. The paper (Eq. 3, Algorithm 1) scores
   |x - median| / IQR instead, counting drops too; two_sided=True does that.
   The top columns of candidate components whose KPI maps to a reason give
   the component and the reason.

Needs the optional group: `uv run --group baro python -m rca baseline baro`.
"""

from dataclasses import dataclass
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
from baro.anomaly_detection import bocpd
from baro.root_cause_analysis import preprocess

from rca import bank
from rca.cases import CST, Case
from rca.scoring import TIME_FORMAT, Answer
from rca.statistical import reason_for


def minute_table(con: duckdb.DuckDBPyConnection, start: int, end: int) -> pd.DataFrame:
    components = list(bank.COMPONENTS)
    marks = ", ".join("?" * len(components))
    long = con.execute(
        f"""
        SELECT CAST(floor(ts / 60) * 60 AS BIGINT) AS time, cmdb_id || '|' || kpi_name AS col, avg(value) AS value
        FROM metric_container WHERE ts >= ? AND ts < ? AND cmdb_id IN ({marks}) GROUP BY ALL
        UNION ALL
        SELECT CAST(floor(ts / 60) * 60 AS BIGINT), tc || '_latency', avg(mrt) FROM metric_app WHERE ts >= ? AND ts < ? GROUP BY ALL
        UNION ALL
        SELECT CAST(floor(ts / 60) * 60 AS BIGINT), tc || '_error', avg(100 - sr) FROM metric_app WHERE ts >= ? AND ts < ? GROUP BY ALL
        """,
        [start, end, *components, start, end, start, end],
    ).df()
    wide = long.pivot_table(index="time", columns="col", values="value").sort_index()
    # Container metrics arrive every 60-120 s; carry the last value over empty minutes.
    return wide.ffill().bfill().reset_index()


@dataclass(frozen=True)
class Ranked:
    column: str
    score: float
    onset: int
    peak: float
    centre: float


def rank_series(con: duckdb.DuckDBPyConnection, case: Case, two_sided: bool = False) -> tuple[int, list[Ranked]]:
    """Split time from BOCPD and every column scored as RobustScorer does, highest first.

    The score is BARO's max((x - median) / IQR) after the split, with an IQR of 0
    counting as 1; onset is the first row after the split scoring 3 or more.
    """
    data = minute_table(con, case.start - 3600, case.end)
    window_rows = [i for i, t in enumerate(data["time"]) if t >= case.start]
    # bocpd reports a change first seen in row i as i + 1, and always reports len(data).
    changed_rows = [i - 1 for i in bocpd(data) if i < len(data)]
    change_points = [i for i in changed_rows if data["time"].iloc[i] >= case.start]
    split = change_points[0] if change_points else window_rows[0]
    split_time = int(data["time"].iloc[split])

    times = data["time"].to_numpy()[split:]
    normal, abnormal = preprocess(data.head(split)), preprocess(data.tail(len(data) - split))
    ranked = []
    for column in (c for c in normal.columns if c in abnormal.columns):
        a, b = normal[column].to_numpy(), abnormal[column].to_numpy()
        centre = float(np.median(a))
        iqr = float(np.percentile(a, 75) - np.percentile(a, 25)) or 1.0
        scores = np.abs(b - centre) / iqr if two_sided else (b - centre) / iqr
        peak = int(np.argmax(scores))
        onset = next((i for i, s in enumerate(scores) if s >= 3), peak)
        ranked.append(Ranked(column, float(scores[peak]), int(times[onset]), float(b[peak]), centre))
    return split_time, sorted(ranked, key=lambda r: r.score, reverse=True)


def answer(con: duckdb.DuckDBPyConnection, case: Case) -> list[Answer]:
    split_time, ranked = rank_series(con, case)
    chosen: list[tuple[str, str]] = []
    for r in ranked:
        component, _, kpi = r.column.partition("|")
        reason = reason_for(kpi)
        if component in bank.COMPONENTS and reason and component not in {c for c, _ in chosen}:
            chosen.append((component, reason))
        if len(chosen) == len(case.faults):
            break
    time = datetime.fromtimestamp(split_time, CST).strftime(TIME_FORMAT)
    return [Answer(time, component, reason) for component, reason in chosen]
