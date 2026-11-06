# Troubleshooting

Check **System → Status** and **System → Logs** in the dashboard first; most problems show up there.

| Symptom | Likely cause | Fix |
|---|---|---|
| App refuses to start: "Refusing to start: SECRET_KEY…" | Production mode with default secrets | Set a random `SECRET_KEY` (≥32 chars), change `ADMIN_PASSWORD`, `COOKIE_SECURE=true` |
| Language model shows "model not pulled" | Ollama doesn't have `LLM_MODEL` | `ollama pull <model>` (or `docker compose exec ollama ollama pull …`) |
| LLM `ConnectError` | Ollama not running / wrong `LLM_BASE_URL` | `ollama serve`; in Docker use `http://ollama:11434` |
| Agent says "I'm having a technical problem" | LLM timed out (first token > `LLM_FIRST_TOKEN_TIMEOUT_S`) | Use a smaller/faster model or a GPU, raise the timeout, configure `LLM_FALLBACK_*` |
| Very slow replies on a laptop | CPU throttled (battery saver, failing charger, thermal), or models too large | Check `pmset -g` (macOS) / `cpupower` (Linux); use `STT_MODEL=base.en`, `TTS_KOKORO_MODEL=kokoro/kokoro-v1.0.int8.onnx`; see PERFORMANCE.md |
| Agent answers "I don't have that information" to things that are in the knowledge base | Source not approved, or relevance threshold too high | Approve the source; use **Knowledge → Test retrieval**; lower `knowledge_min_score` slightly (Agent settings → Model) |
| Agent states wrong facts | Source content is wrong or ambiguous, or the model is weak | Fix the source text (one topic per `##` heading); use a larger model; lower the temperature |
| Microphone doesn't work in the test console | Not HTTPS, permission denied, or another app holds the mic | Use https or localhost; allow the mic in the browser; the console falls back to typed input |
| Agent interrupts itself / stops talking randomly | Speaker echo is picked up as barge-in | Use headphones; raise `barge_in_min_ms` and the VAD threshold |
| Agent replies too early, cutting the caller off | `endpoint_silence_ms` too low | Raise to 800–1000 ms |
| Agent feels sluggish | High endpoint silence or slow models | Lower `endpoint_silence_ms` to 500–600; faster models; GPU |
| Whisper transcribes silence as "Thank you." | Known Whisper hallucination | Already filtered; raise the VAD threshold in noisy rooms |
| `pjsip show registrations` → Rejected / Unregistered | Wrong SIP credentials, host, or transport | Check `SIP_TRUNK_*` against the provider's sheet; try TCP; check the firewall |
| Calls connect but there is silence or one-way audio | NAT / RTP blocked | Set `SIP_EXTERNAL_IP` and `SIP_LOCAL_NET`; open UDP `RTP_START–RTP_END` from the provider; `rtp set debug on` in the Asterisk CLI |
| Inbound calls hear "all circuits are busy" | `MAX_CONCURRENT_CALLS` reached, or the app is down | Check System → Capacity; raise the limit if hardware allows |
| Inbound call drops immediately | Dialplan `CURL()` can't reach the app or the token is wrong | `curl "http://127.0.0.1:8000/api/telephony/asterisk/register?token=…&caller=1"` from the Asterisk host; check `ASTERISK_CALLBACK_TOKEN` |
| Outbound attempts all `failed` with "AMI not connected" | AMI credentials, network or ACL | `ASTERISK_AMI_*` must match; check that `manager.conf` permits the app's IP |
| Outbound number rejected by the carrier | Wrong number format | Set `ASTERISK_DIAL_FORMAT` (national `0…` vs E.164) per provider |
| "Outbound calling is disabled" | Kill switch (by design) | `OUTBOUND_ENABLED=true` after the compliance review |
| "No recorded consent" | Contact consent not `granted` | Record consent with its source, or don't call |
| Workflow step "skipped: workflow not approved" | External action on an unapproved workflow | Admin approves it in Workflows |
| Webhook fails "not in WEBHOOK_ALLOWED_HOSTS" | Host not allow-listed | Add the host to `WEBHOOK_ALLOWED_HOSTS` |
| Account locked | 5 failed logins | Wait 15 minutes, or an admin resets the password |
| `SettingsError … webhook_allowed_hosts` | Invalid list syntax in .env | Use a comma-separated list or a JSON list |

## Useful commands

```bash
# health
curl -s localhost:8000/api/health
# run the automated tests
cd backend && python -m pytest -q
# end-to-end voice call against a running server (recorded audio)
python scripts/e2e_call.py --password '<admin password>'
# answer accuracy with the real model
cd backend && python ../scripts/eval_answers.py
# component benchmark on this machine
cd backend && python ../scripts/benchmark.py --out ../docs/results/benchmark.json
# concurrent calls
python scripts/loadtest.py --calls 4 --password '<admin password>'
# Asterisk CLI
docker compose exec asterisk asterisk -rvvv
```
