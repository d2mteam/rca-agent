import duckdb

from rca import onset
from rca.cases import Case

START = 1_615_266_000
NOISE = [10, 11, 10, 12, 11, 10, 11, 12, 10, 11]


def minutes(values, first=-60):
    return [(START + 60 * (first + i), v) for i, v in enumerate(values)]


def test_step_change_is_found_and_moved_back_over_a_moderate_sample():
    before = [NOISE[i % 10] for i in range(70)]  # minutes -60 .. 9
    values = before + [12.2] + [40] * 5 + [11] * 14  # moderate (z 1.9) at 10, deviation from 11
    d = onset.deviation("cpu", minutes(values), START, START + 1800)
    assert (d.onset, d.samples, d.level) == (START + 600, 5, 3)


def test_single_spike_is_not_a_deviation():
    values = [NOISE[i % 10] for i in range(70)] + [80] + [NOISE[i % 10] for i in range(19)]
    assert onset.deviation("cpu", minutes(values), START, START + 1800) is None


def test_counter_is_tested_on_its_differences():
    rate = [5] * 75 + [50] * 5 + [5] * 10  # requests per minute; the series stores the running total
    total, values = 0, []
    for r in rate:
        total += r
        values.append(total)
    d = onset.deviation("requests", minutes(values), START, START + 1800)
    assert d.differenced and d.onset == START + 15 * 60


def test_baseline_sitting_on_zero_uses_percentile_levels():
    values = [0] * 75 + [300, 280] + [0] * 13
    d = onset.deviation("disk reads", minutes(values), START, START + 1800)
    assert (d.onset, d.samples, d.level, d.baseline) == (START + 15 * 60, 2, 3, 0.0)


def test_callers_are_found_transitively_with_their_distance():
    con = duckdb.connect()
    con.execute("CREATE TABLE trace (ts DOUBLE, cmdb_id VARCHAR, parent_id VARCHAR, span_id VARCHAR, trace_id VARCHAR, duration BIGINT)")
    con.executemany("INSERT INTO trace VALUES (?, ?, ?, ?, ?, ?)", [
        (START + 5, "IG01", "r", "r", "t", 30),
        (START + 5, "Tomcat01", "r", "s1", "t", 20),
        (START + 5, "MG01", "s1", "s2", "t", 10),
    ])
    case = Case("bank-x", "task_1", "", frozenset({"time"}), START, START + 1800, ())
    assert onset.callers(con, case, "MG01") == {"Tomcat01": 1, "IG01": 2}
