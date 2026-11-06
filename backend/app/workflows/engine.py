"""Event-driven workflow engine with idempotent runs and approval gates."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal, utcnow
from ..models import Workflow, WorkflowRun
from ..services.events import bus
from .actions import ACTION_SPECS, HANDLERS, ActionError, lookup, render

log = logging.getLogger(__name__)

OPERATORS = ["eq", "neq", "contains", "exists", "not_exists", "gt", "lt", "in", "truthy", "falsy"]
RETRY_DELAYS = (2, 5, 15)


def condition_matches(cond: dict, payload: dict) -> bool:
    v = lookup(payload, cond.get("field", ""))
    op, target = cond.get("op", "eq"), cond.get("value")
    try:
        if op == "exists":
            return v not in (None, "", [], {})
        if op == "not_exists":
            return v in (None, "", [], {})
        if op == "truthy":
            return bool(v)
        if op == "falsy":
            return not v
        if op == "eq":
            return str(v).lower() == str(target).lower() if not isinstance(v, bool) else v == (str(target).lower() in ("true", "1", "yes"))
        if op == "neq":
            return str(v).lower() != str(target).lower()
        if op == "contains":
            return str(target).lower() in str(v).lower()
        if op == "in":
            options = target if isinstance(target, list) else [t.strip() for t in str(target).split(",")]
            return str(v) in [str(o) for o in options]
        if op == "gt":
            return v is not None and float(v) > float(target)
        if op == "lt":
            return v is not None and float(v) < float(target)
    except (TypeError, ValueError):
        return False
    return False


def idempotency_key(wf_id: str, event_type: str, payload: dict) -> str:
    ident = payload.get("idempotency_key") or payload.get("call_id") or payload.get("id")
    if not ident:
        ident = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]
    return f"{wf_id}:{event_type}:{ident}"


def has_external(wf: Workflow) -> bool:
    return any(ACTION_SPECS.get(a.get("type"), {}).get("external") for a in wf.actions or [])


class WorkflowEngine:
    def start(self) -> None:
        bus.subscribe("*", self.handle_event)

    async def handle_event(self, event_type: str, payload: dict) -> list[str]:
        async with SessionLocal() as db:
            wfs = (await db.execute(select(Workflow).where(Workflow.trigger == event_type, Workflow.enabled.is_(True)))).scalars().all()
        run_ids = []
        for wf in wfs:
            if not all(condition_matches(c, payload) for c in wf.conditions or []):
                continue
            run_id = await self._create_run(wf, event_type, payload)
            if run_id:
                run_ids.append(run_id)
                if wf.require_run_approval:
                    continue
                await self.execute(run_id)
        return run_ids

    async def _create_run(self, wf: Workflow, event_type: str, payload: dict) -> str | None:
        key = idempotency_key(wf.id, event_type, payload)
        async with SessionLocal() as db:
            run = WorkflowRun(workflow_id=wf.id, event_type=event_type, idempotency_key=key, payload=payload,
                              status="awaiting_approval" if wf.require_run_approval else "pending")
            db.add(run)
            try:
                await db.commit()
            except IntegrityError:
                log.info("Duplicate workflow trigger ignored", extra={"ctx": {"workflow": wf.name, "key": key}})
                return None
            return run.id

    async def execute(self, run_id: str) -> WorkflowRun:
        async with SessionLocal() as db:
            run = await db.get(WorkflowRun, run_id)
            wf = await db.get(Workflow, run.workflow_id)
            if run.status not in ("pending", "awaiting_approval", "failed"):
                return run
            run.status = "running"
            run.attempts += 1
            await db.commit()
            payload = dict(run.payload or {}, _run_id=run.id)
            results, error = [], None
            for i, action in enumerate(wf.actions or []):
                atype = action.get("type")
                spec = ACTION_SPECS.get(atype)
                if spec is None:
                    results.append({"action": atype, "status": "failed", "error": "unknown action"})
                    error = f"unknown action {atype}"
                    break
                prev = next((r for r in (run.results or []) if r.get("index") == i and r.get("status") == "success"), None)
                if prev:  # resumed run: never repeat an action that already succeeded
                    results.append(prev)
                    continue
                if spec["external"] and not wf.approved:
                    results.append({"index": i, "action": atype, "status": "skipped", "error": "workflow not approved by an admin"})
                    continue
                if spec["external"] and payload.get("is_test"):
                    results.append({"index": i, "action": atype, "status": "skipped", "error": "test call: external actions disabled"})
                    continue
                params = render(action.get("params") or {}, payload)
                try:
                    out = await self._run_with_retry(atype, params, payload, spec["external"])
                    results.append({"index": i, "action": atype, "status": "success", "output": out})
                    payload[f"step{i}"] = out
                except Exception as e:
                    results.append({"index": i, "action": atype, "status": "failed", "error": str(e)[:500]})
                    error = f"{atype}: {e}"
                    if not action.get("continue_on_error"):
                        break
            run.results = results
            run.status = "failed" if error else "success"
            run.error = error
            run.finished_at = utcnow()
            await db.commit()
            log.info("Workflow run finished", extra={"ctx": {"workflow": wf.name, "status": run.status, "error": error}})
            return run

    async def _run_with_retry(self, atype: str, params: dict, payload: dict, external: bool):
        attempts = len(RETRY_DELAYS) + 1 if external and atype != "outbound_call" else 1
        for n in range(attempts):
            try:
                return await HANDLERS[atype](params, payload)
            except ActionError:
                if n == attempts - 1 or atype in ("email", "sms"):  # config errors won't fix themselves
                    raise
            except Exception:
                if n == attempts - 1:
                    raise
            await asyncio.sleep(RETRY_DELAYS[n])


engine = WorkflowEngine()
