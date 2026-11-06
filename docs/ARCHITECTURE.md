# Architecture

## Goals and constraints

* Answer and place real phone calls on Zambian (+260) numbers, sounding like a competent, friendly human representative.
* Ground every company fact in approved BongoHive knowledge; say "I don't know" rather than invent.
* Run on free, self-hostable components wherever quality allows; keep paid dependencies to the unavoidable ones (phone line and server).
* Be operable by staff through a dashboard, with access control, auditability and safe defaults for outbound calling.

## System overview

```
                 ┌──────────────────────────── Voice Agent server (one process) ───────────────────────────┐
 PSTN / mobile   │                                                                                           │
 caller ──► SIP ─┼─► Asterisk ──AudioSocket (8 kHz PCM, TCP)──► AudioSocketTransport ┐                       │
 trunk (+260)    │      ▲   ▲                                                          │                       │
                 │      │   └── dialplan CURL() → /api/telephony/asterisk/register     ▼                       │
                 │      └────── AMI (originate, redirect) ◄── AsteriskProvider    CallSession (per call)       │
                 │                                                                  │  VAD (Silero, ONNX)       │
 Browser test ───┼─► /ws/voice (16 kHz PCM over WebSocket) ─► WebSocketTransport ───┤  STT (faster-whisper)     │
 console         │   /ws/simphone (simulated handset)                               │  Retrieval (bge + BM25)   │
                 │                                                                  │  LLM (Ollama / OpenAI API)│
                 │                                                                  │  TTS (Kokoro / Piper)     │
                 │                                                                  ▼                           │
                 │   Event bus ──► Workflow engine ──► internal records / webhooks / n8n / email / SMS / calls  │
                 │       ▲                                                                                      │
                 │   Post-call summariser (LLM, JSON)        Outbound dialer (campaigns, retries, compliance)   │
                 │                                                                                           │
                 │   FastAPI REST API + React dashboard     SQLite or PostgreSQL     Live hub (/ws/live)        │
                 └───────────────────────────────────────────────────────────────────────────────────────────┘
```

### The call engine (`backend/app/voice/pipeline.py`)

Every channel implements a small `Transport` interface (send audio, clear audio, send event, hang up, transfer). `CallSession` owns the conversation:

1. **Input**: caller PCM is resampled to 16 kHz and fed to a streaming Silero VAD (32 ms frames, recurrent state kept between chunks). Speech start needs `min_speech_ms`; end of turn is `endpoint_silence_ms` of silence (650 ms default). A 320 ms pre-roll buffer keeps word onsets.
2. **Barge-in**: while the agent speaks or thinks, the VAD start threshold is raised (echo and backchannel resistance). If the caller keeps talking for `barge_in_min_ms` (350 ms), playback is cancelled, the far end is told to flush its buffer, the in-flight LLM/TTS task is cancelled, and only the sentences actually played are kept in history. Short "mm-hmm"s while the agent talks are ignored. If the caller cuts in before any audio was played, the two fragments are merged into one question.
3. **Understanding**: the utterance goes to faster-whisper (greedy decoding, domain hotwords such as BongoHive, Lusaka, kwacha). Empty or no-speech transcripts are discarded.
4. **Guardrails before the LLM** (`agent/guardrails.py`), deterministic so they work even if the model misbehaves: opt-out ("stop calling me") is recorded and honoured immediately; requests for a human trigger escalation; voicemail greetings are detected on the first utterance of outbound calls.
5. **Grounding**: hybrid retrieval over approved knowledge (dense bge-small cosine similarity fused with BM25 by reciprocal rank). A calibrated relevance threshold (0.58) means off-topic questions retrieve nothing, and the prompt instructs the model to admit it doesn't know and offer a callback. Short follow-ups also retrieve with the previous question as context.
6. **Generation**: the LLM streams tokens. A `TagFilter` strips control tags (`[TRANSFER]`, `[END_CALL]`) even when split across tokens. A `SentenceChunker` emits the first clause early (on a comma once it is long enough) so speech starts before the reply is finished.
7. **Speech**: each chunk is synthesised by Kokoro (or Piper) while the previous one plays. A player task sends 20 ms frames paced to real time with a small lead (60 ms telephony, 250 ms browser), which keeps barge-in responsive and makes recordings line up.
8. **Timers**: silence reprompts ("Are you still there?") then a polite hang-up; maximum call length; outbound calls wait about 1.5 s for the callee's "Hello?" before greeting.
9. **After the call**: the session saves duration, outcome and latency statistics, then the summariser extracts a structured record (summary, intent, team, sentiment, contact details, callback request, feedback, action items). Contact details are only kept if they appear in the caller's own words. This record is published as `call.completed` for the workflow engine.

