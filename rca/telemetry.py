"""Telemetry store: one day of OpenRCA CSV becomes Parquet, queried with DuckDB.

Every table gets a `ts` column in epoch seconds so that windows from
`Case.start`/`Case.end` apply to all sources alike. The CSV unit varies by
file: trace timestamps are milliseconds on 2021-03-05 and 2021-03-09 but
seconds on 2021-03-24 and 2021-03-25, so the unit is read from the magnitude.
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class Source:
    csv_path: str
    columns: dict[str, str]


# Explicit types: trace ids look numeric but overflow BIGINT, so they stay text.
# The dialect is explicit too: without escape = '"', a file whose sampled rows
# quote values but contain no "" fails at the first "" further down.
SOURCES = {
    "trace": Source(
        "trace/trace_span.csv",
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
        "log/log_service.csv",
        {
            "log_id": "VARCHAR",
            "timestamp": "BIGINT",
            "cmdb_id": "VARCHAR",
            "log_name": "VARCHAR",
            "value": "VARCHAR",
        },
    ),
    "metric_container": Source(
        "metric/metric_container.csv",
        {"timestamp": "BIGINT", "cmdb_id": "VARCHAR", "kpi_name": "VARCHAR", "value": "DOUBLE"},
    ),
    "metric_app": Source(
        "metric/metric_app.csv",
        {"timestamp": "BIGINT", "rr": "DOUBLE", "sr": "DOUBLE", "cnt": "BIGINT", "mrt": "DOUBLE", "tc": "VARCHAR"},
    ),
}


# Epoch seconds are ~1.6e9 and epoch milliseconds ~1.6e12 for these dates.
TS_SECONDS = "CASE WHEN timestamp >= 100000000000 THEN timestamp / 1000.0 ELSE timestamp END"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_columns(columns: dict[str, str]) -> str:
    return "{" + ", ".join(f"{_sql_str(k)}: {_sql_str(v)}" for k, v in columns.items()) + "}"


def prepare_day(telemetry_dir: Path, parquet_dir: Path, day: str, memory_limit: str = "3GB") -> dict[str, int]:
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
    for name, source in SOURCES.items():
        target = out_dir / f"{name}.parquet"
        if not target.exists():
            partial = target.with_suffix(".parquet.part")
            con.execute(
                f"""
                COPY (
                    SELECT CAST({TS_SECONDS} AS DOUBLE) AS ts, * EXCLUDE (timestamp)
                    FROM read_csv({_sql_str(str(telemetry_dir / day / source.csv_path))},
                                  header = true, columns = {_sql_columns(source.columns)},
                                  delim = ',', quote = '"', escape = '"')
                    ORDER BY ts
                ) TO {_sql_str(str(partial))} (FORMAT parquet, COMPRESSION zstd)
                """
            )
            partial.rename(target)
        counts[name] = con.execute(f"SELECT count(*) FROM read_parquet({_sql_str(str(target))})").fetchone()[0]
    con.close()
    return counts


def connect(parquet_dir: Path, memory_limit: str = "2GB") -> duckdb.DuckDBPyConnection:
    """Open DuckDB with one view per source spanning every prepared day."""
    con = duckdb.connect()
    con.execute(f"SET memory_limit = {_sql_str(memory_limit)}")
    for name in SOURCES:
        files = sorted(parquet_dir.glob(f"*/{name}.parquet"))
        if files:
            file_list = "[" + ", ".join(_sql_str(str(f)) for f in files) + "]"
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({file_list})")
    return con


def window_counts(con: duckdb.DuckDBPyConnection, start: int, end: int) -> dict[str, int]:
    """Rows per source with start <= ts < end."""
    views = {row[0] for row in con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()}
    return {
        name: con.execute(f"SELECT count(*) FROM {name} WHERE ts >= ? AND ts < ?", [start, end]).fetchone()[0]
        for name in SOURCES
        if name in views
    }
