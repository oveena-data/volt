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

`backend/evals/run_eval.py` mirrors the production engine exactly: same
system prompts (fresh synthetic flag per trial), same input filters, same
transform-aware leak detection. Categories: benign / direct-extraction /
intended-solution (+ facilitator-local variants merged from the gitignored
`evals/fixtures_local.py`).

Exact commands (from `backend/`, endpoint reachable):

```bash
# full calibration run, all five levels
python -m evals.run_eval \
  --base-url http://127.0.0.1:11434/v1 --model qwen3:8b \
  --trials-intended 20 --trials-direct 20 --trials-benign 5 \
  --concurrency 2 --out evals/results/qwen3-8b-$(date +%F).json

# single level while tuning
python -m evals.run_eval --base-url ... --model ... --levels l4 \
  --trials-intended 20 --trials-direct 20
```

Exit code 1 + a per-level list when any level misses targets
(intended ≥ 0.80, direct ≤ 0.10, benign leaks = 0). Response: revise that
challenge's prompt/filters, publish a new version, re-run. Do not loosen
the detector; do not overfit to a single phrasing (add local variants).

## Status: real-model validation PENDING

| Stage | Status |
|---|---|
| Harness implemented | ✅ implemented |
| Harness executed end-to-end | ✅ tested (local stub endpoint: multi-turn, filters, leak paths, report, exit codes) |
| Qwen3-8B evaluated on levels 1–5 | ⏳ **pending — no model weights obtainable in the build environment** |

This build environment's network policy denies `ollama.com`,
`registry.ollama.ai` and `huggingface.co`, so no open-model weights could
be downloaded; no inference API credentials were available either. **No
level is claimed model-validated.** The mock used in tests and the stub
used for plumbing verification are isolated test tools and cannot run in
production (startup refuses them).

To complete validation: on a machine with the model available, run the two
commands above and commit the JSON report; if all five levels pass, record
model digest + settings in the event notes and the release is
model-validated.
