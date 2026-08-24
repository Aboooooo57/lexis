"""
Lexume Plus billing — Stripe (web/Mac direct-distribution path) and Google
Play Billing (Android path) integration.

Neither payment platform's client-reported purchase result is ever trusted
on its own: Stripe changes are driven by signature-verified webhooks, and
Play purchases are verified server-side against the Play Developer API
before a subscription row is ever marked active. See api/subscription.py
for how the rest of the app then decides whether to actually use these
subscriptions (BYOK always takes priority and never touches any of this).
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api import config, database
from api.auth import get_current_user_id

router = APIRouter(tags=["billing"])


# ── Shared helpers ───────────────────────────────────────────────────────────

def _require_stripe():
    if not config.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe is not configured on this server.")
    import stripe
    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe


def _plan_to_stripe_price(plan: str) -> str:
    price_id = {"monthly": config.STRIPE_PRICE_ID_MONTHLY, "annual": config.STRIPE_PRICE_ID_ANNUAL}.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail="plan must be 'monthly' or 'annual'")
    return price_id


def _stripe_price_to_plan(price_id: str) -> str:
    if price_id == config.STRIPE_PRICE_ID_ANNUAL:
        return "annual"
    return "monthly"


def _stripe_status_to_internal(stripe_status: str) -> str:
    # Stripe subscription statuses: incomplete, incomplete_expired, trialing,
    # active, past_due, canceled, unpaid, paused. Collapse to our 4 values.
    if stripe_status in ("active", "trialing"):
        return "active"
    if stripe_status == "past_due":
        return "past_due"
    if stripe_status in ("canceled", "unpaid", "incomplete_expired", "paused"):
        return "canceled"
    return "inactive"


async def _apply_stripe_subscription(user_id: str, stripe_sub: dict) -> None:
    """Upsert our subscription row from a Stripe Subscription object (dict-like)."""
    status = _stripe_status_to_internal(stripe_sub["status"])
    price_id = stripe_sub["items"]["data"][0]["price"]["id"]
    plan = _stripe_price_to_plan(price_id)
    period_end = datetime.datetime.fromtimestamp(
        stripe_sub["current_period_end"], tz=datetime.timezone.utc
    ).isoformat()

    existing = await database.get_subscription(user_id)
    is_new_period = not existing or existing.get("current_period_end") != period_end

    await database.upsert_subscription(
        user_id,
        plan=plan,
        status=status,
        payment_platform="stripe",
        platform_customer_id=stripe_sub["customer"],
        platform_subscription_id=stripe_sub["id"],
        current_period_end=period_end,
    )
    if status == "active" and is_new_period:
        await database.start_new_billing_period(user_id, period_end)


# ── Stripe: checkout ─────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan: str  # "monthly" | "annual"


class CheckoutResponse(BaseModel):
    checkout_url: str


@router.post("/billing/stripe/checkout", response_model=CheckoutResponse)
async def create_stripe_checkout(body: CheckoutRequest, user_id: str = Depends(get_current_user_id)):
    stripe = _require_stripe()
    price_id = _plan_to_stripe_price(body.plan)

    existing = await database.get_subscription(user_id)
    customer_id = existing.get("platform_customer_id") if existing else None

    def _create_session():
        kwargs = dict(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=user_id,
            metadata={"user_id": user_id},
            success_url=config.STRIPE_CHECKOUT_SUCCESS_URL,
            cancel_url=config.STRIPE_CHECKOUT_CANCEL_URL,
        )
        if customer_id:
            kwargs["customer"] = customer_id
        # else: no Stripe customer on file yet — Checkout collects the
        # email itself and Stripe creates the customer on completion.
        return stripe.checkout.Session.create(**kwargs)

    try:
        session = await asyncio.to_thread(_create_session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stripe checkout creation failed: {e}")

    return CheckoutResponse(checkout_url=session.url)


@router.post("/billing/stripe/portal", response_model=CheckoutResponse)
async def create_stripe_portal(user_id: str = Depends(get_current_user_id)):
    """Returns a Stripe Billing Portal URL so users can update payment method or cancel without any custom UI."""
    stripe = _require_stripe()
    sub = await database.get_subscription(user_id)
    customer_id = sub.get("platform_customer_id") if sub else None
    if not customer_id:
        raise HTTPException(status_code=404, detail="No Stripe subscription on file for this account.")

    def _create_portal():
        return stripe.billing_portal.Session.create(customer=customer_id, return_url=config.STRIPE_PORTAL_RETURN_URL)

    try:
        portal = await asyncio.to_thread(_create_portal)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stripe portal creation failed: {e}")

    return CheckoutResponse(checkout_url=portal.url)


# ── Stripe: webhook ──────────────────────────────────────────────────────────

@router.post("/billing/stripe/webhook")
async def stripe_webhook(request: Request):
    stripe = _require_stripe()
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, config.STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Stripe webhook signature: {e}")

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        user_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
        stripe_subscription_id = obj.get("subscription")
        if user_id and stripe_subscription_id:
            def _fetch_sub():
                return stripe.Subscription.retrieve(stripe_subscription_id)
            stripe_sub = await asyncio.to_thread(_fetch_sub)
            await _apply_stripe_subscription(user_id, stripe_sub)

    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        existing = await database.get_subscription_by_platform_id(obj["id"])
        user_id = existing["user_id"] if existing else (obj.get("metadata") or {}).get("user_id")
        if user_id:
            await _apply_stripe_subscription(user_id, obj)

    elif event_type == "customer.subscription.deleted":
        existing = await database.get_subscription_by_platform_id(obj["id"])
        if existing:
            await database.upsert_subscription(existing["user_id"], status="canceled")

    return {"received": True}


# ── Google Play Billing: server-side purchase verification ─────────────────

async def _play_access_token() -> str:
    if not config.PLAY_SERVICE_ACCOUNT_JSON:
        raise HTTPException(status_code=503, detail="Google Play Billing is not configured on this server.")

    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest

    info = json.loads(config.PLAY_SERVICE_ACCOUNT_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/androidpublisher"]
    )

    def _refresh():
        credentials.refresh(GoogleAuthRequest())
        return credentials.token

    return await asyncio.to_thread(_refresh)


async def _verify_play_purchase(purchase_token: str, product_id: str) -> dict:
    access_token = await _play_access_token()
    url = (
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
        f"{config.PLAY_PACKAGE_NAME}/purchases/subscriptionsv2/tokens/{purchase_token}"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Play purchase verification failed: {resp.text}")
    return resp.json()


async def _acknowledge_play_purchase(purchase_token: str, product_id: str) -> None:
    """Play auto-refunds a subscription not acknowledged within 3 days — best-effort, failures are logged, not fatal."""
    try:
        access_token = await _play_access_token()
        url = (
            f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
            f"{config.PLAY_PACKAGE_NAME}/purchases/subscriptions/{product_id}/tokens/{purchase_token}:acknowledge"
        )
        async with httpx.AsyncClient() as client:
            await client.post(url, headers={"Authorization": f"Bearer {access_token}"})
    except Exception as e:
        print(f"WARN: Play purchase acknowledgement failed (non-fatal): {e}")


def _play_state_to_internal(subscription_state: str) -> str:
    # subscriptionsv2 states: SUBSCRIPTION_STATE_ACTIVE, _CANCELED, _IN_GRACE_PERIOD,
    # _ON_HOLD, _PAUSED, _EXPIRED, _PENDING.
    if subscription_state in ("SUBSCRIPTION_STATE_ACTIVE", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD"):
        return "active"
    if subscription_state == "SUBSCRIPTION_STATE_ON_HOLD":
        return "past_due"
    return "canceled"


async def _apply_play_subscription(user_id: str, product_id: str, purchase_token: str, play_data: dict) -> None:
    state = _play_state_to_internal(play_data.get("subscriptionState", ""))
    line_items = play_data.get("lineItems", [])
    period_end = line_items[0]["expiryTime"] if line_items else None

    existing = await database.get_subscription(user_id)
    # product_id is only known on the initial client-side verify call — the
    # RTDN webhook path only has the purchase token, so preserve whatever
    # plan is already on file rather than defaulting to "monthly".
    if product_id:
        plan = "annual" if "annual" in product_id else "monthly"
    else:
        plan = (existing or {}).get("plan", "monthly")
    is_new_period = not existing or existing.get("current_period_end") != period_end

    await database.upsert_subscription(
        user_id,
        plan=plan,
        status=state,
        payment_platform="play",
        platform_subscription_id=purchase_token,
        current_period_end=period_end,
    )
    if state == "active" and is_new_period and period_end:
        await database.start_new_billing_period(user_id, period_end)


class PlayVerifyRequest(BaseModel):
    purchase_token: str
    product_id: str


@router.post("/billing/play/verify")
async def verify_play_purchase(body: PlayVerifyRequest, user_id: str = Depends(get_current_user_id)):
    play_data = await _verify_play_purchase(body.purchase_token, body.product_id)
    await _apply_play_subscription(user_id, body.product_id, body.purchase_token, play_data)
    await _acknowledge_play_purchase(body.purchase_token, body.product_id)
    return {"status": "verified", "subscription_state": play_data.get("subscriptionState")}


@router.post("/billing/play/webhook")
async def play_rtdn_webhook(request: Request, secret: str = ""):
    """
    Real-time Developer Notifications push endpoint (Pub/Sub). Google posts a
    PubsubMessage whose base64 `data` field is a DeveloperNotification JSON
    with the purchase token to re-verify — the notification payload itself
    is never trusted for subscription state, only used to know what to
    re-check against the Play Developer API.

    Secured with a shared-secret query param (PLAY_RTDN_WEBHOOK_SECRET) set
    on the Pub/Sub push subscription's endpoint URL. A production hardening
    pass could additionally verify Pub/Sub's OIDC bearer token instead.
    """
    if not config.PLAY_RTDN_WEBHOOK_SECRET or secret != config.PLAY_RTDN_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    body = await request.json()
    message = body.get("message", {})
    data_b64 = message.get("data", "")
    if not data_b64:
        return {"received": True}

    notification = json.loads(base64.b64decode(data_b64))
    sub_notification = notification.get("subscriptionNotification")
    if not sub_notification:
        return {"received": True}  # voided-purchase or test notifications — nothing to do

    purchase_token = sub_notification["purchaseToken"]
    existing = await database.get_subscription_by_platform_id(purchase_token)
    if not existing:
        # Purchase we don't know about yet (verify hasn't landed, or a very
        # old token) — nothing to reconcile against.
        return {"received": True}

    play_data = await _verify_play_purchase(purchase_token, "")
    await _apply_play_subscription(existing["user_id"], "", purchase_token, play_data)
    return {"received": True}


# ── Status ────────────────────────────────────────────────────────────────

class SubscriptionStatusResponse(BaseModel):
    active: bool
    plan: str | None = None
    status: str | None = None
    current_period_end: str | None = None
    pages_narrated_this_period: int = 0
    pages_quota: int = config.SUBSCRIPTION_PAGES_QUOTA


@router.get("/billing/status", response_model=SubscriptionStatusResponse)
async def billing_status(user_id: str = Depends(get_current_user_id)):
    sub = await database.get_subscription(user_id)
    active = await database.is_subscription_active(user_id)
    if not sub:
        return SubscriptionStatusResponse(active=False)
    return SubscriptionStatusResponse(
        active=active,
        plan=sub.get("plan"),
        status=sub.get("status"),
        current_period_end=sub.get("current_period_end"),
        pages_narrated_this_period=sub.get("pages_narrated_this_period", 0),
    )
