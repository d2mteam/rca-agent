# Bank system profile (healthy windows only)

Sources: `rca tool` on normal-2021_03_06-0930 (Sat, called W1), 03_07-0930 (Sun, W2), 03_23-1230 (W3), 03_24-0800 (W4), 03_10-2030 (W5), 03_05-0300 (W6).
Each window is 30 min. All times UTC+8. Numbers below come from W1 unless a window is named.

## 1. Topology (tool `calls`, `overview`, `logs`)
- Traced chain: IG01/IG02 -> Tomcat01..04 -> MG01/MG02 -> dockerA1/A2/B1/B2, and dockerA*/B* call back MG01/MG02 (W1 `calls`).
- IG -> Tomcat is full mesh, 8 edges, each 8.8k-9.1k calls per 30 min (W1), mean call 696-763 ms.
- Tomcat -> MG is full mesh, 8 edges, 8.8k-9.0k calls each, mean 556-583 ms.
- MG -> docker: A1/A2 about 6.3k per edge (mean 425-772 ms); B1/B2 about 8.0-8.2k per edge (mean 572-946 ms). MG->dockerB1 is the slowest edge (927-946 ms).
- docker -> MG callbacks: A about 1.2k per edge (70-98 ms); B about 6.5k per edge (74-92 ms).
- Replicas: 2 apache, 2 IG, 4 Tomcat, 2 MG, 4 docker (traced only), 2 Mysql, 2 Redis. Load is spread evenly: every parallel edge is within +-3% of its peers (W1 `calls`).
- apache is not in traces. Its access-log client is a k6 load generator (`k6/0.29.0`, POSTs to `/UOCP/{base,person}/ServiceTestN.json`). Tomcat access-log clients are IG01/IG02 only. Mysql01 slow-log client hosts seen: Tomcat03, Tomcat04, one IP.
- apache requests = Tomcat requests: W2 apache01 29066 + apache02 37032 = 66098; Tomcat01-04 sum 16548+16611+16585+16358 = 66102. The apache01/apache02 split is uneven (W2 29.1k vs 37.0k).
- Redis/Mysql edges are NOT in traces; their clients cannot be read from the data.
- Traffic vs time of day (`overview`, requests/min, success always 100% in the windows):
  W1 Sat 09:30: 1933-3353 (typ. 2300), 553-1382 ms. W2 Sun 09:30: about the same (Tomcat access log 16.5k vs 17.9k in W1).
  W3 12:30: 1242-1521. W5 20:30: 737-889, 192-327 ms. W4 03-24 08:00: 300-800, 51-215 ms. W6 03:00: very low (Mysql02 Com Select 0.2/s vs 220/s in W1).
  Latency scales with load. Mean span duration per layer W1 vs W5: IG 717 vs 267 ms, MG 420 vs 148, Tomcat 181 vs 68, dockerB1 214 vs 66.
- `overview` "normal" = the hour BEFORE the window, not a long baseline. In W1/W2 the window is far above it (requests 2043+ vs 1375/min; spans about 1.7x), in W3 and W5 it is below. Ramp-ups look like "everything doubled" but are only load. In W4 the reference hour was itself abnormal-looking (success 93.0%, 5481 ms, 218 req/min, IG->Tomcat mean 1.1-1.9 s) so the window shows "latency 10x lower than normal". In W6 there is no reference hour: `overview` crashes (TypeError on None), `calls` is empty, `exclusive` says "no rise", `traces` says "no result".

