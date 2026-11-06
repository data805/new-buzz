"""Speech-to-text with faster-whisper (CTranslate2, MIT licence)."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from ..config import get_settings

log = logging.getLogger(__name__)

# Words Whisper commonly hallucinates on silence / noise.
_HALLUCINATIONS = {
    "", "thank you.", "thanks for watching!", "thank you for watching.", "you", ".", "bye.",
    "thanks for watching.", "please subscribe.", "so", "okay.",
}

# Domain words to bias recognition toward local names and terms.
DEFAULT_HOTWORDS = "BongoHive, Lusaka, Zambia, kwacha, Zamtel, Airtel, MTN, coworking, hot desk, AI Lab, Bemba, Nyanja"


@dataclass
class Transcript:
    text: str
    language: str
    avg_logprob: float
    no_speech_prob: float
    duration_s: float
    elapsed_ms: float

    @property
    def is_confident(self) -> bool:
        return self.avg_logprob > -1.0 and self.no_speech_prob < 0.6


class STTEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._model = None
        self._load_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt")
        self._sem = asyncio.Semaphore(2)
        self.model_name = self.settings.stt_model

    def load(self):
        with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                s = self.settings
                device = s.stt_device
                if device == "auto":
                    try:
                        import ctranslate2

                        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                    except Exception:
                        device = "cpu"
                compute = s.stt_compute_type if device == "cpu" else ("float16" if s.stt_compute_type == "int8" else s.stt_compute_type)
                t = time.perf_counter()
                self._model = WhisperModel(
                    s.stt_model, device=device, compute_type=compute, cpu_threads=s.stt_threads,
                    download_root=str(s.models_dir / "whisper"),
                )
                log.info("STT model loaded", extra={"ctx": {"model": s.stt_model, "device": device, "s": round(time.perf_counter() - t, 1)}})
            return self._model

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def transcribe_sync(self, audio: np.ndarray, language: str | None = "en", prompt: str | None = None) -> Transcript:
        model = self.load()
        t = time.perf_counter()
        multilingual = not self.model_name.endswith(".en")
        segments, info = model.transcribe(
            audio,
            language=language if multilingual else "en",
            beam_size=self.settings.stt_beam_size,
            vad_filter=False,  # we already segmented with our own VAD
            condition_on_previous_text=False,
            initial_prompt=prompt,
            hotwords=DEFAULT_HOTWORDS,
            without_timestamps=True,
            temperature=0.0,
        )
        segs = list(segments)
        text = " ".join(s.text.strip() for s in segs).strip()
        avg_lp = float(np.mean([s.avg_logprob for s in segs])) if segs else -5.0
        nsp = float(np.max([s.no_speech_prob for s in segs])) if segs else 1.0
        if text.lower() in _HALLUCINATIONS and nsp > 0.3:
            text = ""
        return Transcript(
            text=text, language=info.language, avg_logprob=avg_lp, no_speech_prob=nsp,
            duration_s=len(audio) / 16000, elapsed_ms=(time.perf_counter() - t) * 1000,
        )

    async def transcribe(self, audio: np.ndarray, language: str | None = "en", prompt: str | None = None) -> Transcript:
        async with self._sem:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, self.transcribe_sync, audio, language, prompt)


_engine: STTEngine | None = None


def get_stt() -> STTEngine:
    global _engine
    if _engine is None:
        _engine = STTEngine()
    return _engine
