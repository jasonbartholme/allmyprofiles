"""Stripe integration for subscription handling & the admin SaaS overview.

Design notes
------------
* Credentials come from ``app.config`` (env vars STRIPE_SECRET_KEY,
  STRIPE_WEBHOOK_SECRET, ... — see config.py). When no secret key is set the
  whole module degrades gracefully: ``stripe_ready()`` returns False and the
  routes show friendly "billing not configured" messages instead of crashing.
  This keeps dev/test runs working before you paste in your real keys.
* Checkout uses **Stripe Checkout Sessions** (hosted page) so we never touch
  card data. After payment Stripe redirects back to /checkout/success and
  fires webhooks at /webhooks/stripe which are the authoritative source for
  tier changes.
* The local ``User.tier`` is kept in sync with the subscription status so all
  existing tier-gated features (link limits, pinning, themes, featured slots,
  adult gating groundwork) work unchanged.
* The admin SaaS overview (/admin/billing) reads live Stripe data:
  - subscriptions created today
  - cleared funds  = net available balance (settled/payout-eligible)
  - refunding      = refunds currently pending processing
  - held funds     = balance still on hold (pending + chargeback_hold)
  - stripe fees    = fee_cents summed over charges in the current month
  All money figures are shown in the account's currency; per-item amounts use
  each object's own currency. Amounts are cached briefly (BILLING_CACHE_SECS)
  because the dashboard may be refreshed often and Stripe counts as a client.

Setup checklist (once, in the Stripe dashboard)
-----------------------------------------------
1. Create two Products ("Expanded", "Full") each with a recurring monthly
   price; copy the ``price_...`` IDs into Site settings
   (stripe_price_expanded / stripe_price_full) or env vars.
2. Point a Webhook at ``https://<yoursite>/webhooks/stripe`` subscribing to:
   checkout.session.completed, customer.subscription.created,
   customer.subscription.updated, customer.subscription.deleted,
   invoice.payment_failed. Copy the signing secret into STRIPE_WEBHOOK_SECRET.
3. For local testing run: ``stripe listen --forward-to localhost:5000/webhooks/stripe``
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import stripe
from flask import abort, current_app

# Tiers that map to a Stripe price setting/env var. 'Custom' stays manual
# (invoiced / negotiated) and is intentionally not self-serve.
TIER_PRICES = {
    'Expanded': ('stripe_price_expanded', 'STRIPE_PRICE_EXPANDED'),
    'Full': ('stripe_price_full', 'STRIPE_PRICE_FULL'),
}

# Subscription statuses that count as "paying" locally.
ACTIVE_STATUSES = ('active', 'trialing')
# Statuses where access is suspended but billing may resume (past_due grace).
GRACE_STATUSES = ('past_due', 'unpaid')

# Cache window for the (multi-request) SaaS overview aggregation.
BILLING_CACHE_SECS = 60
_billing_cache = {'ts': 0.0, 'data': None}


# ----------------------------------------------------------------------
# Client helpers
# ----------------------------------------------------------------------

def _secret_key():
    return (current_app.config.get('STRIPE_SECRET_KEY') or '').strip()


def stripe_ready():
    """True when a Stripe secret key is configured."""
    return bool(_secret_key())


def client():
    """Return a scoped Stripe client, or raise a 503-safe RuntimeError.

    Callers must check ``stripe_ready()`` first (routes do this via
    ``require_stripe()``), so failures here indicate a genuinely bad key.
    """
    key = _secret_key()
    if not key:
        raise RuntimeError('Stripe is not configured (missing STRIPE_SECRET_KEY).')
    return stripe.StripeClient(api_key=key)


def require_stripe():
    if not stripe_ready():
        abort(503, description=('Billing is not configured yet. Add your Stripe '
                                'secret key (STRIPE_SECRET_KEY) in the environment '
                                'and reload.'))


def price_id_for(tier):
    """Resolve a tier -> Stripe price ID.

    Precedence: Site settings row (admin-editable) > config/env value.
    Returns '' when unconfigured.
    """
    entry = TIER_PRICES.get(tier)
    if not entry:
        return ''
    setting_key, env_key = entry
    # Local import avoids a circular import at module load time.
    from models import Setting
    val = (Setting.get(setting_key, '') or '').strip()
    if val:
        return val
    return (current_app.config.get(env_key) or '').strip()


# ----------------------------------------------------------------------
# Money formatting
# ----------------------------------------------------------------------

_CURRENCY_SYMBOLS = {'usd': '$', 'eur': '€', 'gbp': '£', 'cad': 'CA$',
                     'aud': 'A$', 'jpy': '¥', 'chf': 'CHF ', 'sek': 'kr ',
                     'nok': 'kr ', 'dkk': 'kr ', 'mxn': 'MX$', 'brl': 'R$'}


def fmt_money(amount_minor, currency='usd'):
    """Format a Stripe *minor-unit* amount ($1000 cents -> '$10.00').

    Zero-decimal currencies (JPY, KRW...) are rendered without decimals.
    """
    try:
        amount = int(amount_minor or 0)
    except (TypeError, ValueError):
        amount = 0
    cur = (currency or 'usd').lower()
    symbol = _CURRENCY_SYMBOLS.get(cur, (cur.upper() + ' '))
    zero_decimal = cur in ('jpy', 'krw', 'vnd', 'clp', 'inr_tentatively')
    if zero_decimal:
        return f'{symbol}{amount:,}'
    return f'{symbol}{amount / 100:,.2f}'


# ----------------------------------------------------------------------
# Checkout / portal flows (called from app.py routes)
# ----------------------------------------------------------------------

def create_checkout_session(user, tier, base_url):
    """Create a Stripe Checkout Session for a subscription upgrade.

    Returns the session object (redirect to session.url). Reuses (or creates)
    the customer record so the portal and webhooks can find the user later.
    """
    cl = client()
    price = price_id_for(tier)
    if not price:
        raise RuntimeError(f'No Stripe price configured for tier "{tier}". '
                           'Set it in Admin → Site settings.')

    customer_id = user.stripe_customer_id
    if not customer_id:
        cust = cl.customers.create(
            email=user.email,
            name=user.display_name or user.username,
            metadata={'user_id': str(user.id), 'username': user.username},
        )
        customer_id = cust.id
        user.stripe_customer_id = customer_id

    success = f'{base_url}/checkout/success?tier={tier}'
    cancel = f'{base_url}/checkout/cancel'
    session = cl.checkout.sessions.create(
        mode='subscription',
        customer=customer_id,
        line_items=[{'price': price, 'quantity': 1}],
        success_url=success,
        cancel_url=cancel,
        client_reference_id=str(user.id),
        metadata={'user_id': str(user.id), 'tier': tier},
        subscription_data={'metadata': {'user_id': str(user.id),
                                        'tier': tier}},
        allow_promotion_codes=True,
    )
    return session


def create_portal_session(user, base_url):
    """Billing-portal session so users manage cards / cancel themselves."""
    cl = client()
    if not user.stripe_customer_id:
        raise RuntimeError('No billing profile found for this account yet.')
    from models import Setting
    ret = Setting.get('stripe_customer_portal_return', '/dashboard') or '/dashboard'
    return_url = base_url.rstrip('/') + ret
    return cl.billing_sessions.create(
        customer=user.stripe_customer_id,
        return_url=return_url,
        features={'subscription_update': {'enabled': True},
                  'subscription_cancel': {'enabled': True},
                  'invoice_history': {'enabled': True},
                  'payment_method_update': {'enabled': True}},
    )


# ----------------------------------------------------------------------
# Webhook handling
# ----------------------------------------------------------------------

def construct_webhook_event(payload: bytes, signature: str):
    """Verify + parse an incoming webhook event (raises on bad signature)."""
    secret = (current_app.config.get('STRIPE_WEBHOOK_SECRET') or '').strip()
    if not secret:
        raise RuntimeError('STRIPE_WEBHOOK_SECRET is not configured.')
    return stripe.Webhook.construct_event(payload, signature, secret)


def _find_user(stripe_obj):
    """Locate our User for a Stripe object via metadata / customer id."""
    from models import User
    uid = None
    meta = getattr(stripe_obj, 'metadata', None) or {}
    if isinstance(meta, dict):
        uid = meta.get('user_id')
    if not uid:
        cust = getattr(stripe_obj, 'customer', None)
        cust_id = getattr(cust, 'id', cust)
        if cust_id:
            u = User.query.filter_by(stripe_customer_id=cust_id).first()
            return u, cust_id
        return None, None
    user = db_get_user(int(uid))
    cust = getattr(stripe_obj, 'customer', None)
    cust_id = getattr(cust, 'id', cust)
    return user, cust_id


def db_get_user(uid):
    from models import User, db
    return db.session.get(User, uid)


def apply_subscription_state(user, sub):
    """Mirror a Stripe subscription onto our local tier fields."""
    from models import db
    status = getattr(sub, 'status', None) or 'canceled'
    user.stripe_subscription_id = getattr(sub, 'id', None)
    user.stripe_subscription_status = status
    cpe = getattr(sub, 'current_period_end', None)
    if cpe:
        user.stripe_current_period_end = int(cpe)

    if status in ACTIVE_STATUSES:
        # Which tier does this subscription buy? Inspect its items' prices.
        bought = _tier_from_subscription(sub)
        if bought and user.tier != 'Custom':
            if user.tier != bought:
                user.tier_changed_at = datetime.now()
            user.tier = bought
        elif not user.stripe_subscription_id:
            user.tier = 'Free'
    elif status in GRACE_STATUSES:
        pass  # keep paid tier during dunning grace period
    else:  # canceled / incomplete_expired / paused ...
        if user.tier in TIER_PRICES and user.tier != 'Custom':
            user.tier = 'Free'
            user.tier_changed_at = datetime.now()
    db.session.flush()
    return status


def _tier_from_subscription(sub):
    """Map subscription line items' price IDs back to our tier names."""
    from models import Setting
    wanted = {}
    for tier in TIER_PRICES:
        pid = price_id_for(tier)
        if pid:
            wanted[pid] = tier
    items = getattr(sub, 'items', None)
    for it in (getattr(items, 'data', None) or []):
        price = getattr(it, 'price', None)
        pid = getattr(price, 'id', None)
        if pid and pid in wanted:
            return wanted[pid]
    # Fallback: subscription metadata recorded at checkout time.
    meta = getattr(sub, 'metadata', None) or {}
    tier = meta.get('tier') if isinstance(meta, dict) else None
    return tier if tier in TIER_PRICES else None


