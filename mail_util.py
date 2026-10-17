"""
Outbound email via SMTP (Gmail or any provider) — admin-configurable.

Design goals (same philosophy as the OAuth epic): auth plumbing must never
get in the way of the application.

  * Credentials live in the ``Setting`` key/value table, editable from
    Admin -> Settings -> "Email delivery (SMTP)". Environment variables
    (``MAIL_*``) act as a fallback/default for hosts that prefer .env files.
  * When nothing is configured, every send is a graceful no-op: callers get
    ``(False, reason)`` back and the app behaves exactly as before (inbox
    records are still created; nothing crashes).
  * The password field round-trips like Stripe secrets elsewhere in this
    app: an existing stored value is shown masked; leaving the field blank
    keeps the current value; typing over it replaces it.

Gmail specifics
---------------
Plain username/password auth does not work with Gmail anymore. Use one of:

  1. An App Password (simplest): https://myaccount.google.com/apppasswords
     -> smtp.gmail.com:465 with SSL, login = your address, password = the
     16-character app password (spaces optional). Requires 2-Step
     Verification on the account.
  2. Regular account password + OAuth only — not supported here; App
     Password is the supported path.

Other providers work too: any host/port/SSL combination can be entered
(e.g. outlook.office365.com:587 STARTTLS, Mailgun/SendGrid SMTP relay on
port 2525 without TLS).
"""

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

# ---------------------------------------------------------------------------
# Setting keys (single source of truth; models.DEFAULT_SETTINGS mirrors these)
# ---------------------------------------------------------------------------

SMTP_KEYS = (
    'smtp_enabled',            # 'true'/'false' master switch
    'smtp_host',               # e.g. smtp.gmail.com
    'smtp_port',               # 465 (SSL) / 587 (STARTTLS) / 2525 (relay)
    'smtp_security',           # 'ssl' | 'starttls' | 'none'
    'smtp_username',           # full Gmail address
    'smtp_password',           # App Password (stored; masked in the UI)
    'mail_from',               # From: display address (defaults to username)
    'mail_from_name',          # From: display name, e.g. "AllMyProfiles"
)

# Env-var fallbacks so credentials can also be kept purely in .env files.
_ENV_FALLBACK = {
    'smtp_enabled': 'MAIL_ENABLED',
    'smtp_host': 'MAIL_SERVER',
    'smtp_port': 'MAIL_PORT',
    'smtp_security': 'MAIL_SECURITY',
    'smtp_username': 'MAIL_USERNAME',
    'smtp_password': 'MAIL_PASSWORD',
    'mail_from': 'MAIL_DEFAULT_SENDER',
    'mail_from_name': 'MAIL_SENDER_NAME',
}

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _setting(key):
    """Read one setting: DB row wins; otherwise MAIL_* env var; otherwise default.

    An empty string saved in the DB counts as "not set here", so a host can
    keep credentials purely in .env and the admin UI stays blank-friendly.

    Works with OR without an active Flask app context (background scripts,
    CLI commands, and tests may call send helpers outside a request); when
    no context exists we push one temporarily so the DB read succeeds.
    """
    import os
    from flask import current_app, has_app_context
    from models import db, Setting, DEFAULT_SETTINGS
    value = None

    def _read():
        nonlocal value
        try:
            obj = db.session.get(Setting, key)
            if obj is not None:
                value = obj.value
        except Exception:
            value = None

    if has_app_context():
        _read()
    else:
        try:
            from app import app as _flask_app
            with _flask_app.app_context():
                _read()
        except Exception:
            # No importable module-level app (e.g. factory-only deployment):
            # fall through to env/default resolution.
            pass
    if value:
        return value.strip()
    env = os.environ.get(_ENV_FALLBACK.get(key, ''), '')
    if env:
        return env.strip()
    return DEFAULT_SETTINGS.get(key, '')


