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

import os
import re
from datetime import datetime, timedelta

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   abort, session, send_from_directory)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_migrate import Migrate
from sqlalchemy import func

from config import config_by_name
from models import (db, User, Link, Setting, Activity, Message, MessageRead,
                    LinkCheckResult, MESSAGE_CATEGORIES, MESSAGE_SEVERITIES,
                    PAID_TIERS)
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
    'privacy', 'profile', 'redirect', 'register', 'settings', 'static',
    'status', 'support', 'terms', 'test', 'uploads', 'u', 'www',
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
    migrate = Migrate(app, db)  # noqa: F841  (enables `flask db` commands)
    login_manager.init_app(app)

    # When running under the plain dev server, make sure tables + default
    # settings exist. In production use `flask db upgrade` instead.
    if config_name == 'development':
        with app.app_context():
            db.create_all()
            Setting.seed_defaults()

    register_routes(app)
    register_static_pages(app)
    register_inbox_routes(app)
    register_admin_routes(app)
    register_admin_message_routes(app)
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

            return redirect(url_for('dashboard'))

        user_links = (Link.query.filter_by(user_id=current_user.id)
                      .order_by(Link.position.asc()).all())
        total_clicks = sum(link.click_count or 0 for link in user_links)
        return render_template('dashboard.html', links=user_links,
                               total_clicks=total_clicks,
                               max_upload_kb=Setting.get_int('max_upload_size_kb', 2048))

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
        return render_template('profile.html', user=user, links=active_links)

    # Convenience alias so bare /<username> also works (kept last so it
    # never shadows the routes above).
    @app.route('/<username>')
    def public_profile_alias(username):
        if username.lower() in RESERVED_USERNAMES:
            abort(404)
        return public_profile(username)

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
        return {
            'current_year': datetime.now().year,
            'APP_ENV': os.environ.get('FLASK_ENV', 'development'),
            'site_tagline': Setting.get('site_tagline'),
            'TIERS': TIERS,
            'unread_count': unread,
        }


# ==========================================
# App Initialization
# ==========================================

app = create_app(os.environ.get('FLASK_ENV', 'development'))

if __name__ == '__main__':
    app.run(debug=True)
