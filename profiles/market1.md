# Market1 — healthy-system profile

Evidence gathered via `rca tool` on windows normal-2022_03_20-0100..0600 (01:00-06:30 UTC+8, each a
30-minute window, 1-minute granularity). All six windows look alike: `overview` gives success 100.0%,
mean response 10.9-11.5 ms, ~1570-1650 requests/min, with no visible diurnal trend across this
overnight span (checked 0100-0600). Numbers below are from 0100 unless a window is named.

## 1. Topology

Two parallel, non-interacting deployments of the same shop (traced via `calls`, 0100):
- **Primary** (3 replicas each): frontend-{0,1,2}, checkoutservice-{0,1,2}, cartservice-{0,1,2},
  currencyservice-{0,1,2}, productcatalogservice-{0,1,2}, recommendationservice-{0,1,2},
  adservice-{0,1,2}, emailservice-{0,1,2}, paymentservice-{0,1,2}, shippingservice-{0,1,2}.
- **Shadow stack "2"** (1 replica each, suffix `2-0`): frontend2-0, checkoutservice2-0, cartservice2-0,
  currencyservice2-0, productcatalogservice2-0, recommendationservice2-0, adservice2-0,
  emailservice2-0, paymentservice2-0, shippingservice2-0. No edge ever crosses between the two stacks.
- Call graph: frontend → {adservice, cartservice, checkoutservice, currencyservice,
  productcatalogservice, recommendationservice, shippingservice}; checkoutservice → {cartservice,
  currencyservice, emailservice, paymentservice, productcatalogservice, shippingservice};
  recommendationservice → productcatalogservice. No other callers.
- Per-replica volume (0100, per 30-min window): frontend→productcatalogservice ~1770-1840 calls;
  frontend→currencyservice ~1120-1185; frontend→cartservice ~340-350; frontend→recommendationservice
  ~277-287; frontend→adservice ~231-239; frontend→shippingservice ~76-81; frontend→checkoutservice
  only ~15-17 (checkout is rare — one checkout per many browse/cart calls). checkoutservice fans out
  ~15-47 calls per downstream per replica. Shadow stack carries roughly 1/3 the volume of one primary
  replica (e.g. frontend2-0→productcatalogservice2-0 ~1746 vs ~1800 per primary replica).
- `redis-cart-0` exists only as a name in `overview`'s log-line table (very low volume: ~2 envoy + ~20
  app lines per 30 min window). It is **not** a selectable component for `series`/`logs`/`baro` and
  never appears as a caller or callee in `calls` — cartservice's use of it is invisible to every tool.
- 6 nodes. Container metrics for every service instance are pinned to node-5 or node-6 (checked all 27
  instances at 0100): node-6 hosts most instances; node-5 hosts frontend-1, frontend-2,
  checkoutservice-2, cartservice2-0. node-1 runs moderately loaded (mean CPU 17%, up to 34%, std 10.6)
  with no containers scheduled on it — likely load-generation/control-plane, not app traffic. node-2/3/4
  are lightly loaded (mean CPU 4-6%). Nodes have zero log lines (checked node-1) — metrics only, no
  logs, no traces.

## 2. Data sources per component

| Component kind | Metrics | Logs | Traces |
|---|---|---|---|
| frontend, checkout/cart/currency/product/recommendation/shipping/payment/email-service | container_* (CPU, mem, fs, net, …) | `*-envoy_gateway` + `*-service_application` | yes |
| adservice | container_* **plus** `java_lang_OperatingSystem_*` JVM/JMX metrics | `*-envoy_gateway` only, no `*-service_application` lines observed | yes |
| productcatalogservice | container_* | `*-envoy_gateway` only, no `*-service_application` lines observed | yes |
| node-1..6 | `system.*` host metrics (58-59 KPIs each) | none | not applicable |
| redis-cart-0 | none (not a queryable component) | yes, very sparse | none |
| aggregate names (`paymentservice`, `emailservice`, … without instance suffix) | none via `series` (`series paymentservice ""` → no KPIs) — yet `baro` surfaces series like `paymentservice-grpc_latency` / `emailservice-grpc_latency` that are not reachable by name through `series` on any instance (checked paymentservice-0/1/2, paymentservice2-0, and the aggregate). Treat these as a distinct, low-granularity latency signal. | — | — |

Shadow-stack ("2") instances have the same metric/log/trace shape as their primary counterparts, just
one replica and roughly 1/3 the traffic.

## 3. Runtime / platform evidence

- Every component sits behind an Envoy sidecar: `*-envoy_gateway` logs are Envoy access-log lines
  (`"POST /hipstershop.X/Y HTTP/2" 200 ...`), and these sidecars also emit outbound entries to
  `jaeger-collector:14268` (e.g. shippingservice-0 log, 0100) — tracing-export traffic mixed into the
  gateway log stream, not application traffic.
- gRPC clients identify as `grpc-go/1.31.0` in envoy logs (checkoutservice-0, currencyservice-0,
  paymentservice-0 at 0100) — Go gRPC stack fronting these services.
- cartservice application logs are ASP.NET-Core-style (`Request starting HTTP/2 POST
  http://cartservice:7070/...`, `GetCartAsync called with userId=...`) → .NET runtime.
- adservice exposes `java_lang_OperatingSystem_FreePhysicalMemorySize` (≈364,507,136-364,583,253,
  essentially flat) → JVM/Java runtime, confirmed by no other component having JMX-style metrics.
