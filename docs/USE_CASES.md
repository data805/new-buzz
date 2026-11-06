# Using Buzz at BongoHive without a phone line

Buzz doesn't need a SIP trunk, a phone number or Twilio to be useful. The browser is a complete voice channel: speech recognition, grounded answers, spoken replies, transcripts, summaries and follow-up records all work there today.

Each idea below is marked:

- **Works today:** possible now with the dashboard and settings. Only content and setup are needed.
- **Small build:** the engine is there, but a new screen, endpoint or integration is needed.

Everything here needs verified knowledge first. The bundled content is sample data, so replacing it is step one for any real use.

## Summary

| # | Use | Team that benefits | Status |
|---|---|---|---|
| 1 | Live demo for AI Lab clients | Data and AI, Consulting | Works today |
| 2 | Front-desk assistant at the Hive | Coworking, Reception | Works today |
| 3 | Event and programme information desk | Events, Entrepreneurship | Works today |
| 4 | AI Assessment intake interview | AI Lab | Works today |
| 5 | Post-event and programme feedback | Programmes, Events | Works today |
| 6 | Staff and new-member knowledge assistant | All teams | Works today |
| 7 | Finding gaps in BongoHive's information | Marketing, Operations | Works today |
| 8 | Follow-ups into Slack, Sheets or a CRM | Operations | Works today, with setup |
| 9 | Pitch practice for founders | Entrepreneurship | Worth a trial |
| 10 | "Talk to Buzz" on the website | Marketing | Small build |
| 11 | Self-service tablet mode | Coworking | Small build |
| 12 | Reusable product for clients | AI Lab, Consulting | Small build |

## Works today

### 1. Live demo for AI Lab clients

BongoHive's AI Lab sells AI solutions, and a working voice agent is a stronger demonstration than slides. In a client meeting, upload the client's own FAQ or policy document, approve it, and let them question it out loud. Then show the transcript, the sources, and the moment the agent refuses to guess.

**Why it matters:** clients see their own content answered in minutes. The agent is self-hosted, so the conversation can be kept on a server the client controls, which matters to banks, clinics and government bodies.

**Setup:** use a separate demo instance per client, so their documents never mix with BongoHive's.

### 2. Front-desk assistant at the Hive

Run the Test console on a tablet or laptop at reception, so visitors can ask about coworking plans, hours, directions or who to speak to. When someone leaves their details, an enquiry or callback is created for the team.

**Why it matters:** reception gets fewer repeat questions, and enquiries are captured even when nobody is at the desk.

**Setup:** sign in with an operator account, use **Simulated inbound call** mode, and use a headset or a directional microphone. The page shows staff controls, so keep it supervised until self-service mode (idea 11) exists.

### 3. Event and programme information desk

Before a hackathon, demo day or programme intake, upload the schedule, venue and rules, then run a "Ask Buzz" station at the event. Attendees get immediate answers, and the team sees which questions came up most.

**Setup:** add the event pack as a knowledge source, and archive it after the event. It's a low-risk way to pilot Buzz in front of real people.

### 4. AI Assessment intake interview

Change the persona and greeting in **Agent settings** so Buzz interviews a business owner before a free AI Assessment: what the business does, its biggest problem, and how it handles data today. The name, organisation, topic, details and any callback are extracted, and an enquiry goes to the AI Lab team.

**Why it matters:** consultants start the first meeting with a written brief instead of spending it on the basics.

**Limit:** extraction uses a fixed set of fields today. Assessment-specific fields, such as industry or company size, would end up in the free-text details until the extraction prompt is extended.

### 5. Post-event and programme feedback

Set up a feedback station after a session, where a participant speaks instead of filling in a form. People tend to say more out loud. The rating and comment are extracted by the **Record caller feedback** workflow, and negative feedback can alert a manager once email is configured.

**Setup:** change the greeting to ask for feedback, and enable the workflow. Tell participants that an AI is collecting it.

### 6. Staff and new-member knowledge assistant

Upload onboarding packs, policies, member rules and process documents, so that new staff, interns and members can ask questions instead of searching folders. **Knowledge → Test retrieval** also works as a plain search over approved documents.

**Why it matters:** answers come with their sources, and only approved documents are used, so people aren't relying on outdated copies.

**Limit:** knowledge isn't separated by role, so anyone who can use the instance can reach everything in it. Run a separate instance for sensitive internal material.

### 7. Finding gaps in BongoHive's information

Every "I don't have that information" is a useful signal. Reviewing **Call history** and tickets shows what people ask that BongoHive hasn't published. For example, the sample knowledge has no hot-desk price, so that question always ends in a follow-up.

**Why it matters:** this gives the marketing and operations teams evidence for what to add to the website or FAQ.

**Next step:** a dedicated "top unanswered questions" report would be a small addition to the Overview page.

### 8. Follow-ups into Slack, Sheets or a CRM

Workflows can send call results to a webhook or to n8n, which connects to Slack, Google Sheets, email and most CRMs. A callback can land in a team channel seconds after a conversation ends.

**Setup:** set `N8N_BASE_URL` or `WEBHOOK_ALLOWED_HOSTS`, then have an admin approve the workflow. External actions never run for **Test call** mode, so use **Simulated inbound call** mode. For email, set the `SMTP_*` settings.

### 9. Pitch practice for founders

Rewrite the persona so Buzz plays an investor or a customer, and asks a founder hard questions about their pitch. The transcript gives mentors something concrete to review.

**Limit:** the default rules are built for accurate customer service, so this needs a dedicated persona on its own instance, and a small model will ask simple questions. Try it with one founder before promising it to a cohort. A larger model would help a lot.

## Small builds

### 10. "Talk to Buzz" on the website

A button on bongohive.co.zm that opens a voice conversation in the browser, with no phone call needed for visitors.

**What's missing:** voice connections currently require a signed-in operator. This needs a public endpoint with strict rate limits, abuse protection and a consent notice, plus a small widget. It also needs an always-on server with enough capacity, because each conversation uses real CPU time.

### 11. Self-service tablet mode

A simple full-screen page with one "Press to talk" button for reception or events, which hides the dashboard and resets after each visitor.

**What's missing:** one new page and a restricted account type.

### 12. Reusable product for clients

Package Buzz as a product that the AI Lab can deploy for clients, such as schools, clinics, banks or NGOs. Knowledge, the agent's name, greeting, persona and voice are all editable in the dashboard already.

**What's missing:** "BongoHive" also appears in the code, including the summary prompt, the disclosure lines and the dashboard branding. Moving those into settings would make it ready for other organisations.

## Good to have before any pilot

1. Verified knowledge from staff, with the sample sources archived.
2. A decent always-on machine. A modest office PC or a small server is enough for a pilot, and a GPU makes replies much faster.
3. HTTPS, so browsers allow the microphone on any address other than `localhost`.
4. Sign-in turned on, and `ENVIRONMENT=production` set.
5. A visible notice that people are talking to an AI, and that conversations are stored. See [COMPLIANCE.md](COMPLIANCE.md).

## Limits to be upfront about

- **English only.** Bemba, Nyanja and other local languages aren't supported yet, and accuracy on Zambian accents hasn't been measured with real callers. See [VOICE_EVALUATION.md](VOICE_EVALUATION.md).
- **Noise.** Busy rooms hurt speech recognition, so a headset or close microphone matters more than the software at events.
- **Small model.** The default 3B model sometimes phrases things awkwardly and can misread a garbled sentence. A larger model improves this.
- **Speed.** Replies can take many seconds on a busy laptop. Test on the machine you plan to use.
