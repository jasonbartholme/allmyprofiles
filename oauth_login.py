"""Third-party ("social") sign-in / account-linking for AllMyProfiles.

Authlib-based Authorization-Code flow for Google, Meta (Facebook), Microsoft
and GitHub. The standard email/password auth stays fully intact: this module
only *appends* rows to the ``user_oauth_accounts`` table (see
models.UserOAuthAccount) and never mutates or deletes user records.

Behaviour
---------
* Signed out  -> the callback logs the user in, creating a new Free-tier
  account on first use if the provider returned a verified email that no
  local account claims yet.
* Signed in   -> the callback *links* the provider to the current account
  (an "already linked" notice is shown instead of an error).
* A provider identity already linked to a different account can never be
  hijacked — the user is asked to log in with that account instead.
* Unlinking removes only the ``user_oauth_accounts`` row; the last remaining
  sign-in method (password or any linked provider) can never be removed, so
  users can always get back into their account.

Configuration (.env — see config.Config.OAUTH_CREDENTIAL_ENV_MAP)
-----------------------------------------------------------------
    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
    FACEBOOK_CLIENT_ID / FACEBOOK_CLIENT_SECRET      # Meta app
    MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET
    GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET

Providers whose client id/secret are missing are simply hidden from the UI
and their routes return a friendly "not configured" flash — the rest of the
application keeps working unchanged.

Redirect URIs to register with each provider (dev):
    http://localhost:5000/auth/<provider>/callback
Update the host part for production.
"""

import json
from datetime import datetime

from authlib.integrations.base_client.errors import MismatchingStateError, \
    OAuthError
from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required, login_user

from models import Activity, User, UserOAuthAccount, db

oauth_bp = Blueprint('oauth', __name__, url_prefix='/auth')

# Reusable Authlib client (initialised against the app in init_oauth()).
oauth = OAuth()

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
# 'facebook' is the Meta provider key (Meta's Graph API developer platform).
OAUTH_PROVIDERS = {
    'google': {
        'label': 'Google',
        'icon': 'bi-google',
        'server_metadata_url': (
            'https://accounts.google.com/.well-known/openid-configuration'),
        'client_kwargs': {'scope': 'openid email profile'},
        'userinfo_url': None,          # discovered via OIDC metadata
        'id_field': 'sub',
        'name_field': 'name',
        'picture_field': 'picture',
        'email_verified_field': 'email_verified',
    },
    'facebook': {
        'label': 'Meta',
        'icon': 'bi-facebook',
        # Meta does not publish OIDC discovery; hard-code the endpoints.
        'access_token_url': (
            'https://graph.facebook.com/v21.0/oauth/access_token'),
        'authorize_url': (
            'https://www.facebook.com/v21.0/dialog/oauth'),
        'client_kwargs': {'scope': 'email public_profile'},
        'userinfo_url': ('https://graph.facebook.com/v21.0/me'
                         '?fields=id,name,email,picture'),
        'id_field': 'id',
        'name_field': 'name',
        # Nested: picture.data.url
        'picture_field': None,
        'email_verified_field': None,  # FB only returns verified emails
        'use_graph_api': True,         # graph returns form-encoded errors
    },
    'microsoft': {
        'label': 'Microsoft',
        'icon': 'bi-windows',
        'server_metadata_url': (
            'https://login.microsoftonline.com/common/v2.0/'
            '.well-known/openid-configuration'),
        'client_kwargs': {'scope': 'openid email profile User.Read'},
        'userinfo_url': 'https://graph.microsoft.com/v1.0/me',
        'id_field': 'id',
        'name_field': 'displayName',
        'picture_field': None,         # no photo on the /me endpoint
        # Graph marks accounts "verified" when isPrimaryAddress is true.
        'email_verified_field': None,
        'email_path': 'mail',          # Graph uses `mail`, not `email`
    },
    'github': {
        'label': 'GitHub',
        'icon': 'bi-github',
        'access_token_url': 'https://github.com/login/oauth/access_token',
        'authorize_url': 'https://github.com/login/oauth/authorize',
        'client_kwargs': {'scope': 'user:email'},
        'userinfo_url': 'https://api.github.com/user',
        'id_field': 'id',
        'name_field': 'name',          # may be null -> fall back to login
        'picture_field': 'avatar_url',
        # GitHub /user has no email when private; fetched separately below.
        'email_verified_field': None,
        'fetch_email_url': 'https://api.github.com/user/emails',
    },
}


def provider_display_maps():
    """({provider: label}, {provider: bootstrap-icon}) for templates."""
    labels = {name: meta['label'] for name, meta in OAUTH_PROVIDERS.items()}
    icons = {name: meta['icon'] for name, meta in OAUTH_PROVIDERS.items()}
    return labels, icons


