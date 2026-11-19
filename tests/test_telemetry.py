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


def _telecom_day(tmp_path):
    day = tmp_path / "telemetry" / "2020_05_26"
    for sub in ("trace", "metric"):
        (day / sub).mkdir(parents=True)
    # os_021 serves the request (OSB) and calls docker_001 (CSF -> RemoteProcess), which queries db_003 (JDBC).
    (day / "trace" / "trace_span.csv").write_text(
        "callType,startTime,elapsedTime,success,traceId,id,pid,cmdb_id,dsName,serviceName\n"
        "OSB,1590422400000,40.0,True,t1,a,None,os_021,,osb_001\n"
        "CSF,1590422400001,38.0,True,t1,b,a,os_021,,csf_001\n"
        "RemoteProcess,1590422400002,30.0,True,t1,c,b,docker_001,,csf_001\n"
        "LOCAL,1590422400003,25.0,True,t1,d,c,docker_001,db_003,local_method_001\n"
        "JDBC,1590422400004,20.0,True,t1,e,d,docker_001,db_003,\n"
    )
    long = "itemid,name,bomc_id,timestamp,value,cmdb_id\n"
    (day / "metric" / "metric_node.csv").write_text(long + "1,CPU_util_pct,ZJ-001,1590422400000,1.5,os_021\n")
    (day / "metric" / "metric_container.csv").write_text(long + "2,container_cpu_used,ZJ-004,1590422400000,7.0,docker_001\n")
    (day / "metric" / "metric_service.csv").write_text(long + "3,Proc_User_Used_Pct,ZJ-002,1590422400000,0.9,db_003\n")
    (day / "metric" / "metric_middleware.csv").write_text(long + "4,connected_clients,ZJ-005,1590422400000,40,redis_001\n")
    (day / "metric" / "metric_app.csv").write_text(
        "serviceName,startTime,avg_time,num,succee_num,succee_rate\nosb_001,1590422400000,0.79,361,343,0.95\n"
    )
    return tmp_path / "telemetry"


def test_telecom_lands_in_the_same_views(tmp_path):
    parquet = tmp_path / "parquet"
    counts = prepare_day(_telecom_day(tmp_path), parquet, "2020_05_26", system="Telecom")
    assert counts == {"trace": 5, "metric_container": 4, "metric_app": 1}

    con = connect(parquet, system="Telecom")
    assert window_counts(con, 1590422400, 1590422460) == {"trace": 5, "metric_container": 4, "metric_app": 1}
    assert con.execute("SELECT sr, cnt, mrt, tc FROM metric_app").fetchone() == (95.0, 361, 0.79, "osb_001")
    assert sorted(r[0] for r in con.execute("SELECT DISTINCT cmdb_id FROM metric_container").fetchall()) == [
        "db_003", "docker_001", "os_021", "redis_001"]
    # CSF and JDBC are calls; only JDBC names a callee that leaves no span, its database.
    assert con.execute("SELECT span_id, is_call, call_target FROM trace ORDER BY span_id").fetchall() == [
        ("a", False, None), ("b", True, None), ("c", False, None), ("d", False, None), ("e", True, "db_003")]


def test_telecom_database_call_counts_as_the_databases_network_in(tmp_path):
    from rca import exclusive_time

    prepare_day(_telecom_day(tmp_path), tmp_path / "parquet", "2020_05_26", system="Telecom")
    con = connect(tmp_path / "parquet", system="Telecom")
    rows = {(c, k): v for c, k, _, v in con.execute(exclusive_time.SERIES, [1590422400, 1590422460]).fetchall()}
    assert rows == {
        ("os_021", "processing"): 2.0,        # OSB 40 minus its CSF call 38
        ("docker_001", "network in"): 8.0,    # CSF 38 minus docker_001's RemoteProcess 30
        ("docker_001", "processing"): 5.0,    # (RemoteProcess 30 - LOCAL 25 + LOCAL 25 - JDBC 20) / 2
        ("db_003", "network in"): 20.0,       # the whole JDBC call: db_003 leaves no span
    }
