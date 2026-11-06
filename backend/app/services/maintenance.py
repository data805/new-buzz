"""Data retention, system metrics and first-run bootstrap."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import timedelta
from pathlib import Path

import psutil
from sqlalchemy import delete, func, select, update

from ..config import ROOT_DIR, get_settings
from ..db import SessionLocal, utcnow
from ..models import Call, CallTurn, KnowledgeSource, User, Workflow
from ..security import hash_password
from .settings import get_agent_settings

log = logging.getLogger(__name__)
_STARTED = time.time()


# ------------------------------------------------------------------ retention
async def apply_retention() -> dict:
    """Delete transcripts and recordings older than the configured retention."""
    async with SessionLocal() as db:
        s = await get_agent_settings(db)
        t_cut = utcnow() - timedelta(days=s.retention.transcripts_days)
        r_cut = utcnow() - timedelta(days=s.retention.recordings_days)
        old_ids = (await db.execute(select(Call.id).where(Call.created_at < t_cut))).scalars().all()
        n_turns = 0
        if old_ids:
            res = await db.execute(delete(CallTurn).where(CallTurn.call_id.in_(old_ids)))
            n_turns = res.rowcount or 0
            await db.execute(update(Call).where(Call.id.in_(old_ids)).values(summary="[expired]", extracted={}, action_items=[]))
        recs = (await db.execute(select(Call).where(Call.created_at < r_cut, Call.recording_path.is_not(None)))).scalars().all()
        n_recs = 0
        for c in recs:
            if c.recording_path and Path(c.recording_path).exists():
                Path(c.recording_path).unlink(missing_ok=True)
                n_recs += 1
            c.recording_path = None
        await db.commit()
    if n_turns or n_recs:
        log.info("Retention applied", extra={"ctx": {"turns_deleted": n_turns, "recordings_deleted": n_recs}})
    return {"transcript_turns_deleted": n_turns, "recordings_deleted": n_recs}


async def retention_loop() -> None:
    while True:
        try:
            await apply_retention()
        except Exception:
            log.exception("Retention job failed")
        await asyncio.sleep(6 * 3600)


async def erase_number(phone: str) -> dict:
    """Data-subject erasure: remove transcripts, recordings and contact details for a number."""
    from ..models import Callback, Contact, Enquiry, Ticket

    async with SessionLocal() as db:
        calls = (await db.execute(select(Call).where((Call.from_number == phone) | (Call.to_number == phone)))).scalars().all()
        for c in calls:
            if c.recording_path:
                Path(c.recording_path).unlink(missing_ok=True)
            await db.execute(delete(CallTurn).where(CallTurn.call_id == c.id))
            c.summary, c.extracted, c.action_items, c.recording_path = "[erased]", {}, [], None
            c.from_number = "[erased]" if c.from_number == phone else c.from_number
            c.to_number = "[erased]" if c.to_number == phone else c.to_number
        for model in (Enquiry, Callback, Ticket):
            await db.execute(delete(model).where(model.phone == phone))
        contact = (await db.execute(select(Contact).where(Contact.phone == phone))).scalar_one_or_none()
        if contact:
            # keep a minimal do-not-call marker so the number is never called again
            contact.name = contact.email = contact.organisation = contact.notes = ""
            contact.consent_status = "revoked"
            contact.opted_out_at = contact.opted_out_at or utcnow()
        await db.commit()
    return {"calls_erased": len(calls)}


# ------------------------------------------------------------------ metrics
def system_metrics() -> dict:
    proc = psutil.Process(os.getpid())
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(get_settings().data_dir))
    gpu = None
    try:
        import ctranslate2

        gpu = {"cuda_devices": ctranslate2.get_cuda_device_count()}
    except Exception:
        pass
    return {
        "uptime_s": round(time.time() - _STARTED),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "load_avg": [round(x, 2) for x in os.getloadavg()],
        "memory": {"total_mb": vm.total // 2**20, "used_percent": vm.percent, "process_rss_mb": proc.memory_info().rss // 2**20},
        "disk": {"free_gb": round(disk.free / 2**30, 1), "used_percent": disk.percent},
        "gpu": gpu,
    }


# ------------------------------------------------------------------ bootstrap
async def bootstrap() -> None:
    from ..workflows.templates import TEMPLATES

    s = get_settings()
    async with SessionLocal() as db:
        if not await db.scalar(select(func.count()).select_from(User)):
            db.add(User(email=s.admin_email.lower(), name="Administrator", role="admin",
                        password_hash=hash_password(s.admin_password.get_secret_value()), must_change_password=True))
            log.warning("Created bootstrap admin user", extra={"ctx": {"email": s.admin_email}})
        if not await db.scalar(select(func.count()).select_from(Workflow)):
            for t in TEMPLATES:
                db.add(Workflow(name=t["name"], description=t["description"], trigger=t["trigger"], conditions=t["conditions"],
                                actions=t["actions"], enabled=t.get("enabled", False), template=t["template"],
                                require_run_approval=t.get("require_run_approval", False), created_by="system"))
        await db.commit()
        has_knowledge = await db.scalar(select(func.count()).select_from(KnowledgeSource))
    if not has_knowledge:
        await load_sample_knowledge()


async def load_sample_knowledge() -> int:
    from ..knowledge.service import ingest_source
    from ..knowledge.store import index

    folder = ROOT_DIR / "sample_data" / "knowledge"
    n = 0
    async with SessionLocal() as db:
        for f in sorted(folder.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), f.stem)
            src = KnowledgeSource(title=title, kind="text", filename=f.name, category=f.stem.split("_")[0],
                                  status="approved", is_sample=True, content=text, created_by="sample-data",
                                  approved_by="sample-data")
            db.add(src)
            await db.commit()
            await ingest_source(db, src)
            n += 1
        await index.rebuild(db)
    log.info("Loaded sample knowledge", extra={"ctx": {"sources": n}})
    return n
