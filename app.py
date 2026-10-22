"""
AllMyProfiles - Flask application factory.

Run in development (SQLite):
    flask --app app:app run --debug
    or: python app.py

Run in production (PostgreSQL):
    export FLASK_ENV=production
    export DATABASE_URL=postgresql://user:pass@host:5432/allmyprofiles
    gunicorn "app:create_app('production')"
"""

import json
import os
import random
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   abort, session, send_from_directory, Response)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_migrate import Migrate
from sqlalchemy import func

from config import config_by_name
from models import (db, User, Link, Setting, Activity, Message, MessageRead,
                    LinkCheckResult, LinkSource, LinkCategory,
                    ContactMessage, AbuseReport, Testimonial, ContentSection,
                    Faq, FaqCategory, FAQ_QUESTION_MAX, FAQ_ANSWER_MAX,
                    FAQ_TOKENS, slugify,
                    ReviewInvitation, ReviewInviteTemplate,
                    ReplyMacro, SavedView, TriageRule,
                    REPORT_REASONS, REPORT_STATUSES, CONTACT_STATUSES,
                    LINK_CATEGORIES, MESSAGE_CATEGORIES, MESSAGE_SEVERITIES,
                    PAID_TIERS, HEX_COLOR_RE)
import flask_monitoringdashboard as fmd
from uploads_util import (BACKGROUND_MAX_IMAGE_DIMENSION, save_upload_image,
                          delete_upload_image)
import catalogue_io
import mail_util
import stripe_billing
from oauth_login import (oauth_bp, init_oauth, available_providers,
                         provider_display_maps)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

TIERS = ('Free', 'Expanded', 'Full', 'Custom')

# Session key used while an admin is impersonating another user. When set,
# it holds the real admin's user id.
IMPERSONATION_KEY = 'impersonator_id'

# Usernames that can never be claimed (they collide with app routes or are
# reserved for platform/brand purposes). Enforced at registration time and
# in the home-page availability API.
RESERVED_USERNAMES = {
    'admin', 'api', 'app', 'assets', 'about', 'blog', 'css', 'dashboard',
    'docs', 'help', 'img', 'inbox', 'js', 'login', 'logout', 'pricing',
    'privacy', 'profile', 'redirect', 'register', 'report', 'settings',
    'static', 'status', 'support', 'terms', 'test', 'uploads', 'u', 'www',
    'directory', 'networks', 'mature', 'faq',
}

USERNAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{2,30}$')

# Display Name validation. The Display Name IS the public profile handle —
# it powers the search-engine-friendly URL /<display-name-slug> — so only
# plain text and numbers are allowed for now (no email addresses, no
# punctuation like apostrophes or periods). Spaces separate words and are
# collapsed into single hyphens in the slug.
DISPLAY_NAME_ALLOWED_RE = re.compile(r'^[A-Za-z0-9 ]+$')
DISPLAY_NAME_TEXT_RE = re.compile(r'[A-Za-z0-9]')
DISPLAY_NAME_MAX_LEN = 60

# Cache for the route-derived reserved-name set (see reserved_root_names()).
_RESERVED_ROOT_CACHE = {}


def display_name_slug(text):
    """URL-safe handle derived from a Display Name.

    Lowercased, runs of spaces collapse to a single hyphen ("Jane Doe" ->
    "jane-doe"). Returns '' when nothing alphanumeric survives, which
    callers treat as "not a valid handle".
    """
    slug = re.sub(r'\s+', '-', (text or '').strip().lower())
    return slug[:64]


def is_valid_display_name_format(text):
    """True when a Display Name is well-formed (text/numbers only + slugs)."""
    text = (text or '').strip()
    if len(text) < 3 or len(text) > DISPLAY_NAME_MAX_LEN:
        return False
    # Reject anything resembling an email address — display names must be
    # human-readable names, never contact addresses.
    if '@' in text:
        return False
    if not DISPLAY_NAME_ALLOWED_RE.match(text):
        return False
    if not display_name_slug(text):
        return False
    # The slug itself must look like a clean handle: letters, numbers and
    # single-hyphen word joins only — starts/ends alphanumeric, never a
    # reserved system path segment.
    slug = display_name_slug(text)
    if not re.match(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$', slug):
        return False
    if slug in RESERVED_USERNAMES:
        return False
    return True


def reserved_root_names():
    """First URL path segments claimed by system / application routes.

    Public profiles now live at the root of the domain (``/<display-name>``)
    so they can be search-engine friendly, which means every other route
    competes with them for the first segment. This set is computed from the
    live url_map (plus the static-folder prefix), so anything the platform
    registers — now or later — is automatically off-limits as a Display
    Name slug. Results are cached once the url_map is sealed (first request).
    """
    cached = _RESERVED_ROOT_CACHE.get('names')
    if cached is not None:
        return cached
    names = set(RESERVED_USERNAMES)
    try:
        from flask import current_app
        url_map = current_app.url_map
    except Exception:
        url_map = None
    if url_map is not None:
        for rule in url_map.iter_rules():
            parts = [p for p in rule.rule.split('/') if p]
            if not parts:
                continue
            first = parts[0]
            if first.startswith('<'):
                # Catch-all style rules (e.g. /<handle>) never reserve a name.
                continue
            names.add(first.lower())
        # Werkzeug >= 2.2 removed ``Map.static_folder`` (the static route is
        # now just another rule in the map, so the loop above already covers
        # it). Guard the lookup for backwards compatibility.
        static_folder = getattr(url_map, 'static_folder', None)
        if static_folder:
            names.add(static_folder.strip('/').lower())
    frozen = frozenset(names)
    _RESERVED_ROOT_CACHE['names'] = frozen
    return frozen


def is_reserved_route_segment(segment):
    """True when a bare path segment would collide with a system/app route."""
    seg = (segment or '').strip().lower()
    if not seg:
        return False
    if '.' in seg:  # file-style paths (/llms.txt, /robots.txt, feeds…)
        return True
    return seg in reserved_root_names()


def display_name_taken(text, exclude_id=None):
    """Case-insensitive uniqueness check against claimed display names."""
    wanted = (text or '').strip().casefold()
    if not wanted:
        return False
    for u in User.query.filter(User.display_name.isnot(None)).all():
        stored = (u.display_name or '').strip().casefold()
        if not stored:
            continue
        if exclude_id is not None and u.id == exclude_id:
            continue
        if stored == wanted:
            return True
        # Two different names that produce the same URL slug would create
        # colliding profile URLs ("Jane Doe" vs "jane-doe"), so treat the
        # slug space as claimed too.
        if display_name_slug(stored) == display_name_slug(wanted):
            return True
    return False


def is_claimable_display_name(text, exclude_id=None):
    """Return (ok, reason) for a desired Display Name / profile handle.

    The Display Name becomes the public profile URL (/<slug>), so it is
    required at signup, must be unique against every already-claimed name
    (case-insensitive, slug-aware) and may only contain letters, numbers
    and spaces — no email addresses, no punctuation.
    """
    text = (text or '').strip()
    if not text:
        return False, 'A Display Name is required — it becomes your profile URL.'
    if '@' in text:
        return False, 'Please use a display name, not an email address.'
    if len(text) < 3:
        return False, 'Display name must be at least 3 characters.'
    if len(text) > DISPLAY_NAME_MAX_LEN:
        return False, f'Display name must be {DISPLAY_NAME_MAX_LEN} characters or fewer.'
    if not DISPLAY_NAME_ALLOWED_RE.match(text):
        return False, ('Only letters, numbers and spaces are allowed in a '
                       'display name.')
    slug = display_name_slug(text)
    if slug in RESERVED_USERNAMES or is_reserved_route_segment(slug):
        return False, 'This name is reserved. Please pick another.'
    if display_name_taken(text, exclude_id=exclude_id):
        return False, 'This display name is already claimed.'
    return True, 'Available!'


def is_claimable_username(username):
    """Return (ok, reason) for a desired internal login handle."""
    username = (username or '').strip().lower()
    if not USERNAME_RE.match(username):
        return False, ('Usernames must be 3-31 characters: letters, numbers,'
                       ' hyphens or underscores.')
    if username in RESERVED_USERNAMES or is_reserved_route_segment(username):
        return False, 'This name is reserved.'
    if User.query.filter_by(username=username).first():
        return False, 'This name is already taken.'
    if display_name_taken(username):
        return False, 'This name is already claimed.'
    return True, 'Available!'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(view):
    """Like @login_required but additionally requires the site-admin flag."""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        # While impersonating, admin privileges are suspended on purpose:
        # the admin should experience exactly what the user experiences.
        if not current_user.is_admin or IMPERSONATION_KEY in session:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# ----------------------------------------------------------------------
# Flask-MonitoringDashboard (FMD) integration — profile traffic analytics
#
# FMD auto-monitors every endpoint of this app (including the public
# profile route ``/<handle>``) and serves its own charts UI at /fmd.
# The pieces below adapt it to a multi-tenant link-in-bio SaaS:
#   * group_by  -> records which *profile* each hit belongs to, so the
#                  "Users" tab of an endpoint page becomes a per-profile
#                  traffic breakdown;
#   * auth     -> replaces FMD's built-in admin/admin login with our own
#                  Flask-Login session check (site admins only);
#   * binding  -> opt-in via env var, isolated metrics DB, telemetry off.
# ----------------------------------------------------------------------

#: Public profile endpoints whose traffic users get a per-profile view of.
FMD_PROFILE_ENDPOINTS = ('public_profile')

def _fmd_enabled():
    """Feature flag: set FMD_ENABLED=1 to turn traffic monitoring on.

    Off by default so existing dev/test environments (which reuse the
    Flask session dict) are unaffected until the package is deployed.
    """
    return os.environ.get('FMD_ENABLED', '').lower() in ('1', 'true', 'yes', 'on')


def _fmd_current_handle():
    """Profile handle being served by the current request, or None.

    Prefers the URL variable Werkzeug already matched; falls back to
    looking the user up directly (covers aliases/edge cases).
    """
    handle = (request.view_args or {}).get('handle')
    if handle:
        return handle
    path = (request.path or '/').strip('/')
    if '/' not in path and path:
        u = User.query.filter(db.func.lower(User.username) == path.lower()).first()
        if u is not None:
            return profile_handle(u) or None
    return None


def _fmd_group_by():
    """Value stored in FMD's Request.group_by column for each hit.

    For public profile hits we store the profile handle itself, which
    turns FMD's per-"user" charts into per-profile traffic charts.
    Everything else is grouped by the logged-in account (anonymous
    visitors share one bucket). NOTE: FMD truncates this field to 100
    characters.
    """
    if request.endpoint in FMD_PROFILE_ENDPOINTS:
        return _fmd_current_handle() or 'unknown'
    uid = session.get('_user_id')          # Flask-Login's session key
    return f'user:{uid}' if uid else 'anonymous'


def _fmd_configure(app):
    """Apply FMD's module-level config BEFORE bind() registers routes.

    Important: flask_monitoringdashboard.config is a process-global
    singleton — these settings must be applied before any other code
    calls bind(), otherwise tables are created with stale values.
    """
    # Metrics live in their OWN database file so they never touch the
    # app's SQLAlchemy models/migrations. Env-overridable for prod hosts
    # that want e.g. a dedicated Postgres schema.
    fmd.config.database_name = os.environ.get(
        'FMD_DATABASE_URL', 'sqlite:///flask_monitoringdashboard.db')
    # Serve the charts UI at /fmd — NOT the default /dashboard, which is
    # already taken by the user dashboard route.
    fmd.config.link = os.environ.get('FMD_LINK', 'fmd')
    fmd.config.blueprint_name = 'fmd_dashboard'
    fmd.config.version = os.environ.get('FMD_APP_VERSION', '1.0')
    # monitor_level 1 = response time + status codes per endpoint (cheap).
    # Level 2+ adds outlier stack-trace profiling; raise it if you want
    # deeper diagnostics.
    fmd.config.monitor_level = int(os.environ.get('FMD_MONITOR_LEVEL', '1'))
    # Per-profile attribution (see _fmd_group_by).
    fmd.config.group_by = _fmd_group_by
    # Correct client IP behind a reverse proxy (Render/Railway/heroku-style).
    # In FMD 5.x inject_dependencies lives on the config singleton itself.
    fmd.config.inject_dependencies(get_ip=lambda: (
        request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        or request.remote_addr or '127.0.0.1'))
    # Keep the brand honest in the embedded UI.
    fmd.config.brand_name = 'AllMyProfiles Traffic Monitor'
    fmd.config.title_name = 'Traffic Dashboard — AllMyProfiles'
    # FMD ships with an admin/admin basic-auth login; we replace it with
    # our own Flask-Login gate instead (see _fmd_bind).
    fmd.config.show_login_banner = False
    fmd.config.show_login_footer = False


def _fmd_secure(func):
    """Gate for all FMD views: signed-in SITE ADMIN using our session.

    This deliberately bypasses FMD's own username/password system — the
    admin sees the full, UNFILTERED view (every profile, every visitor,
    every endpoint) without ever logging into a second dashboard.
    Impersonating admins are locked out, mirroring admin_required.
    """
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not (current_user.is_authenticated and current_user.is_admin
                and IMPERSONATION_KEY not in session):
            # Send them through the normal app login first.
            return redirect(url_for('login', next=url_for(request.endpoint)))
        return func(*args, **kwargs)
    return wrapper


def _fmd_bind(app, schedule=True):
    """Bind FMD to the app and take ownership of its blueprint's auth.

    FMD decorates every non-login route with its own ``secure`` (and some
    with ``admin_secure``) at import time. We swap those two functions in
    the module namespace BEFORE bind() imports the views, so every route
    gets registered against OUR admin gate instead.
    """
    import flask_monitoringdashboard.core.auth as fmd_auth
    fmd_auth.secure = _fmd_secure
    fmd_auth.admin_secure = _fmd_secure
    # FMD's blueprint is a module-level singleton. If a previous bind()
    # already registered it on another app (e.g. tests calling
    # create_app() twice in one process), Flask refuses any further
    # setup hooks — reset its registration state so re-binding works.
    if getattr(fmd.blueprint, '_got_registered_once', False):
        fmd.blueprint._got_registered_once = set()
    fmd.bind(app, schedule=schedule)
    # Belt & braces: enforce the gate at dispatch time too, in case a
    # future FMD release adds unauthenticated routes. Login/logout stay
    # open so FMD's own pages don't error while redirecting. Registered
    # on the APP (not the blueprint): FMD's bind() has already registered
    # the blueprint by this point, and Flask forbids new blueprint setup
    # hooks after first registration. The request.blueprint check below
    # scopes it to FMD's views only.
    exempt = {'.login', '.logout', '.static'}

    @app.before_request
    def _fmd_gate():
        if request.blueprint != fmd.config.blueprint_name:
            return None
        if any(request.endpoint.endswith(sfx) for sfx in exempt):
            return None
        if not (current_user.is_authenticated and current_user.is_admin
                and IMPERSONATION_KEY not in session):
            return redirect(url_for('login', next=request.url))
        return None


# ----------------------------------------------------------------------
# Per-profile traffic charts for the USER dashboard (read-only slice of
# FMD's metrics database).
#
# Security model: every query below is keyed on a handle derived ONLY from
# ``profile_handle(current_user)`` — never from the URL or any request
# input. The WHERE clause pins FMD's Request.group_by column to that exact
# handle AND restricts rows to the public-profile endpoints, so a user can
# structurally never see another profile's (or the site's) traffic, even
# by crafting requests. Admins get the full unfiltered view at /fmd; users
# only ever get this server-aggregated panel.
# ----------------------------------------------------------------------

#: Days covered by the user-facing "Profile traffic" chart.
FMD_CHART_DAYS = 30


def _fmd_session():
    """Short-lived session against FMD's isolated metrics DB.

    Returns None when monitoring is off (package import failed or the
    FMD_ENABLED flag is unset), so callers degrade gracefully instead of
    erroring. The engine/tables are created by FMD itself at import time.
    """
    if not _fmd_enabled():
        return None
    try:
        from flask_monitoringdashboard.database import session_scope
    except Exception:
        return None
    return session_scope()


def fmd_profile_stats(handle):
    """Traffic stats for ONE public profile handle, straight from FMD.

    Hardwired filters (the tenant boundary):
    * Endpoint.name == 'public_profile' — only
        hits on /<handle> pages count;
      * Request.group_by == handle — only rows attributed to THIS profile
        (see _fmd_group_by); no other handle can match.
    Timestamps are stored tz-aware UTC (FMD utc_now); we convert to naive
    UTC so the buckets line up with the midnight-UTC window below.

    Returns None when monitoring is disabled; otherwise a dict:
      totals {views, unique_days, avg_ms, first_seen, last_seen},
      days   [{'date': date, 'views': int} for the recent window],
      links  [{'endpoint': name, 'views': int}] newest-first breakdown.
    """
    sess = _fmd_session()
    if sess is None or not handle:
        return None
    from sqlalchemy import and_, case, distinct
    from flask_monitoringdashboard.database import Endpoint, Request
    with sess as s:
        base = (and_(Request.endpoint_id == Endpoint.id,
                     Endpoint.name.in_(FMD_PROFILE_ENDPOINTS),
                     Request.group_by == handle))
        row = (s.query(
                  func.count(Request.id),
                  func.avg(Request.duration),
                  func.min(Request.time_requested),
                  func.max(Request.time_requested),
                  func.count(distinct(case((Request.status_code < 400,
                                             Request.ip)))),
              )
              .select_from(Request)
              .join(Endpoint, Request.endpoint_id == Endpoint.id)
              .filter(base).one())
        views = int(row[0] or 0)
        # Recent daily buckets (naive UTC dates; missing days render as 0).
        since = datetime.utcnow() - timedelta(days=FMD_CHART_DAYS)
        day_rows = (s.query(func.date(Request.time_requested),
                           func.count(Request.id))
                    .select_from(Request)
                    .join(Endpoint, Request.endpoint_id == Endpoint.id)
                    .filter(base, Request.time_requested >= since)
                    .group_by(func.date(Request.time_requested)).all())
        # Per-endpoint split (canonical page vs. legacy alias).
        link_rows = (s.query(Endpoint.name, func.count(Request.id))
                     .select_from(Request)
                     .join(Endpoint, Request.endpoint_id == Endpoint.id)
                     .filter(base)
                     .group_by(Endpoint.name)
                     .order_by(func.count(Request.id).desc()).all())

    counts = {}
    for d, n in day_rows:
        if d is None:
            continue
        try:
            counts[str(d)] = int(n)
        except (TypeError, ValueError):
            continue
    days = []
    today = datetime.utcnow().date()
    for i in range(FMD_CHART_DAYS - 1, -1, -1):
        d = today - timedelta(days=i)
        days.append({'date': d, 'views': counts.get(str(d), 0)})

    def _as_naive(dt):
        return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo else dt

    return {
        'totals': {
            'views': views,
            'unique_visitors': int(row[4] or 0),
            'avg_ms': float(row[1]) if row[1] is not None else 0.0,
            'first_seen': _as_naive(row[2]),
            'last_seen': _as_naive(row[3]),
        },
        'days': days,
        'links': [{'endpoint': name, 'views': int(n)} for name, n in link_rows],
    }


# ----------------------------------------------------------------------
# Profile-extras helpers (skills / video / GA4) — shared by form handling
# and template rendering.
# ----------------------------------------------------------------------

MAX_SKILLS = 12          # badge cap so profiles stay readable
SKILL_MAX_LEN = 30       # per-skill character cap
GA4_RE = re.compile(r'^G-[A-Z0-9]{6,20}$')
YOUTUBE_ID_RE = re.compile(r'^[0-9A-Za-z_\-]{11}$')
VIMEO_ID_RE = re.compile(r'^\d{6,12}$')


def normalize_skills(raw):
    """Parse a comma-separated skills string into a clean list.

    Trims whitespace, drops empties/dupes (case-insensitive), caps each
    entry's length and the total count. Returns [] for no skills.
    """
    if not raw:
        return []
    seen, out = set(), []
    for part in raw.split(','):
        skill = part.strip()[:SKILL_MAX_LEN].strip()
        if not skill:
            continue
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(skill)
        if len(out) >= MAX_SKILLS:
            break
    return out


def parse_video_url(raw):
    """Extract (platform, video_id) from a YouTube/Vimeo URL or bare ID.

    Accepts youtube.com/watch?v=ID, youtu.be/ID, vimeo.com/ID, or a raw
    ID paired with an explicit platform elsewhere. Returns None when the
    input is empty/unrecognized.
    """
    url = (raw or '').strip()
    if not url:
        return None
    m = re.search(r'(?:v=|youtu\.be/)([0-9A-Za-z_\-]{11})', url)
    if 'youtube.com' in url.lower() or 'youtu.be' in url.lower():
        if m and YOUTUBE_ID_RE.match(m.group(1)):
            return ('youtube', m.group(1))
        return None
    if 'vimeo.com' in url.lower():
        m = re.search(r'vimeo\.com/(\d{6,12})', url)
        if m:
            return ('vimeo', m.group(1))
        return None
    # Bare value: treat as an ID if it matches one of the known shapes.
    if YOUTUBE_ID_RE.match(url):
        return ('youtube', url)
    if VIMEO_ID_RE.match(url):
        return ('vimeo', url)
    return None


def video_embed(user):
    """Return a privacy-enhanced embed iframe URL for a user's intro
    video, or None. Intro videos are a paid-tier feature and are only ever
    built from validated ids/platforms."""
    if user.tier not in PAID_TIERS:
        return None
    if not user.video_platform or not user.video_id:
        return None
    if user.video_platform == 'youtube' and YOUTUBE_ID_RE.match(user.video_id):
        return f'https://www.youtube-nocookie.com/embed/{user.video_id}'
    if user.video_platform == 'vimeo' and VIMEO_ID_RE.match(user.video_id):
        return f'https://player.vimeo.com/video/{user.video_id}'
    return None


# ==========================================
# Profile redesign helpers: curated themes + link grouping
# ==========================================

# Curated profile themes. Each entry is a *pre-checked* combination of
# background / text colors (contrast ratio >= 4.5:1) plus an optional
# background image URL, so users can restyle their profile without ever
# producing unreadable combinations. Admin-managed via LinkSource-style
# curation; users pick a key only.
PROFILE_THEMES = {
    'light':   {'label': 'Daylight',   'bg_color': '#f4f6f8', 'text_color': '#333333', 'bg_image_url': None},
    'paper':   {'label': 'Paper',      'bg_color': '#fdfaf5', 'text_color': '#3d3427', 'bg_image_url': None},
    'mint':    {'label': 'Fresh Mint', 'bg_color': '#e8f6f0', 'text_color': '#1e4d3b', 'bg_image_url': None},
    'sky':     {'label': 'Clear Sky',  'bg_color': '#e9f3fb', 'text_color': '#1c3d5a', 'bg_image_url': None},
    'blush':   {'label': 'Blush',      'bg_color': '#fbeef1', 'text_color': '#5c2338', 'bg_image_url': None},
    'sand':    {'label': 'Desert Sand','bg_color': '#f6efe4', 'text_color': '#4a3b22', 'bg_image_url': None},
    'lavender':{'label': 'Lavender',   'bg_color': '#f0ecfa', 'text_color': '#3b2e63', 'bg_image_url': None},
    'slate':   {'label': 'Slate',      'bg_color': '#2b3038', 'text_color': '#e8eaed', 'bg_image_url': None},
    'midnight':{'label': 'Midnight',   'bg_color': '#12141f', 'text_color': '#dfe3ff', 'bg_image_url': None},
    'forest':  {'label': 'Forest',     'bg_color': '#14291f', 'text_color': '#d8ecd9', 'bg_image_url': None},
}
DEFAULT_THEME = 'light'


def theme_for_user(user):
    """Resolve the user's chosen theme into the dict the template expects.

    Returns keys matching the mockup contract: bg_color, text_color,
    bg_image_url (None unless a future premium theme sets one).
    """
    t = PROFILE_THEMES.get(getattr(user, 'theme', None) or DEFAULT_THEME,
                           PROFILE_THEMES[DEFAULT_THEME])
    return {
        'bg_color': t['bg_color'],
        'text_color': t['text_color'],
        'bg_image_url': (
            user.background_src
            if user.tier in PAID_TIERS and user.background_path
            else t.get('bg_image_url')
        ),
    }


def group_links_for_profile(links):
    """Split active links into pinned + categorized groups (mockup §2).

    Pinned links render first with a gold accent; remaining links are
    bucketed by display category in the admin-managed LinkCategory order
    (the catch-all 'Other' always renders last; unknown categories are
    appended alphabetically before it).
    """
    pinned = [l for l in links if l.is_pinned]
    categorized = {}
    for l in links:
        if l.is_pinned:
            continue
        categorized.setdefault(l.display_category, []).append(l)
    order = [c for c in LinkCategory.names() if c != 'Other']
    ordered = {}
    for cat in order:
        if cat in categorized:
            ordered[cat] = categorized.pop(cat)
    for cat in sorted(categorized):
        if cat == 'Other':
            continue
        ordered[cat] = categorized[cat]
    if 'Other' in categorized:
        ordered['Other'] = categorized.pop('Other')
    return pinned, ordered


def profile_handle(user):
    """Public URL handle for a profile — always derived from the Display Name.

    The canonical profile URL is ``/<display-name-slug>``. Email addresses and raw
    usernames are never used in public URLs; an account without a Display
    Name has no public profile at all (callers should 404). Falls back to
    the legacy username only for accounts that predate the Display-Name
    rule so old links keep resolving.
    """
    slug = display_name_slug(getattr(user, 'display_name', None))
    handle = slug or (user.username or '')
    # Reserved names (e.g. 'admin') belong to system routes, never profiles.
    if handle.lower() in RESERVED_USERNAMES or is_reserved_route_segment(handle.lower()):
        return ''
    return handle


def url_for_profile(user, **kwargs):
    """Canonical public-profile URL for a user (Display Name based).

    Templates should use the ``profile_url`` Jinja global instead of
    calling ``url_for('public_profile', ...)`` with a raw username, so
    every link on the site stays consistent with the Display-Name URL.
    Users without a Display Name have no public profile — callers get ''
    and should hide the link.
    """
    if user is None:
        return ''
    if not (getattr(user, 'display_name', None) or '').strip():
        return ''
    handle = profile_handle(user)
    if not handle:
        return ''
    return url_for('public_profile', handle=handle, **kwargs)


def user_by_handle(handle):
    """Resolve a public URL handle to a User, or None.

    Matches the Display Name slug first (the canonical form), then falls
    back to the legacy username lookup for rows created before this story.
    A bare path segment that belongs to a system/application route is
    never treated as a profile — real routes always win over profiles.
    """
    wanted = (handle or '').strip().lower()
    if not wanted or is_reserved_route_segment(wanted):
        return None
    for u in User.query.filter(User.display_name.isnot(None)).all():
        if display_name_slug(u.display_name) == wanted:
            return u
    return User.query.filter_by(username=wanted).first()


# ----------------------------------------------------------------------
# Testimonial display helpers (Admin Testimonial Management story)
# ----------------------------------------------------------------------

def testimonial_profile_link(t):
    """Public-profile URL behind a testimonial's clickable profile icon.

    The icon links to the author's public profile page only when BOTH
    conditions from the acceptance criteria are met:
      1. the user explicitly consented to a public profile link
         (``Testimonial.profile_consent``), and
      2. the user has an active paid subscription — tier is one of
         PAID_TIERS, i.e. not the free tier.

    Returns '' when either condition fails or the account has no
    resolvable public profile, so templates render a plain (non-clickable)
    icon/initials instead of a dead link.
    """
    user = getattr(t, 'author', None)
    if user is None or not t.profile_consent:
        return ''
    if (getattr(user, 'tier', 'Free') or 'Free') not in PAID_TIERS:
        return ''
    return url_for_profile(user)


def testimonial_home_items(limit=None):
    """Home-page testimonial cards, per the site display logic.

    Only testimonials assigned to the homepage placement and currently
    eligible for display are returned. Each item carries the resolved
    profile-link rules via ``testimonial_profile_link``.
    """
    section = ContentSection.query.filter_by(
        slug='homepage-testimonials', is_active=True).first()
    if section is None:
        return []
    return testimonial_section_items(section, limit=limit)


def testimonial_section_items(section, limit=3):
    """Build display-ready cards assigned to a particular site placement."""
    q = Testimonial.section_display(section)
    rows = q.limit(limit).all() if limit else q.all()
    items = []
    for t in rows:
        items.append({
            'testimonial': t,
            'profile_url': testimonial_profile_link(t),
        })
    return items


def testimonial_home_placements():
    """Display-ready testimonial lists keyed by their home-page placement."""
    sections = ContentSection.query.filter_by(is_active=True).order_by(
        ContentSection.sort_order, ContentSection.name).all()
    return {
        section.slug: testimonial_section_items(section)
        for section in sections
        if section.slug != 'homepage-testimonials'
    }


# ----------------------------------------------------------------------
# Review invitation helpers (Story 2: dispatch + dedicated form)
# ----------------------------------------------------------------------

REVIEW_FLAG_RATING_THRESHOLD = 3   # ratings at or below this are flagged
REVIEW_INVITE_EXPIRES_DAYS = 30

# Placeholders an admin can use in the invitation template copy. They
# are filled per-recipient at dispatch time (string.Template style).
REVIEW_INVITE_PLACEHOLDERS = {
    'display_name': None,       # filled per recipient
    'site_name': 'AllMyProfiles',
    'invite_url': None,         # filled per recipient (absolute form URL)
}


def _invitation_context(user, token):
    """Placeholder context for rendering one invitation's copy."""
    invite_url = url_for('review_submit', token=token, _external=True)
    return {
        'display_name': (user.display_name or user.username or 'there'),
        'site_name': 'AllMyProfiles',
        'invite_url': invite_url,
    }


def create_review_invitation(user, batch_id=None, sender=None,
                             custom_message=None, commit=False):
    """Create a ReviewInvitation row and dispatch its notification.

    The invitation is persisted first with its unguessable token, then
    the rendered template copy goes through the standard inbox pipeline
    (``send_admin_message``) tagged with the ``review_invite`` category,
    so every dispatch is auditable like any other admin message on this
    site. The platform's external email delivery (Gmail SMTP) is configured
    from Admin -> Settings; when it is switched on, the profile owner also
    receives an email copy of each contact message. When it is off, the
    delivered inbox message below is the automated notification layer here.
    Returns the ReviewInvitation row.
    """
    token = secrets.token_urlsafe(32)
    inv = ReviewInvitation(token=token, user_id=user.id, batch_id=batch_id,
                           status='pending',
                           expires_days=REVIEW_INVITE_EXPIRES_DAYS)
    db.session.add(inv)
    db.session.flush()  # assign inv.id before linking the message

    tpl = ReviewInviteTemplate.get_default()
    ctx = _invitation_context(user, token)
    subject, body = tpl.render(ctx)
    if custom_message:
        # Admin-supplied extra line prepended to the default copy.
        body = custom_message + '\n\n' + body

    message, delivered = send_admin_message(
        subject=subject, body=body, category='review_invite',
        sender=sender, source='direct', audience='one', target_user=user,
        action_url=url_for('review_submit', token=token),
        action_label='Share your review', commit=False)
    if message is not None:
        inv.message_id = message.id
    Activity.record('review_invite_sent', actor=sender, user=user,
                    detail=f'review invitation #{inv.id} to {user.email} '
                           f'(batch {batch_id or "single"})')
    if commit:
        db.session.commit()
    return inv


def _ensure_schema(db):
    """Dev-only lightweight schema sync.

    ``db.create_all()`` creates *missing tables* but never adds new columns to
    existing ones, so a dev database created before recent model changes (e.g.
    missing ``users.skills``) keeps throwing OperationalError. This helper also
    issues ``ALTER TABLE ... ADD COLUMN`` for any simple nullable columns that
    are missing from an existing table. It is intentionally NOT a replacement
    for Flask-Migrate in production — run ``flask db upgrade`` there.
    """
    import sqlalchemy as sa
    db.create_all()
    inspector = sa.inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # just created by create_all()
        have = {c['name'] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in have or col.primary_key:
                continue  # only auto-add safe, non-PK columns
            try:
                coltype = col.type.compile(db.engine.dialect)
            except Exception:
                continue
            default_sql = ''
            if not col.nullable:
                # Non-nullable columns need a server default so existing rows
                # can be backfilled by ALTER TABLE.
                if col.default is not None and getattr(col.default, 'is_scalar', False):
                    v = col.default.arg
                    if isinstance(v, bool):
                        default_sql = f" DEFAULT {int(v)}"
                    elif isinstance(v, (int, float)):
                        default_sql = f" DEFAULT {v}"
                    elif isinstance(v, str):
                        default_sql = " DEFAULT '" + v.replace("'", "''") + "'"
                    else:
                        continue  # callable/python-side default — skip safely
                else:
                    tname = coltype.upper()
                    if any(t in tname for t in ('INT', 'BOOL')):
                        default_sql = ' DEFAULT 0'
                    elif 'CHAR' in tname or 'TEXT' in tname:
                        default_sql = " DEFAULT ''"
                    else:
                        continue  # can't safely backfill this column type
            with db.engine.begin() as conn:
                conn.execute(sa.text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}{default_sql}'
                ))


