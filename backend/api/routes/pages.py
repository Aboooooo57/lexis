from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request
from typing import Optional, Dict, Any
from api import database, config, subscription
from api.auth import get_current_user_id
from api.models import GenerateRequest, PageResponse
from api.utils import extract_text_from_gemini, generate_with_timestamps, extract_page_images
from api.rate_limit import limiter
import asyncio
import math
import os
import re

router = APIRouter(tags=["pages"])

# Global dictionary to track locks per page to prevent concurrent processing/double-charging
_page_locks: Dict[str, asyncio.Lock] = {}

def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n{2,}", text)
    return [p.strip() for p in parts if p.strip()]


def _compute_gemini_usd(usage_out: list) -> float:
    """Sum USD cost from collected Gemini usage dicts."""
    total = 0.0
    for u in usage_out:
        inp = u.get("input_tokens", 0) or 0
        out = u.get("output_tokens", 0) or 0
        total += (
            inp * config.GEMINI_INPUT_PRICE_PER_M_TOKENS / 1_000_000
            + out * config.GEMINI_OUTPUT_PRICE_PER_M_TOKENS / 1_000_000
        )
    return total


def _compute_elevenlabs_usd(char_count: int) -> float:
    return (char_count / 1000) * config.ELEVENLABS_PRICE_PER_K_CHARS


def _compute_audio_credits(char_count: int) -> float:
    """
    Dynamic audio credits based on actual character count.
    Formula: CREDIT_COST_AUDIO_PER_K_CHARS credits per 1 000 chars, rounded up,
    with a minimum floor of CREDIT_COST_AUDIO_MIN.
    """
    if char_count == 0:
        return 0.0
    raw = char_count / 1000.0 * config.CREDIT_COST_AUDIO_PER_K_CHARS
    return float(max(config.CREDIT_COST_AUDIO_MIN, math.ceil(raw)))


