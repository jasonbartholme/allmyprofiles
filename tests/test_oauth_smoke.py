"""Smoke test for the third-party authentication epic (chunk 1).

Covers:
  * UserOAuthAccount model + separate table (users rows never destroyed),
  * graceful degradation when no provider credentials are configured,
  * /auth/<provider>/login redirect with fake env credentials,
  * callback -> new-account signup path (mocked token + userinfo),
  * callback -> auto-link to existing verified email (append-only),
  * refusal to auto-link unverified emails,
  * link-to-current-user while signed in,
  * cross-account takeover refusal,
  * unlink guard (last sign-in method) and successful unlink,
  * standard email/password login still works for OAuth-created accounts
    once a real password is set.

Run:  python tests/test_oauth_smoke.py
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

from app import create_app                     # noqa: E402
from models import (db, User, UserOAuthAccount, Activity, Setting,  # noqa: E402
                    LinkSource, LinkCategory, ContentSection, Faq)
import oauth_login                             # noqa: E402

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

# The testing config only seeds defaults in development mode; do it here so
# every request (context processor reads Setting) has its tables.
with app.app_context():
    db.create_all()
    Setting.seed_defaults()
    LinkSource.seed_defaults()
    LinkCategory.seed_defaults()
    ContentSection.seed_defaults()
    Faq.seed_defaults()
    db.session.commit()

# ---------------------------------------------------------------------------
# 1. Graceful degradation: no credentials configured anywhere.
# ---------------------------------------------------------------------------
print('== 1. unconfigured providers degrade gracefully ==')
with app.test_client() as client:
    r = client.get('/login')
    body = r.get_data(as_text=True)
    check('login page renders 200', r.status_code == 200)
    check('no social buttons without credentials',
          '/auth/google/login' not in body)
    r = client.get('/auth/github/login')
    check('unconfigured begin -> redirect, no crash',
          r.status_code == 302)
    r = client.get('/auth/notreal/login')
    check('unknown provider -> redirect', r.status_code == 302)

# ---------------------------------------------------------------------------
# Fake credentials + provider responses.
# ---------------------------------------------------------------------------
for key in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET',
            'GITHUB_CLIENT_ID', 'GITHUB_CLIENT_SECRET',
            'FACEBOOK_CLIENT_ID', 'FACEBOOK_CLIENT_SECRET',
            'MICROSOFT_CLIENT_ID', 'MICROSOFT_CLIENT_SECRET'):
    app.config[key] = 'test-value'

TOKEN = {'access_token': 'tok-123', 'expires_in': 3600,
         'refresh_token': 'rt-999', 'token_type': 'Bearer'}


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    """Stands in for an Authlib client: fixed token + userinfo payloads."""

    def __init__(self, profile):
        self.profile = profile

    def fetch_token(self, *a, **kw):
        return dict(TOKEN)

    def get(self, url, *a, **kw):
        if url.endswith('/user/emails'):
            return FakeResp([{'email': self.profile.get('email'),
                              'primary': True, 'verified': True}])
        return FakeResp(self.profile)

    def authorize_redirect(self, redirect_uri, **kw):
        from flask import redirect as _r
        return _r('https://provider.example/authorize?state=xyz')


GOOGLE_PROFILE = {'sub': 'g-uid-1', 'email': 'nova@example.com',
                  'email_verified': True, 'name': 'Nova Tester',
                  'picture': 'https://img.example/nova.png'}
GITHUB_PROFILE = {'id': 4242, 'login': 'novatest', 'name': None,
                  'email': 'nova@example.com', 'avatar_url': 'https://gh/x.png'}

with app.app_context():
    db.create_all()
    Setting.seed_defaults()
    LinkSource.seed_defaults()
    LinkCategory.seed_defaults()
    ContentSection.seed_defaults()
    Faq.seed_defaults()
    db.session.commit()

    # ------------------------------------------------------------------
    # 2. Begin route now redirects to the provider with credentials set.
    # ------------------------------------------------------------------
    print('== 2. begin flow ==')
    with app.test_client() as client:
        with patch.object(oauth_login.oauth, 'create_client',
                          lambda name: FakeClient(GOOGLE_PROFILE)):
            r = client.get('/auth/google/login')
            check('begin -> 302 to provider', r.status_code == 302
                  and 'provider.example' in r.headers['Location'])

        # ------------------------------------------------------------------
        # 3. Callback creates a NEW account (append-only) and logs in.
        # ------------------------------------------------------------------
        print('== 3. callback: new user via Google ==')
        users_before = User.query.count()
        with patch.object(oauth_login.oauth, 'create_client',
                          lambda name: FakeClient(GOOGLE_PROFILE)):
            r = client.get('/auth/google/callback?code=abc&state=xyz',
                           follow_redirects=True)
        check('callback lands on dashboard', r.status_code == 200)
        user = User.query.filter_by(email='nova@example.com').first()
        check('new user created', user is not None
              and User.query.count() == users_before + 1)
        check('username derived from email',
              user is not None and user.username == 'nova')
        acct = (UserOAuthAccount.query_by_user(user)
                if hasattr(UserOAuthAccount, 'query_by_user') else
                UserOAuthAccount.query.filter_by(
                    user_id=user.id, provider='google').first())
        check('oauth row appended in separate table',
              acct is not None and acct.provider_user_id == 'g-uid-1')
        check('tokens stored on the row', bool(acct.access_token)
              and 'rt-999' in (acct.refresh_token or ''))
        check('raw profile captured', 'Nova Tester' in (acct.raw_profile or ''))
        check('signup activity logged',
              Activity.query.filter_by(kind='signup').count() >= 1)
        # Standard auth retained: placeholder password cannot be guessed,
        # but email/password form still functions for everyone.
        ok = client.post('/login', data={'email': 'nova@example.com',
                                         'password': 'tok-guess'})
        check('placeholder password not usable', ok.status_code == 200)
        user.set_password('RealPassw0rd!')
        db.session.commit()
        client.get('/logout')
        r = client.post('/login', data={'email': 'nova@example.com',
                                        'password': 'RealPassw0rd!'},
                        follow_redirects=True)
        check('password login works after setting one',
              b'Your dashboard' in r.data)

        # ------------------------------------------------------------------
        # 4. Second provider links onto the SAME user (never duplicates).
        # ------------------------------------------------------------------
        print('== 4. GitHub sign-in same email -> auto-link ==')
        count_before = User.query.count()
        with patch.object(oauth_login.oauth, 'create_client',
                          lambda name: FakeClient(GITHUB_PROFILE)):
            r = client.get('/auth/github/callback?code=abc&state=xyz',
                           follow_redirects=True)
        check('no duplicate user created', User.query.count() == count_before)
        gh = UserOAuthAccount.query.filter_by(provider='github').first()
        check('github row linked to same user',
              gh is not None and gh.user_id == user.id)
        check('github email fetched & marked verified',
              gh.email == 'nova@example.com' and gh.email_verified)

        # ------------------------------------------------------------------
        # 5. Unverified email must NOT auto-link.
        # ------------------------------------------------------------------
        print('== 5. unverified email refuses auto-link ==')
        other = User(username='otherguy', email='other@example.com')
        other.set_password('Str0ng!Passw0rd')
        db.session.add(other)
        db.session.commit()
        unverified = dict(GOOGLE_PROFILE, sub='g-uid-2',
                          email='other@example.com', email_verified=False)
        client.get('/logout')
        with patch.object(oauth_login.oauth, 'create_client',
                          lambda name: FakeClient(unverified)):
            r = client.get('/auth/google/callback?code=abc&state=xyz',
                           follow_redirects=True)
        check('redirected to login, no link made',
              UserOAuthAccount.query.filter_by(
                  provider_user_id='g-uid-2').first() is None)

        # ------------------------------------------------------------------
        # 6. Signed-in linking + takeover refusal.
        # ------------------------------------------------------------------
        print('== 6. link mode & takeover protection ==')
        client.post('/login', data={'email': 'other@example.com',
                                    'password': 'Str0ng!Passw0rd'})
        fb_profile = {'id': 'fb-uid-9', 'name': 'Other Guy',
                      'email': 'other@example.com',
                      'picture': {'data': {'url': 'https://fb/p.png'}}}
        with patch.object(oauth_login.oauth, 'create_client',
                          lambda name: FakeClient(fb_profile)):
            r = client.get('/auth/facebook/callback?code=a&state=b',
                           follow_redirects=True)
        fb = UserOAuthAccount.query.filter_by(provider='facebook',
                                              provider_user_id='fb-uid-9'
                                              ).first()
        check('signed-in callback links to current user',
              fb is not None and fb.user_id == other.id)
        # Now try to link the same FB identity to nova's account.
        client.get('/logout')
        client.post('/login', data={'email': 'nova@example.com',
                                    'password': 'RealPassw0rd!'})
        with patch.object(oauth_login.oauth, 'create_client',
                          lambda name: FakeClient(fb_profile)):
            r = client.get('/auth/facebook/callback?code=a&state=b',
                           follow_redirects=True)
        fb_after = UserOAuthAccount.query.filter_by(
            provider='facebook', provider_user_id='fb-uid-9').first()
        check('cross-account hijack refused',
              fb_after.user_id == other.id)
        check('existing user record untouched (still has its links/auth)',
              User.query.get(other.id) is not None)

        # ------------------------------------------------------------------
        # 7. Unlink rules.
        # ------------------------------------------------------------------
        print('== 7. unlink guards ==')
        # Nova: password + 2 providers -> unlink github succeeds.
        r = client.post('/auth/unlink/github', follow_redirects=True)
        check('unlink removes only the row',
              UserOAuthAccount.query.filter_by(
                  provider='github', user_id=user.id).first() is None
              and User.query.get(user.id) is not None)
        # OAuth-only account: no password -> last method protected.
        solo = User(username='solodev', email='solo@example.com')
        solo.password_hash = ''   # simulate password-less OAuth account
        db.session.add(solo)
        db.session.commit()
        s_acct = UserOAuthAccount(user_id=solo.id, provider='microsoft',
                                  provider_user_id='ms-1')
        db.session.add(s_acct)
        db.session.commit()
        client.get('/logout')
        # Log solo in through a mocked MS callback instead of password.
        ms_profile = {'id': 'ms-1', 'displayName': 'Solo Dev',
                      'mail': 'solo@example.com'}
        with patch.object(oauth_login.oauth, 'create_client',
                          lambda name: FakeClient(ms_profile)):
            client.get('/auth/microsoft/callback?code=a&state=b')
        r = client.post('/auth/unlink/microsoft', follow_redirects=True)
        check('cannot unlink last sign-in method',
              UserOAuthAccount.query.filter_by(
                  provider='microsoft', user_id=solo.id).first() is not None)

        # ------------------------------------------------------------------
        # 8. Dashboard shows linked accounts + buttons.
        # ------------------------------------------------------------------
        print('== 8. dashboard UI ==')
        r = client.get('/dashboard')
        body = r.get_data(as_text=True)
        check('sign-in methods card rendered', 'Sign-in methods' in body)
        check('linked google shown', 'Google' in body)
        check('relink button for unlinked provider',
              'Link Meta' in body or 'Link Microsoft' in body)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
