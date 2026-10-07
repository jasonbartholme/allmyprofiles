"""End-to-end smoke test for admin messaging / inbox / link-check dedupe."""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Use a temp file DB (via DEV_DATABASE_URL, which config.py reads at import
# time) instead of :memory: so the in-process cron script
# (check_links.run(), which calls create_app()) shares the same database.
os.environ['FLASK_ENV'] = 'development'
_dbdir = tempfile.mkdtemp()
os.environ['DEV_DATABASE_URL'] = 'sqlite:///' + os.path.join(_dbdir, 'smoke.db')

from datetime import datetime, timedelta
from unittest.mock import patch

from app import create_app, send_admin_message, notify_broken_links
from models import (db, User, Link, Message, MessageRead, Setting,
                    LinkCheckResult)

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ok  {name}')
    else:
        FAIL += 1
        print(f' FAIL {name}')


app = create_app('development')
app.config['WTF_CSRF_ENABLED'] = False


def new_client():
    """Fresh test client (session) — registering auto-logs-in, so each
    signup needs its own cookie jar."""
    return app.test_client()


client = new_client()


def register(username, email, password='Str0ng!Passw0rd'):
    c = new_client()
    r = c.post('/register', data={
        'username': username, 'email': email,
        'password': password, 'password2': password},
        follow_redirects=True)
    c.get('/logout')          # log out so later flows control the session
    return r


def login(username, password='Str0ng!Passw0rd'):
    global client
    client = new_client()     # clean session per login
    email = {
        'alice': 'alice@x.test', 'bobpaid': 'bob@x.test',
        'carolfree': 'carol@x.test', 'rootadmin': 'root@x.test',
    }[username]
    return client.post('/login', data={'email': email,
                                       'password': password},
                       follow_redirects=True)


with app.app_context():
    db.create_all()
    # make an admin directly in DB
    admin = User(username='rootadmin', email='root@x.test',
                 display_name='Root Admin', is_admin=True)
    admin.set_password('AdminPass!23')
    db.session.add(admin)
    db.session.commit()

print('== 1. register/login ==')
r = register('alice', 'alice@x.test');  check('register alice', b'Welcome' in r.data or b'dashboard' in r.data.lower())
r = register('bobpaid', 'bob@x.test');   check('register bob', r.status_code == 200)

r = register('carolfree', 'carol@x.test'); check('register carol', r.status_code == 200)


with app.app_context():
    bob = User.query.filter_by(username='bobpaid').first()
    bob.tier = 'Expanded'
    db.session.commit()

r = login('alice', ); check('login alice', b'Logout' in r.data or b'inbox' in r.data.lower())


print('== 2. admin logs in, broadcasts to paid users only ==')
r = login('rootadmin', 'AdminPass!23')
check('admin login -> /admin allowed', client.get('/admin').status_code == 200)
r = client.post('/admin/messages/new', data={
    'subject': 'New feature: drag & drop ordering',
    'body': 'Paid users can now reorder links by dragging.',
    'category': 'announcement',
    'severity': 'info',
    'audience': 'paid',
    'action_url': '', 'action_label': ''},
    follow_redirects=True)
check('broadcast POST delivered', b'delivered to 1 recipient' in r.data.lower()
      or b'Delivered' in r.data)

with app.app_context():
    msg = Message.query.filter_by(category='announcement').first()
    check('announcement message exists', msg is not None)
    recipients = {m.user_id for m in MessageRead.query.filter_by(message_id=msg.id)}
    alice_id = User.query.filter_by(username='alice').first().id
    carol_id = User.query.filter_by(username='carolfree').first().id
    check('paid-only audience correct (bob in, alice/carol out)',
          recipients and alice_id not in recipients and carol_id not in recipients)

print('== 3. unread badge for paid user ==')

login('bobpaid')
r = client.get('/dashboard')
badge = re.search(r'inbox[^>]*>.{0,200}?(\d+)', r.data.decode(), re.S | re.I)
check('inbox link present with unread count 1',
      b'/inbox' in r.data and (b'>1<' in r.data or b'"1"' in r.data or (badge and badge.group(1) == '1')))
with app.app_context():
    bob = User.query.filter_by(username='bobpaid').first()
    check('User.unread_messages == 1', bob.unread_messages == 1)

r = client.get('/inbox')
check('inbox lists announcement', b'drag & drop' in r.data or b'drag' in r.data.lower())
check('unread indicator rendered', b'fw-bold' in r.data or b'badge' in r.data)

print('== 4. mark read + archive ==')
with app.app_context():
    mid = msg.id
r = client.post(f'/inbox/{mid}', data={'mark_read': '1'}, follow_redirects=True)
check('open message marks it read', r.status_code == 200)
with app.app_context():
    mr = MessageRead.query.filter_by(message_id=mid,
                                     user_id=User.query.filter_by(username='bobpaid').first().id).first()
    check('is_read True + read_at set', mr.is_read and mr.read_at is not None)
    bob = User.query.filter_by(username='bobpaid').first()
    check('unread count back to 0', bob.unread_messages == 0)
r = client.post(f'/inbox/archive/{mid}', follow_redirects=True)
check('archive works', r.status_code == 200)
r = client.get('/inbox')
check('archived msg hidden from main inbox', b'drag' not in r.data.lower())
r = client.get('/inbox?view=archived')
check('visible in archived view', b'drag' in r.data.lower())
r = client.post(f'/inbox/unarchive/{mid}', follow_redirects=True)
r = client.get('/inbox')
check('unarchive restores it', b'drag' in r.data.lower())

print('== 5. privacy: alice cannot open bob\'s message ==')

login('alice')
r = client.get(f'/inbox/{mid}')
check('non-recipient gets 404/redirect', r.status_code in (404, 302))


print('== 6. link checker: broken link -> notification, cooldown dedupe ==')
with app.app_context():
    alice = User.query.filter_by(username='alice').first()
    lk = Link(url='https://example.com/dead', title='Dead blog',
              user_id=alice.id, is_active=True)
    db.session.add(lk); db.session.commit()
    link_id = lk.id

fake_broken = (True, 404)
fake_ok = (False, 200)

import check_links

with patch.object(check_links, 'check_url', lambda url: fake_broken):
    rc = check_links.run()
check('run() exit 0', rc == 0)
with app.app_context():
    # Re-query inside this context: objects from earlier `with` blocks are
    # detached once their session scope ends.
    alice_id = User.query.filter_by(username='alice').first().id
    res = LinkCheckResult.query.filter_by(link_id=link_id).first()
    check('LinkCheckResult recorded broken w/ 404', res.is_broken and res.http_status == 404)
    n_msgs = Message.query.filter_by(source='link_check').count()
    check('link_check message sent (1)', n_msgs == 1)
    lc_msg = Message.query.filter_by(source='link_check').first()
    mr = MessageRead.query.filter_by(message_id=lc_msg.id, user_id=alice_id).first()
    check('delivered to alice', mr is not None and not mr.is_read)

# Second run same day -> cooldown suppresses duplicate
with patch.object(check_links, 'check_url', lambda url: fake_broken):
    check_links.run()
with app.app_context():
    n_msgs = Message.query.filter_by(source='link_check').count()
    check('cooldown dedupes second notification (still 1)', n_msgs == 1)

# Shorten cooldown to 0 days via Setting, run again -> should notify again
with app.app_context():
    Setting.set('link_check_notify_days', '0') if hasattr(Setting, 'set') else None
if not hasattr(Setting, 'set'):
    with app.app_context():
        s = Setting.query.filter_by(key='link_check_notify_days').first()
        if s: s.value = '0'
        else: db.session.add(Setting(key='link_check_notify_days', value='0'))
        db.session.commit()
with patch.object(check_links, 'check_url', lambda url: fake_broken):
    check_links.run()
with app.app_context():
    n_msgs = Message.query.filter_by(source='link_check').count()
    check('after cooldown expiry, new report sent (2)', n_msgs == 2)

# Recovery resets clocks
with patch.object(check_links, 'check_url', lambda url: fake_ok):
    check_links.run()
with app.app_context():
    res = LinkCheckResult.query.filter_by(link_id=link_id).first()
    check('recovered: is_broken False, clocks reset',
          not res.is_broken and res.broken_since is None and res.last_notified_at is None)

print('== 7. recall (admin deletes broadcast) ==')
login('rootadmin', 'AdminPass!23')
with app.app_context():
    ann_id = Message.query.filter_by(category='announcement').first().id
r = client.post(f'/admin/messages/delete/{ann_id}', follow_redirects=True)
check('recall POST ok', r.status_code == 200)
with app.app_context():
    check('message gone', db.session.get(Message, ann_id) is None)
    check('its deliveries gone', MessageRead.query.filter_by(message_id=ann_id).count() == 0)

print('== 8. direct message from users list + tos category ==')
with app.app_context():
    carol_id = User.query.filter_by(username='carolfree').first().id
r = client.post(f'/admin/messages/new/{carol_id}', data={
    'subject': 'Content review notice',
    'body': 'One of your links violates our ToS. Please remove it.',
    'category': 'tos_violation', 'severity': 'critical',
    'audience': 'one', 'action_url': '', 'action_label': ''},
    follow_redirects=True)
check('direct DM delivered', r.status_code == 200)

login('carolfree')
r = client.get('/inbox')
check('carol sees urgent ToS notice', b'Content review' in r.data and b'danger' in r.data)
with app.app_context():
    carol = User.query.filter_by(username='carolfree').first()
    check('carol unread == 1', carol.unread_messages == 1)

print('== 9. validation: empty subject rejected ==')
login('rootadmin', 'AdminPass!23')   # carol isn't an admin -> /admin redirects; compose as admin
r = client.post('/admin/messages/new', data={'subject': '', 'body': 'x',
                                             'category': 'note', 'audience': 'all'},
                follow_redirects=True)
check('empty subject -> 400 re-render (not sent)', r.status_code == 400
      and b'required' in r.data.lower())
with app.app_context():
    check('no message created by invalid POST',
          Message.query.filter_by(subject='').count() == 0)

print(f'\nRESULT: {PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