@router.get("/session/{session_id}/page/{page_number}", response_model=PageResponse)
@limiter.limit("30/minute")
async def get_page(
    request: Request,
    session_id: str,
    page_number: int,
    background_tasks: BackgroundTasks,
    gemini_key: Optional[str] = None,
    eleven_key: Optional[str] = None,
    generate_audio: bool = True,
    user_id: str = Depends(get_current_user_id)
) -> PageResponse:
    """
    Fetches a specific page of a session.  If the page hasn't been processed yet,
    Gemini extracts the text (Stage 1) and ElevenLabs generates audio (Stage 2).

    Access for each stage (see api/subscription.py for the actual gating):
      • Stage 1 (extraction) – BYOK (gemini_key) or an active Lexume Plus
        subscription; free/unmetered either way, cheap enough not to quota.
      • Stage 2 (narration) – BYOK (eleven_key), included in an active
        subscription's monthly page quota, or — once that quota is used up
        for the period — the pre-existing credit-based top-up (dynamic
        audio credit, 4 credits / 1 000 chars, min 2).

    Every stage still logs the real USD cost of the underlying API call for
    operator visibility, even when it isn't billed against the user's credit
    balance. If the page returns without audio (generate_audio=False or
    audio already exists), the `audio_credits` field carries the dynamic
    top-up estimate so the client can surface an accurate cost before the
    user decides to generate.
    """
    lock_key = f"{session_id}:{page_number}"
    if lock_key not in _page_locks:
        _page_locks[lock_key] = asyncio.Lock()

    async with _page_locks[lock_key]:
        # ── 1. Cache hit — return immediately ───────────────────────────────
        page_data = await database.get_session_page(session_id, page_number)

        has_text  = bool(page_data and page_data.get("extracted"))
        has_audio = bool(page_data and page_data.get("has_audio"))

        if page_data and (not generate_audio or has_audio):
            cached_text = page_data.get("extracted", "")
            return PageResponse(
                session_id=session_id,
                page_number=page_number,
                title=page_data.get("title"),
                extracted=cached_text,
                paragraphs=page_data.get("paragraphs", []),
                word_timings=page_data.get("word_timings", []),
                page_images=page_data.get("page_images", []),
                # Provide cost estimate so frontend can show it before user clicks Generate
                audio_credits=_compute_audio_credits(len(cached_text)) if not has_audio else None,
            )

        needs_extraction = not has_text
        needs_audio      = generate_audio and not has_audio

        # ── 2. Stage 1 gate: BYOK or an active Lexume Plus subscription ──────
        # Extraction is cheap (~$0.004/page) so it isn't quota-metered for
        # subscribers, just gated on access — see api/subscription.py. Raises
        # 402 if neither a key nor an active subscription is available.
        resolved_gemini_key = ""
        if needs_extraction:
            resolved_gemini_key = await subscription.resolve_extraction_key(gemini_key, user_id)

        # ── 3. Fetch session meta ────────────────────────────────────────────
        session = await database.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if page_number < 1 or page_number > session.get('total_pages', 1):
            raise HTTPException(status_code=400, detail="Invalid page number")

        original_bytes = None
        if needs_extraction:
            original_bytes = await database.get_session_original_bytes(session_id)

        gemini_file_uri = session.get('gemini_file_uri')
        selected_pages  = session.get('selected_pages')
        actual_page_idx = selected_pages[page_number - 1] if selected_pages else page_number - 1

        extracted_text = page_data.get("extracted", "") if page_data else ""
        page_title     = page_data.get("title", "")     if page_data else ""
        word_timings   = page_data.get("word_timings", []) if page_data else []
        page_images    = page_data.get("page_images", [])  if page_data else []
        audio_bytes    = b""

        gemini_usage: list = []

        # ── 4. Extract text (Stage 1) ────────────────────────────────────────
        if needs_extraction:
            if gemini_file_uri:
                try:
                    result = await extract_text_from_gemini(
                        input_path=None,
                        inline_text=None,
                        page_indices=[page_number - 1],
                        gemini_model=config.DEFAULT_GEMINI_MODEL,
                        api_key=resolved_gemini_key,
                        file_uri=gemini_file_uri,
                        _usage_out=gemini_usage,
                    )
                    page_title     = result.get("title", "Lesson Page").strip()
                    extracted_text = result.get("text", "").strip()
                except Exception as e:
                    print(f"ERROR: PDF extraction failed for session {session_id} page {page_number}: {e}")
                    raise HTTPException(status_code=500, detail=f"Failed to process PDF page: {e}")

            elif original_bytes:
                import tempfile
                suffix = "." + session.get('original_filename', 'doc.pdf').rsplit(".", 1)[-1].lower()
                tmp_path = None
                try:
                    def _write_tmp():
                        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                            tmp.write(original_bytes)
                            return tmp.name
                    tmp_path = await asyncio.to_thread(_write_tmp)

                    result = await extract_text_from_gemini(
                        input_path=tmp_path,
                        inline_text=None,
                        page_indices=[actual_page_idx],
                        gemini_model=config.DEFAULT_GEMINI_MODEL,
                        api_key=resolved_gemini_key,
                        _usage_out=gemini_usage,
                    )
                    page_title     = result.get("title", "Lesson Page").strip()
                    extracted_text = result.get("text", "").strip()
                except Exception as e:
                    print(f"ERROR: Fallback PDF extraction failed: {e}")
                    raise HTTPException(status_code=500, detail="Failed to process PDF page")
                finally:
                    if tmp_path:
                        try: await asyncio.to_thread(os.unlink, tmp_path)
                        except: pass
            else:
                if page_number == 1:
                    extracted_text = session.get('extracted', '')
                    page_title = "Lesson Overview"
                else:
                    raise HTTPException(status_code=400, detail="Text sessions only have 1 page")

            # Extract images only on first extraction
            if original_bytes:
                try:
                    page_images = await extract_page_images(original_bytes, actual_page_idx)
                except Exception as e:
                    print(f"WARN: Image extraction failed: {e}")

            # Extraction is free/unmetered on both the BYOK and
            # included-in-subscription paths (see api/subscription.py) — log
            # the real USD cost for operator visibility without touching the
            # credit balance (amount=0).
            gemini_usd = _compute_gemini_usd(gemini_usage)
            await database.deduct_credits(
                user_id,
                0.0,
                reason="page_extraction" if gemini_key else "page_extraction_subscription",
                session_id=session_id,
                usd_cost=round(gemini_usd, 6),
            )

        # ── 5. Stage 2 gate: BYOK, subscription quota, or a credit top-up ────
        paragraphs = _split_paragraphs(extracted_text) if extracted_text.strip() else []
        audio_credits = 0.0
        narration_access: Optional[str] = None
        resolved_eleven_key = ""
        eleven_model = config.DEFAULT_ELEVENLABS_MODEL

        if needs_audio and extracted_text.strip():
            narration_access = await subscription.check_narration_access(eleven_key, user_id)

            if narration_access == "unauthorized":
                raise HTTPException(
                    status_code=402,
                    detail="No ElevenLabs key provided and no active Lexume Plus subscription.",
                )

            if narration_access == "subscription_over_quota":
                # Quota used up for this billing period — fall back to the
                # existing credit-based top-up (still uses Lexume's own key).
                audio_credits = _compute_audio_credits(len(extracted_text))
                balance = await database.get_credits(user_id)
                if balance < audio_credits:
                    # Text extraction succeeded — save it so that work isn't wasted
                    await database.save_session_page(session_id, page_number, {
                        "title":      page_title,
                        "extracted":  extracted_text,
                        "paragraphs": paragraphs,
                        "audio_bytes":  b"",
                        "word_timings": [],
                        "page_images":  page_images,
                    })
                    raise HTTPException(
                        status_code=402,
                        detail=(
                            f"Lexume Plus narration quota reached for this billing period, and your "
                            f"credit balance ({balance:.1f}) doesn't cover the top-up cost "
                            f"({audio_credits:.1f} credits for {len(extracted_text):,} characters). "
                            f"The extracted text has been saved — you can generate audio later, or "
                            f"add your own ElevenLabs key."
                        ),
                    )

            resolved_eleven_key, eleven_model = subscription.narration_credentials(narration_access, eleven_key)

        # ── 6. Generate audio (Stage 2) ──────────────────────────────────────
        elevenlabs_chars = 0
        if needs_audio and extracted_text.strip():
            try:
                elevenlabs_chars = len(extracted_text)
                audio_bytes, word_timings = await generate_with_timestamps(
                    text=extracted_text,
                    voice_settings=None,
                    elevenlabs_model=eleven_model,
                    voice_id=config.ELEVENLABS_VOICE_ID,
                    api_key=resolved_eleven_key,
                )
            except Exception as e:
                print(f"ERROR: Audio generation failed: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to generate audio: {str(e)}")

            # Bill the call: a credit-ledger charge only for the
            # over-quota-topup case; an included-in-subscription call
            # advances the quota counter instead, and BYOK is always free.
            # All three still log the real USD cost for operator visibility.
            elevenlabs_usd = _compute_elevenlabs_usd(elevenlabs_chars)
            if narration_access == "subscription_over_quota":
                await database.deduct_credits(
                    user_id,
                    audio_credits,
                    reason="audio_generation_topup",
                    session_id=session_id,
                    usd_cost=round(elevenlabs_usd, 6),
                )
            else:
                if narration_access == "subscription":
                    await database.increment_narration_usage(user_id)
                await database.deduct_credits(
                    user_id,
                    0.0,
                    reason=f"audio_generation_{narration_access}",
                    session_id=session_id,
                    usd_cost=round(elevenlabs_usd, 6),
                )

        # ── 7. Persist processed page ────────────────────────────────────────
        await database.save_session_page(session_id, page_number, {
            "title":        page_title,
            "extracted":    extracted_text,
            "paragraphs":   paragraphs,
            "audio_bytes":  audio_bytes,
            "word_timings": word_timings,
            "page_images":  page_images,
        })

        return PageResponse(
            session_id=session_id,
            page_number=page_number,
            title=page_title,
            extracted=extracted_text,
            paragraphs=paragraphs,
            word_timings=word_timings,
            page_images=page_images,
            # If audio was just generated, no estimate needed; otherwise return the cost
            audio_credits=None if (needs_audio and audio_bytes) else _compute_audio_credits(len(extracted_text)),
        )
