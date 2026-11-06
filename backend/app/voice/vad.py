"""Streaming voice-activity detection using the Silero VAD v6 ONNX model that
ships with faster-whisper (MIT). Runs on onnxruntime, no torch required."""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import onnxruntime

CHUNK = 512  # samples at 16 kHz = 32 ms
CONTEXT = 64
RATE = 16000

_session: onnxruntime.InferenceSession | None = None
_lock = threading.Lock()


def _model_path() -> Path:
    from faster_whisper.utils import get_assets_path

    return Path(get_assets_path()) / "silero_vad_v6.onnx"


def get_session() -> onnxruntime.InferenceSession:
    global _session
    with _lock:
        if _session is None:
            opts = onnxruntime.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.log_severity_level = 4
            _session = onnxruntime.InferenceSession(
                str(_model_path()), providers=["CPUExecutionProvider"], sess_options=opts
            )
        return _session


class StreamingVAD:
    """Feed arbitrary-length 16 kHz float32 audio; get one speech probability
    per 32 ms chunk. Keeps the recurrent state between calls."""

    def __init__(self) -> None:
        self.session = get_session()
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def process(self, audio: np.ndarray) -> list[tuple[np.ndarray, float]]:
        """Return a list of (chunk, probability) for each complete 512-sample chunk."""
        buf = np.concatenate([self._pending, audio.astype(np.float32)])
        out = []
        n = len(buf) // CHUNK
        for i in range(n):
            chunk = buf[i * CHUNK:(i + 1) * CHUNK]
            x = np.concatenate([self._context, chunk])[None, :]
            prob, self._h, self._c = self.session.run(None, {"input": x, "h": self._h, "c": self._c})
            self._context = chunk[-CONTEXT:]
            out.append((chunk, float(np.asarray(prob).reshape(-1)[0])))
        self._pending = buf[n * CHUNK:]
        return out
