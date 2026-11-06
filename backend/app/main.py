from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import agent, auth, calls, campaigns, knowledge, system, telephony, users, workflows, ws
from .config import get_settings
from .db import SessionLocal, create_all
from .logging_setup import setup_logging

log = logging.getLogger("app")


async def _preload_models() -> None:
    """Load STT/TTS/embeddings and warm the LLM so the first caller doesn't wait."""
    from .knowledge.store import embedder
    from .llm.client import get_llm
    from .voice.stt import get_stt
    from .voice.tts import get_tts
    from .voice.vad import get_session

    s = get_settings()
    for name, fn in (("vad", get_session), ("embeddings", embedder.load), ("stt", get_stt().load),
                     ("tts", lambda: get_tts().load(s.tts_engine))):
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.exception("Model preload failed", extra={"ctx": {"model": name}})
    try:
        await asyncio.to_thread(get_tts().synth_sync, "Hello.", s.tts_engine, "af_heart" if s.tts_engine == "kokoro" else "", 1.0)
    except Exception:
        log.warning("TTS warm-up failed", exc_info=True)
    await get_llm().warmup()
    log.info("Models ready")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level, s.log_json)
    if s.is_production and (problems := s.validate_for_production()):
        for p in problems:
            log.critical("Refusing to start: %s", p)
        sys.exit(2)
    for d in (s.data_dir, s.recordings_dir, s.uploads_dir):
        d.mkdir(parents=True, exist_ok=True)
    if s.database_url.startswith("sqlite"):
        await create_all()  # Postgres deployments run `alembic upgrade head` instead

    from .knowledge.store import index
    from .outbound.dialer import dialer
    from .services.maintenance import bootstrap, retention_loop
    from .telephony.base import get_provider
    from .workflows.engine import engine

    engine.start()
    await bootstrap()
    async with SessionLocal() as db:
        await index.rebuild(db)
    provider = get_provider()
    await provider.start()
    dialer.start()
    bg = [asyncio.create_task(retention_loop())]
    if s.preload_models:
        bg.append(asyncio.create_task(_preload_models()))
    log.info("Started", extra={"ctx": {"env": s.environment, "telephony": provider.name, "llm": f"{s.llm_provider}:{s.llm_model}",
                                       "outbound_enabled": s.outbound_enabled}})
    yield
    for t in bg:
        t.cancel()
    await dialer.stop()
    await provider.stop()
    from .llm.client import get_llm

    await get_llm().close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "microphone=(self), camera=()")
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "media-src 'self' blob:; connect-src 'self' ws: wss:; worker-src 'self' blob:; frame-ancestors 'none'",
            )
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error", extra={"ctx": {"path": request.url.path}})
        return JSONResponse({"detail": "Internal server error. Check System > Logs for details."}, status_code=500)

    for r in (auth.router, users.router, calls.router, knowledge.router, agent.router, campaigns.router,
              workflows.router, system.router, telephony.router, ws.router):
        app.include_router(r)

    dist = s.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith(("api/", "ws/")):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    return app


app = create_app()
