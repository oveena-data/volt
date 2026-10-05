# Load test results

Target: **25 concurrent active players** (provisional). Harness:
`backend/loadtest/load_test.py` — each simulated player registers, opens a
level, then loops *send message → read reply → think 5–12 s*. "Active
players" therefore exceeds simultaneous inference requests; both are
reported.

## Environment measured

- Backend: single uvicorn process, this repo @ release commit
- Postgres 16, same host
- Inference: local OpenAI-compatible **stub with a fixed 1.2 s response
  time** (standing in for model latency — no model weights were available
  in the build environment; re-run against the real endpoint for absolute
  numbers)
- Host: 4 vCPU / 15 GB RAM container

## Run A — 25 players, inference concurrency 4, queue 32, 90 s

| Metric | Value |
|---|---|
| Completed turns | 156 (1.73/s) |
| Turn latency p50 / p95 / p99 | 6.41 s / 7.91 s / 9.67 s |
| Max simultaneous turn requests | 25 |
| Shed with clean 429 queue_full | 9 (clients backed off + retried) |
| Rate-limit 429s | 0 |
| Hard errors | **0** |

Interpretation: at 1.2 s per inference, 4-way concurrency gives ~3.3
turns/s capacity; 25 players generate enough demand that queueing dominates
latency. The bounded queue sheds cleanly instead of collapsing.

## Run B — identical, inference concurrency 8

| Metric | Value |
|---|---|
| Completed turns | 226 (2.51/s) |
| Turn latency p50 / p95 | **1.22 s / 2.54 s** (≈ pure inference time) |
| Shed / rate-limited / errors | 0 / 0 / **0** |

## Sizing guidance

Required inference concurrency ≈
`players × model_latency / (think_time + model_latency)`. For 25 active
players at ~1.2 s latency and ~8.5 s think time: ≈ 4 minimum, 8 comfortable
— matching the two runs. For a slower model (e.g. 8B on CPU at ~10–20 s),
either provision GPU inference or lower the player count per backend; the
queue bound guarantees degradation is explicit (429 + retry hint), never
silent.

**Achieved capacity (this environment): 25 concurrent active players with
zero errors; smooth (unqueued) latency at inference concurrency 8.** The
API layer (auth, DB, engine) was never the bottleneck: non-inference
requests stayed in the 10–60 ms range throughout.

Reproduce:

```bash
python -m loadtest.load_test --base-url http://127.0.0.1:8099 \
  --players 25 --duration 90 --think-min 5 --think-max 12 --out results.json
```