def create_app(config_name=None):
    """Application factory.

    config_name resolution order:
      1. explicit argument
      2. FLASK_ENV environment variable ('development' | 'production' | 'testing')
      3. 'development'
    """
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    # Ensure the (git-ignored) uploads folder exists.
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    # render_as_batch=True makes Alembic use SQLite "batch" mode (table
    # rebuild) for ALTERs, which SQLite needs; it's harmless on PostgreSQL.
    migrate = Migrate(app, db, render_as_batch=True)
    login_manager.init_app(app)
    # Third-party sign-in (Authlib). Safe with zero credentials configured:
    # unconfigured providers are hidden from the UI and their routes show a
    # "not configured" notice instead of failing. See oauth_login.py.
    init_oauth(app)
    app.register_blueprint(oauth_bp)

    # When running under the plain dev server, make sure tables + default
    # settings exist. In production use `flask db upgrade` instead.
    if config_name == 'development' and not os.environ.get('SKIP_AUTO_SCHEMA'):
        with app.app_context():
            _ensure_schema(db)
            Setting.seed_defaults()
            LinkSource.seed_defaults()
            LinkCategory.seed_defaults()
            ContentSection.seed_defaults()
            Faq.seed_defaults()
            # One-time-ish backfill so pre-existing locations get country
            # buckets (idempotent; cheap on dev DBs).
            backfill_country_fields()

    register_routes(app)

    # ------------------------------------------------------------------
    # Flask-MonitoringDashboard: automatic per-endpoint traffic + latency
    # monitoring, charts UI at /fmd (site admins only — see _fmd_bind).
    # Opt-in via FMD_ENABLED=1; schedule=False keeps the APScheduler
    # background thread off (we don't use custom graphs yet, and tests
    # shouldn't spawn threads). Bind AFTER routes exist so FMD can wrap
    # the view functions, but BEFORE the first request is served.
    # ------------------------------------------------------------------
    if _fmd_enabled():
        _fmd_configure(app)
        _fmd_bind(app, schedule=False)

    register_static_pages(app)
    register_seo_routes(app)
    register_inbox_routes(app)
    register_public_interaction_routes(app)
    register_admin_routes(app)
    register_admin_devdata_routes(app)
    register_admin_billing_routes(app)
    register_admin_message_routes(app)
    register_link_source_routes(app)
    register_link_category_routes(app)
    register_admin_productivity_routes(app)
    register_testimonial_routes(app)
    register_faq_routes(app)
    register_error_handlers(app)
    register_template_context(app)

    return app


# ==========================================
# Core Routes
# ==========================================

def _oauth_providers():
    """Configured social providers for the login/register buttons.

    Wrapped defensively: if anything in the OAuth layer misbehaves the auth
    epic simply shows no extra buttons — standard email/password login can
    never be blocked by it.
    """
    try:
        return available_providers()
    except Exception:
        return []


def _provider_label_map():
    """(labels, icons) dicts; safe fallback when OAuth layer is broken."""
    try:
        return provider_display_maps()
    except Exception:
        return {}, {}


def _dashboard_example_link():
    """A populated sample Link used on /dashboard to preview styling.

    Built in-memory (never committed) against a real catalogue source so
    brand colors, icon and CTA come from the same data the profile page
    uses — if the admin edits the catalogue, the preview follows.
    """
    src = (LinkSource.query.filter_by(name='GitHub', is_deleted=False,
                                      is_active=True).first()
           or next(iter(LinkSource.active()), None))
    demo = Link(user_id=0,
                title='My GitHub Profile',
                url=f'https://github.com/yourusername'
                    if src and 'github' in (src.domains or '')
                    else 'https://example.com/yourusername',
                icon_code=(src.icon_code if src else 'github') or 'github',
                category=src.category if src else 'Gaming & Dev',
                subhandle='yourusername',
                cta=src.cta if src and src.cta else 'Follow',
                click_count=128,
                is_active=True)
    if src is not None:
        db.session.add(demo)      # transient: lets matched_source resolve id
        demo.source_id = src.id
    return demo


HEX6_RE = re.compile(r'^#?([0-9a-fA-F]{6})$')


def _hex_rgb(value):
    """'#rrggbb' -> (r, g, b) or None when unparseable."""
    m = HEX6_RE.match((value or '').strip())
    if not m:
        return None
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(rgb):
    """WCAG relative luminance of an sRGB triplet."""
    def chan(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast_ratio(rgb1, rgb2):
    """WCAG contrast ratio between two colors (1.0 .. 21.0)."""
    l1 = _relative_luminance(rgb1)
    l2 = _relative_luminance(rgb2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def readable_text_color(bg, fallback='#212529'):
    """Return a text color that stays legible on ``bg``.

    The source's curated ``text_color`` is used whenever it clears WCAG AA
    for large text (>= 3:1) against the brand background; otherwise we fall
    back to white/near-black depending on which side contrasts better. This
    keeps directory cards in brand colors without ever producing unreadable
    combinations.
    """
    bg_rgb = _hex_rgb(bg)
    if bg_rgb is None:
        return fallback
    cur = _hex_rgb(fallback)
    if cur is not None and _contrast_ratio(cur, bg_rgb) >= 3.0:
        return fallback
    white, ink = (255, 255, 255), (26, 29, 38)
    best = white if _contrast_ratio(white, bg_rgb) >= _contrast_ratio(ink, bg_rgb) else ink
    return '#{:02x}{:02x}{:02x}'.format(*best)


def source_slug(name):
    """URL fragment for a Link Source name ('X / Twitter' -> 'x-twitter')."""
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower())
    return slug.strip('-')


def directory_adult_ok():
    """True when the current viewer may see adult entries in the directory.

    AC: adult cards are hidden for logged-out/guest users and only visible
    to logged-in users who have been verified as 18+ (one-time age gate,
    remembered per account). Admins are always allowed so they can QA the
    catalogue.
    """
    if _is_impersonating():
        return False
    if not current_user.is_authenticated:
        return False
    if getattr(current_user, 'is_admin', False):
        return True
    return bool(session.get('age_ok') or getattr(current_user, 'age_verified', False))


# ---------------------------------------------------------------------------
# Country resolution for the public /countries search
# ---------------------------------------------------------------------------
# Users type a free-form "Location" on their dashboard ("Berlin", "Germany",
# "Nairobi, KE", ...). At save time we normalize it into exactly one country
# bucket (see ``apply_country_fields``) so the country pages never end up
# with near-duplicate tiles. Recognition is data-driven: the ISO 3166 table
# below is generated from pycountry when it is installed and otherwise falls
# back to a curated list of common names — no hard dependency either way.

_ISO3166 = {
    # alpha-2: (official English name, [common aliases])
    'AD': ('Andorra', []),
    'AE': ('United Arab Emirates', ['UAE', 'Emirates']),
    'AF': ('Afghanistan', []),
    'AG': ('Antigua and Barbuda', []),
    'AI': ('Anguilla', []),
    'AL': ('Albania', []),
    'AM': ('Armenia', []),
    'AO': ('Angola', []),
    'AQ': ('Antarctica', []),
    'AR': ('Argentina', []),
    'AS': ('American Samoa', []),
    'AT': ('Austria', []),
    'AU': ('Australia', ['Oz']),
    'AW': ('Aruba', []),
    'AX': ('Åland Islands', []),
    'AZ': ('Azerbaijan', []),
    'BA': ('Bosnia and Herzegovina', ['Bosnia']),
    'BB': ('Barbados', []),
    'BD': ('Bangladesh', []),
    'BE': ('Belgium', []),
    'BF': ('Burkina Faso', []),
    'BG': ('Bulgaria', []),
    'BH': ('Bahrain', []),
    'BI': ('Burundi', []),
    'BJ': ('Benin', []),
    'BL': ('Saint Barthélemy', []),
    'BM': ('Bermuda', []),
    'BN': ('Brunei', ['Brunei Darussalam']),
    'BO': ('Bolivia', ['Bolivia, Plurinational State of']),
    'BQ': ('Caribbean Netherlands', []),
    'BR': ('Brazil', ['Brasil']),
    'BS': ('Bahamas', ['The Bahamas']),
    'BT': ('Bhutan', []),
    'BV': ('Bouvet Island', []),
    'BW': ('Botswana', []),
    'BY': ('Belarus', []),
    'BZ': ('Belize', []),
    'CA': ('Canada', []),
    'CC': ('Cocos (Keeling) Islands', []),
    'CD': ('DR Congo', ['Congo (Kinshasa)', 'Democratic Republic of the Congo',
                        'Democratic Republic of Congo', 'Congo-Kinshasa', 'DRC']),
    'CF': ('Central African Republic', ['CAR']),
    'CG': ('Republic of the Congo', ['Congo (Brazzaville)', 'Congo-Brazzaville']),
    'CH': ('Switzerland', ['Confoederatio Helvetica', 'Suisse', 'Schweiz']),
    'CI': ('Ivory Coast', ["Côte d'Ivoire", 'Cote d Ivoire']),
    'CK': ('Cook Islands', []),
    'CL': ('Chile', []),
    'CM': ('Cameroon', []),
    'CN': ('China', ['Mainland China', 'People\'s Republic of China']),
    'CO': ('Colombia', []),
    'CR': ('Costa Rica', []),
    'CU': ('Cuba', []),
    'CV': ('Cape Verde', ['Cabo Verde']),
    'CW': ('Curaçao', ['Curacao']),
    'CX': ('Christmas Island', []),
    'CY': ('Cyprus', []),
    'CZ': ('Czechia', ['Czech Republic', 'Czech']),
    'DE': ('Germany', ['Deutschland', 'Federal Republic of Germany']),
    'DJ': ('Djibouti', []),
    'DK': ('Denmark', []),
    'DM': ('Dominica', []),
    'DO': ('Dominican Republic', []),
    'DZ': ('Algeria', []),
    'EC': ('Ecuador', []),
    'EE': ('Estonia', []),
    'EG': ('Egypt', []),
    'EH': ('Western Sahara', []),
    'ER': ('Eritrea', []),
    'ES': ('Spain', ['España']),
    'ET': ('Ethiopia', []),
    'FI': ('Finland', ['Suomi']),
    'FJ': ('Fiji', []),
    'FK': ('Falkland Islands', []),
    'FM': ('Micronesia', ['Federated States of Micronesia']),
    'FO': ('Faroe Islands', []),
    'FR': ('France', []),
    'GA': ('Gabon', []),
    'GB': ('United Kingdom', ['UK', 'Britain', 'Great Britain',
                              'United Kingdom of Great Britain',
                              'England', 'Scotland', 'Wales',
                              'Northern Ireland']),
    'GD': ('Grenada', []),
    'GE': ('Georgia', []),
    'GF': ('French Guiana', []),
    'GG': ('Guernsey', []),
    'GH': ('Ghana', []),
    'GI': ('Gibraltar', []),
    'GL': ('Greenland', []),
    'GM': ('Gambia', ['The Gambia']),
    'GN': ('Guinea', []),
    'GP': ('Guadeloupe', []),
    'GQ': ('Equatorial Guinea', []),
    'GR': ('Greece', []),
    'GS': ('South Georgia', []),
    'GT': ('Guatemala', []),
    'GU': ('Guam', []),
    'GW': ('Guinea-Bissau', []),
    'GY': ('Guyana', []),
    'HK': ('Hong Kong', ['Hong Kong SAR', 'Hong Kong, China']),
    'HM': ('Heard Island and McDonald Islands', []),
    'HN': ('Honduras', []),
    'HR': ('Croatia', ['Hrvatska']),
    'HT': ('Haiti', []),
    'HU': ('Hungary', []),
    'ID': ('Indonesia', []),
    'IE': ('Ireland', ['Éire', 'Eire']),
    'IL': ('Israel', []),
    'IM': ('Isle of Man', []),
    'IN': ('India', ['Bharat']),
    'IO': ('British Indian Ocean Territory', []),
    'IQ': ('Iraq', []),
    'IR': ('Iran', ['Islamic Republic of Iran']),
    'IS': ('Iceland', []),
    'IT': ('Italy', ['Italia']),
    'JE': ('Jersey', []),
    'JM': ('Jamaica', []),
    'JO': ('Jordan', []),
    'JP': ('Japan', []),
    'KE': ('Kenya', []),
    'KG': ('Kyrgyzstan', []),
    'KH': ('Cambodia', []),
    'KI': ('Kiribati', []),
    'KM': ('Comoros', []),
    'KN': ('Saint Kitts and Nevis', ['St Kitts and Nevis', 'St. Kitts and Nevis']),
    'KP': ('North Korea', ["Dem People's Rep of Korea",
                           'Democratic People\'s Republic of Korea']),
    'KR': ('South Korea', ['Korea', 'Republic of Korea']),
    'KW': ('Kuwait', []),
    'KY': ('Cayman Islands', []),
    'KZ': ('Kazakhstan', []),
    'LA': ('Laos', ["Lao People's Democratic Republic"]),
    'LB': ('Lebanon', []),
    'LC': ('Saint Lucia', ['St Lucia', 'St. Lucia']),
    'LI': ('Liechtenstein', []),
    'LK': ('Sri Lanka', []),
    'LR': ('Liberia', []),
    'LS': ('Lesotho', []),
    'LT': ('Lithuania', []),
    'LU': ('Luxembourg', []),
    'LV': ('Latvia', []),
    'LY': ('Libya', []),
    'MA': ('Morocco', []),
    'MC': ('Monaco', []),
    'MD': ('Moldova', ['Republic of Moldova']),
    'ME': ('Montenegro', []),
    'MF': ('Saint Martin', []),
    'MG': ('Madagascar', []),
    'MH': ('Marshall Islands', []),
    'MK': ('North Macedonia', ['Macedonia']),
    'ML': ('Mali', []),
    'MM': ('Myanmar', ['Burma']),
    'MN': ('Mongolia', []),
    'MO': ('Macao', ['Macau']),
    'MP': ('Northern Mariana Islands', []),
    'MQ': ('Martinique', []),
    'MR': ('Mauritania', []),
    'MS': ('Montserrat', []),
    'MT': ('Malta', []),
    'MU': ('Mauritius', []),
    'MV': ('Maldives', []),
    'MW': ('Malawi', []),
    'MX': ('Mexico', ['México']),
    'MY': ('Malaysia', []),
    'MZ': ('Mozambique', []),
    'NA': ('Namibia', []),
    'NC': ('New Caledonia', []),
    'NE': ('Niger', []),
    'NF': ('Norfolk Island', []),
    'NG': ('Nigeria', []),
    'NI': ('Nicaragua', []),
    'NL': ('Netherlands', ['The Netherlands', 'Holland', 'NL']),
    'NO': ('Norway', ['Norge']),
    'NP': ('Nepal', []),
    'NR': ('Nauru', []),
    'NU': ('Niue', []),
    'NZ': ('New Zealand', ['Aotearoa']),
    'OM': ('Oman', []),
    'PA': ('Panama', []),
    'PE': ('Peru', ['Perú']),
    'PF': ('French Polynesia', []),
    'PG': ('Papua New Guinea', []),
    'PH': ('Philippines', ['The Philippines']),
    'PK': ('Pakistan', []),
    'PL': ('Poland', ['Polska']),
    'PM': ('Saint Pierre and Miquelon', []),
    'PN': ('Pitcairn Islands', []),
    'PR': ('Puerto Rico', []),
    'PS': ('Palestine', ['State of Palestine', 'Palestinian Territories']),
    'PT': ('Portugal', []),
    'PW': ('Palau', []),
    'PY': ('Paraguay', []),
    'QA': ('Qatar', []),
    'RE': ('Réunion', ['Reunion']),
    'RO': ('Romania', []),
    'RS': ('Serbia', []),
    'RU': ('Russia', ['Russian Federation']),
    'RW': ('Rwanda', []),
    'SA': ('Saudi Arabia', ['Kingdom of Saudi Arabia']),
    'SB': ('Solomon Islands', []),
    'SC': ('Seychelles', []),
    'SD': ('Sudan', []),
    'SE': ('Sweden', ['Sverige']),
    'SG': ('Singapore', []),
    'SH': ('Saint Helena', []),
    'SI': ('Slovenia', []),
    'SJ': ('Svalbard and Jan Mayen', []),
    'SK': ('Slovakia', ['Slovak Republic']),
    'SL': ('Sierra Leone', []),
    'SM': ('San Marino', []),
    'SN': ('Senegal', []),
    'SO': ('Somalia', []),
    'SR': ('Suriname', []),
    'SS': ('South Sudan', []),
    'ST': ('São Tomé and Príncipe',
           ['Sao Tome and Principe', 'Sao Tome']),
    'SV': ('El Salvador', []),
    'SX': ('Sint Maarten', []),
    'SY': ('Syria', ['Syrian Arab Republic']),
    'SZ': ('Eswatini', ['Swaziland']),
    'TC': ('Turks and Caicos Islands', []),
    'TD': ('Chad', []),
    'TF': ('French Southern Territories', []),
    'TG': ('Togo', []),
    'TH': ('Thailand', []),
    'TJ': ('Tajikistan', []),
    'TK': ('Tokelau', []),
    'TL': ('Timor-Leste', ['East Timor']),
    'TM': ('Turkmenistan', []),
    'TN': ('Tunisia', []),
    'TO': ('Tonga', []),
    'TR': ('Turkey', ['Türkiye', 'Turkiye']),
    'TT': ('Trinidad and Tobago', ['Trinidad']),
    'TV': ('Tuvalu', []),
    'TW': ('Taiwan', ['Taiwan, Province of China']),
    'TZ': ('Tanzania', ['United Republic of Tanzania']),
    'UA': ('Ukraine', []),
    'UG': ('Uganda', []),
    'UM': ('US Outlying Islands', []),
    'US': ('United States', ['USA', 'U.S.A.', 'U.S.', 'America',
                             'United States of America', 'The States']),
    'UY': ('Uruguay', []),
    'UZ': ('Uzbekistan', []),
    'VA': ('Vatican City', ['Holy See', 'Vatican', 'Santa Sede']),
    'VC': ('Saint Vincent and the Grenadines',
           ['St Vincent and the Grenadines', 'St. Vincent and the Grenadines']),
    'VE': ('Venezuela', ['Venezuela, Bolivarian Republic of']),
    'VG': ('British Virgin Islands', ['BVI']),
    'VI': ('US Virgin Islands', ['Virgin Islands (U.S.)', 'USVI']),
    'VN': ('Vietnam', ['Việt Nam', 'Viet Nam']),
    'VU': ('Vanuatu', []),
    'WF': ('Wallis and Futuna', []),
    'WS': ('Samoa', []),
    'YE': ('Yemen', []),
    'YT': ('Mayotte', []),
    'ZA': ('South Africa', ['RSA']),
    'ZM': ('Zambia', []),
    'ZW': ('Zimbabwe', []),
    # Common non-ISO territories kept for accurate labelling.
    'XK': ('Kosovo', []),
}


def _build_country_lookup():
    """Return {normalized_alias: (iso2, display_name)}.

    Prefers the full ISO 3166 dataset shipped by ``pycountry`` (names,
    official names and common aliases) when available; always merges the
    curated fallback above so recognition stays deterministic in test/dev
    environments without the optional dependency.
    """
    lookup = {}

    def add(alias, iso2, name):
        key = re.sub(r'[^a-z0-9 ]+', ' ', (alias or '').lower())
        key = re.sub(r'\s+', ' ', key).strip()
        if key and key not in lookup:
            lookup[key] = (iso2, name)

    try:  # optional dependency — best-effort enrichment only
        import pycountry
        for c in pycountry.countries:
            name = _ISO3166.get(c.alpha_2, (None, None))[0] or c.name
            add(c.name, c.alpha_2, name)
            add(getattr(c, 'official_name', None), c.alpha_2, name)
            for alias in getattr(c, 'common_names', []) or []:
                add(alias, c.alpha_2, name)
            add(c.alpha_2, c.alpha_2, name)
            add(getattr(c, 'alpha_3', None), c.alpha_2, name)
    except Exception:
        pass

    for iso2, (name, aliases) in _ISO3166.items():
        add(name, iso2, name)
        add(iso2, iso2, name)
        for alias in aliases:
            add(alias, iso2, name)
    return lookup


_COUNTRY_LOOKUP = _build_country_lookup()

# Free-text location tokens that are cities/regions rather than countries —
# mapped straight to their country so "Paris" and "Paris, France" land in
# the same bucket. Deliberately small: unknown city names simply fall back
# to the slug strategy below.
_CITY_TO_COUNTRY = {
    'london': 'GB', 'new york': 'US', 'nyc': 'US', 'los angeles': 'US',
    'chicago': 'US', 'san francisco': 'US', 'seattle': 'US', 'boston': 'US',
    'austin': 'US', 'toronto': 'CA', 'vancouver': 'CA', 'montreal': 'CA',
    'paris': 'FR', 'berlin': 'DE', 'munich': 'DE', 'hamburg': 'DE',
    'madrid': 'ES', 'barcelona': 'ES', 'rome': 'IT', 'milan': 'IT',
    'amsterdam': 'NL', 'rotterdam': 'NL', 'vienna': 'AT', 'zurich': 'CH',
    'geneva': 'CH', 'stockholm': 'SE', 'oslo': 'NO', 'copenhagen': 'DK',
    'helsinki': 'FI', 'dublin': 'IE', 'lisbon': 'PT', 'porto': 'PT',
    'athens': 'GR', 'prague': 'CZ', 'budapest': 'HU', 'warsaw': 'PL',
    'krakow': 'PL', 'kyiv': 'UA', 'kiev': 'UA', 'tokyo': 'JP',
    'osaka': 'JP', 'beijing': 'CN', 'shanghai': 'CN', 'shenzhen': 'CN',
    'seoul': 'KR', 'singapore': 'SG', 'hong kong': 'HK', 'taipei': 'TW',
    'bangkok': 'TH', 'jakarta': 'ID', 'manila': 'PH', 'ho chi minh': 'VN',
    'hanoi': 'VN', 'new delhi': 'IN', 'mumbai': 'IN', 'bengaluru': 'IN',
    'bangalore': 'IN', 'hyderabad': 'IN', 'chennai': 'IN', 'pune': 'IN',
    'karachi': 'PK', 'lahore': 'PK', 'dhaka': 'BD', 'colombo': 'LK',
    'sydney': 'AU', 'melbourne': 'AU', 'brisbane': 'AU', 'perth': 'AU',
    'auckland': 'NZ', 'dubai': 'AE', 'tel aviv': 'IL', 'jerusalem': 'IL',
    'riyadh': 'SA', 'istanbul': 'TR', 'ankara': 'TR', 'cairo': 'EG',
    'lagos': 'NG', 'nairobi': 'KE', 'capetown': 'ZA', 'cape town': 'ZA',
    'johannesburg': 'ZA', 'accra': 'GH', 'casablanca': 'MA', 'tunis': 'TN',
    'moscow': 'RU', 'saint petersburg': 'RU', 'mexico city': 'MX',
    'guadalajara': 'MX', 'monterrey': 'MX', 'bogota': 'CO', 'lima': 'PE',
    'santiago': 'CL', 'buenos aires': 'AR', 'saopaulo': 'BR',
    'sao paulo': 'BR', 'rio de janeiro': 'BR', 'caracas': 'VE',
    'quito': 'EC', 'lapaz': 'BO', 'montevideo': 'UY', 'santos': 'BR',
}


def slugify_location(text):
    """URL-safe bucket key for an unrecognized free-text location."""
    slug = re.sub(r'[^a-z0-9]+', '-', (text or '').strip().lower())
    return slug.strip('-')[:48]


def match_country(location):
    """Map a free-text location string to (code, display_name) or None.

    Order of attempts: exact country name/alias/ISO code → embedded comma
    segment ("Berlin, Germany") → known-city table. Anything unrecognized
    returns None so the caller can fall back to a slug bucket.
    """
    raw = (location or '').strip()
    if not raw:
        return None
    norm = lambda s: re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ',
                                                s.lower())).strip()
    whole = norm(raw)
    hit = _COUNTRY_LOOKUP.get(whole)
    if hit:
        return hit
    # Comma-separated pieces, most specific first ("Germany, Berlin" →
    # both parts checked; first recognized country wins).
    for part in [norm(p) for p in raw.split(',') if norm(p)] + \
                [norm(p) for p in raw.split('-') if norm(p)]:
        hit = _COUNTRY_LOOKUP.get(part)
        if hit:
            return hit
        hit2 = _COUNTRY_LOOKUP.get(norm(re.sub(r'\s+(?:and|&)\s+.*$', '',
                                               part)))
        if hit2:
            return hit2
    city = _CITY_TO_COUNTRY.get(whole)
    if city is None:
        head = norm(raw.split(',')[0])
        city = _CITY_TO_COUNTRY.get(head)
    if city:
        return _ISO3166[city][0] and (city, _ISO3166[city][0])
    return None