## 2. Data sources per component
| component | metrics | logs | traces |
|---|---|---|---|
| apache01 | OSLinux only, 38 series (CPU, mem, disk, no network, no TCP, no swap, no filesystem) | apache_access_log (0 lines in W1, present in W2-W5) | no |
| apache02 | OSLinux, 82 series (adds FILESYSTEM, NETWORK TCP counts, SWAP, NTP offset, apache process count) | apache_access_log (~1050-1480 lines/min W1) | no |
| Tomcat01-04 | OSLinux 85-87 + Tomcat 21-24 (JVM heap via JMX id 7441, Requests for connector "http-nio-8003", Sessions) | localhost_access_log (~500-750/min), gc | yes |
| MG01/02 | OSLinux 85 + JVM 7 (port 7779) | none | yes |
| IG01/02 | OSLinux 83 + JVM 7 (port 7778) | none | yes |
| Mysql01/02 | OSLinux 101 + Mysql 124 (port 3306) | Mysql01: slow log; "error" log exists but had 0 lines (W3). Mysql02: none seen | no |
| Redis01/02 | OSLinux 75-81 + redis 32-34 (port 6379) | none | no |
| docker A1/A2/B1/B2 | none | none | yes (spans only) |
- docker* are not valid answer components and have no metrics or logs; they only show up in traces/exclusive/MicroRank.
- Tomcat has no JVM-CPU-load or Uptime series; MG/IG have JVM series (HeapMemoryUsed/Max/Usage, NoHeap, CPULoad, ThreadCount, Uptime) but no Tomcat series.
- No network series (ens160) on apache01/02 or on Mysql01/02 (only Tomcat, MG, IG, Redis01 listed ens160 NET*); Redis02 has TCP counts but no ens160.
- Tomcat session counters are 0 for the whole window; ErrorCountRequestInfo constant at 23, MaxTimeRequestInfo constant 348847 (cumulative, no change).

## 3. Runtime and software (evidence)
- Tomcat01-04: Tomcat JMX metrics, connector `http-nio-8003`, JVM max heap 4,187,619,328 B; gc log is CMS/ParNew style (`[GC (Allocation Failure) ... [ParNew: 850654K->10573K(943744K)...] 1739928K->899873K(4089472K)`), heap about 4.09 GB, young 944 MB. Tomcat access log: `<client> POST <uri> HTTP/1.1 <status> <bytes> - k6 ... <ms> <sec>`.
- IG/MG: JVM services (JMX ports 7778 IG, 7779 MG), max heap 2,040,135,680 B (2 GB).
- apache: Apache httpd (apache_access_log, apache process count series) in front.
- Mysql: MySQL with InnoDB, MyISAM key cache, Qcache series, binlog, slow log (`Query_time`, `Lock_time`), MaxConnections 2000 (W1).
- Redis: standalone, connected_slaves 0, maxmemory 4,294,967,296, AOF/RDB series present.
- Hosts are Linux VMs with 4 cores (CPU-0..3), NIC ens160, Zabbix agent (zabbix process, `Host_Uptime`), disks sda/sdb (Mysql also sdc). Host uptime about 6.19M s (72 days) for MG01/IG01; JVM uptime MG01 2.06M s, IG01 71-73k s (IG restarted about 20 h earlier).