### Telephony (`backend/app/telephony/`)

* `TelephonyProvider` interface: `originate()`, `health()`, and a dial-outcome callback for attempts that never reach the agent (busy, no answer, failed, voicemail).
* **Simulator** (default): inbound calls come from the browser test console; outbound calls ring a browser tab registered as a handset (`/ws/simphone`). It supports answer, decline (busy) and ignore (no answer), and numbers without a handset get a default outcome, so the dialer, retries and workflows can be tested end to end with no phone line.
* **Asterisk**: the production path for real SIP trunks. Media goes over AudioSocket. Control goes over AMI: originate with an async `OriginateResponse` mapped to busy, no answer or congestion, plus Redirect for transfer to a human. Answering-machine detection uses `AMD()` in the dialplan. Before AudioSocket starts, the dialplan calls `/api/telephony/asterisk/register`, which passes caller ID, DID and channel name and lets the app refuse calls when at capacity. See [TELEPHONY_ZAMBIA.md](TELEPHONY_ZAMBIA.md).

### Knowledge (`backend/app/knowledge/`)

Lifecycle: **upload / URL / FAQ → extract text → chunk by heading → embed → draft → approve → indexed**. Only approved sources are retrievable; editing content returns a source to draft. Web imports are restricted to an allowlist of domains and refuse private addresses (SSRF). The index is held in memory and rebuilt from the database on every approval change. A few thousand chunks search in under 10 ms; above roughly 50k chunks, switch to pgvector (the `KnowledgeIndex.search` contract stays the same).

### Workflows (`backend/app/workflows/`)

Event-driven: `trigger` (event type) + `conditions` (dot-path field operators) + ordered `actions`. Every run has a unique idempotency key (`workflow:event:call_id`), so duplicate events never re-execute. Retried runs skip steps that already succeeded. Internal actions (enquiry, ticket, callback, feedback, notification) run immediately. External actions (webhook, n8n, email, SMS, outbound call) only run when an admin has approved the workflow definition; editing the definition revokes that approval. They never run for test-console calls, can be gated per run (`require_run_approval`), and are retried with backoff. Webhooks are restricted to allow-listed hosts and HMAC-signed.

### Outbound dialer (`backend/app/outbound/dialer.py`)

Campaign state machine: `draft → pending_approval → approved → running ⇄ paused → completed/cancelled`. Every call, whatever its source, passes `check_can_call`:

* the global kill switch (`OUTBOUND_ENABLED`, off by default);
* allowed country codes;
* the number is a known contact with recorded consent, and has not opted out;
* minimum hours between connected calls to the same number;
* the hourly volume cap and concurrent-channel capacity;
* the campaign calling window in Africa/Lusaka time.

Retries back off as `retry_delay × attempt` for no-answer, busy, failed and voicemail, up to `max_attempts`. Campaigns larger than `BULK_THRESHOLD` cannot be approved unless `ALLOW_BULK_OUTBOUND=true`, and need an admin approver.

### Security

