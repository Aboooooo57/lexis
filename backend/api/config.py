import os
import pathlib
from dotenv import load_dotenv

# Load .env file
load_dotenv()

BASE_DIR = pathlib.Path(__file__).parent.parent

# --- Google OAuth2 & JWT ---
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
GOOGLE_DRIVE_REDIRECT_URI = os.environ.get("GOOGLE_DRIVE_REDIRECT_URI", "http://localhost:8000/api/auth/drive/callback")

# --- Security & OWASP ---
JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-key-change-me")
# Encryption secret for sensitive tokens (Must be 32 bytes for Fernet)
ENCRYPTION_SECRET = os.environ.get("ENCRYPTION_SECRET", "8jU6eE8vD5mN2pX1qW9zY0rV3bB7nN4m1lK2jH3gG4f=")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# --- Database ---
DB_URL = f"sqlite+aiosqlite:///{BASE_DIR}/lexume.db"

# --- Gemini API ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"

# --- ElevenLabs API ---
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"

# --- Credits ---
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "lexume-admin-secret")
CREDIT_STARTER_BALANCE = float(os.environ.get("CREDIT_STARTER_BALANCE", "20.0"))
CREDIT_COST_EXTRACTION = float(os.environ.get("CREDIT_COST_EXTRACTION", "1.0"))       # per page (Gemini)
CREDIT_COST_AUDIO_PER_K_CHARS = float(os.environ.get("CREDIT_COST_AUDIO_PER_K_CHARS", "4.0"))  # credits per 1 000 ElevenLabs chars
CREDIT_COST_AUDIO_MIN = float(os.environ.get("CREDIT_COST_AUDIO_MIN", "2.0"))       # floor — any audio costs at least this
CREDIT_COST_TRANSLATION = float(os.environ.get("CREDIT_COST_TRANSLATION", "0.1"))   # per translation call

# --- Actual API Pricing (USD) — used to record real spend per transaction ---
# Current published rates as of the Lexume Plus subscription plan (Aug 2026).
# Re-check these periodically — providers change pricing without much notice,
# and this is what the unit-economics/margin numbers in that plan assume.
# Gemini 3.7 Flash (standard tier, <=200K context)
GEMINI_INPUT_PRICE_PER_M_TOKENS = float(os.environ.get("GEMINI_INPUT_PRICE_PER_M_TOKENS", "0.75"))
GEMINI_OUTPUT_PRICE_PER_M_TOKENS = float(os.environ.get("GEMINI_OUTPUT_PRICE_PER_M_TOKENS", "3.75"))
# ElevenLabs Multilingual v2 = $0.12/1K chars; Flash/Turbo = $0.06/1K chars.
# Lexume Plus narration is forced to Flash/Turbo (see api/subscription.py) so
# this constant reflects that rate; BYOK callers may use pricier models, in
# which case this is just an under-estimate in the audit log, not a real cost
# to Lexume (BYOK calls are never billed against Lexume's own money).
ELEVENLABS_PRICE_PER_K_CHARS = float(os.environ.get("ELEVENLABS_PRICE_PER_K_CHARS", "0.06"))

# --- Lexume Plus subscription (Stripe + Google Play Billing) ---
# Monthly narrated-page quota included in the subscription before a paid
# top-up (existing credit-package purchase flow) is needed. Sized in the
# subscription plan so a subscriber who maxes this out every month is still
# profitable at SUBSCRIPTION_PRICE_MONTHLY_USD, not just the average user.
SUBSCRIPTION_PAGES_QUOTA = int(os.environ.get("SUBSCRIPTION_PAGES_QUOTA", "70"))
SUBSCRIPTION_PRICE_MONTHLY_USD = float(os.environ.get("SUBSCRIPTION_PRICE_MONTHLY_USD", "12.99"))
SUBSCRIPTION_PRICE_ANNUAL_USD = float(os.environ.get("SUBSCRIPTION_PRICE_ANNUAL_USD", "99.99"))

# Stripe (web / Mac direct-distribution path)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY", "")
STRIPE_PRICE_ID_ANNUAL = os.environ.get("STRIPE_PRICE_ID_ANNUAL", "")
STRIPE_CHECKOUT_SUCCESS_URL = os.environ.get("STRIPE_CHECKOUT_SUCCESS_URL", "http://localhost:3000/billing/success")
STRIPE_CHECKOUT_CANCEL_URL = os.environ.get("STRIPE_CHECKOUT_CANCEL_URL", "http://localhost:3000/billing/cancel")
STRIPE_PORTAL_RETURN_URL = os.environ.get("STRIPE_PORTAL_RETURN_URL", "http://localhost:3000/settings")

# Google Play Billing (Android path) — a service-account JSON key with access
# to the Play Console's Android Publisher API, used to verify purchase tokens
# server-side rather than trusting the client's purchase result.
PLAY_PACKAGE_NAME = os.environ.get("PLAY_PACKAGE_NAME", "com.aboooooo57.lexume")
PLAY_SERVICE_ACCOUNT_JSON = os.environ.get("PLAY_SERVICE_ACCOUNT_JSON", "")  # inline JSON, not a path
# Shared secret Play's Real-time Developer Notifications (Pub/Sub push) must
# present as a query param, since that endpoint can't carry a user JWT.
PLAY_RTDN_WEBHOOK_SECRET = os.environ.get("PLAY_RTDN_WEBHOOK_SECRET", "")
