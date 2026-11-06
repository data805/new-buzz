# Project status

_Last updated: 6 November 2026_

## Completed and verified locally

| Area | Feature | Verified by |
|---|---|---|
| Voice pipeline | Streaming VAD, endpointing, Whisper STT, grounded LLM replies streamed sentence-by-sentence into Kokoro TTS, paced playback | `e2e_call.py` (6-turn recorded-audio call over websocket), session tests |
| | Barge-in (caller interrupts agent), backchannel suppression, merged fragments | `test_barge_in_interrupts_agent` on real speech audio |
| | Silence reprompt and polite hangup, max call duration | `test_silence_reprompt_then_hangup` |
| | AI disclosure in every greeting; recording disclosure when enabled. If the caller speaks before the disclosure has actually played (e.g. slow TTS), the next reply starts with a short disclosure | `test_greeting_includes_ai_disclosure`, `test_disclosure_given_when_caller_speaks_before_greeting_plays` |
| | LLM failure → apology + callback offer + escalation flag; optional fallback provider | `test_llm_outage_falls_back_gracefully` |
| | Opt-out detection (deterministic) → do-not-call, campaign queues cleared | `test_opt_out_is_deterministic` |
| | Human escalation: transfer when possible, ticket + callback offer otherwise | session tests |
| | Voicemail heuristics (outbound) | unit test |
| Knowledge | Upload PDF/DOCX/PPTX/HTML/MD/TXT/CSV, approved-domain URL import (SSRF-safe), FAQ editor, draft → approve → index lifecycle, edit → re-approval, hybrid retrieval with calibrated "don't know" threshold, retrieval tester | API tests, calibration run |
| | Sample knowledge from bongohive.co.zm, flagged as sample with a dashboard warning | bootstrap |
| Post-call | LLM summary + structured extraction with anti-hallucination sanitising; summary prompt forbids claiming the agent "provided" details it said were unavailable | e2e run (enquiry, callback, ticket created), unit test; summary re-checked 3/3 correct on the e2e transcript |
| Workflows | Engine, 9 templates, conditions, `{{placeholders}}` with fallbacks, idempotent runs, admin approval for external actions, per-run approval, retries, dry-run | API tests |
| | Actions: enquiry, ticket, callback, feedback, notification, webhook (HMAC-signed, host allowlist), n8n, email (SMTP), SMS (Twilio / generic HTTP), outbound call | internal actions tested; external ones tested with a stub (see "needs credentials") |
| Outbound | Campaign approval flow, consent and attestation, opt-out, calling windows, hourly caps, spacing, retries with backoff, bulk block, kill switch, idempotency | API tests incl. simulator no-answer/busy and retry scheduling |
| Telephony | Provider abstraction; simulator (browser call, simulated handset with answer/decline/ignore); Zambian number validation and operator lookup | API + unit tests |
| | Asterisk provider: AudioSocket server, AMI client (originate, outcomes, redirect), dialplan hook, AMD, config templates, Docker image | code complete; **not tested against a live trunk** (see blockers) |
| Dashboard | Overview, live monitor, call history and detail (transcript, sources, latency, recording), follow-ups, knowledge, agent settings with voice preview, outbound (campaigns/contacts/single call), workflows (editor, approval, dry run, runs), test console (voice + typed + simulated phone), system (status, metrics, logs), security (users, roles, API keys, audit, privacy) | TypeScript build passes; API endpoints exercised by tests |
| Security | RBAC on all endpoints and websockets, Argon2, lockout, forced password change, session invalidation, scoped hashed API keys, rate limiting, CSP and headers, audit log, production start-up checks, secrets only in env | API tests |
| Privacy | Retention job, redaction option, data-subject erasure, audited recording access | API test (erasure) |
| Ops | Docker/Compose (app, Postgres, Ollama, Asterisk, n8n profiles), Alembic migration, model downloader, benchmark, eval, load-test scripts, full documentation | migration generated from models; `alembic check` reports no drift; `docker compose config` valid for all profiles |

## Test results

* `pytest`: **86 passed** (unit, API, call engine).
* Browser smoke test (`scripts/browser_smoke_test.mjs`, real Chrome): all 11 dashboard pages, inbound call, outbound call to a simulated handset with AI-disclosure check: **21/21**.
* Fresh-install check (27 Oct 2026): empty database → admin bootstrap → sample knowledge indexed (6 sources, 44 chunks) → 6-turn voice call → summary, enquiry, callback and ticket created.
* End-to-end voice call: all turns answered and grounded; 5/6 content checks passed (the miss was an STT error on synthetic speech, with no fabrication). Latency on the throttled laptop: 28.7 s average to first audio; see PERFORMANCE.md for why that isn't representative.
* Retrieval calibration: 14/14 in-domain questions above threshold, 10/10 out-of-domain below.

## Blockers / needs external input

| Item | Why blocked | What's needed |
|---|---|---|
| **Real Zambian calls** | No SIP trunk or number available | Provider account + DID (Zamtel / Paratus / other, see TELEPHONY_ZAMBIA.md), a server with a public IP, then run the acceptance test in §6 |
| **Realistic latency numbers** | Dev laptop's CPU throttled (battery 1%, not charging) | Re-run the benchmarks on healthy or target hardware |
| **Verified BongoHive knowledge** | Only public website content was available; no confidential data was assumed | Staff upload and approve verified documents, FAQs and policies; archive the sample sources |
| SMTP / SMS / n8n delivery | No credentials | Configure SMTP_*, SMS_*, N8N_BASE_URL and approve the corresponding workflows |
| Compliance sign-off | Legal review required | DPO/counsel complete the COMPLIANCE.md checklist; ODPC registration |
| Zambian accent / local languages | No permissively licensed models or data | See the VOICE_EVALUATION.md improvement path |

## Remaining work (recommended next steps)

1. Connect a trial SIP trunk and complete the telephony acceptance tests; tune barge-in and VAD thresholds for real line echo.
2. Benchmark on target hardware; set `MAX_CONCURRENT_CALLS`; choose the production LLM (licence permitting, e.g. `qwen2.5:7b-instruct`).
3. Collect 30–50 consented real calls to measure WER and answer accuracy with real Zambian callers.
4. Replace the sample knowledge; run `eval_answers.py` with BongoHive-specific questions added.
5. Optional: Playwright UI tests; pgvector backend for very large knowledge bases; multi-instance deployment.