def handle_webhook_event(event):
    """Process one verified Stripe event. Returns a short action string."""
    etype = event.get('type') if isinstance(event, dict) else event.type
    obj = (event['data']['object'] if isinstance(event, dict)
           else event.data.object)

    if etype == 'checkout.session.completed':
        user, cust_id = _find_user(obj)
        if not user:
            return 'ignored:no-user'
        if cust_id:
            user.stripe_customer_id = cust_id
        sub_id = getattr(obj, 'subscription', None) or \
            (obj.get('subscription') if isinstance(obj, dict) else None)
        if sub_id:
            cl = client()
            sub = cl.subscriptions.retrieve(sub_id)
            apply_subscription_state(user, sub)
        return f'activated:{user.username}'

    if etype in ('customer.subscription.created',
                 'customer.subscription.updated',
                 'customer.subscription.deleted'):
        user, cust_id = _find_user(obj)
        if not user and cust_id:
            from models import User
            user = User.query.filter_by(stripe_customer_id=cust_id).first()
        if not user:
            return 'ignored:no-user'
        status = apply_subscription_state(user, obj)
        return f'{etype}:{status}'

    if etype == 'invoice.payment_failed':
        # Surface it to the admin audit trail; grace logic lives in
        # apply_subscription_state via subscription.updated events.
        return 'flagged:payment_failed'

    return f'skipped:{etype}'


