"""Authentication, role-based access control, API keys and rate limiting."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db, utcnow
from .models import ApiKey, AuditLog, User

COOKIE_NAME = "va_session"
ROLES = ["viewer", "operator", "manager", "admin"]
ROLE_DESCRIPTIONS = {
    "viewer": "Read-only access to dashboards, call history and knowledge",
    "operator": "Viewer + test console, live monitoring, manage enquiries/tickets/callbacks",
    "manager": "Operator + knowledge, campaigns, workflows and agent configuration (except approvals)",
    "admin": "Everything, incl. users, API keys, approvals of campaigns/workflows and data deletion",
}
API_SCOPES = ["calls:create", "calls:read", "events:create", "knowledge:read", "contacts:write"]

_ph = PasswordHasher()


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("at least 10 characters")
    if password.lower() == password or password.upper() == password:
        problems.append("mixed upper and lower case")
    if not any(c.isdigit() for c in password):
        problems.append("at least one digit")
    return problems


# ------------------------------------------------------------------ sessions
def create_session_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "role": user.role,
        "tv": user.token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=s.session_hours)).timestamp()),
    }
    return jwt.encode(payload, s.secret_key.get_secret_value(), algorithm="HS256")


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().secret_key.get_secret_value(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


async def _user_from_token(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    data = decode_session_token(token)
    if not data:
        return None
    user = await db.get(User, data.get("sub"))
    if not user or not user.active or user.token_version != data.get("tv"):
        return None
    return user


async def _demo_user(db: AsyncSession) -> User | None:
    """AUTH_DISABLED (local demos only): act as the first active admin."""
    s = get_settings()
    if not s.auth_disabled or s.is_production:
        return None
    return (await db.execute(
        select(User).where(User.active.is_(True), User.role == "admin").order_by(User.created_at).limit(1)
    )).scalar_one_or_none()


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer ") and not auth[7:].startswith("vak_"):
        token = auth[7:]
    user = await _user_from_token(token, db) or await _demo_user(db)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    request.state.user = user
    return user


async def ws_user(websocket: WebSocket, db: AsyncSession) -> User | None:
    return await _user_from_token(websocket.cookies.get(COOKIE_NAME), db) or await _demo_user(db)


def require_role(min_role: str):
    """Dependency factory: user must have at least `min_role`."""
    need = ROLES.index(min_role)

    async def dep(user: User = Depends(current_user)) -> User:
        if ROLES.index(user.role) < need:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role '{min_role}' or higher")
        return user

    return dep


def has_role(user: User, min_role: str) -> bool:
    return ROLES.index(user.role) >= ROLES.index(min_role)


# ------------------------------------------------------------------ API keys
def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, hash). Plaintext is shown once to the user."""
    raw = "vak_" + secrets.token_urlsafe(32)
    return raw, raw[:12], _hash_key(raw)


def _hash_key(raw: str) -> str:
    return hmac.new(get_settings().secret_key.get_secret_value().encode(), raw.encode(), hashlib.sha256).hexdigest()


def require_api_key(scope: str):
    async def dep(request: Request, db: AsyncSession = Depends(get_db)) -> ApiKey:
        raw = request.headers.get("x-api-key") or ""
        auth = request.headers.get("authorization", "")
        if not raw and auth.lower().startswith("bearer vak_"):
            raw = auth[7:]
        if not raw.startswith("vak_"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing API key")
        rows = (await db.execute(select(ApiKey).where(ApiKey.prefix == raw[:12], ApiKey.revoked.is_(False)))).scalars()
        h = _hash_key(raw)
        for key in rows:
            if hmac.compare_digest(key.key_hash, h):
                if scope not in key.scopes:
                    raise HTTPException(status.HTTP_403_FORBIDDEN, f"API key lacks scope '{scope}'")
                if not rate_limiter.allow(f"apikey:{key.id}", limit=120, window_s=60):
                    raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "API key rate limit exceeded")
                key.last_used_at = utcnow()
                await db.commit()
                return key
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")

    return dep


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    """In-process sliding-window limiter. For multi-instance deployments put a
    shared limiter (e.g. at the reverse proxy) in front as well."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self._hits) > 50_000:  # crude memory guard
            self._hits.clear()
        return True

    def reset(self) -> None:
        self._hits.clear()


rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


def rate_limit(name: str, limit: int, window_s: float = 60):
    async def dep(request: Request) -> None:
        if not rate_limiter.allow(f"{name}:{client_ip(request)}", limit, window_s):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down")

    return dep


# ------------------------------------------------------------------ audit
async def audit(
    db: AsyncSession, action: str, target: str = "", user: User | None = None,
    details: dict | None = None, request: Request | None = None, actor: str | None = None,
) -> None:
    db.add(AuditLog(
        user_id=user.id if user else None,
        actor=actor or (user.email if user else "system"),
        action=action, target=target, details=details or {},
        ip=client_ip(request) if request else "",
    ))