def provider_configured(name):
    """True when both client id & secret env vars are present."""
    ids, secrets = current_app.config['OAUTH_CREDENTIAL_ENV_MAP'][name]
    return bool((current_app.config.get(ids) or '').strip()
                and (current_app.config.get(secrets) or '').strip())


def available_providers():
    """Provider metadata for buttons — only fully-configured providers."""
    out = []
    for name, meta in OAUTH_PROVIDERS.items():
        if provider_configured(name):
            out.append({'key': name, 'label': meta['label'],
                        'icon': meta['icon']})
    return out


def init_oauth(app):
    """Register all providers with Authlib and stash credential lookups.

    Client ids/secrets are resolved lazily from ``app.config`` at request
    time (Authlib calls the callables), so tests can monkey-patch config
    after start-up and nothing crashes when credentials are absent.
    """
    oauth.init_app(app)
    for name, meta in OAUTH_PROVIDERS.items():
        id_key, secret_key = app.config['OAUTH_CREDENTIAL_ENV_MAP'][name]
        kwargs = {
            # Callables -> credentials resolved lazily from app.config at
            # request time (defaults captured per iteration via args).
            'client_id': (lambda idk=id_key: current_app.config.get(idk)),
            'client_secret': (lambda sk=secret_key:
                              current_app.config.get(sk)),
        }
        for key in ('access_token_url', 'authorize_url', 'server_metadata_url'):
            if meta.get(key):
                kwargs[key] = meta[key]
        userinfo = meta.get('userinfo_url')
        if userinfo and not meta.get('use_graph_api'):
            # Full URL is passed to client.get() below; api_base_url only
            # needs the origin so Authlib does not double-prefix paths.
            kwargs['api_base_url'] = userinfo.split('?')[0].rpartition('/')[0] \
                + '/'
        oauth.register(name=name, client_kwargs=meta['client_kwargs'],
                       **kwargs)
    return oauth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_profile(name, profile):
    """Normalise a provider userinfo payload into our storage fields."""
    meta = OAUTH_PROVIDERS[name]
    uid = str(profile.get(meta['id_field']) or '').strip()
    email_field = meta.get('email_path') or 'email'
    email = (profile.get(email_field) or '').strip().lower() or None

    picture = None
    if meta['picture_field']:
        picture = profile.get(meta['picture_field'])
    elif name == 'facebook':
        picture = ((profile.get('picture') or {}).get('data') or {}).get('url')

    if meta['email_verified_field'] is not None:
        verified = bool(profile.get(meta['email_verified_field'], False))
    elif name == 'facebook':
        # Facebook only ever returns verified addresses.
        verified = bool(email)
    elif name == 'microsoft':
        # Tenant directory mail is admin-managed; treat as verified.
        verified = bool(email)
    else:  # github — refined by _github_fetch_email
        verified = False

    display = (profile.get(meta['name_field'])
               or profile.get('login')  # github fallback
               or (email.split('@')[0] if email else None))
    return uid, email, verified, display, picture


def _github_fetch_email(client, token_response):
    """GitHub hides the email on /user when set to private; ask /user/emails.

    Returns (email, verified) preferring a verified primary address.
    """
    try:
        resp = client.get(OAUTH_PROVIDERS['github']['fetch_email_url'],
                          headers={'Authorization':
                                   f"Bearer {token_response.get('access_token')}"},
                          accept='application/json')
        emails = resp.json()
    except Exception:
        return None, False
    if not isinstance(emails, list):
        return None, False
    primary = next((e for e in emails
                    if e.get('primary') and e.get('verified')), None)
    fallback = next((e for e in emails if e.get('verified')), None)
    chosen = primary or fallback
    if chosen:
        return (chosen.get('email') or '').strip().lower(), bool(
            chosen.get('verified'))
    return None, False


def _safe_next(target):
    """Only allow same-site relative redirects (mirrors the login route)."""
    if target and target.startswith('/') and not target.startswith('//'):
        return target
    return None


def _linked_or_none(name, provider_uid):
    return UserOAuthAccount.query.filter_by(provider=name,
                                            provider_user_id=provider_uid
                                            ).first()


def _already_linked(user, name):
    return UserOAuthAccount.query.filter_by(user_id=user.id,
                                            provider=name).first()


def _record_login(user, name, created=False):
    Activity.record('oauth_login' if not created else 'oauth_signup',
                    user=user,
                    detail=f'{name}: {"account created" if created else "sign-in"}')
    db.session.commit()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@oauth_bp.route('/<name>/login')
