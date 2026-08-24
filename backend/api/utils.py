"""
PDF/Text → Gemini → ElevenLabs TTS pipeline.
Async implementation.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import tempfile
import asyncio
import base64
import random

import httpx
from dotenv import load_dotenv
from elevenlabs.client import AsyncElevenLabs
from elevenlabs import VoiceSettings
from google import genai
from google.genai import types
from pypdf import PdfReader, PdfWriter

from api import config


# ── PDF helpers ───────────────────────────────────────────────────────────────

def parse_page_ranges(page_str: str, total_pages: int) -> list[int]:
    """Parse '1-3,5,7-9' → sorted 0-based page indices."""
    pages: set[int] = set()
    for part in page_str.split(","):
        part = part.strip()
        if not part: continue
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                pages.update(range(int(start) - 1, int(end)))
            except ValueError: continue
        else:
            try:
                pages.add(int(part) - 1)
            except ValueError: continue
    return sorted(p for p in pages if 0 <= p < total_pages)


async def extract_pdf_pages(pdf_path: str, page_indices: list[int]) -> str:
    """Write selected pages to a temp PDF and return its path."""
    def _sync_extract():
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        for idx in page_indices:
            writer.add_page(reader.pages[idx])
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        with open(tmp.name, "wb") as f:
            writer.write(f)
        return tmp.name
    return await asyncio.to_thread(_sync_extract)


async def get_pdf_page_count(pdf_path: str) -> int:
    return await asyncio.to_thread(lambda: len(PdfReader(pdf_path).pages))


# ── Gemini ────────────────────────────────────────────────────────────────────

# Singleton client per API key — created once, reused for all requests with the same key
_gemini_clients: dict[str, genai.Client] = {}

def _get_gemini_client(api_key: str | None = None) -> genai.Client:
    """Return a shared Gemini client for the given key. Creates one on first call."""
    key = api_key or config.GEMINI_API_KEY
    if key not in _gemini_clients:
        _gemini_clients[key] = genai.Client(api_key=key)
    return _gemini_clients[key]


# Schema Gemini must follow for every extraction call
_EXTRACT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "title": types.Schema(
            type=types.Type.STRING,
            description="A short descriptive title for this page/passage (3–8 words). "
                        "Infer it from the content if not explicitly stated.",
        ),
        "text": types.Schema(
            type=types.Type.STRING,
            description="The full extracted text as clean readable prose. "
                        "No Markdown, no asterisks, no hashes. Preserve paragraph breaks.",
        ),
    },
    required=["title", "text"],
)

_EXTRACT_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=_EXTRACT_SCHEMA,
)

_EXTRACT_PROMPT = (
    "Extract the text from the provided content. "
    "Return a JSON object with exactly two fields:\n"
    "  - title: a short descriptive title (3–8 words) for this passage\n"
    "  - text: the full extracted text as clean readable prose "
    "(no Markdown, no asterisks, no hashes, preserve paragraph breaks)\n"
    "Do not add any commentary outside the JSON object."
)

_REFORMAT_PROMPT = (
    "Reformat the following text as clean readable prose and give it a short title.\n"
    "Return a JSON object with exactly two fields:\n"
    "  - title: a short descriptive title (3–8 words) for this passage\n"
    "  - text: the reformatted prose (no Markdown, no asterisks, preserve paragraph breaks)\n\n"
)


async def _call_gemini_with_retry(client, model, prompt: str, max_retries=5, api_key: str | None = None) -> str:
    """Call Gemini for plain-text output with exponential-backoff on 429s."""
    for attempt in range(max_retries):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2 + random.uniform(0, 1)
                print(f"Gemini 429. Retrying in {wait_time:.1f}s (attempt {attempt+1}/{max_retries})...")
                await asyncio.sleep(wait_time)
            else:
                raise e


async def _call_gemini_structured(
    client,
    model,
    contents,
    max_retries=5,
    api_key: str | None = None,
    _usage_out: list | None = None,
) -> dict:
    """
    Call Gemini with structured JSON output. Returns {"title": str, "text": str}.
    If `_usage_out` is provided (a list), appends a dict with token counts after a
    successful call: {"input_tokens": int, "output_tokens": int}.
    """
    for attempt in range(max_retries):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=_EXTRACT_CONFIG,
            )
            # Capture token usage for cost accounting if requested
            if _usage_out is not None:
                meta = getattr(response, "usage_metadata", None)
                if meta:
                    _usage_out.append({
                        "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
                        "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
                    })
            return json.loads(response.text)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2 + random.uniform(0, 1)
                print(f"Gemini 429. Retrying in {wait_time:.1f}s (attempt {attempt+1}/{max_retries})...")
                await asyncio.sleep(wait_time)
            else:
                raise e


async def extract_text_from_gemini(
    input_path: str | None,
    inline_text: str | None,
    page_indices: list[int] | None = None,
    gemini_model: str = config.DEFAULT_GEMINI_MODEL,
    api_key: str | None = None,
    file_uri: str | None = None,
    prompt_override: str | None = None,
    _usage_out: list | None = None,
) -> dict:
    """
    Extract/process text via Gemini using structured output.
    Returns {"title": str, "text": str}.
    Pass `_usage_out=[]` to receive token-count dicts appended to that list after
    each successful Gemini call: [{"input_tokens": int, "output_tokens": int}, ...]
    """
    client = _get_gemini_client(api_key)

    if inline_text:
        return await _call_gemini_structured(
            client, gemini_model,
            _REFORMAT_PROMPT + inline_text,
            api_key=api_key,
            _usage_out=_usage_out,
        )

    if file_uri:
        prompt = prompt_override or _EXTRACT_PROMPT
        if page_indices is not None:
            # Inform Gemini exactly which page(s) to look at in the multi-page file
            pages_str = ", ".join(str(p + 1) for p in page_indices)
            prompt = f"From the provided multi-page document, focus ONLY on page(s): {pages_str}.\n\n" + prompt

        return await _call_gemini_structured(
            client, gemini_model,
            [
                types.Part.from_uri(file_uri=file_uri, mime_type="application/pdf"),
                prompt,
            ],
            api_key=api_key,
            _usage_out=_usage_out,
        )

    path = pathlib.Path(input_path)
    if not await asyncio.to_thread(path.exists):
        raise FileNotFoundError(f"File not found: {input_path}")

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        upload_path = str(path)
        tmp_path = None
        if page_indices is not None:
            tmp_path = await extract_pdf_pages(str(path), page_indices)
            upload_path = tmp_path
        try:
            # File upload retry logic
            max_retries = 4
            for attempt in range(max_retries):
                try:
                    uploaded_file = await client.aio.files.upload(file=upload_path)
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < max_retries - 1:
                        wait_time = (2 ** attempt) * 5 + (random.uniform(0, 1) * 2)
                        print(f"Gemini File Upload 429. Retrying in {wait_time:.1f}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        raise e

            return await _call_gemini_structured(
                client, gemini_model,
                [
                    types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="application/pdf"),
                    _EXTRACT_PROMPT,
                ],
                api_key=api_key,
                _usage_out=_usage_out,
            )
        finally:
            if tmp_path:
                await asyncio.to_thread(os.unlink, tmp_path)

    elif suffix in (".txt", ".md"):
        def _read_file():
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        text = await asyncio.to_thread(_read_file)
        return await _call_gemini_structured(
            client, gemini_model,
            _REFORMAT_PROMPT + text,
            api_key=api_key,
            _usage_out=_usage_out,
        )

    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .pdf, .txt, or .md")


# ── Markdown → plain text (for TTS) ──────────────────────────────────────────

def strip_markdown(text: str) -> str:
    """Remove markdown syntax so ElevenLabs receives clean prose."""
    # Fenced code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # ATX headers  →  keep text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold / italic  (***x***, **x**, *x*, ___x___, __x__, _x_)
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", text)
    # Blockquotes
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Links  [text](url)  →  text
    text = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Unordered list markers
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    # Ordered list markers
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Collapse 3+ blank lines → 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── ElevenLabs ────────────────────────────────────────────────────────────────

async def generate_with_timestamps(
    text: str,
    voice_settings: dict | None = None,
    elevenlabs_model: str = config.DEFAULT_ELEVENLABS_MODEL,
    voice_id: str | None = None,
    api_key: str | None = None,
) -> tuple[bytes, list[dict]]:
    """
    Call ElevenLabs convert_with_timestamps asynchronously.
    Returns (audio_bytes, word_timings) where word_timings is a list of
    {"word": str, "start": float, "end": float}.
    """
    resolved_key = api_key or config.ELEVENLABS_API_KEY
    vid = voice_id or config.ELEVENLABS_VOICE_ID
    vs = voice_settings or {}

    client = AsyncElevenLabs(api_key=resolved_key)
    settings = VoiceSettings(
        stability=vs.get("stability", 0.5),
        similarity_boost=vs.get("similarity_boost", 0.75),
        style=vs.get("style", 0.0),
        use_speaker_boost=vs.get("use_speaker_boost", True),
        speed=vs.get("speed", 1.0),
    )

    response = await client.text_to_speech.convert_with_timestamps(
        voice_id=vid,
        text=text,
        model_id=elevenlabs_model,
        voice_settings=settings,
        output_format="mp3_44100_128",
    )

    audio_bytes = base64.b64decode(response.audio_base_64)

    alignment = response.alignment
    word_timings = _chars_to_words(
        alignment.characters,
        alignment.character_start_times_seconds,
        alignment.character_end_times_seconds,
    )
    return audio_bytes, word_timings


def _chars_to_words(
    characters: list[str],
    start_times: list[float],
    end_times: list[float],
) -> list[dict]:
    """
    Aggregate character-level ElevenLabs alignment into word-level dicts.
    Matches frontend's .split(/\s+/) logic by treating any sequence of 
    whitespace as a word boundary.
    """
    words: list[dict] = []
    if not characters:
        return words

    current_word_chars: list[str] = []
    current_start: float | None = None
    current_end: float = 0.0

    for char, t_start, t_end in zip(characters, start_times, end_times):
        is_whitespace = bool(re.match(r"\s", char))

        if is_whitespace:
            if current_word_chars:
                # Word ended, save it
                words.append({
                    "word": "".join(current_word_chars),
                    "start": current_start,
                    "end": current_end
                })
                current_word_chars = []
                current_start = None
            # Whitespace is ignored as a 'word' token
        else:
            if current_start is None:
                current_start = t_start
            current_word_chars.append(char)
            current_end = t_end

    # Handle last word
    if current_word_chars:
        words.append({
            "word": "".join(current_word_chars),
            "start": current_start,
            "end": current_end
        })

    return words


# ── PDF image extraction ──────────────────────────────────────────────────────

def extract_images_from_pdf_page(pdf_bytes: bytes, page_index: int, min_size: int = 100) -> list[str]:
    """
    Extract embedded images from a single PDF page.
    Returns a list of base64-encoded PNG strings.
    min_size: ignore images smaller than this many pixels in either dimension (icons/decorations).
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        return []

    result = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if page_index >= len(doc):
        return []

    page = doc[page_index]
    image_list = page.get_images(full=True)

    seen_xrefs = set()
    for img_info in image_list:
        xref = img_info[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)

        try:
            base_img = doc.extract_image(xref)
            w, h = base_img.get("width", 0), base_img.get("height", 0)
            if w < min_size or h < min_size:
                continue  # skip tiny icons/bullets

            img_bytes = base_img["image"]
            ext = base_img.get("ext", "png")

            # Convert to PNG via fitz pixmap for consistency
            pix = fitz.Pixmap(doc, xref)
            if pix.n > 4:  # CMYK → RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            png_bytes = pix.tobytes("png")
            result.append(base64.b64encode(png_bytes).decode("utf-8"))
        except Exception:
            continue

    doc.close()
    return result


