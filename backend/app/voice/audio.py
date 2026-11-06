"""PCM helpers. Internally everything is mono float32 in [-1, 1]; on the wire it
is 16-bit little-endian signed PCM."""
from __future__ import annotations

import io

import numpy as np
import soundfile as sf
import soxr


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    return soxr.resample(audio, src_rate, dst_rate, quality="HQ").astype(np.float32)


class StreamResampler:
    """Stateful resampler for streamed chunks (avoids clicks at chunk borders)."""

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        self.passthrough = src_rate == dst_rate
        self._rs = None if self.passthrough else soxr.ResampleStream(src_rate, dst_rate, 1, dtype="float32")

    def __call__(self, audio: np.ndarray, last: bool = False) -> np.ndarray:
        if self.passthrough:
            return audio
        return self._rs.resample_chunk(audio.astype(np.float32), last=last)


def rms_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    return 20 * np.log10(max(rms, 1e-6))


def wav_bytes(audio: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def load_audio_file(path_or_bytes, target_rate: int = 16000) -> np.ndarray:
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    audio, rate = sf.read(src, dtype="float32", always_2d=True)
    return resample(audio.mean(axis=1), rate, target_rate)


def apply_fade(audio: np.ndarray, rate: int, ms: float = 8.0) -> np.ndarray:
    n = min(int(rate * ms / 1000), audio.size // 2)
    if n <= 0:
        return audio
    audio = audio.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    audio[:n] *= ramp
    audio[-n:] *= ramp[::-1]
    return audio
