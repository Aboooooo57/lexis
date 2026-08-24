from __future__ import annotations
import os
from api import config, subscription
from fastapi import APIRouter, HTTPException, Depends
from api.auth import get_current_user_id
from api import session as sess
from api import database
from api.models import KeyTermsResponse
from api.utils import fetch_word_definition, identify_key_terms, translate_text

router = APIRouter(tags=["dictionary"])

from pydantic import BaseModel

class TranslateRequest(BaseModel):
    text: str

@router.post("/dictionary/translate")
async def translate(req: TranslateRequest, user_id: str = Depends(get_current_user_id)) -> dict:
    prefs = await database.get_preferences(user_id)
    target_lang = prefs.get("target_language", "Persian")
    engine = prefs.get("translation_engine", "google")
    print(f"Translating using engine: {engine}")

    # This route has no user-supplied Gemini key field today (BYOK isn't
    # expressible here yet — a future enhancement, not a subscription-gap),
    # so the only way the real Gemini fallback can proceed is an active
    # Lexume Plus subscription. The free Google endpoint (the common case)
    # is untouched by this — resolve_gemini_key is only invoked if Gemini
    # actually ends up needed, see api/utils.py's translate_text.
    async def _resolve_gemini_key() -> str:
        return await subscription.resolve_extraction_key(None, user_id)

    translation = await translate_text(req.text, target_lang, engine=engine, resolve_gemini_key=_resolve_gemini_key)
    return {"translation": translation}

@router.get("/dictionary/{word}")
async def dictionary(word: str, user_id: str = Depends(get_current_user_id)) -> dict:
    entry = await fetch_word_definition(word)
    if not entry:
        raise HTTPException(404, f"No entry found for '{word}'.")
    return entry

@router.get("/key-terms", response_model=KeyTermsResponse)
async def key_terms(
    session_id: str,
    paragraph_index: int,
    gemini_key: str = "",
    gemini_model: str = config.DEFAULT_GEMINI_MODEL,
    mock_gemini: bool = False,
    user_id: str = Depends(get_current_user_id),
) -> KeyTermsResponse:
    session = await sess.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired.")

    paragraphs: list[str] = session.get("paragraphs", [])
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        raise HTTPException(400, "paragraph_index out of range.")

    if mock_gemini:
        return KeyTermsResponse(terms=["asyncio", "concurrent", "multiprocessing", "lightweight", "yield"])

    resolved_key = await subscription.resolve_extraction_key(gemini_key.strip() or None, user_id)

    try:
        terms = await identify_key_terms(
            paragraphs[paragraph_index],
            gemini_model=gemini_model,
            api_key=resolved_key,
        )
    except Exception as exc:
        raise HTTPException(500, f"Gemini error: {exc}") from exc

    return KeyTermsResponse(terms=terms)
