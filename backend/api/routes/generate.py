from __future__ import annotations

import os
import asyncio

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import Response
from api import config, database, subscription
from api.auth import get_current_user_id
from api.rate_limit import limiter

from api import session as sess
from api.models import GenerateRequest, GenerateResponse, WordTiming
from api.utils import (
    generate_with_timestamps,
)

router = APIRouter()


@router.post("/generate", response_model=GenerateResponse)
@limiter.limit("20/minute")
async def generate(request: Request, req: GenerateRequest, user_id: str = Depends(get_current_user_id)) -> GenerateResponse:
    session = await sess.get(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired.")

    extracted: str = session["extracted"]
    user_key = req.eleven_key.strip() or None
    resolved_vid = req.voice_id.strip() or config.ELEVENLABS_VOICE_ID

    # Same BYOK-or-active-Lexume-Plus-subscription gate as the per-page
    # narration flow (api/routes/pages.py) — this older whole-session
    # endpoint has no existing credit-topup UX, so an over-quota subscriber
    # is pointed at the dedicated per-page flow or a top-up instead of
    # silently spending credits here.
    access = await subscription.check_narration_access(user_key, user_id)
    if access == "unauthorized":
        raise HTTPException(402, "ElevenLabs API key is required, or an active Lexume Plus subscription.")
    if access == "subscription_over_quota":
        raise HTTPException(
            402,
            f"Lexume Plus narration quota reached ({config.SUBSCRIPTION_PAGES_QUOTA} pages this "
            f"billing period). Add your own ElevenLabs key, or buy a credit top-up from your account.",
        )

    if access == "byok":
        resolved_key = user_key
        resolved_model = req.eleven_model
    else:
        resolved_key = config.ELEVENLABS_API_KEY
        resolved_model = subscription.SUBSCRIPTION_ELEVENLABS_MODEL

    voice_settings = {
        "stability": req.stability,
        "similarity_boost": req.similarity_boost,
        "speed": req.speed,
        "style": req.style,
        "use_speaker_boost": True,
    }

    try:
        audio_bytes, word_timings = await generate_with_timestamps(
            text=extracted,
            voice_settings=voice_settings,
            elevenlabs_model=resolved_model,
            voice_id=resolved_vid,
            api_key=resolved_key,
        )
    except Exception as exc:
        raise HTTPException(500, f"ElevenLabs error: {exc}") from exc

    if access == "subscription":
        await database.increment_narration_usage(user_id)

    await sess.update(req.session_id, {"audio_bytes": audio_bytes, "word_timings": word_timings})

    return GenerateResponse(
        session_id=req.session_id,
        word_timings=[WordTiming(**w) for w in word_timings],
    )



