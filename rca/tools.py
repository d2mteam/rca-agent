"""Investigation tools an LLM agent calls from the shell, one case at a time.

Every tool sees only the query and the telemetry; none of them reveals the
graded faults. The normal level comes from the hour before the window, or from
the same clock time on 2021-03-05 when that hour is not loaded.

  question  the query, the answer space and how to submit
  overview  symptoms: business KPIs per minute, log and span volume vs normal
  anomalies metric series ranked by z-score against the normal level
  series    one component's KPIs minute by minute
  traces    MicroRank and TraceRCA rankings of traced components
  logs      log volume per minute and sample lines of one component
  submit    record the answer for the case
"""

import json
from datetime import datetime
from pathlib import Path

import duckdb

from rca import bank, microrank, statistical, tracerca
from rca.cases import CST, Case
from rca.scoring import TIME_FORMAT, Answer, write_predictions


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, CST).strftime("%H:%M")


def _reference(con: duckdb.DuckDBPyConnection, case: Case) -> tuple[int, int, str]:
    rows = con.execute("SELECT count(*) FROM metric_app WHERE ts >= ? AND ts < ?", [case.start - 3600, case.start]).fetchone()[0]
    if rows:
        return case.start - 3600, case.start, "the hour before the window"
    start, end = statistical.reference_period(case, "normal-day")
    return start, end, "2021-03-05 (a normal day) around the same clock time"


def question(con: duckdb.DuckDBPyConnection, case: Case) -> None:
    print(case.instruction)
    print(f"\nWindow: {datetime.fromtimestamp(case.start, CST):%Y-%m-%d %H:%M} to {datetime.fromtimestamp(case.end, CST):%Y-%m-%d %H:%M} (UTC+8)")
    print("Components:", ", ".join(bank.COMPONENTS))
    print("Reasons:", "; ".join(bank.REASONS))
    print("Traces cover IG, Tomcat, MG and docker only; apache, Mysql and Redis show up in metrics and logs.")
    print(f'Submit one answer per failure: python -m rca tool submit {case.case_id} --time "YYYY-MM-DD HH:MM:SS" --component X --reason "..."')


def overview(con: duckdb.DuckDBPyConnection, case: Case) -> None:
    ref_start, ref_end, ref_name = _reference(con, case)
    scale = (case.end - case.start) / (ref_end - ref_start)
    print(f"Normal level: {ref_name}\n")

    ref = con.execute("SELECT avg(sr), avg(mrt), sum(cnt) * ? FROM metric_app WHERE ts >= ? AND ts < ?", [scale, ref_start, ref_end]).fetchone()
    print(f"Business KPIs, all services (normal: success {ref[0]:.1f}%, response {ref[1]:.0f} ms, {ref[2] / 30:.0f} requests/min)")
    print("minute  success%  response_ms  requests")
    for ts, sr, mrt, cnt in con.execute(
        "SELECT ts, avg(sr), avg(mrt), sum(cnt) FROM metric_app WHERE ts >= ? AND ts < ? GROUP BY ts ORDER BY ts",
        [case.start, case.end],
    ).fetchall():
        print(f"{_hm(ts)}   {sr:7.1f}  {mrt:11.0f}  {cnt:8d}")

    print("\nLog lines per component and log (window vs normal, scaled to the window length)")
    for cmdb, name, now, normal in con.execute(
        """SELECT cmdb_id, log_name, count(*) FILTER (WHERE ts >= ?) AS now, count(*) FILTER (WHERE ts < ?) * ? AS normal
           FROM log WHERE (ts >= ? AND ts < ?) OR (ts >= ? AND ts < ?) GROUP BY ALL ORDER BY cmdb_id, log_name""",
        [case.start, case.start, scale, case.start, case.end, ref_start, ref_end],
    ).fetchall():
        print(f"  {cmdb:9} {name:22} {now:7d}  normal {normal:9.0f}")

    print("\nSpans per component (window vs normal; mean duration ms)")
    for cmdb, now, normal, d_now, d_normal in con.execute(
        """SELECT cmdb_id, count(*) FILTER (WHERE ts >= ?), count(*) FILTER (WHERE ts < ?) * ?,
                  avg(duration) FILTER (WHERE ts >= ?), avg(duration) FILTER (WHERE ts < ?)
           FROM trace WHERE (ts >= ? AND ts < ?) OR (ts >= ? AND ts < ?) GROUP BY ALL ORDER BY cmdb_id""",
        [case.start, case.start, scale, case.start, case.start, case.start, case.end, ref_start, ref_end],
    ).fetchall():
        print(f"  {cmdb:9} {now:7d}  normal {normal:9.0f}   duration {d_now or 0:8.1f}  normal {d_normal or 0:8.1f}")


