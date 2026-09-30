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
import re
from datetime import datetime, timedelta

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   abort, session, send_from_directory, Response)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_migrate import Migrate
from sqlalchemy import func

from config import config_by_name
from models import (db, User, Link, Setting, Activity, Message, MessageRead,
                    LinkCheckResult, LinkSource, ContactMessage, AbuseReport,
                    ReplyMacro, SavedView, TriageRule,
                    REPORT_REASONS, REPORT_STATUSES, CONTACT_STATUSES,
                    LINK_CATEGORIES, MESSAGE_CATEGORIES, MESSAGE_SEVERITIES,
                    PAID_TIERS, HEX_COLOR_RE)
from uploads_util import save_upload_image, delete_upload_image

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
}

USERNAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{2,30}$')


def is_claimable_username(username):
    """Return (ok, reason) for a desired public handle."""
    username = (username or '').strip().lower()
    if not USERNAME_RE.match(username):
        return False, ('Usernames must be 3-31 characters: letters, numbers,'
                       ' hyphens or underscores.')
    if username in RESERVED_USERNAMES:
        return False, 'This name is reserved.'
    if User.query.filter_by(username=username).first():
        return False, 'This name is already taken.'
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
    video, or None. Only ever built from validated ids/platforms."""
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
        'bg_image_url': t.get('bg_image_url'),
    }


def group_links_for_profile(links):
    """Split active links into pinned + categorized groups (mockup §2).

    Pinned links render first with a gold accent; remaining links are
    bucketed by display category in LINK_CATEGORIES order (unknown
    categories appended alphabetically).
    """
    pinned = [l for l in links if l.is_pinned]
    categorized = {}
    for l in links:
        if l.is_pinned:
            continue
        categorized.setdefault(l.display_category, []).append(l)
    ordered = {}
    for cat in LINK_CATEGORIES:
        if cat in categorized:
            ordered[cat] = categorized.pop(cat)
    for cat in sorted(categorized):
        ordered[cat] = categorized[cat]
    return pinned, ordered


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

    # When running under the plain dev server, make sure tables + default
    # settings exist. In production use `flask db upgrade` instead.
    if config_name == 'development' and not os.environ.get('SKIP_AUTO_SCHEMA'):
        with app.app_context():
            _ensure_schema(db)
            Setting.seed_defaults()
            LinkSource.seed_defaults()

    register_routes(app)
    register_static_pages(app)
    register_seo_routes(app)
    register_inbox_routes(app)
    register_public_interaction_routes(app)
    register_admin_routes(app)
    register_admin_message_routes(app)
    register_link_source_routes(app)
    register_admin_productivity_routes(app)
    register_error_handlers(app)
    register_template_context(app)

    return app


# ==========================================
# Core Routes
# ==========================================

def register_routes(app):

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

        return render_template('index.html', stats=stats,
                               featured=featured,
                               free_max=Setting.get_int('free_tier_max_links',
                                                        3))

    @app.route('/api/check-username')
    def check_username():
        """Live availability lookup used by the home-page claim box."""
        ok, reason = is_claimable_username(request.args.get('username', ''))
        return {'available': ok, 'message': reason}

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            username = (request.form.get('username') or '').strip().lower()
            email = (request.form.get('email') or '').strip().lower()
            password = request.form.get('password') or ''
            display_name = (request.form.get('display_name') or '').strip()

            errors = []
            if not username or len(username) < 3:
                errors.append('Username must be at least 3 characters.')
            elif not USERNAME_RE.match(username):
                errors.append('Usernames may only contain letters, numbers,'
                              ' hyphens and underscores (3-31 chars).')
            elif username in RESERVED_USERNAMES:
                errors.append('That username is reserved. Please pick'
                              ' another.')
            if '@' not in email:
                errors.append('Please enter a valid email address.')
            if len(password) < 8:
                errors.append('Password must be at least 8 characters.')

            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('register.html'), 400

            if User.query.filter_by(username=username).first():
                flash('Username is already taken.', 'danger')
                return render_template('register.html'), 400

            if User.query.filter_by(email=email).first():
                flash('Email is already registered.', 'danger')
                return render_template('register.html'), 400

            user = User(
                username=username,
                email=email,
                display_name=display_name or username,
            )
            user.set_password(password)

            db.session.add(user)
            db.session.flush()
            Activity.record('signup', user=user, detail=f'@{username} registered')
            db.session.commit()

            login_user(user)
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
                current_user.display_name = (request.form.get('display_name')
                                             or current_user.display_name).strip()
                current_user.headline = request.form.get('headline')
                current_user.bio = request.form.get('bio')
                current_user.about_section = request.form.get('about_section')
                current_user.location = (request.form.get('location')
                                         or '').strip()[:120] or None
                # Skills: normalized list, stored back as a clean CSV string.
                skills = normalize_skills(request.form.get('skills'))
                current_user.skills = ', '.join(skills) or None
                # Adult-content flag (checkbox posts 'on' when ticked).
                current_user.is_adult_oriented = bool(
                    request.form.get('is_adult_oriented'))
                # Intro video: accept full URL or bare ID; store validated id.
                video_raw = (request.form.get('intro_video') or '').strip()
                if video_raw:
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
                link.category = cat if cat in LINK_CATEGORIES else None
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
        return render_template('dashboard.html', links=user_links,
                               total_clicks=total_clicks,
                               max_upload_kb=Setting.get_int('max_upload_size_kb', 2048),
                               link_sources=LinkSource.active(),
                               link_categories=LINK_CATEGORIES,
                               themes=PROFILE_THEMES)

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

    # ==========================================
    # Public Facing Profile & Analytics
    # ==========================================

    @app.route('/u/<username>')
    def public_profile(username):
        """ Public SEO optimized profile page (canonical: /u/<username>) """
        user = User.query.filter_by(username=username.lower()).first_or_404()
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

    @app.route('/u/<username>/confirm-age', methods=['POST'])
    def confirm_age(username):
        """Age confirmation for adult-oriented profiles (18+)."""
        user = User.query.filter_by(username=username.lower()).first_or_404()
        if not user.is_adult_oriented:
            return redirect(url_for('public_profile', username=user.username))
        if request.form.get('confirm') == 'yes':
            session['age_ok'] = True
        return redirect(url_for('public_profile', username=user.username))

    # Convenience alias so bare /<username> also works (kept last so it
    # never shadows the routes above).
    @app.route('/<username>')
    def public_profile_alias(username):
        if username.lower() in RESERVED_USERNAMES:
            abort(404)
        return public_profile(username)

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
- Canonical URL: {base}/u/<username>
- Machine-readable link feed: {base}/u/<username>/links.xml
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

        unread_total = current_user.unread_messages
        return render_template('inbox.html', reads=reads, view=view,
                               category=category,
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
        if not msg.is_read:
            msg.is_read = True
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

    @app.route('/u/<username>/submit-contact', methods=['POST'])
    def submit_contact(username):
        """Profile 'Contact Me' modal -> recipient's inbox (unread)."""
        user = User.query.filter_by(username=username.lower()).first_or_404()
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
            return redirect(url_for('public_profile', username=user.username))
        if not CONTACT_EMAIL_RE.match(email):
            flash('That email address looks invalid.', 'danger')
            return redirect(url_for('public_profile', username=user.username))
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
            return redirect(url_for('public_profile', username=user.username))
        msg = ContactMessage(recipient_id=user.id, sender_name=name,
                             sender_email=email, subject=subject or None,
                             body=body, sender_ip=ip)
        db.session.add(msg)
        Activity.record('contact_received', user=user, detail=f'from {email}')
        db.session.commit()
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
        return render_template('admin/users.html',
                               pagination=pagination, users=pagination.items,
                               q=q)

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
        flag = request.form.get('flag')  # 'is_admin'
        if flag == 'is_admin':
            user.is_admin = not user.is_admin
            Activity.record('admin_flag_change', user=user, actor=real,
                            detail='granted' if user.is_admin else 'revoked')
            db.session.commit()
            flash('User updated.', 'success')
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
                    'link_check_notify_days']
        numeric = {'free_tier_max_links', 'expanded_tier_max_links',
                   'max_upload_size_kb', 'link_check_notify_days'}
        if request.method == 'POST':
            for key in editable:
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
                Setting.set_value(key, value)
            Activity.record('settings_update', actor=_real_admin(),
                            detail='updated site settings')
            db.session.commit()
            flash('Settings saved.', 'success')
            return redirect(url_for('admin_settings'))

        settings = {k: Setting.get(k) for k in Setting.DEFAULT_SETTINGS}
        return render_template('admin/settings.html', settings=settings)

    # Expose helpers to templates
    app.jinja_env.globals['is_impersonating'] = _is_impersonating
    app.jinja_env.filters['skills_list'] = normalize_skills
    app.jinja_env.globals['video_embed'] = video_embed


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
        cta = (form.get('cta') or '').strip()[:40]
        try:
            sort_order = int(form.get('sort_order') or 0)
        except ValueError:
            sort_order = 0

        cleaned = {
            'name': name, 'domains': domains, 'url': url,
            'profile_pattern': pattern, 'icon_code': icon_code,
            'category': category, 'cta': cta, 'sort_order': sort_order,
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
                               categories=LINK_CATEGORIES)

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
                                       categories=LINK_CATEGORIES)
            src = LinkSource(**data)
            db.session.add(src)
            Activity.record('link_source_created', actor=_real_admin(),
                            detail=f'created link source "{src.name}"')
            db.session.commit()
            flash(f'Link source "{src.name}" created. It is live for users.',
                  'success')
            return redirect(url_for('admin_link_sources'))
        return render_template('admin/link_source_form.html', src=None,
                               form=None, categories=LINK_CATEGORIES)

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
                                       categories=LINK_CATEGORIES)
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
                               form=None, categories=LINK_CATEGORIES)

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
            'Allow: /u/',
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
        fav = url_for('favicon')
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<browserconfig>
  <msapplication>
    <tile>
      <square70x70logo src="{fav}?size=70"/>
      <square150x150logo src="{fav}?size=150"/>
      <square310x310logo src="{fav}?size=310"/>
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
            ('/contact', 'monthly', '0.4'),
            ('/ai-grounding', 'weekly', '0.7'),
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
            lastmod = _iso_dt(getattr(u, 'updated_at', None) or u.created_at)
            canonical = f'{base}/u/{u.username}'
            links_feed = f'{base}/u/{u.username}/links.xml'
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
    @app.route('/u/<username>/links.xml')
    def user_links_xml(username):
        user = User.query.filter_by(username=username.lower()).first_or_404()
        base = _site_base_url()
        links = (Link.query.filter_by(user_id=user.id, is_active=True)
                 .order_by(Link.position.asc()).all())
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 f'<links user="{user.username}" display_name="{user.display_name}"'
                 f' profile="{base}/u/{user.username}">']
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
        PUBLIC_INDEXABLE = {'home', 'public_profile', 'public_profile_alias',
                            'help_center', 'contact', 'terms', 'privacy',
                            'ai_grounding'}
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
            'APP_ENV': os.environ.get('FLASK_ENV', 'development'),
            'site_tagline': Setting.get('site_tagline'),
            'TIERS': TIERS,
            'unread_count': unread,
            'canonical_url': canonical_url,
            'site_url': site_url,
            'site_name': 'AllMyProfiles',
            'page_robots': page_robots,
        }


# ==========================================
# App Initialization
# ==========================================

app = create_app(os.environ.get('FLASK_ENV', 'development'))

if __name__ == '__main__':
    app.run(debug=True)
