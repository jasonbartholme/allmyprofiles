"""Smoke test for the Stripe billing integration.

Runs WITHOUT real credentials: verifies graceful degradation, schema sync,
route wiring, template rendering, and the tier-sync logic with a fake
subscription object.
"""
import os
import sys
from pathlib import Path

os.environ['FLASK_ENV'] = 'development'
os.environ.setdefault('SKIP_AUTO_SCHEMA', '')  # exercise dev auto-ALTER
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stripe_billing  # noqa: E402
from app import create_app  # noqa: E402
from models import db, User, Setting  # noqa: E402


def main():
    app = create_app('development')
    checks = []

    def ok(name, cond):
        checks.append((name, bool(cond)))
        print(('PASS' if cond else 'FAIL'), '-', name)

    with app.app_context():
        # 1. New columns exist after _ensure_schema ran at create_app().
        cols = {c['name'] for c in db.inspect(db.engine).get_columns('users')}
        ok('users.stripe_* columns added',
           {'stripe_customer_id', 'stripe_subscription_id',
            'stripe_subscription_status', 'stripe_current_period_end'} <= cols)

        # 2. Settings seeded.
        Setting.seed_defaults()
        db.session.commit()
        ok('stripe price settings seeded',
           Setting.get('stripe_price_expanded') is not None)

        # 3. Graceful degradation without a secret key.
        ok('stripe_ready False w/o key', not stripe_billing.stripe_ready())
        ok('fmt_money cents->dollars',
           stripe_billing.fmt_money(1250, 'usd') == '$12.50')
        ok('fmt_money zero-decimal (JPY)',
           stripe_billing.fmt_money(1250, 'jpy').startswith('¥'))

        # 4. Tier sync logic (no network needed — plain objects).
        class O:
            def __init__(self, **kw): self.__dict__.update(kw)
        u = User.query.filter_by(username='stripe_smoke_user').first()
        if u is None:
            u = User(username='stripe_smoke_user', email='s@x.io', display_name='Smoke', tier='Free')
            u.set_password('pw')
            db.session.add(u)
            db.session.commit()
        sub_active = O(id='sub_1', status='active', current_period_end=1760000000,
                       items=O(data=[O(price=O(id='price_fake_full'))]),
                       metadata={'tier': 'Full'})
        # No price IDs configured -> metadata fallback decides the tier.
        stripe_billing.apply_subscription_state(u, sub_active)
        db.session.commit()
        ok('active sub w/ metadata tier -> Full', u.tier == 'Full')
        ok('status mirrored', u.stripe_subscription_status == 'active')
        ok('period end stored', u.stripe_current_period_end == 1760000000)

        sub_canceled = O(id='sub_1', status='canceled', current_period_end=None,
                         items=O(data=[]), metadata={})
        stripe_billing.apply_subscription_state(u, sub_canceled)
        db.session.commit()
        ok('canceled sub -> back to Free', u.tier == 'Free')

        sub_pastdue = O(id='sub_1', status='past_due', current_period_end=None,
                        items=O(data=[]), metadata={'tier': 'Expanded'})
        u.tier = 'Expanded'
        stripe_billing.apply_subscription_state(u, sub_pastdue)
        db.session.commit()
        ok('past_due keeps paid tier (grace)', u.tier == 'Expanded')

        # cleanup
        db.session.delete(u)
        db.session.commit()

    # 5. Routes via test client.
    c = app.test_client()
    r = c.post('/webhooks/stripe', data=b'{}',
               headers={'Stripe-Signature': 't=1,v1=bad'})
    ok('webhook rejects bad signature (400)', r.status_code == 400)

    r = c.get('/admin/billing')
    ok('admin billing requires login (redirect)', r.status_code in (302, 301))

    # Login as an admin to render the page (graceful "not connected" state).
    with app.app_context():
        admin = User.query.filter_by(is_admin=True).first()
        if admin is None:
            admin = User(username='stripe_admin_smoke', email='a@x.io', display_name='Admin',
                         is_admin=True, tier='Full')
            admin.set_password('pw')
            db.session.add(admin)
            db.session.commit()
        aid = admin.id
    c.post('/login', data={'email_or_username': 'stripe_admin_smoke',
                           'password': 'pw'})
    # If the login field names differ, fall back to direct session login.
    r = c.get('/admin/billing')
    if r.status_code in (302, 301):
        with c.session_transaction() as sess:
            sess['_user_id'] = str(aid)
        r = c.get('/admin/billing')
    ok('admin billing renders (no creds -> guidance)',
       r.status_code == 200 and b'Stripe is not connected' in r.data)

    # 6. Dashboard shows upgrade buttons for free users.
    with app.app_context():
        fu = User.query.filter_by(username='stripe_free_view').first()
        if fu is None:
            fu = User(username='stripe_free_view', email='f@x.io', display_name='Free', tier='Free')
            fu.set_password('pw')
            db.session.add(fu)
            db.session.commit()
        fid = fu.id
    with c.session_transaction() as sess:
        sess['_user_id'] = str(fid)
    r = c.get('/dashboard')
    ok('dashboard has Upgrade to Expanded',
       r.status_code == 200 and b'Upgrade to Expanded' in r.data)
    r = c.post('/upgrade/Full')  # stripe not ready -> flash + redirect
    ok('upgrade degrades gracefully (302)', r.status_code == 302)
    with app.app_context():
        u2 = db.session.get(User, fid)
        db.session.delete(u2)
        db.session.commit()

    failed = [n for n, v in checks if not v]
    print(f'\n{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('FAILED:', failed)
        sys.exit(1)


if __name__ == '__main__':
    main()
