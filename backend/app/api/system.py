from __future__ import annotations

import time
from datetime import timedelta

import numpy as np
from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db, utcnow
from ..knowledge.store import embedder, index
from ..llm.client import get_llm
from ..logging_setup import recent_logs
from ..models import Call, User
from ..outbound.dialer import dialer
from ..security import require_role
from ..services.maintenance import apply_retention, system_metrics
from ..telephony.base import capacity, get_provider
from ..voice.stt import get_stt
from ..voice.tts import get_tts

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    """Unauthenticated liveness probe (no sensitive detail)."""
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "db": db_ok}


@router.get("/system/status")
async def status(_: User = Depends(require_role("viewer")), db: AsyncSession = Depends(get_db)):
    s = get_settings()
    t = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        db_status = {"ok": True, "latency_ms": round((time.perf_counter() - t) * 1000, 1),
                     "engine": s.database_url.split(":")[0]}
    except Exception as e:
        db_status = {"ok": False, "detail": str(e)}
    stt, tts = get_stt(), get_tts()
    integrations = {
        "smtp": bool(s.smtp_host), "sms": s.sms_provider or None, "n8n": s.n8n_base_url or None,
        "webhook_allowed_hosts": s.webhook_allowed_hosts,
    }
    return {
        "environment": s.environment,
        "database": db_status,
        "llm": await get_llm().health(),
        "stt": {"ok": stt.loaded, "model": s.stt_model, "detail": "loaded" if stt.loaded else "loads on first use"},
        "tts": {"ok": tts.loaded, "engine": s.tts_engine, "detail": "loaded" if tts.loaded else "loads on first use"},
        "knowledge": {"ok": len(index.entries) > 0, "chunks": len(index.entries), "embedder_loaded": embedder._model is not None},
        "telephony": await get_provider().health(),
        "dialer": {"outbound_enabled": s.outbound_enabled, "last_tick": dialer.last_tick.isoformat() if dialer.last_tick else None,
                   "last_error": dialer.last_error},
        "capacity": {"max_concurrent_calls": capacity().limit, "active": capacity().active},
        "integrations": integrations,
        "production_problems": s.validate_for_production() if s.is_production else [],
    }


@router.get("/system/metrics")
async def metrics(hours: int = 24, _: User = Depends(require_role("viewer")), db: AsyncSession = Depends(get_db)):
    calls = (await db.execute(select(Call).where(Call.created_at >= utcnow() - timedelta(hours=hours),
                                                 Call.answered_at.is_not(None)))).scalars().all()
    agg: dict[str, list[float]] = {}
    for c in calls:
        for k, v in (c.metrics or {}).items():
            if isinstance(v, dict) and "avg" in v:
                agg.setdefault(k, []).append(v["avg"])
    latency = {k: {"avg": round(float(np.mean(v))), "p50": round(float(np.percentile(v, 50))),
                   "p95": round(float(np.percentile(v, 95))), "calls": len(v)} for k, v in agg.items()}
    return {"system": system_metrics(), "latency": latency, "calls_measured": len(calls)}


@router.get("/system/logs")
async def logs(level: str | None = None, q: str | None = None, limit: int = 300, _: User = Depends(require_role("manager"))):
    return recent_logs(level, min(limit, 2000), q)


@router.post("/system/retention/run")
async def run_retention(_: User = Depends(require_role("admin"))):
    return await apply_retention()


@router.get("/system/config")
async def config_summary(_: User = Depends(require_role("admin"))):
    """Non-secret configuration for troubleshooting. Secrets are reported only as set/unset."""
    s = get_settings()
    secret_fields = {"secret_key", "admin_password", "llm_api_key", "llm_fallback_api_key", "asterisk_ami_password",
                     "asterisk_callback_token", "smtp_password", "twilio_auth_token", "sms_http_token", "webhook_signing_secret"}
    out = {}
    for k, v in s.model_dump().items():
        if k in secret_fields:
            out[k] = "set" if getattr(s, k).get_secret_value() else "unset"
        elif k in ("database_url",):
            out[k] = v.split("@")[-1] if "@" in v else v
        else:
            out[k] = str(v) if not isinstance(v, (int, float, bool, list, type(None))) else v
    return out
