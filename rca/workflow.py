"""Code-led RCA workflow: fixed steps, two localisation tools, an LLM that only decides.

1. Question   the window and how many failures (from the query).
2. Symptoms   business KPIs per minute, summarised by code.
3. Localise   two tools on the same window:
              - BARO: BOCPD change point + RobustScorer over metrics (statistics);
                its scores are also the per-candidate evidence
              - Graph: exclusive time per component from the traces (rca.exclusive_time),
                plus replicas of each layer of the dependency graph compared by their
                share of traffic
4. Decide     code lists BARO's results for every candidate; the LLM picks
              component, reason and onset minute as JSON.
5. Verify     code checks each pick against the evidence and sends the LLM back
              to step 4 with the reason, at most twice.
"""

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import duckdb

from rca import bank, baro, exclusive_time, tools
from rca.cases import CST, Case
from rca.scoring import TIME_FORMAT, Answer
from rca.statistical import reason_for

# Replicas sharing a layer of the dependency graph; spans count traffic for the
# traced layers, access-log lines for apache.
LAYERS = (("apache01", "apache02"), ("IG01", "IG02"), ("Tomcat01", "Tomcat02", "Tomcat03", "Tomcat04"), ("MG01", "MG02"))
SHARE_DROP = 0.5  # a replica keeping less than half its normal share of its layer's traffic

# Evidence families: label, KPI pattern, reasons the family supports.
FAMILIES = (
    ("OS CPU utilisation", r"CPU_CPU_CPUCpuUtil$", {"high CPU usage"}),
    ("CPU iowait", r"CPU_CPU_CPUWio$", {"high disk I/O read usage"}),
    ("JVM CPU load", r"JVM_CPULoad$", {"high JVM CPU load"}),
    ("JVM heap used", r"HeapMemoryUsed$|JVMUsedMemory$|JVMMemoryUsedPercent$", {"JVM Out of Memory (OOM) Heap"}),
    ("memory used % (no cache)", r"NoCacheMemPerc$", {"high memory usage"}),
    ("disk busy %", r"DSKPercentBusy$", {"high disk I/O read usage"}),
    ("disk read", r"_DSKRead$", {"high disk I/O read usage"}),
    ("filesystem used %", r"FSCapacity$", {"high disk space usage"}),
    ("network packets in", r"NETPacketsIn$", {"network latency", "network packet loss"}),
    ("TCP connections", r"TotalTcpConnNum$", {"network latency", "network packet loss"}),
    ("TCP FIN-WAIT", r"TCP-FIN-WAIT$", {"network latency", "network packet loss"}),
    ("GC log lines/min", r"\|log_gc_lines$", {"JVM Out of Memory (OOM) Heap", "high JVM CPU load"}),
    ("trace span latency (ms)", r"\|trace_latency_ms$", {"network latency", "network packet loss"}),
)
NETWORK = {"network latency", "network packet loss"}
# Reasons a rise in exclusive time supports: work inside a component, or the way to it.
TRACE_REASONS = {
    "processing": frozenset({"high CPU usage", "high JVM CPU load", "JVM Out of Memory (OOM) Heap",
                             "high memory usage", "high disk I/O read usage"}),
    "network in": frozenset(NETWORK),
}

RULES = """Backend rules:
- OS CPU utilisation up while JVM CPU load stays flat -> "high CPU usage"; JVM CPU load up -> "high JVM CPU load".
- JVM heap used rising, or GC log lines jumping (garbage collection running constantly) -> "JVM Out of Memory (OOM) Heap".
- Memory used % (no cache) up -> "high memory usage". Memory rising only as page cache next to heavy disk reads is not a memory fault.
- Filesystem used % jumping close to full -> "high disk space usage". Disk busy/iowait/reads up without the filesystem filling -> "high disk I/O read usage".
- A replica losing its share of traffic (its siblings take it over), FIN-WAIT or connections dropping, no CPU/memory/disk change -> a network fault on that replica.
  The siblings that receive the extra traffic are healthy, even if their load rises.
- A metric that was already abnormal before the window, or a single-sample spike, is not the root cause.
- Exclusive time is the time a component spends itself, not waiting on the components it calls: a
  caller waiting on a slow callee keeps its exclusive time. "processing" up means the work inside the
  component slowed (CPU, JVM, memory, disk); "network in" up means requests took longer to reach it.
- The onset is the first minute the root-cause component changed, not when users noticed."""


