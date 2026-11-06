"""Structured logging plus an in-memory ring buffer the dashboard can read."""
from __future__ import annotations

import collections
import json
import logging
import sys
import time

_RING: collections.deque[dict] = collections.deque(maxlen=2000)
_REDACT_KEYS = ("password", "secret", "token", "authorization", "api_key")


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _RING.append(_record_to_dict(record))
        except Exception:  # pragma: no cover
            pass


def _record_to_dict(record: logging.LogRecord) -> dict:
    data = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
        "level": record.levelname,
        "logger": record.name,
        "msg": record.getMessage(),
    }
    extra = getattr(record, "ctx", None)
    if isinstance(extra, dict):
        data.update({k: ("***" if any(r in k.lower() for r in _REDACT_KEYS) else v) for k, v in extra.items()})
    if record.exc_info:
        data["exc"] = logging.Formatter().formatException(record.exc_info)[-2000:]
    return data


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_record_to_dict(record), default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = _record_to_dict(record)
        extra = {k: v for k, v in d.items() if k not in ("ts", "level", "logger", "msg", "exc")}
        line = f"{d['ts']} {d['level']:<7} {d['logger']}: {d['msg']}"
        if extra:
            line += " " + " ".join(f"{k}={v}" for k, v in extra.items())
        if "exc" in d:
            line += "\n" + d["exc"]
        return line


def setup_logging(level: str = "INFO", as_json: bool = False) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter() if as_json else TextFormatter())
    root.addHandler(h)
    root.addHandler(RingHandler())
    for noisy in ("httpx", "httpcore", "uvicorn.access", "aiosqlite", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def recent_logs(level: str | None = None, limit: int = 300, q: str | None = None) -> list[dict]:
    order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    min_i = order.index(level.upper()) if level and level.upper() in order else 0
    out = [r for r in _RING if order.index(r["level"]) >= min_i and (not q or q.lower() in json.dumps(r).lower())]
    return out[-limit:][::-1]


def log_ctx(logger: logging.Logger, level: int, msg: str, **ctx) -> None:
    logger.log(level, msg, extra={"ctx": ctx})
