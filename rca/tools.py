"""Investigation tools an LLM agent calls from the shell, one case at a time.

Every tool sees only the query and the telemetry; none of them reveals the
graded faults. The tools hold no knowledge of what a signal means: raw ones show
the data, statistical ones rank what departed from normal. Reading meaning into
metric names and log lines is left to the agent. The normal level comes from the
hour before the window, or, on Bank, from the same clock time on 2021-03-05 when
that hour is not loaded. Every tool reads the case's system through the same views.

  question   the query, the answer space, the tools and how to submit
  overview   business KPIs per minute, log and span volume vs normal      (raw)
  calls      caller -> callee pairs seen in the traces, vs normal          (raw)
  series     KPIs of one component minute by minute                        (raw)
  logs       log lines of one component, optionally grepped, per minute   (raw)
  baro       metric series ranked by BARO (BOCPD + RobustScorer)   (statistical)
  exclusive  exclusive-time rises per component, from the traces   (statistical)
  traces     MicroRank ranking of traced components               (statistical)
  onset      when one component started to deviate, and when its callers did   (verification)
  submit     record the answer for the case
"""

import json
from datetime import datetime
from pathlib import Path

import duckdb

from rca import exclusive_time, hypotheses, microrank, onset, statistical, systems
from rca.cases import CST, Case
from rca.scoring import TIME_FORMAT, Answer, write_predictions
from rca.telemetry import has_view


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, CST).strftime("%H:%M")


def _reference(con: duckdb.DuckDBPyConnection, case: Case) -> tuple[int, int, str]:
    rows = con.execute("SELECT count(*) FROM metric_app WHERE ts >= ? AND ts < ?", [case.start - 3600, case.start]).fetchone()[0]
    normal_day = systems.get(case.system).normal_day
    if rows or normal_day is None:
        return case.start - 3600, case.start, "the hour before the window" + ("" if rows else " (no business KPIs there)")
    start, end = statistical.reference_period(case, "normal-day")
    return start, end, f"{normal_day} (a normal day) around the same clock time"


def question(con: duckdb.DuckDBPyConnection, case: Case, protocol: str = "hypotheses") -> None:
    space = systems.get(case.system)
    print(case.instruction or "A normal window: no fault was injected and there is nothing to submit.")
    print(f"\nWindow: {datetime.fromtimestamp(case.start, CST):%Y-%m-%d %H:%M} to {datetime.fromtimestamp(case.end, CST):%Y-%m-%d %H:%M} (UTC+8)")
    print("Components (answer with one of these):", ", ".join(space.components))
    print("Reasons (answer with one of these):", "; ".join(space.reasons))
    if not has_view(con, "log"):
        print("This system has no logs: the logs tool and the log part of overview are empty.")
    print(f"""
Tools, all run as python -m rca tool <name> {case.case_id} ...:
  overview                         business KPIs per minute; log lines and spans per component vs normal
  calls                            caller -> callee pairs in the traces: volume and call duration vs normal
  series <component> <pattern>     KPIs of a component whose name contains <pattern>, minute by minute
                                   (an empty pattern "" lists every KPI name of the component)
  logs <component> [--grep TEXT] [--samples N]
                                   log lines per minute and sample lines, optionally only those containing TEXT
  baro [--component C] [--top N]   metric series ranked by BARO: change point, then robust score per series
  exclusive                        rises in exclusive time per component, from the traces
  traces                           MicroRank ranking of traced components
  onset <component> [--pattern P]  when that component's series started to deviate, and when the components
                                   calling it (from the traces) did: to check a hypothesis's onset and order
""")
    if protocol == "free":
        print(f"""Submit one answer per failure (add --append for the second and later):
  python -m rca tool --protocol free submit {case.case_id} --time "YYYY-MM-DD HH:MM:SS" --component X --reason "..."
""")
        return
    print(f"""Work by hypotheses. Each one is a candidate cause with what you expect to observe if it is true and if it is false:
  hypothesis {case.case_id} add --component X --reason "..." --if-true "..." --if-false "..."
  hypothesis {case.case_id} test <id> --status supported|refuted|open --evidence "tool, numbers"
  hypothesis {case.case_id} list
Submit one answer per failure (add --append for the second and later). An answer is accepted only when it
matches a supported hypothesis and at least one other hypothesis was refuted:
  python -m rca tool submit {case.case_id} --time "YYYY-MM-DD HH:MM:SS" --component X --reason "..."
""")