def apply_country_fields(user, location):
    """Set ``location`` plus derived ``country_code``/``country_name``."""
    user.location = location
    hit = match_country(location)
    if hit:
        user.country_code, user.country_name = hit
    elif location:
        # Unrecognized place ("Springfield") — keep it searchable under a
        # stable slug bucket instead of dropping it silently.
        user.country_code = slugify_location(location)[:2] or None
        user.country_name = location
    else:
        user.country_code = user.country_name = None
    return user


def apply_selected_country(user, country_code):
    """Set a user's country from a canonical ISO alpha-2 selection.

    ``location`` retains the country label for compatibility with existing
    data consumers while new profile edits always use the canonical fields.
    """
    code = (country_code or '').strip().upper()
    if not code:
        user.location = None
        user.country_code = None
        user.country_name = None
        return True
    country = _ISO3166.get(code)
    if country is None:
        return False
    user.country_code = code
    user.country_name = country[0]
    user.location = country[0]
    return True


def flag_emoji(iso2):
    """Regional-indicator emoji for a real ISO alpha-2 code, else None.

    Slug buckets (lowercase, e.g. 'sp') are deliberately excluded so they
    never render as bogus flags.
    """
    code = (iso2 or '').upper()
    if len(code) != 2 or not code.isalpha():
        return None
    if code not in _ISO3166:
        return None
    return ''.join(chr(0x1F1E6 + ord(ch) - ord('A')) for ch in code)


def country_choices():
    """Countries available in the profile country selector."""
    return [
        {'code': code, 'name': name, 'flag': flag_emoji(code)}
        for code, (name, _) in sorted(
            _ISO3166.items(), key=lambda item: item[1][0].casefold())
    ]


def country_slug(code):
    """URL fragment for a country bucket ('VA' -> 'va', 'sp' -> 'sp')."""
    return (code or '').strip().lower()


def _country_profile_ids(include_adult):
    """Ids of opted-in users with directory-listable content links.

    "Contenting profile" = at least one active link on a non-personal-
    website Link Source plus a headline or bio, and the searchable box
    checked. Adult-oriented owners only qualify for 18+-verified viewers.
    """
    ids = LinkSource.directory_source_ids(include_adult=include_adult)
    if not ids:
        return set()
    q = (db.session.query(Link.user_id)
         .join(User, User.id == Link.user_id)
         .filter(Link.is_active.is_(True),
                 User.directory_visible.is_(True),
                 Link.source_id.in_(ids),
                 db.or_(User.headline.isnot(None),
                        User.bio.isnot(None)))
         .distinct())
    if not include_adult:
        q = q.filter(User.is_adult_oriented.is_(False))
    return {r[0] for r in q.all()}


def country_buckets(include_adult):
    """List of {'code','name','members','flag'} buckets sorted by name.

    Single source of truth for every country-search surface (/countries,
    /country/<slug>, sidebar leaderboards, sitemap). A user only counts
    toward a bucket when ALL of the following hold:
      * they checked the "make my profile searchable" box on their
        dashboard (User.directory_visible — opt-in, defaults to FALSE),
      * they have at least one active link on a directory-listable
        Link Source ("contenting profiles"; personal websites never
        qualify — see LinkSource.NON_DIRECTORY_SOURCES),
      * their profile carries visible content (a headline or a bio), so
        empty placeholder accounts don't inflate a country tile,
      * adult-oriented profiles are counted only for 18+-verified
        viewers (``include_adult``), matching the directory age gate.
    Countries with zero qualifying members are simply absent — Vatican
    City stays off the map until someone there opts in with real content.
    """
    member_ids = _country_profile_ids(include_adult)
    if not member_ids:
        return []
    rows = (db.session.query(
                User.country_code, User.country_name,
                func.count(func.distinct(User.id)).label('members'))
            .filter(User.directory_visible.is_(True),
                    User.id.in_(member_ids),
                    User.country_code.isnot(None),
                    User.country_code != '')
            .group_by(func.upper(User.country_code), User.country_name).all())
    buckets = [{'code': r[0],
                'name': r[1] or r[0],
                'members': int(r[2]),
                'flag': flag_emoji(r[0])}
               for r in rows if r[2]]
    # Locations that didn't resolve to a real country end up in lowercase
    # slug buckets (e.g. "Springfield" -> 'sp'). Hide them by default so
    # /countries only ever shows genuine countries with searchable members;
    # admins can flip the 'country_show_unrecognized' site setting on.
    if not Setting.get_bool('country_show_unrecognized', False):
        buckets = [b for b in buckets if b['flag'] is not None]
    buckets.sort(key=lambda b: (b['name'] or '').casefold())
    return buckets


def backfill_country_fields():
    """Derive country_code/country_name for users that predate this story.

    Idempotent: only touches rows where location is set but no bucket was
    computed yet. Run at dev startup (see create_app); production should
    run it once inside the Alembic migration or via a management shell.
    """
    stale = (User.query
             .filter(User.location.isnot(None),
                     User.location != '',
                     db.or_(User.country_code.is_(None),
                            User.country_code == ''))
             .all())
    changed = False
    for u in stale:
        apply_country_fields(u, u.location)
        if u.country_code:
            changed = True
    if changed:
        db.session.commit()


