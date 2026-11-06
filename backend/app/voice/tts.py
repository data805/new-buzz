"""Text-to-speech. Kokoro-82M (Apache-2.0) is the default for naturalness;
Piper (GPL-3.0, optional) is a much lighter fallback for weak hardware."""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from ..config import get_settings
from .audio import apply_fade, resample

log = logging.getLogger(__name__)

OUTPUT_RATE = 24000  # all engines are normalised to 24 kHz mono float32

KOKORO_VOICES = {
    # curated subset that sounded most natural in our conversational tests (docs/VOICE_EVALUATION.md)
    "af_heart": "Heart - warm female, US accent (recommended)",
    "af_bella": "Bella - bright female, US accent",
    "af_sarah": "Sarah - calm female, US accent",
    "af_nicole": "Nicole - soft female, US accent",
    "bf_emma": "Emma - female, British accent",
    "bf_isabella": "Isabella - female, British accent",
    "am_michael": "Michael - male, US accent",
    "am_fenrir": "Fenrir - deep male, US accent",
    "bm_george": "George - male, British accent",
    "bm_fable": "Fable - male, British accent",
}

_TEXT_FIXES = [
    (re.compile(r"\bBongoHive\b", re.I), "Bongo Hive"),
    (re.compile(r"\bZICTA\b"), "Zickta"),
    (re.compile(r"\bK\s?(\d[\d,]*)\b"), r"\1 kwacha"),
    (re.compile(r"\+260\s?"), "plus two six zero, "),
    (re.compile(r"\bAI\b"), "A.I."),
    (re.compile(r"[*_#`>\[\]]"), ""),
    (re.compile(r"https?://\S+"), "our website"),
    (re.compile(r"\s+"), " "),
]


def normalise_for_speech(text: str) -> str:
    for pat, rep in _TEXT_FIXES:
        text = pat.sub(rep, text)
    return text.strip()


@dataclass
class SynthResult:
    audio: np.ndarray  # float32 @ OUTPUT_RATE
    elapsed_ms: float
    cached: bool = False


class TTSEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._kokoro = None
        self._piper: dict[str, object] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts")
        self._sem = asyncio.Semaphore(2)
        self._cache: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._cache_max = 256

    # ---------------------------------------------------------------- loading
    def load(self, engine: str | None = None):
        engine = engine or self.settings.tts_engine
        with self._lock:
            if engine == "kokoro" and self._kokoro is None:
                import onnxruntime as ort
                from kokoro_onnx import Kokoro

                t = time.perf_counter()
                so = ort.SessionOptions()
                so.intra_op_num_threads = self.settings.tts_threads
                so.inter_op_num_threads = 1
                providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
                sess = ort.InferenceSession(str(self.settings.model_path(self.settings.tts_kokoro_model)), so, providers=providers)
                self._kokoro = Kokoro.from_session(sess, str(self.settings.model_path(self.settings.tts_kokoro_voices)))
                log.info("Kokoro TTS loaded", extra={"ctx": {"s": round(time.perf_counter() - t, 1), "providers": providers}})
            if engine == "piper":
                self._get_piper(self.settings.tts_piper_voice)

    def _get_piper(self, voice: str):
        if voice not in self._piper:
            try:
                from piper import PiperVoice
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("Piper is not installed: pip install piper-tts") from e
            path = self.settings.model_path(voice if voice.endswith(".onnx") else f"piper/{voice}.onnx")
            self._piper[voice] = PiperVoice.load(str(path))
        return self._piper[voice]

    @property
    def loaded(self) -> bool:
        return self._kokoro is not None or bool(self._piper)

    def available_voices(self) -> dict[str, list[dict]]:
        out = {"kokoro": [{"id": k, "label": v} for k, v in KOKORO_VOICES.items()], "piper": []}
        piper_dir = self.settings.models_dir / "piper"
        if piper_dir.exists():
            out["piper"] = [{"id": p.stem, "label": p.stem} for p in sorted(piper_dir.glob("*.onnx"))]
        return out

    # ---------------------------------------------------------------- synthesis
    def synth_sync(self, text: str, engine: str, voice: str, speed: float = 1.0, lang: str = "en-us") -> SynthResult:
        key = (engine, voice, round(speed, 2), lang, text)
        if key in self._cache:
            self._cache.move_to_end(key)
            return SynthResult(self._cache[key], 0.0, cached=True)
        t = time.perf_counter()
        spoken = normalise_for_speech(text)
        if not spoken:
            return SynthResult(np.zeros(0, dtype=np.float32), 0.0)
        if engine == "piper":
            audio = self._synth_piper(spoken, voice, speed)
        else:
            self.load("kokoro")
            if lang == "en-us" and voice.startswith("b"):
                lang = "en-gb"
            samples, rate = self._kokoro.create(spoken, voice=voice, speed=speed, lang=lang)
            audio = resample(np.asarray(samples, dtype=np.float32), rate, OUTPUT_RATE)
        audio = apply_fade(audio, OUTPUT_RATE)
        self._cache[key] = audio
        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return SynthResult(audio, (time.perf_counter() - t) * 1000)

    def _synth_piper(self, text: str, voice: str, speed: float) -> np.ndarray:
        from piper import SynthesisConfig

        pv = self._get_piper(voice if voice and not voice.startswith(("af_", "am_", "bf_", "bm_")) else self.settings.tts_piper_voice)
        cfg = SynthesisConfig(length_scale=1.0 / max(speed, 0.1))
        chunks, rate = [], pv.config.sample_rate
        for ch in pv.synthesize(text, syn_config=cfg):
            chunks.append(ch.audio_float_array)
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        return resample(audio.astype(np.float32), rate, OUTPUT_RATE)

    async def synth(self, text: str, engine: str, voice: str, speed: float = 1.0, lang: str = "en-us") -> SynthResult:
        async with self._sem:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, self.synth_sync, text, engine, voice, speed, lang)


_engine: TTSEngine | None = None


def get_tts() -> TTSEngine:
    global _engine
    if _engine is None:
        _engine = TTSEngine()
    return _engine


_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+|\n+")


class SentenceChunker:
    """Splits a token stream into speakable chunks. Emits the first clause early
    (on a comma once long enough) to cut time-to-first-audio."""

    def __init__(self, first_min_chars: int = 24, min_chars: int = 40) -> None:
        self.buf = ""
        self.first = True
        self.first_min = first_min_chars
        self.min_chars = min_chars

    def feed(self, token: str) -> list[str]:
        self.buf += token
        out = []
        while True:
            m = _SENTENCE_END.search(self.buf)
            cut = None
            if m and m.start() >= (8 if self.first else 2):
                cut = m.end()
            elif self.first and len(self.buf) > self.first_min:
                i = self.buf.rfind(", ", self.first_min // 2)
                if i > 0:
                    cut = i + 2
            if cut is None and len(self.buf) > 220:  # very long run-on sentence
                i = self.buf.rfind(" ", 0, 200)
                cut = i + 1 if i > 0 else 200
            if cut is None:
                break
            piece, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if piece:
                out.append(piece)
                self.first = False
        return out

    def flush(self) -> list[str]:
        piece, self.buf = self.buf.strip(), ""
        return [piece] if piece else []