@dataclass(frozen=True)
class Change:
    label: str
    reasons: frozenset
    centre: float
    peak: float
    onset: int
    score: float


@dataclass
class Report:
    case_id: str
    symptoms: str = ""
    baro: list = field(default_factory=list)
    graph: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    seconds: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    attempts: list = field(default_factory=list)
    answers: list = field(default_factory=list)


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, CST).strftime("%H:%M")


def symptoms(con: duckdb.DuckDBPyConnection, case: Case) -> str:
    ref_start, ref_end, _ = tools._reference(con, case)
    sr, mrt = con.execute("SELECT median(sr), median(mrt) FROM metric_app WHERE ts >= ? AND ts < ?", [ref_start, ref_end]).fetchone()
    rows = con.execute(
        "SELECT ts, avg(sr), avg(mrt) FROM metric_app WHERE ts >= ? AND ts < ? GROUP BY ts ORDER BY ts", [case.start, case.end]
    ).fetchall()
    bad = [(ts, s, m) for ts, s, m in rows if s < min(sr, 99.5) - 2 or m > 2 * mrt]
    if not bad:
        return f"No business impact: success rate and response time stayed near normal ({sr:.1f}%, {mrt:.0f} ms)."
    worst_sr = min(b[1] for b in bad)
    worst_mrt = max(b[2] for b in bad)
    minutes = ", ".join(_hm(b[0]) for b in bad[:12]) + (" ..." if len(bad) > 12 else "")
    return (f"Business impact in {len(bad)} minutes ({minutes}): success down to {worst_sr:.1f}% "
            f"(normal {sr:.1f}%), response time up to {worst_mrt:.0f} ms (normal {mrt:.0f} ms).")


def baro_tool(ranked: list[baro.Ranked], split: int, case: Case, top: int = 8) -> tuple[str, list[str]]:
    """BARO's ranking, strongest scorable change per component."""
    lines, seen = [], set()
    for r in ranked:
        component, _, kpi = r.column.partition("|")
        if component not in bank.COMPONENTS or component in seen or reason_for(kpi) is None:
            continue
        seen.add(component)
        lines.append(f"{component}: {kpi.split('_', 2)[-1]} {r.centre:.2f} -> {r.peak:.2f} "
                     f"(robust score {r.score:.0f}, from {_hm(r.onset)})")
        if len(lines) == top:
            break
    head = f"BOCPD change point at {_hm(split)}." if split >= case.start else "No change point inside the window."
    return head, lines


def _share_drops(con: duckdb.DuckDBPyConnection, case: Case) -> list[tuple[str, int, int, float, tuple]]:
    """Runs of 2+ minutes where a replica kept under SHARE_DROP of its normal share of its layer."""
    ref_start, ref_end, _ = tools._reference(con, case)
    counts: dict[tuple[str, int], int] = {}
    for start, end, ref in ((ref_start, ref_end, True), (case.start, case.end, False)):
        sql = """SELECT cmdb_id, CAST(floor(ts / 60) * 60 AS BIGINT), count(*) FROM {} WHERE ts >= ? AND ts < ? {} GROUP BY ALL"""
        rows = con.execute(sql.format("trace", ""), [start, end]).fetchall()
        rows += con.execute(sql.format("log", "AND log_name = 'apache_access_log'"), [start, end]).fetchall()
        for cmdb, minute, n in rows:
            counts[(cmdb, -1 if ref else minute)] = counts.get((cmdb, -1 if ref else minute), 0) + n
    drops = []
    minutes = range(case.start - case.start % 60, case.end, 60)
    for layer in LAYERS:
        total_ref = sum(counts.get((c, -1), 0) for c in layer)
        if not total_ref:
            continue
        for c in layer:
            normal = counts.get((c, -1), 0) / total_ref
            run = []
            for m in minutes:
                total = sum(counts.get((x, m), 0) for x in layer)
                share = counts.get((c, m), 0) / total if total else normal
                if total and share < SHARE_DROP * normal:
                    run.append((m, share))
                    continue
                if len(run) >= 2:
                    drops.append((c, run[0][0], run[-1][0], min(s for _, s in run) / normal, layer))
                run = []
            if len(run) >= 2:
                drops.append((c, run[0][0], run[-1][0], min(s for _, s in run) / normal, layer))
    return drops