def get_smtp_config():
    """Return the effective SMTP config dict (never raises).

    Returns BOTH key styles so every caller works unchanged:
      * short keys used internally: enabled(bool), host, port(int), security,
        username, password, mail_from, mail_from_name
      * full setting-name aliases used by app.py / templates: smtp_enabled,
        smtp_host, smtp_port, smtp_security, smtp_username, smtp_password
    Never raises.
    """
    cfg = {key.replace('smtp_', ''): _setting(key) for key in SMTP_KEYS}
    raw = (cfg.get('enabled') or '').strip().lower()
    cfg['enabled'] = raw in ('1', 'true', 'yes', 'on')
    try:
        cfg['port'] = int(cfg.get('port') or 0)
    except ValueError:
        cfg['port'] = 0
    sec = (cfg.get('security') or 'ssl').lower()
    cfg['security'] = sec if sec in ('ssl', 'starttls', 'none') else 'ssl'
    if not cfg['port']:
        cfg['port'] = 465 if cfg['security'] == 'ssl' else 587
    if not cfg['mail_from']:
        cfg['mail_from'] = cfg.get('username') or ''
    # Full-name aliases (values as stored strings/normalised above)
    cfg['smtp_enabled'] = 'true' if cfg['enabled'] else 'false'
    cfg['smtp_host'] = cfg['host']
    cfg['smtp_port'] = str(cfg['port'])
    cfg['smtp_security'] = cfg['security']
    cfg['smtp_username'] = cfg['username']
    cfg['smtp_password'] = cfg['password']
    return cfg


def is_configured(cfg=None):
    cfg = cfg or get_smtp_config()
    return bool(cfg['enabled'] and cfg['host'] and cfg['username']
                and cfg['password'] and EMAIL_RE.match(cfg['mail_from'] or ''))


def validate_form(form):
    """Validate admin-settings form values. Returns list of error strings."""
    errors = []
    enabled = bool(form.get('smtp_enabled'))
    host = (form.get('smtp_host') or '').strip()
    security = (form.get('smtp_security') or 'ssl').strip().lower()
    try:
        port = int((form.get('smtp_port') or '').strip() or 0)
    except ValueError:
        errors.append('SMTP port must be a number (e.g. 465 or 587).')
        port = 0
    username = (form.get('smtp_username') or '').strip()
    mail_from = (form.get('mail_from') or '').strip()
    if security not in ('ssl', 'starttls', 'none'):
        errors.append("SMTP security must be 'ssl', 'starttls', or 'none'.")
    if enabled:
        if not host:
            errors.append('SMTP server is required when email is enabled '
                          '(e.g. smtp.gmail.com).')
        if not (1 <= port <= 65535):
            errors.append('SMTP port must be between 1 and 65535.')
        if not username:
            errors.append('SMTP username is required (your Gmail address).')
        elif not EMAIL_RE.match(username):
            errors.append('SMTP username must be a valid email address.')
        if mail_from and not EMAIL_RE.match(mail_from):
            errors.append('"From" address must be a valid email address.')
    if port and not (1 <= port <= 65535):
        errors.append('SMTP port must be between 1 and 65535.')
    return errors


def save_from_form(form, keep_password_if_blank=True):
    """Persist the SMTP section of the admin settings form into Settings."""
    from models import Setting
    Setting.set_value('smtp_enabled', 'true' if form.get('smtp_enabled') else 'false')
    Setting.set_value('smtp_host', (form.get('smtp_host') or '').strip()[:120])
    Setting.set_value('smtp_port', (form.get('smtp_port') or '').strip()[:10])
    Setting.set_value('smtp_security',
                      (form.get('smtp_security') or 'ssl').strip().lower()[:10])
    Setting.set_value('smtp_username', (form.get('smtp_username') or '').strip()[:190])
    Setting.set_value('mail_from', (form.get('mail_from') or '').strip()[:190])
    Setting.set_value('mail_from_name', (form.get('mail_from_name') or '').strip()[:120])
    pwd = form.get('smtp_password') or ''
    if pwd.strip():
        Setting.set_value('smtp_password', pwd.strip())
    elif not keep_password_if_blank:
        Setting.set_value('smtp_password', '')


