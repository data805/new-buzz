"""Post-call summary and structured extraction (feeds the workflow engine)."""
from __future__ import annotations

import json
import logging
import re

from ..llm.client import get_llm

log = logging.getLogger(__name__)

INTENTS = ["general_enquiry", "coworking", "ai_lab", "programmes", "consulting", "events", "partnership",
           "support", "complaint", "feedback", "callback_request", "appointment", "wrong_number", "other"]
TEAMS = ["coworking", "ai_lab", "entrepreneurship", "consulting", "events", "general"]

PROMPT = """You analyse phone call transcripts for BongoHive (an innovation hub in Lusaka, Zambia).
Return ONLY a JSON object with exactly these keys:
{
 "summary": "2-3 sentence factual summary of the call",
 "intent": one of %s,
 "team": one of %s,
 "sentiment": "positive" | "neutral" | "negative",
 "resolved": true if the caller's question was fully answered, else false,
 "caller_name": string or null,
 "caller_phone": string or null (only if the caller said a phone number),
 "caller_email": string or null,
 "organisation": string or null,
 "enquiry_topic": short string if the caller wants follow-up on a topic, else null,
 "enquiry_details": string or null,
 "callback_requested": true/false,
 "callback_time": string or null (as the caller said it, e.g. "tomorrow morning"),
 "needs_human": true if a staff member must act on this call,
 "feedback_rating": integer 1-5 if the caller gave a rating, else null,
 "feedback_comment": string or null,
 "action_items": ["short imperative action for staff", ...]
}
Use only information stated in the transcript. Never invent names, numbers or emails.
Describe what the Agent actually said. If the Agent said information was not available or not
published (for example a price), the summary must say so; never claim the Agent "provided" or
"gave" a price, date or other detail unless that exact detail appears in an Agent line.
A question is only "resolved" if the Agent answered it with a concrete fact."""


async def summarise_call(transcript: list[dict]) -> dict:
    lines = [f"{'Caller' if t['role'] == 'user' else 'Agent'}: {t['text']}" for t in transcript if t["role"] in ("user", "assistant")]
    if not any(t["role"] == "user" for t in transcript):
        return {"summary": "No caller speech was captured.", "intent": "other", "team": "general", "sentiment": "neutral",
                "resolved": False, "action_items": [], "callback_requested": False, "needs_human": False}
    text = "\n".join(lines)[-12000:]
    try:
        raw = await get_llm().complete(
            [{"role": "system", "content": PROMPT % (INTENTS, TEAMS)}, {"role": "user", "content": f"TRANSCRIPT:\n{text}"}],
            temperature=0.0, max_tokens=500, json_mode=True,
        )
        data = _parse_json(raw)
    except Exception as e:
        log.warning("Summarisation failed, using heuristic", extra={"ctx": {"error": str(e)}})
        data = {}
    return _sanitise(data, text)


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)


_PHONE = re.compile(r"(\+?260[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{4}|\b0\d{2}[\s-]?\d{3}[\s-]?\d{4}\b)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _sanitise(d: dict, transcript_text: str) -> dict:
    """Enforce types and drop hallucinated contact details that never appear in the transcript."""
    out = {
        "summary": str(d.get("summary") or "")[:2000] or transcript_text[:300],
        "intent": d.get("intent") if d.get("intent") in INTENTS else "other",
        "team": d.get("team") if d.get("team") in TEAMS else "general",
        "sentiment": d.get("sentiment") if d.get("sentiment") in ("positive", "neutral", "negative") else "neutral",
        "resolved": bool(d.get("resolved")),
        "callback_requested": bool(d.get("callback_requested")),
        "needs_human": bool(d.get("needs_human")),
        "action_items": [str(a)[:300] for a in (d.get("action_items") or []) if a][:10],
    }
    caller_text = "\n".join(l for l in transcript_text.splitlines() if l.startswith("Caller:")).lower()
    digits = re.sub(r"\D", "", caller_text)
    for key in ("caller_name", "organisation", "enquiry_topic", "enquiry_details", "callback_time", "feedback_comment"):
        v = d.get(key)
        out[key] = str(v)[:1000] if v not in (None, "", "null") else None
    # names/organisations must come from the caller's own words, not the agent's
    for key in ("caller_name", "organisation"):
        if out[key] and (out[key].split()[0].lower() not in caller_text or "bongohive" in out[key].lower()):
            out[key] = None
    phone = d.get("caller_phone")
    if phone and len(re.sub(r"\D", "", str(phone))) >= 9 and re.sub(r"\D", "", str(phone))[-9:] in digits:
        out["caller_phone"] = str(phone)
    else:
        m = _PHONE.search(caller_text)
        out["caller_phone"] = m.group(0) if m else None
    email = d.get("caller_email")
    out["caller_email"] = email if email and str(email).lower() in caller_text else (m.group(0) if (m := _EMAIL.search(caller_text)) else None)
    r = d.get("feedback_rating")
    out["feedback_rating"] = int(r) if isinstance(r, (int, float)) and 1 <= int(r) <= 5 else None
    return out
