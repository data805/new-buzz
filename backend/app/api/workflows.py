from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User, Workflow, WorkflowRun
from ..security import audit, require_role
from ..services.events import EVENT_TYPES
from ..workflows.actions import ACTION_SPECS
from ..workflows.engine import OPERATORS, engine, has_external
from ..workflows.templates import TEMPLATES

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
viewer, operator, manager, admin = (require_role(r) for r in ("viewer", "operator", "manager", "admin"))


def wf_out(w: Workflow) -> dict:
    return {"id": w.id, "name": w.name, "description": w.description, "trigger": w.trigger, "conditions": w.conditions,
            "actions": w.actions, "enabled": w.enabled, "approved": w.approved, "approved_by": w.approved_by,
            "require_run_approval": w.require_run_approval, "template": w.template, "has_external": has_external(w),
            "updated_at": w.updated_at.isoformat() if w.updated_at else None}


def run_out(r: WorkflowRun, name: str = "") -> dict:
    return {"id": r.id, "workflow_id": r.workflow_id, "workflow": name, "event_type": r.event_type, "status": r.status,
            "results": r.results, "error": r.error, "attempts": r.attempts, "payload": r.payload,
            "created_at": r.created_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None}


@router.get("/meta")
async def meta(_: User = Depends(viewer)):
    return {"events": EVENT_TYPES, "actions": ACTION_SPECS, "operators": OPERATORS, "templates": TEMPLATES}


@router.get("")
async def list_workflows(_: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    return [wf_out(w) for w in (await db.execute(select(Workflow).order_by(Workflow.created_at))).scalars()]


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    trigger: str
    conditions: list[dict] = []
    actions: list[dict] = Field(min_length=1)
    enabled: bool = False
    require_run_approval: bool = False


def _validate(body: WorkflowIn) -> None:
    if body.trigger not in EVENT_TYPES:
        raise HTTPException(400, f"Unknown trigger '{body.trigger}'")
    for c in body.conditions:
        if c.get("op") not in OPERATORS or not c.get("field"):
            raise HTTPException(400, f"Invalid condition {c}")
    for a in body.actions:
        if a.get("type") not in ACTION_SPECS:
            raise HTTPException(400, f"Unknown action type '{a.get('type')}'")
        if not isinstance(a.get("params", {}), dict):
            raise HTTPException(400, "Action params must be an object")


@router.post("", status_code=201)
async def create_workflow(body: WorkflowIn, request: Request, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    _validate(body)
    w = Workflow(**body.model_dump(), created_by=user.email)
    db.add(w)
    await audit(db, "workflow.created", w.name, user=user, request=request)
    await db.commit()
    return wf_out(w)


@router.put("/{wid}")
async def update_workflow(wid: str, body: WorkflowIn, request: Request, user: User = Depends(manager),
                          db: AsyncSession = Depends(get_db)):
    _validate(body)
    w = await db.get(Workflow, wid)
    if not w:
        raise HTTPException(404, "Workflow not found")
    definition_changed = (w.actions != body.actions or w.conditions != body.conditions or w.trigger != body.trigger)
    for k, v in body.model_dump().items():
        setattr(w, k, v)
    if definition_changed and w.approved:
        w.approved, w.approved_by = False, None  # external actions need re-approval after edits
    await audit(db, "workflow.updated", w.name, user=user, details={"definition_changed": definition_changed}, request=request)
    await db.commit()
    return wf_out(w)


@router.post("/{wid}/approve")
async def approve_workflow(wid: str, request: Request, user: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    w = await db.get(Workflow, wid)
    if not w:
        raise HTTPException(404, "Workflow not found")
    w.approved, w.approved_by = True, user.email
    await audit(db, "workflow.approved", w.name, user=user, request=request)
    await db.commit()
    return wf_out(w)


@router.post("/{wid}/revoke")
async def revoke_workflow(wid: str, request: Request, user: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    w = await db.get(Workflow, wid)
    if not w:
        raise HTTPException(404, "Workflow not found")
    w.approved, w.approved_by = False, None
    await audit(db, "workflow.approval_revoked", w.name, user=user, request=request)
    await db.commit()
    return wf_out(w)


@router.delete("/{wid}")
async def delete_workflow(wid: str, request: Request, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    w = await db.get(Workflow, wid)
    if not w:
        raise HTTPException(404, "Workflow not found")
    await db.delete(w)
    await audit(db, "workflow.deleted", w.name, user=user, request=request)
    await db.commit()
    return {"ok": True}


class TestIn(BaseModel):
    payload: dict


@router.post("/{wid}/test")
async def test_workflow(wid: str, body: TestIn, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    """Dry-run: evaluate conditions and show rendered action parameters without executing anything."""
    from ..workflows.actions import render
    from ..workflows.engine import condition_matches

    w = await db.get(Workflow, wid)
    if not w:
        raise HTTPException(404, "Workflow not found")
    conds = [{"condition": c, "matches": condition_matches(c, body.payload)} for c in w.conditions or []]
    return {"would_run": all(c["matches"] for c in conds), "conditions": conds,
            "actions": [{"type": a["type"], "external": ACTION_SPECS[a["type"]]["external"],
                         "blocked": ACTION_SPECS[a["type"]]["external"] and not w.approved,
                         "params": render(a.get("params") or {}, body.payload)} for a in w.actions or []]}


@router.get("/runs/list")
async def list_runs(status: str | None = None, limit: int = 200, _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    q = select(WorkflowRun, Workflow.name).join(Workflow).order_by(WorkflowRun.created_at.desc()).limit(min(limit, 1000))
    if status:
        q = q.where(WorkflowRun.status == status)
    return [run_out(r, n) for r, n in (await db.execute(q)).all()]


@router.post("/runs/{run_id}/{action}")
async def run_action(run_id: str, action: str, request: Request, user: User = Depends(manager),
                     db: AsyncSession = Depends(get_db)):
    r = await db.get(WorkflowRun, run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    if action == "approve" and r.status == "awaiting_approval":
        await audit(db, "workflow_run.approved", run_id, user=user, request=request)
        await db.commit()
        r = await engine.execute(run_id)
    elif action == "reject" and r.status == "awaiting_approval":
        r.status = "rejected"
        await audit(db, "workflow_run.rejected", run_id, user=user, request=request)
        await db.commit()
    elif action == "retry" and r.status == "failed":
        await audit(db, "workflow_run.retried", run_id, user=user, request=request)
        await db.commit()
        r = await engine.execute(run_id)
    else:
        raise HTTPException(400, f"Cannot {action} a run in status '{r.status}'")
    return run_out(r)