* Session JWT in an httpOnly, SameSite=Strict cookie. Argon2 password hashing, lockout after 5 failures, forced password change for bootstrap and admin-set passwords, and token versioning so role changes and password resets invalidate sessions.
* Roles: viewer < operator < manager < admin, enforced on every endpoint and websocket.
* API keys for integrations: stored as HMAC hashes, scoped, revocable, rate-limited.
* Rate limits on login, the Asterisk hook and API keys. Security headers include a strict CSP.
* An audit log for logins, settings changes, approvals, recording access, imports and erasure.
* Secrets come only from the environment, are never stored in the database, and are reported as set/unset in the UI. SIP credentials are rendered into Asterisk config at container start.
* The production start-up check refuses default secrets or insecure cookies.

### Data model (main tables)

`users, api_keys, audit_log, settings` · `knowledge_sources, knowledge_chunks` · `contacts, campaigns, campaign_contacts` · `calls, call_turns` · `enquiries, tickets, callbacks, feedback` · `workflows, workflow_runs`.

## Technology choices

| Component | Chosen | Alternatives evaluated | Why |
|---|---|---|---|
| STT | **faster-whisper** (MIT) `small.en` CPU int8; `large-v3-turbo` on GPU | whisper.cpp, Vosk, NeMo Parakeet, Meta MMS | Best accuracy/latency/licence balance on CPU and GPU, handles accented English well, hotword biasing. Vosk is faster but noticeably less accurate on accented speech; Parakeet is English-only and needs NeMo/torch. |
| VAD | **Silero v6** ONNX (MIT), shipped with faster-whisper | WebRTC VAD, energy threshold | Far more robust to noise; no torch needed. |
| LLM | **Ollama** + `qwen2.5:3b-instruct` (CPU/Apple), `qwen2.5:7b-instruct` / `llama3.1:8b` (GPU); any OpenAI-compatible API as primary or fallback | llama.cpp server, vLLM, hosted APIs | Ollama is the simplest self-hosted runtime with good Apple/NVIDIA support. Qwen2.5 follows grounding instructions well for its size. Apache-2.0 for 3B/7B. A hosted fallback keeps calls alive during outages. |
| TTS | **Kokoro-82M** ONNX (Apache-2.0), voice `af_heart` | Piper (GPL-3.0), XTTS-v2 (non-commercial licence), F5-TTS (CC-BY-NC weights), Orpheus 3B (GPU-heavy), MMS-TTS (CC-BY-NC) | Most natural open-licence voice that still runs on CPU. Piper is kept as an optional fallback for weak hardware. See [VOICE_EVALUATION.md](VOICE_EVALUATION.md). |
| Telephony | **Asterisk 20 LTS** + PJSIP + AudioSocket + AMI | FreeSWITCH + mod_audio_fork, Twilio Media Streams, Jambonz | Works with any SIP trunk, including Zambian carriers; AudioSocket is a tiny, robust media protocol; huge community; GPL-2.0 used as a separate process. |
| Retrieval | **fastembed bge-small-en-v1.5** + BM25, in-memory | pgvector, Qdrant, Chroma | No extra service, fast enough for a company knowledge base, easy to swap. |
| Backend | **FastAPI** + SQLAlchemy 2 async | Django, Node | Async websockets plus the Python ML ecosystem. |
| DB | **PostgreSQL** (prod) / SQLite (dev) | — | Standard and free; Alembic migrations. |
| Automation | **Built-in engine** + **n8n** integration | n8n only | Safety-critical logic (idempotency, approvals, consent) stays in-process; n8n handles CRM, Sheets and Slack glue via signed webhooks and the API. |
| Frontend | React 19 + Vite + Tailwind 4 | — | Fast to build; served as static files by FastAPI. |

## Scaling

The engine is CPU/GPU-bound per concurrent call. `MAX_CONCURRENT_CALLS` caps sessions, and extra inbound calls hear "all circuits busy". To scale out:

1. Move the LLM to a GPU box (or several Ollama/vLLM replicas behind a load balancer).
2. Run STT/TTS on GPU (`STT_DEVICE=cuda`; onnxruntime-gpu for Kokoro).
3. Run several app instances behind Asterisk using a dialplan round-robin on AudioSocket targets. The dialer and retention jobs must run on exactly one instance; the shared DB makes the rest stateless apart from live-monitoring state.
