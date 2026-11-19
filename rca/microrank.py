"""MicroRank (Yu et al., WWW 2021): the paper's PageRank scorer and spectrum ranker,
with a different anomaly detector.

1. Anomaly detector. The paper's Eq. 1 calls a trace abnormal when its latency
   exceeds the sum over its spans of (mu_o + 1.5 sigma_o). Summed over 10-25 spans
   that bound sits far above any real latency, so on Bank it flags only traces
   hanging for tens of seconds, and none at all in about half the cases. Here a
   trace is abnormal when, against the hour before,
   - it is slower than the 99th percentile of traces of its kind (same coverage
     tree; the 99th percentile of all traces when its kind has fewer than 100), or
   - it failed: a call it makes left no span on the callee, for an outgoing
     call that reached its callee in 99 % or more of 100 or more calls.
2. PageRank scorer, run once on the abnormal and once on the normal traces:
   personalised PageRank v = d A v + (1 - d) u (Eq. 4) on the operation-trace
   graph plus the call graph, A = [[w A_oo, A_ot], [A_to, 0]] (Eq. 5-6). A_oo
   goes from an operation to each operation it calls, A_ot from a trace to each operation it covers, A_to
   from an operation to each trace covering it, each split evenly (Eq. 2). The
   preference u lies on traces only (Eq. 7): abnormal traces weigh covering
   fewer operations and being of a rarer kind (phi = 0.5), normal traces the
   rarer kind alone. Traces with the same coverage tree are of one kind.
3. Weighted spectrum ranker (Eq. 8): O_ef = F N_ef, O_nf = F (N_f - N_ef),
   O_ep = P N_ep, with F and P an operation's abnormal and normal PageRank
   scores (largest = 1, and 0.0001 when it has none), ranked by Ochiai, the
   paper's default formula.

The paper leaves d, w and the rounds of Eq. 4 unstated. They follow the authors'
code: d = 0.85, w = 0.01, 25 rounds starting from v_o = 1/N_o, v_t = 1/N_t
(section 4.4.4), each round scaling v_o and v_t to a largest value of 1. With
Eq. 7 this reproduces the paper's worked example (F = 0.990, 0.328, 0.328, 1).
Elsewhere that code departs from the paper: its abnormal-trace preference
favours traces covering more operations, against Eq. 7, and its threshold is
1 sigma + 50 ms instead of 1.5 sigma. This module follows the paper there.

Bank traces carry no operation names, so an operation is a component plus its
span side (outgoing call or server-side work). OpenRCA gives a 30-minute window
instead of a detection time, so every trace of the window is analysed. A
component scores the best of its operations.
"""

from collections import Counter, defaultdict

import duckdb
import numpy as np

from rca.cases import Case

D, OMEGA, PHI, NO_SCORE = 0.85, 0.01, 0.5, 1e-4
ROUNDS = 25
PERCENTILE, MIN_KIND_TRACES = 99, 100
RELIABLE, MIN_CALLS = 0.99, 100

# One trace: its span ids, their parent ids and their operations, in matching order.
Trace = tuple[list[str], list[str], list[str]]


def _op(alias: str = "") -> str:
    """SQL naming a span's operation: its component and whether it is an outgoing call."""
    a = f"{alias}." if alias else ""
    return f"{a}cmdb_id || CASE WHEN {a}is_call THEN ':call' ELSE ':serve' END"


def coverage_tree(trace: Trace) -> tuple:
    """Canonical form of a trace's coverage tree: equal for traces of the same kind.

    A span is a root when it is its own parent (Bank's root spans) or its parent
    is not in the trace.
    """
    span_ids, parent_ids, ops = trace
    present = set(span_ids)
    op_of = dict(zip(span_ids, ops))
    children, roots = defaultdict(list), []
    for span, parent in zip(span_ids, parent_ids):
        if parent != span and parent in present:
            children[parent].append(span)
        else:
            roots.append(span)

    def form(span: str) -> tuple:
        return op_of[span], tuple(sorted(form(child) for child in children[span]))

    return tuple(sorted(form(root) for root in roots))


def transition(traces: list[Trace]) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    """Operations and the blocks A_oo, A_ot, A_to of Eq. 5, columns being the source."""
    covers = [set(ops) for _, _, ops in traces]
    operations = sorted(set().union(*covers))
    index = {o: i for i, o in enumerate(operations)}

    callees = defaultdict(set)
    for span_ids, parent_ids, ops in traces:
        op_of = dict(zip(span_ids, ops))
        for span, parent, op in zip(span_ids, parent_ids, ops):
            if parent != span and parent in op_of:
                callees[op_of[parent]].add(op)
    a_oo = np.zeros((len(operations), len(operations)))
    for caller, called in callees.items():
        for callee in called:
            a_oo[index[callee], index[caller]] = 1 / len(called)

    covering = Counter(o for cover in covers for o in cover)
    a_ot = np.zeros((len(operations), len(traces)))
    a_to = np.zeros((len(traces), len(operations)))
    for t, cover in enumerate(covers):
        for o in cover:
            a_ot[index[o], t] = 1 / len(cover)
            a_to[t, index[o]] = 1 / covering[o]
    return operations, a_oo, a_ot, a_to


