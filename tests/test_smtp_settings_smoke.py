"""Smoke test for the Gmail SMTP admin-settings epic chunk.

Covers:
  * new smtp_* / mail_* keys seeded via DEFAULT_SETTINGS (append-only),
  * migration 0003 idempotent seed of settings rows,
  * admin settings page renders the Email delivery section,
  * saving the form persists values; blank password keeps the stored one,
  * validation rejects bad host/port/username when enabled,
  * unconfigured send is a graceful no-op (returns False, never raises),
  * contact form still works with email off (functionality unaffected),
  * contact form fires an email copy when configured (mocked SMTP).

Run:  python tests/test_smtp_settings_smoke.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ['FLASK_ENV'] = 'testing'
_tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmpdb.close()
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmpdb.name

from app import create_app                    # noqa: E402
from models import (db, User, Setting, LinkSource, LinkCategory,  # noqa: E402
                    ContentSection, Faq, ContactMessage)
import mail_util                               # noqa: E402
from app import profile_handle                 # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  PASS  {name}')
    else:
        failed += 1
        print(f'  FAIL  {name}')


app = create_app('testing')
with app.app_context():
    db.create_all()
    Setting.seed_defaults()
    LinkSource.seed_defaults()
    LinkCategory.seed_defaults()
    ContentSection.seed_defaults()
    Faq.seed_defaults()
    db.session.commit()

    # ---- seeding ---------------------------------------------------------
    for key in mail_util.SMTP_KEYS:
        check(f'default seeded: {key}', Setting.get(key) is not None)
    check('smtp disabled by default',
          mail_util.get_smtp_config()['enabled'] is False)
    check('unconfigured -> is_configured False',
          mail_util.is_configured() is False)

    # env fallback works without any DB row change
    with patch.dict(os.environ, {'MAIL_SERVER': 'smtp.gmail.com',
                                 'MAIL_USERNAME': 'envuser@gmail.com'}):
        cfg = mail_util.get_smtp_config()
        check('MAIL_SERVER env fallback', cfg['host'] == 'smtp.gmail.com')
        check('MAIL_USERNAME env fallback', cfg['username'] == 'envuser@gmail.com')

    # ---- users -----------------------------------------------------------
    admin = User(username='smtpadmin', email='smtpadmin@example.com',
                 display_name='SMTP Admin', is_admin=True)
    admin.set_password('AdminPass123!')
    owner = User(username='owner1', email='owner@example.com',
                 display_name='Profile Owner')
    owner.set_password('OwnerPass123!')
    db.session.add_all([admin, owner])
    db.session.commit()
    owner_id, owner_email_before = owner.id, owner.email
    profile_handle_for_contact = profile_handle(owner)

client = app.test_client()


def login(email, password):
    return client.post('/login', data={'email': email, 'password': password},
                       follow_redirects=True)


r = login('smtpadmin@example.com', 'AdminPass123!')
check('admin can log in', '/admin' in r.request.path or 'dashboard' in r.text.lower())

# ---- settings page renders the new section -------------------------------
page = client.get('/admin/settings').text
check('settings page shows SMTP section', 'Email delivery (Gmail SMTP)' in page)
check('settings page has App Password help', 'apppasswords' in page)
check('raw password not in HTML', 'smtp_password" value=""' in page
      or 'value=""' in page)

# ---- save via the form ----------------------------------------------------
form = {'smtp_enabled': 'true', 'smtp_host': 'smtp.gmail.com',
        'smtp_port': '465', 'smtp_security': 'ssl',
        'smtp_username': 'me@gmail.com', 'smtp_password': 'abcd efgh ijkl mnop',
        'mail_from': 'me@gmail.com', 'mail_from_name': 'AllMyProfiles',
        'site_tagline': 'still works'}
r = client.post('/admin/settings', data=form, follow_redirects=True)
check('save succeeded', r.status_code == 200 and 'Settings saved' in r.text)
cfg = None
with app.app_context():
    cfg = mail_util.get_smtp_config()
check('values persisted', cfg['host'] == 'smtp.gmail.com'
      and cfg['port'] == 465 and cfg['security'] == 'ssl'
      and cfg['username'] == 'me@gmail.com')
check('is_configured now True', mail_util.is_configured(cfg))

# blank password keeps the stored one
r = client.post('/admin/settings', data={**form, 'smtp_password': ''},
                follow_redirects=True)
with app.app_context():
    check('blank password keeps stored secret',
          Setting.get('smtp_password') == 'abcd efgh ijkl mnop')

# masked display, never raw
page = client.get('/admin/settings').text
check('password masked in UI', 'abcd efgh ijkl mnop' not in page
      and 'Saved:' in page)

# ---- validation ------------------------------------------------------------
bad = {'smtp_enabled': 'true', 'smtp_host': '', 'smtp_port': '99999',
       'smtp_username': 'not-an-email', 'smtp_security': 'ssl'}
r = client.post('/admin/settings', data=bad, follow_redirects=True)
check('invalid SMTP input rejected', 'must be' in r.text.lower())
with app.app_context():
    check('rejected save did not clobber host',
          Setting.get('smtp_host') == 'smtp.gmail.com')

# ---- graceful degradation on send ------------------------------------------
# NOTE: mail settings are read from the DB, so these direct calls run inside
# an app context (mirrors what happens inside a real request).
with app.app_context():
    with patch('mail_util.smtplib.SMTP_SSL', side_effect=OSError('network down')):
        ok, detail = mail_util.send_email('someone@example.com', 'hi', 'body')
check('failed send returns False + reason (no raise)',
      ok is False and 'network down' in detail)

with app.app_context():
    ok, detail = mail_util.send_email('bad@@address', 'hi', 'body')
check('invalid recipient rejected politely', ok is False and 'invalid' in detail)

# ---- contact flow: email OFF behaves exactly as before ----------------------
client.get('/logout')
with app.app_context():
    Setting.set_value('smtp_enabled', 'false')
    db.session.commit()
r = client.post(f"/{profile_handle_for_contact}/submit-contact",
                data={'name': 'Visitor',
                      'email': 'v@visitor.com',
                      'subject': 'Hello',
                      'message': 'Love your profiles.'},
                follow_redirects=True)
check('contact form works with email disabled (bare /<handle> URL)',
      r.status_code == 200)
with app.app_context():
    cm = ContactMessage.query.order_by(ContactMessage.id.desc()).first()
    check('contact message recorded in inbox', cm is not None
          and cm.sender_email == 'v@visitor.com')
    u = db.session.get(User, owner_id)
    check('existing user record untouched (append-only)',
          u.email == owner_email_before and u.password_hash)

# ---- contact flow: email ON sends a copy (mocked SMTP) ---------------------
sent = {}


class FakeServer:
    def __init__(self, *a, **k):
        pass
    def login(self, user, pwd):
        sent['login'] = (user, pwd)
    def send_message(self, msg, from_addr=None, to_addrs=None):
        sent['to'] = to_addrs
        sent['msg'] = msg
    def quit(self):
        pass


with patch('mail_util.smtplib.SMTP_SSL', FakeServer):
    with app.app_context():
        Setting.set_value('smtp_enabled', 'true')
        db.session.commit()
    r = client.post(f'/{profile_handle_for_contact}/submit-contact',
                    data={'name': 'Visitor2',
                          'email': 'v2@visitor.com',
                          'subject': 'Second',
                          'message': 'Another hello.'},
                    follow_redirects=True)
    check('contact form still succeeds with email enabled', r.status_code == 200)
    check('email copy attempted to profile owner',
          sent.get('to') == ['owner@example.com'])
    check('email body includes sender', 'Visitor2' in sent['msg'].as_string())

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