def overview(con: duckdb.DuckDBPyConnection, case: Case) -> None:
    ref_start, ref_end, ref_name = _reference(con, case)
    scale = (case.end - case.start) / (ref_end - ref_start)
    print(f"Normal level: {ref_name}\n")

    ref = con.execute("SELECT avg(sr), avg(mrt), sum(cnt) * ? FROM metric_app WHERE ts >= ? AND ts < ?", [scale, ref_start, ref_end]).fetchone()
    if ref[0] is not None:
        print(f"Business KPIs, all services (normal: success {ref[0]:.1f}%, mean response {ref[1]:.4g}, {ref[2] / 30:.0f} requests/min)")
    print("minute  success%  mean_response  requests")
    for ts, sr, mrt, cnt in con.execute(
        "SELECT ts, avg(sr), avg(mrt), sum(cnt) FROM metric_app WHERE ts >= ? AND ts < ? GROUP BY ts ORDER BY ts",
        [case.start, case.end],
    ).fetchall():
        print(f"{_hm(ts)}   {sr:7.1f}  {mrt:13.4g}  {cnt:8d}")

    if has_view(con, "log"):
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
           FROM trace WHERE ((ts >= ? AND ts < ?) OR (ts >= ? AND ts < ?)) AND cmdb_id IS NOT NULL
           GROUP BY ALL ORDER BY cmdb_id""",
        [case.start, case.start, scale, case.start, case.start, case.start, case.end, ref_start, ref_end],
    ).fetchall():
        print(f"  {cmdb:9} {now:7d}  normal {normal:9.0f}   duration {d_now or 0:8.1f}  normal {d_normal or 0:8.1f}")


def calls(con: duckdb.DuckDBPyConnection, case: Case) -> None:
    ref_start, ref_end, ref_name = _reference(con, case)
    scale = (case.end - case.start) / (ref_end - ref_start)
    print(f"Normal level: {ref_name}. A call is a span whose child span is on another component, or a span")
    print("naming the component it calls when that one leaves no span (a database); its duration is the caller's")
    print("span. Counts are scaled to the window length.\n")
    print(f"{'caller':9} -> {'callee':9} {'calls':>8} {'normal':>8}  {'mean ms':>9} {'normal':>9}")
    for caller, callee, now, normal, d_now, d_normal in con.execute(
        """WITH s AS (SELECT trace_id, span_id, parent_id, cmdb_id, call_target, duration, ts >= ? AS win FROM trace
                      WHERE (ts >= ? AND ts < ?) OR (ts >= ? AND ts < ?)),
                pairs AS (SELECT p.cmdb_id AS caller, c.cmdb_id AS callee, p.duration, p.win
                          FROM s p JOIN s c ON c.trace_id = p.trace_id AND c.parent_id = p.span_id AND c.span_id <> c.parent_id
                          WHERE c.cmdb_id <> p.cmdb_id
                          UNION ALL
                          SELECT cmdb_id, call_target, duration, win FROM s WHERE call_target IS NOT NULL)
           SELECT caller, callee, count(*) FILTER (WHERE win), count(*) FILTER (WHERE NOT win) * ?,
                  avg(duration) FILTER (WHERE win), avg(duration) FILTER (WHERE NOT win)
           FROM pairs GROUP BY ALL ORDER BY 1, 2""",
        [case.start, case.start, case.end, ref_start, ref_end, scale],
    ).fetchall():
        print(f"{caller:9} -> {callee:9} {now:8d} {normal:8.0f}  {d_now or 0:9.1f} {d_normal or 0:9.1f}")


def baro_ranking(con: duckdb.DuckDBPyConnection, case: Case, component: str | None, top: int) -> None:
    from rca import baro  # optional dependency group "baro"

    split, ranked = baro.rank_series(con, case)
    if component:
        ranked = [r for r in ranked if r.column.startswith(component + "|")]
    print("BARO: BOCPD over all metric series finds the change point; each series is then scored")
    print("max((x - median) / IQR) after it, against the minutes before. onset = first minute scoring 3 or more.")
    print(f"Change point: {_hm(split)}" + ("" if split >= case.start else " (before the window: none inside it)") + "\n")
    print(f"{'score':>8}  onset  {'median':>12} {'peak':>12}  series")
    for r in ranked[:top]:
        print(f"{r.score:8.1f}  {_hm(r.onset)}  {r.centre:12.2f} {r.peak:12.2f}  {r.column}")


def exclusive(con: duckdb.DuckDBPyConnection, case: Case) -> None:
    print("Exclusive time = a span's duration minus the durations of the spans it calls, per minute and component:")
    print("  processing  the component's own spans")
    print("  network in  calls reaching the component: the caller's call span minus the component's span")
    print("Scored (x - median) / IQR against the hour before; listed when 3 or more, strongest first.\n")
    found = exclusive_time.changes(con, case)
    for c in found:
        print(f"{c.component:9} {c.kind:10} {c.normal_ms:8.1f} -> {c.peak_ms:8.1f} ms  score {c.score:7.1f}  "
              f"{_hm(c.start)}-{_hm(c.end)}")
    if not found:
        print("no rise")


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
    print("MicroRank: a trace is abnormal when slower than the 99th percentile of same-shaped traces in the hour")
    print("before, or when a call that normally reaches its callee left no span there. Personalised PageRank over")
    print("the operation-trace graph, then Ochiai spectrum score. Higher = more suspicious.")
    ranks = microrank.rank_components(con, case)
    print("  " + (", ".join(f"{c} {s:.3g}" for c, s in ranks[:8]) if ranks else "no result (no normal hour, or every trace abnormal or every trace normal)"))


def onsets(con: duckdb.DuckDBPyConnection, case: Case, component: str, pattern: str | None, top: int) -> None:
    print(f"Deviations of {component}{f' (series containing {pattern!r})' if pattern else ''}: 2+ consecutive samples above")
    print("warning against the 15 samples before (percentile levels if the baseline sits on one value,")
    print("z levels otherwise), onset moved back over the moderate samples just before. Counters are")
    print("tested on their differences. Sorted by onset.\n")
    found = onset.deviations(con, case, component, pattern)
    print(f"{'onset':5}  {'samples':>7}  {'level':8}  {'baseline':>12} -> {'peak':>12}  series")
    for d in found[:top]:
        print(f"{_hm(d.onset)}  {d.samples:7d}  {onset.LEVELS[d.level]:8}  {d.baseline:12.4g} -> {d.peak:12.4g}  "
              f"{d.series}{' (differences)' if d.differenced else ''}")
    if not found:
        print("no deviation")
    elif len(found) > top:
        print(f"... {len(found) - top} more; narrow with --pattern")
    first = found[0].onset if found else None
    print(f"\nComponents calling {component} (from the traces), by hops, and when their span duration deviated:")
    rows = onset.caller_onsets(con, case, component)
    for caller, hops, d in rows:
        if d is None:
            print(f"  {caller:9} {hops} hop(s): no deviation")
            continue
        order = "" if first is None else ("  (before " + component + "'s first deviation)" if d.onset < first - 60 else "")
        print(f"  {caller:9} {hops} hop(s): from {_hm(d.onset)}, {d.samples} samples, {d.baseline:.1f} -> {d.peak:.1f} ms{order}")
    if not rows:
        print("  none in the traces")


def logs(con: duckdb.DuckDBPyConnection, case: Case, component: str, grep: str | None, samples: int) -> None:
    if not has_view(con, "log"):
        print(f"{case.system} has no logs.")
        return
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


def submit(case: Case, answers_dir: Path, time: str, component: str, reason: str, append: bool,
           protocol: str = "hypotheses") -> None:
    datetime.strptime(time, TIME_FORMAT)
    systems.get(case.system).check(component, reason)
    refused = protocol == "hypotheses" and hypotheses.check_answer(
        hypotheses.load(answers_dir.parent, case.case_id), component, reason)
    if refused:
        raise SystemExit(f"not recorded: {refused}")
    path = answers_dir / f"{case.case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    answers = json.loads(path.read_text()) if append and path.exists() else []
    answers.append({"time": time, "component": component, "reason": reason})
    path.write_text(json.dumps(answers))
    write_predictions(answers_dir.parent / "predictions.jsonl", {
        p.stem: [Answer(**a) for a in json.loads(p.read_text())] for p in sorted(answers_dir.glob("*.json"))
    })
    print(f"{case.case_id}: {len(answers)} answer(s) recorded")