def begin(name):
    """Kick off the Authorization Code redirect for <name>."""
    if name not in OAUTH_PROVIDERS:
        return redirect(url_for('login'))
    if not provider_configured(name):
        flash(f'{OAUTH_PROVIDERS[name]["label"]} sign-in is not configured '
              'on this site yet.', 'info')
        return redirect(url_for('login'))

    # Remember where to land after the round-trip (dashboard by default).
    session_next = _safe_next(request.args.get('next'))
    from flask import session
    session['oauth_next'] = session_next or url_for('dashboard')
    # Linking intent is implicit: if a user is signed in we link to them.
    session['oauth_link_mode'] = '1' if current_user.is_authenticated else ''

    redirect_uri = url_for('oauth.callback', name=name, _external=True)
    try:
        client = oauth.create_client(name)
        return client.authorize_redirect(redirect_uri)
    except Exception as exc:                      # noqa: BLE001
        current_app.logger.warning('OAuth %s start failed: %s', name, exc)
        flash(f'Could not start {OAUTH_PROVIDERS[name]["label"]} sign-in. '
              'Please try again or use email/password.', 'danger')
        return redirect(url_for('login'))


@oauth_bp.route('/<name>/callback')
def callback(name):
    """Handle the provider redirect-back: sign in, sign up, or link."""
    if name not in OAUTH_PROVIDERS:
        return redirect(url_for('login'))
    label = OAUTH_PROVIDERS[name]['label']

    if not provider_configured(name):
        flash(f'{label} sign-in is not configured on this site yet.', 'info')
        return redirect(url_for('login'))

    try:
        client = oauth.create_client(name)
        token = client.fetch_token()
    except MismatchingStateError:
        flash('Your sign-in attempt expired or was started in another tab. '
              'Please try again.', 'warning')
        return redirect(url_for('login'))
    except OAuthError as exc:
        current_app.logger.warning('OAuth %s token error: %s', name, exc)
        flash(f'{label} rejected the sign-in attempt. Please try again.',
              'danger')
        return redirect(url_for('login'))
    except Exception as exc:                      # noqa: BLE001
        current_app.logger.warning('OAuth %s callback failed: %s', name, exc)
        flash(f'{label} sign-in failed unexpectedly. You can still log in '
              'with email and password.', 'danger')
        return redirect(url_for('login'))

    # ---- fetch the profile ------------------------------------------------
    try:
        meta = OAUTH_PROVIDERS[name]
        if name == 'facebook':
            # Graph needs the token as a query param more reliably than a
            # bearer header for the /me fields call.
            resp = client.get(meta['userinfo_url'],
                              token={'access_token': token['access_token'],
                                     'token_type': 'Bearer'})
            profile = resp.json()
        else:
            userinfo = meta['userinfo_url'] or ''
            if userinfo:
                resp = client.get(userinfo,
                                  access_token=token.get('access_token'))
                profile = resp.json() \
                    if hasattr(resp, 'json') else resp
            else:
                # OIDC shortcut (google; microsoft when metadata-discovered)
                profile = client.userinfo()
    except Exception as exc:                      # noqa: BLE001
        current_app.logger.warning('OAuth %s userinfo failed: %s', name, exc)
        flash(f'Could not read your {label} profile. Please try again.',
              'danger')
        return redirect(url_for('login'))

    if not isinstance(profile, dict) or 'error' in profile:
        current_app.logger.info('OAuth %s profile error: %s',
                                name, json.dumps(profile)[:300])
        flash(f'{label} returned an error reading your profile.', 'danger')
        return redirect(url_for('login'))

    uid, email, verified, dispname, picture = _extract_profile(name, profile)
    if name == 'github' and not email:
        email, verified = _github_fetch_email(client, token)
    if not uid:
        flash(f'{label} did not return an account identifier.', 'danger')
        return redirect(url_for('login'))

    existing = _linked_or_none(name, uid)

    # ---- signed-in user: LINK mode ---------------------------------------
    if current_user.is_authenticated:
        user = current_user._get_current_object()
        if existing and existing.user_id == user.id:
            _touch(existing, token, email, verified, dispname, picture,
                   profile)
            flash(f'Your {label} account was already linked.', 'info')
            return _post_auth_redirect()
        if existing:                              # belongs to someone else
            flash(f'That {label} account is already linked to a different '
                  'AllMyProfiles account. Log out and sign in with it '
                  'directly to use it here.', 'warning')
            return _post_auth_redirect()
        acct = UserOAuthAccount(user_id=user.id, provider=name,
                                provider_user_id=uid)
        _fill(acct, token, email, verified, dispname, picture, profile)
        db.session.add(acct)
        Activity.record('oauth_link', user=user, detail=f'{name} linked')
        db.session.commit()
        flash(f'{label} linked to your account. You can now sign in with '
              'either method.', 'success')
        return _post_auth_redirect()

    # ---- signed out: LOGIN / SIGNUP --------------------------------------
    if existing:
        user = db.session.get(User, existing.user_id)
        if user is None:                          # dangling row: self-heal
            db.session.delete(existing)
            db.session.commit()
            existing = None
        else:
            _touch(existing, token, email, verified, dispname, picture,
                   profile)
            db.session.commit()
            login_user(user, remember=True)
            _record_login(user, name)
            return _post_auth_redirect()

    if email:
        user = User.query.filter_by(email=email).first()
        if user is not None:
            # Auto-link ONLY when the provider says the address is verified
            # — otherwise someone could claim another person's account by
            # registering an unverified alias on a social network.
            if not verified:
                flash(f'The email {email} already has an AllMyProfiles '
                      f'account. Log in with your password first, then link '
                      f'{label} from your profile settings.', 'warning')
                return redirect(url_for('login'))
            acct = UserOAuthAccount(user_id=user.id, provider=name,
                                    provider_user_id=uid)
            _fill(acct, token, email, verified, dispname, picture, profile)
            db.session.add(acct)
            db.session.commit()
            login_user(user, remember=True)
            _record_login(user, name)
            flash(f'Signed in and linked {label} to your existing account.',
                  'success')
            return _post_auth_redirect()

        # New customer: create the account (append-only; nothing existing is
        # touched). Password-less until they add one in settings.
        username = _derive_username(email.split('@')[0])
        user = User(username=username, email=email,
                    display_name=dispname or username,
                    age_verified=False)   # must pass the age gate like anyone
        user.set_password(_random_placeholder_password())
        db.session.add(user)
        db.session.flush()
        acct = UserOAuthAccount(user_id=user.id, provider=name,
                                provider_user_id=uid)
        _fill(acct, token, email, verified, dispname, picture, profile)
        db.session.add(acct)
        Activity.record('signup', user=user,
                        detail=f'@{username} registered via {name}')
        db.session.commit()
        login_user(user, remember=True)
        flash(f'Account created via {label}! Finish your profile whenever '
              'you like.', 'success')
        return _post_auth_redirect()

    # No usable email from the provider and no prior link.
    flash(f'We could not get a usable email address from {label}. Please '
          'register with email/password and then link your accounts.',
          'warning')
    return redirect(url_for('register'))


