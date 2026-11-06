"""API-level tests: auth/RBAC, knowledge lifecycle, compliance gates, workflows, integrations."""
from __future__ import annotations

import asyncio
import io
import time

import pytest


def _wait_until(fn, timeout=20.0):
    t = time.time()
    while time.time() - t < timeout:
        if fn():
            return True
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------- auth & RBAC
def test_requires_auth(client):
    from fastapi.testclient import TestClient

    anon = TestClient(client.app)
    assert anon.get("/api/calls").status_code == 401
    assert anon.get("/api/health").status_code == 200


def test_auth_disabled_demo_mode(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings

    anon = TestClient(client.app)
    monkeypatch.setattr(get_settings(), "auth_disabled", True)
    assert anon.get("/api/calls").status_code == 200
    me = anon.get("/api/auth/me").json()
    assert me["role"] == "admin" and me["auth_disabled"] and not me["must_change_password"]
    monkeypatch.setattr(get_settings(), "environment", "production")
    assert anon.get("/api/calls").status_code == 401  # never honoured in production
    assert "AUTH_DISABLED must be false in production" in get_settings().validate_for_production()


def test_rbac_viewer_cannot_change_settings(client):
    r = client.post("/api/users", json={"email": "viewer@test.local", "name": "V", "role": "viewer", "password": "ViewerPass123"})
    assert r.status_code == 201, r.text
    from fastapi.testclient import TestClient

    v = TestClient(client.app)
    assert v.post("/api/auth/login", json={"email": "viewer@test.local", "password": "ViewerPass123"}).status_code == 200
    assert v.get("/api/calls").status_code == 200
    settings = v.get("/api/agent/settings").json()["settings"]
    assert v.put("/api/agent/settings", json=settings).status_code == 403
    assert v.get("/api/users").status_code == 403
    assert v.post("/api/knowledge/text", json={"title": "x", "content": "y"}).status_code == 403


def test_login_lockout_and_rate_limit(client):
    from fastapi.testclient import TestClient

    c = TestClient(client.app)
    client.post("/api/users", json={"email": "lock@test.local", "role": "viewer", "password": "LockPass1234"})
    codes = [c.post("/api/auth/login", json={"email": "lock@test.local", "password": "wrong"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5
    assert codes[5] == 423  # locked
    assert c.post("/api/auth/login", json={"email": "lock@test.local", "password": "LockPass1234"}).status_code == 423


def test_weak_password_rejected(client):
    r = client.post("/api/users", json={"email": "weak@test.local", "role": "viewer", "password": "alllowercase"})
    assert r.status_code == 400


# ---------------------------------------------------------------- knowledge
def test_knowledge_lifecycle_draft_not_used_until_approved(client):
    from app.knowledge.store import index

    body = {"title": "Parking policy (verified)", "category": "facilities",
            "faqs": [{"question": "Is there visitor parking at the hub?", "answer": "Yes, the zebra-striped bays near gate seven are reserved for visitors."}]}
    r = client.post("/api/knowledge/text", json=body)
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["status"] == "draft" and src["ingest_status"] == "ready" and src["chunk_count"] == 1
    hits = client.post("/api/knowledge/search", json={"query": "zebra-striped bays gate seven", "min_score": 0}).json()["hits"]
    assert not any(h["source_id"] == src["id"] for h in hits)

    assert client.post(f"/api/knowledge/{src['id']}/status", json={"status": "approved"}).status_code == 200
    hits = client.post("/api/knowledge/search", json={"query": "Where can visitors park? zebra bays gate seven"}).json()["hits"]
    assert hits and hits[0]["source_id"] == src["id"]

    # editing content sends it back to draft
    r = client.patch(f"/api/knowledge/{src['id']}", json={"content": "## Parking\nVisitors park in bay 3."})
    assert r.json()["status"] == "draft"
    assert not any(e.source_id == src["id"] for e in index.entries)
    assert client.delete(f"/api/knowledge/{src['id']}").status_code == 200


def test_upload_csv_faq_and_reject_bad_type(client):
    csv = b"question,answer\nDo you sell coffee?,There is a cafe on site.\n"
    r = client.post("/api/knowledge/upload", files={"file": ("faq.csv", io.BytesIO(csv), "text/csv")}, data={"category": "faq"})
    assert r.status_code == 201, r.text
    assert r.json()["chunk_count"] == 1
    r = client.post("/api/knowledge/upload", files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert r.status_code == 400


def test_url_import_domain_allowlist(client):
    r = client.post("/api/knowledge/url", json={"url": "https://evil.example.org/page"})
    assert r.status_code == 400 and "approved list" in r.json()["detail"]
    r = client.post("/api/knowledge/url", json={"url": "http://localhost:8000/api/health"})
    assert r.status_code == 400


def test_irrelevant_question_returns_no_knowledge(client):
    hits = client.post("/api/knowledge/search", json={"query": "who won the football match yesterday"}).json()["hits"]
    assert hits == []


# ---------------------------------------------------------------- agent settings
def test_agent_settings_validation_and_audit(client):
    s = client.get("/api/agent/settings").json()["settings"]
    s["turn_taking"]["endpoint_silence_ms"] = 50  # below minimum
    assert client.put("/api/agent/settings", json=s).status_code == 422
    s["turn_taking"]["endpoint_silence_ms"] = 700
    r = client.put("/api/agent/settings", json=s)
    assert r.status_code == 200 and "turn_taking" in r.json()["changed"]
    audit = client.get("/api/audit?action=agent").json()
    assert audit and audit[0]["action"] == "agent.settings_updated"


# ---------------------------------------------------------------- outbound compliance
def test_contact_consent_rules(client):
    assert client.post("/api/contacts", json={"phone": "0971000001", "consent_status": "granted"}).status_code == 400  # no source
    r = client.post("/api/contacts", json={"phone": "0971000001", "name": "Test", "consent_status": "granted", "consent_source": "web form"})
    assert r.status_code == 201 and r.json()["phone"] == "+260971000001"
    cid = r.json()["id"]
    assert client.post(f"/api/contacts/{cid}/opt-out").status_code == 200
    r = client.post("/api/contacts", json={"phone": "+260971000001", "consent_status": "granted", "consent_source": "again"})
    assert r.status_code == 409  # cannot re-grant after opt-out by import


def test_single_call_requires_consent(client):
    client.post("/api/contacts", json={"phone": "0961000002", "consent_status": "unknown"})
    r = client.post("/api/calls/outbound", json={"phone": "0961000002", "purpose": "follow up"})
    assert r.status_code == 400 and "consent" in r.json()["detail"].lower()
    r = client.post("/api/calls/outbound", json={"phone": "+447911123456", "purpose": "follow up"})
    assert r.status_code == 400 and "country" in r.json()["detail"].lower()


def test_campaign_approval_flow_and_gates(client):
    camp = client.post("/api/campaigns", json={"name": "Event reminder", "purpose": "reminder", "script": "Remind about the AI workshop."}).json()
    # contacts without consent block submission
    csv = b"phone,name,consent\n0951000003,A,no\n"
    r = client.post("/api/contacts/import", files={"file": ("c.csv", io.BytesIO(csv), "text/csv")}, data={"campaign_id": camp["id"]})
    assert r.json()["added_to_campaign"] == 1
    r = client.post(f"/api/campaigns/{camp['id']}/submit")
    assert r.status_code == 400 and "consent" in r.json()["detail"]

    camp2 = client.post("/api/campaigns", json={"name": "Workshop follow-up", "script": "Ask for feedback on the workshop."}).json()
    csv = b"phone,name,consent\n0951000004,B,yes\n0771000005,C,yes\n"
    r = client.post("/api/contacts/import", files={"file": ("c.csv", io.BytesIO(csv), "text/csv")}, data={"campaign_id": camp2["id"]})
    assert r.status_code == 400  # consent=yes needs attestation
    r = client.post("/api/contacts/import", files={"file": ("c.csv", io.BytesIO(csv), "text/csv")},
                    data={"campaign_id": camp2["id"], "consent_attestation": "Signed up at workshop, Oct 2026"})
    assert r.json()["added_to_campaign"] == 2
    assert client.post(f"/api/campaigns/{camp2['id']}/start").status_code == 400  # not approved
    assert client.post(f"/api/campaigns/{camp2['id']}/submit").status_code == 200
    assert client.post(f"/api/campaigns/{camp2['id']}/approve").status_code == 200
    r = client.get(f"/api/campaigns/{camp2['id']}").json()
    assert r["status"] == "approved" and r["approved_by"] == "admin@test.local"


def test_bulk_campaign_blocked(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "bulk_threshold", 1)
    camp = client.post("/api/campaigns", json={"name": "Bulk", "script": "Bulk test"}).json()
    csv = b"phone,consent\n0951000010,yes\n0951000011,yes\n"
    client.post("/api/contacts/import", files={"file": ("c.csv", io.BytesIO(csv), "text/csv")}, data={"campaign_id": camp["id"], "consent_attestation": "x"})
    client.post(f"/api/campaigns/{camp['id']}/submit")
    r = client.post(f"/api/campaigns/{camp['id']}/approve")
    assert r.status_code == 400 and "Bulk" in r.json()["detail"]


# ---------------------------------------------------------------- outbound dialing via simulator
def test_outbound_no_answer_and_busy_outcomes(client):
    from app.telephony.base import get_provider

    sim = get_provider()
    client.post("/api/contacts", json={"phone": "0971000020", "consent_status": "granted", "consent_source": "test"})
    r = client.post("/api/calls/outbound", json={"phone": "0971000020", "purpose": "Confirm appointment"})
    assert r.status_code == 200, r.text
    call_id = r.json()["call_id"]
    assert _wait_until(lambda: client.get(f"/api/calls/{call_id}").json()["outcome"] == "no_answer")

    # spacing rule: a successful call within 24h blocks another; a failed attempt does not
    r = client.post("/api/calls/outbound", json={"phone": "0971000020", "purpose": "Retry"})
    assert r.status_code == 200

    from app.telephony.simulator import SimHandset

    async def busy(call_id, req):
        return "busy"

    async def run_call(call_id):
        pass

    client.post("/api/contacts", json={"phone": "0971000021", "consent_status": "granted", "consent_source": "test"})
    sim.register(SimHandset("+260971000021", busy, run_call))
    r = client.post("/api/calls/outbound", json={"phone": "0971000021", "purpose": "Busy test"})
    assert r.status_code == 200, r.text
    call_id = r.json()["call_id"]
    assert _wait_until(lambda: client.get(f"/api/calls/{call_id}").json()["outcome"] == "busy")


def test_campaign_retry_scheduling(client):
    """Dialer places calls for a running campaign and schedules retries after no-answer."""
    from app.outbound.dialer import dialer

    camp = client.post("/api/campaigns", json={"name": "Retry test", "script": "Say hello", "max_attempts": 2, "retry_delay_minutes": 30,
                                                "window_start": "00:00", "window_end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6]}).json()
    csv = b"phone,consent\n0961000030,yes\n"
    client.post("/api/contacts/import", files={"file": ("c.csv", io.BytesIO(csv), "text/csv")}, data={"campaign_id": camp["id"], "consent_attestation": "x"})
    for a in ("submit", "approve", "start"):
        assert client.post(f"/api/campaigns/{camp['id']}/{a}").status_code == 200, a
    placed = client.portal.call(dialer.tick)
    assert placed == 1

    def contact_state():
        return client.get(f"/api/campaigns/{camp['id']}").json()["contacts"][0]

    assert _wait_until(lambda: contact_state()["status"] == "no_answer")
    cc = contact_state()
    assert cc["attempts"] == 1 and cc["next_attempt_at"] is not None
    assert client.portal.call(dialer.tick) == 0  # retry not yet due


# ---------------------------------------------------------------- workflows
def test_workflow_idempotency_and_external_approval(client, monkeypatch):
    from app.services.events import bus
    from app.workflows import actions

    sent = []

    async def fake_webhook(params, payload):
        sent.append(params)
        return {"status": 200}

    monkeypatch.setitem(actions.HANDLERS, "webhook", fake_webhook)
    wf = client.post("/api/workflows", json={
        "name": "Hook", "trigger": "custom", "conditions": [{"field": "kind", "op": "eq", "value": "lead"}],
        "actions": [{"type": "notify_staff", "params": {"message": "lead {{name}}"}},
                    {"type": "webhook", "params": {"url": "https://hooks.example.com/x", "body": {"n": "{{name}}"}}}],
        "enabled": True}).json()
    payload = {"kind": "lead", "name": "Chanda", "idempotency_key": "evt-1"}
    client.portal.call(bus.publish, "custom", payload, True)
    client.portal.call(bus.publish, "custom", payload, True)  # duplicate
    runs = [r for r in client.get("/api/workflows/runs/list").json() if r["workflow_id"] == wf["id"]]
    assert len(runs) == 1
    steps = {s["action"]: s["status"] for s in runs[0]["results"]}
    assert steps == {"notify_staff": "success", "webhook": "skipped"}  # not approved yet
    assert sent == []

    assert client.post(f"/api/workflows/{wf['id']}/approve").status_code == 200
    client.portal.call(bus.publish, "custom", {**payload, "idempotency_key": "evt-2"}, True)
    assert sent == [{"url": "https://hooks.example.com/x", "body": {"n": "Chanda"}}]

    # editing the definition revokes approval
    w = client.get("/api/workflows").json()
    w = next(x for x in w if x["id"] == wf["id"])
    w["actions"][1]["params"]["url"] = "https://hooks.example.com/y"
    assert client.put(f"/api/workflows/{wf['id']}", json=w).json()["approved"] is False


def test_webhook_host_allowlist():
    from app.workflows.actions import ActionError, _check_host

    _check_host("https://hooks.example.com/abc")
    with pytest.raises(ActionError):
        _check_host("https://attacker.example.net/abc")


def test_run_approval_required(client):
    from app.services.events import bus

    wf = client.post("/api/workflows", json={"name": "Needs ok", "trigger": "custom", "actions": [{"type": "create_ticket", "params": {"subject": "S"}}],
                                             "enabled": True, "require_run_approval": True}).json()
    client.portal.call(bus.publish, "custom", {"idempotency_key": "evt-approve"}, True)
    run = next(r for r in client.get("/api/workflows/runs/list").json() if r["workflow_id"] == wf["id"])
    assert run["status"] == "awaiting_approval"
    r = client.post(f"/api/workflows/runs/{run['id']}/approve").json()
    assert r["status"] == "success"


# ---------------------------------------------------------------- integration API
def test_api_keys_and_scopes(client):
    key = client.post("/api/api-keys", json={"name": "n8n", "scopes": ["events:create"]}).json()["key"]
    from fastapi.testclient import TestClient

    m = TestClient(client.app)
    assert m.post("/api/v1/events", json={"type": "custom", "data": {"a": 1}}, headers={"X-API-Key": key}).status_code == 200
    assert m.post("/api/v1/calls", json={"phone": "0971234567", "purpose": "x y z"}, headers={"X-API-Key": key}).status_code == 403
    assert m.post("/api/v1/events", json={"type": "custom"}, headers={"X-API-Key": "vak_wrong"}).status_code == 401
    assert m.post("/api/v1/events", json={"type": "call.completed"}, headers={"X-API-Key": key}).status_code == 400


def test_asterisk_register_requires_token(client):
    assert client.get("/api/telephony/asterisk/register?token=nope").status_code == 403


def test_privacy_erasure(client):
    from app.services.calls import add_turn, create_call

    async def make():
        c = await create_call(direction="inbound", channel="simulator", from_number="+260971999999", to_number="x", status="completed")
        await add_turn(c.id, "user", "My name is Test")
        return c.id

    cid = client.portal.call(make)
    r = client.post("/api/privacy/erase", json={"phone": "0971999999"})
    assert r.json()["calls_erased"] == 1
    d = client.get(f"/api/calls/{cid}").json()
    assert d["turns"] == [] and d["from_number"] == "[erased]"