async def extract_page_images(pdf_bytes: bytes, page_index: int) -> list[str]:
    """Async wrapper — runs pymupdf in a thread to avoid blocking the event loop."""
    return await asyncio.to_thread(extract_images_from_pdf_page, pdf_bytes, page_index)


# ── Dictionary helpers ────────────────────────────────────────────────────────

async def identify_key_terms(
    text: str,
    gemini_model: str = config.DEFAULT_GEMINI_MODEL,
    api_key: str | None = None,
    max_terms: int = 6,
) -> list[str]:
    """Ask Gemini to pick the most dictionary-worthy words from a paragraph."""
    client = _get_gemini_client(api_key)
    prompt = (
        f"From the text below, choose up to {max_terms} individual words that are "
        "most worth looking up in a dictionary — prefer technical terms, uncommon words, "
        "or words central to understanding the passage. "
        "Return ONLY a valid JSON array of lowercase single words. Example: [\"concurrent\",\"lightweight\"]\n\n"
        f"Text:\n{text}"
    )
    
    raw = await _call_gemini_with_retry(client, gemini_model, prompt, api_key=api_key)
    raw = raw.strip()
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()
    return json.loads(raw)


async def fetch_word_definition(word: str) -> dict | None:
    """Fetch a word entry from the Free Dictionary API asynchronously."""
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            r = await client.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word.lower()}")
            if r.status_code == 200:
                return r.json()[0]
        except Exception:
            pass
    return None


