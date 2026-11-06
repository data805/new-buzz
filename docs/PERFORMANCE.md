# Performance and quality

## Important caveat about the development machine

All measurements below were taken on the development laptop: an Apple M2 (8 cores, 16 GB RAM). During the whole session its **battery was at 1% and not charging on AC power**, and the CPU was **severely throttled**:

| Sanity check | Measured | Typical healthy M2 |
|---|---|---|
| Pure-Python loop, 5M iterations | 5.36 s | ~0.3 s |
| NumPy 1500×1500 matmul throughput | 38 GFLOPS | several hundred GFLOPS |
| Ollama `qwen2.5:3b` generation | 5.7 tokens/s | ~30–50 tokens/s (Metal) |

So the absolute latency figures here are **10–20× worse than the same hardware in normal condition**, and much worse than a GPU server. They prove the pipeline works end to end. They are **not** representative of production performance and must not be quoted as such. Re-run `scripts/benchmark.py`, `scripts/e2e_call.py` and `scripts/loadtest.py` on the target server and replace this section.

## Measured on the throttled machine (27 Oct 2026)

### End-to-end voice call (`scripts/e2e_call.py`, 6 caller turns, recorded audio over websocket)

| Stage (per turn) | Average | p95 |
|---|---|---|
| Speech-to-text (Whisper `base.en`, int8 CPU) | 5.7 s | 13.4 s |
| Knowledge retrieval (embed query + hybrid search) | 0.05–0.13 s | |
| LLM first token (`qwen2.5:3b`, Ollama) | 9.1 s | 16.0 s |
| TTS for the whole reply (Kokoro int8) | 18.4 s | 32.4 s |
| **Caller stops → first agent audio** | **28.7 s** | **44.8 s** |

All six turns were transcribed and answered, the conversation flowed and the call ended on "bye". Grounded-answer checks passed 5 of 6. The one "failure" was STT mishearing "who founded" as "who find it" on synthetic accented speech; the agent then gave the founding year from the knowledge base and invented nothing. The post-call summary, extraction and three workflows (enquiry, callback, follow-up ticket) all ran.

### Components

| Component | Measurement |
|---|---|
| Whisper `base.en` | 14.4 s to transcribe 4.0 s of audio (RTF 3.6); transcript exact |
| Whisper `small.en` | 46 s for the same 4.0 s (RTF 11.5); transcript exact |
| Kokoro fp32 (before heavy throttling) | RTF 0.98–2.8 across 10 voices |
| Kokoro int8 / fp32 (throttled) | RTF 5.3 / 8.6 on a short sentence |
| Piper (cori-high / lessac-medium, throttled) | RTF 4.8 / 9.1 |
| Retrieval (44 chunks) | 45–130 ms including query embedding |
| Relevance calibration | In-domain questions scored ≥ 0.60 cosine; out-of-domain ≤ 0.58 → threshold 0.58 |

*RTF = processing time ÷ audio duration; below 1.0 is faster than real time.*

### Automated tests

`cd backend && python -m pytest -q` → **66 passed**. Coverage includes phone-number handling, guardrails, chunking, the summariser's anti-hallucination sanitising, workflow conditions, idempotency and approval gates, RBAC, login lockout, the knowledge lifecycle, outbound consent, country, spacing and bulk gates, simulator dial outcomes (no answer, busy), campaign retry scheduling, privacy erasure, and call-engine behaviour (greeting disclosure, grounded replies, `[END_CALL]`, opt-out, transfer, escalation without transfer, LLM outage fallback, silence reprompt and hangup, VAD endpointing and barge-in on real speech audio).

## Performance targets for production hardware

These are **targets to verify**, based on the component choices. They are not measurements.

| Metric | Minimum tier (healthy 8-core CPU / Apple M-series, 16 GB) | Recommended tier (NVIDIA 12–16 GB GPU) |
|---|---|---|
| End-of-speech detection | 650 ms (configurable silence) | 500–650 ms |
| STT for a 3 s utterance | < 1.0 s (`small.en` int8) | < 0.3 s (`large-v3-turbo` fp16) |
| LLM first token | < 1.0 s (3B) | < 0.4 s (7B) |
| First TTS audio (first clause) | < 0.8 s (Kokoro int8) | < 0.2 s |
| **Caller stops → agent starts speaking** | **≈ 2.5–3.5 s** | **≈ 1.2–1.8 s** |
| Concurrent calls | 1–2 | 4–8 |
| Transcription accuracy (WER, real Zambian callers) | to be measured | to be measured |
| Answer correctness (`eval_answers.py`) | ≥ 90% with 0 invented facts | ≥ 95% |

How to verify: `scripts/benchmark.py` (component speed, WER on the clips, CPU/RAM), `scripts/e2e_call.py` (per-turn latency), `scripts/loadtest.py --calls N` (find the largest N whose median response stays within target, then set `MAX_CONCURRENT_CALLS`), and `scripts/eval_answers.py` (accuracy and hallucination).

## Resource footprint (loaded models, measured)

* Process RSS with Whisper `base.en`, Kokoro int8 and bge-small loaded: about 1.2 GB. Add about 1.5 GB for `small.en` plus Kokoro fp32.
* Ollama `qwen2.5:3b` (Q4): about 2.5 GB RAM; `qwen2.5:7b`: about 5.5 GB.
* Disk for models: Kokoro 620 MB (all variants), Whisper base+small 1 GB, Piper 240 MB, embeddings 64 MB, LLM 2–5 GB.
