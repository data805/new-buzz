"""Download all local models into ./models (or $MODELS_DIR).

    python scripts/download_models.py               # Kokoro (fp32 + int8), Whisper small.en + base.en, embeddings
    python scripts/download_models.py --piper       # also Piper voices (optional, GPL engine)
    python scripts/download_models.py --whisper large-v3-turbo   # extra Whisper model (GPU)

The LLM is pulled separately:  ollama pull qwen2.5:3b-instruct
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = Path(os.environ.get("MODELS_DIR", ROOT / "models"))
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
PIPER_VOICES = ["en/en_GB/cori/high/en_GB-cori-high", "en/en_GB/alba/medium/en_GB-alba-medium", "en/en_US/lessac/medium/en_US-lessac-medium"]


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ✓ {dest.relative_to(MODELS)}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  ↓ {dest.relative_to(MODELS)}", flush=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--piper", action="store_true")
    ap.add_argument("--whisper", nargs="*", default=["small.en", "base.en"])
    a = ap.parse_args()
    print("Kokoro TTS")
    for f in ("kokoro-v1.0.onnx", "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        fetch(KOKORO + f, MODELS / "kokoro" / f)
    if a.piper:
        print("Piper voices")
        for v in PIPER_VOICES:
            name = v.rsplit("/", 1)[1]
            fetch(f"{PIPER}{v}.onnx", MODELS / "piper" / f"{name}.onnx")
            fetch(f"{PIPER}{v}.onnx.json", MODELS / "piper" / f"{name}.onnx.json")
    sys.path.insert(0, str(ROOT / "backend"))
    from faster_whisper import WhisperModel

    for m in a.whisper:
        print(f"Whisper {m}")
        WhisperModel(m, device="cpu", compute_type="int8", download_root=str(MODELS / "whisper"))
    print("Embeddings")
    from fastembed import TextEmbedding

    TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir=str(MODELS / "fastembed"))
    print("Done.")


if __name__ == "__main__":
    main()