def preference(traces: list[Trace], abnormal: bool) -> np.ndarray:
    """u_t of Eq. 7: theta_i = phi n_i^-1 / sum n_j^-1 + (1 - phi) k_i^-1 / sum k_j^-1.

    n_i is how many operations trace i covers and k_i how many traces share its
    kind. Normal traces use the kind term alone.
    """
    kinds = [coverage_tree(trace) for trace in traces]
    same = Counter(kinds)
    rarer_kind = np.array([1 / same[kind] for kind in kinds])
    rarer_kind /= rarer_kind.sum()
    if not abnormal:
        return rarer_kind
    smaller_scope = np.array([1 / len(set(ops)) for _, _, ops in traces])
    return PHI * smaller_scope / smaller_scope.sum() + (1 - PHI) * rarer_kind


def pagerank(traces: list[Trace], abnormal: bool) -> dict[str, float]:
    """PageRank score of every operation, the largest scaled to 1."""
    operations, a_oo, a_ot, a_to = transition(traces)
    u = preference(traces, abnormal)
    v_o, v_t = np.full(len(operations), 1 / len(operations)), np.full(len(traces), 1 / len(traces))
    for _ in range(ROUNDS):
        v_o, v_t = D * (OMEGA * a_oo @ v_o + a_ot @ v_t), D * (a_to @ v_o) + (1 - D) * u
        v_o, v_t = v_o / v_o.max(), v_t / v_t.max()
    return dict(zip(operations, v_o))


def _lost_calls(trace: Trace) -> set[str]:
    """Outgoing-call operations of the trace whose call left no span on the callee."""
    span_ids, parent_ids, ops = trace
    parents = set(parent_ids)
    return {op for span, op in zip(span_ids, ops) if op.endswith(":call") and span not in parents}


def partition(reference: list[tuple[float, Trace]], window: list[tuple[float, Trace]]) -> tuple[list[Trace], list[Trace]]:
    """Abnormal and normal traces of the window, as (latency, trace) pairs are judged against the reference."""
    by_kind, calls, lost = defaultdict(list), Counter(), Counter()
    for latency, trace in reference:
        by_kind[coverage_tree(trace)].append(latency)
        calls.update(op for op in set(trace[2]) if op.endswith(":call"))
        lost.update(_lost_calls(trace))
    overall = float(np.percentile([latency for latency, _ in reference], PERCENTILE))
    bound = {kind: float(np.percentile(v, PERCENTILE)) for kind, v in by_kind.items() if len(v) >= MIN_KIND_TRACES}
    reliable = {op for op, n in calls.items() if n >= MIN_CALLS and (n - lost[op]) / n >= RELIABLE}

    abnormal, normal = [], []
    for latency, trace in window:
        failed = bool(_lost_calls(trace) & reliable)
        slow = latency > bound.get(coverage_tree(trace), overall)
        (abnormal if failed or slow else normal).append(trace)
    return abnormal, normal


def rank_components(con: duckdb.DuckDBPyConnection, case: Case) -> list[tuple[str, float]]:
    # A span without a component (a few hundred a day on Telecom) names no operation and is left out.
    sql = (f"SELECT max(duration), list(span_id), list(parent_id), list({_op()}) FROM trace "
           "WHERE ts >= ? AND ts < ? AND cmdb_id IS NOT NULL GROUP BY trace_id")
    load = lambda start, end: [(latency, (s, p, o)) for latency, s, p, o in con.execute(sql, [start, end]).fetchall()]
    reference = load(case.start - 3600, case.start)
    if not reference:
        return []
    abnormal, normal = partition(reference, load(case.start, case.end))
    if not abnormal or not normal:
        return []

    f_score, p_score = pagerank(abnormal, abnormal=True), pagerank(normal, abnormal=False)
    n_ef = Counter(o for _, _, ops in abnormal for o in set(ops))
    n_ep = Counter(o for _, _, ops in normal for o in set(ops))
    best = defaultdict(float)
    for o in set(f_score) | set(p_score):
        f, p = f_score.get(o, NO_SCORE), p_score.get(o, NO_SCORE)
        ef, nf, ep = f * n_ef[o], f * (len(abnormal) - n_ef[o]), p * n_ep[o]
        ochiai = ef / np.sqrt((ef + ep) * (ef + nf)) if ef else 0.0
        component = o.split(":")[0]
        best[component] = max(best[component], float(ochiai))
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)
