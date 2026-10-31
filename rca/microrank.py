"""MicroRank-style localisation (Yu et al., WWW 2021), following the authors' code.

1. An operation's normal latency is the mean and std of its spans in the hour
   before the window. A trace is abnormal when its duration exceeds the sum of
   mean + std over all its spans plus 50 ms.
2. Personalized PageRank (d = 0.85, 25 rounds) on the graph of operations and
   traces, run once over abnormal traces and once over normal ones, weights
   each operation; the preference favours traces of rare shapes.
3. DStar2 spectrum score: ef^2 / (ep + nf), with ef/ep/nf the PageRank-weighted
   counts of abnormal/normal traces passing or missing the operation.

Bank traces carry no operation names, so an operation is a component plus its
span side (outgoing call or server-side work). A component scores the best of
its operations.
"""

from collections import defaultdict

import duckdb
import numpy as np

from rca.cases import Case

DAMPING, ALPHA, ROUNDS, SLACK_MS = 0.85, 0.01, 25, 50


def _pagerank(ops: list[str], traces: list[frozenset], calls: set[tuple[str, str]], anomaly: bool) -> dict[str, float]:
    index = {op: i for i, op in enumerate(ops)}
    n_ops, n_tr = len(ops), len(traces)
    p_ss = np.zeros((n_ops, n_ops))
    children = defaultdict(list)
    for parent, child in calls:
        children[parent].append(child)
    for parent, kids in children.items():
        for child in kids:
            p_ss[index[child], index[parent]] = 1 / len(kids)
    p_sr = np.zeros((n_ops, n_tr))
    p_rs = np.zeros((n_tr, n_ops))
    passing = defaultdict(list)
    for t, members in enumerate(traces):
        for op in members:
            p_sr[index[op], t] = 1 / len(members)
            passing[op].append(t)
    for op, ts in passing.items():
        p_rs[ts, index[op]] = 1 / len(ts)

    shape_count = defaultdict(int)
    for members in traces:
        shape_count[members] += 1
    kind = np.array([shape_count[m] for m in traces], dtype=float)
    if anomaly:
        size = np.array([len(m) for m in traces], dtype=float)
        v = 1 / (kind / np.sum(1 / kind) * 0.5 + 1 / size) / np.sum(1 / size) * 0.5
    else:
        v = (1 / kind) / np.sum(1 / kind)
    v = v.reshape(-1, 1)

    ops_rank = np.ones((n_ops, 1)) / (n_ops + n_tr)
    tr_rank = np.ones((n_tr, 1)) / (n_ops + n_tr)
    for _ in range(ROUNDS):
        new_ops = DAMPING * (p_sr @ tr_rank + ALPHA * (p_ss @ ops_rank))
        new_tr = DAMPING * (p_rs @ ops_rank) + (1 - DAMPING) * v
        ops_rank, tr_rank = new_ops / new_ops.max(), new_tr / new_tr.max()
    ops_rank = ops_rank / ops_rank.max()
    total = ops_rank.sum()
    return {op: float(ops_rank[index[op], 0] * total / n_ops) for op in ops}


def _op(alias: str = "") -> str:
    """SQL naming a span's operation: its component and whether it is an outgoing call."""
    a = f"{alias}." if alias else ""
    return f"{a}cmdb_id || CASE WHEN {a}span_id LIKE '369-%' THEN ':call' ELSE ':serve' END"


def rank_components(con: duckdb.DuckDBPyConnection, case: Case) -> list[tuple[str, float]]:
    op = _op()
    slo = dict(
        (o, m + s)
        for o, m, s in con.execute(
            f"SELECT {op}, avg(duration), coalesce(stddev_pop(duration), 0) FROM trace WHERE ts >= ? AND ts < ? GROUP BY 1",
            [case.start - 3600, case.start],
        ).fetchall()
    )
    rows = con.execute(
        f"SELECT trace_id, max(duration), list({op}) FROM trace WHERE ts >= ? AND ts < ? GROUP BY trace_id",
        [case.start, case.end],
    ).fetchall()
    calls = set(
        con.execute(
            f"""SELECT DISTINCT {_op('p')}, {_op('c')}
                FROM trace c JOIN trace p ON p.span_id = c.parent_id AND p.trace_id = c.trace_id
                WHERE c.ts >= ? AND c.ts < ? AND p.ts >= ? AND p.ts < ? AND c.parent_id <> c.span_id""",
            [case.start, case.end, case.start - 60, case.end],
        ).fetchall()
    )

    abnormal, normal = [], []
    for _, duration, ops in rows:
        expected = sum(slo.get(o, 0.0) for o in ops) + SLACK_MS
        (abnormal if duration > expected else normal).append(frozenset(ops))
    if not abnormal or not normal:
        return []

    def side(traces):
        ops = sorted({o for members in traces for o in members})
        weight = _pagerank(ops, traces, {c for c in calls if c[0] in ops and c[1] in ops}, traces is abnormal)
        count = defaultdict(int)
        for members in traces:
            for o in members:
                count[o] += 1
        return weight, count

    w_a, n_a = side(abnormal)
    w_n, n_n = side(normal)
    scores = {}
    for o in set(w_a) | set(w_n):
        if o in w_a:
            ef, nf = w_a[o] * n_a[o], w_a[o] * (len(abnormal) - n_a[o])
        else:
            ef = nf = 1e-7
        if o in w_n and o in w_a:
            ep = w_n[o] * n_n[o]
        elif o in w_n:
            ep = (1 + w_n[o]) * n_n[o]
        else:
            ep = 1e-7
        scores[o] = ef * ef / (ep + nf) if ep + nf else 0.0

    best = defaultdict(float)
    for o, s in scores.items():
        component = o.split(":")[0]
        best[component] = max(best[component], s)
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)