# ----------------------------------------------------------------------
# Admin SaaS overview aggregation
# ----------------------------------------------------------------------

def _month_start_utc(now=None):
    now = now or datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def collect_overview():
    """Query Stripe for the SaaS metrics shown on /admin/billing.

    Cached for BILLING_CACHE_SECS so repeated refreshes don't hammer the API.
    Keys of the returned dict mirror the template's number-cards.
    """
    now = time.time()
    if (_billing_cache['data'] is not None
            and now - _billing_cache['ts'] < BILLING_CACHE_SECS):
        return _billing_cache['data']

    cl = client()
    today_start = datetime.now().replace(hour=0, minute=0, second=0,
                                         microsecond=0)
    ts_start = int(today_start.timestamp())
    month_ts = int(_month_start_utc().timestamp())

    # --- Subscriptions created today -----------------------------------
    subs_today = cl.subscriptions.list(created={'gte': ts_start}, limit=100)
    n_subs_today = len(subs_today.data)
    active_subscribers = db_active_subscriber_count()

    # --- Balance: cleared vs held ---------------------------------------
    bal = cl.balance.retrieve()
    avail_minor, avail_cur = 0, 'usd'
    pending_minor, pending_cur = 0, 'usd'
    for b in (bal.available or []):
        avail_minor, avail_cur = int(b.amount), b.currency
    for p in (bal.pending or []):
        pending_minor, pending_cur = int(p.amount), p.currency

    # Funds explicitly held by Stripe (risk reserve / chargeback holds).
    held_minor = 0
    try:
        for act in cl.balance_transactions.list(
                type='reserve_hold', limit=100):
            if int(getattr(act, 'amount', 0) or 0) < 0:
                held_minor += abs(int(act.amount))
    except Exception:
        held_minor = 0  # endpoint shape varies by account; degrade quietly

    # --- Refunds currently processing ------------------------------------
    refunding_minor, refund_cur = 0, avail_cur
    try:
        for r in cl.refunds.list(limit=100):
            if getattr(r, 'status', None) in (None, 'pending'):
                refunding_minor += int(getattr(r, 'amount', 0) or 0)
                refund_cur = getattr(r, 'currency', refund_cur)
    except Exception:
        pass

    # --- Stripe fees this month (sum over charge fees) --------------------
    fees_minor, fees_cur, gross_minor = 0, avail_cur, 0
    try:
        cursor = None
        for _ in range(10):  # hard page cap: max 10 x 100 charges
            page = cl.charges.list(created={'gte': month_ts}, limit=100,
                                   **({'starting_after': cursor} if cursor else {}))
            for ch in page.data:
                fees_minor += int(getattr(ch, 'fee', 0) or 0)
                gross_minor += int(getattr(ch, 'amount', 0) or 0)
                fees_cur = getattr(ch, 'currency', fees_cur)
            if not page.has_more or not page.data:
                break
            cursor = page.data[-1].id
    except Exception:
        pass

    # --- Recent activity lists (nice-to-have under the cards) ------------
    recent_subs = []
    for s in list(subs_today.data)[:10]:
        recent_subs.append({
            'id': s.id,
            'status': s.status,
            'customer': getattr(s.customer, 'email', None) or s.customer,
            'period_end': datetime.fromtimestamp(int(s.current_period_end))
                          if getattr(s, 'current_period_end', None) else None,
        })

    data = {
        'generated_at': datetime.now(),
        'currency': avail_cur.upper(),
        'subs_today': n_subs_today,
        'active_subscribers': active_subscribers,
        'cleared_minor': avail_minor, 'cleared_cur': avail_cur,
        'refunding_minor': refunding_minor, 'refunding_cur': refund_cur,
        'held_minor': pending_minor + held_minor, 'held_cur': pending_cur,
        'fees_minor': fees_minor, 'fees_cur': fees_cur,
        'gross_month_minor': gross_minor,
        'recent_subs': recent_subs,
        'live_mode': _secret_key().startswith('sk_live'),
    }
    _billing_cache['ts'] = now
    _billing_cache['data'] = data
    return data


def db_active_subscriber_count():
    """Local count of accounts on an active/trialing Stripe subscription."""
    from models import User
    return (User.query
            .filter(User.stripe_subscription_status.in_(list(ACTIVE_STATUSES)))
            .count())