async def translate_text(
    text: str,
    target_language: str,
    api_key: str | None = None,
    engine: str = "google",
    resolve_gemini_key=None,
) -> str:
    """
    Translate word/phrase using the preferred engine (fast Google API or
    accurate Gemini). The free Google endpoint (engine="google") costs
    nothing and isn't gated; Gemini — used when explicitly requested, or as
    the fallback when the free endpoint fails — costs real money, so when
    the caller didn't already resolve a key (`api_key`), `resolve_gemini_key`
    (an async callable, typically api.subscription.resolve_extraction_key
    bound to the current user) is called lazily, only if Gemini actually
    ends up needed. This keeps the common free-path case ungated while still
    requiring BYOK-or-an-active-subscription for the real Gemini call.
    """
    if engine == "google":
        # Map friendly language names to ISO 639-1 codes
        lang_map = {
            "Persian": "fa", "Spanish": "es", "French": "fr", "German": "de",
            "Chinese": "zh-CN", "Japanese": "ja", "Russian": "ru", "Arabic": "ar",
            "Turkish": "tr", "Italian": "it"
        }
        
        lang_code = lang_map.get(target_language)
        
        # 1. Try Fast Translation API
        if lang_code:
            try:
                # Use POST to avoid URL length limits for long paragraphs
                async with httpx.AsyncClient(timeout=5) as http_client:
                    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl={lang_code}&dt=t"
                    r = await http_client.post(url, data={"q": text})
                    if r.status_code == 200:
                        data = r.json()
                        # Google translate returns a complex nested array. 
                        # The first element is an array of translated sentence chunks.
                        # data[0] = [ ["translated_sentence_1", "original_sentence_1", ...], ["translated_sentence_2", ...] ]
                        if data and isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                            # Combine all translated sentences
                            translated = "".join([sentence[0] for sentence in data[0] if isinstance(sentence, list) and sentence[0]])
                            if translated:
                                return translated.strip()
            except Exception as e:
                print(f"Fast translation failed: {e}. Falling back to Gemini.")

    # 2. Gemini Translation (either requested or fallback)
    if api_key is None and resolve_gemini_key is not None:
        api_key = await resolve_gemini_key()
    client = _get_gemini_client(api_key)
    prompt = (
        f"Translate the following English word or phrase to {target_language}. "
        "Return ONLY the translated text, no extra commentary or explanations.\n\n"
        f"Text: {text}"
    )
    
    translation = await _call_gemini_with_retry(client, config.DEFAULT_GEMINI_MODEL, prompt, api_key=api_key)
    return translation.strip()


