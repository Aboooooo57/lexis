"""
Lexume Plus subscription domain logic.

Deliberately separate from api/routes/billing.py (the HTTP surface — Stripe
checkout/webhooks, Play verify/webhooks) and api/database.py (pure data
access): this is the one place that decides "is this call allowed to use
Lexume's own Gemini/ElevenLabs keys, and at what cost to the user's quota."
api/routes/pages.py and api/routes/extract.py call into this instead of
resolving `gemini_key or config.GEMINI_API_KEY` themselves, so the
subscription/BYOK decision isn't duplicated per route.

Ground rule (see the subscription plan's business-plan section): BYOK is
always free to the caller and untouched by any of this — if a user supplies
their own key, it's used as-is, no subscription check, no quota, no credit
deduction. Only the "no key supplied" fallback path touches subscription
state, because that's the only path that spends Lexume's own money.
"""
from __future__ import annotations

from fastapi import HTTPException

from api import database, config

# Lexume Plus narration is forced to a Flash/Turbo ElevenLabs model,
# regardless of what config.DEFAULT_ELEVENLABS_MODEL or the caller's own
# preference says. Multilingual v2 (the app's own default) costs roughly 2x
# as much per character and would break the unit economics the
# SUBSCRIPTION_PRICE_MONTHLY_USD price point is sized around. BYOK callers
# are unaffected — they pick any model since they're paying ElevenLabs
# directly, not Lexume.
SUBSCRIPTION_ELEVENLABS_MODEL = "eleven_flash_v2_5"

_NO_KEY_NO_SUB_DETAIL = (
    "No API key provided and no active Lexume Plus subscription. Add your "
    "own {service} key in Settings, or subscribe to Lexume Plus."
)


async def resolve_extraction_key(user_gemini_key: str | None, user_id: str) -> str:
    """
    Resolve the Gemini API key to use for a text-extraction call.

    Extraction is cheap (~$0.004/page, see the subscription plan) so it
    isn't quota-metered for subscribers — just gated on subscription status.
    """
    if user_gemini_key:
        return user_gemini_key

    if not await database.is_subscription_active(user_id):
        raise HTTPException(status_code=402, detail=_NO_KEY_NO_SUB_DETAIL.format(service="Gemini"))

    if not config.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="Lexume Plus extraction is temporarily unavailable.")

    return config.GEMINI_API_KEY


async def check_narration_access(user_eleven_key: str | None, user_id: str) -> str:
    """
    Decide how a narration call is allowed to proceed, without raising —
    the caller (api/routes/pages.py) needs to distinguish "over quota" from
    "no access at all" because the former still has a path forward (the
    existing credit-based top-up flow), not just a hard failure.

    Returns one of:
      "byok"                    — caller supplied their own key, no gating.
      "subscription"            — active subscription, within quota.
      "subscription_over_quota" — active subscription, quota used up this
                                   period; caller should offer the existing
                                   credit-purchase top-up flow instead of
                                   failing outright.
      "unauthorized"            — no key and no active subscription.
    """
    if user_eleven_key:
        return "byok"
    if not await database.is_subscription_active(user_id):
        return "unauthorized"
    sub = await database.get_subscription(user_id)
    used = (sub or {}).get("pages_narrated_this_period", 0)
    if used >= config.SUBSCRIPTION_PAGES_QUOTA:
        return "subscription_over_quota"
    return "subscription"


def narration_credentials(access: str, user_eleven_key: str | None) -> tuple[str, str]:
    """
    Resolve the (api_key, model_id) to actually use, given the access
    decision from check_narration_access(). Both subscription outcomes use
    Lexume's own key forced to the Flash/Turbo model — "over_quota" is only
    reached here after the caller has separately confirmed a credit-based
    top-up covers this call (see api/routes/pages.py).
    """
    if access == "byok":
        return user_eleven_key or "", config.DEFAULT_ELEVENLABS_MODEL
    if not config.ELEVENLABS_API_KEY:
        raise HTTPException(status_code=503, detail="Lexume Plus narration is temporarily unavailable.")
    return config.ELEVENLABS_API_KEY, SUBSCRIPTION_ELEVENLABS_MODEL