def register_routes(app):

    # ------------------------------------------------------------------
    # Stripe billing: self-serve upgrades, customer portal, webhooks.
    # All routes degrade gracefully when no STRIPE_SECRET_KEY is set so
    # dev/test environments never crash before credentials are pasted in.
    # ------------------------------------------------------------------

    @app.route('/upgrade/<tier>', methods=['POST'])
    @login_required
    def upgrade(tier):
        """Start a Stripe Checkout session for a paid tier."""
        if tier not in stripe_billing.TIER_PRICES:
            flash('That plan is not available for self-service upgrade.',
                  'warning')
            return redirect(url_for('dashboard'))
        if not stripe_billing.stripe_ready():
            flash('Billing is not configured yet — Stripe secret key missing.',
                  'warning')
            return redirect(url_for('dashboard'))
        try:
            session = stripe_billing.create_checkout_session(
                current_user, tier, _site_base_url())
            db.session.commit()  # persist any newly-created customer id
        except Exception as exc:
            db.session.rollback()
            app.logger.error('Stripe checkout failed: %s', exc)
            flash(f'Could not start checkout: {exc}', 'danger')
            return redirect(url_for('dashboard'))
        return redirect(session.url)

    @app.route('/checkout/success')
    @login_required
    def checkout_success():
        """Post-checkout landing page (tier flips via webhook shortly after)."""
        tier = request.args.get('tier') or ''
        Activity.record('stripe_checkout_return', user=current_user,
                        detail=f'success tier={tier}')
        db.session.commit()
        flash('Thanks! Your payment is processing — your plan updates '
              'within a few seconds.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/checkout/cancel')
    @login_required
    def checkout_cancel():
        Activity.record('stripe_checkout_return', user=current_user,
                        detail='cancelled')
        db.session.commit()
        flash('Checkout cancelled — no charge was made.', 'info')
        return redirect(url_for('dashboard'))

    @app.route('/billing/portal', methods=['POST'])
    @login_required
    def billing_portal():
        """Redirect to the Stripe customer portal (cards, cancel, invoices)."""
        if not stripe_billing.stripe_ready():
            flash('Billing is not configured yet.', 'warning')
            return redirect(url_for('dashboard'))
        try:
            session = stripe_billing.create_portal_session(
                current_user, _site_base_url())
        except Exception as exc:
            flash(str(exc), 'warning')
            return redirect(url_for('dashboard'))
        return redirect(session.url)

    @app.route('/webhooks/stripe', methods=['POST'])
    def stripe_webhook():
        """Stripe -> us. Signature-verified; drives all tier changes."""
        payload = request.get_data()          # raw bytes: required for sig check
        sig_header = request.headers.get('Stripe-Signature', '')
        try:
            event = stripe_billing.construct_webhook_event(payload, sig_header)
        except Exception as exc:
            app.logger.warning('Rejected Stripe webhook: %s', exc)
            abort(400)
        try:
            action = stripe_billing.handle_webhook_event(event)
        except Exception as exc:
            app.logger.exception('Stripe webhook handler failed: %s', exc)
            db.session.rollback()
            # 500 makes Stripe retry the delivery with backoff.
            abort(500)
        if action and not action.startswith(('ignored', 'skipped')):
            etype = event.get('type') if isinstance(event, dict) else event.type
            Activity.record('stripe_webhook', detail=f'{etype} -> {action}')
        db.session.commit()
        return {'status': 'ok', 'action': action}, 200

    @app.route('/')
    def home():
        """ Landing / Marketing Home Page """
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        # Social proof counters (cheap aggregate queries).
        stats = {
            'profiles': db.session.query(func.count(User.id)).scalar() or 0,
            'links': db.session.query(func.count(Link.id)).scalar() or 0,
            'clicks': (db.session.query(
                func.coalesce(func.sum(Link.click_count), 0)).scalar() or 0),
        }

        # Featured profiles showcase: active paid users with a complete
        # profile, preferring those with real avatars, most recent first.
        featured = []
        paid_users = (User.query
                      .filter(User.tier.in_(list(PAID_TIERS)))
                      .order_by(User.created_at.desc())
                      .limit(12).all())
        for u in paid_users:
            n_links = Link.query.filter_by(user_id=u.id, is_active=True).count()
            if n_links >= 2 and (u.avatar_path or u.avatar_url):
                featured.append({'user': u, 'link_count': n_links})
            if len(featured) >= 6:
                break
        if len(featured) < 3:  # fall back to any well-populated profiles
            fallback = (User.query.order_by(User.created_at.desc()).limit(20)
                        .all())
            seen = {f['user'].id for f in featured}
            for u in fallback:
                if u.id in seen:
                    continue
                n_links = Link.query.filter_by(user_id=u.id,
                                               is_active=True).count()
                if n_links >= 2:
                    featured.append({'user': u, 'link_count': n_links})
                if len(featured) >= 6:
                    break

        # Social-proof testimonials assigned to the homepage placement.
        testimonials = testimonial_home_items(limit=6)

        return render_template('index.html', stats=stats,
                               featured=featured,
                               testimonials=testimonials,
                               testimonial_placements=testimonial_home_placements(),
                               free_max=Setting.get_int('free_tier_max_links',
                                                        3))

    @app.route('/api/check-username')
    def check_username():
        """Live availability lookup used by the home-page claim box.

        The public profile URL is derived from the Display Name, so this
        endpoint validates the typed text with the Display-Name rules and
        returns the exact /<display-name-slug> URL the visitor would get.
        """
        raw = request.args.get('username', '') or ''
        ok, reason = is_claimable_display_name(raw)
        if not ok:
            # A bare slug typed into the claim box ("jane-doe") should also
            # be checked as a display name in its own right.
            pretty = raw.strip().replace('-', ' ')
            ok2, reason2 = is_claimable_display_name(pretty)
            if ok2:
                return {'available': True, 'message': reason2,
                        'url': '/' + display_name_slug(pretty)}
        return {'available': ok, 'message': reason,
                'url': ('/' + display_name_slug(raw)) if ok else None}

    @app.route('/api/check-display-name')
    def check_display_name():
        """Live Display Name availability for the register / dashboard forms."""
        ok, reason = is_claimable_display_name(
            request.args.get('display_name', ''),
            exclude_id=(current_user.id if current_user.is_authenticated
                        else None))
        return {'available': ok, 'message': reason,
                'url': ('/' + display_name_slug(request.args.get('display_name', ''))
                        if ok else None)}

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            email = (request.form.get('email') or '').strip().lower()
            password = request.form.get('password') or ''
            display_name = (request.form.get('display_name') or '').strip()
            # The Display Name is REQUIRED: it becomes the public profile
            # URL (/<display-name-slug>) and must not collide with any
            # already-claimed name. Only letters, numbers and spaces are
            # allowed for now — never an email address.
            username = (request.form.get('username') or '').strip().lower()
            # Required age confirmation (18+) — persisted as User.age_verified
            # so adult-content access controls can be enforced everywhere.
            age_confirmed = request.form.get('age_confirmed') in ('yes', 'on', 'true', '1')

            errors = []
            # --- Display Name: required, text/numbers only, unique ---------
            dn_ok, dn_reason = is_claimable_display_name(display_name)
            if not dn_ok:
                errors.append(dn_reason)
            if '@' not in email:
                errors.append('Please enter a valid email address.')
            if len(password) < 8:
                errors.append('Password must be at least 8 characters.')
            if not age_confirmed:
                errors.append('You must confirm that you are 18 years of age'
                              ' or older.')
            if username:
                if not USERNAME_RE.match(username):
                    errors.append('Usernames may only contain letters, numbers,'
                                  ' hyphens and underscores (3-31 chars).')
                elif username in RESERVED_USERNAMES:
                    errors.append('That username is reserved. Please pick'
                                  ' another.')
                elif User.query.filter_by(username=username).first():
                    errors.append('Username is already taken.')

            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('register.html'), 400

            if User.query.filter_by(email=email).first():
                flash('Email is already registered.', 'danger')
                return render_template('register.html'), 400

            # Derive the internal handle from the claimed Display Name and
            # guarantee uniqueness with a numeric suffix if needed.
            if not username:
                base = display_name_slug(display_name)[:30].replace('-', '_') \
                    or 'user'
                base = re.sub(r'^[^a-z0-9]+|[^a-z0-9]+$', '', base) or 'user'
                candidate = base
                n = 1
                while (User.query.filter_by(username=candidate).first()
                       or candidate in RESERVED_USERNAMES):
                    n += 1
                    suffix = str(n)
                    candidate = base[:31 - len(suffix)] + suffix
                username = candidate

            user = User(
                username=username,
                email=email,
                display_name=display_name,
                # Age gate: registration asserts 18+ (required checkbox), so
                # the account is created already verified for adult content.
                age_verified=bool(age_confirmed),
            )
            user.set_password(password)

            db.session.add(user)
            db.session.flush()
            Activity.record('signup', user=user, detail=f'@{username} registered')
            db.session.commit()

            login_user(user)
            # Mirror the assertion in the session so any view that checks the
            # short-lived ``age_ok`` flag agrees with the stored attribute.
            if age_confirmed:
                session['age_ok'] = True
            flash('Account created successfully!', 'success')
            return redirect(url_for('dashboard'))

        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            email = (request.form.get('email') or '').strip().lower()
            password = request.form.get('password') or ''

            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                login_user(user, remember=bool(request.form.get('remember')))
                next_page = request.args.get('next')
                # Only allow same-site redirects
                if next_page and next_page.startswith('/'):
                    return redirect(next_page)
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid email or password.', 'danger')

        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        stop_impersonation(commit=True)
        logout_user()
        flash('Logged out successfully.', 'info')
        return redirect(url_for('home'))

    # ==========================================
    # User Dashboard
    # ==========================================

    @app.route('/dashboard', methods=['GET', 'POST'])
    @login_required
    def dashboard():
        """ User Management Dashboard for Links and Profile """
        if request.method == 'POST':
            form_action = request.form.get('form_action', 'profile')

            if form_action == 'profile':
                # Display Name is the public profile handle: it must be
                # provided, contain only text/numbers, and not already be
                # claimed by another account.
                new_display = (request.form.get('display_name') or '').strip()
                dn_ok, dn_reason = is_claimable_display_name(
                    new_display, exclude_id=current_user.id)
                if not dn_ok:
                    flash(dn_reason, 'danger')
                    return redirect(url_for('dashboard'))
                current_user.display_name = new_display
                current_user.headline = request.form.get('headline')
                current_user.bio = request.form.get('bio')
                current_user.about_section = request.form.get('about_section')
                if not apply_selected_country(
                        current_user, request.form.get('country_code')):
                    flash('Please choose a valid country.', 'warning')
                # Skills: normalized list, stored back as a clean CSV string.
                skills = normalize_skills(request.form.get('skills'))
                current_user.skills = ', '.join(skills) or None
                # Adult-content flag (checkbox posts 'on' when ticked).
                current_user.is_adult_oriented = bool(
                    request.form.get('is_adult_oriented'))
                # Intro video is limited to paid tiers. Accept a full URL or
                # bare ID only after enforcing the tier restriction.
                video_raw = (request.form.get('intro_video') or '').strip()
                if current_user.tier not in PAID_TIERS:
                    if video_raw or current_user.video_platform:
                        flash('Intro videos are available on paid tiers.',
                              'warning')
                    current_user.video_platform = None
                    current_user.video_id = None
                elif video_raw:
                    parsed = parse_video_url(video_raw)
                    if parsed:
                        current_user.video_platform, current_user.video_id = parsed
                    else:
                        flash('Intro video not recognized — use a YouTube or '
                              'Vimeo link (or remove it to clear).', 'warning')
                elif request.form.get('clear_video'):
                    current_user.video_platform = None
                    current_user.video_id = None
                # GA4 measurement id: strict format check before storing.
                ga4 = (request.form.get('ga4_id') or '').strip().upper()
                if ga4:
                    if GA4_RE.match(ga4):
                        current_user.ga4_id = ga4
                    else:
                        flash('GA4 ID looks invalid — expected format like '
                              'G-ABC123XYZ9. Not changed.', 'warning')
                elif request.form.get('clear_ga4'):
                    current_user.ga4_id = None
                # Curated theme: only accept known keys; free tier is
                # restricted to the first two palettes.
                theme_key = (request.form.get('theme') or '').strip()
                if theme_key in PROFILE_THEMES:
                    allowed = set(list(PROFILE_THEMES)[:2]) \
                        if current_user.tier == 'Free' else set(PROFILE_THEMES)
                    if theme_key in allowed:
                        current_user.theme = theme_key
                    else:
                        flash('That theme is available on paid tiers.',
                              'warning')
                # Contact form opt-out checkbox.
                current_user.contact_disabled = bool(
                    request.form.get('contact_disabled'))
                # Directory visibility preference (privacy toggle). When off,
                # none of this user's links are listed on /directory; personal
                # websites never appear there regardless of this setting.
                current_user.directory_visible = bool(
                    request.form.get('directory_visible'))
                avatar_url = (request.form.get('avatar_url') or '').strip()
                if avatar_url:
                    current_user.avatar_url = avatar_url
                db.session.commit()
                flash('Profile updated successfully!', 'success')

            elif form_action == 'avatar-upload':
                upload = request.files.get('avatar_file')
                max_kb = Setting.get_int('max_upload_size_kb', 2048)
                try:
                    rel_path = save_upload_image(upload, app.config,
                                                 subdir='avatars',
                                                 max_kb=max_kb)
                except ValueError as e:
                    flash(str(e), 'danger')
                    return redirect(url_for('dashboard'))
                old = current_user.avatar_path
                current_user.avatar_path = rel_path
                db.session.commit()
                if old:
                    delete_upload_image(old, app.config)
                flash('Avatar updated!', 'success')
                return redirect(url_for('dashboard'))

            elif form_action == 'avatar-remove':
                delete_upload_image(current_user.avatar_path, app.config)
                current_user.avatar_path = None
                db.session.commit()
                flash('Uploaded avatar removed.', 'info')
                return redirect(url_for('dashboard'))

            elif form_action == 'background-upload':
                if current_user.tier not in PAID_TIERS:
                    flash('Custom backgrounds are available on paid tiers.',
                          'warning')
                    return redirect(url_for('dashboard'))
                upload = request.files.get('background_file')
                max_kb = Setting.get_int('max_upload_size_kb', 2048)
                try:
                    rel_path = save_upload_image(
                        upload, app.config, subdir='backgrounds',
                        max_kb=max_kb,
                        max_dimension=BACKGROUND_MAX_IMAGE_DIMENSION)
                except ValueError as e:
                    flash(str(e), 'danger')
                    return redirect(url_for('dashboard'))
                old = current_user.background_path
                current_user.background_path = rel_path
                db.session.commit()
                if old:
                    delete_upload_image(old, app.config)
                flash('Profile background updated!', 'success')
                return redirect(url_for('dashboard'))

            elif form_action == 'background-remove':
                if current_user.tier not in PAID_TIERS:
                    flash('Custom backgrounds are available on paid tiers.',
                          'warning')
                    return redirect(url_for('dashboard'))
                delete_upload_image(current_user.background_path, app.config)
                current_user.background_path = None
                db.session.commit()
                flash('Profile background removed.', 'info')
                return redirect(url_for('dashboard'))

            elif form_action == 'link-order':
                # Persist drag-and-drop ordering: ordered list of link ids
                order_json = request.form.get('order', '')
                try:
                    ids = [int(x) for x in order_json.split(',') if x.strip()]
                except ValueError:
                    ids = []
                for pos, link_id in enumerate(ids):
                    link = Link.query.filter_by(id=link_id,
                                                user_id=current_user.id).first()
                    if link:
                        link.position = pos
                db.session.commit()
                flash('Link order saved.', 'success')

            elif form_action == 'link-edit':
                # Edit per-link metadata (subhandle, cta, utm, category, pin)
                link_id = request.form.get('link_id', type=int)
                link = Link.query.filter_by(id=link_id,
                                            user_id=current_user.id).first()
                if link is None:
                    abort(404)
                subhandle = (request.form.get('subhandle') or '').strip().lstrip('@')[:80]
                link.subhandle = subhandle or None
                cta = (request.form.get('cta') or '').strip()[:40]
                link.cta = cta or None
                utm = (request.form.get('utm_params') or '').strip()[:300]
                if utm and not utm.startswith('?utm_'):
                    flash('UTM params must start with "?utm_" — not changed.',
                          'warning')
                else:
                    link.utm_params = utm or None
                cat = (request.form.get('category') or '').strip()[:40]
                link.category = cat if cat in LinkCategory.names() else None
                # Pinning is a paid-tier feature.
                want_pin = bool(request.form.get('is_pinned'))
                if want_pin and current_user.tier not in PAID_TIERS:
                    flash('Pinning links is available on paid tiers.',
                          'warning')
                else:
                    link.is_pinned = want_pin
                db.session.commit()
                flash('Link details saved.', 'success')

            return redirect(url_for('dashboard'))

        user_links = (Link.query.filter_by(user_id=current_user.id)
                      .order_by(Link.position.asc()).all())
        total_clicks = sum(link.click_count or 0 for link in user_links)
        # Per-profile traffic panel: read-only slice of FMD's metrics DB,
        # hardwired server-side to THIS user's handle (see fmd_profile_stats).
        profile_traffic = None
        if _fmd_enabled():
            try:
                profile_traffic = fmd_profile_stats(profile_handle(current_user))
            except Exception:
                app.logger.exception('FMD stats query failed')
                profile_traffic = None
        return render_template('dashboard.html', links=user_links,
                               total_clicks=total_clicks,
                               profile_traffic=profile_traffic,
                               fmd_enabled=_fmd_enabled(),
                               days=FMD_CHART_DAYS,
                               # NOTE: do NOT pass a ``profile_url`` render
                               # kwarg here — base.html calls the
                               # ``profile_url`` Jinja GLOBAL (the
                               # url_for_profile callable). A string kwarg
                               # would shadow it and crash the template.
                               max_upload_kb=Setting.get_int('max_upload_size_kb', 2048),
                               paid_tiers=PAID_TIERS,
                               country_choices=country_choices(),
                               link_sources=LinkSource.active(),
                               link_categories=LinkCategory.names(),
                               themes=PROFILE_THEMES,
                               example_link=_dashboard_example_link())

    @app.route('/link/add', methods=['POST'])
    @login_required
    def add_link():
        title = (request.form.get('title') or '').strip()
        url = (request.form.get('url') or '').strip()
        icon_code = request.form.get('icon_code') or 'link'
        if not title or not url:
            flash('Both a title and a URL are required.', 'danger')
            return redirect(url_for('dashboard'))

        if not (url.startswith('http://') or url.startswith('https://')):
            url = 'https://' + url

        # Enforce tier constraints on active links
        max_links = current_user.max_active_links
        if max_links is not None:
            active_count = Link.query.filter_by(user_id=current_user.id,
                                                is_active=True).count()
            if active_count >= max_links:
                flash(f"Your {current_user.tier} tier is limited to "
                      f"{max_links} active profile links. Upgrade to add more.",
                      'warning')
                return redirect(url_for('dashboard'))

        position = Link.query.filter_by(user_id=current_user.id).count()
        new_link = Link(
            user_id=current_user.id,
            title=title,
            url=url,
            icon_code=icon_code,
            position=position,
        )
        # Explicit source selection from the admin-managed catalogue; falls
        # back to automatic domain matching (Link.matched_source) otherwise.
        source_id = request.form.get('source_id')
        if source_id:
            src = db.session.get(LinkSource, int(source_id or 0)) \
                if source_id.isdigit() else None
            if src is not None and not src.is_deleted:
                new_link.source_id = src.id
                if not icon_code or icon_code == 'link':
                    new_link.icon_code = src.icon_code or 'link-45deg'
                new_link.category = src.category
                if src.cta and not request.form.get('cta'):
                    new_link.cta = src.cta
        cta = (request.form.get('cta') or '').strip()[:40]
        if cta:
            new_link.cta = cta
        subhandle = (request.form.get('subhandle') or '').strip().lstrip('@')[:80]
        if subhandle:
            new_link.subhandle = subhandle
        utm = (request.form.get('utm_params') or '').strip()
        if utm:
            if utm.startswith('?utm_') and len(utm) <= 300:
                new_link.utm_params = utm
            else:
                flash('UTM params ignored — must start with "?utm_" '
                      '(tracking still works via our own click counter).',
                      'warning')
        db.session.add(new_link)
        db.session.commit()

        flash('Link added successfully!', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/link/toggle/<int:link_id>', methods=['POST'])
    @login_required
    def toggle_link(link_id):
        link = Link.query.filter_by(id=link_id,
                                    user_id=current_user.id).first_or_404()
        if not link.is_active:
            max_links = current_user.max_active_links
            if max_links is not None:
                active_count = Link.query.filter_by(user_id=current_user.id,
                                                    is_active=True).count()
                if active_count >= max_links:
                    flash(f"Your {current_user.tier} tier is limited to "
                          f"{max_links} active links.", 'warning')
                    return redirect(url_for('dashboard'))
        link.is_active = not link.is_active
        db.session.commit()
        flash('Link visibility updated.', 'info')
        return redirect(url_for('dashboard'))

    @app.route('/link/delete/<int:link_id>', methods=['POST'])
    @login_required
    def delete_link(link_id):
        link = db.session.get(Link, link_id)
        if link is None:
            abort(404)
        # Admins may moderate any link; normal users only their own.
        if link.user_id != current_user.id and not current_user.is_admin:
            abort(403)

        db.session.delete(link)
        db.session.commit()
        flash('Link removed.', 'info')
        return redirect(url_for('dashboard'))

    @app.route('/analytics')
    @login_required
    def profile_analytics():
        """Per-profile traffic page — THIS user's /<handle> page only.

        There is deliberately no handle/username parameter anywhere on this
        route: the tenant key comes from ``profile_handle(current_user)``,
        and fmd_profile_stats() hardwires that value into the WHERE clause
        of every query against FMD's metrics DB. Users cannot browse other
        profiles' analytics even by crafting URLs — the full multi-profile
        view lives at /fmd (site admins only).
        """
        stats = None
        if _fmd_enabled():
            my_handle = profile_handle(current_user)
            if my_handle:
                try:
                    stats = fmd_profile_stats(my_handle)
                except Exception:
                    app.logger.exception('FMD stats query failed')
                    stats = None
        return render_template('analytics.html', stats=stats,
                               handle=my_handle if _fmd_enabled() else '',
                               days=FMD_CHART_DAYS,
                               profile_url=url_for_profile(current_user))

    # ==========================================
    # Public Facing Profile & Analytics
    # ==========================================

    # Canonical public profile URL is the bare /<display-name-slug>.
    # Every system /
    # application route registered on the app takes priority over it (the
    # catch-all rule below is matched last by Werkzeug, and reserved first
    # segments are rejected outright in user_by_handle()).
    @app.route('/<handle>')
    def public_profile(handle):
        """ Public SEO optimized profile page (canonical: /<display-name>)

        The URL handle is derived from the account's Display Name — never an
        email address. Accounts that have not provided a Display Name do not
        get a public profile page at all.
        """
        user = user_by_handle(handle)
        if user is None or not (user.display_name or '').strip():
            abort(404)
        active_links = (Link.query.filter_by(user_id=user.id, is_active=True)
                        .order_by(Link.position.asc()).all())
        # Adult-content gate: require a one-time age confirmation before
        # any profile content is rendered to the visitor/session.
        if user.is_adult_oriented and not session.get('age_ok'):
            return render_template('profile_agegate.html', user=user)
        pinned, categorized = group_links_for_profile(active_links)
        # Contact form is enabled unless the user opted out; admins never
        # get spammed by the demo profile either way.
        show_contact = not user.contact_disabled
        return render_template('profile.html', user=user, links=active_links,
                               skills=normalize_skills(user.skills),
                               video=video_embed(user),
                               profile=theme_for_user(user),
                               pinned_links=pinned,
                               categorized_links=categorized,
                               show_contact=show_contact)

    @app.route('/<handle>/confirm-age', methods=['POST'])
    def confirm_age(handle):
        """Age confirmation for adult-oriented profiles (18+)."""
        user = user_by_handle(handle)
        if user is None or not (user.display_name or '').strip():
            abort(404)
        if not user.is_adult_oriented:
            return redirect(url_for('public_profile',
                                    handle=profile_handle(user)))
        if request.form.get('confirm') == 'yes':
            session['age_ok'] = True
            # Remember verification on the account too, so a logged-in user
            # who is 18+ is never gated again (used by /directory & /mature).
            if current_user.is_authenticated and not current_user.age_verified:
                current_user.age_verified = True
                db.session.commit()
        return redirect(url_for('public_profile',
                                handle=profile_handle(user)))

    # ------------------------------------------------------------------
    # Public Link Source master directory (/directory)
    # ------------------------------------------------------------------
    # Browsable index of the admin-managed Link Sources ("Social Networks")
    # with member counts. Rules baked into this route:
    #   * personal-website sources are never listed (LinkSource.NON_DIRECTORY_SOURCES),
    #   * only links whose owner opted in (User.directory_visible) count,
    #   * adult sources/cards are hidden from guests and from logged-in users
    #     who have not been verified as 18+.
    # ------------------------------------------------------------------

    DIRECTORY_PER_PAGE = 24

    def _directory_member_ids(include_adult):
        """Ids of users with at least one directory-listable link."""
        q = (db.session.query(Link.user_id)
             .join(User, User.id == Link.user_id)
             .filter(Link.is_active.is_(True),
                     User.directory_visible.is_(True)))
        ids = LinkSource.directory_source_ids(include_adult=include_adult)
        if ids:
            q = q.filter(Link.source_id.in_(ids))
        else:
            return set()
        return {row[0] for row in q.distinct().all()}

    def _directory_card(src, member_ids):
        n_members = (Link.query
                     .filter(Link.source_id == src.id,
                             Link.is_active.is_(True),
                             Link.user_id.in_(member_ids))
                     .with_entities(func.count(Link.id)).scalar() or 0)
        return {
            'source': src,
            'members': n_members,
            'bg': src.bg_color or '#f8f9fa',
            'text': readable_text_color(src.bg_color, src.text_color),
            'border': src.border_color or '#dee2e6',
            'icon': f'bi bi-{src.icon_code or "link-45deg"}',
        }

    @app.route('/directory')
    def directory():
        """Master directory of available Link Sources (social networks)."""
        include_adult = directory_adult_ok()
        sources = LinkSource.visible_to(include_adult)
        directory_sources = [s for s in sources if not s.is_directory_excluded]
        member_ids = _directory_member_ids(include_adult)
        cards = [_directory_card(s, member_ids) for s in directory_sources]
        # Alphabetical ordering by display name (brand colors come from the
        # source record; see templates/directory.html).
        cards.sort(key=lambda c: (c['source'].name or '').casefold())
        total_pages = max(1, -(-len(cards) // DIRECTORY_PER_PAGE))
        page = max(1, request.args.get('page', 1, type=int))
        page = min(page, total_pages)
        start = (page - 1) * DIRECTORY_PER_PAGE
        return render_template('directory.html',
                               cards=cards[start:start + DIRECTORY_PER_PAGE],
                               page=page, total_pages=total_pages,
                               total_sources=len(cards),
                               total_members=len(member_ids),
                               include_adult=include_adult,
                               gated_adult=bool(len(sources) > len(directory_sources)))

    @app.route('/directory/age-gate', methods=['POST'])
    def directory_age_gate():
        """One-time 18+ confirmation that unlocks adult directory cards."""
        if not current_user.is_authenticated:
            flash('Please log in to view 18+ networks.', 'info')
            return redirect(url_for('login', next=url_for('directory')))
        if request.form.get('confirm') == 'yes':
            session['age_ok'] = True
            if not current_user.age_verified:
                current_user.age_verified = True
                db.session.commit()
            flash('Adult networks are now visible.', 'success')
        return redirect(url_for('directory'))

    # ------------------------------------------------------------------
    # Country search (/countries, /country/<slug>)
    # ------------------------------------------------------------------
    # Same visibility contract as the Link Source directory: only users
    # who checked "make my profile searchable" (User.directory_visible)
    # AND have a contenting profile count. Countries with zero qualifying
    # members never render — Vatican City stays hidden until a verified
    # user there opts in — so the map is self-curating.
    #
    # Unique presentation: an alphabetical *continent shelf* layout. The
    # page groups live country tiles under continent headings (Africa,
    # Asia, Europe, ...) computed from the ISO code via pycountry when
    # available (else a curated region map), each tile prefixed with its
    # flag emoji and member count, plus a "Top countries" leaderboard.
    # Unrecognized free-text locations fall into an "Elsewhere" shelf so
    # no opted-in user is ever silently dropped.
    # ------------------------------------------------------------------

    COUNTRY_PAGE_SIZE_DEFAULT = 24

    # Fallback ISO alpha-2 → UN-style region grouping (used when pycountry
    # is not installed; kept deliberately coarse).
    _REGION_FALLBACK = {
        'Africa': ['DZ','AO','BJ','BW','BF','BI','CM','CV','CF','TD','KM','CG',
                   'CD','CI','DJ','EG','GQ','ER','SZ','ET','GA','GM','GH','GN',
                   'GW','KE','LS','LR','LY','MG','MW','ML','MR','MU','MA','MZ',
                   'NA','NE','NG','RW','ST','SN','SC','SL','SO','ZA','SS','SD',
                   'TZ','TG','TN','UG','EH','ZM','ZW','SH'],
        'Americas': ['AG','AR','BS','BB','BZ','BM','BO','BR','CA','CL','CO','CR',
                     'CU','DM','DO','EC','SV','GD','GL','GT','GY','HT','HN','JM',
                     'MX','NI','PA','PY','PE','KN','LC','VC','SR','TT','US','UY',
                     'VE','PR','VI','VG','AI','AW','BQ','CW','SX','TC','PM','MS',
                     'KY','FK','GF','GP','MQ','NF'],
        'Asia': ['AF','AM','AZ','BH','BD','BT','BN','KH','CN','CY','GE','IN','ID',
                 'IR','IQ','IL','JP','JO','KZ','KP','KR','KW','KG','LA','LB','MO',
                 'MY','MV','MN','MM','NP','OM','PK','PH','QA','SA','SG','LK','SY',
                 'TJ','TH','TL','TR','TM','AE','UZ','VN','YE','HK','TW','PS'],
        'Europe': ['AL','AD','AT','BY','BE','BA','BG','HR','CZ','DK','EE','FI','FR',
                   'DE','GI','GR','HU','IS','IE','IT','XK','LV','LI','LT','LU','MT',
                   'MD','MC','ME','NL','MK','NO','PL','PT','RO','RU','SM','RS','SK',
                   'SI','ES','SE','CH','UA','GB','VA','FO','GG','IM','JE','SJ','YT'],
        'Oceania': ['AU','FJ','KI','MH','NR','NZ','NU','PF','PG','PN','WS','SB','TK',
                    'TO','TV','VU','NC','MP','GU','AS','CK','WF','HM'],
    }
    _CODE_TO_REGION = {}
    for _region, _codes in _REGION_FALLBACK.items():
        for _c in _codes:
            _CODE_TO_REGION.setdefault(_c, _region)

    def _continent_of(code):
        """Continent/region label for a bucket code ('US' -> 'Americas').

        Slug buckets (lowercase, unrecognized places) always land in
        "Elsewhere". Prefers pycountry's region/sub-region data when the
        optional package is installed.
        """
        if not code or code != code.upper() or not code.isalpha():
            return 'Elsewhere'
        try:
            import pycountry
            c = pycountry.countries.get(alpha_2=code)
            if c is not None:
                sub = getattr(c, 'sub_region', None) or ''
                for name, keys in (('Africa', ('Africa',)),
                                   ('Americas', ('America',)),
                                   ('Asia', ('Asia',)),
                                   ('Europe', ('Europe',)),
                                   ('Oceania', ('Oceania', 'Pacific'))):
                    if any(k.lower() in sub.lower() for k in keys):
                        return name
        except Exception:
            pass
        return _CODE_TO_REGION.get(code, 'Elsewhere')

    CONTINENT_ORDER = ('Africa', 'Americas', 'Asia', 'Europe', 'Oceania',
                       'Elsewhere')

    @app.route('/countries')
    def countries():
        """Country search: every country that has searchable members."""
        include_adult = directory_adult_ok()
        buckets = country_buckets(include_adult)
        total_profiles = sum(b['members'] for b in buckets)
        # Shelf layout: group by continent, alphabetical within each shelf.
        shelves = []
        grouped = {}
        for b in buckets:
            grouped.setdefault(_continent_of(b['code']), []).append(b)
        for region in CONTINENT_ORDER:
            items = grouped.pop(region, None)
            if items:
                shelves.append({'region': region, 'items': items})
        for region in sorted(grouped):  # defensive: unexpected labels
            shelves.append({'region': region, 'items': grouped[region]})
        top = sorted(buckets, key=lambda b: (-b['members'],
                                             (b['name'] or '').casefold()))
        sidebar_n = max(3, min(Setting.get_int('country_sidebar_top', 10), 25))
        q = (request.args.get('q') or '').strip().lower()
        filtered = [b for b in buckets
                    if q and q in (b['name'] or '').lower()] if q else None
        return render_template('countries.html',
                               shelves=shelves,
                               buckets=buckets,
                               filtered=filtered,
                               q=q,
                               top=top[:sidebar_n],
                               total_countries=len(buckets),
                               total_profiles=total_profiles,
                               include_adult=include_adult)

    @app.route('/country/<slug>')
    def country_detail(slug):
        """Member listing for one country bucket."""
        include_adult = directory_adult_ok()
        want = (slug or '').strip().lower()
        bucket = next((b for b in country_buckets(include_adult)
                       if country_slug(b['code']) == want), None)
        if bucket is None:
            abort(404)

        per_page = max(1, min(Setting.get_int('country_page_size',
                                              COUNTRY_PAGE_SIZE_DEFAULT), 200))
        featured_max = max(0, min(Setting.get_int('directory_featured_max', 5),
                                  20))
        member_ids = _country_profile_ids(include_adult)
        rows = (User.query
                .filter(User.id.in_(member_ids),
                        User.directory_visible.is_(True),
                        func.upper(User.country_code) == want.upper())
                .all())
        cards = []
        for u in rows:
            best = (Link.query
                    .filter(Link.user_id == u.id,
                            Link.is_active.is_(True),
                            Link.source_id.in_(
                                LinkSource.directory_source_ids(
                                    include_adult) or {-1}))
                    .first())
            cards.append({
                'user': u,
                'link': best,
                'paid': u.tier in PAID_TIERS,
                'featured': bool(getattr(u, 'is_featured', False)),
            })
        pool = [c for c in cards if c['paid'] or c['featured']]
        random.shuffle(pool)
        featured = pool[:featured_max]
        featured_ids = {c['user'].id for c in featured}
        rest = sorted((c for c in cards if c['user'].id not in featured_ids),
                      key=lambda c: ((c['user'].display_name or
                                      c['user'].username).casefold(),
                                     c['user'].username))
        total_pages = max(1, -(-len(rest) // per_page))
        page = min(max(1, request.args.get('page', 1, type=int)), total_pages)
        start = (page - 1) * per_page
        members = rest[start:start + per_page]

        other = [b for b in country_buckets(include_adult)
                 if b['code'] != bucket['code']][:10]
        return render_template('country_detail.html',
                               bucket=bucket,
                               flag=bucket['flag'],
                               featured=featured,
                               members=members,
                               page=page, total_pages=total_pages,
                               total_members=len(cards),
                               total_rest=len(rest),
                               per_page=per_page,
                               other_countries=other,
                               include_adult=include_adult)

    # Link Source detail page (/directory/<slug>)
    # ------------------------------------------------------------------
    # Lists the member profiles associated with one network. Ordering rules:
    #   1. Paying users (any PAID_TIERS account, or an admin-pinned
    #      ``User.is_featured`` account) occupy the "featured" slots at the
    #      top — at most Setting('directory_featured_max', default 5) of
    #      them, chosen at random on every request.
    #   2. Everyone else can be filtered by display name or skills and sorted
    #      by display name, then paginated with the admin-configurable
    #      Setting('directory_page_size').
    # Card contents (name, avatar thumbnail, external network-profile link,
    # and an "AMP Profile" link to the local site profile) are rendered by
    # templates/directory_source.html from the dicts built below.
    # ------------------------------------------------------------------

    DIRECTORY_FEATURED_MAX_DEFAULT = 5
    DIRECTORY_PAGE_SIZE_DEFAULT = 24

    def _directory_settings():
        """Admin-tunable directory display settings (clamped, safe)."""
        per_page = Setting.get_int('directory_page_size',
                                   DIRECTORY_PAGE_SIZE_DEFAULT)
        featured_max = Setting.get_int('directory_featured_max',
                                       DIRECTORY_FEATURED_MAX_DEFAULT)
        return max(1, min(per_page, 200)), max(0, min(featured_max, 20))

    def _directory_profile_rows(src, member_ids):
        """One row per opted-in member who lists an active link on ``src``.

        Returns dicts carrying the user, their representative link for this
        network, and the tier info needed for featured/alphabetical split.
        """
        rows = (Link.query
                .filter(Link.source_id == src.id,
                        Link.is_active.is_(True),
                        Link.user_id.in_(member_ids or {-1}))
                .all())
        seen, out = set(), []
        for link in rows:
            owner = link.owner
            if owner is None or owner.id in seen:
                continue
            # Prefer the link whose URL host actually matches this source so
            # the card's external link points at the right network profile.
            best = link
            for other in rows:
                if other.user_id != owner.id:
                    continue
                if (other.matched_source is not None
                        and other.matched_source.id == src.id
                        and best.matched_source is None):
                    best = other
            seen.add(owner.id)
            out.append({
                'user': owner,
                'link': best,
                'paid': owner.tier in PAID_TIERS,
                'featured': bool(getattr(owner, 'is_featured', False)),
            })
        # Collapse duplicates defensively (one entry per member).
        deduped, done = [], set()
        for row in out:
            if row['user'].id in done:
                continue
            done.add(row['user'].id)
            deduped.append(row)
        return deduped

    @app.route('/directory/<path:source_name>')
    def directory_source(source_name):
        """Member listing / Link Source detail page."""
        include_adult = directory_adult_ok()
        slug = (source_name or '').strip().lower()
        src = next((s for s in LinkSource.visible_to(include_adult)
                    if source_slug(s.name) == slug), None)
        if src is None or src.is_directory_excluded:
            abort(404)

        per_page, featured_max = _directory_settings()
        member_ids = _directory_member_ids(include_adult)
        rows = _directory_profile_rows(src, member_ids)

        # Only countries that have at least one profile on this network.
        country_names = {}
        for row in rows:
            code = (row['user'].country_code or '').upper()
            if code:
                country_names.setdefault(
                    code, row['user'].country_name or code)
        countries = sorted(country_names.items(),
                           key=lambda kv: (kv[1].casefold(), kv[0]))
        country = (request.args.get('country') or '').strip().upper()
        if country not in country_names:
            country = ''
        if country:
            rows = [r for r in rows
                    if (r['user'].country_code or '').upper() == country]

        query = (request.args.get('q') or '').strip()[:100]
        query_key = query.casefold()
        if query_key:
            rows = [
                row for row in rows
                if query_key in (row['user'].display_name or '').casefold()
                or query_key in (row['user'].skills or '').casefold()
            ]

        # 1) Featured: paying accounts (+ admin-pinned ones), max N at random.
        pool = [r for r in rows if r['paid'] or r['featured']]
        random.shuffle(pool)
        featured = pool[:featured_max]
        featured_ids = {r['user'].id for r in featured}

        sort = request.args.get('sort', 'name_asc')
        if sort not in {'name_asc', 'name_desc'}:
            sort = 'name_asc'

        # 2) The rest sorted by display name (username breaks ties).
        rest = sorted(
            (r for r in rows if r['user'].id not in featured_ids),
            key=lambda r: ((r['user'].display_name or
                            r['user'].username).casefold(),
                           r['user'].username),
            reverse=sort == 'name_desc')

        total_pages = max(1, -(-len(rest) // per_page))
        try:
            page = int(request.args.get('page', 1))
        except (TypeError, ValueError):
            page = 1
        page = min(max(1, page), total_pages)
        start = (page - 1) * per_page
        members = rest[start:start + per_page]

        return render_template(
            'directory_source.html',
            source=src,
            card=_directory_card(src, member_ids),
            text=readable_text_color(src.bg_color, src.text_color),
            site_name='AllMyProfiles',
            featured=featured,
            members=members,
            page=page,
            total_pages=total_pages,
            total_members=len(rows),
            total_rest=len(rest),
            per_page=per_page,
            query=query,
            sort=sort,
            countries=countries,
            country=country,
            include_adult=include_adult,
        )

    # ------------------------------------------------------------------
    # AI grounding page — machine-readable facts about this site for
    # LLM crawlers (GPTBot, PerplexityBot, ClaudeBot, ...). Linked from
    # the footer. Kept crawlable via robots.txt; noindex is NOT set so
    # search engines can surface it too.
    # ------------------------------------------------------------------
    @app.route('/ai-grounding')
    def ai_grounding():
        base = _site_base_url()
        today = datetime.now().strftime('%Y-%m-%d')
        profile_count = User.query.count()
        link_count = db.session.query(func.count(Link.id)).scalar() or 0
        click_total = db.session.query(func.coalesce(func.sum(
            func.coalesce(Link.click_count, 0)), 0)).scalar() or 0
        featured = (User.query.order_by(User.created_at.desc()).limit(5).all())
        return render_template(
            'ai_grounding.html', base=base, today=today,
            profile_count=profile_count, link_count=link_count,
            click_total=click_total, featured=featured, TIERS=TIERS)

    @app.route('/llms.txt')
    def llms_txt():
        """Plain-text grounding summary for models that fetch /llms.txt."""
        base = _site_base_url()
        body = f"""# AllMyProfiles

> One link for all your profiles, content, and business. Claim a unique
> username, unify your digital footprint, and optimize your personal brand
> for search and AI discovery. Every public profile ships with JSON-LD
> schema.org markup and semantic HTML so Google, ChatGPT, and Perplexity
> can parse it cleanly.

## Key resources
- Home / sign up: {base}/
- AI grounding page (full facts): {base}/ai-grounding
- Help center: {base}/help
- Frequently asked questions: {base}/faq
- Contact / support: {base}/contact
- Terms of Service: {base}/terms
- Privacy Policy: {base}/privacy
- Sitemap index: {base}/sitemap.xml
- Static pages sitemap: {base}/sitemap-static.xml
- Profiles sitemap: {base}/sitemap-profiles-1.xml

## Plans
- Free: basic profile, up to 3 links, platform branding, basic view counts.
- Expanded: more links, custom background/styles, standard analytics.
- Full: advanced analytics, tracking pixels (Meta/Google), zero ads, priority support.
- Custom: everything in Full plus early beta access and dedicated admin communication.

## Public profile format
- Canonical URL: {base}/<display-name-slug>
- Machine-readable link feed: {base}/<display-name-slug>/links.xml
- Each profile embeds schema.org ProfilePage + Person JSON-LD with sameAs
  links to the owner's external profiles.
"""
        resp = Response(body, mimetype='text/plain')
        resp.headers['Cache-Control'] = 'public, max-age=3600'
        return resp

    @app.route('/redirect/<int:link_id>')
    def redirect_link(link_id):
        """ Outbound tracking wrapper route """
        link = db.session.get(Link, link_id)
        if link is None:
            abort(404)
        link.click_count = (link.click_count or 0) + 1
        db.session.commit()
        return redirect(link.url)

    @app.route('/uploads/<path:filename>')
    def uploaded_file(filename):
        """Serve user-uploaded images from the local uploads folder."""
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

    # ------------------------------------------------------------------
    # Favicon (generated on the fly — no binary assets to commit)
    # ------------------------------------------------------------------
    _FAVICON_CACHE = {}

    @app.route('/favicon.ico')
    @app.route('/apple-touch-icon.png')
    @app.route('/apple-touch-icon-<int:size>x<int:_size2>.png')
    @app.route('/icon-<int:size>.png')
    def favicon(size=64, **_ignored):
        """Dynamically render the brand icon at the requested size.

        Serves .ico for /favicon.ico and PNG for every other route so a
        single generator covers all device/viewport icon requests
        (desktop tabs, iOS home screen, Android, pinned tiles).
        """
        want_ico = request.path == '/favicon.ico'
        size = max(16, min(int(size or 64), 512))
        key = (size, want_ico)
        if key not in _FAVICON_CACHE:
            _FAVICON_CACHE[key] = _render_favicon(size, want_ico)
        data, mime = _FAVICON_CACHE[key]
        resp = Response(data, mimetype=mime)
        resp.headers['Cache-Control'] = 'public, max-age=604800'
        return resp


def _render_favicon(size, as_ico):
    """Draw the AllMyProfiles glyph (gradient tile + white link mark)."""
    import base64
    import io
    from PIL import Image, ImageDraw

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'static', 'favicon-source.png')
    if os.path.exists(path):
        img = Image.open(path).convert('RGBA').resize((size, size),
                                                      Image.LANCZOS)
    else:
        s = 512
        img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r = s * 0.22
        # Rounded gradient tile (top-left indigo -> bottom-right blue).
        tile = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        for y in range(s):
            t = y / (s - 1)
            color = (int(0x35 + (0x4C - 0x35) * t),
                     int(0x30 + (0x8D - 0x30) * t),
                     int(0x6B + (0xFF - 0x6B) * t), 255)
            td.line([(0, y), (s, y)], fill=color)
        mask = Image.new('L', (s, s), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1],
                                               radius=r, fill=255)
        tile.putalpha(mask)
        img = tile
        draw = ImageDraw.Draw(img)
        # Two interlocking chain links, drawn diagonally in white.
        lw = int(s * 0.075)
        rad = s * 0.115
        off = s * 0.135
        cx, cy = s / 2, s / 2
        for dx in (-off, off):
            x0 = cx + dx - rad * 1.35
            y0 = cy + dx - rad
            x1 = cx + dx + rad * 1.35
            y1 = cy + dx + rad
            draw.rounded_rectangle([x0, y0, x1, y1], radius=rad,
                                   outline='white', width=lw)
        img = img.rotate(-45, resample=Image.BICUBIC, expand=False)
        img = img.resize((size, size), Image.LANCZOS)

    buf = io.BytesIO()
    if as_ico:
        sizes = [x for x in (16, 24, 32, 48) if x <= max(size, 48)]
        img.save(buf, format='ICO', sizes=[(x, x) for x in sizes] or [(size, size)])
        mime = 'image/x-icon'
    else:
        img.save(buf, format='PNG')
        mime = 'image/png'
    return buf.getvalue(), mime


# ==========================================
# User Inbox Routes
# ==========================================

def register_inbox_routes(app):

    def _inbox_query(user, view='inbox'):
        q = (MessageRead.query
             .join(Message, MessageRead.message_id == Message.id)
             .filter(MessageRead.user_id == user.id))
        if view == 'archived':
            q = q.filter(MessageRead.is_archived.is_(True))
        elif view == 'unread':
            q = q.filter(MessageRead.is_archived.is_(False),
                         MessageRead.is_read.is_(False))
        else:  # 'inbox'
            q = q.filter(MessageRead.is_archived.is_(False))
        return q

    @app.route('/inbox')
    @login_required
    def inbox():
        """Personal message inbox with unread indicators + filters."""
        view = request.args.get('view', 'inbox')
        if view not in ('inbox', 'unread', 'archived'):
            view = 'inbox'
        category = request.args.get('category', '')

        query = _inbox_query(current_user, view)
        if category and category in MESSAGE_CATEGORIES:
            query = query.filter(Message.category == category)
        reads = (query.order_by(MessageRead.delivered_at.desc()).all())

        # Sort visible list: unread first, then most severe, then newest.
        def sort_key(r):
            return (r.is_read, -r.message.severity_info['rank'],
                    -r.delivered_at.timestamp())
        reads = sorted(reads, key=sort_key)

        # Visitor contact messages (from public profile forms) shown in the
        # same inbox, newest first; archived tab includes archived contacts.
        cq = ContactMessage.query.filter_by(recipient_id=current_user.id)
        if view == 'archived':
            cq = cq.filter(ContactMessage.is_archived.is_(True))
        elif view == 'unread':
            cq = cq.filter(ContactMessage.is_archived.is_(False),
                           ContactMessage.is_read.is_(False))
        else:  # 'inbox'
            cq = cq.filter(ContactMessage.is_archived.is_(False))
        contacts = cq.order_by(ContactMessage.created_at.desc()).all()

        unread_total = current_user.unread_messages
        return render_template('inbox.html', reads=reads, contacts=contacts,
                               view=view, category=category,
                               unread_total=unread_total,
                               categories=MESSAGE_CATEGORIES)

    @app.route('/inbox/<int:message_id>', methods=['GET', 'POST'])
    @login_required
    def inbox_message(message_id):
        """Read a single message; opening it marks it as read."""
        read = MessageRead.query.filter_by(message_id=message_id,
                                           user_id=current_user.id).first()
        if read is None:
            abort(404)
        if not read.is_read:
            read.is_read = True
            read.read_at = datetime.now()
            db.session.commit()
        return render_template('inbox_message.html', read=read,
                               message=read.message)

    @app.route('/inbox/read-all', methods=['POST'])
    @login_required
    def inbox_read_all():
        (MessageRead.query
         .filter_by(user_id=current_user.id, is_read=False)
         .update({'is_read': True, 'read_at': datetime.now()},
                 synchronize_session=False))
        db.session.commit()
        flash('All messages marked as read.', 'success')
        return redirect(url_for('inbox'))

    @app.route('/inbox/archive/<int:message_id>', methods=['POST'])
    @login_required
    def inbox_archive(message_id):
        read = MessageRead.query.filter_by(message_id=message_id,
                                           user_id=current_user.id).first_or_404()
        read.is_archived = True
        db.session.commit()
        flash('Message archived.', 'info')
        return redirect(request.referrer or url_for('inbox'))

    @app.route('/inbox/unarchive/<int:message_id>', methods=['POST'])
    @login_required
    def inbox_unarchive(message_id):
        read = MessageRead.query.filter_by(message_id=message_id,
                                           user_id=current_user.id).first_or_404()
        read.is_archived = False
        db.session.commit()
        flash('Message restored to your inbox.', 'success')
        return redirect(request.referrer or url_for('inbox'))

    # ---------------- Contact messages in the inbox --------------------

    @app.route('/inbox/contact/<int:contact_id>', methods=['GET', 'POST'])
    @login_required
    def inbox_contact(contact_id):
        """Open a visitor contact message (marks it read)."""
        msg = ContactMessage.query.filter_by(id=contact_id,
                                             recipient_id=current_user.id).first_or_404()
        if request.method == 'POST':
            # Reply from the inbox: deliver to the sender as an admin-style
            # message? No — record status and mailto-free note. We mark the
            # item 'replied' and show a confirmation; actual reply happens
            # via the sender's email shown on the page.
            action = request.form.get('action')
            if action == 'reply-marked':
                msg.status = 'replied'
                db.session.commit()
                flash('Marked as replied.', 'success')
            return redirect(url_for('inbox_contact', contact_id=msg.id))
        if not msg.is_read:
            msg.is_read = True
            if msg.status == 'new':
                msg.status = 'read'
            db.session.commit()
        return render_template('inbox_contact.html', msg=msg)

    @app.route('/inbox/contact/archive/<int:contact_id>', methods=['POST'])
    @login_required
    def inbox_contact_archive(contact_id):
        msg = ContactMessage.query.filter_by(id=contact_id,
                                             recipient_id=current_user.id).first_or_404()
        msg.is_archived = True
        db.session.commit()
        flash('Contact message archived.', 'info')
        return redirect(request.referrer or url_for('inbox'))

    @app.route('/inbox/contact/delete/<int:contact_id>', methods=['POST'])
    @login_required
    def inbox_contact_delete(contact_id):
        msg = ContactMessage.query.filter_by(id=contact_id,
                                             recipient_id=current_user.id).first_or_404()
        db.session.delete(msg)
        db.session.commit()
        flash('Contact message deleted.', 'info')
        return redirect(url_for('inbox'))


# ==========================================
# Public profile contact + abuse report routes
# ==========================================

CONTACT_EMAIL_RE = re.compile(r'^[^@\s]{1,60}@[^@\s.]+(\.[^@\s.]+)+$')


def register_public_interaction_routes(app):

    @app.route('/<handle>/submit-contact', methods=['POST'])
    def submit_contact(handle):
        """Profile 'Contact Me' modal -> recipient's inbox (unread)."""
        user = user_by_handle(handle)
        if user is None or not (user.display_name or '').strip():
            abort(404)
        # Honeypot: silently accept bots that filled the hidden field.
        if request.form.get('website'):
            return render_template('contact_thanks.html')
        name = (request.form.get('name') or '').strip()[:80]
        email = (request.form.get('email') or '').strip().lower()[:120]
        subject = (request.form.get('subject') or '').strip()[:120]
        body = (request.form.get('message') or '').strip()
        if not name or not email or not body or len(body) > 5000:
            flash('Please fill in your name, email, and a message '
                  '(max 5000 characters).', 'danger')
            return redirect(url_for('public_profile', handle=profile_handle(user)))
        if not CONTACT_EMAIL_RE.match(email):
            flash('That email address looks invalid.', 'danger')
            return redirect(url_for('public_profile', handle=profile_handle(user)))
        # Rate limit: max 3 contact messages per IP per hour.
        ip = request.remote_addr
        since = datetime.now() - timedelta(hours=1)
        recent = (ContactMessage.query
                  .filter(ContactMessage.sender_ip == ip,
                          ContactMessage.created_at >= since)
                  .count())
        if recent >= 3:
            flash('You have sent several messages recently. Please try '
                  'again later.', 'warning')
            return redirect(url_for('public_profile', handle=profile_handle(user)))
        msg = ContactMessage(recipient_id=user.id, sender_name=name,
                             sender_email=email, subject=subject or None,
                             body=body, sender_ip=ip)
        db.session.add(msg)
        Activity.record('contact_received', user=user, detail=f'from {email}')
        db.session.commit()
        # Optional Gmail/SMTP notification to the profile owner. Wrapped so a
        # mail failure can NEVER block the message — the inbox record above
        # is already committed and is the guaranteed delivery path.
        if user.email and mail_util.is_configured():
            try:
                ok, detail = mail_util.send_email(
                    user.email,
                    subject=('New message on your AllMyProfiles profile'
                             + (f': {subject}' if subject else '')),
                    body=(f'{name} <{email}> wrote to you via your public '
                          f'profile:\n\n{body}\n\n'
                          'Reply from your inbox on this site.'))
                Activity.record('contact_mail', user=user,
                                detail=('sent' if ok else f'skipped: {detail[:120]}'))
                db.session.commit()
            except Exception:
                pass  # email must never break functionality
        return render_template('contact_thanks.html')

    @app.route('/report', methods=['GET'])
    def report_abuse():
        """Report Abuse landing page (list reasons / pick a profile)."""
        target = (request.args.get('profile') or '').strip().lstrip('@')
        return render_template('report.html', target=target,
                               reasons=REPORT_REASONS)

    @app.route('/report/<username>', methods=['GET'])
    def report_abuse_profile(username):
        user = User.query.filter_by(username=username.lower()).first_or_404()
        return render_template('report.html', target=user.username,
                               target_user=user, reasons=REPORT_REASONS)

    @app.route('/report/submit', methods=['POST'])
    def report_abuse_submit():
        username = (request.form.get('target_username') or '').strip().lstrip('@')
        target = User.query.filter_by(username=username.lower()).first()
        reason = request.form.get('reason') or 'other'
        details = (request.form.get('details') or '').strip()
        if target is None or reason not in REPORT_REASONS or not details:
            flash('Pick a profile, a reason, and describe the issue.', 'danger')
            return redirect(url_for('report_abuse', profile=username))
        link_id = request.form.get('link_id', type=int)
        if link_id is not None:
            link = Link.query.filter_by(id=link_id, user_id=target.id).first()
            link_id = link.id if link else None
        report = AbuseReport(
            reporter_name=(request.form.get('reporter_name') or '').strip()[:80] or None,
            reporter_email=(request.form.get('reporter_email') or '').strip().lower()[:120] or None,
            target_user_id=target.id, target_link_id=link_id,
            reason=reason, details=details[:5000])
        db.session.add(report)
        db.session.flush()  # assign report.id before triage notes reference it
        Activity.record('report_submitted', user=target,
                        detail=f'{reason} (report #{report.id})')
        try:
            apply_triage_rules(report)  # auto-status by admin-defined rules
        except Exception:
            pass  # automation must never block a public report submission
        db.session.commit()
        return render_template('report_thanks.html')


# ==========================================
# Admin / SaaS Dashboard Routes
# ==========================================

def _is_impersonating():
    return IMPERSONATION_KEY in session


def _real_admin():
    """The actual admin account (even while impersonating)."""
    uid = session.get(IMPERSONATION_KEY)
    if uid:
        return db.session.get(User, uid)
    return current_user._get_current_object() if current_user.is_authenticated else None


def stop_impersonation(commit=True):
    """Restore the admin session and log an impersonate_end activity."""
    if IMPERSONATION_KEY not in session:
        return
    target = current_user._get_current_object() if current_user.is_authenticated else None
    admin = db.session.get(User, session.pop(IMPERSONATION_KEY))
    if admin and target:
        Activity.record('impersonate_end', user=target, actor=admin)
    if commit:
        db.session.commit()


# ==========================================
# Messaging helpers (used by web + check_links.py)
# ==========================================

def deliver_message(message, users):
    """Create per-user MessageRead rows for `users` (deduped).

    Returns the number of *new* deliveries. Caller commits.
    """
    existing = {r.user_id for r in
                MessageRead.query.filter_by(message_id=message.id)
                .options(db.load_only(MessageRead.user_id)).all()} \
        if message.id else set()
    delivered = 0
    for u in users:
        if u.id in existing:
            continue
        db.session.add(MessageRead(message_id=message.id, user_id=u.id))
        existing.add(u.id)
        delivered += 1
    return delivered


def send_admin_message(subject, body, category='note', severity=None,
                       sender=None, source='direct', audience='all',
                       target_user=None, action_url=None, action_label=None,
                       dedupe_key=None, commit=True):
    """Create a Message and deliver it to the chosen audience.

    audience: 'one' (target_user), 'all', 'paid' (PAID_TIERS), or a tier name.
    Returns (message, delivered_count).
    """
    if category not in MESSAGE_CATEGORIES:
        raise ValueError(f'Unknown message category: {category!r}')
    if severity not in MESSAGE_SEVERITIES:
        severity = MESSAGE_CATEGORIES[category]['default_severity']

    message = Message(subject=subject[:200], body=body, category=category,
                      severity=severity, sender=sender, source=source,
                      action_url=action_url, action_label=action_label,
                      dedupe_key=dedupe_key)
    db.session.add(message)
    db.session.flush()  # assign message.id

    if audience == 'one':
        recipients = [target_user] if target_user else []
    elif audience == 'paid':
        recipients = User.query.filter(User.tier.in_(PAID_TIERS)).all()
    elif audience in TIERS:
        recipients = User.query.filter_by(tier=audience).all()
    else:  # 'all'
        recipients = User.query.all()

    delivered = deliver_message(message, recipients)
    if delivered == 0:
        # Nobody received it — don't leave an orphan message behind.
        db.session.delete(message)
        db.session.flush()
        return None, 0

    Activity.record('message_sent', actor=sender,
                    detail=f'{category}/{severity} → {delivered} recipient(s): '
                           f'{subject[:80]}')
    if commit:
        db.session.commit()
    return message, delivered


def notify_broken_links(user, broken_results, days_between_notifications=7,
                        now=None):
    """Send a 'broken link report' inbox message to `user`.

    Spam guard: skips if any of the same links was notified about within the
    cooldown window. Returns the Message if one was sent, else None.
    Caller commits.
    """
    from datetime import timedelta
    now = now or datetime.now()
    cooldown = timedelta(days=max(0, int(days_between_notifications)))

    # Never re-notify while every link in this set was reported recently.
    for res in broken_results:
        if res.last_notified_at and now - res.last_notified_at < cooldown:
            return None

    lines = []
    for res in broken_results:
        status = res.http_status if res.http_status else 'unreachable'
        since = ''
        if res.broken_since:
            since = f' (down since {res.broken_since:%Y-%m-%d})'
        lines.append(f'• “{res.link.title}” → HTTP {status}{since}')
    body = ('We checked the links on your public profile and found '
            'some that are no longer working:\n\n'
            + '\n'.join(lines)
            + '\n\nPlease update or remove them from your dashboard so '
              'visitors don’t hit dead ends.')

    link_ids = sorted(r.link_id for r in broken_results)
    message = Message(
        subject=f'{len(broken_results)} of your links appear broken',
        body=body,
        category='broken_links',
        severity=MESSAGE_CATEGORIES['broken_links']['default_severity'],
        source='link_check',
        dedupe_key='lc:' + ','.join(str(i) for i in link_ids)[:110],
    )
    db.session.add(message)
    db.session.flush()
    deliver_message(message, [user])
    for res in broken_results:
        res.last_notified_at = now
    Activity.record('message_sent', user=user,
                    detail=f'broken-link report ({len(broken_results)} links)')
    return message


def register_admin_routes(app):

    @app.route('/admin')
    @admin_required
    def admin_dashboard():
        """SaaS-style metrics overview + recent activity feed."""
        now = datetime.now()
        day = timedelta(days=1)

        total_users = db.session.scalar(func.count(User.id))
        total_links = db.session.scalar(func.count(Link.id))
        total_clicks = db.session.scalar(
            func.coalesce(func.sum(Link.click_count), 0))

        signups_today = db.session.scalar(
            db.select(func.count(User.id)).where(User.created_at >= now - day))
        signups_week = db.session.scalar(
            db.select(func.count(User.id)).where(User.created_at >= now - 7 * day))

        tier_counts = dict(db.session.query(User.tier, func.count(User.id))
                           .group_by(User.tier).all())

        top_links = (db.session.query(Link, User.username)
                     .join(User, Link.user_id == User.id)
                     .order_by(func.coalesce(Link.click_count, 0).desc())
                     .limit(10).all())
        recent_users = (User.query.order_by(User.created_at.desc())
                        .limit(10).all())
        activities = (Activity.query
                      .order_by(Activity.created_at.desc())
                      .limit(30).all())

        return render_template('admin/dashboard.html',
                               total_users=total_users,
                               total_links=total_links,
                               total_clicks=total_clicks,
                               signups_today=signups_today,
                               signups_week=signups_week,
                               tier_counts=tier_counts,
                               top_links=top_links,
                               recent_users=recent_users,
                               activities=activities)

    @app.route('/admin/users')
    @admin_required
    def admin_users():
        q = (request.args.get('q') or '').strip().lower()
        query = User.query
        if q:
            like = f'%{q}%'
            query = query.filter(db.or_(User.username.ilike(like),
                                        User.email.ilike(like),
                                        User.display_name.ilike(like)))
        page = request.args.get('page', 1, type=int)
        pagination = query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=25, error_out=False)
        views = SavedView.query.filter_by(page='users') \
            .order_by(SavedView.name).all()
        return render_template('admin/users.html',
                               pagination=pagination, users=pagination.items,
                               q=q, views=views)

    @app.route('/admin/user/<int:user_id>/tier', methods=['POST'])
    @admin_required
    def admin_set_tier(user_id):
        user = db.session.get(User, user_id)
        if user is None:
            abort(404)
        new_tier = request.form.get('tier')
        if new_tier not in TIERS:
            flash('Unknown tier.', 'danger')
            return redirect(url_for('admin_users'))
        old_tier = user.tier
        if old_tier != new_tier:
            user.tier = new_tier
            user.tier_changed_at = datetime.now()
            Activity.record('tier_change', user=user, actor=_real_admin(),
                            detail=f'{old_tier} \u2192 {new_tier}')
            db.session.commit()
            flash(f'{user.username}: tier {old_tier} \u2192 {new_tier}.',
                  'success')
        return redirect(request.referrer or url_for('admin_users'))

    @app.route('/admin/user/<int:user_id>/flags', methods=['POST'])
    @admin_required
    def admin_toggle_flag(user_id):
        user = db.session.get(User, user_id)
        if user is None:
            abort(404)
        real = _real_admin()
        if real and user.id == real.id:
            flash("You can't change your own admin flag.", 'warning')
            return redirect(url_for('admin_users'))
        flag = request.form.get('flag')  # 'is_admin' | 'is_featured'
        if flag == 'is_admin':
            user.is_admin = not user.is_admin
            Activity.record('admin_flag_change', user=user, actor=real,
                            detail='granted' if user.is_admin else 'revoked')
            db.session.commit()
            flash('User updated.', 'success')
        elif flag == 'is_featured':
            # Manual override for the /directory/<source> featured slots.
            # Paying accounts already get featured placement automatically;
            # this pin lets admins feature a free account (or hide one that
            # is opted out of the directory anyway).
            user.is_featured = not bool(getattr(user, 'is_featured', False))
            Activity.record('admin_flag_change', user=user, actor=real,
                            detail='featured' if user.is_featured
                                   else 'unfeatured')
            db.session.commit()
            flash(f'{user.username}: directory featured '
                  f'{"on" if user.is_featured else "off"}.', 'success')
        else:
            flash('Unknown flag.', 'danger')
        return redirect(request.referrer or url_for('admin_users'))

    @app.route('/admin/impersonate/<int:user_id>', methods=['POST'])
    @admin_required
    def admin_impersonate(user_id):
        """Start viewing the site as another user (support sessions)."""
        target = db.session.get(User, user_id)
        if target is None:
            abort(404)
        if target.id == current_user.id:
            flash('You are already signed in as this user.', 'info')
            return redirect(url_for('dashboard'))
        admin = current_user._get_current_object()
        session[IMPERSONATION_KEY] = admin.id  # remember the real admin
        Activity.record('impersonate_start', user=target, actor=admin,
                        detail=f'as @{target.username}')
        db.session.commit()
        login_user(target)
        flash(f'Now impersonating {target.username}.', 'warning')
        return redirect(url_for('dashboard'))

    @app.route('/admin/stop-impersonation', methods=['POST'])
    @login_required
    def admin_stop_impersonation():
        """Available to whoever is impersonating (even while impersonating)."""
        if IMPERSONATION_KEY not in session:
            abort(404)
        admin = _real_admin()
        stop_impersonation(commit=True)
        login_user(admin)
        flash('Impersonation ended — back to your admin account.', 'info')
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/settings', methods=['GET', 'POST'])
    @admin_required
    def admin_settings():
        editable = ['free_tier_max_links', 'expanded_tier_max_links',
                    'site_tagline', 'max_upload_size_kb',
                    'link_check_notify_days',
                    'directory_page_size', 'directory_featured_max',
                    'country_page_size', 'country_sidebar_top',
                    # Stripe billing config (price IDs + portal return URL).
                    'stripe_price_expanded', 'stripe_price_full',
                    'stripe_customer_portal_return',
                    'gtm_container_id',
                    'cookie_banner_enabled', 'consent_policy_version']
        numeric = {'free_tier_max_links', 'expanded_tier_max_links',
                   'max_upload_size_kb', 'link_check_notify_days',
                   'directory_page_size', 'directory_featured_max',
                   'country_page_size', 'country_sidebar_top',
                   'consent_policy_version'}
        booleans = {'country_show_unrecognized', 'cookie_banner_enabled'}
        if request.method == 'POST':
            # --- Email delivery (SMTP) section ---------------------------
            # Handled by mail_util so its validation/secret round-trip rules
            # stay in one place. Validated first; a bad SMTP value aborts the
            # whole save so nothing is half-persisted. The dedicated test
            # buttons post here too — they don't save, just validate+report.
            smtp_errors = mail_util.validate_form(request.form)
            if smtp_errors:
                for err in smtp_errors:
                    flash(err, 'danger')
                return redirect(url_for('admin_settings'))
            if request.form.get('smtp_test_save_first'):
                mail_util.save_from_form(request.form)
                ok, detail = mail_util.test_connection()
                Activity.record('smtp_test', actor=_real_admin(),
                                detail=('ok' if ok else f'failed: {detail[:180]}'))
                db.session.commit()
                flash(('SMTP connection OK — ' if ok else 'SMTP test failed — ') + detail,
                      'success' if ok else 'danger')
                return redirect(url_for('admin_settings'))
            if request.form.get('smtp_send_test'):
                # Save the SMTP section, then send a real email to the
                # configured "From" address (self-test; no extra field needed).
                mail_util.save_from_form(request.form)
                cfg = mail_util.get_smtp_config()
                to_addr = cfg['mail_from']
                ok, detail = mail_util.send_email(
                    to_addr,
                    subject='AllMyProfiles — SMTP test email',
                    body=('This is a test message from your AllMyProfiles site.\n\n'
                          'If you received it, your Gmail SMTP credentials are '
                          'working and outbound email delivery is live.\n'))
                Activity.record('smtp_test_send', actor=_real_admin(),
                                detail=f'{to_addr}: ' + ('ok' if ok else detail[:160]))
                db.session.commit()
                flash(('Test email sent to ' + to_addr + '.') if ok
                      else f'Test email failed — {detail}',
                      'success' if ok else 'danger')
                return redirect(url_for('admin_settings'))
            mail_util.save_from_form(request.form)

            for key in editable:
                if key in booleans:
                    Setting.set_value(key,
                                      'true' if request.form.get(key) else 'false')
                    continue
                value = request.form.get(key)
                if value is None:
                    continue
                value = value.strip()
                if key in numeric:
                    try:
                        value = str(max(0, int(value)))
                    except ValueError:
                        flash(f'"{key}" must be a number.', 'danger')
                        return redirect(url_for('admin_settings'))
                if key == 'gtm_container_id':
                    value = value.upper()
                    if value and not re.fullmatch(r'GTM-[A-Z0-9]{4,12}', value):
                        flash('Google Tag Manager ID must look like '
                              'GTM-XXXXXXX.', 'danger')
                        return redirect(url_for('admin_settings'))
                Setting.set_value(key, value)
            Activity.record('settings_update', actor=_real_admin(),
                            detail='updated site settings')
            db.session.commit()
            flash('Settings saved.', 'success')
            return redirect(url_for('admin_settings'))

        settings = {k: Setting.get(k) for k in Setting.DEFAULT_SETTINGS}
        # Never expose the raw SMTP password to the template; show a mask
        # plus a "stored?" flag so the admin knows whether to retype it.
        smtp_cfg = mail_util.get_smtp_config()
        return render_template('admin/settings.html', settings=settings,
                               smtp_masked=mail_util.mask(smtp_cfg['smtp_password']),
                               smtp_has_password=bool(smtp_cfg['smtp_password']))

    # Expose helpers to templates
    app.jinja_env.globals['profile_handle'] = profile_handle
    app.jinja_env.globals['profile_url'] = url_for_profile
    app.jinja_env.globals['is_impersonating'] = _is_impersonating
    app.jinja_env.filters['skills_list'] = normalize_skills
    app.jinja_env.globals['video_embed'] = video_embed


# ==========================================
# Admin Dev Data (dummy user loader, development only)
# ==========================================

def register_admin_devdata_routes(app):
    """One-click dummy dataset for local development.

    The whole section is hidden from the admin UI unless the app runs in
    development mode (``app.debug`` or FLASK_ENV=development), and the
    seeder itself re-checks the environment before touching the database.
    ``dev_mode`` is exposed as a Jinja global so templates can gate links
    without importing app internals.
    """

    def _dev_mode():
        return bool(app.debug or os.environ.get('FLASK_ENV', '') == 'development')

    app.jinja_env.globals['dev_mode'] = _dev_mode

    @app.route('/admin/dev-data')
    @admin_required
    def admin_dev_data():
        if not _dev_mode():
            abort(404)
        import dev_seed
        status = dev_seed.seed_status()
        return render_template('admin/dev_data.html', status=status,
                               dev_mode=True)

    @app.route('/admin/dev-data/seed', methods=['POST'])
    @admin_required
    def admin_dev_data_seed():
        if not _dev_mode():
            abort(404)
        import dev_seed
        try:
            count = max(1, min(int(request.form.get('count') or 25), 500))
            seed_val = (request.form.get('seed') or '').strip()
            summary = dev_seed.seed_dummy_data(
                count=count, seed=seed_val or None)
        except RuntimeError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('admin_dashboard'))
        Activity.record('dev_seed_batch', actor=_real_admin(),
                        detail=f'seeded {summary["created"]} demo users')
        db.session.commit()
        flash(f'Demo data loaded: {summary["created"]} users created, '
              f'{summary["skipped"]} already existed, {summary["links"]} '
              f'links total, {summary["messages"]} messages sent. '
              f'Password for every demo account: "{dev_seed.DEMO_PASSWORD}"',
              'success')
        return redirect(url_for('admin_dev_data'))

    @app.route('/admin/dev-data/wipe', methods=['POST'])
    @admin_required
    def admin_dev_data_wipe():
        if not _dev_mode():
            abort(404)
        import dev_seed
        try:
            summary = dev_seed.wipe_dummy_data()
        except RuntimeError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('admin_dashboard'))
        Activity.record('dev_seed_wipe', actor=_real_admin(),
                        detail=f'removed {summary["users"]} demo users')
        db.session.commit()
        flash(f'Demo data wiped: {summary["users"]} users, '
              f'{summary["links"]} links, {summary["messages"]} messages, '
              f'{summary["activities"]} audit entries removed.', 'info')
        return redirect(url_for('admin_dev_data'))


# ==========================================
# Admin Billing (Stripe SaaS overview)
# ==========================================

def register_admin_billing_routes(app):

    @app.route('/admin/billing')
    @admin_required
    def admin_billing():
        """SaaS overview of the Stripe account: number cards + quick links.

        Cards: subscriptions created today, cleared (available) funds,
        refunds pending processing, funds on hold, and Stripe fees for the
        current month. Live figures come from the Stripe API; when no secret
        key is configured the page shows setup guidance instead of erroring.
        """
        if not stripe_billing.stripe_ready():
            return render_template('admin/billing.html',
                                   stripe_ready=False, overview=None,
                                   fmt_money=stripe_billing.fmt_money)
        try:
            overview = stripe_billing.collect_overview()
        except Exception as exc:
            app.logger.error('Stripe overview failed: %s', exc)
            flash(f'Could not load Stripe data: {exc}', 'danger')
            overview = None
        # Local subscription snapshot (works even if the API call above
        # partially failed) for the table under the cards.
        subs = (User.query
                .filter(User.stripe_subscription_status.isnot(None))
                .order_by(User.tier_changed_at.desc())
                .limit(50).all())
        return render_template('admin/billing.html',
                               stripe_ready=True, overview=overview,
                               subs=subs,
                               fmt_money=stripe_billing.fmt_money)


# ==========================================
# Admin Messaging Routes (compose / broadcast)
# ==========================================

def register_admin_message_routes(app):

    @app.route('/admin/messages')
    @admin_required
    def admin_messages():
        """Outbox: list of all sent messages with delivery/read stats."""
        page = request.args.get('page', 1, type=int)
        pagination = (Message.query.order_by(Message.created_at.desc())
                      .paginate(page=page, per_page=20, error_out=False))
        return render_template('admin/messages.html',
                               pagination=pagination,
                               messages=pagination.items,
                               categories=MESSAGE_CATEGORIES,
                               severities=MESSAGE_SEVERITIES)

    @app.route('/admin/messages/new', methods=['GET', 'POST'])
    @app.route('/admin/messages/new/<int:user_id>', methods=['GET', 'POST'])
    @admin_required
    def admin_compose(user_id=None):
        """Compose a direct message or broadcast to an audience.

        The <user_id> variant is linked from the admin users list ("Message"
        button next to each account) and pre-selects a single recipient.
        """
        target_user = db.session.get(User, user_id) if user_id else None
        if user_id and target_user is None:
            abort(404)

        audiences = []
        if target_user:
            audiences.append(('one', f'Direct message → @{target_user.username}'))
        audiences += [
            ('all', 'All users'),
            ('paid', 'Paid users only (Expanded / Full / Custom)'),
        ] + [(tier, f'Tier: {tier} only') for tier in TIERS]

        if request.method == 'POST':
            subject = (request.form.get('subject') or '').strip()
            body = (request.form.get('body') or '').strip()
            category = request.form.get('category', 'note')
            severity = request.form.get('severity', '')
            audience = request.form.get('audience', 'all')
            action_url = (request.form.get('action_url') or '').strip()
            action_label = (request.form.get('action_label') or '').strip()

            errors = []
            if not subject:
                errors.append('A subject is required.')
            if not body:
                errors.append('The message body cannot be empty.')
            if category not in MESSAGE_CATEGORIES:
                errors.append('Unknown message category.')
            if audience == 'one' and target_user is None:
                errors.append('Direct messages need a recipient user.')
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/compose.html',
                                       target_user=target_user,
                                       audiences=audiences,
                                       categories=MESSAGE_CATEGORIES,
                                       severities=MESSAGE_SEVERITIES,
                                       macros=ReplyMacro.query.order_by(
                                           ReplyMacro.name).all(),
                                       form=request.form), 400

            if audience != 'one':
                # Don't let someone broadcast "directly" by mistake.
                target_user = None

            message, delivered = send_admin_message(
                subject=subject, body=body, category=category,
                severity=severity or None, sender=_real_admin(),
                source='broadcast' if audience != 'one' else 'direct',
                audience=audience, target_user=target_user,
                action_url=action_url or None,
                action_label=action_label or None)

            if message:
                flash(f'Message delivered to {delivered} recipient(s).',
                      'success')
                return redirect(url_for('admin_messages'))
            flash('Message not sent — no recipients matched that audience.',
                  'warning')
            return redirect(url_for('admin_compose',
                                    **({'user_id': user_id} if user_id else {})))

        return render_template('admin/compose.html',
                               target_user=target_user,
                               audiences=audiences,
                               categories=MESSAGE_CATEGORIES,
                               severities=MESSAGE_SEVERITIES,
                               macros=ReplyMacro.query.order_by(
                                   ReplyMacro.name).all(),
                               form={})

    @app.route('/admin/messages/delete/<int:message_id>', methods=['POST'])
    @admin_required
    def admin_delete_message(message_id):
        """Recall a message: removes it (and everyone's read state)."""
        message = db.session.get(Message, message_id)
        if message is None:
            abort(404)
        Activity.record('message_deleted', actor=_real_admin(),
                        detail=f'recalled message #{message_id}: '
                               f'{message.subject[:80]}')
        db.session.delete(message)
        db.session.commit()
        flash('Message recalled and removed from all inboxes.', 'info')
        return redirect(url_for('admin_messages'))


# ==========================================
# Admin Link Source CRUD (social platform catalogue)
# ==========================================

def register_link_source_routes(app):

    def _validate_source_form(form, source_id=None):
        """Validate admin link-source fields. Returns (errors, cleaned)."""
        errors = []
        name = (form.get('name') or '').strip()
        if not name or len(name) > 80:
            errors.append('Name is required (max 80 characters).')
        dup = LinkSource.query.filter(LinkSource.name == name)
        if source_id is not None:
            dup = dup.filter(LinkSource.id != source_id)
        if dup.first():
            errors.append(f'A source named "{name}" already exists.')

        domains = (form.get('domains') or '').strip().lower()
        # basic sanity: comma-separated bare hostnames, no scheme/spaces
        for d in [x.strip() for x in domains.split(',') if x.strip()]:
            if any(c in d for c in (' ', '/', ':', '@')):
                errors.append(f'Domain "{d}" must be a bare hostname '
                              f'(e.g. github.com), no http:// or spaces.')

        url = (form.get('url') or '').strip()
        if url and not url.startswith(('http://', 'https://')):
            errors.append('Example URL must start with http:// or https://.')

        home_url = (form.get('home_url') or '').strip()
        if home_url and not home_url.startswith(('http://', 'https://')):
            errors.append('Home page URL must start with http:// or https://.')

        pattern = (form.get('profile_pattern') or '').strip()
        if pattern:
            if '{handle}' not in pattern:
                errors.append('Profile pattern must contain the {handle} '
                              'placeholder (e.g. https://github.com/{handle}).')
            if not pattern.startswith(('http://', 'https://')):
                errors.append('Profile pattern must start with http:// or '
                              'https://.')

        colors = {}
        for field, label in (('bg_color', 'Background'), ('text_color', 'Text'),
                             ('border_color', 'Border')):
            value = (form.get(field) or '').strip().lower()
            if not HEX_COLOR_RE.match(value or ''):
                errors.append(f'{label} color must be a hex value like '
                              f'#ff0000.')
                value = value or '#ffffff'
            colors[field] = value

        icon_code = (form.get('icon_code') or '').strip() or 'link-45deg'
        category = (form.get('category') or '').strip() or 'Other'
        if category not in LinkCategory.names():
            errors.append(f'Unknown category "{category}". Create it under '
                          f'Admin → Link Categories first.')
        cta = (form.get('cta') or '').strip()[:40]
        # Directory blurb: rendered as the first element under the H1 on the
        # directory templates, so it must be a unique-ish 20-50 word summary
        # of the website itself (see LinkSource.validate_description).
        description = re.sub(r'\s+', ' ', form.get('description') or '').strip()
        desc_error = LinkSource.validate_description(description)
        if desc_error:
            errors.append(desc_error)
        try:
            sort_order = int(form.get('sort_order') or 0)
        except ValueError:
            sort_order = 0

        cleaned = {
            'name': name, 'domains': domains, 'url': url,
            'home_url': home_url, 'profile_pattern': pattern, 'icon_code': icon_code,
            'category': category, 'cta': cta, 'sort_order': sort_order,
            'description': description,
            'is_active': form.get('is_active') == 'on',
            'is_adult': form.get('is_adult') == 'on',
            'is_nofollow': form.get('is_nofollow') == 'on',
            **colors,
        }
        return errors, cleaned

    @app.route('/admin/link-sources')
    @admin_required
    def admin_link_sources():
        """Catalogue list with per-source usage report (distinct users)."""
        sources = LinkSource.query.filter_by(is_deleted=False) \
            .order_by(LinkSource.sort_order, LinkSource.name).all()
        # Usage report: total users + total links referencing each source.
        user_counts = dict(
            db.session.query(Link.source_id, func.count(func.distinct(Link.user_id)))
            .filter(Link.source_id.isnot(None))
            .group_by(Link.source_id).all())
        link_counts = dict(
            db.session.query(Link.source_id, func.count(Link.id))
            .filter(Link.source_id.isnot(None))
            .group_by(Link.source_id).all())
        rows = [{'src': s,
                 'users': user_counts.get(s.id, 0),
                 'links': link_counts.get(s.id, 0)} for s in sources]
        deleted = LinkSource.query.filter_by(is_deleted=True) \
            .order_by(LinkSource.name).all()
        return render_template('admin/link_sources.html', rows=rows,
                               deleted=deleted,
                               categories=LinkCategory.names())

    @app.route('/admin/link-sources/new', methods=['GET', 'POST'])
    @admin_required
    def admin_link_source_new():
        if request.method == 'POST':
            errors, data = _validate_source_form(request.form)
            icon_file = request.files.get('custom_icon')
            if icon_file and icon_file.filename:
                rel, err = save_upload_image(icon_file, app.config,
                                             subdir='icons')
                if err:
                    errors.append(f'Custom icon: {err}')
                else:
                    data['custom_icon_path'] = rel
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/link_source_form.html',
                                       src=None, form=request.form,
                                       categories=LinkCategory.names())
            src = LinkSource(**data)
            db.session.add(src)
            Activity.record('link_source_created', actor=_real_admin(),
                            detail=f'created link source "{src.name}"')
            db.session.commit()
            flash(f'Link source "{src.name}" created. It is live for users.',
                  'success')
            return redirect(url_for('admin_link_sources'))
        return render_template('admin/link_source_form.html', src=None,
                               form=None, categories=LinkCategory.names())

    @app.route('/admin/link-sources/<int:source_id>/edit',
               methods=['GET', 'POST'])
    @admin_required
    def admin_link_source_edit(source_id):
        src = db.session.get(LinkSource, source_id)
        if src is None or src.is_deleted:
            abort(404)
        if request.method == 'POST':
            errors, data = _validate_source_form(request.form,
                                                 source_id=source_id)
            icon_file = request.files.get('custom_icon')
            if icon_file and icon_file.filename:
                rel, err = save_upload_image(icon_file, app.config,
                                             subdir='icons')
                if err:
                    errors.append(f'Custom icon: {err}')
                else:
                    if src.custom_icon_path:
                        delete_upload_image(src.custom_icon_path,
                                            app.config)
                    data['custom_icon_path'] = rel
            elif request.form.get('remove_icon') == 'on':
                if src.custom_icon_path:
                    delete_upload_image(src.custom_icon_path, app.config)
                data['custom_icon_path'] = None
            else:
                data['custom_icon_path'] = src.custom_icon_path
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/link_source_form.html',
                                       src=src, form=request.form,
                                       categories=LinkCategory.names())
            for key, value in data.items():
                setattr(src, key, value)
            src.updated_at = datetime.now()
            Activity.record('link_source_updated', actor=_real_admin(),
                            detail=f'updated link source "{src.name}"')
            db.session.commit()
            flash(f'Link source "{src.name}" updated. Changes are live.',
                  'success')
            return redirect(url_for('admin_link_sources'))
        return render_template('admin/link_source_form.html', src=src,
                               form=None, categories=LinkCategory.names())

    @app.route('/admin/link-sources/<int:source_id>/delete',
               methods=['POST'])
    @admin_required
    def admin_link_source_delete(source_id):
        """Soft delete: existing user links keep working via their saved URL;
        the source just disappears from pickers/suggestions."""
        src = db.session.get(LinkSource, source_id)
        if src is None or src.is_deleted:
            abort(404)
        src.is_deleted = True
        src.is_active = False
        src.updated_at = datetime.now()
        Activity.record('link_source_deleted', actor=_real_admin(),
                        detail=f'deleted link source "{src.name}"')
        db.session.commit()
        flash(f'Link source "{src.name}" deleted (recoverable below).',
              'info')
        return redirect(url_for('admin_link_sources'))

    @app.route('/admin/link-sources/<int:source_id>/restore',
               methods=['POST'])
    @admin_required
    def admin_link_source_restore(source_id):
        src = db.session.get(LinkSource, source_id)
        if src is None or not src.is_deleted:
            abort(404)
        # If another source took the unique name meanwhile, ask admin first.
        clash = LinkSource.query.filter_by(name=src.name,
                                           is_deleted=False).first()
        if clash:
            flash(f'Cannot restore "{src.name}": an active source with that '
                  f'name exists. Delete it first or rename before restoring.',
                  'danger')
            return redirect(url_for('admin_link_sources'))
        src.is_deleted = False
        src.updated_at = datetime.now()
        Activity.record('link_source_restored', actor=_real_admin(),
                        detail=f'restored link source "{src.name}"')
        db.session.commit()
        flash(f'Link source "{src.name}" restored.', 'success')
        return redirect(url_for('admin_link_sources'))

    # ------------------------------------------------ bulk import/export

    @app.route('/admin/link-sources/export')
    @admin_required
    def admin_link_source_export():
        """Download the catalogue as JSON (categories + sources). Sources
        reference categories by label, so the file is portable across
        environments where ids differ."""
        include_deleted = request.args.get('deleted') == '1'
        body = catalogue_io.export_json(include_deleted=include_deleted)
        stamp = datetime.now().strftime('%Y%m%d-%H%M')
        resp = Response(body, mimetype='application/json')
        resp.headers['Content-Disposition'] = (
            f'attachment; filename="allmyprofiles-catalogue-{stamp}.json"')
        Activity.record('link_catalogue_exported', actor=_real_admin(),
                        detail=f'exported catalogue ({len(body)} bytes'
                               f'{", incl. deleted" if include_deleted else ""})')
        db.session.commit()
        return resp

    @app.route('/admin/link-sources/import', methods=['GET', 'POST'])
    @admin_required
    def admin_link_source_import():
        """Bulk-import networks (and their categories) from a JSON file or
        pasted text. Validation-first: nothing is written unless every
        record in the file is clean. Supports a dry-run preview and an
        overwrite/skip policy for names that already exist live."""
        template_ctx = dict(active_tab='import', form={})

        if request.method == 'POST':
            # A body over Flask's MAX_CONTENT_LENGTH yields an empty parse;
            # surface a clear message instead of "nothing supplied".
            if not request.form and not request.files.get('file'):
                flash('Upload failed — the request was larger than the '
                      'server limit (5 MB). Trim the file or import in '
                      'smaller batches.', 'danger')
                return render_template(
                    'admin/link_source_import.html',
                    **template_ctx, categories=LinkCategory.names())
            raw = ''
            filename = None
            upload = request.files.get('file')
            if upload and upload.filename:
                filename = upload.filename
                try:
                    raw = upload.read().decode('utf-8-sig', errors='replace')
                except Exception:
                    flash('Could not read the uploaded file as UTF-8 text.',
                          'danger')
                    return render_template(
                        'admin/link_source_import.html',
                        **template_ctx, categories=LinkCategory.names())
            else:
                raw = request.form.get('text') or ''
            if not raw.strip():
                flash('Choose a .json file or paste the JSON text to import.',
                      'danger')
                return render_template(
                    'admin/link_source_import.html',
                    **template_ctx, categories=LinkCategory.names())

            source_mode = (request.form.get('source_mode')
                           if request.form.get('source_mode')
                           in ('overwrite', 'skip') else 'overwrite')
            category_mode = (request.form.get('category_mode')
                             if request.form.get('category_mode')
                             in ('overwrite', 'skip') else 'overwrite')
            dry_run = request.form.get('preview') == 'on'

            try:
                result, errors = catalogue_io.import_from_text(
                    raw, source_mode=source_mode,
                    category_mode=category_mode, dry_run=dry_run)
            except catalogue_io.ImportError_ as e:
                flash(str(e), 'danger')
                return render_template(
                    'admin/link_source_import.html',
                    **template_ctx, categories=LinkCategory.names())

            if errors:
                for err in errors[:25]:
                    flash(err, 'danger')
                if len(errors) > 25:
                    flash(f'…and {len(errors) - 25} more problems.', 'danger')
                flash('Nothing was imported — fix the file and try again.',
                      'warning')
                return render_template(
                    'admin/link_source_import.html',
                    **{'active_tab': 'import',
                       'form': {'text': raw, 'source_mode': source_mode,
                                'category_mode': category_mode,
                                'preview': 'on' if dry_run else None}},
                    categories=LinkCategory.names())

            if dry_run:
                plan = result['plan']
                # Flatten to display-friendly name lists for the template.
                preview = {}
                for table, bucket in plan.items():
                    preview[table] = {
                        'create': [d['name'] for d in bucket['create']],
                        'revive': [d['name'] for _, d in bucket['revive']],
                        # "changed" (not update/items): dict attribute access
                        # in Jinja resolves to dict methods first, so keys
                        # that collide with them must be renamed.
                        'changed': [d['name'] for _, d in bucket['update']],
                        'skip': list(bucket['skip']),
                    }
                return render_template(
                    'admin/link_source_import.html',
                    active_tab='preview', plan=preview,
                    form={'text': raw, 'source_mode': source_mode,
                          'category_mode': category_mode, 'preview': 'on'},
                    categories=LinkCategory.names())

            summary = result
            db.session.commit()
            Activity.record(
                'link_catalogue_imported', actor=_real_admin(),
                detail=(f'bulk import via {filename or "pasted text"}: '
                        f"{summary['created']} created, "
                        f"{summary['updated']} updated, "
                        f"{summary['revived']} revived, "
                        f"{summary['skipped']} skipped"))
            db.session.commit()
            flash(f"Import complete: {summary['created']} created, "
                  f"{summary['updated']} updated, "
                  f"{summary['revived']} revived, "
                  f"{summary['skipped']} skipped.", 'success')
            return redirect(url_for('admin_link_sources'))

        return render_template('admin/link_source_import.html',
                               **template_ctx,
                               categories=LinkCategory.names())


