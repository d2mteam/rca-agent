"""TORAI-style localisation (Pham et al., FSE'26), rebuilt from the paper and RCAEval's code.

RCAEval's TORAI needs its RCD module, pinned to a Python 3.8-era stack, so this
is a lighter re-implementation for OpenRCA Bank:

1. Sources as minute series over the hour before the window plus the window:
   metrics; log templates (numbers masked, standing in for Drain) counted per
   minute; mean span duration per minute for traced components.
2. SeverityScorer: every series scores max |z| after the split (mean/std of the
   part before); scores are normalised within each source; a component sums its
   metric and trace scores and keeps its highest log score. A missing source
   (no traces for apache, Mysql, Redis) scores 0.
3. SymptomCluster: a Gaussian mixture over the components' severity vectors,
   its size chosen by BIC; clusters ordered by mean severity.
4. CausalRanker, inside each cluster of 2+ components: RCD-style search with
   an F-node marking rows after the split. Series are binned, and a series stays
   a candidate root cause while chi-square tests keep it dependent on the F-node,
   alone and given each other candidate; candidates are chunked and filtered
   repeatedly. Components rank by the strongest dependence of their series.
   This replaces RCD's Psi-PC with a localised, order-1 version.
5. FineGrainer: BARO's max((x - median) / IQR) over the root component's series
   names the indicator, and its KPI the reason.

The split comes from BOCPD, as in BARO, since OpenRCA gives no injection time.
"""

import random
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import chi2
from sklearn.mixture import GaussianMixture

from rca import bank, baro
from rca.cases import CST, Case
from rca.scoring import TIME_FORMAT, Answer

TRACED = {"IG01", "IG02", "Tomcat01", "Tomcat02", "Tomcat03", "Tomcat04", "MG01", "MG02"}
BINS, CHUNK, ALPHA = 5, 5, 0.05


def source_tables(con: duckdb.DuckDBPyConnection, start: int, end: int) -> dict[str, pd.DataFrame]:
    """Minute series per source, columns `<component>|<series>`, indexed by minute."""
    components = list(bank.COMPONENTS)
    marks = ", ".join("?" * len(components))
    minute = "CAST(floor(ts / 60) * 60 AS BIGINT)"
    queries = {
        "metric": f"SELECT {minute} t, cmdb_id || '|' || kpi_name c, avg(value) v FROM metric_container "
                  f"WHERE ts >= ? AND ts < ? AND cmdb_id IN ({marks}) GROUP BY ALL",
        "log": f"SELECT {minute} t, cmdb_id || '|' || log_name || ':' || left(regexp_replace(value, '[0-9]+', '#', 'g'), 60) c, "
               f"count(*) v FROM log WHERE ts >= ? AND ts < ? AND cmdb_id IN ({marks}) GROUP BY ALL",
        "trace": f"SELECT {minute} t, cmdb_id || '|latency' c, avg(duration) v FROM trace "
                 f"WHERE ts >= ? AND ts < ? AND cmdb_id IN ({marks}) GROUP BY ALL",
    }
    minutes = pd.Index(range(start - start % 60, end, 60), name="t")
    tables = {}
    for source, sql in queries.items():
        long = con.execute(sql, [start, end, *components]).df()
        wide = long.pivot_table(index="t", columns="c", values="v").reindex(minutes)
        # A minute without lines counted none; metrics and latency carry their last value.
        tables[source] = wide.fillna(0) if source == "log" else wide.ffill().bfill()
        tables[source] = tables[source].loc[:, tables[source].nunique() > 1]
    return tables


def severity(tables: dict[str, pd.DataFrame], split: int) -> pd.DataFrame:
    """Components x sources severity matrix (rows: components with any series)."""
    columns = {}
    for source, table in tables.items():
        normal, abnormal = table.iloc[:split], table.iloc[split:]
        mean, std = normal.mean(), normal.std(ddof=0).replace(0, 1.0)
        score = ((abnormal - mean) / std).abs().max().fillna(0)
        if score.sum() > 0:
            score = score / score.sum()
        component = [c.partition("|")[0] for c in score.index]
        per = score.groupby(component).max() if source == "log" else score.groupby(component).sum()
        if source == "log" and per.sum() > 0:
            per = per / per.sum()
        columns[source] = per
    return pd.DataFrame(columns).reindex(list(bank.COMPONENTS)).fillna(0)