## 4. Normal levels (W1 unless noted; mean +- std from the tool's reference hour)
- Memory used % (MEMUsedMemPerc) is very high and flat on many hosts, normal: Tomcat02/03/04 98, MG01/02 98, Mysql01/02 98, apache01 97 (page cache; Cache memory counts as used). Lower: Tomcat01 67-71 (rising 1 pt/10 min), IG01/02 71-76 (drifting up), apache02 52, Redis01 37, Redis02 16.
- Host CPU util (CPUCpuUtil): Tomcat 26.8-27.6 (std <0.7), MG 27.3, IG 25.9-26.0, Redis01 27.8, apache01 28.1, apache02 0.56, Mysql01 1.4, Mysql02 13.5 (18-21 in window), Redis02 1.2. W6 (03:00) Tomcat01 25.5 std 0.12, so about 25% is the idle-time floor, independent of load.
- Single-core saturation is normal: on 4-core hosts one core sits at 100% for minutes while the others are about 3%. Seen on Tomcat01 CPU-1 (54.6 mean, std 46.9, readings 90-100) and MG01 CPU-2/CPU-3 alternating (100% for ~10 min each), MG02 CPU-2/3 too. Host-level CPUCpuUtil does not move. IG hosts instead run 13-15% on every core.
- CPULoad (load avg): Tomcat 1.1, Redis01 1.14, apache01 1.2, IG JVM CPULoad 0.37-0.45, MG JVM CPULoad 1.45 (2.0-3.0 in the window); apache02/Mysql01/Redis02 about 0.02-0.05, Mysql02 0.9-1.7.
- JVM heap: IG/MG HeapMemoryUsed saw-tooths 56 MB-1.0 GB (mean 0.46-0.60 GB, std 0.23-0.24 GB) out of 2.04 GB, i.e. up to 50% is normal. Tomcat JVMMemoryUsedPercent 23-38 (mean 33). NoHeap MG 506,846,432 constant; IG 57.4M, std 69 kB. Threads MG 72.6, IG 71.
- Tomcat gc lines: Tomcat01/03/04 about 1/min; Tomcat02 about 3/min (W1 85 lines vs 29-31). Tomcat02 is always the heavy GC node (W2: 82 vs 27-28). ParNew pauses 0.05-0.08 s.
- Disk: sda writes about 6-12 KB/s, reads 0 (DSKRead is exactly 0 most minutes). Mysql sdc about 22-34 tps. Occasional read bursts (up to 1363 on MG02, 1310 on MG01, 960 on Tomcat02) appear in every window; see Section 5.
- Network: ens160 KBTotalPerSec Tomcat01 0.28-0.90 M (unit as stored), BandwidthUtil 0.00-0.01, errors 0. TotalTcpConnNum Tomcat01 874 +- 81, apache02 173 +- 62 (W1 values 250-400), MG02 median 2239 rising to 6572 within W1. CLOSE-WAIT about 0.
- Swap used 0%; zabbix_agentd.log size constant.
- Filesystem: Tomcat01 `/` used 4296 (capacity 30.11), inode 16.37%, all constant over the window. FS series are flat for 30 min.
- Mysql02 is the busy database: Com Select 219/s, Com Insert 136/s, ThreadsConnected 20 (std 0) (W1). Mysql01 is nearly idle: Com Select 33/s, Com Insert 0.01, SlowQueries 0.01, ThreadsConnected 23-31, response/CurrentSQLMaxRunningTime 0. Mysql01 slow log still has 49 lines (W1) vs 20 normal, 51 (W2), 33 (W4), 72 (W5), samples are `commit;` with Query_time 0.16-0.80 s, so slow commits of 0.2-0.8 s are normal. Innodb buffer pool free pages 252449 constant.
- Redis01: connected_clients 602, ops/s 3056 mean (3.5k-6.8k), used_memory about 19 MB, used_memory_peak 42.6 MB, keyspace hits growing about 200k/min, evicted 0. Redis02: clients 402, ops/s 1968 mean (2.4k-4k).
- Latency: see Section 1 (per-layer span means). Fixed per-service operation names ServiceTest1..11 with very different durations (13-15 ms vs 1000+ ms in Tomcat/apache access logs), so a fat latency tail is normal.