# ==========================================
# Admin CRUD: Link Source Categories
# ==========================================

def register_link_category_routes(app):
    """CRUD for the grouping buckets that organize link sources on public
    profiles (Admin -> Link Categories).

    Robustness rules baked in here:
      * Categories are referenced by name from ``LinkSource.category``, so
        renames can optionally cascade to sources ("Apply to N sources").
      * Deletes never orphan data: sources in a deleted category fall back
        to the catch-all 'Other' (or a category the admin reassigns to).
      * 'Other' is protected — it is the guaranteed fallback bucket and
        cannot be deleted or renamed.
    """

    def _source_count(cat_name):
        return LinkSource.query.filter_by(category=cat_name,
                                          is_deleted=False).count()

    def _validate_category_form(form, cat_id=None):
        """Validate admin category fields. Returns (errors, cleaned)."""
        errors = []
        name = (form.get('name') or '').strip()
        if not name:
            errors.append('Name is required.')
        elif len(name) > 40:
            errors.append('Name must be 40 characters or fewer.')
        # The DB enforces unique(name) across ALL rows -- live *and*
        # soft-deleted -- so the form validator must too. Otherwise a
        # create can pass validation and then die at commit time with an
        # IntegrityError whenever a deleted row still holds the name.
        dup = LinkCategory.query.filter(LinkCategory.name == name)
        if cat_id is not None:
            dup = dup.filter(LinkCategory.id != cat_id)
        clash = dup.first()
        if clash is not None:
            hint = (' (a deleted category still holds this name -- restore '
                    'it from the Deleted list below, or pick another name)'
                    if clash.is_deleted else '')
            errors.append(f'A category named "{name}" already exists.{hint}')

        icon_code = (form.get('icon_code') or '').strip() or 'tag'
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,48}', icon_code):
            errors.append('Icon code must be a Bootstrap Icons slug '
                          '(lowercase letters, digits, hyphens), e.g. "people".')

        colors = {}
        for field, label in (('bg_color', 'Background'),
                             ('text_color', 'Text')):
            value = (form.get(field) or '').strip().lower()
            if not HEX_COLOR_RE.match(value or ''):
                errors.append(f'{label} color must be a hex value like '
                              f'#ff0000.')
                value = value or ('#f8f9fa' if field == 'bg_color'
                                  else '#212529')
            colors[field] = value

        try:
            sort_order = int(form.get('sort_order') or 0)
        except ValueError:
            sort_order = 0

        # Display-control flag (checkbox posts 'on' when ticked). Free-tier
        # users should not see adult content; this marks the category as
        # 18+ so gating logic (and next-phase tier features) can hide it.
        is_adult = bool(form.get('is_adult'))

        cleaned = {'name': name, 'icon_code': icon_code,
                   'sort_order': sort_order, 'is_adult': is_adult, **colors}
        return errors, cleaned

    @app.route('/admin/link-categories')
    @admin_required
    def admin_link_categories():
        """List categories with live source counts + deleted (recoverable)."""
        cats = LinkCategory.query.filter_by(is_deleted=False) \
            .order_by(LinkCategory.sort_order, LinkCategory.name).all()
        rows = [{'cat': c, 'sources': _source_count(c.name)} for c in cats]
        deleted = LinkCategory.query.filter_by(is_deleted=True) \
            .order_by(LinkCategory.name).all()
        # Sources whose category no longer resolves to a live row — they
        # render under 'Other'; surface them so admins can re-file them.
        live_names = {c.name for c in cats}
        orphans = (LinkSource.query
                   .filter_by(is_deleted=False)
                   .order_by(LinkSource.category, LinkSource.name).all())
        orphans = [s for s in orphans if s.category not in live_names]
        return render_template('admin/link_categories.html', rows=rows,
                               deleted=deleted, orphans=orphans,
                               all_cats=cats)

    @app.route('/admin/link-categories/new', methods=['GET', 'POST'])
    @admin_required
    def admin_link_category_new():
        if request.method == 'POST':
            errors, data = _validate_category_form(request.form)
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/link_category_form.html',
                                       cat=None, form=request.form)
            cat = LinkCategory(**data)
            db.session.add(cat)
            Activity.record('link_category_created', actor=_real_admin(),
                            detail=f'created link category "{cat.name}"')
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                flash(f'A category named "{data["name"]}" already exists '
                      f'(possibly a deleted one). Restore it from the '
                      f'Deleted list or choose another name.', 'danger')
                return render_template('admin/link_category_form.html',
                                       cat=None, form=request.form)
            flash(f'Category "{cat.name}" created. It is now selectable on '
                  f'link sources.', 'success')
            return redirect(url_for('admin_link_categories'))
        next_order = (max((c.sort_order for c in LinkCategory.query.all()),
                          default=-1) + 1)
        return render_template('admin/link_category_form.html', cat=None,
                               form={'sort_order': next_order})

    @app.route('/admin/link-categories/<int:cat_id>/edit',
               methods=['GET', 'POST'])
    @admin_required
    def admin_link_category_edit(cat_id):
        cat = db.session.get(LinkCategory, cat_id)
        if cat is None or cat.is_deleted:
            abort(404)
        protected = cat.name == 'Other'
        if request.method == 'POST':
            if protected:
                flash('"Other" is the protected fallback category — its '
                      'name cannot be changed. Colors, icon and order can.',
                      'warning')
                form_data = dict(request.form)
                form_data['name'] = cat.name
            else:
                form_data = request.form
            errors, data = _validate_category_form(form_data, cat_id=cat_id)
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/link_category_form.html',
                                       cat=cat, form=request.form)
            old_name = cat.name
            rename_applied = 0
            if data['name'] != old_name and not protected:
                if request.form.get('apply_rename') == 'on':
                    rename_applied = LinkSource.query.filter_by(
                        category=old_name).update(
                        {'category': data['name']},
                        synchronize_session='fetch')
            for key, value in data.items():
                setattr(cat, key, value)
            cat.updated_at = datetime.now()
            Activity.record('link_category_updated', actor=_real_admin(),
                            detail=f'updated link category "{cat.name}"'
                                   + (f' (renamed from "{old_name}", '
                                      f'{rename_applied} source(s) moved)'
                                      if data['name'] != old_name else ''))
            db.session.commit()
            flash(f'Category "{cat.name}" updated.', 'success')
            return redirect(url_for('admin_link_categories'))
        return render_template('admin/link_category_form.html', cat=cat,
                               form=None, protected=protected,
                               source_count=_source_count(cat.name))

    @app.route('/admin/link-categories/<int:cat_id>/delete',
               methods=['POST'])
    @admin_required
    def admin_link_category_delete(cat_id):
        """Soft delete with explicit reassignment of member sources."""
        cat = db.session.get(LinkCategory, cat_id)
        if cat is None or cat.is_deleted:
            abort(404)
        if cat.name == 'Other':
            flash('"Other" is the protected fallback category and cannot '
                  'be deleted.', 'danger')
            return redirect(url_for('admin_link_categories'))
        target_name = (request.form.get('move_to') or 'Other').strip()
        target = LinkCategory.query.filter_by(name=target_name,
                                              is_deleted=False).first()
        if target is None or target.id == cat.id:
            target = LinkCategory.query.filter_by(name='Other',
                                                  is_deleted=False).first()
        moved = 0
        if target is not None:
            moved = LinkSource.query.filter_by(category=cat.name).update(
                {'category': target.name}, synchronize_session='fetch')
        cat.is_deleted = True
        cat.updated_at = datetime.now()
        Activity.record('link_category_deleted', actor=_real_admin(),
                        detail=f'deleted link category "{cat.name}" '
                               f'({moved} source(s) moved to '
                               f'"{target.name if target else "??"}")')
        db.session.commit()
        flash(f'Category "{cat.name}" deleted; {moved} source(s) moved to '
              f'"{target.name if target else "Other"}".', 'info')
        return redirect(url_for('admin_link_categories'))

    @app.route('/admin/link-categories/<int:cat_id>/restore',
               methods=['POST'])
    @admin_required
    def admin_link_category_restore(cat_id):
        """Restore a soft-deleted category.

        Because uniqueness is enforced at the DB level across *all* rows
        (live and deleted), restoring can clash in two ways: another LIVE
        category already uses the name, or a previously re-created row does.
        Both are handled here; the admin can also supply ``new_name`` to
        restore under a different name instead of deleting the conflicting
        row first.
        """
        cat = db.session.get(LinkCategory, cat_id)
        if cat is None or not cat.is_deleted:
            abort(404)
        # NOTE: do NOT .strip() before validating — silently trimming a
        # leading-space name would turn " Foo" into a valid "Foo" and
        # bypass the shape check below.
        new_name = request.form.get('new_name') or ''
        if new_name and (len(new_name) > 40
                         or not re.fullmatch(r'[^\s].{0,38}[^\s]', new_name)):
            flash('New name must be 1–40 characters and cannot start or '
                  'end with whitespace.', 'danger')
            return redirect(url_for('admin_link_categories'))
        new_name = new_name.strip()
        target_name = new_name or cat.name
        clash_live = LinkCategory.query.filter(LinkCategory.name == target_name,
                                               LinkCategory.id != cat.id,
                                               LinkCategory.is_deleted == False)  # noqa: E712
        if clash_live.first():
            flash(f'Cannot restore as "{target_name}": an active category '
                  f'with that name exists. Restore under a different name '
                  f'(optional field next to the Restore button) or delete '
                  f'the active one first.', 'danger')
            return redirect(url_for('admin_link_categories'))
        clash_any = LinkCategory.query.filter(LinkCategory.name == target_name,
                                              LinkCategory.id != cat.id).first()
        if clash_any:
            flash(f'Cannot restore as "{target_name}": a deleted category '
                  f'already carries that name. Restore under a different '
                  f'name instead.', 'danger')
            return redirect(url_for('admin_link_categories'))
        old_name = cat.name
        cat.name = target_name
        cat.is_deleted = False
        cat.updated_at = datetime.now()
        Activity.record('link_category_restored', actor=_real_admin(),
                        detail=f'restored link category "{target_name}"'
                               + (f' (was "{old_name}")'
                                  if target_name != old_name else ''))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Restore failed due to a data conflict; nothing changed.',
                  'danger')
            return redirect(url_for('admin_link_categories'))
        flash(f'Category "{target_name}" restored.', 'success')
        return redirect(url_for('admin_link_categories'))

    @app.route('/admin/link-categories/reorder', methods=['POST'])
    @admin_required
    def admin_link_category_reorder():
        """Persist tab order: form sends comma-separated ids in new order."""
        ids = []
        for chunk in (request.form.get('order') or '').split(','):
            chunk = chunk.strip()
            if chunk.isdigit():
                ids.append(int(chunk))
        if not ids:
            abort(400)
        for pos, cid in enumerate(ids):
            cat = db.session.get(LinkCategory, cid)
            if cat is not None and not cat.is_deleted:
                cat.sort_order = pos
        Activity.record('link_category_reordered', actor=_real_admin(),
                        detail='reordered link categories')
        db.session.commit()
        flash('Category order saved. Profile tabs follow this order.',
              'success')
        return redirect(url_for('admin_link_categories'))


