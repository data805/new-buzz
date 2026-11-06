"""Workflow actions. Internal actions write to our own database; external
actions reach other systems and only run for admin-approved workflows."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..db import SessionLocal
from ..models import AuditLog, Callback, Enquiry, Feedback, Ticket
from ..services.events import bus

log = logging.getLogger(__name__)


class ActionError(Exception):
    pass


ACTION_SPECS: dict[str, dict] = {
    "create_enquiry": {"external": False, "label": "Create enquiry / lead",
                       "params": ["name", "phone", "email", "organisation", "topic", "details", "team"]},
    "create_ticket": {"external": False, "label": "Create support ticket",
                      "params": ["subject", "details", "priority", "team", "phone"]},
    "create_callback": {"external": False, "label": "Schedule callback", "params": ["phone", "name", "requested_time", "reason"]},
    "record_feedback": {"external": False, "label": "Record feedback", "params": ["rating", "comment"]},
    "notify_staff": {"external": False, "label": "Dashboard notification", "params": ["message"]},
    "webhook": {"external": True, "label": "HTTP webhook (POST JSON)", "params": ["url", "body"]},
    "n8n": {"external": True, "label": "Trigger n8n workflow", "params": ["webhook_path"]},
    "email": {"external": True, "label": "Send email (SMTP)", "params": ["to", "subject", "body"]},
    "sms": {"external": True, "label": "Send SMS", "params": ["to", "message"]},
    "outbound_call": {"external": True, "label": "Place outbound call (consent required)", "params": ["phone", "purpose"]},
}

_TPL = re.compile(r"\{\{\s*([\w.|]+)\s*\}\}")


def lookup(payload: dict, path: str) -> Any:
    """Dotted-path lookup; `a.b||c` falls back to `c` when `a.b` is empty."""
    if "||" in path:
        for alt in path.split("||"):
            v = lookup(payload, alt.strip())
            if v not in (None, "", [], {}):
                return v
        return None
    cur: Any = payload
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def render(value: Any, payload: dict) -> Any:
    if isinstance(value, str):
        whole = _TPL.fullmatch(value.strip())
        if whole:
            v = lookup(payload, whole.group(1))
            return v if v is not None else ""
        return _TPL.sub(lambda m: "" if (v := lookup(payload, m.group(1))) is None else str(v), value)
    if isinstance(value, dict):
        return {k: render(v, payload) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, payload) for v in value]
    return value


# ---------------------------------------------------------------------- internal
async def _create_enquiry(p: dict, payload: dict) -> dict:
    async with SessionLocal() as db:
        e = Enquiry(call_id=payload.get("call_id"), name=str(p.get("name") or "")[:255], phone=str(p.get("phone") or payload.get("phone") or "")[:32],
                    email=str(p.get("email") or "")[:255], organisation=str(p.get("organisation") or "")[:255],
                    topic=str(p.get("topic") or "")[:255], details=str(p.get("details") or ""), team=str(p.get("team") or "general")[:100],
                    qualified=bool(p.get("phone") or payload.get("phone")))
        db.add(e)
        await db.commit()
    await bus.publish("enquiry.created", {"id": e.id, "call_id": e.call_id, "topic": e.topic, "team": e.team, "phone": e.phone, "name": e.name})
    return {"enquiry_id": e.id}


async def _create_ticket(p: dict, payload: dict) -> dict:
    prio = p.get("priority") if p.get("priority") in ("low", "normal", "high", "urgent") else "normal"
    async with SessionLocal() as db:
        t = Ticket(call_id=payload.get("call_id"), subject=str(p.get("subject") or "Follow-up needed")[:500], details=str(p.get("details") or ""),
                   priority=prio, team=str(p.get("team") or "general")[:100], phone=str(p.get("phone") or payload.get("phone") or "")[:32])
        db.add(t)
        await db.commit()
    await bus.publish("ticket.created", {"id": t.id, "call_id": t.call_id, "subject": t.subject, "priority": t.priority, "team": t.team})
    return {"ticket_id": t.id}


async def _create_callback(p: dict, payload: dict) -> dict:
    async with SessionLocal() as db:
        c = Callback(call_id=payload.get("call_id"), phone=str(p.get("phone") or payload.get("phone") or "")[:32], name=str(p.get("name") or "")[:255],
                     requested_time=str(p.get("requested_time") or "")[:255], reason=str(p.get("reason") or ""))
        db.add(c)
        await db.commit()
    await bus.publish("callback.created", {"id": c.id, "call_id": c.call_id, "phone": c.phone, "requested_time": c.requested_time})
    return {"callback_id": c.id}


async def _record_feedback(p: dict, payload: dict) -> dict:
    try:
        rating = int(p.get("rating")) if p.get("rating") not in (None, "") else None
    except (TypeError, ValueError):
        rating = None
    async with SessionLocal() as db:
        f = Feedback(call_id=payload.get("call_id"), rating=rating, comment=str(p.get("comment") or ""))
        db.add(f)
        await db.commit()
    await bus.publish("feedback.received", {"id": f.id, "call_id": f.call_id, "rating": rating, "comment": f.comment})
    return {"feedback_id": f.id}


async def _notify_staff(p: dict, payload: dict) -> dict:
    async with SessionLocal() as db:
        db.add(AuditLog(action="notification", actor="workflow", target=payload.get("call_id") or "",
                        details={"message": str(p.get("message") or "")[:2000]}))
        await db.commit()
    return {"notified": True}


# ---------------------------------------------------------------------- external
def _check_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    allowed = get_settings().webhook_allowed_hosts
    n8n = urlparse(get_settings().n8n_base_url).hostname
    if n8n:
        allowed = [*allowed, n8n.lower()]
    if not urlparse(url).scheme in ("http", "https") or not host:
        raise ActionError("Invalid URL")
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise ActionError(f"Host '{host}' is not in WEBHOOK_ALLOWED_HOSTS")


async def _post_json(url: str, body: Any) -> dict:
    _check_host(url)
    data = json.dumps(body, default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "BongoHiveVoiceAgent/1.0"}
    secret = get_settings().webhook_signing_secret.get_secret_value()
    if secret:
        headers["X-Signature-SHA256"] = hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, content=data, headers=headers)
    if r.status_code >= 400:
        raise ActionError(f"HTTP {r.status_code}: {r.text[:200]}")
    return {"status": r.status_code}


async def _webhook(p: dict, payload: dict) -> dict:
    if not p.get("url"):
        raise ActionError("webhook url is required")
    return await _post_json(p["url"], p.get("body") or payload)


async def _n8n(p: dict, payload: dict) -> dict:
    base = get_settings().n8n_base_url.rstrip("/")
    if not base:
        raise ActionError("N8N_BASE_URL is not configured")
    path = str(p.get("webhook_path") or "").lstrip("/")
    if not path:
        raise ActionError("webhook_path is required (e.g. webhook/voice-call-completed)")
    return await _post_json(f"{base}/{path}", payload)


async def _email(p: dict, payload: dict) -> dict:
    s = get_settings()
    if not s.smtp_host or not s.smtp_from:
        raise ActionError("SMTP_HOST / SMTP_FROM not configured")
    to = [a.strip() for a in str(p.get("to") or "").split(",") if "@" in a]
    if not to:
        raise ActionError("No valid recipient")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, ", ".join(to), str(p.get("subject") or "Voice agent notification")[:200]
    msg.set_content(str(p.get("body") or json.dumps(payload, indent=2, default=str)))

    def send():
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
            smtp.send_message(msg)

    await asyncio.to_thread(send)
    return {"sent_to": len(to)}


async def _sms(p: dict, payload: dict) -> dict:
    from ..telephony.numbers import normalise

    s = get_settings()
    to = normalise(str(p.get("to") or payload.get("phone") or ""))
    text = str(p.get("message") or "")[:480]
    if not text:
        raise ActionError("message is required")
    async with httpx.AsyncClient(timeout=15) as c:
        if s.sms_provider == "twilio":
            r = await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
                             data={"To": to, "From": s.sms_from, "Body": text},
                             auth=(s.twilio_account_sid, s.twilio_auth_token.get_secret_value()))
        elif s.sms_provider == "http":
            r = await c.post(s.sms_http_url, json={"to": to, "from": s.sms_from, "message": text},
                             headers={"Authorization": f"Bearer {s.sms_http_token.get_secret_value()}"})
        else:
            raise ActionError("SMS_PROVIDER not configured")
    if r.status_code >= 400:
        raise ActionError(f"SMS gateway HTTP {r.status_code}: {r.text[:200]}")
    return {"status": r.status_code}


async def _outbound_call(p: dict, payload: dict) -> dict:
    from ..outbound.dialer import CallNotAllowed, place_call

    phone = str(p.get("phone") or payload.get("phone") or "")
    try:
        call = await place_call(phone, purpose=str(p.get("purpose") or ""), requested_by="workflow",
                                idempotency_key=f"wf-call:{payload.get('_run_id')}")
    except CallNotAllowed as e:
        raise ActionError(f"Call not allowed: {e}") from e
    return {"call_id": call.id}


HANDLERS = {
    "create_enquiry": _create_enquiry, "create_ticket": _create_ticket, "create_callback": _create_callback,
    "record_feedback": _record_feedback, "notify_staff": _notify_staff, "webhook": _webhook, "n8n": _n8n,
    "email": _email, "sms": _sms, "outbound_call": _outbound_call,
}