# ── CLI entry point ───────────────────────────────────────────────────────────

async def run(
    input_path: str | None,
    inline_text: str | None,
    output_path: str,
    pages: str | None = None,
    gemini_model: str = config.DEFAULT_GEMINI_MODEL,
    elevenlabs_model: str = config.DEFAULT_ELEVENLABS_MODEL,
) -> None:
    page_indices = None
    if pages and input_path and input_path.lower().endswith(".pdf"):
        total = get_pdf_page_count(input_path)
        page_indices = parse_page_ranges(pages, total)
        print(f"Processing pages: {[p + 1 for p in page_indices]}")

    print(f"Step 1: Extracting text with {gemini_model}...")
    result = await extract_text_from_gemini(input_path, inline_text, page_indices, gemini_model)
    title = result.get("title", "")
    text = result.get("text", "")

    preview = text[:300].replace("\n", " ")
    print(f"\n--- Title: {title} ---")
    print(f"--- Extracted ({len(text)} chars) ---\n{preview}{'...' if len(text) > 300 else ''}\n---\n")

    print(f"Step 2: Generating audio with timestamps...")
    audio_bytes, _ = await generate_with_timestamps(text, elevenlabs_model=elevenlabs_model)
    
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
    print(f"\nSaved to {output_path} ({len(audio_bytes):,} bytes)")


def main() -> None:
    import sys

    parser = argparse.ArgumentParser(description="PDF/Text → Gemini → ElevenLabs TTS")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--file", "-f", help="Path to PDF, TXT, or MD file")
    group.add_argument("--text", "-t", help="Inline text to convert to speech")
    parser.add_argument("--pages", "-p", help="PDF pages to process, e.g. '1-3,5'")
    parser.add_argument("--output", "-o", default="output.mp3")
    parser.add_argument("--gemini-model", default=config.DEFAULT_GEMINI_MODEL)
    parser.add_argument("--elevenlabs-model", default=config.DEFAULT_ELEVENLABS_MODEL)
    args = parser.parse_args()

    if not args.file and not args.text:
        parser.print_help()
        sys.exit(0)

    asyncio.run(run(args.file, args.text, args.output, args.pages, args.gemini_model, args.elevenlabs_model))


if __name__ == "__main__":
    main()
