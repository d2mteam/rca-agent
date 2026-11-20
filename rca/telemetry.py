"""Telemetry store: one day of OpenRCA CSV becomes Parquet, queried with DuckDB.

Every system lands in the same four views, so the tools need not know which system they read:
  trace             ts, cmdb_id, parent_id, span_id, trace_id, duration (ms), is_call, call_target
  log               ts, log_id, cmdb_id, log_name, value                  (Bank only)
  metric_container  ts, cmdb_id, kpi_name, value
  metric_app        ts, sr (% success), cnt (requests), mrt (mean response), tc (service), ...
`is_call` marks a span calling another component: on Bank its id starts with 369-, on Telecom it
is a CSF or JDBC span. `call_target` names the component called when the trace names it although
the callee leaves no span: Telecom's JDBC spans name their database.

Every table gets a `ts` column in epoch seconds so that windows from `Case.start`/`Case.end`
apply to all sources alike. The CSV unit varies by file: Bank trace timestamps are milliseconds
on 2021-03-05 and 2021-03-09 but seconds on 2021-03-24 and 2021-03-25, so the unit is read from
the magnitude.
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class Source:
    csv_paths: tuple[str, ...]  # files of one day read into the table; several share one schema
    columns: dict[str, str]
    select: str = "* EXCLUDE (timestamp)"  # columns kept after ts
    timestamp: str = "timestamp"
    filename: bool = False  # expose the CSV path of each row as `filename` to `select`
    derive: str | None = None  # query over the selected rows, named `raw`, giving the stored table


# Explicit types: trace ids look numeric but overflow BIGINT, so they stay text.
# The dialect is explicit too: without escape = '"', a file whose sampled rows
# quote values but contain no "" fails at the first "" further down.
SOURCES = {
    "Bank": {
        "trace": Source(
            ("trace/trace_span.csv",),
            {
                "timestamp": "BIGINT",
                "cmdb_id": "VARCHAR",
                "parent_id": "VARCHAR",
                "span_id": "VARCHAR",
                "trace_id": "VARCHAR",
                "duration": "BIGINT",
            },
        ),
        "log": Source(
            ("log/log_service.csv",),
            {
                "log_id": "VARCHAR",
                "timestamp": "BIGINT",
                "cmdb_id": "VARCHAR",
                "log_name": "VARCHAR",
                "value": "VARCHAR",
            },
        ),
        "metric_container": Source(
            ("metric/metric_container.csv",),
            {"timestamp": "BIGINT", "cmdb_id": "VARCHAR", "kpi_name": "VARCHAR", "value": "DOUBLE"},
        ),
        "metric_app": Source(
            ("metric/metric_app.csv",),
            {"timestamp": "BIGINT", "rr": "DOUBLE", "sr": "DOUBLE", "cnt": "BIGINT", "mrt": "DOUBLE", "tc": "VARCHAR"},
        ),
    },
    "Telecom": {
        "trace": Source(
            ("trace/trace_span.csv",),
            {
                "callType": "VARCHAR",
                "startTime": "BIGINT",
                "elapsedTime": "DOUBLE",
                "success": "VARCHAR",
                "traceId": "VARCHAR",
                "id": "VARCHAR",
                "pid": "VARCHAR",
                "cmdb_id": "VARCHAR",
                "dsName": "VARCHAR",
                "serviceName": "VARCHAR",
            },
            select="cmdb_id, pid AS parent_id, id AS span_id, traceId AS trace_id, elapsedTime AS duration, "
            "callType AS call_type, CASE WHEN callType = 'JDBC' THEN dsName END AS call_target",
            timestamp="startTime",
        ),
        # Nodes (os_*), pods (docker_*), databases (db_*) and Redis share one long format.
        "metric_container": Source(
            tuple(f"metric/metric_{kind}.csv" for kind in ("node", "container", "service", "middleware")),
            {"itemid": "VARCHAR", "name": "VARCHAR", "bomc_id": "VARCHAR", "timestamp": "BIGINT", "value": "DOUBLE",
             "cmdb_id": "VARCHAR"},
            select="cmdb_id, name AS kpi_name, value",
        ),
        "metric_app": Source(
            ("metric/metric_app.csv",),
            {"serviceName": "VARCHAR", "startTime": "BIGINT", "avg_time": "DOUBLE", "num": "BIGINT",
             "succee_num": "BIGINT", "succee_rate": "DOUBLE"},
            select="100 * succee_rate AS sr, num AS cnt, avg_time AS mrt, serviceName AS tc",
            timestamp="startTime",
        ),
    },
}

# Market: metric_container rows name `<node>.<pod>`, mesh rows `<pod>.<direction>.<peer>`, runtime rows
# `<app>.ts:<port>`. Each lands on its pod, node or app; the node a pod ran on and the mesh peer stay in the
# KPI name. Span durations are microseconds and are stored as milliseconds.
_MARKET_METRIC_CMDB = """CASE WHEN filename LIKE '%metric_container.csv' THEN split_part(cmdb_id, '.', 2)
                              WHEN filename LIKE '%metric_node.csv' THEN cmdb_id
                              ELSE split_part(cmdb_id, '.', 1) END"""
_MARKET_METRIC_KPI = """CASE WHEN filename LIKE '%metric_container.csv' THEN kpi_name || ' (on ' || split_part(cmdb_id, '.', 1) || ')'
                             WHEN filename LIKE '%metric_mesh.csv' THEN kpi_name || ' (' || substr(cmdb_id, strpos(cmdb_id, '.') + 1) || ')'
                             ELSE kpi_name END"""
# A span is a call when one of its children runs on another pod; the trace has no field saying so.
_MARKET_CALLS = """
WITH calls AS (SELECT DISTINCT p.trace_id, p.span_id FROM raw p JOIN raw c
               ON c.trace_id = p.trace_id AND c.parent_id = p.span_id WHERE c.cmdb_id <> p.cmdb_id)
