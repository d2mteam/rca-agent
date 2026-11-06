import duckdb

from rca import exclusive_time
from rca.cases import Case

START = 1_615_266_000  # a window start, on a whole minute


def window_case() -> Case:
    return Case("bank-x", "task_1", "", frozenset({"time"}), START, START + 1800, ())


def traces(slow_from: int, tomcat_ms: int, network_ms: int, minutes_before: int = 60) -> duckdb.DuckDBPyConnection:
    """One trace per minute: IG01 serves, calls Tomcat01 and spends 3 ms of its own; from minute
    `slow_from` of the window, Tomcat01 works `tomcat_ms` and the call spends `network_ms` on the way."""
    con = duckdb.connect()
    con.execute("CREATE TABLE trace (ts DOUBLE, cmdb_id VARCHAR, parent_id VARCHAR, span_id VARCHAR, trace_id VARCHAR, duration BIGINT)")
    rows = []
    for minute in range(-minutes_before, 30):
        work, gap = (tomcat_ms, network_ms) if minute >= slow_from else (20, 2)
        ts, trace = START + 60 * minute + 5, f"t{minute}"
        rows += [
            (ts, "IG01", f"r{minute}", f"r{minute}", trace, work + gap + 3),
            (ts, "IG01", f"r{minute}", f"369-c{minute}", trace, work + gap),
            (ts, "Tomcat01", f"369-c{minute}", f"s{minute}", trace, work),
        ]
    con.executemany("INSERT INTO trace VALUES (?, ?, ?, ?, ?, ?)", rows)
    return con


def test_slow_processing_blames_the_callee_not_its_caller():
    found = exclusive_time.changes(traces(slow_from=10, tomcat_ms=120, network_ms=2), window_case())
    assert [(c.component, c.kind, c.start, c.end) for c in found] == [("Tomcat01", "processing", START + 600, START + 1740)]
    assert (found[0].normal_ms, found[0].peak_ms) == (20.0, 120.0)


def test_network_delay_is_credited_to_the_component_called():
    found = exclusive_time.changes(traces(slow_from=10, tomcat_ms=20, network_ms=202), window_case())
    assert [(c.component, c.kind, c.start) for c in found] == [("Tomcat01", "network in", START + 600)]


def test_no_result_without_an_hour_before():
    assert exclusive_time.changes(traces(slow_from=10, tomcat_ms=120, network_ms=2, minutes_before=0), window_case()) == []
