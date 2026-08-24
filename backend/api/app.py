from __future__ import annotations

import os
import pathlib

# Ensure the certifi CA bundle is used by httpx / ssl globally
# (fixes "Server disconnected" errors when calling Google APIs from Docker)
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.rate_limit import limiter
from api.routes.extract import router as extract_router
from api.routes.generate import router as generate_router
from api.routes.dictionary import router as dict_router
from api.routes.library import router as library_router
from api.routes.users import router as users_router
from api.routes.audio import router as audio_router
from api.routes.session import router as session_router
from api.routes.auth import router as auth_router
from api.routes.pages import router as pages_router
from api.routes.billing import router as billing_router

from contextlib import asynccontextmanager
from api.database import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB
    await init_db()
    yield

app = FastAPI(title="Lexume – English Reader & Translator", lifespan=lifespan)

# Rate limiting — none existed before the Lexume Plus subscription work;
# added specifically because a company-held key now sits behind these
# routes (BYOK requests hit the same limiter but only cost the caller their
# own API usage, so this is generous enough not to bother them). Per-IP
# since these routes also carry an auth dependency, not a substitute for it.
# The Limiter instance itself lives in api/rate_limit.py (see that module's
# docstring for why — avoids a circular import with the route modules that
# apply @limiter.limit(...)).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — set ALLOWED_ORIGINS in .env for production (comma-separated list)
_raw_origins = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
)
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes
app.include_router(extract_router, prefix="/api")
app.include_router(generate_router, prefix="/api")
app.include_router(dict_router, prefix="/api")
app.include_router(library_router, prefix="/api/library")
app.include_router(library_router, prefix="/api/auth/drive")
app.include_router(users_router, prefix="/api/users")
app.include_router(audio_router, prefix="/api")
app.include_router(session_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(pages_router, prefix="/api")
app.include_router(billing_router, prefix="/api")

