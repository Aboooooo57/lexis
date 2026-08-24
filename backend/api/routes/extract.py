from __future__ import annotations

import os
import re
import tempfile
import asyncio
import json
import httpx

from fastapi import APIRouter, Form, HTTPException, UploadFile, Depends, Request
from google import genai
from api.utils import _get_gemini_client
from api import config, subscription
from api.auth import get_current_user_id
from api.rate_limit import limiter

from api import session as sess
from api.models import ExtractResponse
from api.utils import (
    get_pdf_page_count,
    parse_page_ranges,
    extract_pdf_pages,
)

router = APIRouter()

@router.post("/extract", response_model=ExtractResponse)
@limiter.limit("20/minute")
async def extract(
    request: Request,
    file: UploadFile | None = None,
    text: str = Form(default=""),
    pages: str = Form(default=""),
    drive_file_id: str = Form(default=""),
    drive_file_name: str = Form(default=""),
    audio_mode: str = Form(default="manual"),
    gemini_model: str = Form(default=config.DEFAULT_GEMINI_MODEL),
    gemini_key: str = Form(default=""),
    user_id: str = Depends(get_current_user_id),
) -> ExtractResponse:
    if not file and not text.strip() and not drive_file_id:
        raise HTTPException(400, "Provide either a file, text, or Drive file ID.")

    total_pages = 1
    page_indices = None
    original_bytes = None
    original_filename = None
    tmp_path = None
    gemini_file_uri = None

    try:
        if drive_file_id:
            # 1. Direct Drive Fetch
            from api.routes.library import get_valid_drive_token
            access_token = await get_valid_drive_token(user_id)
            original_filename = drive_file_name or "Drive Document"
            suffix = "." + original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ".pdf"

            async with httpx.AsyncClient() as client:
                download_url = f"https://www.googleapis.com/drive/v3/files/{drive_file_id}?alt=media"
                file_resp = await client.get(download_url, headers={"Authorization": f"Bearer {access_token}"})
                if file_resp.status_code != 200:
                    raise HTTPException(400, "Failed to download file from Google Drive")
                original_bytes = file_resp.content

            def _write_tmp_drive():
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(original_bytes)
                    return tmp.name
            tmp_path = await asyncio.to_thread(_write_tmp_drive)

        elif file:
            suffix = "." + file.filename.rsplit(".", 1)[-1].lower()
            content = await file.read()
            original_bytes = content
            original_filename = file.filename
            
            def _write_tmp():
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(content)
                    return tmp.name
            tmp_path = await asyncio.to_thread(_write_tmp)

        if tmp_path and suffix == ".pdf":
            total = await get_pdf_page_count(tmp_path)
            if pages.strip():
                page_indices = parse_page_ranges(pages.strip(), total)
                total_pages = len(page_indices)
            else:
                page_indices = list(range(total))
                total_pages = total
            
            # Upload the subset PDF to Gemini File API — BYOK or an active
            # Lexume Plus subscription, same gate as per-page extraction
            # (api/routes/pages.py); raises 402 if neither is available.
            resolved_gemini_key = await subscription.resolve_extraction_key(gemini_key, user_id)
            subset_pdf_path = await extract_pdf_pages(tmp_path, page_indices)
            try:
                client = _get_gemini_client(resolved_gemini_key)
                uploaded_file = await client.aio.files.upload(file=subset_pdf_path)
                gemini_file_uri = uploaded_file.uri
            finally:
                await asyncio.to_thread(os.unlink, subset_pdf_path)

    except Exception as exc:
        raise HTTPException(500, f"File processing error: {exc}") from exc
    finally:
        if tmp_path:
            try:
                await asyncio.to_thread(os.unlink, tmp_path)
            except OSError:
                pass

    sid = await sess.create({
        "extracted": text.strip() if not file else "", 
        "paragraphs": [text.strip()] if not file and text.strip() else [],
        "audio_bytes": None, 
        "word_timings": [],
        "original_bytes": original_bytes,
        "original_filename": original_filename,
        "total_pages": total_pages,
        "audio_mode": audio_mode,
        "selected_pages": json.dumps(page_indices) if page_indices else None,
        "gemini_file_uri": gemini_file_uri
    }, user_id=user_id)
    
    return ExtractResponse(session_id=sid, extracted="", paragraphs=[])