# ==========================================
# Admin Productivity: bulk actions, saved views, reply macros,
# abuse-report console with triage automation, audit log
# ==========================================

MACRO_PLACEHOLDERS = {
    'site_name': 'AllMyProfiles',
    'admin_email': 'support@allmyprofiles.com',
    'help_url': '/help',
    'today': None,  # filled at render time
}


def apply_triage_rules(report):
    """Run active TriageRules against a new AbuseReport. First match wins.

    Returns the matching rule or None. Caller commits.
    """
    rules = (TriageRule.query.filter_by(is_active=True)
             .order_by(TriageRule.priority.desc(), TriageRule.id).all())
    for rule in rules:
        try:
            if rule.matches(report):
                report.status = rule.set_status or report.status
                if rule.note:
                    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
                    prefix = f'[auto · {rule.name} · {stamp}]'
                    report.admin_note = ((report.admin_note or '') + '\n'
                                         + prefix + ' ' + rule.note).strip()
                Activity.record('triage_applied', user=report.target_user,
                                detail=f'rule "{rule.name}" → '
                                       f'{report.status} (report #{report.id})')
                return rule
        except Exception:
            continue  # a bad rule must never break public reporting
    return None


def register_admin_productivity_routes(app):

    # ---------------- Abuse reports console ------------------------------

    @app.route('/admin/reports')
    @admin_required
    def admin_reports():
        status = request.args.get('status', 'open')
        if status not in REPORT_STATUSES + ['all']:
            status = 'open'
        reason = request.args.get('reason', '')
        q = (request.args.get('q') or '').strip().lower()

        query = AbuseReport.query
        if status != 'all':
            query = query.filter(AbuseReport.status == status)
        if reason in REPORT_REASONS:
            query = query.filter(AbuseReport.reason == reason)
        if q:
            query = (query.join(User, AbuseReport.target_user_id == User.id)
                     .filter(db.or_(User.username.ilike(f'%{q}%'),
                                    User.email.ilike(f'%{q}%'))))
        page = request.args.get('page', 1, type=int)
        pagination = (query.order_by(AbuseReport.created_at.desc())
                      .paginate(page=page, per_page=20, error_out=False))
        counts = dict(db.session.query(AbuseReport.status,
                                       func.count(AbuseReport.id))
                      .group_by(AbuseReport.status).all())
        views = SavedView.query.filter_by(page='reports') \
            .order_by(SavedView.name).all()
        macros = ReplyMacro.query.order_by(ReplyMacro.name).all()
        return render_template('admin/reports.html',
                               pagination=pagination,
                               reports=pagination.items,
                               status=status, reason=reason, q=q,
                               counts=counts, reasons=REPORT_REASONS,
                               statuses=REPORT_STATUSES, views=views,
                               macros=macros)

    @app.route('/admin/report/<int:report_id>/update', methods=['POST'])
    @admin_required
    def admin_report_update(report_id):
        report = db.session.get(AbuseReport, report_id)
        if report is None:
            abort(404)
        new_status = request.form.get('status')
        note = (request.form.get('admin_note') or '').strip()[:2000]
        changed = False
        if new_status in REPORT_STATUSES and new_status != report.status:
            report.status = new_status
            report.resolved_by_id = current_user.id
            changed = True
        if note and note != (report.admin_note or ''):
            report.admin_note = note
            changed = True
        if changed:
            report.updated_at = datetime.now()
            Activity.record('report_update', user=report.target_user,
                            actor=current_user,
                            detail=f'report #{report.id} → {report.status}')
            db.session.commit()
            flash(f'Report #{report.id} updated.', 'success')
        return redirect(request.referrer or url_for('admin_reports'))

    @app.route('/admin/reports/bulk', methods=['POST'])
    @admin_required
    def admin_reports_bulk():
        ids = _bulk_ids(request.form.getlist('ids'))
        action = request.form.get('action')
        if not ids:
            flash('Select at least one report first.', 'warning')
            return redirect(request.referrer or url_for('admin_reports'))
        done = 0
        if action in REPORT_STATUSES:
            now = datetime.now()
            done = (AbuseReport.query.filter(AbuseReport.id.in_(ids))
                    .update({'status': action, 'updated_at': now,
                             'resolved_by_id': current_user.id},
                            synchronize_session=False))
        elif action == 'note':
            note = (request.form.get('note') or '').strip()[:500]
            if note:
                for r in AbuseReport.query.filter(AbuseReport.id.in_(ids)).all():
                    r.admin_note = ((r.admin_note or '') + f'\n[note] {note}').strip()
                    r.updated_at = datetime.now()
                done = len(ids)
        if done:
            Activity.record('bulk_action', actor=current_user,
                            detail=f'reports: {action} on {done} item(s)')
            db.session.commit()
            flash(f'Bulk “{action}” applied to {done} report(s).', 'success')
        else:
            flash('Bulk action needs a valid status or note text.', 'danger')
        return redirect(request.referrer or url_for('admin_reports'))

    @app.route('/admin/report/<int:report_id>/message', methods=['POST'])
    @admin_required
    def admin_report_message(report_id):
        """Reply to the *profile owner* about a report using a macro."""
        report = db.session.get(AbuseReport, report_id)
        if report is None:
            abort(404)
        subject = (request.form.get('subject') or '').strip()
        body = (request.form.get('body') or '').strip()
        macro_id = request.form.get('macro_id', type=int)
        if not body and macro_id:
            macro = db.session.get(ReplyMacro, macro_id)
            if macro:
                ctx = dict(MACRO_PLACEHOLDERS)
                ctx['today'] = datetime.now().strftime('%B %d, %Y')
                ctx['user'] = report.target_user.username
                ctx['display_name'] = report.target_user.display_name or \
                    report.target_user.username
                body = macro.render(ctx)
                subject = subject or macro.name
        if not subject or not body:
            flash('A subject and body (or a macro) are required.', 'danger')
            return redirect(url_for('admin_reports'))
        send_admin_message(subject=subject, body=body, category='tos_violation',
                           sender=current_user, audience='one',
                           target_user=report.target_user)
        Activity.record('report_reply', user=report.target_user,
                        actor=current_user,
                        detail=f'messaged about report #{report.id}')
        db.session.commit()
        flash(f'Message sent to @{report.target_user.username}.', 'success')
        return redirect(request.referrer or url_for('admin_reports'))

    # ---------------- Users bulk actions ---------------------------------

    @app.route('/admin/users/bulk', methods=['POST'])
    @admin_required
    def admin_users_bulk():
        ids = _bulk_ids(request.form.getlist('ids'))
        action = request.form.get('action')
        if not ids:
            flash('Select at least one user first.', 'warning')
            return redirect(request.referrer or url_for('admin_users'))
        me = current_user.id
        targets = User.query.filter(User.id.in_(ids)).all()
        done = 0
        if action in TIERS:
            now = datetime.now()
            for u in targets:
                if u.tier != action:
                    old = u.tier
                    u.tier = action
                    u.tier_changed_at = now
                    Activity.record('tier_change', user=u, actor=current_user,
                                    detail=f'{old} → {action} (bulk)')
                    done += 1
        elif action == 'message':
            subject = (request.form.get('subject') or '').strip()
            body = (request.form.get('body') or '').strip()
            macro_id = request.form.get('macro_id', type=int)
            if not body and macro_id:
                macro = db.session.get(ReplyMacro, macro_id)
                if macro:
                    ctx = dict(MACRO_PLACEHOLDERS)
                    ctx['today'] = now_ctx_date()
                    body = macro.render(ctx)
                    subject = subject or macro.name
            if subject and body:
                delivered = 0
                for u in targets:
                    _, n = send_admin_message(subject=subject, body=body,
                                              category='note',
                                              sender=current_user,
                                              audience='one', target_user=u,
                                              commit=False)
                    delivered += n
                db.session.commit()
                flash(f'Message delivered to {delivered} user(s).', 'success')
                return redirect(request.referrer or url_for('admin_users'))
            flash('Bulk message needs a subject and body (or pick a macro).',
                  'danger')
            return redirect(request.referrer or url_for('admin_users'))
        elif action == 'revoke_admin':
            for u in targets:
                if u.is_admin and u.id != me:
                    u.is_admin = False
                    Activity.record('admin_flag_change', user=u,
                                    actor=current_user,
                                    detail='revoked (bulk)')
                    done += 1
        if done:
            Activity.record('bulk_action', actor=current_user,
                            detail=f'users: {action} on {done} item(s)')
            db.session.commit()
            flash(f'Bulk “{action}” applied to {done} user(s).', 'success')
        else:
            flash('Nothing to do — check your selection and action.',
                  'warning')
        return redirect(request.referrer or url_for('admin_users'))

    # ---------------- Saved views ----------------------------------------

    @app.route('/admin/saved-views', methods=['POST'])
    @admin_required
    def admin_save_view():
        name = (request.form.get('name') or '').strip()[:60]
        page_key = request.form.get('page')
        qs = (request.form.get('query_string') or '').strip()[:300]
        back = request.form.get('next') or ''
        if not back.startswith('/'):
            back = ''
        if page_key not in ('users', 'reports'):
            flash('Unknown view target.', 'danger')
        elif not name:
            flash('Give the saved view a name.', 'danger')
        else:
            db.session.add(SavedView(name=name, page=page_key,
                                     query_string=qs))
            Activity.record('saved_view_created', actor=current_user,
                            detail=f'"{name}" ({page_key})')
            db.session.commit()
            flash(f'Saved view “{name}” created.', 'success')
        return redirect(back or url_for('admin_dashboard'))

    @app.route('/admin/saved-views/<int:view_id>/delete', methods=['POST'])
    @admin_required
    def admin_delete_view(view_id):
        v = db.session.get(SavedView, view_id)
        if v is None:
            abort(404)
        db.session.delete(v)
        Activity.record('saved_view_deleted', actor=current_user,
                        detail=f'"{v.name}"')
        db.session.commit()
        flash('Saved view deleted.', 'info')
        return redirect(request.referrer or url_for('admin_dashboard'))

    # ---------------- Reply macros CRUD ----------------------------------

    @app.route('/admin/macros')
    @admin_required
    def admin_macros():
        macros = ReplyMacro.query.order_by(ReplyMacro.name).all()
        return render_template('admin/macros.html', macros=macros,
                               placeholders=sorted(MACRO_PLACEHOLDERS))

    @app.route('/admin/macros/new', methods=['POST'])
    @admin_required
    def admin_macro_new():
        name = (request.form.get('name') or '').strip()[:80]
        body = (request.form.get('body') or '').strip()
        if not name or not body:
            flash('Macro needs both a name and a body.', 'danger')
            return redirect(url_for('admin_macros'))
        if ReplyMacro.query.filter_by(name=name).first():
            flash(f'A macro named “{name}” already exists.', 'danger')
            return redirect(url_for('admin_macros'))
        db.session.add(ReplyMacro(name=name, body=body[:5000]))
        Activity.record('macro_created', actor=current_user, detail=f'"{name}"')
        db.session.commit()
        flash(f'Macro “{name}” created.', 'success')
        return redirect(url_for('admin_macros'))

    @app.route('/admin/macros/<int:macro_id>/edit', methods=['POST'])
    @admin_required
    def admin_macro_edit(macro_id):
        m = db.session.get(ReplyMacro, macro_id)
        if m is None:
            abort(404)
        name = (request.form.get('name') or '').strip()[:80]
        body = (request.form.get('body') or '').strip()
        if not name or not body:
            flash('Macro needs both a name and a body.', 'danger')
            return redirect(url_for('admin_macros'))
        clash = ReplyMacro.query.filter(ReplyMacro.name == name,
                                       ReplyMacro.id != macro_id).first()
        if clash:
            flash(f'A macro named “{name}” already exists.', 'danger')
            return redirect(url_for('admin_macros'))
        m.name, m.body, m.updated_at = name, body[:5000], datetime.now()
        Activity.record('macro_updated', actor=current_user, detail=f'"{name}"')
        db.session.commit()
        flash(f'Macro “{name}” updated.', 'success')
        return redirect(url_for('admin_macros'))

    @app.route('/admin/macros/<int:macro_id>/delete', methods=['POST'])
    @admin_required
    def admin_macro_delete(macro_id):
        m = db.session.get(ReplyMacro, macro_id)
        if m is None:
            abort(404)
        db.session.delete(m)
        Activity.record('macro_deleted', actor=current_user,
                        detail=f'"{m.name}"')
        db.session.commit()
        flash('Macro deleted.', 'info')
        return redirect(url_for('admin_macros'))

    # ---------------- Triage rules CRUD ----------------------------------

    @app.route('/admin/triage')
    @admin_required
    def admin_triage():
        rules = TriageRule.query.order_by(TriageRule.priority.desc(),
                                          TriageRule.id).all()
        return render_template('admin/triage.html', rules=rules,
                               reasons=REPORT_REASONS,
                               statuses=REPORT_STATUSES)

    @app.route('/admin/triage/new', methods=['POST'])
    @admin_required
    def admin_triage_new():
        errors, data = _validate_triage_form(request.form)
        if errors:
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('admin_triage'))
        db.session.add(TriageRule(**data))
        Activity.record('triage_rule_created', actor=current_user,
                        detail=f'"{data["name"]}"')
        db.session.commit()
        flash(f'Triage rule “{data["name"]}” created.', 'success')
        return redirect(url_for('admin_triage'))

    @app.route('/admin/triage/<int:rule_id>/edit', methods=['POST'])
    @admin_required
    def admin_triage_edit(rule_id):
        rule = db.session.get(TriageRule, rule_id)
        if rule is None:
            abort(404)
        errors, data = _validate_triage_form(request.form, rule_id=rule_id)
        if errors:
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('admin_triage'))
        for key, value in data.items():
            setattr(rule, key, value)
        Activity.record('triage_rule_updated', actor=current_user,
                        detail=f'"{rule.name}"')
        db.session.commit()
        flash(f'Triage rule “{rule.name}” updated.', 'success')
        return redirect(url_for('admin_triage'))

    @app.route('/admin/triage/<int:rule_id>/toggle', methods=['POST'])
    @admin_required
    def admin_triage_toggle(rule_id):
        rule = db.session.get(TriageRule, rule_id)
        if rule is None:
            abort(404)
        rule.is_active = not rule.is_active
        Activity.record('triage_rule_toggled', actor=current_user,
                        detail=f'"{rule.name}" '
                               + ('enabled' if rule.is_active else 'paused'))
        db.session.commit()
        flash(f'Rule {"enabled" if rule.is_active else "paused"}.', 'info')
        return redirect(url_for('admin_triage'))

    @app.route('/admin/triage/<int:rule_id>/delete', methods=['POST'])
    @admin_required
    def admin_triage_delete(rule_id):
        rule = db.session.get(TriageRule, rule_id)
        if rule is None:
            abort(404)
        db.session.delete(rule)
        Activity.record('triage_rule_deleted', actor=current_user,
                        detail=f'"{rule.name}"')
        db.session.commit()
        flash('Triage rule deleted.', 'info')
        return redirect(url_for('admin_triage'))

    # ---------------- Audit log ------------------------------------------

    @app.route('/admin/audit')
    @admin_required
    def admin_audit():
        kind = (request.args.get('kind') or '').strip()
        q = (request.args.get('q') or '').strip().lower()
        query = Activity.query
        if kind:
            query = query.filter(Activity.kind == kind)
        if q:
            query = query.filter(db.or_(
                Activity.detail.ilike(f'%{q}%'),
                Activity.kind.ilike(f'%{q}%')))
        page = request.args.get('page', 1, type=int)
        pagination = (query.order_by(Activity.created_at.desc())
                      .paginate(page=page, per_page=50, error_out=False))
        kinds = [k[0] for k in db.session.query(Activity.kind)
                 .distinct().order_by(Activity.kind).all()]
        return render_template('admin/audit.html',
                               pagination=pagination,
                               entries=pagination.items,
                               kind=kind, q=q, kinds=kinds)



