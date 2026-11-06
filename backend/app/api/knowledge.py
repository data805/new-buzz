from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..knowledge.extract import SUPPORTED_EXTENSIONS, ExtractionError, extract_text, fetch_url
from ..knowledge.service import faq_to_text, ingest_source
from ..knowledge.store import index
from ..models import KnowledgeSource, User
from ..security import audit, require_role

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
viewer = require_role("viewer")
manager = require_role("manager")


def source_out(s: KnowledgeSource, content: bool = False) -> dict:
    d = {"id": s.id, "title": s.title, "kind": s.kind, "filename": s.filename, "url": s.url, "category": s.category,
         "tags": s.tags, "status": s.status, "is_sample": s.is_sample, "chunk_count": s.chunk_count,
         "ingest_status": s.ingest_status, "error": s.error, "created_by": s.created_by, "approved_by": s.approved_by,
         "created_at": s.created_at.isoformat() if s.created_at else None,
         "updated_at": s.updated_at.isoformat() if s.updated_at else None, "chars": len(s.content or "")}
    if content:
        d["content"] = s.content
    return d


@router.get("")
async def list_sources(status: str | None = None, category: str | None = None, q: str | None = None,
                       _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    stmt = select(KnowledgeSource).order_by(KnowledgeSource.updated_at.desc())
    if status:
        stmt = stmt.where(KnowledgeSource.status == status)
    if category:
        stmt = stmt.where(KnowledgeSource.category == category)
    if q:
        stmt = stmt.where(KnowledgeSource.title.like(f"%{q}%") | KnowledgeSource.content.like(f"%{q}%"))
    rows = (await db.execute(stmt)).scalars().all()
    sample = await db.scalar(select(func.count()).select_from(KnowledgeSource).where(
        KnowledgeSource.is_sample.is_(True), KnowledgeSource.status == "approved"))
    return {"items": [source_out(s) for s in rows], "indexed_chunks": len(index.entries), "sample_sources_live": sample,
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS), "allowed_domains": get_settings().knowledge_allowed_domains}


@router.get("/{source_id}")
async def get_source(source_id: str, _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    s = await db.get(KnowledgeSource, source_id)
    if not s:
        raise HTTPException(404, "Source not found")
    return source_out(s, content=True)


async def _create(db: AsyncSession, user: User, request: Request, **fields) -> KnowledgeSource:
    s = KnowledgeSource(created_by=user.email, status="draft", **fields)
    db.add(s)
    await audit(db, "knowledge.created", s.title, user=user, details={"kind": s.kind}, request=request)
    await db.commit()
    await ingest_source(db, s)
    return s


@router.post("/upload", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), title: str = Form(""), category: str = Form("general"),
                 tags: str = Form(""), user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    data = await file.read()
    try:
        text = await asyncio.to_thread(extract_text, file.filename or "upload.txt", data)
    except ExtractionError as e:
        raise HTTPException(400, str(e)) from e
    s = await _create(db, user, request, title=(title or file.filename or "Untitled")[:500], kind="file",
                      filename=file.filename, category=category[:100] or "general",
                      tags=[t.strip() for t in tags.split(",") if t.strip()], content=text)
    return source_out(s)


class UrlIn(BaseModel):
    url: str = Field(max_length=2000)
    category: str = "website"
    title: str = ""


@router.post("/url", status_code=201)
async def add_url(body: UrlIn, request: Request, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    try:
        title, text = await fetch_url(body.url, get_settings().knowledge_allowed_domains)
    except ExtractionError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"Could not fetch page: {e}") from e
    s = await _create(db, user, request, title=(body.title or title)[:500], kind="url", url=body.url,
                      category=body.category[:100], content=f"# {body.title or title}\n\nSource: {body.url}\n\n{text}")
    return source_out(s)


class FaqItem(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    answer: str = Field(min_length=1, max_length=10000)


class TextIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    category: str = "general"
    tags: list[str] = []
    content: str | None = Field(None, max_length=2_000_000)
    faqs: list[FaqItem] | None = None


@router.post("/text", status_code=201)
async def add_text(body: TextIn, request: Request, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    if body.faqs:
        content, kind = faq_to_text([f.model_dump() for f in body.faqs]), "faq"
    elif body.content:
        content, kind = body.content, "text"
    else:
        raise HTTPException(400, "Provide content or faqs")
    s = await _create(db, user, request, title=body.title, kind=kind, category=body.category, tags=body.tags, content=content)
    return source_out(s)


class SourceUpdate(BaseModel):
    title: str | None = Field(None, max_length=500)
    category: str | None = Field(None, max_length=100)
    tags: list[str] | None = None
    content: str | None = Field(None, max_length=2_000_000)
    is_sample: bool | None = None


@router.patch("/{source_id}")
async def update_source(source_id: str, body: SourceUpdate, request: Request, user: User = Depends(manager),
                        db: AsyncSession = Depends(get_db)):
    s = await db.get(KnowledgeSource, source_id)
    if not s:
        raise HTTPException(404, "Source not found")
    changes = body.model_dump(exclude_none=True)
    reingest = "content" in changes or "title" in changes
    for k, v in changes.items():
        setattr(s, k, v)
    if "content" in changes and s.status == "approved":
        s.status = "draft"  # edited content must be re-approved
    await audit(db, "knowledge.updated", s.title, user=user, details={"fields": list(changes)}, request=request)
    await db.commit()
    if reingest:
        await ingest_source(db, s)
    else:
        await index.rebuild(db)
    return source_out(s)


class StatusIn(BaseModel):
    status: str


@router.post("/{source_id}/status")
async def set_status(source_id: str, body: StatusIn, request: Request, user: User = Depends(manager),
                     db: AsyncSession = Depends(get_db)):
    if body.status not in ("draft", "approved", "archived"):
        raise HTTPException(400, "status must be draft, approved or archived")
    s = await db.get(KnowledgeSource, source_id)
    if not s:
        raise HTTPException(404, "Source not found")
    if body.status == "approved" and s.ingest_status != "ready":
        raise HTTPException(400, f"Cannot approve: ingestion status is '{s.ingest_status}' ({s.error or 'not ready'})")
    s.status = body.status
    if body.status == "approved":
        s.approved_by = user.email
    await audit(db, f"knowledge.{body.status}", s.title, user=user, request=request)
    await db.commit()
    await index.rebuild(db)
    return source_out(s)


@router.delete("/{source_id}")
async def delete_source(source_id: str, request: Request, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    s = await db.get(KnowledgeSource, source_id)
    if not s:
        raise HTTPException(404, "Source not found")
    await db.delete(s)
    await audit(db, "knowledge.deleted", s.title, user=user, request=request)
    await db.commit()
    await index.rebuild(db)
    return {"ok": True}


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(5, ge=1, le=20)
    min_score: float | None = None


@router.post("/search")
async def search(body: SearchIn, _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    from ..services.settings import get_agent_settings

    s = await get_agent_settings(db)
    t = time.perf_counter()
    hits = await asyncio.to_thread(index.search, body.query, body.top_k,
                                   s.knowledge_min_score if body.min_score is None else body.min_score)
    return {"elapsed_ms": round((time.perf_counter() - t) * 1000, 1), "hits": [
        {"source_id": h.source_id, "source_title": h.source_title, "heading": h.heading, "text": h.text,
         "dense": round(h.dense, 3), "score": round(h.score, 4), "is_sample": h.is_sample} for h in hits]}


@router.post("/reindex")
async def reindex(_: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    return {"chunks": await index.rebuild(db)}