def anomalies(con: duckdb.DuckDBPyConnection, case: Case, component: str | None, top: int) -> None:
    findings = statistical.rank_findings(con, case, "zscore", "before")
    if component:
        findings = [f for f in findings if f.component == component]
    print("Metric series of the window ranked by z-score = |value - normal mean| / normal std.")
    print("onset = first minute with z >= 3 (or the peak). reason = the fault type this KPI usually indicates.\n")
    print(f"{'component':9} {'z':>8}  onset  {'peak':>12} {'normal':>12}  reason / kpi")
    for f in findings[:top]:
        print(f"{f.component:9} {f.score:8.1f}  {_hm(f.onset)}  {f.value:12.2f} {f.centre:12.2f}  {f.reason} / {f.kpi}")


def series(con: duckdb.DuckDBPyConnection, case: Case, component: str, kpi: str) -> None:
    ref_start, ref_end, ref_name = _reference(con, case)
    names = [r[0] for r in con.execute(
        "SELECT DISTINCT kpi_name FROM metric_container WHERE cmdb_id = ? AND kpi_name ILIKE ? AND ts >= ? AND ts < ? ORDER BY 1",
        [component, f"%{kpi}%", case.start, case.end],
    ).fetchall()]
    if not names:
        print(f"No KPI of {component} matches {kpi!r}.")
        return
    if len(names) > 6:
        print(f"{len(names)} KPIs match; showing 6. Narrow the pattern. All matches:\n  " + "\n  ".join(names) + "\n")
    print(f"Normal level: {ref_name}")
    for name in names[:6]:
        mean, std = con.execute(
            "SELECT avg(value), stddev_pop(value) FROM metric_container WHERE cmdb_id = ? AND kpi_name = ? AND ts >= ? AND ts < ?",
            [component, name, ref_start, ref_end],
        ).fetchone()
        points = con.execute(
            "SELECT ts, value FROM metric_container WHERE cmdb_id = ? AND kpi_name = ? AND ts >= ? AND ts < ? ORDER BY ts",
            [component, name, case.start, case.end],
        ).fetchall()
        normal = f"normal mean {mean:.2f} std {std:.2f}" if mean is not None else "no normal data"
        print(f"\n{name} ({normal})")
        print("  " + "  ".join(f"{_hm(ts)}={value:.2f}" for ts, value in points))


def traces(con: duckdb.DuckDBPyConnection, case: Case) -> None:
    print("MicroRank: a trace is abnormal when slower than the sum of its spans' normal mean+std plus 50 ms;")
    print("PageRank over the trace graph + spectrum score. Higher = more suspicious.")
    ranks = microrank.rank_components(con, case)
    print("  " + (", ".join(f"{c} {s:.3g}" for c, s in ranks[:8]) if ranks else "no result (no normal hour or no abnormal traces)"))
    print("\nTraceRCA: share of abnormal traces (a span slower than normal mean+3std) passing the component, Jaccard.")
    suspects = tracerca.rank_components(con, case)
    for s in suspects[:8]:
        print(f"  {s.component:9} {s.score:.3f}  abnormal traces through it {s.abnormal_traces}, normal {s.normal_traces}")
    if not suspects:
        print("  no result (no normal hour)")


def logs(con: duckdb.DuckDBPyConnection, case: Case, component: str, grep: str | None, samples: int) -> None:
    pattern = f"%{grep}%" if grep else "%"
    print(f"Log lines of {component}{f' containing {grep!r}' if grep else ''} per minute:")
    rows = con.execute(
        """SELECT log_name, CAST(floor(ts / 60) * 60 AS BIGINT) AS m, count(*) FROM log
           WHERE cmdb_id = ? AND value ILIKE ? AND ts >= ? AND ts < ? GROUP BY ALL ORDER BY 1, 2""",
        [component, pattern, case.start, case.end],
    ).fetchall()
    current = None
    for name, minute, n in rows:
        if name != current:
            print(f"\n  {name}:", end="")
            current = name
        print(f" {_hm(minute)}={n}", end="")
    print("\n\nSample lines:")
    for ts, name, value in con.execute(
        "SELECT * FROM (SELECT ts, log_name, value FROM log WHERE cmdb_id = ? AND value ILIKE ? AND ts >= ? AND ts < ?)"
        f" USING SAMPLE reservoir({int(samples)} ROWS) REPEATABLE (0) ORDER BY ts",
        [component, pattern, case.start, case.end],
    ).fetchall():
        print(f"  {_hm(ts)} [{name}] {value[:220]}")


def submit(case: Case, answers_dir: Path, time: str, component: str, reason: str, append: bool) -> None:
    datetime.strptime(time, TIME_FORMAT)
    if component not in bank.COMPONENTS:
        raise SystemExit(f"unknown component {component!r}; choose from {', '.join(bank.COMPONENTS)}")
    if reason not in bank.REASONS:
        raise SystemExit(f"unknown reason {reason!r}; choose from {'; '.join(bank.REASONS)}")
    path = answers_dir / f"{case.case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    answers = json.loads(path.read_text()) if append and path.exists() else []
    answers.append({"time": time, "component": component, "reason": reason})
    path.write_text(json.dumps(answers))
    write_predictions(answers_dir.parent / "predictions.jsonl", {
        p.stem: [Answer(**a) for a in json.loads(p.read_text())] for p in sorted(answers_dir.glob("*.json"))
    })
    print(f"{case.case_id}: {len(answers)} answer(s) recorded")