# ==========================================
# Admin Testimonial Management (CRUD & display rules)
# ==========================================

def register_testimonial_routes(app):
    """Full CRUD for home-page social-proof testimonials.

    Display rules enforced here / in the model:
      * ``is_published``   — master visibility switch.
      * ``exclude_from_home`` — specifically marked rows never show on /.
      * ``is_incomplete``  — drafts are automatically excluded by the
        homepage display logic even when published.
      * Profile-link icon  — clickable only with explicit consent AND an
        active paid subscription (see ``testimonial_profile_link``).
      * Live counter       — "published on display" badge recomputed on
        every admin page load from ``Testimonial.home_display()``.
    """

    def _validate_testimonial_form(form, t_id=None):
        """Validate admin testimonial fields. Returns (errors, cleaned)."""
        errors = []
        author_name = (form.get('author_name') or '').strip()
        if not author_name:
            errors.append('Author name is required.')
        elif len(author_name) > 80:
            errors.append('Author name must be 80 characters or fewer.')

        quote = (form.get('quote') or '').strip()
        if not quote:
            errors.append('Quote text is required.')
        elif len(quote) > 1000:
            errors.append('Quote must be 1000 characters or fewer.')

        author_role = (form.get('author_role') or '').strip()[:120]

        try:
            rating = int(form.get('rating') or 5)
        except ValueError:
            rating = 5
            errors.append('Rating must be a whole number between 1 and 5.')
        else:
            if not 1 <= rating <= 5:
                errors.append('Rating must be between 1 and 5.')
                rating = min(max(rating, 1), 5)

        try:
            sort_order = int(form.get('sort_order') or 0)
        except ValueError:
            sort_order = 0

        user_id = None
        raw_user = (form.get('user_id') or '').strip()
        if raw_user:
            try:
                user_id = int(raw_user)
            except ValueError:
                errors.append('Linked account must be selected from the list.')
            else:
                if db.session.get(User, user_id) is None:
                    errors.append('Linked account does not exist.')

        section_ids = []
        for raw_section_id in form.getlist('section_ids'):
            try:
                section_id = int(raw_section_id)
            except (TypeError, ValueError):
                errors.append('Content section selection is invalid.')
                continue
            if section_id not in section_ids:
                section_ids.append(section_id)
        valid_section_ids = {
            section.id for section in ContentSection.query.filter(
                ContentSection.id.in_(section_ids),
                ContentSection.is_active.is_(True)).all()
        } if section_ids else set()
        if valid_section_ids != set(section_ids):
            errors.append('Choose only active content sections.')

        # Checkbox flags post 'on' when ticked; absent means False.
        cleaned = {
            'author_name': author_name,
            'author_role': author_role,
            'quote': quote,
            'rating': rating,
            'sort_order': sort_order,
            'user_id': user_id,
            'profile_consent': bool(form.get('profile_consent')),
            'is_published': bool(form.get('is_published')),
            'exclude_from_home': bool(form.get('exclude_from_home')),
            'is_incomplete': bool(form.get('is_incomplete')),
            'section_ids': section_ids,
        }
        return errors, cleaned

    @app.route('/admin/testimonials')
    @admin_required
    def admin_testimonials():
        """List all testimonials + live counter of published-on-display."""
        q = Testimonial.query
        status = (request.args.get('status') or '').strip()
        if status == 'published':
            q = q.filter(Testimonial.is_published.is_(True))
        elif status == 'draft':
            q = q.filter(Testimonial.is_published.is_(False))
        elif status == 'excluded':
            q = q.filter(db.or_(Testimonial.exclude_from_home.is_(True),
                                Testimonial.is_incomplete.is_(True)))
        page = request.args.get('page', 1, type=int)
        pagination = q.order_by(Testimonial.sort_order,
                                Testimonial.created_at.desc()).paginate(
            page=page, per_page=25, error_out=False)
        # Live display counter reflects actual assignment to the home page.
        homepage_slugs = [slug for slug, _, _ in
                          ContentSection.DEFAULT_SECTIONS]
        on_display = (Testimonial.query
                      .join(Testimonial.content_sections)
                      .filter(
                          ContentSection.slug.in_(homepage_slugs),
                          ContentSection.is_active.is_(True),
                          Testimonial.is_published.is_(True),
                          Testimonial.is_incomplete.is_(False),
                          db.or_(
                              Testimonial.is_flagged.is_(False),
                              Testimonial.resolution_status.in_(
                                  ('resolved', 'dismissed')),
                          ),
                          db.or_(
                              ContentSection.slug != 'homepage-testimonials',
                              Testimonial.exclude_from_home.is_(False),
                          ),
                      ).distinct().count())
        total_published = Testimonial.query.filter_by(
            is_published=True).count()
        return render_template('admin/testimonials.html',
                               pagination=pagination,
                               items=pagination.items,
                               status=status,
                               on_display=on_display,
                               total_published=total_published,
                               sections=ContentSection.query.order_by(
                                   ContentSection.sort_order,
                                   ContentSection.name).all())

    @app.route('/admin/content-sections')
    @admin_required
    def admin_content_sections():
        sections = ContentSection.query.order_by(
            ContentSection.sort_order, ContentSection.name).all()
        return render_template('admin/content_sections.html',
                               sections=sections)

    @app.route('/admin/testimonials/new', methods=['GET', 'POST'])
    @admin_required
    def admin_testimonial_new():
        if request.method == 'POST':
            errors, data = _validate_testimonial_form(request.form)
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/testimonial_form.html',
                                       t=None, form=request.form,
                                       users=User.query.order_by(
                                           User.username).all(),
                                       sections=ContentSection.query.filter_by(
                                           is_active=True).order_by(
                                               ContentSection.sort_order,
                                               ContentSection.name).all(),
                                       selected_section_ids=[
                                           int(value) for value in
                                           request.form.getlist('section_ids')
                                           if value.isdigit()])
            section_ids = data.pop('section_ids', [])
            t = Testimonial(**data)
            t.content_sections = ContentSection.query.filter(
                ContentSection.id.in_(section_ids),
                ContentSection.is_active.is_(True)).all()
            db.session.add(t)
            Activity.record('testimonial_created', actor=_real_admin(),
                            detail=f'created testimonial by "{t.author_name}"')
            db.session.commit()
            flash(f'Testimonial by {t.author_name} created.', 'success')
            return redirect(url_for('admin_testimonials'))
        next_order = (max((x.sort_order for x in Testimonial.query.all()),
                          default=-1) + 1)
        return render_template('admin/testimonial_form.html', t=None,
                               form={'sort_order': next_order},
                               users=User.query.order_by(User.username).all(),
                               sections=ContentSection.query.filter_by(
                                   is_active=True).order_by(
                                       ContentSection.sort_order,
                                       ContentSection.name).all(),
                               selected_section_ids=[])

    @app.route('/admin/testimonials/<int:t_id>/edit', methods=['GET', 'POST'])
    @admin_required
    def admin_testimonial_edit(t_id):
        t = db.session.get(Testimonial, t_id)
        if t is None:
            abort(404)
        if request.method == 'POST':
            errors, data = _validate_testimonial_form(request.form, t_id=t_id)
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/testimonial_form.html',
                                       t=t, form=request.form,
                                       users=User.query.order_by(
                                           User.username).all(),
                                       sections=ContentSection.query.filter_by(
                                           is_active=True).order_by(
                                               ContentSection.sort_order,
                                               ContentSection.name).all(),
                                       selected_section_ids=[
                                           int(value) for value in
                                           request.form.getlist('section_ids')
                                           if value.isdigit()])
            section_ids = data.pop('section_ids', [])
            for field, value in data.items():
                setattr(t, field, value)
            t.content_sections = ContentSection.query.filter(
                ContentSection.id.in_(section_ids),
                ContentSection.is_active.is_(True)).all()
            t.updated_at = datetime.now()
            Activity.record('testimonial_updated', actor=_real_admin(),
                            detail=f'edited testimonial #{t.id} '
                                   f'("{t.author_name}")')
            db.session.commit()
            flash('Testimonial updated.', 'success')
            return redirect(url_for('admin_testimonials'))
        return render_template('admin/testimonial_form.html', t=t, form=None,
                               users=User.query.order_by(User.username).all(),
                               sections=ContentSection.query.filter_by(
                                   is_active=True).order_by(
                                       ContentSection.sort_order,
                                       ContentSection.name).all(),
                               selected_section_ids=[
                                   section.id for section in t.content_sections])

    @app.route('/admin/testimonials/<int:t_id>/delete', methods=['POST'])
    @admin_required
    def admin_testimonial_delete(t_id):
        t = db.session.get(Testimonial, t_id)
        if t is None:
            abort(404)
        name = t.author_name
        db.session.delete(t)
        Activity.record('testimonial_deleted', actor=_real_admin(),
                        detail=f'deleted testimonial #{t_id} ("{name}")')
        db.session.commit()
        flash(f'Testimonial by {name} deleted.', 'info')
        return redirect(url_for('admin_testimonials'))

    @app.route('/admin/testimonials/<int:t_id>/toggle', methods=['POST'])
    @admin_required
    def admin_testimonial_toggle(t_id):
        """Quick visibility flip straight from the list view."""
        t = db.session.get(Testimonial, t_id)
        if t is None:
            abort(404)
        t.is_published = not bool(t.is_published)
        t.updated_at = datetime.now()
        Activity.record('testimonial_toggled', actor=_real_admin(),
                        detail=f'testimonial #{t.id} '
                               f'{"published" if t.is_published else "unpublished"}')
        db.session.commit()
        flash(f'Testimonial {"published" if t.is_published else "unpublished"}.',
              'success')
        return redirect(request.referrer or url_for('admin_testimonials'))


def _bulk_ids(raw_list):
    """Parse checkbox values into a clean list of positive ints (max 200)."""
    out = []
    for raw in raw_list or []:
        try:
            i = int(raw)
            if i > 0:
                out.append(i)
        except (TypeError, ValueError):
            continue
    return out[:200]


def now_ctx_date():
    return datetime.now().strftime('%B %d, %Y')


