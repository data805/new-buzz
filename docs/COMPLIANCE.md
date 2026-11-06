# Responsible calling, privacy and compliance

> This is an engineering checklist, not legal advice. Have BongoHive's legal counsel and Data Protection Officer confirm the obligations below before enabling production calling.

## Applicable Zambian frameworks (research summary)

| Law / body | Relevance | Source |
|---|---|---|
| **Data Protection Act No. 3 of 2021** | Calls, transcripts, recordings and contact lists are personal data. Data controllers must **register with the Data Protection Commissioner** (registration certificates are valid for one year), appoint a **Data Protection Officer**, process lawfully and minimally, secure data, report breaches to the Commissioner **within 24 hours**, and honour data-subject rights. The Office of the Data Protection Commissioner is reported to be operational, with an online registration portal. | [ZambiaLII – Act](https://zambialii.org/akn/zm/act/2021/3/eng@2021-03-24), [Registration Regulations SI 58/2021](https://zambialii.org/akn/zm/act/si/2021/58/eng@2021-05-14/source), [Bowmans](https://bowmanslaw.com/insights/zambia-office-of-the-data-protection-commissioner-operational/) |
| **Cyber Security and Cyber Crimes Act 2021** | Lists transmitting multiple unsolicited electronic messages among its offences, which supports consent-only outbound contact. | [Bowmans summary](https://bowmanslaw.com/insights/zambia-the-cyber-security-and-cyber-crimes-act-2021-key-provisions-and-implications-for-service-providers-and-private-citizens/) |
| **ZICTA / Electronic Communications and Transactions Act** | ZICTA regulates electronic communications and has directed operators to curb unsolicited communications. Use a licensed provider for numbers and trunks. | [ITWeb](https://itweb.africa/article/bid-to-stop-spam-text-messages-in-zambia/j5alrvQlX16qpYQk), [ZICTA](https://en.wikipedia.org/wiki/Zambia_Information_and_Communications_Technology_Authority) |

Specific telemarketing rules (calling hours, mandatory identification wording, do-not-call registries) could not be confirmed from primary sources during this research. The defaults below follow international good practice; adjust them to counsel's advice.

## Safeguards implemented

| Requirement | How the platform enforces it |
|---|---|
| AI disclosure | Every greeting states that the caller is speaking to an AI assistant; the engine prepends a disclosure if a customised greeting omits it. Only an admin can turn this off (strongly discouraged). |
| Recording disclosure | Recording is off by default. When enabled, the disclosure is spoken in every greeting. |
| Consent for outbound | Calls are only placed to contacts with `consent_status=granted`, which requires a recorded consent source (how and when). CSV imports need an attestation for consenting rows. Campaign submission and approval fail if any contact lacks consent. |
| Opt-out | "Stop calling me" and similar phrases are detected deterministically on any call. The number is marked revoked, removed from all pending campaign queues, and cannot be re-consented by import. Staff can opt contacts out from the dashboard. |
| No bulk calling by default | `OUTBOUND_ENABLED=false` (global kill switch). Campaigns larger than `BULK_THRESHOLD` (50) cannot be approved unless `ALLOW_BULK_OUTBOUND=true`. |
| Human approval | Campaigns need admin approval, and any edit returns them to draft. Workflows with external actions need admin approval, and per-run approval is available. |
| Volume and timing limits | Campaign windows (default Mon–Fri 09:00–17:00 Lusaka time), per-campaign calls per hour and concurrency, a global hourly cap, 24 h minimum spacing between connected calls to the same number, and a maximum of 3 attempts with backoff. |
| Allowed destinations | `ALLOWED_COUNTRY_CODES=260` by default. |
| Duplicate prevention | Idempotency keys on API-triggered calls, campaign attempts and workflow runs. |
| Escalation to humans | Keyword triggers, a model-initiated `[TRANSFER]`, and an automatic offer after repeated unanswered questions. When transfer isn't possible, a ticket is created and a callback offered. |
| Data minimisation | Optional redaction of phone numbers and emails in stored transcripts. The summariser keeps contact details only if the caller actually said them. |
| Retention | Configurable transcript and recording retention (default 90 and 30 days), enforced every 6 hours. |
| Data-subject rights | Admin "erase by phone number" removes transcripts, recordings, summaries, enquiries, callbacks and tickets, and keeps a do-not-call marker. |
| Access control and audit | Role-based access; recordings are manager+ and every playback is audited; all approvals, settings changes, imports and erasures are in the audit log. |
| Security | See [ARCHITECTURE.md § Security](ARCHITECTURE.md#security). Use HTTPS (`COOKIE_SECURE=true`), encrypted disks for `data/`, and SRTP/TLS on the trunk where the provider supports it. |

## Before enabling production calling

- [ ] Register BongoHive as a data controller with the ODPC (and the voice platform's hosting provider as processor if applicable); appoint a DPO.
- [ ] Publish a privacy notice covering phone calls, AI processing, recordings and retention; link it from the website and mention it in the IVR/agent ("you can ask us to delete your information").
- [ ] Confirm with counsel: lawful basis per use case, calling hours, identification wording, any do-not-call obligations.
- [ ] Confirm with the telecom provider that automated calling is allowed under their terms.
- [ ] Replace all **sample** knowledge with verified, approved content.
- [ ] Set retention periods with the DPO.
- [ ] Run the acceptance tests in [TELEPHONY_ZAMBIA.md](TELEPHONY_ZAMBIA.md) §6.
- [ ] Only then set `OUTBOUND_ENABLED=true`, and start with small, approved, consented lists.