- paymentservice application logs are plain `severity: info` lines with a `PaymentService#Charge
  invoked with request {...}` JSON payload; similar `severity: info/debug` free-text lines on
  currencyservice, shippingservice, frontend — consistent Node/Go style structured logger, not stack
  traces.
- All container metrics use the cAdvisor-style `container_*` naming (cfs periods, fs per
  `/dev/vda`/`/dev/vda1`, `eth0` network counters); node metrics use Datadog-style `system.*` naming —
  two different collectors, one per layer.

## 4. Normal resource & latency levels (0100, container_cpu_usage_seconds / memory_working_set_MB; span
durations from `overview`)

| Component | CPU (s, mean) | Memory (MB, mean) | Span duration (ms) |
|---|---|---|---|
| frontend | 0.29 | 25.2 | 8.5-8.8 |
| checkoutservice | 0.04-0.08 | n/a checked | 7.9-10.6 |
| cartservice | 0.25 | 102.4 | 0.0-0.3 |
| productcatalogservice | 0.41 | 28.8 | 4.0-4.6 |
| recommendationservice | 0.11 | 65.5 | 2.3-2.5 |
| currencyservice | 0.12 | 51.9 | 0.1 |
| adservice | 0.17 | n/a checked | 0.0 |
| emailservice | 0.03 | 70.8 | 0.2 |
| paymentservice | ~0.00 (rounds to 0) | 48.3 | 0.1-0.2 |
| shippingservice | 0.01 | 22.8 | 0.1 |

Node baselines (0100): node-5 CPU mean 5.7% (std 0.7), mem 44.7%; node-6 CPU mean 10.3% (std 2.9, peaks
~16% in the first few minutes of a window then settles — not a fault), mem 21.9%. node-1 CPU mean 17.2%
(std 10.6, swings 3-34% within a window — this is its normal noisy baseline, not an anomaly).
`checkoutservice`/`paymentservice`/`emailservice` CPU reads as ~0.00-0.08s per minute because their
call volume is tiny (~15 calls/min per replica vs ~1800 for productcatalogservice) — low numbers here
are normal, not evidence of starvation.

## 5. False alarms in healthy windows (what the tools flag — learn to recognize noise)

`baro` (checked 0100 and 0400) always fires on the same handful of series, with scores that are
meaningless in magnitude:
- `paymentservice-grpc_latency` and `emailservice-grpc_latency`: scores in the 1e10-1e11 range, but the
  underlying values are tiny and quantized (median ~1.05 ms, peak only 1.3-3.1 ms) — the huge score is
  an artifact of near-zero IQR on a low-cardinality series, not a real spike.
- `node-6|system.disk.total/used/free`, `system.fs.inodes.*`: scores in the 1e2-1e9 range. These are
  step functions (near-constant for the whole "normal" hour, then one level shift) — any shift at all
  produces an enormous z-score because the baseline IQR is ~0.
- `cartservice2-0|istio_request_duration_milliseconds...(destination.checkoutservice2.cartservice2)`:
  score 1.4e11 at 0100 — same cause, a 1-replica low-volume histogram bucket (median 1.05 ms, peak
  3.10 ms).
- Low-volume `container_network_receive_MB.eth0` on payment/email/ad/shipping instances: baseline ~0.02,
  occasional single-minute blip to 0.6-0.7, scores 130-320 — recurring, different instance/minute each
  window, not a real network event.

`exclusive` (checked 0100 and 0400) repeatedly flags `emailservice*` and `paymentservice*` "network in"
(baseline ~3-4 ms, spiking to 15-60 ms for a single minute, scores 10-47) and `checkoutservice*`
"processing" (baseline ~10 ms, spiking to 20-48 ms, scores 9-38). These are the lowest-traffic
components (~15 calls/min/replica), so single-minute timing jitter reads as a large z-score. Different
instance/minute fires each window — treat isolated single-minute exclusive-time spikes on these three
services as expected noise.

`traces` (MicroRank, checked 0100 and 0400) ranks checkoutservice/paymentservice/emailservice replicas
as most "suspicious" even with no fault (scores 0.37-0.60), consistently higher than
cartservice/productcatalogservice/frontend. This matches their low call volume and multi-hop fan-out
from checkout — a structural ranking bias, not evidence of a problem.

## 6. Other data notes

- All series (metrics, logs, spans) are reported per minute, 30 samples per window.
- Low-volume logs (`paymentservice-envoy_gateway`, etc.) silently omit minutes with zero lines rather
  than printing 0 — gaps in the per-minute listing are "no events," not missing data.
- `container_cpu_load_average_10s` is always exactly 0.00 for every instance checked (frontend-0) — not
  instrumented, always treat as uninformative.
- `system.disk.pct_usage` on nodes is essentially constant (e.g. node-1: mean 46.23, std 0.01).
- `container_memory_working_set_MB` is extremely flat per service (std 0.02-1.5 MB) — any multi-MB move
  is unusual for this metric.
- Shadow-stack histogram buckets (e.g. `istio_request_duration_milliseconds.grpc.200.14.0`,
  `...200.9.0`) are constant 0.00 for the whole window — reserved/unused latency buckets, not missing
  data.
- Business KPI units: `mean_response` is milliseconds (~11, consistent with span durations), requests
  are per-minute counts; success is a percentage.
