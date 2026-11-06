"""Concurrent-call load test against a running server.

Opens N simultaneous /ws/voice calls, each speaking the same recorded question,
and reports response latency per call plus calls rejected for capacity.

    python scripts/loadtest.py --calls 4 --base http://localhost:8000 --password '...'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
import websockets

ROOT = Path(__file__).resolve().parents[1]


async def one_call(i: int, ws_url: str, cookie: str, pcm: bytes, timeout: float) -> dict:
    t_start = time.monotonic()
    try:
        async with websockets.connect(ws_url, additional_headers={"Cookie": cookie}, max_size=None) as ws:
            state, greeted, t_stop, first_audio = "starting", False, None, None
            async def reader():
                nonlocal state, greeted, first_audio
                async for m in ws:
                    if isinstance(m, bytes):
                        if t_stop and first_audio is None:
                            first_audio = time.monotonic() - t_stop
                        continue
                    ev = json.loads(m)
                    if ev["type"] == "error":
                        raise RuntimeError(ev["message"])
                    if ev["type"] == "state":
                        state = ev["state"]
                        if state == "listening":
                            greeted = True
            rd = asyncio.create_task(reader())
            t0 = time.monotonic()
            while not greeted and time.monotonic() - t0 < timeout:
                if rd.done():
                    rd.result()
                await asyncio.sleep(0.05)
            for k in range(0, len(pcm), 1024):
                await ws.send(pcm[k:k + 1024])
                await asyncio.sleep(0.032)
            t_stop = time.monotonic()
            while first_audio is None and time.monotonic() - t_stop < timeout:
                if rd.done():
                    rd.result()
                await asyncio.sleep(0.05)
            await ws.send(json.dumps({"type": "hangup"}))
            rd.cancel()
            return {"call": i, "ok": first_audio is not None, "response_s": round(first_audio, 2) if first_audio else None,
                    "total_s": round(time.monotonic() - t_start, 1)}
    except Exception as e:
        return {"call": i, "ok": False, "error": str(e)[:120]}


async def main(a) -> None:
    async with httpx.AsyncClient(base_url=a.base) as c:
        (await c.post("/api/auth/login", json={"email": a.email, "password": a.password})).raise_for_status()
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    audio, _ = sf.read(ROOT / "sample_data" / "audio" / "q_location.wav", dtype="int16")
    pcm = audio.tobytes() + np.zeros(16000, dtype=np.int16).tobytes()
    ws_url = a.base.replace("http", "ws", 1) + "/ws/voice?mode=test"
    results = await asyncio.gather(*[one_call(i, ws_url, cookie, pcm, a.timeout) for i in range(a.calls)])
    ok = [r["response_s"] for r in results if r.get("ok")]
    summary = {"calls": a.calls, "succeeded": len(ok), "failed": a.calls - len(ok),
               "response_s_median": round(statistics.median(ok), 2) if ok else None, "response_s_max": max(ok) if ok else None}
    print(json.dumps({"summary": summary, "results": results}, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", default="admin@example.com")
    ap.add_argument("--password", default="ChangeMe!12345")
    ap.add_argument("--calls", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=120)
    asyncio.run(main(ap.parse_args()))
