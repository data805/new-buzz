"""End-to-end voice call over the /ws/voice websocket, using recorded caller audio.

Plays each WAV as a caller would (real-time 32 ms frames followed by silence),
waits for the agent to finish replying, and prints the transcript plus
per-turn latency. Exit code 1 if any expectation fails.

Usage:
  python scripts/e2e_call.py --base http://localhost:8000 --email admin@example.com --password '...'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
import websockets

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "sample_data" / "audio"

# (clip, any-of keywords expected in the agent reply, forbidden keywords)
SCENARIO = [
    ("q_hotdesk_price", ["not published", "enquire", "pricing", "details", "don't have", "contact", "price"], ["kwacha per month", "K"]),
    ("q_location", ["ZEP-RE", "Zep", "Alick Nkhata", "Mass Media"], []),
    ("q_founder", ["don't have", "not sure", "do not have", "don't know", "unable", "no information", "call back", "callback"], []),
    ("q_ai_assessment", ["free", "no cost", "assessment"], []),
    ("q_callback", ["Mwila", "call", "tomorrow"], []),
    ("q_bye", [], []),
]


async def run(base: str, email: str, password: str, timeout: float) -> int:
    async with httpx.AsyncClient(base_url=base) as http:
        r = await http.post("/api/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        cookie = "; ".join(f"{k}={v}" for k, v in http.cookies.items())
    ws_url = base.replace("http", "ws", 1) + "/ws/voice?mode=test"
    failures = 0
    async with websockets.connect(ws_url, additional_headers={"Cookie": cookie}, max_size=None) as ws:
        state = {"value": "starting", "assistant": [], "events": [], "audio_s": 0.0, "ended": None}

        async def reader():
            async for msg in ws:
                if isinstance(msg, bytes):
                    state["audio_s"] += len(msg) / 2 / 24000
                    continue
                ev = json.loads(msg)
                state["events"].append(ev)
                if ev["type"] == "state":
                    state["value"] = ev["state"]
                elif ev["type"] == "assistant":
                    state["assistant"].append(ev["text"])
                elif ev["type"] == "transcript":
                    print(f"  CALLER (STT): {ev['text']}")
                elif ev["type"] == "metrics":
                    print(f"  latency: {ev['latency']}")
                elif ev["type"] == "sources":
                    print(f"  sources: {[s['heading'] for s in ev['items']]}")
                elif ev["type"] == "call_ended":
                    state["ended"] = ev
                    return

        rd = asyncio.create_task(reader())

        async def wait_listening(t0: float) -> bool:
            # wait until the agent has spoken and returned to listening
            spoke = False
            while time.monotonic() - t0 < timeout:
                if state["ended"]:
                    return True
                if state["value"] in ("speaking", "thinking"):
                    spoke = True
                if spoke and state["value"] == "listening":
                    return True
                await asyncio.sleep(0.05)
            return False

        print("== greeting")
        t0 = time.monotonic()
        if not await wait_listening(t0):
            print("  !! no greeting within timeout")
            return 1
        print(f"  AGENT: {' '.join(state['assistant'])}  ({time.monotonic() - t0:.1f}s)")

        silence = np.zeros(int(16000 * 1.2), dtype=np.int16).tobytes()
        for clip, expect, forbid in SCENARIO:
            if state["ended"]:
                break
            audio, sr = sf.read(AUDIO / f"{clip}.wav", dtype="int16")
            pcm = audio.tobytes() + silence
            print(f"== {clip}")
            state["assistant"] = []
            frame = 512 * 2
            for i in range(0, len(pcm), frame):
                await ws.send(pcm[i:i + frame])
                await asyncio.sleep(0.032)
            t0 = time.monotonic()
            ok = await wait_listening(t0)
            reply = " ".join(state["assistant"])
            print(f"  AGENT: {reply}  ({time.monotonic() - t0:.1f}s after caller stopped)")
            if not ok:
                print("  !! timeout waiting for reply")
                failures += 1
                continue
            low = reply.lower()
            if expect and not any(k.lower() in low for k in expect):
                print(f"  !! FAIL: expected one of {expect}")
                failures += 1
            bad = [k for k in forbid if (f" {k} " if len(k) == 1 else k.lower()) in (f" {reply} " if len(k) == 1 else low)]
            if bad:
                print(f"  !! FAIL: forbidden content {bad}")
                failures += 1
        if not state["ended"]:
            await ws.send(json.dumps({"type": "hangup"}))
        try:
            await asyncio.wait_for(rd, 30)
        except asyncio.TimeoutError:
            pass
        print(f"== ended: {state['ended'] and state['ended'].get('outcome')} metrics={state['ended'] and state['ended'].get('metrics')}")
        print(f"   agent audio received: {state['audio_s']:.1f}s, failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", default="admin@example.com")
    ap.add_argument("--password", default="ChangeMe!12345")
    ap.add_argument("--timeout", type=float, default=180)
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.base, a.email, a.password, a.timeout)))
