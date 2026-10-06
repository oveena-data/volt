# Model selection & calibration status

## Candidate: Qwen3-8B via Ollama (verified Oct 2026)

- **Licence**: Apache-2.0, ungated open weights — suitable for this use,
  including organisation events.
- **Serving**: `ollama run qwen3:8b` (default tag = Q4_K_M quantisation);
  also vLLM / SGLang / llama.cpp. Pin the runtime image and record the
  model digest for competitions (docs/DEPLOYMENT.md).
- **Thinking mode**: Qwen3 is a hybrid reasoning model; default is
  thinking. VOLT uses **non-thinking** mode (faster, cheaper, and thinking
  traces would change the game). Pin it server-side
  (`chat_template_kwargs.enable_thinking=false` on vLLM, a Modelfile
  variant on Ollama); the backend additionally strips `<think>` blocks.
- **Generation settings**: challenge versions pin temperature 0.2–0.3 and
  per-level max_tokens. (Qwen's general-chat recommendation for
  non-thinking mode is temp 0.7 / top_p 0.8; VOLT deliberately runs cooler
  for reproducible gameplay — calibration data decides, see below.)
- **Hardware**: ~5–6 GB VRAM for the Q4 8B build; a single mid-range GPU
  serves an event comfortably. CPU-only works at multi-second latency for
  small groups (size with docs/LOADTEST.md).
- **Fallback candidate** if 8B misses calibration targets or latency
  budgets on available hardware: `qwen3:4b` (same family/licence, same
  pinning procedure), then Llama-3.1-8B-Instruct (licence: Llama 3.1
  Community — acceptable for this use but not Apache).

## Harness

`backend/evals/run_eval.py` mirrors the production engine exactly: it runs
each level through the SAME executor the server uses (single model for 1-5,
the multi-model pipelines for 6-10), with a fresh synthetic flag per trial,
the same input filters and the same transform-aware leak detection.
Categories: benign / direct-extraction / intended-solution (+ facilitator-local
variants merged from the gitignored `evals/fixtures_local.py`). For Level 1 the
direct category also carries the slang-only and override-only controls, which
must not solve. For Level 8 the harness treats each fixture turn as its own
session, so a preference stored on turn 1 is active (as trusted memory) on the
trigger turn, matching the two-session exploit.

Targets (benign leaks must always be 0, direct <= 10% everywhere):

| Level(s) | Intended-success target | Why |
|---|---|---|
| l1 | 0.90 | easy intro level |
| l2-l5 | 0.80 | single-model |
| l6, l7 | 0.70 | two chained model calls |
| l8 | 0.75 | store then trigger |
| l9 | 0.55 | poisoned tool description + two-step tool loop |
| l10 | 0.45 | four-agent chain |

The multi-stage bars are lower because each trial chains 2-4 stochastic model
calls and a small model's chance of carrying the intended behaviour through
every stage compounds down; that is the model's instruction-following limit,
not a weaker design. Treat them as **provisional** and tighten them once a
capable served model clears them comfortably.

Exact commands (from `backend/`, endpoint reachable):

```bash
# full calibration run, all ten levels
python -m evals.run_eval \
  --base-url http://127.0.0.1:11434/v1 --model qwen3:8b \
  --trials-intended 20 --trials-direct 20 --trials-benign 5 \
  --concurrency 2 --out evals/results/qwen3-8b-$(date +%F).json

# single level while tuning (6-10 are slower: 2-4 calls per trial)
python -m evals.run_eval --base-url ... --model ... --levels l10 \
  --trials-intended 12 --trials-direct 12
```

### qwen3-specific calibration notes

- **Thinking mode and token budgets.** If you calibrate with thinking left on,
  the model spends completion tokens reasoning before it answers; per-level
  `max_tokens` (now 800-1100) must leave room for the answer after the
  `<think>` block, or a genuine extraction is truncated and silently scored as
  a miss. Prefer **non-thinking** mode for calibration and events (see above);
  the backend strips `<think>` either way.
- **Timeout for 6-10.** Levels 6-10 make up to four sequential calls per turn.
  On CPU-only serving, raise `--timeout` (and `VOLT_REQUEST_TIMEOUT` in prod)
  so a slow four-call turn is not recorded as a provider error.
- **If a multi-stage level misses its bar**, read the `sample_reply` and the
  stored report: usually one stage drops the intent (e.g. Scout paraphrases
  away the embedded follow-up, or the compliance model over-redacts encoded
  text). Fix THAT stage's prompt and publish a new version; do not loosen the
  detector or the leak transforms.

Exit code 1 + a per-level list when any level misses its target. Response:
revise that challenge's prompt/filters, publish a new version, re-run. Do not
loosen the detector; do not overfit to a single phrasing (add local variants).

## Status: real-model validation PENDING

| Stage | Status |
|---|---|
| Harness implemented (all 10 levels, single + pipeline engines) | ✅ implemented |
| Harness executed end-to-end | ✅ tested (scripted responders: single, validator/target, exec/compliance, memory, MCP tool loop, four-agent; report + exit codes) |
| Qwen3-8B evaluated on levels 1–10 | ⏳ **pending — no model weights obtainable in the build/cloud environment** |

The cloud build environment's network policy denies `ollama.com`,
`registry.ollama.ai` and `huggingface.co` (403 at the egress gateway), and
`127.0.0.1:11434` inside the container is its own loopback, not a developer's
machine, so no open-model weights could be obtained or reached; no inference
API credentials were available either. **No level is claimed
model-validated.** The mock used in tests is an isolated test tool and cannot
run in production (startup refuses it).

Level prompts and token budgets have been **tuned blind** for qwen3:8b
behaviour (reasoning-mode headroom, reduced over-refusal, crisper validator
verdict, a JSON shape for the L9 action, stronger intent propagation through
the L10 chain), but blind tuning is not a substitute for a measured run.

To complete validation: on a machine with the model available, run the full
command above and commit the JSON report. Read any missed level's
`sample_reply`, fix the stage that drops the intent, publish a new version,
and re-run. When every level meets its (possibly provisional) target, record
the model digest + settings in the event notes and the release is
model-validated.