def clusters(matrix: pd.DataFrame) -> list[list[str]]:
    x = matrix.to_numpy()
    best = None
    for k in range(1, len(x)):
        gmm = GaussianMixture(k, covariance_type="full", max_iter=50, random_state=0, reg_covar=1e-6).fit(x)
        bic = gmm.bic(x)
        if best is None or bic < best[0]:
            best = (bic, gmm)
    labels = best[1].predict(x)
    groups = {}
    for component, label in zip(matrix.index, labels):
        groups.setdefault(label, []).append(component)
    severity_of = matrix.mean(axis=1)
    return sorted(groups.values(), key=lambda g: severity_of[g].mean(), reverse=True)


def _chi2_pvalue(x: np.ndarray, y: np.ndarray, z: np.ndarray | None) -> float:
    """G-test of x independent of binary y (given z), summed over z's strata; values are small ints."""
    strata = np.zeros_like(x) if z is None else z
    counts = np.bincount((strata * BINS + x) * 2 + y, minlength=BINS * BINS * 2).reshape(BINS, BINS, 2)
    stat, dof = 0.0, 0
    for table in counts:
        table = table[table.sum(1) > 0][:, table.sum(0) > 0]
        if table.shape[0] < 2 or table.shape[1] < 2:
            continue
        expected = table.sum(1, keepdims=True) * table.sum(0, keepdims=True) / table.sum()
        nonzero = table > 0
        stat += 2 * (table[nonzero] * np.log(table[nonzero] / expected[nonzero])).sum()
        dof += (table.shape[0] - 1) * (table.shape[1] - 1)
    return 1.0 if dof == 0 else float(chi2.sf(stat, dof))


def _localised(binned: dict[str, np.ndarray], f: np.ndarray, candidates: list[str]) -> dict[str, float]:
    """Candidates still dependent on the F-node alone and given each other; value = weakest p."""
    kept = {c: _chi2_pvalue(binned[c], f, None) for c in candidates}
    kept = {c: p for c, p in kept.items() if p <= ALPHA}
    for c in list(kept):
        for other in kept:
            if other == c:
                continue
            p = _chi2_pvalue(binned[c], f, binned[other])
            kept[c] = max(kept[c], p)
            if p > ALPHA:
                break
    return {c: p for c, p in kept.items() if p <= ALPHA}


def causal_rank(tables: dict[str, pd.DataFrame], split: int, group: list[str], seed: int = 0) -> list[str] | None:
    data = pd.concat([t.loc[:, [c.partition("|")[0] in group for c in t.columns]] for t in tables.values()], axis=1)
    if data.empty:
        return None
    binned = {c: pd.qcut(data[c].rank(method="first"), BINS, labels=False).to_numpy().astype(int) for c in data.columns}
    f = np.r_[np.zeros(split), np.ones(len(data) - split)].astype(int)
    rng = random.Random(seed)
    candidates = list(binned)
    while len(candidates) > CHUNK:
        rng.shuffle(candidates)
        kept = []
        for i in range(0, len(candidates), CHUNK):
            kept += list(_localised(binned, f, candidates[i:i + CHUNK]))
        if not kept or len(kept) == len(candidates):
            break
        candidates = kept
    final = _localised(binned, f, candidates)
    order = []
    for column, _ in sorted(final.items(), key=lambda kv: kv[1]):
        component = column.partition("|")[0]
        if component not in order:
            order.append(component)
    # As in RCAEval: keep the causal order only when it covers the whole cluster.
    return order if set(order) == set(group) else None


def rank(con: duckdb.DuckDBPyConnection, case: Case) -> tuple[int, list[str], list[baro.Ranked]]:
    split_time, _ = baro.rank_series(con, case)
    tables = source_tables(con, case.start - 3600, case.end)
    split = int(np.searchsorted(tables["metric"].index.to_numpy(), split_time))
    matrix = severity(tables, split)
    order = []
    for group in clusters(matrix):
        by_score = sorted(group, key=lambda c: matrix.loc[c].mean(), reverse=True)
        order += (causal_rank(tables, split, group) if len(group) > 1 else None) or by_score
    # FineGrainer: BARO's robust score over the components' metric and log series.
    _, fine = baro.rank_series(con, case, multi_source=True)
    return split_time, order, fine


def answer(con: duckdb.DuckDBPyConnection, case: Case) -> list[Answer]:
    split_time, order, fine = rank(con, case)
    answers = []
    for component in order[:len(case.faults)]:
        reason = next((baro.reason_of(r.column.partition("|")[2]) for r in fine
                       if r.column.startswith(component + "|") and baro.reason_of(r.column.partition("|")[2])), None)
        answers.append(Answer(datetime.fromtimestamp(split_time, CST).strftime(TIME_FORMAT), component, reason or bank.REASONS[0]))
    return answers