## 5. False alarms (what tools say in healthy windows)
`baro` (change point is always at/near window start, e.g. 09:30, 12:30, 20:30; W4 08:12). Top scores per window, none is a fault:
- W1: MG02 TotalTcpConnNum 4333 (median 2239 to 6572); Mysql01 `max trx lock memory bytes` 1136; Mysql01 Innodb Row Lock Time 603; Mysql02 sda DSKRead 387; MG02 sda DSKReadWrite 338; Mysql02 DSKReadWrite 156 / DSKRTps 130; MG01 CPU-2 idle 97; Redis01/02 CPUWio 65/52; MG01 CPU-3 util 34.
- W2: IG02 JVM NoHeapMemoryUsed 33760 (58.09M -> 58.13M, a tiny absolute change on a near-constant series); Tomcat02 sda DSKRead 963; MG01 DSKRead 491 (09:54); MG02 DSKRead 138; Mysql02 DSKRead 156; Mysql01 Row Lock Time 97.
- W3: Mysql01 max trx lock memory 1136 (12:40); Tomcat03 sda DSKRead 261; MG01 DSKRead 141; Tomcat04/IG02/MG02 single-core CPU util 100 (scores 60-126).
- W4: Mysql02 max trx lock memory 1136; MG01 DSKReadWrite 981; MG02 DSKRead 692; apache01 CPU-2 util 261 (peak 63.5%); Tomcat04 CPU-0 util 163.
- W5: Mysql01 sda DSKRead 626 (peak 83); apache01 CPU-2 util 312; Tomcat03 DSKRead 215; MG02/Tomcat03/Tomcat04 single-core util 100.
- W6 (03:00): baro crashes (traceback inside BOCPD change-point detection) and gives no ranking.
- Recurring baro noise classes: (a) `max trx lock memory bytes` is exactly 1136 whenever it fires (a step from 0, Mysql01 or Mysql02); (b) sda DSKRead / DSKReadWrite / DSKRTps bursts on any host (scores 100-980); (c) a single CPU core hitting 100% (scores 30-310); (d) Row Lock Time on Mysql01; (e) CPUWio on Redis01/02 (peak 0.35); (f) TCP connection counts on MG02 that follow load; (g) near-constant series whose IQR is about 0 give giant scores for tiny changes (NoHeapMemoryUsed 33760).
`exclusive`: reported "no rise" in W1, W2, W3, W6. Weak hits only in light-load windows: W4 Tomcat04 processing 2.6->20.6 ms (score 4.3), Tomcat03 network-in 1.3->5.0 (3.6), Tomcat01 processing 3.5->16.9 (3.1), Tomcat02 processing 3.1->14.5 (3.1); W5 Tomcat02 network-in 1.5->7.9 (6.4), Tomcat04 network-in 1.4->7.5 (6.1), Tomcat01 network-in 1.2->4.3 (3.1), Tomcat01 processing 6.8->15.8 (3.1). Only Tomcat components, scores up to 6.4, single-minute events.
`traces` (MicroRank) always ranks something, since it only ranks. Healthy-window outputs:
- W1: dockerB1 0.464, MG01 0.229, MG02 0.227, dockerB2 0.168, IG01 0.144, IG02 0.137, Tomcat02 0.114.
- W2: dockerB1 0.373, MG01 0.215, dockerB2 0.209, MG02 0.207, IG02 0.137, IG01 0.13.
- W3: dockerB2 0.171, MG02 0.103, MG01 0.086, dockerB1 0.086.
- W4: Tomcat02 0.144, dockerA2 0.118, IG02 0.079, MG01 0.072.
- W5: dockerB1 0.26, MG02 0.131, MG01 0.11, IG02 0.081.
dockerB1 (also the slowest edge) tops three of five windows; healthy scores are 0.04-0.46, so an absolute MicroRank value in that range is noise. W6: no result.

## 6. Other data notes
- Metric sampling: most series 1/min (CPU, load, uptime); some every 2-5 min (Tomcat JMX series 1 sample per 5 min, e.g. JVMMemoryUsedPercent at 09:32, 09:42...). `series` prints a subsample, with gaps of 1-3 min in many series.
- Counters are cumulative (Tomcat RequestCount 240k -> grows ~550/min, ProcessingTime, redis keyspace_hits, Mysql InnoDB counters). Mysql Com* values in `series` are per-second rates; Tomcat Requests*, redis hits, uptime are raw counters. Both appear in `baro`.
- Constant or near-zero series (any score on them is meaningless): Tomcat session counters, ErrorCount, MaxTime, NoHeap (MG), Mysql Aborted Clients/Connects, GetResponseTimeOfMysqld, CurrentSQLMaxRunningTime, Mysql Binlog stmt cache use (360 const), Innodb pages free, redis connected_slaves/evicted_keys, swap, NIC error counters, FS capacity/used, MaxConnections 2000, heap max, Mysql02 ThreadsConnected 20.
- Units: HeapMemory* and Redis memory in bytes; FS capacity as percent (30.11) vs FSUsedSpace in MB-like units (4296); CPU in percent; times in ms in `overview/calls` and Tomcat log (ms then s in the last two columns).
- apache01 had no log lines in W1 (while Tomcat logs were full); present in W2-W5. IG, MG, Mysql02, Redis01/02 never have log lines.
- Business success rate 100% in every healthy window except the pre-window reference of W4 (93.0%). Response ms in `overview` is per request across the chain (W1 538-1382 ms).
- Weekend caution: W1 (Sat) and W2 (Sun) show larger traffic than weekday W3/W5 despite being weekend, and k6 load is a synthetic generator, so load is test-driven rather than business-driven.