def mask(value):
    """Display-safe rendering of a stored secret (mirrors other admin UIs)."""
    if not value:
        return ''
    if len(value) <= 4:
        return '\u2022' * 8
    return value[:2] + '\u2022' * 8 + value[-2:]


def build_message(subject, body, cfg=None, to_addr=None, html=None,
                  from_addr=None, reply_to=None):
    """Assemble an EmailMessage (used by both real sends and test mode)."""
    cfg = cfg or get_smtp_config()
    msg = EmailMessage()
    msg['Subject'] = subject[:480]
    sender = from_addr or cfg['mail_from']
    msg['From'] = formataddr((cfg['mail_from_name'] or '', sender))
    if to_addr:
        msg['To'] = to_addr
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()
    if reply_to:
        msg['Reply-To'] = reply_to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype='html')
    return msg


def send_email(to_addr, subject, body, html=None, reply_to=None, cfg=None):
    """Send one email over SMTP.

    Returns (success: bool, detail: str). Never raises and never blocks the
    caller's transaction — a failed send leaves the inbox record intact.
    If email is not configured this is a silent no-op returning False.
    """
    cfg = cfg or get_smtp_config()
    if not is_configured(cfg):
        return False, 'Email delivery is not configured (Admin → Settings → Email).'
    if not to_addr or not EMAIL_RE.match(to_addr.strip()):
        return False, 'Recipient address is missing or invalid.'
    to_addr = to_addr.strip()

    msg = build_message(subject, body, cfg=cfg, to_addr=to_addr,
                        html=html, reply_to=reply_to)

    try:
        context = ssl.create_default_context()
        if cfg['security'] == 'ssl':
            server = smtplib.SMTP_SSL(cfg['host'], cfg['port'],
                                      timeout=15, context=context)
        else:
            server = smtplib.SMTP(cfg['host'], cfg['port'], timeout=15)
        try:
            if cfg['security'] == 'starttls':
                server.starttls(context=context)
            server.login(cfg['username'], cfg['password'])
            # Gmail rejects envelopes whose RFC5322 From differs from the
            # authenticated user unless explicitly permitted — use the
            # authenticated identity as the envelope sender.
            server.send_message(msg, from_addr=cfg['username'],
                                to_addrs=[to_addr])
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except smtplib.SMTPAuthenticationError as exc:
        return False, ('Gmail rejected the credentials. For Gmail use an App '
                       f'Password (see help text). Server said: {exc}')
    except (smtplib.SMTPException, OSError) as exc:
        return False, f'SMTP error: {exc}'
    except Exception as exc:  # defensive: auth/plumbing must never break a request
        return False, f'Unexpected mail error: {exc}'
    return True, f'Delivered to {to_addr} via {cfg["host"]}:{cfg["port"]}'


def test_connection(cfg=None):
    """Login-only check used by the admin 'Test connection' button.

    Connects and authenticates but sends nothing. Returns (ok, detail).
    """
    cfg = cfg or get_smtp_config()
    if not (cfg['host'] and cfg['username'] and cfg['password']):
        return False, 'Fill in server, username, and password first.'
    try:
        context = ssl.create_default_context()
        if cfg['security'] == 'ssl':
            server = smtplib.SMTP_SSL(cfg['host'], cfg['port'],
                                      timeout=15, context=context)
        else:
            server = smtplib.SMTP(cfg['host'], cfg['port'], timeout=15)
        try:
            if cfg['security'] == 'starttls':
                server.starttls(context=context)
            server.login(cfg['username'], cfg['password'])
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except smtplib.SMTPAuthenticationError as exc:
        return False, ('Authentication failed. For Gmail, create an App '
                       f'Password at myaccount.google.com/apppasswords. ({exc})')
    except (smtplib.SMTPException, OSError) as exc:
        return False, f'Could not reach {cfg["host"]}:{cfg["port"]} — {exc}'
    except Exception as exc:
        return False, f'Unexpected error: {exc}'
    return True, f'Successful login to {cfg["host"]}:{cfg["port"]} ' \
                 f'({cfg["security"].upper()}). No email was sent.'