def graph_tool(con: duckdb.DuckDBPyConnection, case: Case, top: int = 8) -> tuple[list[str], list[str], list, list]:
    """Exclusive-time changes and replica traffic-share drops, as prompt lines and as records."""
    traced = exclusive_time.changes(con, case)
    lines = [
        f"{c.component}: {c.kind} {c.normal_ms:.0f} -> {c.peak_ms:.0f} ms "
        f"(robust score {c.score:.0f}, from {_hm(c.start)} to {_hm(c.end)})"
        for c in traced[:top]
    ]
    drops = _share_drops(con, case)
    shift = [
        f"{c} kept only {kept:.0%} of its normal share of {'/'.join(layer)} traffic from {_hm(a)} to {_hm(b)}; "
        f"{', '.join(x for x in layer if x != c)} took it over"
        for c, a, b, kept, layer in drops
    ]
    return lines, shift, drops, traced


def evidence(ranked: list[baro.Ranked], component: str) -> list[Change]:
    """BARO results of one component: the best-scoring series of each family scoring 3 or more."""
    changes = []
    for label, pattern, reasons in FAMILIES:
        matching = [r for r in ranked if r.column.startswith(component + "|") and re.search(pattern, r.column)]
        if matching:
            r = max(matching, key=lambda r: r.score)
            if r.score >= 3:
                changes.append(Change(label, frozenset(reasons), r.centre, r.peak, r.onset, r.score))
    return sorted(changes, key=lambda c: c.score, reverse=True)


def _describe(changes: list[Change]) -> str:
    if not changes:
        return "  BARO found no increase scoring 3 or more"
    return "\n".join(
        f"  {c.label}: median {c.centre:.1f} -> {c.peak:.1f} (robust score {c.score:.0f}, from {_hm(c.onset)})" for c in changes[:8]
    )


def _schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "answers": {"type": "array", "minItems": n, "maxItems": n, "items": {
                "type": "object",
                "properties": {
                    "component": {"type": "string", "enum": list(bank.COMPONENTS)},
                    "reason": {"type": "string", "enum": list(bank.REASONS)},
                    "onset_minute": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
                    "why": {"type": "string"},
                },
                "required": ["component", "reason", "onset_minute", "why"],
            }},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["answers", "confidence"],
    }


