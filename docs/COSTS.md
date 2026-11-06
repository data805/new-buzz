# Costs: what is free and what is not

## Free / open-source components (no licence fees)

| Component | Licence | Notes |
|---|---|---|
| Asterisk 20 LTS | GPL-2.0 | Runs as a separate process; no obligations for internal use |
| faster-whisper, CTranslate2, Whisper models | MIT | |
| Silero VAD | MIT | |
| Kokoro-82M | Apache-2.0 | Commercial use allowed |
| Piper (optional) | GPL-3.0 | Optional; only if installed. Internal use is fine; distributing a modified build triggers GPL obligations |
| Ollama | MIT | |
| Qwen2.5 3B / 7B Instruct | Qwen licence (3B: Qwen Research Licence; 7B: Apache-2.0) | **Check:** the Qwen2.5-**3B** weights use the Qwen Research licence. For commercial production prefer **Qwen2.5-7B (Apache-2.0)**, Llama 3.1/3.2 (Llama community licence) or another Apache/MIT model |
| fastembed + bge-small-en-v1.5 | Apache-2.0 / MIT | |
| FastAPI, SQLAlchemy, React, Tailwind, Recharts | MIT / BSD | |
| PostgreSQL | PostgreSQL licence | |
| n8n (self-hosted) | Sustainable Use Licence | Free for internal business use; not for reselling as a service |

## Unavoidable costs

| Item | Typical range (verify locally) | Notes |
|---|---|---|
| **Phone number (DID) + SIP trunk** | Monthly number fee plus per-minute charges for outbound calls; inbound to a geographic number is usually free or cheap | The only telecom cost. Get quotes from Zamtel, Paratus and others (see TELEPHONY_ZAMBIA.md). Toll-free numbers cost more. |
| **Server** | See tiers below | One-off hardware or monthly cloud rental |
| **Electricity / internet** (on-premise) | Site-dependent | Consider load-shedding: UPS or solar for an on-premise box |
| **Domain + TLS** | ~USD 10–15/year for the domain; TLS free (Let's Encrypt) | |
| **SMS / email** (optional) | Per-message SMS fees; SMTP via an existing mailbox is often free | Only if those workflows are enabled |
| **Hosted LLM fallback** (optional) | Pay-per-token | Optional; only billed when the local model fails or if chosen as primary |

## Hardware tiers (sizing guidance)

The latency figures below are **targets to verify** with `scripts/benchmark.py` on the chosen machine. They are not measurements; see [PERFORMANCE.md](PERFORMANCE.md) for what was actually measured.

| Tier | Example | Models | Concurrent calls (target) |
|---|---|---|---|
| Minimum (pilot) | 8-core x86 / Apple M-series, 16 GB RAM, no GPU | small.en int8, Kokoro int8, qwen2.5:3b | 1–2 |
| Recommended | 8+ cores, 32 GB RAM, NVIDIA GPU with 12–16 GB VRAM (e.g. RTX 4060 Ti 16 GB / L4) | large-v3-turbo (GPU), Kokoro (GPU), qwen2.5:7b (GPU) | 4–8 |
| Scale | Separate GPU inference box(es) + app/Asterisk box | as above + vLLM | 10+ |

## Cost per call (how to estimate)

`cost_per_call ≈ telecom_minutes × per_minute_rate + server_monthly / calls_per_month`

* Inbound calls to a local number: usually close to zero telecom cost; the main cost is the amortised server.
* Outbound calls: dominated by the provider's per-minute rate to Zambian mobile networks.
* Example method (fill in real quotes): a recommended-tier cloud GPU server at USD X/month handling 3,000 calls of 3 minutes costs X/3000 per call in compute, plus 3 × the per-minute rate for outbound calls. Self-hosted compute has **no per-call API fees**, unlike commercial voice-AI platforms, which typically bill per minute for STT, LLM and TTS on top of telephony.
