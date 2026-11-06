"""Component benchmark: STT accuracy/latency, TTS speed, LLM latency, retrieval,
plus CPU/RAM. Run on the target server to set realistic performance targets.

    cd backend && python ../scripts/benchmark.py [--stt small.en base.en] [--tts kokoro piper] [--out ../docs/results/benchmark.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
AUDIO = ROOT / "sample_data" / "audio"


def wer(ref: str, hyp: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("bongo hive", "bongohive")).split()  # noqa: E731
    r, h = norm(ref), norm(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=int)
    d[:, 0] = range(len(r) + 1)
    d[0, :] = range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r), len(h)] / max(1, len(r))


def bench_stt(models: list[str]) -> list[dict]:
    from faster_whisper import WhisperModel

    refs = dict(line.split("\t", 1) for line in (AUDIO / "transcripts.txt").read_text().splitlines() if "\t" in line)
    out = []
    for m in models:
        model = WhisperModel(m, device="cpu", compute_type="int8", cpu_threads=4, download_root=str(ROOT / "models" / "whisper"))
        list(model.transcribe(np.zeros(16000, dtype=np.float32))[0])
        rows = []
        for name, ref in refs.items():
            audio, _ = sf.read(AUDIO / f"{name}.wav", dtype="float32")
            t = time.perf_counter()
            segs, _ = model.transcribe(audio, beam_size=1, language="en", without_timestamps=True, condition_on_previous_text=False)
            hyp = " ".join(s.text.strip() for s in segs)
            dt = time.perf_counter() - t
            rows.append({"clip": name, "audio_s": round(len(audio) / 16000, 2), "seconds": round(dt, 2), "wer": round(wer(ref, hyp), 3), "hyp": hyp})
        out.append({"model": m, "mean_wer": round(float(np.mean([r["wer"] for r in rows])), 3),
                    "rtf": round(sum(r["seconds"] for r in rows) / sum(r["audio_s"] for r in rows), 3), "clips": rows})
        print(json.dumps({k: v for k, v in out[-1].items() if k != "clips"}), flush=True)
    return out


def bench_tts(engines: list[str]) -> list[dict]:
    from app.voice.tts import OUTPUT_RATE, TTSEngine

    tts = TTSEngine()
    texts = ["Sure, I can help with that.",
             "BongoHive is at ZEP-RE Business Park, Mass Media, on Alick Nkhata Road in Lusaka.",
             "Prices for coworking are not published, but I can take your details so the team can share current pricing with you."]
    out = []
    for eng in engines:
        voice = "af_heart" if eng == "kokoro" else "en_GB-cori-high"
        try:
            tts.synth_sync("warm up", eng, voice)
        except Exception as e:
            out.append({"engine": eng, "error": str(e)})
            continue
        rows = []
        for t_ in texts:
            tts._cache.clear()
            r = tts.synth_sync(t_, eng, voice)
            secs = len(r.audio) / OUTPUT_RATE
            rows.append({"chars": len(t_), "audio_s": round(secs, 2), "seconds": round(r.elapsed_ms / 1000, 2), "rtf": round(r.elapsed_ms / 1000 / secs, 3)})
        out.append({"engine": eng, "voice": voice, "first_sentence_s": rows[0]["seconds"], "mean_rtf": round(float(np.mean([r["rtf"] for r in rows])), 3), "rows": rows})
        print(json.dumps(out[-1]), flush=True)
    return out


async def bench_llm(n: int) -> dict:
    from app.agent.conversation import CallContext, Conversation
    from app.db import SessionLocal, create_all
    from app.knowledge.store import index
    from app.services.settings import AgentSettings

    await create_all()
    async with SessionLocal() as db:
        await index.rebuild(db)
    qs = ["Where are you located?", "Is the AI assessment free?", "What coworking plans do you have?", "Can I still apply to the accelerator?"]
    first, total, ret = [], [], []
    for i in range(n):
        conv = Conversation(AgentSettings(), CallContext())
        q = qs[i % len(qs)]
        conv.add_user(q)
        t = time.perf_counter()
        hits = await conv.retrieve_async(q)
        ret.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        ft = None
        async for _ in conv.stream_reply(hits):
            ft = ft or (time.perf_counter() - t)
        first.append(ft * 1000)
        total.append((time.perf_counter() - t) * 1000)
    res = {"model": os.environ.get("LLM_MODEL", "default"), "runs": n, "first_token_ms_avg": round(float(np.mean(first))),
           "first_token_ms_p95": round(float(np.percentile(first, 95))), "full_reply_ms_avg": round(float(np.mean(total))),
           "retrieval_ms_avg": round(float(np.mean(ret)), 1)}
    print(json.dumps(res), flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stt", nargs="*", default=["base.en", "small.en"])
    ap.add_argument("--tts", nargs="*", default=["kokoro", "piper"])
    ap.add_argument("--llm-runs", type=int, default=4)
    ap.add_argument("--out")
    a = ap.parse_args()
    proc = psutil.Process()
    psutil.cpu_percent(None)
    report = {
        "host": {"platform": platform.platform(), "cpu": platform.processor() or platform.machine(), "cores": psutil.cpu_count(),
                 "ram_gb": round(psutil.virtual_memory().total / 2**30, 1)},
        "stt": bench_stt(a.stt) if a.stt else [],
        "tts": bench_tts(a.tts) if a.tts else [],
        "llm": asyncio.run(bench_llm(a.llm_runs)) if a.llm_runs else {},
    }
    report["resources"] = {"process_rss_mb": proc.memory_info().rss // 2**20, "cpu_percent_during_run": psutil.cpu_percent(None)}
    print(json.dumps(report["resources"]))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