def _ask(url: str, model: str, messages: list, n: int) -> tuple[dict, dict]:
    body = {"model": model, "messages": messages, "stream": False, "format": _schema(n),
            "options": {"temperature": 0, "num_ctx": 8192}}
    request = urllib.request.Request(f"{url}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=900) as response:
        reply = json.load(response)["message"]
    return reply, json.loads(reply["content"])


def verify(case: Case, picks: list[dict], evidence_of: dict, drops: list) -> list[str]:
    problems = []
    seen = set()
    for p in picks:
        if p["component"] in seen:
            problems.append(f"{p['component']} is given twice; each failure needs its own component.")
        seen.add(p["component"])
        ts = _onset(case, p["onset_minute"]).timestamp()
        if not case.start <= ts < case.end:
            problems.append(f"{p['onset_minute']} is outside the window.")
            continue
        support = [c for c in evidence_of.get(p["component"], []) if p["reason"] in c.reasons]
        shifted = [d for d in drops if d[0] == p["component"]]
        if p["reason"] in NETWORK and shifted:
            support = support or shifted
        if not support:
            problems.append(f"{p['component']} shows no evidence for \"{p['reason']}\" "
                            "(no matching metric or log family, and no traffic-share drop for a network fault).")
            continue
        onsets = [c.onset for c in support if isinstance(c, Change)] + [d[1] for d in shifted]
        if min(abs(ts - o) for o in onsets) > 300:
            first = ", ".join(sorted({_hm(o) for o in onsets}))
            problems.append(f"{p['component']} {p['reason']}: the supporting evidence starts at {first}, "
                            f"not near {p['onset_minute']}.")
    return problems


def _onset(case: Case, minute: str) -> datetime:
    """HH:MM inside the window as a datetime, rolling past midnight when the window does."""
    start = datetime.fromtimestamp(case.start, CST)
    hh, mm = map(int, minute.split(":"))
    at = start.replace(hour=hh, minute=mm)
    return at + timedelta(days=1) if at < start else at


def answers_of(case: Case, picks: list[dict]) -> list[Answer]:
    return [Answer(_onset(case, p["onset_minute"]).strftime(TIME_FORMAT), p["component"], p["reason"]) for p in picks]


def build(con: duckdb.DuckDBPyConnection, case: Case) -> tuple[str, dict, list, Report]:
    """Steps 1-3 and the evidence: the prompt for the deciding LLM, plus what verify needs."""
    report = Report(case.case_id)
    n = len(case.faults)  # the query states how many failures there are
    clock = time.perf_counter()

    def lap(step: str) -> None:
        nonlocal clock
        report.seconds[step] = round(time.perf_counter() - clock, 2)
        clock = time.perf_counter()

    report.symptoms = symptoms(con, case)
    lap("2 symptoms")
    split, ranked = baro.rank_series(con, case)
    baro_head, report.baro = baro_tool(ranked, split, case)
    lap("3a baro")
    traced_lines, shift, drops, traced = graph_tool(con, case)
    report.graph = traced_lines + shift
    lap("3b graph")

    # Each source puts forward its components in turn, so no source fills every place.
    sources = [[line.split(":")[0] for line in report.baro], [c.component for c in traced], [d[0] for d in drops]]
    candidates = []
    for rank in range(max(map(len, sources), default=0)):
        for ranked_components in sources:
            if rank < len(ranked_components) and ranked_components[rank] not in candidates:
                candidates.append(ranked_components[rank])
    candidates = [c for c in candidates if c in bank.COMPONENTS][:8]
    evidence_of = {
        c: evidence(ranked, c) + [
            Change(f"exclusive {t.kind} time (traces)", TRACE_REASONS[t.kind], t.normal_ms, t.peak_ms, t.start, t.score)
            for t in traced if t.component == c
        ]
        for c in candidates
    }
    report.evidence = {c: _describe(e) for c, e in evidence_of.items()}
    lap("evidence")

    window = f"{datetime.fromtimestamp(case.start, CST):%Y-%m-%d %H:%M} to {datetime.fromtimestamp(case.end, CST):%H:%M}"
    prompt = f"""Find the root cause of {n} failure(s) in a bank web system between {window} (UTC+8).
Call chain: apache01/02 -> IG01/02 -> Tomcat01-04 -> MG01/02 -> docker services; Mysql01/02 and Redis01/02 are datastores.
Components: {", ".join(bank.COMPONENTS)}.
Reasons (use one exactly): {"; ".join(bank.REASONS)}.

SYMPTOMS
{report.symptoms}

TOOL 1 - BARO (metrics: change points, strongest change per component)
{baro_head}
{chr(10).join(report.baro) or "nothing"}

TOOL 2 - GRAPH
Exclusive time from the traces, rises of 3 or more against the hour before (covers IG, Tomcat, MG only):
{chr(10).join(traced_lines) or "no rise"}
Replicas of the same layer compared by share of traffic:
{chr(10).join(shift) or "no replica lost its share of traffic"}

EVIDENCE PER CANDIDATE (BARO series and exclusive times that rose, robust score >= 3)
{chr(10).join(f"{c}:{chr(10)}{d}" for c, d in report.evidence.items())}

{RULES}

Give {n} answer(s): the root-cause component, the reason, and the onset minute (HH:MM) of each failure, with a one-line why."""
    return prompt, evidence_of, drops, report


def run(con: duckdb.DuckDBPyConnection, case: Case, model: str, url: str = "http://localhost:11434",
        retries: int = 2) -> tuple[list[Answer], Report]:
    """The whole workflow with a local Ollama model deciding."""
    prompt, evidence_of, drops, report = build(con, case)
    n = len(case.faults)
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(retries + 1):
        started = time.perf_counter()
        reply, parsed = _ask(url, model, messages, n)
        report.seconds[f"4 llm #{attempt + 1}"] = round(time.perf_counter() - started, 2)
        picks = parsed["answers"][:n]
        problems = verify(case, picks, evidence_of, drops)
        report.attempts.append({"answer": parsed, "problems": problems})
        if not problems or attempt == retries:
            break
        messages += [reply, {"role": "user", "content": "Verification failed:\n- " + "\n- ".join(problems)
                             + "\nRe-read the evidence and answer again."}]
    answers = answers_of(case, picks)
    report.answers = [a.__dict__ for a in answers]
    report.messages = messages + [reply]
    return answers, report
