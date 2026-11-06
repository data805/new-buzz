from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..security import audit, require_role
from ..services.settings import AgentSettings, get_agent_settings, save_agent_settings
from ..voice.audio import wav_bytes
from ..voice.tts import OUTPUT_RATE, get_tts

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/settings")
async def get_settings_(_: User = Depends(require_role("viewer")), db: AsyncSession = Depends(get_db)):
    return {"settings": (await get_agent_settings(db)).model_dump(), "defaults": AgentSettings().model_dump()}


@router.put("/settings")
async def put_settings(body: dict, request: Request, user: User = Depends(require_role("manager")),
                       db: AsyncSession = Depends(get_db)):
    try:
        new = AgentSettings.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    old = (await get_agent_settings(db)).model_dump()
    if not new.ai_disclosure_required and user.role != "admin":
        raise HTTPException(403, "Only an admin may disable the AI disclosure")
    changed = [k for k, v in new.model_dump().items() if old.get(k) != v]
    await audit(db, "agent.settings_updated", "agent", user=user, details={"changed": changed}, request=request)
    await save_agent_settings(db, new, user.email)
    return {"settings": new.model_dump(), "changed": changed}


@router.get("/voices")
async def voices(_: User = Depends(require_role("viewer"))):
    return get_tts().available_voices()


class PreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    engine: str = "kokoro"
    voice: str = "af_heart"
    speed: float = Field(1.0, ge=0.6, le=1.5)


@router.post("/tts-preview")
async def tts_preview(body: PreviewIn, _: User = Depends(require_role("operator"))):
    try:
        res = await get_tts().synth(body.text, body.engine, body.voice, body.speed)
    except Exception as e:
        raise HTTPException(500, f"TTS failed: {e}") from e
    return Response(wav_bytes(res.audio, OUTPUT_RATE), media_type="audio/wav",
                    headers={"X-Synthesis-Ms": str(round(res.elapsed_ms)), "X-Audio-Seconds": str(round(len(res.audio) / OUTPUT_RATE, 2))})