def _validate_triage_form(form, rule_id=None):
    errors = []
    name = (form.get('name') or '').strip()[:80]
    if not name:
        errors.append('Rule name is required.')
    dup = TriageRule.query.filter(TriageRule.name == name)
    if rule_id is not None:
        dup = dup.filter(TriageRule.id != rule_id)
    if name and dup.first():
        errors.append(f'A rule named “{name}” already exists.')
    set_status = form.get('set_status') or 'reviewing'
    if set_status not in REPORT_STATUSES:
        errors.append('Invalid target status.')
    match_reason = (form.get('match_reason') or '').strip()
    if match_reason and match_reason not in REPORT_REASONS:
        errors.append('Invalid reason matcher.')
    try:
        priority = int(form.get('priority') or 0)
    except ValueError:
        priority = 0
    cleaned = {
        'name': name,
        'match_reason': match_reason or None,
        'match_domain': (form.get('match_domain') or '').strip().lower()[:120] or None,
        'match_keyword': (form.get('match_keyword') or '').strip()[:80] or None,
        'set_status': set_status,
        'note': (form.get('note') or '').strip()[:300] or None,
        'is_active': form.get('is_active') != 'off',
        'priority': priority,
    }
    return errors, cleaned


# ==========================================
# FAQs (public page, section placements, admin CRUD)
# ==========================================

def register_faq_routes(app):
    """FAQs with categories.

    Public: ``/faq`` lists every visible FAQ grouped by category and emits
    schema.org FAQPage JSON-LD. Any template can pull the FAQs placed on a
    content section with ``faqs_for_section('<slug>')`` (see
    ``templates/_faq.html``); the home page uses ``homepage-faq``.
    Admin: full CRUD for FAQs and FAQ categories.
    """
    app.jinja_env.globals['faqs_for_section'] = Faq.for_section
    app.jinja_env.globals['faq_jsonld_data'] = Faq.jsonld

    def _sections():
        return ContentSection.query.filter_by(is_active=True).order_by(
            ContentSection.sort_order, ContentSection.name).all()

    def _form_ctx(faq, form, selected):
        return dict(faq=faq, form=form, sections=_sections(),
                    categories=FaqCategory.ordered(),
                    selected_section_ids=selected, tokens=FAQ_TOKENS)

    def _posted_section_ids():
        return [int(v) for v in request.form.getlist('section_ids')
                if v.isdigit()]

    def _validate_faq_form(form):
        errors = []
        question = (form.get('question') or '').strip()
        if not question:
            errors.append('Question is required.')
        elif len(question) > FAQ_QUESTION_MAX:
            errors.append(f'Question must be {FAQ_QUESTION_MAX} characters '
                          f'or fewer.')
        answer = (form.get('answer') or '').strip()
        if not answer:
            errors.append('Answer is required.')
        elif len(answer) > FAQ_ANSWER_MAX:
            errors.append(f'Answer must be {FAQ_ANSWER_MAX} characters or '
                          f'fewer.')
        category_id = None
        raw_cat = (form.get('category_id') or '').strip()
        if raw_cat:
            if raw_cat.isdigit() and db.session.get(FaqCategory,
                                                    int(raw_cat)):
                category_id = int(raw_cat)
            else:
                errors.append('Choose a valid category.')
        try:
            sort_order = int(form.get('sort_order') or 0)
        except ValueError:
            sort_order = 0
        return errors, {'question': question, 'answer': answer,
                        'category_id': category_id,
                        'sort_order': sort_order,
                        'is_published': bool(form.get('is_published'))}

    def _validate_category_form(form, cat_id=None):
        errors = []
        name = (form.get('name') or '').strip()
        slug = slugify(name)
        if not name:
            errors.append('Name is required.')
        elif len(name) > 80:
            errors.append('Name must be 80 characters or fewer.')
        elif not slug:
            errors.append('Name must contain letters or numbers.')
        else:
            clash = FaqCategory.query.filter(db.or_(
                func.lower(FaqCategory.name) == name.lower(),
                FaqCategory.slug == slug))
            if cat_id is not None:
                clash = clash.filter(FaqCategory.id != cat_id)
            if clash.first() is not None:
                errors.append(f'A category named "{name}" already exists.')
        description = (form.get('description') or '').strip()
        if len(description) > 250:
            errors.append('Description must be 250 characters or fewer.')
        try:
            sort_order = int(form.get('sort_order') or 0)
        except ValueError:
            sort_order = 0
        return errors, {'name': name, 'slug': slug,
                        'description': description or None,
                        'sort_order': sort_order,
                        'is_active': bool(form.get('is_active'))}

    # ---------------------------- public ---------------------------------
    @app.route('/faq')
    def faq_page():
        groups = Faq.grouped_for_page()
        all_faqs = [f for _, items in groups for f in items]
        return render_template('faq.html', groups=groups,
                               jsonld=Faq.jsonld(all_faqs))

    # ------------------------- admin: FAQs -------------------------------
    @app.route('/admin/faqs')
    @admin_required
    def admin_faqs():
        q = Faq.query
        cat = request.args.get('category', '')
        status = request.args.get('status', '')
        section = request.args.get('section', '')
        if cat == 'none':
            q = q.filter(Faq.category_id.is_(None))
        elif cat.isdigit():
            q = q.filter(Faq.category_id == int(cat))
        if status == 'published':
            q = q.filter(Faq.is_published.is_(True))
        elif status == 'draft':
            q = q.filter(Faq.is_published.is_(False))
        if section.isdigit():
            q = q.filter(Faq.content_sections.any(
                ContentSection.id == int(section)))
        faqs = q.order_by(Faq.sort_order, Faq.id).all()
        return render_template('admin/faqs.html', faqs=faqs,
                               categories=FaqCategory.ordered(),
                               sections=_sections(), cat=cat, status=status,
                               section=section,
                               total=Faq.query.count(),
                               on_display=len(Faq.public_query().all()))

    @app.route('/admin/faqs/new', methods=['GET', 'POST'])
    @admin_required
    def admin_faq_new():
        if request.method == 'POST':
            errors, data = _validate_faq_form(request.form)
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/faq_form.html', **_form_ctx(
                    None, request.form, _posted_section_ids()))
            faq = Faq(**data)
            faq.content_sections = ContentSection.query.filter(
                ContentSection.id.in_(_posted_section_ids()),
                ContentSection.is_active.is_(True)).all()
            db.session.add(faq)
            Activity.record('faq_created', actor=_real_admin(),
                            detail=f'created FAQ "{faq.question[:80]}"')
            db.session.commit()
            flash('FAQ created.', 'success')
            return redirect(url_for('admin_faqs'))
        next_order = (db.session.query(func.max(Faq.sort_order)).scalar()
                      or 0) + 1
        return render_template('admin/faq_form.html', **_form_ctx(
            None, {'sort_order': next_order, 'is_published': 'on'}, []))

    @app.route('/admin/faqs/<int:faq_id>/edit', methods=['GET', 'POST'])
    @admin_required
    def admin_faq_edit(faq_id):
        faq = db.session.get(Faq, faq_id)
        if faq is None:
            abort(404)
        if request.method == 'POST':
            errors, data = _validate_faq_form(request.form)
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/faq_form.html', **_form_ctx(
                    faq, request.form, _posted_section_ids()))
            for field, value in data.items():
                setattr(faq, field, value)
            faq.content_sections = ContentSection.query.filter(
                ContentSection.id.in_(_posted_section_ids()),
                ContentSection.is_active.is_(True)).all()
            faq.updated_at = datetime.now()
            Activity.record('faq_updated', actor=_real_admin(),
                            detail=f'edited FAQ #{faq.id}')
            db.session.commit()
            flash('FAQ updated.', 'success')
            return redirect(url_for('admin_faqs'))
        return render_template('admin/faq_form.html', **_form_ctx(
            faq, None, [s.id for s in faq.content_sections]))

    @app.route('/admin/faqs/<int:faq_id>/delete', methods=['POST'])
    @admin_required
    def admin_faq_delete(faq_id):
        faq = db.session.get(Faq, faq_id)
        if faq is None:
            abort(404)
        db.session.delete(faq)
        Activity.record('faq_deleted', actor=_real_admin(),
                        detail=f'deleted FAQ #{faq_id}')
        db.session.commit()
        flash('FAQ deleted.', 'info')
        return redirect(url_for('admin_faqs'))

    @app.route('/admin/faqs/<int:faq_id>/toggle', methods=['POST'])
    @admin_required
    def admin_faq_toggle(faq_id):
        faq = db.session.get(Faq, faq_id)
        if faq is None:
            abort(404)
        faq.is_published = not faq.is_published
        faq.updated_at = datetime.now()
        Activity.record('faq_updated', actor=_real_admin(),
                        detail=f'{"published" if faq.is_published else "unpublished"} '
                               f'FAQ #{faq.id}')
        db.session.commit()
        flash('FAQ published.' if faq.is_published else 'FAQ unpublished.',
              'success')
        return redirect(request.referrer or url_for('admin_faqs'))

    # ---------------------- admin: FAQ categories ------------------------
    @app.route('/admin/faq-categories')
    @admin_required
    def admin_faq_categories():
        return render_template('admin/faq_categories.html',
                               categories=FaqCategory.ordered(),
                               uncategorized=Faq.query.filter(
                                   Faq.category_id.is_(None)).count())

    @app.route('/admin/faq-categories/new', methods=['GET', 'POST'])
    @admin_required
    def admin_faq_category_new():
        if request.method == 'POST':
            errors, data = _validate_category_form(request.form)
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/faq_category_form.html',
                                       cat=None, form=request.form)
            cat = FaqCategory(**data)
            db.session.add(cat)
            Activity.record('faq_category_created', actor=_real_admin(),
                            detail=f'created FAQ category "{cat.name}"')
            db.session.commit()
            flash(f'Category "{cat.name}" created.', 'success')
            return redirect(url_for('admin_faq_categories'))
        next_order = (db.session.query(func.max(FaqCategory.sort_order))
                      .scalar() or 0) + 1
        return render_template('admin/faq_category_form.html', cat=None,
                               form={'sort_order': next_order,
                                     'is_active': 'on'})

    @app.route('/admin/faq-categories/<int:cat_id>/edit',
               methods=['GET', 'POST'])
    @admin_required
    def admin_faq_category_edit(cat_id):
        cat = db.session.get(FaqCategory, cat_id)
        if cat is None:
            abort(404)
        if request.method == 'POST':
            errors, data = _validate_category_form(request.form,
                                                   cat_id=cat_id)
            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('admin/faq_category_form.html',
                                       cat=cat, form=request.form)
            for field, value in data.items():
                setattr(cat, field, value)
            Activity.record('faq_category_updated', actor=_real_admin(),
                            detail=f'updated FAQ category "{cat.name}"')
            db.session.commit()
            flash(f'Category "{cat.name}" updated.', 'success')
            return redirect(url_for('admin_faq_categories'))
        return render_template('admin/faq_category_form.html', cat=cat,
                               form=None)

    @app.route('/admin/faq-categories/<int:cat_id>/delete',
               methods=['POST'])
    @admin_required
    def admin_faq_category_delete(cat_id):
        cat = db.session.get(FaqCategory, cat_id)
        if cat is None:
            abort(404)
        name = cat.name
        # FAQs are kept and become uncategorized.
        Faq.query.filter_by(category_id=cat.id).update(
            {'category_id': None}, synchronize_session='fetch')
        db.session.delete(cat)
        Activity.record('faq_category_deleted', actor=_real_admin(),
                        detail=f'deleted FAQ category "{name}"')
        db.session.commit()
        flash(f'Category "{name}" deleted. Its FAQs are now uncategorized.',
              'info')
        return redirect(url_for('admin_faq_categories'))


# ==========================================
# Static Marketing Pages (linked from footer)
# ==========================================

def register_static_pages(app):

    @app.route('/help')
    def help_center():
        return render_template('help.html')

    @app.route('/contact', methods=['GET', 'POST'])
    def contact():
        if request.method == 'POST':
            name = (request.form.get('name') or '').strip()
            email = (request.form.get('email') or '').strip()
            message = (request.form.get('message') or '').strip()
            if not (name and '@' in email and len(message) >= 10):
                flash('Please fill in your name, a valid email, and a '
                      'message of at least 10 characters.', 'danger')
                return render_template('contact.html'), 400
            # No external email service configured yet: log the request as an
            # activity event so it surfaces on the admin dashboard.
            Activity.record('contact_request', actor=None,
                            detail=f'{name} <{email}>: {message[:300]}')
            db.session.commit()
            flash('Thanks! Your message reached our support team — we reply '
                  'within 2 business days.', 'success')
            return redirect(url_for('contact'))
        return render_template('contact.html')

    @app.route('/suggest', methods=['GET', 'POST'])
    def suggest():
        """Collect website/social-network suggestions for admin review."""
        categories = LinkCategory.names()
        form_data = {
            'name': (current_user.display_name or current_user.username)
                    if current_user.is_authenticated else '',
            'email': current_user.email if current_user.is_authenticated else '',
        }
        if request.method == 'POST':
            form_data = request.form
            # Honeypot: appear successful to bots without adding inbox noise.
            if request.form.get('website'):
                flash('Thanks for your suggestion.', 'success')
                return redirect(url_for('suggest'))

            name = (request.form.get('name') or '').strip()
            email = (request.form.get('email') or '').strip().lower()
            suggestion_type = (request.form.get('suggestion_type') or '').strip()
            profile_url = (request.form.get('profile_url') or '').strip()
            category = (request.form.get('category') or '').strip()
            description = (request.form.get('description') or '').strip()
            is_adult = (
                suggestion_type == 'social_network'
                and request.form.get('is_adult') == 'on'
            )
            errors = []

            if not name or len(name) > 120:
                errors.append('Enter your name (up to 120 characters).')
            if not CONTACT_EMAIL_RE.match(email):
                errors.append('Enter a valid email address.')
            if suggestion_type not in ('social_network', 'site'):
                errors.append('Choose a suggestion type.')
            if not description or len(description) > 3000:
                errors.append(
                    'Describe your suggestion (up to 3000 characters).')

            if suggestion_type == 'social_network':
                try:
                    parsed_url = urlsplit(profile_url)
                    valid_url = (
                        parsed_url.scheme in ('http', 'https')
                        and bool(parsed_url.hostname)
                        and not parsed_url.username
                        and not parsed_url.password
                        and len(profile_url) <= 500
                    )
                except ValueError:
                    valid_url = False
                if not valid_url:
                    errors.append(
                        'Enter a valid social network profile page URL '
                        'starting with http:// or https://.')
                if category not in categories:
                    errors.append('Choose a category from the list.')
            elif suggestion_type == 'site':
                if profile_url:
                    try:
                        parsed_url = urlsplit(profile_url)
                        valid_url = (
                            parsed_url.scheme in ('http', 'https')
                            and bool(parsed_url.hostname)
                            and not parsed_url.username
                            and not parsed_url.password
                            and len(profile_url) <= 500
                        )
                    except ValueError:
                        valid_url = False
                    if not valid_url:
                        errors.append(
                            'If provided, the website URL must start with '
                            'http:// or https://.')
                if category and category not in categories:
                    errors.append('Choose a category from the list.')

            if errors:
                for error in errors:
                    flash(error, 'danger')
                return render_template('suggest.html', categories=categories,
                                       form_data=form_data), 400

            admins = User.query.filter_by(is_admin=True).all()
            if not admins:
                app.logger.error(
                    'Cannot deliver site suggestion: no admin accounts exist.')
                flash('Your suggestion could not be delivered right now. '
                      'Please try again later.', 'danger')
                return render_template('suggest.html', categories=categories,
                                       form_data=form_data), 503

            type_label = ('Social network' if suggestion_type == 'social_network'
                          else 'General site suggestion')
            body = (
                f'Submitted by: {name} <{email}>\n'
                f'Suggestion type: {type_label}\n'
                f'Profile / website URL: {profile_url or "Not provided"}\n'
                f'Suggested category: {category or "Not applicable"}\n'
                f'Adult content: {"Yes" if is_adult else "No"}\n\n'
                f'Description:\n{description}\n\n'
                'This submission is for admin consideration only. Inclusion '
                'in the site catalogue is at the discretion of the site admins.'
            )
            message = Message(
                subject=(f'{type_label}: {profile_url or description[:100]}'
                         )[:200],
                body=body,
                category='site_suggestion',
                severity=MESSAGE_CATEGORIES['site_suggestion'][
                    'default_severity'],
                sender=(current_user._get_current_object()
                        if current_user.is_authenticated else None),
                source='direct',
            )
            db.session.add(message)
            db.session.flush()
            deliver_message(message, admins)
            Activity.record(
                'site_suggestion_received',
                actor=(current_user._get_current_object()
                       if current_user.is_authenticated else None),
                detail=f'{type_label} from {email}',
            )
            db.session.commit()
            flash('Thanks for your suggestion! It has been sent to the site '
                  'admins for consideration. Submissions are reviewed at '
                  'their discretion and are not automatically added to the '
                  'website.', 'success')
            return redirect(url_for('suggest'))

        return render_template('suggest.html', categories=categories,
                               form_data=form_data)

    @app.route('/terms')
    def terms():
        return render_template('terms.html')

    @app.route('/privacy')
    def privacy():
        return render_template('privacy.html')


# ==========================================
# SEO: robots.txt, XML sitemaps, browserconfig
# ==========================================

def _site_base_url():
    """Absolute base URL for the current request (scheme + host)."""
    return request.url_root.rstrip('/')


def _iso_dt(dt):
    if dt is None:
        return ''
    return dt.strftime('%Y-%m-%dT%H:%M:%S+00:00')


def _xml_response(body):
    resp = Response(body, mimetype='application/xml')
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


def register_seo_routes(app):

    # ---- robots.txt ------------------------------------------------------
    @app.route('/robots.txt')
    def robots_txt():
        base = _site_base_url()
        disallow = ['/admin', '/inbox', '/dashboard', '/api/', '/redirect/',
                    '/login', '/logout', '/register', '/settings', '/uploads']
        lines = ['User-agent: *', 'Disallow: /$']
        lines += [f'Disallow: {p}' for p in disallow]
        lines += [
            '',
            '# Allow public profile pages and their link feeds',
            'Allow: /',
            '',
            '# AI crawlers are explicitly welcome (see /ai-grounding)',
            'User-agent: GPTBot',
            'Allow: /',
            '',
            'User-agent: OAI-SearchBot',
            'Allow: /',
            '',
            'User-agent: ChatGPT-User',
            'Allow: /',
            '',
            'User-agent: PerplexityBot',
            'Allow: /',
            '',
            'User-agent: ClaudeBot',
            'Allow: /',
            '',
            f'Sitemap: {base}/sitemap.xml',
            '',
        ]
        return Response('\n'.join(lines), mimetype='text/plain')

    # ---- Microsoft / Android tile manifest -------------------------------
    @app.route('/browserconfig.xml')
    def browserconfig_xml():
        img = url_for('static', filename='images/web-app-manifest-192x192.png')
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<browserconfig>
  <msapplication>
    <tile>
      <square150x150logo src="{img}"/>
      <TileColor>#35306b</TileColor>
    </tile>
  </msapplication>
</browserconfig>
"""
        return _xml_response(body)

    # ---- Sitemap index ---------------------------------------------------
    @app.route('/sitemap.xml')
    def sitemap_index():
        base = _site_base_url()
        today = datetime.now().strftime('%Y-%m-%d')
        urls = [
            ('/sitemap-static.xml', 'daily'),
            ('/sitemap-profiles-1.xml', 'hourly'),
        ]
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for loc, freq in urls:
            parts.append(f'  <sitemap><loc>{base}{loc}</loc>'
                         f'<lastmod>{today}</lastmod></sitemap>')
        # Chunked profile sitemaps so huge sites stay under the 50k limit.
        user_total = User.query.count()
        chunks = max(1, -(-user_total // 50000))
        for i in range(2, chunks + 1):
            parts.append(f'  <sitemap><loc>{base}/sitemap-profiles-{i}.xml</loc>'
                         f'<lastmod>{today}</lastmod></sitemap>')
        parts.append('</sitemapindex>')
        return _xml_response('\n'.join(parts))

    # ---- Static marketing/support pages ----------------------------------
    @app.route('/sitemap-static.xml')
    def sitemap_static():
        base = _site_base_url()
        today = datetime.now().strftime('%Y-%m-%d')
        entries = [
            ('/', 'daily', '1.0'),
            ('/help', 'weekly', '0.6'),
            ('/faq', 'weekly', '0.7'),
            ('/contact', 'monthly', '0.4'),
            ('/ai-grounding', 'weekly', '0.7'),
            ('/directory', 'daily', '0.9'),
            ('/terms', 'yearly', '0.2'),
            ('/privacy', 'yearly', '0.2'),
        ]
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for path, freq, prio in entries:
            parts.append(f'  <url><loc>{base}{path}</loc>'
                         f'<lastmod>{today}</lastmod>'
                         f'<changefreq>{freq}</changefreq>'
                         f'<priority>{prio}</priority></url>')
        parts.append('</urlset>')
        return _xml_response('\n'.join(parts))

    # ---- Public profile pages (chunked, auto-generated) ------------------
    @app.route('/sitemap-profiles-<int:page>.xml')
    def sitemap_profiles(page):
        page = max(1, page)
        per_page = 50000
        base = _site_base_url()
        users = (User.query.order_by(User.created_at.desc())
                 .offset((page - 1) * per_page).limit(per_page).all())
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
                 ' xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">']
        for u in users:
            # Adult-oriented profiles carry noindex — exclude them from
            # sitemaps so crawlers aren't pointed at non-indexable URLs.
            if getattr(u, 'is_adult_oriented', False):
                continue
            # Only profiles with a claimed Display Name are public/search
            # engine friendly (the URL is derived from the name itself).
            handle = display_name_slug(u.display_name)
            if not handle:
                continue
            lastmod = _iso_dt(getattr(u, 'updated_at', None) or u.created_at)
            canonical = f'{base}/{handle}'
            links_feed = f'{canonical}/links.xml'
            avatar = u.avatar_src
            if avatar and not avatar.startswith('http'):
                avatar = base + url_for('uploaded_file', filename=avatar) \
                    if not avatar.startswith('/') else base + avatar
            parts.append(
                f'  <url><loc>{canonical}</loc>'
                f'<lastmod>{lastmod}</lastmod>'
                f'<changefreq>hourly</changefreq><priority>0.8</priority>')
            if avatar:
                parts.append(f'<image:image><image:loc>{avatar}</image:loc>'
                             f'</image:image>')
            parts.append('</url>')
            parts.append(f'  <url><loc>{links_feed}</loc>'
                         f'<lastmod>{lastmod}</lastmod>'
                         f'<changefreq>hourly</changefreq>'
                         f'<priority>0.5</priority></url>')
        parts.append('</urlset>')
        return _xml_response('\n'.join(parts))

    # ---- Per-user link feed (XML) ----------------------------------------
    @app.route('/<handle>/links.xml')
    def user_links_xml(handle):
        user = user_by_handle(handle)
        if user is None or not (user.display_name or '').strip():
            # No Display Name -> no public profile -> no crawlable feed.
            abort(404)
        base = _site_base_url()
        links = (Link.query.filter_by(user_id=user.id, is_active=True)
                 .order_by(Link.position.asc()).all())
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 f'<links user="{user.username}" display_name="{user.display_name}"'
                 f' profile="{base}/{profile_handle(user)}">']
        for l in links:
            parts.append(f'  <link position="{l.position}">'
                         f'<title>{l.title}</title>'
                         f'<url>{l.url}</url>'
                         f'<clicks>{l.click_count or 0}</clicks>'
                         f'</link>')
        parts.append('</links>')
        return _xml_response('\n'.join(parts))


# ==========================================
# Error Handlers & Template Helpers
# ==========================================

def register_error_handlers(app):

    @app.errorhandler(404)
    def not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('404.html', message='You do not have permission to access this resource.'), 403

    @app.errorhandler(413)
    def too_large(e):
        flash('That upload was too large. Please choose a smaller image.',
              'danger')
        return redirect(request.referrer or url_for('dashboard'))


_GEO_HEADERS = ('CF-IPCountry', 'CloudFront-Viewer-Country',
                'X-Vercel-IP-Country', 'X-AppEngine-Country',
                'Fastly-Client-Country', 'X-Country-Code')


def _consent_region():
    """'us' for US visitors (opt-out model), else 'strict' (opt-in).

    Uses the country header set by the CDN/host. When no header is present
    the visitor is treated as strict, the safe default.
    """
    for header in _GEO_HEADERS:
        code = (request.headers.get(header) or '').strip().upper()
        if code and code not in ('XX', 'T1'):
            return 'us' if code == 'US' else 'strict'
    return 'strict'


def _valid_gtm_id(value):
    """Return a well-formed GTM container ID, else '' (tag disabled)."""
    value = (value or '').strip().upper()
    return value if re.fullmatch(r'GTM-[A-Z0-9]{4,12}', value) else ''


def register_template_context(app):

    @app.context_processor
    def inject_globals():
        unread = 0
        if current_user.is_authenticated:
            unread = current_user.unread_messages

        # --- Canonical URL + robots directive -------------------------
        # Public, marketing, and profile pages are indexable even when the
        # viewer happens to be logged in. Everything behind auth (dashboard,
        # inbox, admin) is noindex,nofollow so bots never see private state.
        PUBLIC_INDEXABLE = {'home', 'public_profile',
                            'help_center', 'faq_page', 'contact', 'terms',
                            'privacy',
                            'ai_grounding', 'directory', 'directory_source',
                            'countries', 'country_detail'}
        endpoint = request.endpoint
        noindex = (endpoint not in PUBLIC_INDEXABLE) or _is_impersonating()
        if noindex:
            page_robots = 'noindex, nofollow, noimageindex'
        else:
            page_robots = ('index, follow, max-image-preview:large, '
                           'max-snippet:-1, max-video-preview:-1')

        site_url = request.url_root.rstrip('/')
        # Strip query strings for a clean canonical (except pagination).
        args = {k: v for k, v in request.args.items()
                if k in ('page',)}
        canonical_url = request.base_url
        if args:
            from urllib.parse import urlencode
            canonical_url += '?' + urlencode(args)

        return {
            'current_year': datetime.now().year,
            'datetime': datetime,  # templates: period-end timestamps -> dates
            'APP_ENV': os.environ.get('FLASK_ENV', 'development'),
            'site_tagline': Setting.get('site_tagline'),
            'gtm_id': _valid_gtm_id(Setting.get('gtm_container_id')),
            'consent': {
                'enabled': Setting.get_bool('cookie_banner_enabled', True),
                'region': _consent_region(),
                'version': Setting.get_int('consent_policy_version', 1),
            },
            'TIERS': TIERS,
            # Social sign-in buttons (login/register/_auth_buttons.html).
            # Empty list when no provider credentials are configured — the
            # partial then renders nothing and standard auth is unchanged.
            'oauth_providers': _oauth_providers(),
            # Provider label/icon maps for the dashboard "Sign-in methods"
            # card (renders linked accounts even if a provider was later
            # unconfigured).
            'provider_labels': _provider_label_map()[0],
            'provider_icons': _provider_label_map()[1],
            'unread_count': unread,
            'canonical_url': canonical_url,
            'site_url': site_url,
            'site_name': 'AllMyProfiles',
            'page_robots': page_robots,
            # Public navigation / footer links (directory of Link Sources).
            'source_slug': source_slug,
            # Country search helpers (templates render flag emoji + slugs).
            'country_slug': country_slug,
            'flag_emoji': flag_emoji,

        }


# ==========================================
# App Initialization
# ==========================================

app = create_app(os.environ.get('FLASK_ENV', 'development'))

if __name__ == '__main__':
    app.run(debug=True)
