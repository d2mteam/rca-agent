# Telecom system profile (healthy state)

Source: `tool` runs on six no-fault windows (W1=05-22 03:30, W2=05-28 02:00, W3=05-28 05:30, W4=05-31 00:30,
W5=05-31 01:00, W6=05-31 05:30; each 30 min). "Normal" in tool output is the hour before the window.
Components: os_001-022 (22 hosts), docker_001-008 (8 containers), db_001-013 (13 databases). No logs exist.

## 1. Topology (from `calls`, W2/W3/W4; layers = tiers of the request path)
```
os_022 -> docker_001, docker_002   (tier A, 2 replicas)   os_022: ~4150-4300 calls/30min to each
os_021 -> docker_003, docker_004   (tier B, 2 replicas)   os_021: ~4150-4300 calls/30min to each
docker_001/002 -> docker_007 + docker_008 (both, ~8300-8500 calls each), -> db_007 (~8300-8500), -> db_009 (~58000-60000)
docker_003/004 -> docker_005 + docker_006 (both, ~8300-8500 each),     -> db_007 (~8300-8500), -> db_009 (~58000-60000)
docker_005/006/007/008 -> db_003 (~42000 calls each)
```
- 8 docker + 2 os (os_021, os_022) are the only traced components (spans); db_003/007/009 appear only as callee names
  (a call to a database is counted from the caller's span; the db leaves no span). Spans/30min: docker_001-004 ~150k,
  docker_005-008 ~84k, os_021/022 ~16.8k (W2). docker_009+ do not exist; os_001-020 and the other 10 dbs never appear in traces.
- Fan-out is uniform: every caller splits ~50/50 across its two callees; replicas within a layer are symmetric.
- The tier-A/B -> tier-C pairing is NOT fixed across days: W1 (05-22) had docker_001/002 -> docker_005/006 and
  docker_003/004 -> docker_007/008; W2, W3, W4 (05-28, 05-31) had docker_001/002 -> 007/008 and 003/004 -> 005/006.
  Always read `calls` of the actual window rather than assuming the pairing.
- Volume vs time of day (all-services requests/min, `overview`, typical minute): W2 02:00 ~560 (range 423-1103), W3 05:30 ~571,
  W4 00:30 ~509, W5 01:00 ~500, W6 05:30 ~571 (identical stats to W3), W1 03:30 ~358 (min 121; see blips). Night load is flat 500-600/min
  with success 100%, mean response 0.5-0.6 s. W1 (05-22) is lower: docker_001 spans ~99k vs ~150k, edges ~5300-5500 instead of ~8300-8500.
- W4 normal-baseline is distorted: baseline hour had 189 req/min and docker_001 52 ms vs window 21.6 ms (baseline straddles midnight/low data).
  Treat "vs normal" columns skeptically when the baseline hour is odd (W4 docker_001 -> docker_007 baseline 260 ms vs 46 ms in window).

## 2. Data sources per component
- docker_001-008: 9 series each: container_cpu_used, container_mem_used, container_fgc, container_fgct, container_session_used,
  container_thread_idle/running/total/used_pct. Only cpu_used and mem_used carry information. Also in traces (spans).
- os_001-022: 51 series each (CPU_*, Memory_*, Disk_*, FS_*, Processor_load_*, Page_pi/po, Received/Sent_packets, *_errors_packets,
  Recv_total/Send_total, Sent_queue/Received_queue, Incoming/Outgoing_network_traffic, ICMP_ping, Agent_ping, Num_of_processes,
  Swap_used_pct, Zombie_Process, ss_total ...). os_021 and os_022 are also traced; others not.
- db_001-013: 45-46 series each (CPU_Used_Pct, MEM_Used_Pct, Sess_Connect/Active, Session_pct, TPS_Per_Sec, Exec_Per_Sec, Call_Per_Sec,
  DbTime, Logic/Physical_Read_Per_Sec, Redo_Per_Sec, LFSync, Row_Lock, Hang, On_Off_State, tnsping_result_time, *Tbs_* tablespace series,
  PGA_*, Proc_Used_Pct, ASM free ...). Only db_003, db_007, db_009 are visible in traces, as callees without spans.
- Business series (not a component): osb_001_latency, visible in baro output; `overview` gives success%, mean response, requests/min.

## 3. What runs on what (from series names only)
- db_*: Oracle-like RDBMS. Evidence: tnsping_result_time, ASM (Asm_Free_Tb), PGA_Used_Pct, UndoTbs_Pct, TempTbs_Pct, LFSync/LFParaWrite (log file
  sync), "Logic_Read"/"Physical_Read", Redo_Per_Sec, Sess_* / Proc_Used_Pct. db_013 has 1332 connected sessions, the most.
- docker_*: container with JVM-style names (container_fgc = full GC count, thread pools, session_used) but ALL of fgc, fgct, session_used and
  thread_* are constant 0 in every healthy window (checked docker_001/005/007 in W2) -> those series carry no signal; only cpu and mem do.
  Spans in docker are fast (8-40 ms mean) and the middle tiers are service-style application containers.
- os_*: Linux hosts (Page_pi/po, Zombie_Process, Buffers/Cache_used, Processor_load_1/5/15, ss_total). Memory/FS values are percent.
- No webserver/runtime names appear beyond this; os_021/os_022 spans (400-1000 ms) look like gateway/frontend hosts calling the containers.

## 4. Normal levels (W2 `series`, hour-before mean/std unless noted)
- docker cpu_used (integer percent, 1-min): bursty and wide. docker_001 mean 43.6 std 26 (values jump 2..87 minute to minute); docker_002 up to
  ~40-71 mean in other windows; docker_003 6-40; docker_004 ~0-20; docker_005 1.9 std 5 (mostly ~0 with spikes); docker_007 11.7 std 6.7.
  Mean per container varies by window (docker_001 7.97 in W6 vs 66.97 in W4, docker_002 3.2 in W1 vs 71 in W4): a 80-90 % reading is normal.
  container_mem_used is flat: docker_001 41-42, docker_005 67, docker_007 76 (std 0).
- os CPU_util_pct: os_001/002 ~2 (1.8-2.05), os_003 9.6 (W2) / ~9 (W3-W6), os_004 1.3, os_005 5.0, os_017-020 ~2.5-2.9, os_021/022 ~5-6, rest <1.
  Memory_used_pct: os_012 90.8 (alarming-looking, std 0.01 and normal), os_001/002 76-77, os_021 50.9, os_022 57.7, os_017-020 ~6.5-7, os_010 7.3.
  Processor_load_1_min: 0.00-0.07 everywhere. Swap_used_pct 0, ICMP_ping 1, Agent_ping 1 (constant). Num_of_processes 147-400, std ~0-4.
  FS_used_pct 15-67 (static: std ~0). Disk_io_util ~0-0.5; Disk_await mean 0-15 but with rare spikes (os_001 W2 mean 15 std 106; W3 mean 0.32 std 2.5).
  Network: os_003 Recv_total ~2444, os_004 ~990, os_005 ~1290, os_017-020 ~310, others <20 (idle).
- db CPU_Used_Pct 1-3 % everywhere. MEM_Used_Pct is static per DB and often high: db_009 98.65 (and 98.9-99.1 all windows), db_012 88.7, db_008 86.2,
  db_007 85.0, db_005 84.2 -> normal. Tbs_Used_Pct static and high: db_003 98.4, db_006 97.0, db_005 96.7, db_007 96.6 -> normal.
  Sess_Connect 230-550 (db_013 1332), std 1-12. Session_pct 0.01-0.2. Hang=1, On_Off_State=1, Row_Lock=0 constant.
  TPS_Per_Sec: db_005 91 (185 in W1), db_010 40, db_009 24, db_003 20, db_007 10.6, others <2. Exec_Per_Sec 5-210 with big std on db_001/004/008 (std 84-100).
  DbTime: db_009 96.6 (std 3.4, the highest), db_003 32, others 5-20. Proc_Used_Pct db_013 26.4, db_009 13.6, db_012 10.7, others 1-5.
  Sess_Active: db_009 ~1 (1.95 in W4), others 0.04-0.3. tnsping_result_time: 0 or 10 flips (db_001..db_009 mean 1.3-5 std 3-5; db_010-013 mean 16-19).
- Latency (`calls`, ms): docker->db_009 1.2-3.4, ->db_007 4-7.5, tier-C -> db_003 2.6 (docker_005/006) or 7.1 (docker_007/008 in W2/W3);
  tier A/B -> tier C 36-76 (docker_001/002 -> 007/008: 69-76; docker_003/004 -> 005/006: 42-46). os_021 -> docker 295-410, os_022 -> docker 600-1300
  in W2 (very unstable: 639 vs 884 baseline, 1276 vs 761). Span mean durations (ms): docker_001 21-32, docker_002 23-39, docker_003/004 15-24, 005-008 8-17.

## 5. False alarms seen in healthy windows
baro (change point is arbitrary, often at window start or at a blip: W1 03:30, W2 02:17, W3 05:30, W4 00:30, W5 01:18, W6 05:51):
- Periodic scheduled jobs: `db_009|Physical_Read_Per_Sec` spikes to ~2836 exactly every 10 min (x8 minute, e.g. W3 05:38/05:48/05:58) in every window, score 2800-28000.
  Simultaneous companions: db_001/004/005/007/008 Physical_Read_Per_Sec (score 200-340), db_001/db_008/db_004 Exec_Per_Sec (score 280-340, peak ~350),
  db_011/db_012 Logic_Read_Per_Sec (score 190-760, peak ~5400), db_012 Physical_Read (1271), db_002 Logic_Read (score 420-690, peak ~17000),
  db_006/db_008 Redo_Per_Sec (score 350-880, peak 16000-32000), db_011 LFSync_Per_Sec (score 210-525), db_004 Call_Per_Sec (313).
- `os_022|Sent_queue` / `os_021|Sent_queue` (5-min sampled; 3 -> 5431 or 42579 single spike): score 5428 (W2), 42577 (W1), 5438 (W4).
- Tablespace series are multi-valued per timestamp: `db_003|New_Tbs_Used_Pct`, `db_009|New_Tbs_Used_Pct`, `db_004|New_Tbs_Free_Gb`
  give astronomic scores (1.4e12, 3.5e12, 2.2e10). Ignore any baro score >1e6.
- iowait/queue noise: os_018 Page_pi 582 (W2), os_021 CPU_iowait 919 (W1), os_004 CPU_iowait 262, os_001/002 CPU_iowait + Disk_avgqu_sz
  (1540/965 in W5, scores from tiny absolute values 0.7-2 %), os_006/os_009 CPU_iowait 2780/1390 (peak 0.01, W6), os_022 Page_po 608 / Disk_wr_kbs 871.
  Per-component baro on docker_001 gives only 1.5 (cpu_used), on os_001 up to 14.8 (peak 0.00) -> small scores on tiny levels are noise.
exclusive:
- W2: docker_002 network in 32 -> 3385 ms score 239 (02:17-02:22), plus os_022 processing 17 -> 42 (23), docker_002 processing 22.7 -> 106 (17).
  W1: os_021 processing 21 -> 2864 ms (433), docker_004 network in 24 -> 7625 (263), os_022 processing (90) at the first window minute.
  W4: docker_001 network in 34 -> 3444 (102, 00:48-00:52). W5: os_021 and os_022 processing 20 -> ~24400 ms (score ~3600-3700, 01:18-01:22).
  W6: docker_003 network in 19 -> 1504 (83, 05:49-05:53). W3: only 3-4 scores. Minor (score 3-9) hits on docker_00x processing 4-5 -> 10-15 ms.
  Windows with no labelled fault can contain 5-minute events where one component's network-in or processing rises
  100x. Whether these are unlabelled incidents or normal behaviour is unknown; their size alone does not tell.
- `os_022 processing` high at the FIRST minute of W1, W2, W4, W6 (17 -> 42, 20 -> 440, 20 -> 77, 23 -> 61 ms): artifact of the window start.
traces (MicroRank): scores 0.08-0.43 and the top entry is arbitrary: W2 docker_002 0.433, W1 os_021 0.184, W3 docker_005 0.115, W4 os_021 0.146,
  W5 os_021 0.155, W6 docker_007 0.082. Rankings sit within a narrow band for most components (~0.01-0.2).
Business-level events in these unlabelled windows: W1 03:49-03:53 requests drop 400 -> 121-153/min with success 98.4 % and response up to 11 s; W5 01:18-01:21 requests 30/min,
  response ~22 s; W2 02:00 requests 1103 (double), 02:17-02:21 response 1.2-3.2 s; W6 05:51 response 52.7 s; W4 00:49/00:51 response 3.1-3.7 s.

## 6. Data quirks
- Sampling: docker, most os, most db series per minute; some series are sparser: os Buffers_used, Cache_used (5-min, 02:02, 02:07...),
  Sent_queue (5-min), ASM free (5-min), db New_Tbs_* (10-min, ten values per timestamp). db CPU_Used_Pct has missing minutes.
- Minute labels occasionally repeat/skip (docker_001 W2: 02:01 appears twice, 02:02 missing; 02:15 twice, 02:16 missing; db_001 02:10, 02:22 twice).
- Constant series (std 0): all docker thread/session/fgc series, ICMP_ping, Agent_ping, Swap_used_pct, Hang, On_Off_State, Row_Lock, Tbs_Used_Pct, FS_used_pct,
  Proc_Used_Pct, MEM_Total, Buffers_used. Mostly zero: Disk_io_util, Disk_await, iowait, Received/Sent_errors_packets, Incoming/Outgoing traffic on idle hosts.
- "Normal" baseline and the first window minute are sometimes unrepresentative (W4 baseline 189 req/min). tool windows are UTC+8.
- W3 and W6 (05:30 on 05-28 and 05-31) report identical requests/min stats (mean 571.27, min 472, max 652) while response times differ.
