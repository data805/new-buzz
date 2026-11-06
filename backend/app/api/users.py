from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ApiKey, AuditLog, User
from ..security import (
    API_SCOPES, ROLE_DESCRIPTIONS, ROLES, audit, generate_api_key, hash_password, password_problems, require_role,
)
from .auth import user_out

router = APIRouter(prefix="/api", tags=["users"])
admin = require_role("admin")


class UserIn(BaseModel):
    email: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field("", max_length=255)
    role: str = "viewer"
    password: str = Field(min_length=10, max_length=200)


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(None, min_length=10, max_length=200)


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str]


@router.get("/roles")
async def roles(_: User = Depends(require_role("viewer"))):
    return [{"role": r, "description": ROLE_DESCRIPTIONS[r]} for r in ROLES]


@router.get("/users")
async def list_users(_: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    return [user_out(u) for u in (await db.execute(select(User).order_by(User.created_at))).scalars()]


@router.post("/users", status_code=201)
async def create_user(body: UserIn, request: Request, me: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    if problems := password_problems(body.password):
        raise HTTPException(400, "Password needs " + ", ".join(problems))
    if await db.scalar(select(func.count()).select_from(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "A user with that email already exists")
    u = User(email=body.email.lower(), name=body.name, role=body.role, password_hash=hash_password(body.password),
             must_change_password=True)
    db.add(u)
    await audit(db, "user.created", u.email, user=me, details={"role": u.role}, request=request)
    await db.commit()
    return user_out(u)


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate, request: Request, me: User = Depends(admin),
                      db: AsyncSession = Depends(get_db)):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    changes = body.model_dump(exclude_none=True)
    if "role" in changes and changes["role"] not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    if u.id == me.id and (changes.get("role", "admin") != "admin" or changes.get("active") is False):
        raise HTTPException(400, "You cannot demote or deactivate yourself")
    if "password" in changes:
        if problems := password_problems(changes["password"]):
            raise HTTPException(400, "Password needs " + ", ".join(problems))
        u.password_hash = hash_password(changes.pop("password"))
        u.must_change_password = True
        u.token_version += 1
    for k, v in changes.items():
        setattr(u, k, v)
    if "role" in changes or changes.get("active") is False:
        u.token_version += 1
    await audit(db, "user.updated", u.email, user=me, details={k: v for k, v in changes.items()}, request=request)
    await db.commit()
    return user_out(u)


@router.get("/api-keys")
async def list_keys(_: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    keys = (await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars()
    return {"scopes": API_SCOPES, "keys": [
        {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes, "revoked": k.revoked,
         "created_at": k.created_at.isoformat(), "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None}
        for k in keys]}


@router.post("/api-keys", status_code=201)
async def create_key(body: ApiKeyIn, request: Request, me: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    bad = [s for s in body.scopes if s not in API_SCOPES]
    if bad or not body.scopes:
        raise HTTPException(400, f"Invalid scopes {bad}; allowed: {API_SCOPES}")
    raw, prefix, h = generate_api_key()
    k = ApiKey(name=body.name, prefix=prefix, key_hash=h, scopes=body.scopes, created_by=me.id)
    db.add(k)
    await audit(db, "api_key.created", body.name, user=me, details={"scopes": body.scopes}, request=request)
    await db.commit()
    return {"id": k.id, "key": raw, "note": "Copy this key now; it will not be shown again."}


@router.delete("/api-keys/{key_id}")
async def revoke_key(key_id: str, request: Request, me: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    k = await db.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "Key not found")
    k.revoked = True
    await audit(db, "api_key.revoked", k.name, user=me, request=request)
    await db.commit()
    return {"ok": True}


@router.get("/audit")
async def audit_log(limit: int = 200, action: str | None = None, _: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    return [{"id": a.id, "ts": a.ts.isoformat(), "actor": a.actor, "action": a.action, "target": a.target,
             "details": a.details, "ip": a.ip} for a in (await db.execute(q)).scalars()]


@router.get("/notifications")
async def notifications(_: User = Depends(require_role("operator")), db: AsyncSession = Depends(get_db)):
    q = select(AuditLog).where(AuditLog.action == "notification").order_by(AuditLog.id.desc()).limit(50)
    return [{"id": a.id, "ts": a.ts.isoformat(), "message": a.details.get("message", ""), "call_id": a.target}
            for a in (await db.execute(q)).scalars()]
