"""Non-LLM baseline: rank metric deviations by z-score or IQR, answer from the top one.

For every metric series of a candidate component, a reference period gives its
centre and spread, and each point in the query window is scored
  zscore: |x - mean| / std
  iqr:    |x - median| / (P75 - P25)   (BARO's robust score, made two-sided)
A spread of 0 counts as 1, as in BARO. Only series whose KPI maps to a reason
take part. The series with the highest score names the component and, through
its KPI, the reason; its first point scoring THRESHOLD or more gives the time.
A query with n faults takes the top n series on distinct components.

Reference periods: "before" is the hour before the window (falling back to
"normal-day" when that hour is not loaded); "normal-day" is the same clock
time, widened by 30 minutes each side, on 2021-03-05, a day without incidents.
"""

import re
from dataclasses import dataclass
from datetime import datetime

import duckdb

from rca import systems
from rca.cases import CST, Case
from rca.scoring import TIME_FORMAT, Answer

THRESHOLD = 3.0

# Bank KPI name -> reason, first match wins. Unlisted KPIs (MySQL, Redis, Tomcat
# request/session/thread counters, swap, processes...) are ignored.
REASON_RULES = (
    (re.compile(r"JVM_JVM_CPULoad$"), "high JVM CPU load"),
    (re.compile(r"(?<!No)HeapMemory(Usage|Used)$|JVM(UsedMemory|MemoryUsedPercent|FreeMemory)$"), "JVM Out of Memory (OOM) Heap"),
    (re.compile(r"^OSLinux-CPU_.*(CpuUtil|CPULoad|UserTime|SysTime|Cpuidle|idleutil)$"), "high CPU usage"),
    (re.compile(r"_MEMORY_MEMORY_(MEMUsedMemPerc|NoCacheMemPerc|UserMem|MEMFreeMem)$"), "high memory usage"),
    (re.compile(r"_LOCALDISK_.*_(DSKRead|DSKRTps|DSKBps|DSKPercentBusy|DSKAvgServ|DSKReadWrite)$"), "high disk I/O read usage"),
    (re.compile(r"_FILESYSTEM_.*_(FSCapacity|FSUsedSpace|FSAvailableSpace)$"), "high disk space usage"),
    (re.compile(r"_NETWORK_.*(Err|ErrPrc|ErrPrcc|TCP-FIN-WAIT|TCP-CLOSE-WAIT)$"), "network packet loss"),
    (re.compile(r"_NETWORK_.*(NETKBTotalPerSec|NETPackets(In|Out)|NETBandwidthUtil|TotalTcpConnNum)$"), "network latency"),
)


@dataclass(frozen=True)
class Finding:
    component: str
    kpi: str
    reason: str
    score: float
    onset: int
    value: float
    centre: float


def reason_for(kpi: str) -> str | None:
    for pattern, reason in REASON_RULES:
        if pattern.search(kpi):
            return reason
    return None


def reference_period(case: Case, reference: str) -> tuple[int, int]:
    if reference == "before":
        return case.start - 3600, case.start
    normal_day = systems.get(case.system).normal_day
    if normal_day is None:
        raise SystemExit(f"{case.system} has no normal day; use the hour before")
    days_back = (datetime.fromtimestamp(case.start, CST).date() - normal_day).days
    shift = days_back * 86400
    return case.start - shift - 1800, case.end - shift + 1800


def rank_findings(con: duckdb.DuckDBPyConnection, case: Case, method: str, reference: str) -> list[Finding]:
    """Every scorable series of the window, highest score first."""
    ref_start, ref_end = reference_period(case, reference)
    components = list(systems.get(case.system).components)
    marks = ", ".join("?" * len(components))
    rows = con.execute(
        f"""
        WITH ref AS (
            SELECT cmdb_id, kpi_name, avg(value) AS mean, stddev_pop(value) AS std, median(value) AS med,
                   quantile_cont(value, 0.75) - quantile_cont(value, 0.25) AS iqr
            FROM metric_container
            WHERE ts >= ? AND ts < ? AND cmdb_id IN ({marks})
            GROUP BY ALL HAVING count(*) >= 5
        )
        SELECT w.cmdb_id, w.kpi_name, w.ts, w.value, r.mean, r.std, r.med, r.iqr
        FROM metric_container w JOIN ref r USING (cmdb_id, kpi_name)
        WHERE w.ts >= ? AND w.ts < ?
        ORDER BY w.cmdb_id, w.kpi_name, w.ts
        """,
        [ref_start, ref_end, *components, case.start, case.end],
    ).fetchall()

    series: dict[tuple[str, str], list[tuple[int, float, float, float]]] = {}
    for component, kpi, ts, value, mean, std, med, iqr in rows:
        if reason_for(kpi) is None:
            continue
        centre, spread = (mean, std) if method == "zscore" else (med, iqr)
        score = abs(value - centre) / (spread if spread else 1.0)
        series.setdefault((component, kpi), []).append((int(ts), score, value, centre))

    findings = []
    for (component, kpi), points in series.items():
        peak = max(points, key=lambda p: p[1])
        onset = next((p for p in points if p[1] >= THRESHOLD), peak)
        findings.append(Finding(component, kpi, reason_for(kpi), peak[1], onset[0], peak[2], peak[3]))
    if not findings and reference == "before":
        # A window starting at 00:00 has its hour before on a day that is not extracted.
        return rank_findings(con, case, method, "normal-day")
    return sorted(findings, key=lambda f: f.score, reverse=True)


def answer(case: Case, findings: list[Finding]) -> list[Answer]:
    chosen: list[Finding] = []
    for finding in findings:
        if finding.component not in {f.component for f in chosen}:
            chosen.append(finding)
        if len(chosen) == len(case.faults):
            break
    return [
        Answer(datetime.fromtimestamp(f.onset, CST).strftime(TIME_FORMAT), f.component, f.reason)
        for f in sorted(chosen, key=lambda f: f.onset)
    ]