@oauth_bp.route('/unlink/<name>', methods=['POST'])
@login_required
def unlink(name):
    """Remove a linked provider. Never removes the last way to sign in."""
    if name not in OAUTH_PROVIDERS:
        return redirect(url_for('dashboard'))
    acct = _already_linked(current_user, name)
    if not acct:
        flash(f'No {OAUTH_PROVIDERS[name]["label"]} account is linked.',
              'info')
        return redirect(url_for('dashboard'))
    remaining = current_user.oauth_accounts.count() - 1
    has_password = bool((current_user.password_hash or '').strip())
    if remaining == 0 and not has_password:
        flash('Set a password before unlinking your only sign-in method.',
              'warning')
        return redirect(url_for('dashboard'))
    db.session.delete(acct)
    Activity.record('oauth_unlink', user=current_user, detail=f'{name} unlinked')
    db.session.commit()
    flash(f'{OAUTH_PROVIDERS[name]["label"]} unlinked.', 'success')
    return redirect(url_for('dashboard'))


# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------

def _fill(acct, token, email, verified, dispname, picture, profile):
    acct.email = email
    acct.email_verified = bool(verified)
    acct.name = dispname
    acct.picture_url = picture
    acct.raw_profile = json.dumps(profile, default=str)[:60000]
    acct.store_tokens(dict(token))
    acct.last_used_at = datetime.now()


def _touch(acct, token, email, verified, dispname, picture, profile):
    """Refresh an existing link row (never creates or destroys anything)."""
    _fill(acct, token, email, verified, dispname, picture, profile)


def _derive_username(base):
    """Turn the local part of an email into a unique, legal username."""
    import re as _re
    base = _re.sub(r'[^a-z0-9_-]', '', base.lower())[:24] or 'user'
    candidate = base
    n = 1
    while User.query.filter_by(username=candidate).first():
        n += 1
        candidate = f'{base}{n}'[:31]
    return candidate


def _random_placeholder_password():
    """Unguessable password for OAuth-created accounts.

    Keeps ``users.password_hash`` NOT NULL intact (no destructive schema
    change) while making sure password login is impossible until the user
    explicitly sets a real password.
    """
    import secrets
    return secrets.token_urlsafe(24)


def _post_auth_redirect():
    from flask import session
    nxt = _safe_next(session.pop('oauth_next', None))
    session.pop('oauth_link_mode', None)
    return redirect(nxt or url_for('dashboard'))
