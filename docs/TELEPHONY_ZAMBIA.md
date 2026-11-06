# Connecting to the Zambian telephone network (+260)

> **Status:** the Asterisk integration (SIP trunk → Asterisk → AudioSocket/AMI → agent) is implemented and the configuration is provided, but **real Zambian calls have not been tested end to end**, because no SIP trunk credentials were available during development. Everything up to the SIP trunk was verified with the local simulator. Do not consider real calling production-ready until you complete the test plan at the end of this document.

## 1. What open-source software does *not* give you

Asterisk (or FreeSWITCH) is a phone switch, not a phone line. To reach the public network you need a **telecom provider** that terminates calls to and from +260 numbers, and gives you a **number (DID)** and a **SIP trunk** that Asterisk registers to. This is the main unavoidable recurring cost.

## 2. Provider options (research summary, October 2026)

Verify current offers, prices and terms directly; nothing below is an endorsement or a quote.

| Option | What it is | Notes |
|---|---|---|
| **Zamtel** (state-owned operator) | Lists **SIP trunking for PBX systems and contact centres** among its corporate landline products ([zamtel.zm](https://www.zamtel.zm/internet-and-voice)). | Local geographic numbers (e.g. 0211 Lusaka). A local operator usually has the best caller-ID presentation and local rates. BongoHive's existing relationship with Zamtel (see its case study) may help. Ask for a SIP trunk to an Asterisk PBX with G.711 A-law, plus whether it is registration- or IP-authenticated. |
| **Paratus Zambia** | Business connectivity provider offering business voice ([paratus.africa/zambia](https://paratus.africa/zambia/)). | Could bundle internet plus voice to the server site. Ask whether SIP trunks with +260 DIDs are offered. |
| **Liquid Intelligent Technologies, Airtel Business, MTN Business** | Enterprise carriers in Zambia. | Liquid did not show up in this research, so its offer could not be confirmed. Ask each for "SIP trunk with DIDs for an on-premise/cloud IP-PBX". Mobile operators may only offer mobile-number/VPN products, so confirm SIP availability. |
| **International DID/SIP wholesalers** (e.g. [DIDWW](https://www.didww.com/voice/global-sip-trunking/Zambia), [CommPeak](https://www.commpeak.com/services/sip-trunking/zambia), [PBX.IM](https://www.pbx.im/services/sip-trunking-provider/zm)) | Sell Zambian numbers and termination, usable from any server worldwide. | Fastest to start, self-service. Check **number type** (geographic, mobile or toll-free), **inbound vs outbound support**, **caller-ID presentation on Zambian networks**, **documentation requirements** (proof of address, company registration), and whether using a foreign carrier for Zambian numbers is permissible for your use (see §5). |

**Questions to ask any provider**

1. Can you provide a +260 number (geographic 0211 / mobile / toll-free) with inbound **and** outbound calling?
2. SIP details: server host and port, UDP/TCP/TLS, registration vs IP authentication, codecs (G.711 A-law preferred), DTMF (RFC 4733).
3. Which number format do you expect on outbound calls (E.164 `+260…`, `260…`, or national `0…`)? This sets `ASTERISK_DIAL_FORMAT`.
4. Will our number be presented as caller ID on Airtel, MTN and Zamtel mobiles?
5. Per-minute rates to Zambian mobile and fixed numbers, monthly number fee, channel limits, setup fees.
6. Documentation needed (company registration, ZRA TPIN, ID, proof of address).
7. Do you support SRTP/TLS for encrypted calls?

## 3. Setup steps

### 3.1 Server and network

* A Linux server (Ubuntu 22.04/24.04) with a **static public IP**, either in Lusaka (lower latency to Zambian networks) or a nearby cloud region (Johannesburg / Cape Town).
* Firewall: allow **UDP 5060** (or TCP/TLS 5061) and **UDP 10000–10200** (RTP) **only from the provider's IP ranges**. Keep 5038 (AMI) and 9092 (AudioSocket) closed to the internet.
* The app and Asterisk can share the host (see `docker-compose.yml`, profile `telephony`).

### 3.2 Configure `.env`

```ini
TELEPHONY_PROVIDER=asterisk
ASTERISK_AMI_PASSWORD=<random>
ASTERISK_CALLBACK_TOKEN=<random>
ASTERISK_DIAL_FORMAT=e164          # per provider
OUTBOUND_CALLER_ID=+260211xxxxxx    # your DID
HUMAN_TRANSFER_TARGET=+26097xxxxxxx # reception mobile, or PJSIP/reception for a SIP desk phone

SIP_TRUNK_HOST=sip.provider.example
SIP_TRUNK_PORT=5060
SIP_TRUNK_TRANSPORT=udp
SIP_TRUNK_USERNAME=<from provider>  # leave empty for IP-authenticated trunks
SIP_TRUNK_PASSWORD=<from provider>
SIP_DID=260211xxxxxx                # as the provider sends it in the Request-URI
SIP_EXTERNAL_IP=<server public IP>
```

### 3.3 Start

```bash
docker compose --profile telephony up -d --build
docker compose logs -f asterisk          # look for "Registered" for zm-trunk-reg
docker compose exec asterisk asterisk -rx "pjsip show registrations"
docker compose exec asterisk asterisk -rx "pjsip show endpoint zm-trunk"
```

In the dashboard, **System → Status → Telephony** should show `ami_connected: true`.

### 3.4 Routing

* **Inbound**: any number the trunk delivers enters `[from-trunk]`, which answers, registers the call with the app (caller ID, DID, channel) and starts AudioSocket. If the app is at `MAX_CONCURRENT_CALLS`, callers hear "all circuits are busy". Multiple DIDs can be routed to different behaviour by matching the DID in the dialplan or in the app (`did` is passed through).
* **Outbound**: the dialer sends AMI `Originate` to `PJSIP/<number>@zm-trunk`. Answer → `[outbound-agent]` runs `AMD()` (answering-machine detection) → AudioSocket. Busy, no answer and congestion come back as `OriginateResponse` reasons and are recorded as call outcomes with retries.
* **Transfer**: the agent redirects the channel to `[agent-transfer]`, which dials `HUMAN_TRANSFER_TARGET` for 30 s, then apologises and hangs up if nobody answers. The app also creates an escalation ticket.

### 3.5 Call recording

Off by default. When enabled in Agent settings, the greeting always includes the recording disclosure, and recordings are stored as two-channel WAV files in `data/recordings` with retention deletion. Only managers and admins can play them, and every access is audited.

## 4. Error handling built in

| Situation | Behaviour |
|---|---|
| Trunk down / AMI disconnected | AMI reconnects with backoff; outbound attempts are recorded as `failed` with the cause and retried by campaigns; status shows on the dashboard. |
| App unreachable from dialplan | `CURL()` returns empty → caller hears an announcement → hangup. |
| Capacity reached | Inbound: busy announcement (cause 17). Outbound: the attempt is deferred. |
| Caller hangs up | AudioSocket closes → session ends, transcript and outcome saved, post-call workflows run. |
| Model failure mid-call | The agent apologises, offers a callback and marks the call escalated; the LLM fallback provider is used if configured. |
| Voicemail on outbound | `AMD()` plus transcript heuristics → hang up (and retry later) or leave the configured message. |
| Silence | Reprompt, then polite hangup (`abandoned`). |

## 5. Regulatory checklist (Zambia) — confirm with counsel

* **ZICTA** licenses electronic communications services. Using a *licensed* provider's SIP trunk for your own business calls normally keeps you an end-user customer. Confirm with the provider that your intended use (automated inbound and outbound customer calls) is permitted under their licence and terms.
* **Data Protection Act No. 3 of 2021**: register as a data controller with the Office of the Data Protection Commissioner, appoint a DPO, document lawful basis and retention, and handle data-subject requests. See [COMPLIANCE.md](COMPLIANCE.md).
* **Unsolicited communications**: Zambian law, including the Cyber Security and Cyber Crimes Act 2021 (which lists transmitting multiple unsolicited electronic messages as an offence) and ZICTA's enforcement against spam, makes consent-based outbound calling essential. The platform enforces consent and opt-out and blocks bulk calling by default.

## 6. End-to-end acceptance test (do this before going live)

1. `pjsip show registrations` shows *Registered*. Call your DID from an Airtel, an MTN and a Zamtel mobile, and from a landline. Each call is answered, the greeting is heard, a question gets an answer, and the call appears in Call history with the correct caller ID.
2. Check audio quality both ways (no one-way audio: if there is, fix `SIP_EXTERNAL_IP`, `SIP_LOCAL_NET` and the RTP firewall).
3. Barge-in works over the phone (talk over the agent). Adjust `barge_in_min_ms` and the VAD threshold if line echo causes false interruptions.
4. Say "I want to speak to a person" during office hours: the call transfers to `HUMAN_TRANSFER_TARGET`.
5. Add your own mobile as a consented contact, enable `OUTBOUND_ENABLED`, and place a single call. Test answer, reject (busy), ignore (no answer) and voicemail (switch the phone off), and confirm the outcomes and retries.
6. Confirm your caller ID shows correctly on each network.
7. Record the results (date, networks, MOS or subjective quality, latency) in [STATUS.md](STATUS.md).
