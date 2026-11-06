"""Answer-accuracy evaluation against the knowledge base, using the real LLM.

Each case is asked in a fresh conversation (text, no audio). Answerable cases
must contain an expected fact; unanswerable ones must admit not knowing and
must not contain invented specifics. Writes a JSON report.

    cd backend && python ../scripts/eval_answers.py [--out ../docs/results/eval_answers.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.conversation import CallContext, Conversation  # noqa: E402
from app.db import SessionLocal, create_all  # noqa: E402
from app.knowledge.store import index  # noqa: E402
from app.services.settings import AgentSettings  # noqa: E402

DONT_KNOW = r"(don'?t have|do not have|not (sure|available|published|specified|mentioned|listed|provided)|no information|unable to|can'?t confirm|confirm|call(back| you back)|team member|staff member|get back to you|take your (details|name))"

CASES = [
    # (question, required regex (any), forbidden regex)
    ("Where is BongoHive located?", r"ZEP-?RE|Alick Nkhata|Mass Media", None),
    ("What is the phone number for BongoHive?", r"211.?845.?600|845 600", None),
    ("How much does a hot desk cost per month?", DONT_KNOW + r"|not published|enquire", r"\bK\s?\d{2,}|\d{3,} ?kwacha|\$\s?\d+"),
    ("What are the hot desk hours?", r"8.?(am|:00)|eight", None),
    ("Is parking available?", r"parking", None),
    ("Is the AI assessment free?", r"free|no cost", None),
    ("Can I still apply for the AI for Business Accelerator 2026?", r"closed|24 July", None),
    ("How long is the accelerator programme?", r"12|twelve", None),
    ("Do you run student tours?", r"last Friday", None),
    ("What is Idea Spring?", r"idea", None),
    ("How long is the human-centred design course?", r"7|seven", None),
    # unanswerable from the KB -> must not invent
    ("Who is the CEO of BongoHive?", DONT_KNOW, r"\b(CEO|founder) (is|was) [A-Z][a-z]+"),
    ("How much does a private office cost?", DONT_KNOW + r"|not published|enquire", r"\bK\s?\d{2,}|\d{3,} ?kwacha|\$\s?\d+"),
    ("What is the email address for the AI Lab?", DONT_KNOW, r"[\w.]+@[\w.]+|\[email"),
    ("When is the next accelerator intake?", DONT_KNOW + r"|future intake", r"(January|February|March|April|May|June|August|September|October|November|December) 20\d\d"),
    ("Do you offer a discount for students?", DONT_KNOW, r"\b\d{1,2} ?(%|percent)|(no|not offer|not available|aren'?t available).{0,25}discount|discounts? (are|is) not"),
    ("Are you open on Saturdays?", DONT_KNOW + r"|Monday to Friday|weekdays|24/7", r"open on Saturdays? from"),
]


async def ask(question: str) -> tuple[str, float, int]:
    conv = Conversation(AgentSettings(), CallContext())
    conv.add_user(question)
    hits = await conv.retrieve_async(question)
    t = time.perf_counter()
    text = " ".join([s async for s in conv.reply_sentences(hits)])  # same guarded path as live calls
    return text.strip(), time.perf_counter() - t, len(hits), conv.blocked


async def main(out: str | None) -> int:
    await create_all()
    async with SessionLocal() as db:
        await index.rebuild(db)
    results, passed = [], 0
    for q, need, forbid in CASES:
        ans, dt, n_hits, blocked = await ask(q)
        ok = bool(re.search(need, ans, re.I)) and not (forbid and re.search(forbid, ans, re.I))
        passed += ok
        results.append({"question": q, "answer": ans, "pass": ok, "seconds": round(dt, 1), "knowledge_hits": n_hits, "blocked": blocked})
        note = f" [guard blocked: {blocked['sentence']!r} ({blocked['reason']})]" if blocked else ""
        print(f"[{'PASS' if ok else 'FAIL'}] ({dt:.1f}s, {n_hits} hits) {q}\n       -> {ans}{note}\n", flush=True)
    summary = {"passed": passed, "total": len(CASES), "accuracy": round(passed / len(CASES), 3)}
    print(json.dumps(summary))
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    sys.exit(asyncio.run(main(ap.parse_args().out)))
