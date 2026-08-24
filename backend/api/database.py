from __future__ import annotations

import os
import uuid
import json
import datetime
import asyncio
import pathlib
from typing import List, Dict, Any, Optional

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, deferred
from sqlalchemy import String, Integer, Text, LargeBinary, select, update, delete, ForeignKey, Float, UniqueConstraint, func

from api import config

# --- Database Setup ---
DATABASE_URL = "sqlite+aiosqlite:///./lexume.db"
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String)
    picture: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)

class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True) # Linked to User.id
    name: Mapped[Optional[str]] = mapped_column(String)
    type: Mapped[Optional[str]] = mapped_column(String)
    date: Mapped[str] = mapped_column(String)
    total_pages: Mapped[Optional[int]] = mapped_column(Integer)
    last_page: Mapped[Optional[int]] = mapped_column(Integer, default=1)
    last_audio_page: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    last_audio_position: Mapped[Optional[float]] = mapped_column(Float, default=None)
    audio_mode: Mapped[Optional[str]] = mapped_column(String, default="manual")

    extracted_text: Mapped[str] = mapped_column(Text)
    paragraphs: Mapped[str] = deferred(mapped_column(Text)) # JSON string
    audio_bytes: Mapped[Optional[bytes]] = deferred(mapped_column(LargeBinary))
    word_timings: Mapped[str] = deferred(mapped_column(Text)) # JSON string
    original_bytes: Mapped[Optional[bytes]] = deferred(mapped_column(LargeBinary))
    original_filename: Mapped[Optional[str]] = mapped_column(String)
    selected_pages: Mapped[Optional[str]] = mapped_column(Text) # JSON string of indices
    gemini_file_uri: Mapped[Optional[str]] = mapped_column(String)

    bookmarks = relationship("Bookmark", back_populates="session", cascade="all, delete-orphan")
    lookups = relationship("VocabularyLookup", back_populates="session", cascade="all, delete-orphan")
    pages = relationship("SessionPage", back_populates="session", cascade="all, delete-orphan")

class SessionPage(Base):
    __tablename__ = "session_pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[Optional[str]] = mapped_column(String)
    extracted_text: Mapped[str] = mapped_column(Text)
    paragraphs: Mapped[str] = deferred(mapped_column(Text)) # JSON string
    audio_bytes: Mapped[Optional[bytes]] = deferred(mapped_column(LargeBinary))
    word_timings: Mapped[str] = deferred(mapped_column(Text)) # JSON string
    page_images: Mapped[Optional[str]] = mapped_column(Text)  # JSON list of base64 PNG strings

    session = relationship("Session", back_populates="pages")
    __table_args__ = (UniqueConstraint("session_id", "page_number", name="uq_session_page"),)

class Bookmark(Base):
    __tablename__ = "bookmarks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    
    session = relationship("Session", back_populates="bookmarks")
    __table_args__ = (UniqueConstraint("session_id", "text", name="uq_bookmark_text"),)

class VocabularyLookup(Base):
    __tablename__ = "vocabulary_lookups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    word: Mapped[str] = mapped_column(String)
    definition: Mapped[Optional[str]] = mapped_column(Text)
    date: Mapped[str] = mapped_column(String, index=True)
    
    session = relationship("Session", back_populates="lookups")
    __table_args__ = (UniqueConstraint("session_id", "word", name="uq_lookup_word"),)

