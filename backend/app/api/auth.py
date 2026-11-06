from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db, utcnow
from ..models import User
from ..security import (
    COOKIE_NAME, audit, create_session_token, current_user, hash_password, password_problems, rate_limit,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "active": u.active,
            "must_change_password": u.must_change_password,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None}


@router.post("/login", dependencies=[Depends(rate_limit("login", 10, 60))])
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email.lower().strip()))).scalar_one_or_none()
    now = utcnow()
    if user and user.locked_until and user.locked_until.replace(tzinfo=now.tzinfo) > now:
        raise HTTPException(423, "Account temporarily locked after repeated failed logins. Try again later.")
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= 5:
                user.locked_until = now + timedelta(minutes=15)
                user.failed_logins = 0
            await audit(db, "auth.login_failed", user.email, request=request)
            await db.commit()
        raise HTTPException(401, "Invalid email or password")
    user.failed_logins = 0
    user.last_login_at = now
    await audit(db, "auth.login", user.email, user=user, request=request)
    await db.commit()
    s = get_settings()
    response.set_cookie(COOKIE_NAME, create_session_token(user), httponly=True, secure=s.cookie_secure,
                        samesite="strict", max_age=s.session_hours * 3600, path="/")
    return user_out(user)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    out = user_out(user)
    if get_settings().auth_disabled:
        out["must_change_password"] = False  # no password prompt in sign-in-free demo mode
        out["auth_disabled"] = True
    return out


@router.post("/change-password")
async def change_password(body: ChangePasswordIn, request: Request, response: Response,
                          user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(400, "Password needs " + ", ".join(problems))
    user = await db.get(User, user.id)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version += 1  # invalidate other sessions
    await audit(db, "auth.password_changed", user.email, user=user, request=request)
    await db.commit()
    s = get_settings()
    response.set_cookie(COOKIE_NAME, create_session_token(user), httponly=True, secure=s.cookie_secure,
                        samesite="strict", max_age=s.session_hours * 3600, path="/")
    return {"ok": True}