SELECT raw.*, calls.span_id IS NOT NULL AS is_call, CAST(NULL AS VARCHAR) AS call_target
FROM raw LEFT JOIN calls USING (trace_id, span_id)
"""
_MARKET = {
    "trace": Source(
        ("trace/trace_span.csv",),
        {"timestamp": "BIGINT", "cmdb_id": "VARCHAR", "span_id": "VARCHAR", "trace_id": "VARCHAR", "duration": "DOUBLE",
         "type": "VARCHAR", "status_code": "VARCHAR", "operation_name": "VARCHAR", "parent_span": "VARCHAR"},
        select="cmdb_id, parent_span AS parent_id, span_id, trace_id, duration / 1000.0 AS duration, type AS span_type, "
        "status_code, operation_name",
        derive=_MARKET_CALLS,
    ),
    "log": Source(
        ("log/log_service.csv", "log/log_proxy.csv"),
        {"log_id": "VARCHAR", "timestamp": "BIGINT", "cmdb_id": "VARCHAR", "log_name": "VARCHAR", "value": "VARCHAR"},
    ),
    "metric_container": Source(
        tuple(f"metric/metric_{kind}.csv" for kind in ("container", "mesh", "node", "runtime")),
        {"timestamp": "BIGINT", "cmdb_id": "VARCHAR", "kpi_name": "VARCHAR", "value": "DOUBLE"},
        select=f"{_MARKET_METRIC_CMDB} AS cmdb_id, {_MARKET_METRIC_KPI} AS kpi_name, value",
        filename=True,
    ),
    "metric_app": Source(
        ("metric/metric_service.csv",),
        {"service": "VARCHAR", "timestamp": "BIGINT", "rr": "DOUBLE", "sr": "DOUBLE", "mrt": "DOUBLE", "count": "BIGINT"},
        select="rr, sr, mrt, count AS cnt, service AS tc",
    ),
}
SOURCES["Market1"] = SOURCES["Market2"] = _MARKET

# OpenTelemetry Demo, as converted from the collector's OTLP files: span durations already in ms, `kind` is
# the OTLP span kind, call_target the database or cache a client span reaches without a span of its own.
SOURCES["OtelDemo"] = {
    "trace": Source(
        ("trace/trace_span.csv",),
        {"timestamp": "BIGINT", "cmdb_id": "VARCHAR", "span_id": "VARCHAR", "trace_id": "VARCHAR",
         "parent_id": "VARCHAR", "duration": "DOUBLE", "kind": "INTEGER", "status": "VARCHAR", "name": "VARCHAR",
         "call_target": "VARCHAR"},
        select="cmdb_id, span_id, trace_id, parent_id, duration, kind, status, name, nullif(call_target, '') AS call_target",
    ),
    "log": Source(
        ("log/log_service.csv",),
        {"log_id": "VARCHAR", "timestamp": "BIGINT", "cmdb_id": "VARCHAR", "log_name": "VARCHAR", "value": "VARCHAR"},
    ),
    "metric_container": Source(
        ("metric/metric_container.csv",),
        {"timestamp": "BIGINT", "cmdb_id": "VARCHAR", "kpi_name": "VARCHAR", "value": "DOUBLE"},
    ),
    "metric_app": Source(
        ("metric/metric_app.csv",),
        {"timestamp": "BIGINT", "tc": "VARCHAR", "sr": "DOUBLE", "mrt": "DOUBLE", "cnt": "BIGINT"},
    ),
}

# Columns each view adds to its Parquet files.
VIEW_COLUMNS = {
    "Bank": {"trace": "*, span_id LIKE '369-%' AS is_call, CAST(NULL AS VARCHAR) AS call_target"},
    "Telecom": {"trace": "*, call_type IN ('CSF', 'JDBC') AS is_call"},
    "Market1": {},
    "Market2": {},
    # Client and producer spans call another component.
    "OtelDemo": {"trace": "*, kind IN (3, 4) AS is_call"},
}

VIEWS = ("trace", "log", "metric_container", "metric_app")

# Epoch seconds are ~1.6e9 and epoch milliseconds ~1.6e12 for these dates.
TS_SECONDS = "CASE WHEN {0} >= 100000000000 THEN {0} / 1000.0 ELSE {0} END"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_columns(columns: dict[str, str]) -> str:
    return "{" + ", ".join(f"{_sql_str(k)}: {_sql_str(v)}" for k, v in columns.items()) + "}"


def prepare_day(telemetry_dir: Path, parquet_dir: Path, day: str, memory_limit: str = "3GB",
                system: str = "Bank") -> dict[str, int]:
    """Convert telemetry/<day>/*.csv into parquet/<day>/<source>.parquet, sorted by ts.

    Returns the row count written per source. An existing Parquet file is kept,
    so an interrupted run resumes where it stopped.
    """
    out_dir = parquet_dir / day
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = {_sql_str(memory_limit)}")
    con.execute(f"SET temp_directory = {_sql_str(str(parquet_dir / '.tmp'))}")
    counts = {}
    for name, source in SOURCES[system].items():
        target = out_dir / f"{name}.parquet"
        if not target.exists():
            partial = target.with_suffix(".parquet.part")
            files = "[" + ", ".join(_sql_str(str(telemetry_dir / day / p)) for p in source.csv_paths) + "]"
            rows = f"""
                SELECT CAST({TS_SECONDS.format(source.timestamp)} AS DOUBLE) AS ts, {source.select}
                FROM read_csv({files}, header = true, columns = {_sql_columns(source.columns)},
                              delim = ',', quote = '"', escape = '"'{", filename = true" if source.filename else ""})
            """
            if source.derive:
                con.execute(f"CREATE OR REPLACE TEMP TABLE raw AS {rows}")
                rows = source.derive
            con.execute(f"COPY ({rows} ORDER BY ts) TO {_sql_str(str(partial))} (FORMAT parquet, COMPRESSION zstd)")
            con.execute("DROP TABLE IF EXISTS raw")
            partial.rename(target)
        counts[name] = con.execute(f"SELECT count(*) FROM read_parquet({_sql_str(str(target))})").fetchone()[0]
    con.close()
    return counts


def connect(parquet_dir: Path, memory_limit: str = "2GB", system: str = "Bank") -> duckdb.DuckDBPyConnection:
    """Open DuckDB with one view per source spanning every prepared day."""
    con = duckdb.connect()
    con.execute(f"SET memory_limit = {_sql_str(memory_limit)}")
    for name in SOURCES[system]:
        files = sorted(parquet_dir.glob(f"*/{name}.parquet"))
        if files:
            file_list = "[" + ", ".join(_sql_str(str(f)) for f in files) + "]"
            columns = VIEW_COLUMNS[system].get(name, "*")
            con.execute(f"CREATE VIEW {name} AS SELECT {columns} FROM read_parquet({file_list})")
    return con


def has_view(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM duckdb_views() WHERE NOT internal AND view_name = ?", [name]).fetchone()[0])


def window_counts(con: duckdb.DuckDBPyConnection, start: int, end: int) -> dict[str, int]:
    """Rows per source with start <= ts < end."""
    return {
        name: con.execute(f"SELECT count(*) FROM {name} WHERE ts >= ? AND ts < ?", [start, end]).fetchone()[0]
        for name in VIEWS
        if has_view(con, name)
    }
