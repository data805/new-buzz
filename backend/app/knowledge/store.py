"""Chunking, embeddings and hybrid (dense + BM25) retrieval.

The index is held in memory and rebuilt from the database whenever approved
knowledge changes. For a customer-service knowledge base (thousands of chunks)
this is faster than an external vector database and has no extra moving parts;
see docs/ARCHITECTURE.md for when to move to pgvector."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("""a an and are as at be but by can do does for from has have how i if in is it its me my
of on or our so that the their them there these they this to was we what when where which who why will
with you your about please tell would like could should any some just also more""".split())


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


# ------------------------------------------------------------------ chunking
@dataclass
class Chunk:
    heading: str
    text: str


def chunk_text(text: str, target_chars: int = 900, overlap_chars: int = 150) -> list[Chunk]:
    """Split on markdown headings first, then paragraphs, keeping the nearest
    heading attached to each chunk so retrieved snippets stay self-explanatory."""
    sections: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in text.splitlines():
        m = re.match(r"^\s*#{1,6}\s+(.*)", line)
        if m:
            if "".join(buf).strip():
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        sections.append((heading, "\n".join(buf).strip()))

    chunks: list[Chunk] = []
    for heading, body in sections:
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        cur = ""
        for p in paras:
            if len(p) > target_chars:  # split very long paragraphs on sentences
                sents = re.split(r"(?<=[.!?])\s+", p)
            else:
                sents = [p]
            for s in sents:
                if cur and len(cur) + len(s) + 1 > target_chars:
                    chunks.append(Chunk(heading, cur.strip()))
                    cur = cur[-overlap_chars:] if overlap_chars else ""
                    cur = cur[cur.find(" ") + 1:] if " " in cur else cur
                cur = (cur + "\n" + s) if cur else s
        if cur.strip():
            chunks.append(Chunk(heading, cur.strip()))
    return chunks


# ------------------------------------------------------------------ embeddings
class Embedder:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                s = get_settings()
                t = time.perf_counter()
                self._model = TextEmbedding(s.embedding_model, cache_dir=str(s.models_dir / "fastembed"), threads=2)
                log.info("Embedding model loaded", extra={"ctx": {"model": s.embedding_model, "s": round(time.perf_counter() - t, 1)}})
            return self._model

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        vecs = np.array(list(self.load().passage_embed(texts)), dtype=np.float32)
        return _normalise(vecs)

    def embed_query(self, text: str) -> np.ndarray:
        return _normalise(np.array(list(self.load().query_embed([text])), dtype=np.float32))[0]


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.maximum(n, 1e-9)


embedder = Embedder()


# ------------------------------------------------------------------ index
@dataclass
class Hit:
    chunk_id: int
    source_id: str
    source_title: str
    heading: str
    text: str
    score: float
    dense: float
    is_sample: bool = False


@dataclass
class _Entry:
    chunk_id: int
    source_id: str
    source_title: str
    heading: str
    text: str
    is_sample: bool
    tokens: Counter = field(default_factory=Counter)
    length: int = 0


class KnowledgeIndex:
    def __init__(self) -> None:
        self.entries: list[_Entry] = []
        self.matrix = np.zeros((0, 384), dtype=np.float32)
        self.df: Counter = Counter()
        self.avg_len = 1.0
        self.version = 0
        self._lock = asyncio.Lock()

    async def rebuild(self, db: AsyncSession) -> int:
        from ..models import KnowledgeChunk, KnowledgeSource

        async with self._lock:
            rows = (await db.execute(
                select(KnowledgeChunk, KnowledgeSource)
                .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
                .where(KnowledgeSource.status == "approved")
                .order_by(KnowledgeChunk.id)
            )).all()
            entries, vecs = [], []
            df: Counter = Counter()
            for ch, src in rows:
                toks = Counter(tokenize(f"{src.title} {ch.heading} {ch.text}"))
                entries.append(_Entry(ch.id, src.id, src.title, ch.heading, ch.text, src.is_sample, toks, sum(toks.values())))
                vecs.append(np.frombuffer(ch.embedding, dtype=np.float32))
                df.update(toks.keys())
            self.entries = entries
            self.matrix = np.vstack(vecs) if vecs else np.zeros((0, 384), dtype=np.float32)
            self.df = df
            self.avg_len = (sum(e.length for e in entries) / len(entries)) if entries else 1.0
            self.version += 1
            log.info("Knowledge index rebuilt", extra={"ctx": {"chunks": len(entries)}})
            return len(entries)

    def _bm25(self, q_tokens: list[str], k1: float = 1.4, b: float = 0.75) -> np.ndarray:
        n = len(self.entries)
        scores = np.zeros(n, dtype=np.float32)
        for t in set(q_tokens):
            df = self.df.get(t, 0)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, e in enumerate(self.entries):
                tf = e.tokens.get(t, 0)
                if tf:
                    scores[i] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * e.length / self.avg_len))
        return scores

    def search(self, query: str, top_k: int = 4, min_score: float = 0.58) -> list[Hit]:
        """Hybrid retrieval: cosine similarity fused with BM25 via reciprocal rank
        fusion. `min_score` is applied to the dense similarity so that unrelated
        questions return nothing (which lets the agent say "I don't know")."""
        if not self.entries or not query.strip():
            return []
        qv = embedder.embed_query(query)
        dense = self.matrix @ qv
        bm25 = self._bm25(tokenize(query))
        k = 60
        dense_rank = np.argsort(-dense)
        rrf = np.zeros(len(self.entries), dtype=np.float32)
        for r, i in enumerate(dense_rank):
            rrf[i] += 1.0 / (k + r)
        if bm25.max() > 0:
            for r, i in enumerate(np.argsort(-bm25)):
                if bm25[i] <= 0:
                    break
                rrf[i] += 1.0 / (k + r)
        bm25_norm = bm25 / bm25.max() if bm25.max() > 0 else bm25
        hits = []
        for i in np.argsort(-rrf)[: top_k * 3]:
            # a strong keyword match can rescue a slightly-below-threshold dense score
            if dense[i] < min_score and not (dense[i] >= min_score - 0.1 and bm25_norm[i] > 0.6):
                continue
            e = self.entries[i]
            hits.append(Hit(e.chunk_id, e.source_id, e.source_title, e.heading, e.text, float(rrf[i]), float(dense[i]), e.is_sample))
            if len(hits) >= top_k:
                break
        return hits


index = KnowledgeIndex()
