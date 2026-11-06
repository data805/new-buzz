"""Generate caller test utterances (16 kHz WAV) with Piper for end-to-end tests.

Usage: python scripts/make_test_audio.py [--voice en_GB-alba-medium]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import soundfile as sf  # noqa: E402

from app.voice.audio import resample  # noqa: E402
from app.voice.tts import OUTPUT_RATE, TTSEngine  # noqa: E402

UTTERANCES = {
    "q_hotdesk_price": "Hello. How much does a hot desk cost at Bongo Hive?",
    "q_location": "Where are you located?",
    "q_founder": "Who founded Bongo Hive?",
    "q_human": "Can I speak to a real person please?",
    "q_optout": "Please stop calling me. Don't call this number again.",
    "q_callback": "My name is Mwila Banda and my number is 0 9 7 1 2 3 4 5 6 7. Please call me back tomorrow morning about coworking.",
    "q_bye": "No, that's all. Thank you, bye.",
    "q_ai_assessment": "Does the A I lab offer a free assessment for my business?",
    "voicemail_greeting": "The subscriber you have called is not available. Please leave a message after the tone.",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="en_GB-alba-medium")
    ap.add_argument("--out", default=str(ROOT / "sample_data" / "audio"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tts = TTSEngine()
    for name, text in UTTERANCES.items():
        path = out / f"{name}.wav"
        if path.exists():
            continue
        res = tts.synth_sync(text.replace("Bongo Hive", "BongoHive"), "piper", args.voice)
        sf.write(path, resample(res.audio, OUTPUT_RATE, 16000), 16000, subtype="PCM_16")
        print(f"wrote {path.name} ({len(res.audio) / OUTPUT_RATE:.1f}s)", flush=True)
    (out / "transcripts.txt").write_text("\n".join(f"{k}\t{v}" for k, v in UTTERANCES.items()) + "\n")


if __name__ == "__main__":
    main()
