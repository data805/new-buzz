"""Knowledge source lifecycle: create -> ingest (chunk + embed) -> approve -> index."""
from __future__ import annotations

import asyncio
import hashlib
import logging

import numpy as np
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import KnowledgeChunk, KnowledgeSource
from .store import chunk_text, embedder, index

log = logging.getLogger(__name__)


async def ingest_source(db: AsyncSession, source: KnowledgeSource) -> KnowledgeSource:
    """(Re)build the chunks for a source from its stored text content."""
    text = source.content or ""
    source.content_hash = hashlib.sha256(text.encode()).hexdigest()
    try:
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("No text content found")
        texts = [f"{source.title}. {c.heading}\n{c.text}" if c.heading else f"{source.title}.\n{c.text}" for c in chunks]
        vecs = await asyncio.to_thread(embedder.embed_documents, texts)
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id))
        for i, (c, v) in enumerate(zip(chunks, vecs)):
            db.add(KnowledgeChunk(source_id=source.id, idx=i, heading=c.heading[:500], text=c.text,
                                  embedding=np.asarray(v, dtype=np.float32).tobytes()))
        source.chunk_count = len(chunks)
        source.ingest_status = "ready"
        source.error = None
    except Exception as e:
        log.exception("Ingestion failed", extra={"ctx": {"source": source.id}})
        source.ingest_status = "error"
        source.error = str(e)[:2000]
        source.chunk_count = 0
    await db.commit()
    # always rebuild: an edit may have moved a previously approved source back to draft
    await index.rebuild(db)
    return source


def faq_to_text(items: list[dict]) -> str:
    return "\n\n".join(f"## {i['question'].strip()}\n{i['answer'].strip()}" for i in items if i.get("question") and i.get("answer"))
