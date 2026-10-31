from rca.telemetry import connect, prepare_day, window_counts


def _day(tmp_path):
    day = tmp_path / "telemetry" / "2021_03_09"
    for sub in ("trace", "log", "metric"):
        (day / sub).mkdir(parents=True)
    (day / "trace" / "trace_span.csv").write_text(
        "timestamp,cmdb_id,parent_id,span_id,trace_id,duration\n"
        "1615219199819,MG02,21030800016503820235,369-a,gw01,8\n"
        "1615219260500,MG02,369-a,369-b,gw01,9\n"
    )
    (day / "log" / "log_service.csv").write_text(
        "log_id,timestamp,cmdb_id,log_name,value\n"
        'a1,1615219200,Tomcat04,localhost,"INFO, with comma"\n'
    )
    (day / "metric" / "metric_container.csv").write_text(
        "timestamp,cmdb_id,kpi_name,value\n"
        "1615219200,Tomcat04,OSLinux-CPU_CPU_CPUCpuUtil,26.1244\n"
        "1615219260,Tomcat04,OSLinux-CPU_CPU_CPUCpuUtil,27.0\n"
    )
    (day / "metric" / "metric_app.csv").write_text(
        "timestamp,rr,sr,cnt,mrt,tc\n"
        "1615219140,88.68,67.8,53,4963.58,ServiceTest8\n"
    )
    return tmp_path / "telemetry"


def test_prepare_then_count_rows_in_window(tmp_path):
    parquet = tmp_path / "parquet"
    counts = prepare_day(_day(tmp_path), parquet, "2021_03_09")
    assert counts == {"trace": 2, "log": 1, "metric_container": 2, "metric_app": 1}

    con = connect(parquet)
    # Trace ts keeps milliseconds: 1615219199.819 falls before the window.
    assert window_counts(con, 1615219200, 1615219261) == {
        "trace": 1,
        "log": 1,
        "metric_container": 2,
        "metric_app": 0,
    }
    assert con.execute("SELECT parent_id FROM trace ORDER BY ts LIMIT 1").fetchone()[0] == "21030800016503820235"


def test_trace_in_seconds_lands_on_the_same_clock(tmp_path):
    # 2021_03_25/trace_span.csv stores seconds where 2021_03_09 stores milliseconds.
    telemetry = _day(tmp_path)
    (telemetry / "2021_03_09" / "trace" / "trace_span.csv").write_text(
        "timestamp,cmdb_id,parent_id,span_id,trace_id,duration\n"
        "1615219200,MG02,p,s,t,8\n"
    )
    prepare_day(telemetry, tmp_path / "parquet", "2021_03_09")
    con = connect(tmp_path / "parquet")
    assert con.execute("SELECT ts FROM trace").fetchone()[0] == 1615219200


def test_escaped_quote_after_many_rows_without_one(tmp_path):
    # Mirrors 2021_03_05/log_service.csv: quoted values from line 2, first "" at line 272538.
    telemetry = _day(tmp_path)
    plain = "".join(f'p{i},1615219200,Tomcat01,gc,"49115.139: [GC (Allocation Failure), done]"\n' for i in range(30_000))
    quoted = 'q1,1615219201,apache02,access,"IPAddress ""POST /api HTTP/1.1"" 200 ""-"""\n'
    (telemetry / "2021_03_09" / "log" / "log_service.csv").write_text("log_id,timestamp,cmdb_id,log_name,value\n" + plain + quoted)

    counts = prepare_day(telemetry, tmp_path / "parquet", "2021_03_09")
    assert counts["log"] == 30_001
    con = connect(tmp_path / "parquet")
    assert con.execute("SELECT value FROM log WHERE log_id = 'q1'").fetchone()[0] == 'IPAddress "POST /api HTTP/1.1" 200 "-"'