class UserPreference(Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    theme: Mapped[str] = mapped_column(String, default="dark")
    voice_id: Mapped[str] = mapped_column(String, default=config.ELEVENLABS_VOICE_ID)
    eleven_key: Mapped[str] = mapped_column(String, default="")
    gemini_key: Mapped[str] = mapped_column(String, default="")
    speed: Mapped[float] = mapped_column(Float, default=1.0)
    stability: Mapped[float] = mapped_column(Float, default=0.5)
    similarity_boost: Mapped[float] = mapped_column(Float, default=0.75)
    font_size: Mapped[str] = mapped_column(String, default="base")
    font_family: Mapped[str] = mapped_column(String, default="sans")
    target_language: Mapped[str] = mapped_column(String, default="Persian")
    translation_engine: Mapped[str] = mapped_column(String, default="google")
    google_drive_token: Mapped[Optional[str]] = mapped_column(Text)
    generate_audio: Mapped[int] = mapped_column(Integer, default=0)  # legacy, keep for now
    audio_mode: Mapped[str] = mapped_column(String, default="manual") # auto, manual, off
    credits: Mapped[float] = mapped_column(Float, default=config.CREDIT_STARTER_BALANCE)


class Subscription(Base):
    """
    Lexume Plus subscription state — one row per user. Populated/kept in sync
    by the Stripe and Google Play Billing webhook handlers in
    api/routes/billing.py; read (never trusted from the client) by
    api/subscription.py to gate the BYOK-or-Lexume's-own-keys fallback.
    """
    __tablename__ = "subscriptions"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    plan: Mapped[str] = mapped_column(String, default="monthly")  # "monthly" | "annual"
    status: Mapped[str] = mapped_column(String, default="inactive")  # "active" | "canceled" | "past_due" | "inactive"
    payment_platform: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # "stripe" | "play"
    platform_customer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # Stripe customer id
    platform_subscription_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # Stripe sub id / Play purchase token
    current_period_end: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # ISO timestamp
    period_started_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pages_narrated_this_period: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[float] = mapped_column(Float)           # positive = top-up, negative = spend
    reason: Mapped[str] = mapped_column(String)            # e.g. "page_extraction", "audio_generation", "admin_grant"
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    usd_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # real USD spent on APIs
    date: Mapped[str] = mapped_column(String, index=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe column migrations for existing databases
        migrations = [
            "ALTER TABLE session_pages ADD COLUMN page_images TEXT",
            "ALTER TABLE user_preferences ADD COLUMN generate_audio INTEGER DEFAULT 0",
            f"ALTER TABLE user_preferences ADD COLUMN credits REAL DEFAULT {config.CREDIT_STARTER_BALANCE}",
            "ALTER TABLE credit_transactions ADD COLUMN usd_cost REAL",
            "ALTER TABLE sessions ADD COLUMN user_id TEXT",
            "ALTER TABLE sessions ADD COLUMN last_page INTEGER DEFAULT 1",
            "ALTER TABLE user_preferences ADD COLUMN audio_mode TEXT DEFAULT 'manual'",
            "ALTER TABLE sessions ADD COLUMN audio_mode TEXT DEFAULT 'manual'",
            "ALTER TABLE sessions ADD COLUMN last_audio_page INTEGER DEFAULT NULL",
            "ALTER TABLE sessions ADD COLUMN last_audio_position REAL DEFAULT NULL",
            "CREATE INDEX IF NOT EXISTS ix_session_pages_session_id ON session_pages (session_id)",
            "CREATE INDEX IF NOT EXISTS ix_vocabulary_lookups_date ON vocabulary_lookups (date)",
            "CREATE INDEX IF NOT EXISTS ix_credit_transactions_date ON credit_transactions (date)",
            "CREATE INDEX IF NOT EXISTS ix_credit_transactions_session_id ON credit_transactions (session_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bookmark_text ON bookmarks (session_id, text)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_lookup_word ON vocabulary_lookups (session_id, word)",
        ]
        for sql in migrations:
            try:
                await conn.execute(__import__("sqlalchemy").text(sql))
            except Exception:
                pass  # Column already exists

# --- CRUD Functions ---

async def create_session(data: Dict[str, Any], user_id: Optional[str] = None) -> str:
    sid = str(uuid.uuid4())
    now = datetime.datetime.now().isoformat()
    
    original_filename = data.get('original_filename')
    if original_filename:
        name = original_filename
        stype = 'upload'
    else:
        paras = data.get('paragraphs', [])
        if paras and len(paras) > 0 and len(paras[0]) > 0:
            name = paras[0][:50] + ('...' if len(paras[0]) > 50 else '')
        else:
            name = 'Untitled Session'
        stype = 'paste'

    new_session = Session(
        id=sid,
        user_id=user_id,
        name=name,
        type=stype,
        date=now,
        extracted_text=data.get('extracted', ''),
        paragraphs=json.dumps(data.get('paragraphs', [])),
        audio_bytes=data.get('audio_bytes'),
        word_timings=json.dumps(data.get('word_timings', [])),
        original_bytes=data.get('original_bytes'),
        original_filename=data.get('original_filename'),
        total_pages=data.get('total_pages', 1),
        last_page=1,
        audio_mode=data.get('audio_mode', 'manual'),
        selected_pages=data.get('selected_pages'),
        gemini_file_uri=data.get('gemini_file_uri')
    )

    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(new_session)
            return sid

async def get_session(sid: str) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        # Explicit column selection to avoid loading large blobs if possible,
        # but here we need almost everything for the detail view EXCEPT potentially original_bytes
        # if the frontend doesn't use it.
        stmt = select(
            Session.id, Session.name, Session.type, Session.date,
            Session.extracted_text, Session.paragraphs, Session.word_timings,
            Session.original_filename, Session.total_pages, Session.last_page,
            Session.last_audio_page, Session.last_audio_position,
            Session.audio_mode, Session.selected_pages, Session.gemini_file_uri
            # Skipping original_bytes and audio_bytes for metadata fetch
        ).where(Session.id == sid)
        
        res = await session.execute(stmt)
        result = res.first()
        
        if not result:
            return None
        
        # Convert to dict and format as per original API
        data = {
            "id": result.id,
            "name": result.name,
            "type": result.type,
            "date": result.date,
            "extracted": result.extracted_text,
            "paragraphs": json.loads(result.paragraphs) if result.paragraphs else [],
            "word_timings": json.loads(result.word_timings) if result.word_timings else [],
            "original_filename": result.original_filename,
            "total_pages": result.total_pages or 1,
            "last_page": result.last_page or 1,
            "last_audio_page": result.last_audio_page,
            "last_audio_position": result.last_audio_position,
            "audio_mode": result.audio_mode or "manual",
            "selected_pages": json.loads(result.selected_pages) if result.selected_pages else None,
            "gemini_file_uri": result.gemini_file_uri
        }
        return data

async def get_session_audio_bytes(sid: str) -> Optional[bytes]:
    async with AsyncSessionLocal() as session:
        stmt = select(Session.audio_bytes).where(Session.id == sid)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

async def get_session_original_bytes(sid: str) -> Optional[bytes]:
    async with AsyncSessionLocal() as session:
        stmt = select(Session.original_bytes).where(Session.id == sid)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

async def get_session_page_audio_bytes(sid: str, page_number: int) -> Optional[bytes]:
    async with AsyncSessionLocal() as session:
        stmt = select(SessionPage.audio_bytes).where(SessionPage.session_id == sid, SessionPage.page_number == page_number)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

async def get_session_page(sid: str, page_number: int) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        # Explicit selection to avoid large binary blobs
        stmt = select(
            SessionPage.id,
            SessionPage.session_id,
            SessionPage.page_number,
            SessionPage.title,
            SessionPage.extracted_text,
            SessionPage.paragraphs,
            SessionPage.word_timings,
            SessionPage.page_images,
            # Check for existence of audio without loading bytes
            (SessionPage.audio_bytes != None).label("has_audio")
        ).where(SessionPage.session_id == sid, SessionPage.page_number == page_number).limit(1)
        
        result = await session.execute(stmt)
        page = result.first()
        if not page:
            return None
        return {
            "id": page.id,
            "session_id": page.session_id,
            "page_number": page.page_number,
            "title": page.title,
            "extracted": page.extracted_text,
            "paragraphs": json.loads(page.paragraphs) if page.paragraphs else [],
            "has_audio": bool(page.has_audio),
            "word_timings": json.loads(page.word_timings) if page.word_timings else [],
            "page_images": json.loads(page.page_images) if page.page_images else [],
        }

async def save_session_page(sid: str, page_number: int, data: Dict[str, Any]) -> int:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Check for existing to avoid duplicates if UniqueConstraint isn't enough/triggering
            stmt = select(SessionPage).where(SessionPage.session_id == sid, SessionPage.page_number == page_number)
            existing = (await session.execute(stmt)).scalars().first()
            images_json = json.dumps(data.get('page_images', []))
            if existing:
                existing.title = data.get('title')
                existing.extracted_text = data.get('extracted', '')
                existing.paragraphs = json.dumps(data.get('paragraphs', []))
                
                # Only update audio if provided
                new_audio = data.get('audio_bytes')
                if new_audio:
                    existing.audio_bytes = new_audio
                
                new_timings = data.get('word_timings')
                if new_timings:
                    existing.word_timings = json.dumps(new_timings)
                
                existing.page_images = images_json
                await session.flush()
                return existing.id

            new_page = SessionPage(
                session_id=sid,
                page_number=page_number,
                title=data.get('title'),
                extracted_text=data.get('extracted', ''),
                paragraphs=json.dumps(data.get('paragraphs', [])),
                audio_bytes=data.get('audio_bytes'),
                word_timings=json.dumps(data.get('word_timings', [])),
                page_images=images_json,
            )
            session.add(new_page)
            await session.flush()
            return new_page.id

async def update_session(sid: str, updates: Dict[str, Any]) -> bool:
    valid_keys = {
        'name', 'audio_bytes', 'word_timings', 'original_bytes',
        'original_filename', 'total_pages', 'selected_pages', 'extracted_text', 'paragraphs', 'last_page', 'audio_mode'
    }

    mapped_updates = {}
    for k, v in updates.items():
        if k in valid_keys:
            if k in ['word_timings', 'paragraphs', 'selected_pages'] and not isinstance(v, str):
                v = json.dumps(v)
            mapped_updates[k] = v
            
    if not mapped_updates:
        return False

    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = update(Session).where(Session.id == sid).values(**mapped_updates)
            result = await session.execute(stmt)
            return result.rowcount > 0

async def delete_session(sid: str) -> bool:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = delete(Session).where(Session.id == sid)
            result = await session.execute(stmt)
            return result.rowcount > 0

async def get_all_sessions_summary(
    user_id: Optional[str] = None, 
    limit: int = 12, 
    offset: int = 0, 
    search_query: str = ""
) -> Dict[str, Any]:
    async with AsyncSessionLocal() as db_session:
        # 1. Base query for sessions - SELECT ONLY NECESSARY COLUMNS
        # Avoiding audio_bytes and original_bytes which can be huge
        stmt = select(
            Session.id, 
            Session.name, 
            Session.type, 
            Session.date, 
            Session.total_pages, 
            func.substr(Session.extracted_text, 1, 200).label("extracted_preview")
        ).order_by(Session.date.desc())
        
        # 2. Filter by user_id
        if user_id:
            stmt = stmt.where(Session.user_id == user_id)
            
        # 3. Filter by search_query if provided
        if search_query:
            stmt = stmt.where(Session.name.ilike(f"%{search_query}%"))
            
        # 4. Get total count for pagination (before limit/offset)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await db_session.execute(count_stmt)).scalar() or 0
        
        # 5. Apply pagination
        stmt = stmt.limit(limit).offset(offset)
        
        result = await db_session.execute(stmt)
        sessions = result.all() # returns Row objects now
        
        if not sessions:
            return {"sessions": [], "total": total_count}

        session_ids = [s.id for s in sessions]

        # 6. Bulk fetch related data only for the current page's sessions
        b_stmt = select(Bookmark.session_id, Bookmark.text).where(Bookmark.session_id.in_(session_ids))
        b_rows = (await db_session.execute(b_stmt)).all()
        bookmarks_map = {}
        for sid, text in b_rows:
            bookmarks_map.setdefault(sid, []).append(text)

        l_stmt = select(VocabularyLookup.session_id, VocabularyLookup.word).where(VocabularyLookup.session_id.in_(session_ids))
        l_rows = (await db_session.execute(l_stmt)).all()
        lookups_map = {}
        for sid, word in l_rows:
            lookups_map.setdefault(sid, []).append({"word": word})

        p_stmt = select(SessionPage.session_id, func.count(SessionPage.id)).where(SessionPage.session_id.in_(session_ids)).group_by(SessionPage.session_id)
        p_rows = (await db_session.execute(p_stmt)).all()
        read_pages_map = {sid: count for sid, count in p_rows}

        # 7. Assemble final output
        output = []
        for s in sessions:
            output.append({
                "id": s.id,
                "name": s.name,
                "type": s.type,
                "date": s.date,
                "bookmarks": bookmarks_map.get(s.id, []),
                "lookups": lookups_map.get(s.id, []),
                "total_pages": s.total_pages or 1,
                "read_pages": read_pages_map.get(s.id, 0),
                "extracted": s.extracted_preview
            })
        return {"sessions": output, "total": total_count}

async def create_user(email: str, name: Optional[str], picture: Optional[str]) -> str:
    uid = str(uuid.uuid4())
    now = datetime.datetime.now().isoformat()
    new_user = User(id=uid, email=email, name=name, picture=picture, created_at=now)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(new_user)
            return uid

async def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user: return None
        return {"id": user.id, "email": user.email, "name": user.name, "picture": user.picture}

async def get_or_create_user(user_info: Dict[str, Any]) -> str:
    email = user_info.get("email")
    if not email:
        raise ValueError("User info must contain email")
    
    user = await get_user_by_email(email)
    if user:
        return user["id"]
    
    return await create_user(
        email=email,
        name=user_info.get("name"),
        picture=user_info.get("picture")
    )

async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.email == email)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user: return None
        return {"id": user.id, "email": user.email, "name": user.name, "picture": user.picture}

async def get_preferences(user_id: str) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        result = await session.get(UserPreference, user_id)
        if not result:
            # First time accessing preferences, create defaults
            try:
                async with session.begin():
                    result = UserPreference(user_id=user_id)
                    session.add(result)
                    # We continue after commit to use the freshly created object
            except __import__("sqlalchemy").exc.IntegrityError:
                # If another concurrent request created it first, just get it again
                result = await session.get(UserPreference, user_id)
        
        # If result is still none (shouldn't happen with the logic above)
        if not result:
            return {}

        return {
            "theme": result.theme,
            "voice_id": result.voice_id,
            "eleven_key": result.eleven_key,
            "gemini_key": result.gemini_key,
            "speed": result.speed,
            "stability": result.stability,
            "similarity_boost": result.similarity_boost,
            "font_size": result.font_size,
            "font_family": result.font_family,
            "target_language": result.target_language,
            "translation_engine": result.translation_engine,
            "google_drive_token": result.google_drive_token,
            "generate_audio": result.generate_audio,
            "credits": result.credits if result.credits is not None else config.CREDIT_STARTER_BALANCE,
        }

async def update_preferences(user_id: str, updates: Dict[str, Any]) -> bool:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = update(UserPreference).where(UserPreference.user_id == user_id).values(**updates)
            result = await session.execute(stmt)
            return result.rowcount > 0

async def get_session_bookmarks(session_id: str) -> list[str]:
    async with AsyncSessionLocal() as session:
        stmt = select(Bookmark.text).where(Bookmark.session_id == session_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

async def add_bookmark(session_id: str, text: str) -> int:
    async with AsyncSessionLocal() as session:
        try:
            async with session.begin():
                new_b = Bookmark(session_id=session_id, text=text)
                session.add(new_b)
                await session.flush()
                return new_b.id
        except __import__("sqlalchemy").exc.IntegrityError:
            return -1

async def remove_bookmark(session_id: str, text: str) -> bool:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = select(Bookmark).where(Bookmark.session_id == session_id, Bookmark.text == text).limit(1)
            result = await session.execute(stmt)
            bookmark = result.scalars().first()
            if bookmark:
                await session.delete(bookmark)
                return True
            return False

async def add_lookup(session_id: str, word: str, definition: Optional[str] = None) -> int:
    now = datetime.datetime.now().isoformat()
    async with AsyncSessionLocal() as session:
        try:
            async with session.begin():
                new_l = VocabularyLookup(session_id=session_id, word=word, definition=definition, date=now)
                session.add(new_l)
                await session.flush()
                return new_l.id
        except __import__("sqlalchemy").exc.IntegrityError:
            return -1


# --- Credit Functions ---

async def get_credits(user_id: str) -> float:
    """Return the current credit balance for a user."""
    async with AsyncSessionLocal() as session:
        stmt = select(UserPreference.credits).where(UserPreference.user_id == user_id)
        result = await session.execute(stmt)
        credits = result.scalar_one_or_none()
        return credits if credits is not None else config.CREDIT_STARTER_BALANCE


async def deduct_credits(
    user_id: str,
    amount: float,
    reason: str,
    session_id: Optional[str] = None,
    usd_cost: Optional[float] = None,
) -> float:
    """
    Deduct `amount` credits from the user and log the transaction.
    `usd_cost` is the real API spend in USD (for auditing).
    Raises ValueError if the balance is insufficient.
    Returns the new balance.
    """
    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.get(UserPreference, user_id)
            if not result:
                result = UserPreference(user_id=user_id)
                db.add(result)
                await db.flush()

            current = result.credits if result.credits is not None else config.CREDIT_STARTER_BALANCE
            if current < amount:
                raise ValueError(
                    f"Insufficient credits: {current:.1f} available, {amount:.1f} required"
                )

            result.credits = current - amount
            new_balance = result.credits

            now = datetime.datetime.now().isoformat()
            tx = CreditTransaction(
                user_id=user_id,
                amount=-amount,
                reason=reason,
                session_id=session_id,
                usd_cost=usd_cost,
                date=now,
            )
            db.add(tx)
            return new_balance


async def add_credits(user_id: str, amount: float, reason: str = "admin_grant") -> float:
    """
    Add `amount` credits to the user's balance and log the transaction.
    Creates the preference row if it doesn't exist yet.
    Returns the new balance.
    """
    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.get(UserPreference, user_id)
            if not result:
                result = UserPreference(user_id=user_id)
                db.add(result)
                await db.flush()

            current = result.credits if result.credits is not None else 0.0
            result.credits = current + amount
            new_balance = result.credits

            now = datetime.datetime.now().isoformat()
            tx = CreditTransaction(
                user_id=user_id,
                amount=amount,
                reason=reason,
                session_id=None,
                date=now,
            )
            db.add(tx)
            return new_balance


# --- Subscription functions ---

def _subscription_to_dict(sub: "Subscription") -> Dict[str, Any]:
    return {
        "user_id": sub.user_id,
        "plan": sub.plan,
        "status": sub.status,
        "payment_platform": sub.payment_platform,
        "platform_customer_id": sub.platform_customer_id,
        "platform_subscription_id": sub.platform_subscription_id,
        "current_period_end": sub.current_period_end,
        "period_started_at": sub.period_started_at,
        "pages_narrated_this_period": sub.pages_narrated_this_period,
        "updated_at": sub.updated_at,
    }


async def get_subscription(user_id: str) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        sub = await session.get(Subscription, user_id)
        return _subscription_to_dict(sub) if sub else None


async def get_subscription_by_platform_id(platform_subscription_id: str) -> Optional[Dict[str, Any]]:
    """Look up a subscription by its Stripe subscription id / Play purchase token — used by webhook handlers, which only carry the platform's own id."""
    async with AsyncSessionLocal() as session:
        stmt = select(Subscription).where(Subscription.platform_subscription_id == platform_subscription_id)
        result = await session.execute(stmt)
        sub = result.scalar_one_or_none()
        return _subscription_to_dict(sub) if sub else None


async def upsert_subscription(user_id: str, **fields: Any) -> Dict[str, Any]:
    """
    Create or update a user's subscription row. Only touches the columns
    passed in `fields`; always stamps `updated_at`. Used by both the Stripe
    and Play webhook/verify handlers so neither has to know whether a row
    already exists.
    """
    now = datetime.datetime.now().isoformat()
    async with AsyncSessionLocal() as session:
        async with session.begin():
            sub = await session.get(Subscription, user_id)
            if not sub:
                sub = Subscription(user_id=user_id)
                session.add(sub)
            for key, value in fields.items():
                setattr(sub, key, value)
            sub.updated_at = now
            await session.flush()
            return _subscription_to_dict(sub)


async def is_subscription_active(user_id: str) -> bool:
    """
    True iff status is "active" and, when a period end is recorded, it
    hasn't passed. The period-end check is a safety net for a missed
    cancellation webhook — status is the primary source of truth and is
    kept current by the Stripe/Play webhook handlers.
    """
    sub = await get_subscription(user_id)
    if not sub or sub["status"] != "active":
        return False
    period_end = sub.get("current_period_end")
    if period_end and period_end < datetime.datetime.now().isoformat():
        return False
    return True


async def increment_narration_usage(user_id: str) -> int:
    """Advance the current period's narrated-page counter by 1. Returns the new count."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            sub = await session.get(Subscription, user_id)
            if not sub:
                # Shouldn't happen — narration only reaches this after
                # is_subscription_active() passed, which requires a row —
                # but don't crash the narration response over bookkeeping.
                return 0
            sub.pages_narrated_this_period = (sub.pages_narrated_this_period or 0) + 1
            return sub.pages_narrated_this_period


async def start_new_billing_period(user_id: str, current_period_end: str) -> None:
    """Reset the narration counter for a new billing period (called from the Stripe/Play webhook handlers on renewal)."""
    now = datetime.datetime.now().isoformat()
    async with AsyncSessionLocal() as session:
        async with session.begin():
            sub = await session.get(Subscription, user_id)
            if not sub:
                sub = Subscription(user_id=user_id)
                session.add(sub)
            sub.current_period_end = current_period_end
            sub.period_started_at = now
            sub.pages_narrated_this_period = 0
            sub.updated_at = now


async def get_credit_history(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return the last `limit` credit transactions for the user, newest first."""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(CreditTransaction)
            .where(CreditTransaction.user_id == user_id)
            .order_by(CreditTransaction.date.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        txs = result.scalars().all()
        return [
            {
                "id": tx.id,
                "amount": tx.amount,
                "reason": tx.reason,
                "session_id": tx.session_id,
                "usd_cost": tx.usd_cost,
                "date": tx.date,
            }
            for tx in txs
        ]
