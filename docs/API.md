# API and integrations

Interactive OpenAPI docs: **`/api/docs`** (served by the running app).

## Authentication

* **Dashboard users**: `POST /api/auth/login {email, password}` sets an httpOnly session cookie (SameSite=Strict). All `/api/*` endpoints and websockets use it. Roles: viewer < operator < manager < admin.
* **Machine integrations** (n8n, CRM, scripts): create a scoped API key in **Security & access → API keys**, then send `X-API-Key: vak_…` (or `Authorization: Bearer vak_…`). Keys are rate-limited to 120 requests/min.

| Scope | Allows |
|---|---|
| `calls:create` | `POST /api/v1/calls` |
| `calls:read` | `GET /api/v1/calls/{id}` |
| `events:create` | `POST /api/v1/events` |
| `contacts:write` | `POST /api/v1/contacts` |

## Integration endpoints (`/api/v1`)

### Request an outbound call
```http
POST /api/v1/calls
X-API-Key: vak_...
{"phone": "+260971234567", "purpose": "Confirm the coworking tour on Friday 10:00", "idempotency_key": "crm-booking-8812"}
```
`200 {"call_id": "...", "status": "queued"}`. Returns `422` with the reason when blocked (outbound disabled, no consent, opted out, called recently, hourly cap, capacity). The same `idempotency_key` never produces a second call.

### Get call result
`GET /api/v1/calls/{call_id}` → status, outcome, duration, summary, intent, action items and extracted fields.

### Post a custom event (triggers workflows with trigger `custom`)
```http
POST /api/v1/events
{"type": "custom", "data": {"kind": "lead", "phone": "+260971234567", "name": "Chanda"}, "idempotency_key": "form-123"}
```

### Upsert a contact (with consent)
```http
POST /api/v1/contacts
{"phone": "0971234567", "name": "Chanda", "consent_status": "granted", "consent_source": "Website form 2026-10-27"}
```

## Outgoing webhooks (workflow actions)

`webhook` and `n8n` actions POST JSON. The body is either the event payload or the rendered `body` param. Headers:

* `Content-Type: application/json`
* `X-Signature-SHA256: hex(hmac_sha256(WEBHOOK_SIGNING_SECRET, raw_body))` when the secret is set

Targets must be in `WEBHOOK_ALLOWED_HOSTS` (the n8n host is allowed automatically). Failed deliveries retry after 2, 5 and 15 s.

### `call.completed` payload
```json
{
  "call_id": "…", "direction": "inbound", "channel": "asterisk", "phone": "+260971234567",
  "outcome": "completed", "escalated": false, "campaign_id": null, "is_test": false,
  "extracted": {
    "summary": "…", "intent": "coworking", "team": "coworking", "sentiment": "positive", "resolved": false,
    "caller_name": "Mwila", "caller_phone": "0971234567", "caller_email": null, "organisation": null,
    "enquiry_topic": "Private office pricing", "enquiry_details": "…", "callback_requested": true,
    "callback_time": "tomorrow morning", "needs_human": true, "feedback_rating": null, "feedback_comment": null,
    "action_items": ["Send private office pricing"]
  }
}
```
Other events: `call.started`, `call.failed` (`outcome`: no_answer|busy|failed|rejected|voicemail), `call.escalated` (`reason`), `call.transferred`, `contact.opted_out`, `enquiry.created`, `callback.created`, `ticket.created`, `feedback.received`, `campaign.completed`, `custom`.

## n8n recipe

1. `docker compose --profile automation up -d` and open n8n at `http://localhost:5678`.
2. Create a workflow with a **Webhook** trigger, method POST, path `voice-call-completed`. Optionally verify `X-Signature-SHA256` in a Code node.
3. Add nodes (Google Sheets append, Slack message, HubSpot contact, …) using fields from `{{$json.body.extracted}}`.
4. In the dashboard, set `N8N_BASE_URL=http://n8n:5678` (Docker) and enable the **Send call summary to n8n** workflow template; an admin approves it.
5. To let n8n place calls (e.g. a booking reminder the day before), create an API key with `calls:create` and use an **HTTP Request** node to `POST {app}/api/v1/calls`.

## Websockets

| Path | Role | Purpose |
|---|---|---|
| `/ws/voice?mode=test\|simulate&caller=+260…` | operator | Browser call to the agent. Binary: PCM16 mono, client→server 16 kHz, server→client 24 kHz. JSON events: `ready, call_started, state, transcript, assistant, sources, metrics, vad, clear, interrupted, hangup, call_ended`. Client JSON: `{type:"text",text}`, `{type:"hangup"}` |
| `/ws/simphone?number=+260…` | operator | Simulated handset for outbound: receives `incoming`, replies `answer`/`decline`, then the same audio protocol |
| `/ws/live` | operator | Live call monitoring feed (`snapshot`, `call`, `transcript`, `ended`) |

## Asterisk hook

`GET /api/telephony/asterisk/register?token=…&direction=inbound|outbound&caller=&did=&channel=&uuid=&amd=` (called by the dialplan) returns plain text: a UUID for AudioSocket, `BUSY` or `HANGUP`. It requires `ASTERISK_CALLBACK_TOKEN`.
