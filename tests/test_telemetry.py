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


def _market_day(tmp_path):
    day = tmp_path / "telemetry" / "2022_03_20"
    for sub in ("trace", "log", "metric"):
        (day / sub).mkdir(parents=True)
    # frontend-0 serves (http, 40 ms) and calls cartservice (rpc, 30 ms); cartservice-1 serves it (25 ms).
    # Durations are microseconds.
    (day / "trace" / "trace_span.csv").write_text(
        "timestamp,cmdb_id,span_id,trace_id,duration,type,status_code,operation_name,parent_span\n"
        "1647705600000,frontend-0,a,t1,40000,http,200,hipstershop.Frontend/Recv.,\n"
        "1647705600001,frontend-0,b,t1,30000,rpc,0,hipstershop.CartService/GetCart,a\n"
        "1647705600002,cartservice-1,c,t1,25000,rpc,0,hipstershop.CartService/GetCart,b\n"
    )
    log = "log_id,timestamp,cmdb_id,log_name,value\n"
    (day / "log" / "log_service.csv").write_text(log + 'l1,1647705600,cartservice-1,log_cartservice-service_application,"severity: info"\n')
    (day / "log" / "log_proxy.csv").write_text(log + 'l2,1647705600,cartservice-1,log_cartservice-envoy_gateway,"""POST /x"" 200"\n')
    long = "timestamp,cmdb_id,kpi_name,value\n"
    (day / "metric" / "metric_container.csv").write_text(long + "1647705600,node-6.cartservice-1,container_cpu_usage_seconds,0.5\n")
    (day / "metric" / "metric_mesh.csv").write_text(
        long + "1647705600,cartservice-1.destination.frontend.cartservice,istio_requests.grpc.0.0,3.0\n")
    (day / "metric" / "metric_node.csv").write_text(long + "1647705600,node-6,system.cpu.iowait,0.31\n")
    (day / "metric" / "metric_runtime.csv").write_text(long + "1647705600,adservice.ts:8088,java_lang_Threading_ThreadCount,30\n")
    (day / "metric" / "metric_service.csv").write_text(
        "service,timestamp,rr,sr,mrt,count\ncartservice-grpc,1647705600,100.0,99.0,2.4,61\n")
    return tmp_path / "telemetry"


def test_market_lands_in_the_same_views(tmp_path):
    parquet = tmp_path / "parquet"
    counts = prepare_day(_market_day(tmp_path), parquet, "2022_03_20", system="Market1")
    assert counts == {"trace": 3, "log": 2, "metric_container": 4, "metric_app": 1}

    con = connect(parquet, system="Market1")
    assert con.execute("SELECT sr, cnt, mrt, tc FROM metric_app").fetchone() == (99.0, 61, 2.4, "cartservice-grpc")
    # Each metric lands on its pod, node or app; the pod's node and the mesh peer stay in the KPI name.
    assert sorted(con.execute("SELECT cmdb_id, kpi_name FROM metric_container").fetchall()) == [
        ("adservice", "java_lang_Threading_ThreadCount"),
        ("cartservice-1", "container_cpu_usage_seconds (on node-6)"),
        ("cartservice-1", "istio_requests.grpc.0.0 (destination.frontend.cartservice)"),
        ("node-6", "system.cpu.iowait"),
    ]
    # Only frontend-0's rpc span has a child on another pod; durations are now milliseconds.
    assert con.execute("SELECT span_id, is_call, duration FROM trace ORDER BY span_id").fetchall() == [
        ("a", False, 40.0), ("b", True, 30.0), ("c", False, 25.0)]


def test_market_call_counts_as_the_callees_network_in(tmp_path):
    from rca import exclusive_time

    prepare_day(_market_day(tmp_path), tmp_path / "parquet", "2022_03_20", system="Market1")
    con = connect(tmp_path / "parquet", system="Market1")
    rows = {(c, k): v for c, k, _, v in con.execute(exclusive_time.SERIES, [1647705600, 1647705660]).fetchall()}
    assert rows == {
        ("frontend-0", "processing"): 10.0,      # http 40 minus its rpc call 30
        ("cartservice-1", "network in"): 5.0,    # frontend's call 30 minus cartservice-1's span 25
        ("cartservice-1", "processing"): 25.0,
    }


def test_oteldemo_client_spans_are_calls_and_name_untraced_databases(tmp_path):
    from rca import exclusive_time

    day = tmp_path / "telemetry" / "2026_11_20"
    for sub in ("trace", "log", "metric"):
        (day / sub).mkdir(parents=True)
    # frontend (server 50 ms) calls product-catalog (client 40 ms, server 30 ms), which queries astronomy-db
    # (client 20 ms, no server span).
    (day / "trace" / "trace_span.csv").write_text(
        "timestamp,cmdb_id,span_id,trace_id,parent_id,duration,kind,status,name,call_target\n"
        "1795152000000,frontend,a,t,,50,2,0,GET /,\n"
        "1795152000001,frontend,b,t,a,40,3,0,ProductCatalogService/ListProducts,\n"
        "1795152000002,product-catalog,c,t,b,30,2,0,ProductCatalogService/ListProducts,\n"
        "1795152000003,product-catalog,d,t,c,20,3,0,SELECT products,astronomy-db\n"
    )
    (day / "metric" / "metric_container.csv").write_text("timestamp,cmdb_id,kpi_name,value\n1795152000,ad,jvm.cpu.recent_utilization,0.1\n")
    (day / "metric" / "metric_app.csv").write_text("timestamp,tc,sr,mrt,cnt\n1795152000,frontend-proxy,100.0,71.9,901\n")
    (day / "log" / "log_service.csv").write_text("log_id,timestamp,cmdb_id,log_name,value\nl1,1795152000,cart,info,ok\n")
    prepare_day(tmp_path / "telemetry", tmp_path / "parquet", "2026_11_20", system="OtelDemo")
    con = connect(tmp_path / "parquet", system="OtelDemo")
    assert con.execute("SELECT span_id, is_call, call_target FROM trace ORDER BY span_id").fetchall() == [
        ("a", False, None), ("b", True, None), ("c", False, None), ("d", True, "astronomy-db")]
    rows = {(c, k): v for c, k, _, v in con.execute(exclusive_time.SERIES, [1795152000, 1795152060]).fetchall()}
    assert rows == {
        ("frontend", "processing"): 10.0,           # 50 minus its call 40
        ("product-catalog", "network in"): 10.0,    # call 40 minus product-catalog's span 30
        ("product-catalog", "processing"): 10.0,    # 30 minus its query 20
        ("astronomy-db", "network in"): 20.0,       # the whole query: the database records no span
    }
