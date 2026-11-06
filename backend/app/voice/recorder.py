"""Two-channel call recorder (left = caller, right = agent), aligned by wall clock.
Only active when recording is enabled in agent settings (with spoken disclosure)."""
from __future__ import annotations

import time

import numpy as np
import soundfile as sf

from ..config import get_settings
from .audio import pcm16_to_float, resample

REC_RATE = 16000


class Recorder:
    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.t0 = time.monotonic()
        self._in: list[np.ndarray] = []
        self._out: list[tuple[float, np.ndarray]] = []

    def add_inbound(self, pcm16: bytes) -> None:
        self._in.append(pcm16_to_float(pcm16))

    def add_outbound(self, pcm16: bytes) -> None:
        self._out.append((time.monotonic() - self.t0, pcm16_to_float(pcm16)))

    def render(self) -> np.ndarray:
        caller = resample(np.concatenate(self._in) if self._in else np.zeros(0, np.float32), self.in_rate, REC_RATE)
        total = max(len(caller), int((time.monotonic() - self.t0) * REC_RATE))
        agent = np.zeros(total, dtype=np.float32)
        pos = 0
        for ts, frame in self._out:
            f = resample(frame, self.out_rate, REC_RATE)
            start = max(pos, int(ts * REC_RATE))
            end = min(start + len(f), total)
            if end <= start:
                continue
            agent[start:end] = f[: end - start]
            pos = end
        caller = np.pad(caller, (0, total - len(caller)))
        return np.stack([caller, agent], axis=1)

    def save(self, call_id: str) -> str:
        d = get_settings().recordings_dir
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{call_id}.wav"
        sf.write(path, self.render(), REC_RATE, subtype="PCM_16")
        return str(path)
