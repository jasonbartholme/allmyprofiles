"""
Database models for AllMyProfiles.

Import `db` from this module when creating tables / running migrations:

    from models import db, User, Link, Setting, Activity
"""

import re
from datetime import datetime
from urllib.parse import parse_qsl

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# Default values for admin-editable site settings (see Setting model).
DEFAULT_SETTINGS = {
    'free_tier_max_links': '3',        # max active links for Free-tier users
    'expanded_tier_max_links': '10',   # max active links for Expanded tier
    'site_tagline': 'All your links in one place.',
    'max_upload_size_kb': '2048',      # avatar upload size limit (KB)
    # Broken-link notification spam guard: minimum days between reports
    # about the *same* still-broken link set (used by check_links.py).
    'link_check_notify_days': '7',
}


class Setting(db.Model):
    """Simple key/value store for admin-editable site settings."""
    __tablename__ = 'settings'

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(500), nullable=False, default='')

    def __repr__(self):
        return f'<Setting {self.key}={self.value!r}>'

    @classmethod
    def get(cls, key, fallback=None):
        row = db.session.get(cls, key)
        if row is not None:
            return row.value
        return DEFAULT_SETTINGS.get(key, fallback)

    @classmethod
    def get_int(cls, key, fallback=0):
        try:
            return int(cls.get(key, str(fallback)))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def set_value(cls, key, value):
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key, value=str(value))
            db.session.add(row)
        else:
            row.value = str(value)

    @classmethod
    def seed_defaults(cls):
        """Insert any missing default settings (idempotent)."""
        created = False
        for key, value in DEFAULT_SETTINGS.items():
            if db.session.get(cls, key) is None:
                db.session.add(cls(key=key, value=value))
                created = True
        if created:
            db.session.commit()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)

    # Profile customization fields
    display_name = db.Column(db.String(120), nullable=False)
    headline = db.Column(db.String(250), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    about_section = db.Column(db.Text, nullable=True)
    # Externally-hosted avatar URL (legacy / optional). Local uploads take
    # precedence via avatar_path below.
    avatar_url = db.Column(db.String(500), nullable=True)
    # Relative path (inside UPLOAD_FOLDER) of a locally-stored uploaded
    # avatar, e.g. "avatars/3-a1b2c3d4.jpg". Set by the dashboard upload form.
    avatar_path = db.Column(db.String(300), nullable=True)

    # Tiering & Monetization
    tier = db.Column(db.String(20), default='Free')  # Free, Expanded, Full, Custom
    tier_changed_at = db.Column(db.DateTime, nullable=True)

    # Profile extras (from Colab mockup spec)
    # Skills: short comma-separated list rendered as badges on the public
    # profile (e.g. "Python, Flask, UI/UX"). Normalized/capped in app.py.
    skills = db.Column(db.String(300), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    # Adult-oriented content flag. When True the profile renders an
    # age-gate interstitial and sends `noindex, nofollow` meta so it is
    # excluded from search/AI indexing.
    is_adult_oriented = db.Column(db.Boolean, default=False,
                                  nullable=False, server_default='0')
    # Intro video embed: platform is 'youtube' or 'vimeo' (None = disabled)
    video_platform = db.Column(db.String(10), nullable=True)
    video_id = db.Column(db.String(64), nullable=True)
    # Per-user Google Analytics 4 Measurement ID (e.g. "G-ABC123XYZ9").
    # Validated against ^G-[A-Z0-9]{6,20}$ before being stored/rendered.
    ga4_id = db.Column(db.String(32), nullable=True)

    # Site administration flag
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    # Curated profile theme key (see app.PROFILE_THEMES). Users pick from a
    # fixed palette; arbitrary colors are never accepted. 'Free' tier is
    # restricted to the first two themes server-side.
    theme = db.Column(db.String(20), nullable=True, default='light')
    # Contact form: when True the public "Contact Me" button/modal is
    # hidden for this profile.
    contact_disabled = db.Column(db.Boolean, default=False, nullable=False,
                                 server_default='0')

    created_at = db.Column(db.DateTime, default=lambda: datetime.now())

    # Relationships
    links = db.relationship('Link', backref='owner', lazy=True,
                            cascade='all, delete-orphan')
    message_reads = db.relationship('MessageRead',
                                    back_populates='user',
                                    foreign_keys='MessageRead.user_id',
                                    cascade='all, delete-orphan',
                                    lazy='dynamic')

    @property
    def unread_messages(self):
        """Count of unread, non-archived inbox items for this user.

        Includes admin->user messages AND visitor contact messages so the
        navbar badge reflects everything waiting in the inbox.
        """
        admin_unread = (MessageRead.query
                        .filter_by(user_id=self.id, is_read=False,
                                   is_archived=False)
                        .count())
        contact_unread = ContactMessage.query.filter_by(
            recipient_id=self.id, is_read=False, is_archived=False).count()
        return admin_unread + contact_unread

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def max_active_links(self):
        """Tier-based link limits, driven by admin-editable site settings.

        None means unlimited ('Full' / 'Custom' tiers).
        """
        if self.tier == 'Free':
            return Setting.get_int('free_tier_max_links', 3)
        if self.tier == 'Expanded':
            return Setting.get_int('expanded_tier_max_links', 10)
        return None  # 'Full' / 'Custom' -> unlimited

    @property
    def avatar_src(self):
        """Best available avatar: local upload first, then external URL,
        then a generated initials placeholder."""
        if self.avatar_path:
            return url_for_upload(self.avatar_path)
        if self.avatar_url:
            return self.avatar_url
        from urllib.parse import quote
        return ('https://ui-avatars.com/api/?background=random&name='
                + quote(self.display_name or self.username))


def url_for_upload(rel_path):
    """Public URL for a file inside UPLOAD_FOLDER (served via /uploads)."""
    from flask import url_for
    return url_for('uploaded_file', filename=rel_path)


class Activity(db.Model):
    """Append-only audit log used by the admin dashboard's recent-activity
    feed: signups, tier upgrades/downgrades, admin actions."""
    __tablename__ = 'activity'

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(40), nullable=False, index=True)
    # e.g. 'signup', 'tier_change', 'impersonate_start', 'impersonate_end',
    #      'account_deleted', 'settings_update'

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    detail = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           index=True)

    user = db.relationship('User', foreign_keys=[user_id])
    actor = db.relationship('User', foreign_keys=[actor_id])

    @classmethod
    def record(cls, kind, user=None, actor=None, detail=None):
        entry = cls(kind=kind,
                    user_id=getattr(user, 'id', None),
                    actor_id=getattr(actor, 'id', None),
                    detail=detail)
        db.session.add(entry)
        return entry


class Link(db.Model):
    __tablename__ = 'links'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    title = db.Column(db.String(120), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    icon_code = db.Column(db.String(50), nullable=True, default='link-45deg')
    position = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    click_count = db.Column(db.Integer, default=0)

    # Profile-redesign fields (Colab mockup). All optional with safe
    # defaults so existing rows keep working after migration.
    source_id = db.Column(db.Integer, db.ForeignKey('link_sources.id'),
                          nullable=True)
    category = db.Column(db.String(40), nullable=True)  # manual override
    is_pinned = db.Column(db.Boolean, default=False, nullable=False,
                          server_default='0')           # paid tiers only
    utm_params = db.Column(db.String(300), nullable=True)
    subhandle = db.Column(db.String(80), nullable=True)  # "@handle" line
    cta = db.Column(db.String(40), nullable=True)        # "Follow", "Listen"

    created_at = db.Column(db.DateTime, default=lambda: datetime.now())

    @property
    def matched_source(self):
        """LinkSource whose domain matches this link's URL (or None)."""
        if self.source_id:
            src = db.session.get(LinkSource, self.source_id)
            if src is not None:
                return src
        host = _url_host(self.url)
        return LinkSource.for_host(host) if host else None

    @property
    def display_category(self):
        src = self.matched_source
        return self.category or (src.category if src else 'Other') \
            or 'Other'

    @property
    def brand_bg(self):
        src = self.matched_source
        return src.bg_color if src else '#ffffff'

    @property
    def brand_text(self):
        src = self.matched_source
        return src.text_color if src else '#212529'

    @property
    def brand_border(self):
        src = self.matched_source
        return src.border_color if src else '#dee2e6'

    @property
    def brand_icon(self):
        """Bootstrap-icons class for this link's card."""
        src = self.matched_source
        if self.icon_code and not src:
            return f'bi bi-{self.icon_code}'
        code = (src.icon_code if src and src.icon_code
                else self.icon_code) or 'link-45deg'
        return f'bi bi-{code}'

    @property
    def safe_utm(self):
        """UTM string only if it looks like genuine UTM params."""
        u = (self.utm_params or '').strip()
        if u.startswith('?utm_') and len(u) <= 300 and '"' not in u \
                and '<' not in u and ' ' not in u:
            return u
        return ''


def _url_host(url):
    from urllib.parse import urlparse
    try:
        p = urlparse(url if '://' in url else 'https://' + url)
        h = (p.hostname or '').lower()
        return h[4:] if h.startswith('www.') else h
    except ValueError:
        return None


# ==========================================
# Link Sources (admin-managed social networks)
# ==========================================

# Category order used by the profile tabs.
LINK_CATEGORIES = ['Gaming & Dev', 'Content', 'Social', 'Store', 'Portfolio',
                   'Music', 'Other']

DEFAULT_LINK_SOURCES = [
    # name, domains (comma-sep), bg, text, border, bootstrap icon, category
    ('GitHub',    'github.com,gitlab.com',            '#24292e', '#ffffff', '#24292e', 'github',        'Gaming & Dev'),
    ('Steam',     'store.steampowered.com,steamcommunity.com', '#1b2838', '#c7d5e0', '#171a21', 'steam', 'Gaming & Dev'),
    ('Xbox Live', 'xbox.com,xboxlive.com',            '#107c10', '#ffffff', '#0b5a0b', 'controller',    'Gaming & Dev'),
    ('PlayStation Network', 'playstation.com',        '#00439c', '#ffffff', '#002d6b', 'controller',    'Gaming & Dev'),
    ('Discord',   'discord.gg,discord.com',           '#5865f2', '#ffffff', '#4752c4', 'discord',       'Social'),
    ('YouTube',   'youtube.com,youtu.be',             '#ff0000', '#ffffff', '#cc0000', 'youtube',       'Content'),
    ('Twitch',    'twitch.tv',                        '#9146ff', '#ffffff', '#772ce8', 'twitch',        'Content'),
    ('Vimeo',     'vimeo.com',                        '#1ab7ea', '#ffffff', '#1494bd', 'vimeo',         'Content'),
    ('Medium',    'medium.com',                       '#12100e', '#ffffff', '#000000', 'medium',        'Content'),
    ('Substack',  'substack.com',                     '#ff6719', '#ffffff', '#d95511', 'envelope-paper','#Content'),
    ('Spotify',   'spotify.com',                      '#1db954', '#0b2a14', '#169c46', 'spotify',       'Music'),
    ('Apple Music','music.apple.com',                 '#fa243c', '#ffffff', '#c91e31', 'apple-music',   'Music'),
    ('SoundCloud','soundcloud.com',                   '#ff5500', '#ffffff', '#d64800', 'cloud-fill',    'Music'),
    ('Bandcamp',  'bandcamp.com',                     '#629aa9', '#ffffff', '#4f7d87', 'music-note-beamed', 'Music'),
    ('X / Twitter','twitter.com,x.com',               '#000000', '#ffffff', '#333333', 'twitter-x',     'Social'),
    ('LinkedIn',  'linkedin.com',                     '#0a66c2', '#ffffff', '#085299', 'linkedin',      'Social'),
    ('Reddit',    'reddit.com',                       '#ff4500', '#ffffff', '#d63a00', 'reddit',        'Social'),
    ('Quora',     'quora.com',                        '#b92b27', '#ffffff', '#97231f', 'question-circle', 'Social'),
    ('Facebook',  'facebook.com',                     '#1877f2', '#ffffff', '#125ecc', 'facebook',      'Social'),
    ('Instagram', 'instagram.com',                    '#c13584', '#ffffff', '#9a2a69', 'instagram',     'Social'),
    ('TikTok',    'tiktok.com',                       '#010101', '#ffffff', '#25f4ee', 'camera-reels',  'Social'),
    ('Threads',   'threads.net',                      '#000000', '#ffffff', '#333333', 'at',            'Social'),
    ('Patreon',   'patreon.com',                      '#f96854', '#ffffff', '#c94f3f', 'heart-fill',    'Store'),
    ('Ko-fi',     'ko-fi.com',                        '#ff5e5b', '#ffffff', '#d94c49', 'cup-hot',       'Store'),
    ('Buy Me a Coffee', 'buymeacoffee.com',           '#ffd43b', '#2b2b2b', '#e0b92f', 'cash-coin',     'Store'),
    ('Etsy',      'etsy.com',                         '#f16521', '#ffffff', '#c95119', 'shop',          'Store'),
    ('Amazon Storefront', 'amazon.com,amzn.to',       '#ff9900', '#131921', '#e08700', 'bag',           'Store'),
    ('Personal Website', '',                            '#f8f9fa', '#212529', '#dee2e6', 'globe2',      'Portfolio'),
]


class LinkSource(db.Model):
    """Admin-managed catalogue of social/link platforms.

    Each source carries the platform's brand colors so profile link cards
    can be styled to match the origin site while keeping contrast readable
    (bg/text pairs are curated by the admin, never free-form user input).
    """
    __tablename__ = 'link_sources'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    domains = db.Column(db.String(300), nullable=True)  # comma-separated
    url = db.Column(db.String(300), nullable=True)      # example/default URL
    profile_pattern = db.Column(db.String(300), nullable=True)  # {handle} tmpl
    bg_color = db.Column(db.String(9), default='#ffffff', nullable=False)
    text_color = db.Column(db.String(9), default='#212529', nullable=False)
    border_color = db.Column(db.String(9), default='#dee2e6', nullable=False)
    icon_code = db.Column(db.String(50), default='link-45deg')  # bi-* slug
    custom_icon_path = db.Column(db.String(300), nullable=True)  # uploads/…
    # When True, profile link anchors for this source get rel="nofollow"
    # (useful for affiliate/adult/partner sites you don't want to vouch for).
    is_nofollow = db.Column(db.Boolean, default=False, nullable=False,
                            server_default='0')
    cta = db.Column(db.String(40), nullable=True)       # "Follow", "Subscribe"
    category = db.Column(db.String(40), default='Other', nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_adult = db.Column(db.Boolean, default=False, nullable=False,
                         server_default='0')
    is_deleted = db.Column(db.Boolean, default=False, nullable=False,
                           server_default='0')  # soft delete (admin CRUD)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    HEX_RE = None  # set below (avoids shadowing confusion in class body)

    def __repr__(self):
        return f'<LinkSource {self.name}>'

    @classmethod
    def active(cls):
        """Sources shown to users: not soft-deleted and flagged active."""
        return (cls.query.filter_by(is_deleted=False, is_active=True)
                .order_by(cls.sort_order, cls.name).all())

    @classmethod
    def catalogue(cls):
        """Everything except soft-deleted rows (admin list view)."""
        return (cls.query.filter_by(is_deleted=False)
                .order_by(cls.sort_order, cls.name).all())

    @classmethod
    def domain_map(cls):
        """{'github.com': <LinkSource>, ...} for all active sources."""
        out = {}
        for src in cls.active():
            for d in (src.domains or '').split(','):
                d = d.strip().lower()
                if d:
                    out.setdefault(d, src)
        return out

    @classmethod
    def for_host(cls, host):
        if not host:
            return None
        dm = cls.domain_map()
        parts = host.split('.')
        for i in range(len(parts) - 1):
            candidate = '.'.join(parts[i:])
            if candidate in dm:
                return dm[candidate]
        return None

    @classmethod
    def seed_defaults(cls):
        """Populate the catalogue on first run (idempotent)."""
        if cls.query.first() is not None:
            return
        for idx, (name, domains, bg, txt, brd, icon, cat) in enumerate(DEFAULT_LINK_SOURCES):
            first_domain = (domains or '').split(',')[0].strip()
            url = f'https://{first_domain}/' if first_domain else None
            pattern = (f'https://{first_domain}/{{handle}}'
                       if first_domain else None)
            db.session.add(cls(name=name, domains=domains, bg_color=bg,
                               text_color=txt, border_color=brd,
                               icon_code=icon, category=cat,
                               sort_order=idx, url=url,
                               profile_pattern=pattern))
        db.session.commit()


HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
LinkSource.HEX_RE = HEX_COLOR_RE


# ==========================================
# Admin -> User Messaging ("Inbox")
# ==========================================

# Message categories. Each has a display label and a default severity,
# but the admin can override the severity when composing.
MESSAGE_CATEGORIES = {
    'announcement':  {'label': 'Feature announcement',    'default_severity': 'info'},
    'broken_links':  {'label': 'Broken link report',      'default_severity': 'warning'},
    'note':          {'label': 'Personal note',           'default_severity': 'info'},
    'tos_violation': {'label': 'Terms of Service notice', 'default_severity': 'critical'},
    'billing':       {'label': 'Billing / account',       'default_severity': 'info'},
}

# Severity levels, ordered by ascending urgency. Drives badge colour and
# inbox sorting (most severe first).
MESSAGE_SEVERITIES = {
    'info':     {'rank': 0, 'label': 'Info',      'badge': 'secondary'},
    'success':  {'rank': 1, 'label': 'Good news', 'badge': 'success'},
    'warning':  {'rank': 2, 'label': 'Warning',   'badge': 'warning'},
    'critical': {'rank': 3, 'label': 'Urgent',    'badge': 'danger'},
}

# Paid tiers — used by the broadcast audience filter "paid users only".
PAID_TIERS = ('Expanded', 'Full', 'Custom')


class Message(db.Model):
    """A message sent from the admin to one or more users.

    Broadcasts create a single Message row shared by all recipients; each
    recipient's read/archived state lives in its own MessageRead row so we
    never duplicate message bodies.
    """
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(30), nullable=False, default='note')
    severity = db.Column(db.String(20), nullable=False, default='info')

    # Optional link included with the message (e.g. changelog URL).
    action_url = db.Column(db.String(500), nullable=True)
    action_label = db.Column(db.String(60), nullable=True)

    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           index=True)

    # How this message was delivered: 'direct' (one user), 'broadcast'
    # (audience filter), or 'link_check' (generated by check_links.py).
    source = db.Column(db.String(20), default='direct', nullable=False)
    # For link_check messages: fingerprint of the broken-link set + period,
    # used to avoid re-notifying about the same problem (spam guard).
    dedupe_key = db.Column(db.String(120), index=True, nullable=True)

    sender = db.relationship('User', foreign_keys=[sender_id])
    reads = db.relationship('MessageRead', back_populates='message',
                            cascade='all, delete-orphan', lazy='dynamic')

    @property
    def category_label(self):
        return MESSAGE_CATEGORIES.get(self.category, {}).get(
            'label', self.category)

    @property
    def severity_info(self):
        return MESSAGE_SEVERITIES.get(self.severity,
                                      MESSAGE_SEVERITIES['info'])

    def recipient_count(self):
        return self.reads.count()

    def unread_count(self):
        return MessageRead.query.filter_by(message_id=self.id,
                                           is_read=False).count()

    def __repr__(self):
        return f'<Message #{self.id} {self.subject!r} [{self.category}]>'


class MessageRead(db.Model):
    """Per-user delivery/read/archive state for a message."""
    __tablename__ = 'message_reads'
    __table_args__ = (
        db.UniqueConstraint('message_id', 'user_id', name='uq_message_user'),
    )

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'),
                           nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    delivered_at = db.Column(db.DateTime, default=lambda: datetime.now())
    read_at = db.Column(db.DateTime, nullable=True)

    message = db.relationship('Message', back_populates='reads')
    user = db.relationship('User', foreign_keys=[user_id])


class LinkCheckResult(db.Model):
    """Latest status of a link as found by the periodic link checker.

    Upserted by check_links.py. `last_notified_at` powers the notification
    cooldown so users aren't spammed with repeated reports of the same
    outage.
    """
    __tablename__ = 'link_check_results'

    id = db.Column(db.Integer, primary_key=True)
    link_id = db.Column(db.Integer, db.ForeignKey('links.id'),
                        nullable=False, unique=True, index=True)
    http_status = db.Column(db.Integer, nullable=True)   # None = request error
    is_broken = db.Column(db.Boolean, default=False, nullable=False,
                          index=True)
    checked_at = db.Column(db.DateTime, default=lambda: datetime.now())
    # When did this link *first* appear broken? Used for "down since" text.
    broken_since = db.Column(db.DateTime, nullable=True)
    last_notified_at = db.Column(db.DateTime, nullable=True)

    link = db.relationship('Link')


# ==========================================
# Public contact messages (profile "Contact Me" form -> user inbox)
# ==========================================

CONTACT_STATUSES = ['new', 'read', 'replied', 'archived']


class ContactMessage(db.Model):
    """A message a visitor sent from someone's public profile page.

    Delivered into the *recipient user's* inbox as an unread item with a
    category and severity, mirroring the admin-message UX. Includes abuse
    safeguards: honeypot field, rate limiting by IP, and length caps.
    """
    __tablename__ = 'contact_messages'

    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                             nullable=False, index=True)
    sender_name = db.Column(db.String(80), nullable=False)
    sender_email = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(120), nullable=True)
    body = db.Column(db.Text, nullable=False)
    # Spam/abuse metadata
    sender_ip = db.Column(db.String(45), nullable=True)
    is_spam = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           index=True)
    # Delivery status inside the recipient's inbox
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    # Workflow status for the recipient ('new' → 'read' → 'replied'/'archived')
    status = db.Column(db.String(20), default='new', nullable=False,
                       index=True)

    recipient = db.relationship('User', foreign_keys=[recipient_id])

    @property
    def preview(self):
        text = (self.body or '').strip().replace('\n', ' ')
        return text[:90] + ('…' if len(text) > 90 else '')


# ==========================================
# Abuse reports (public "Report Abuse" -> admin console section)
# ==========================================

REPORT_REASONS = {
    'adult_unmarked': 'Adult content not marked 18+',
    'spam_scam':      'Spam, scam, or fraud',
    'harassment':     'Harassment or hate speech',
    'copyright':      'Copyright / trademark violation',
    'malware':        'Malware or phishing links',
    'impersonation':  'Impersonation',
    'other':          'Something else',
}

REPORT_STATUSES = ['open', 'reviewing', 'resolved', 'dismissed']


class AbuseReport(db.Model):
    """Public abuse report about a profile or one of its links.

    Surfaces in the admin console (like the messaging system) rather than
    email so it scales to many reports/day. Admin can set a status and an
    internal note.
    """
    __tablename__ = 'abuse_reports'

    id = db.Column(db.Integer, primary_key=True)
    reporter_name = db.Column(db.String(80), nullable=True)   # optional
    reporter_email = db.Column(db.String(120), nullable=True)  # optional
    target_user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                               nullable=False, index=True)
    target_link_id = db.Column(db.Integer, db.ForeignKey('links.id'),
                               nullable=True)
    reason = db.Column(db.String(30), nullable=False)         # REPORT_REASONS key
    details = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='open', nullable=False,
                       index=True)                            # REPORT_STATUSES
    admin_note = db.Column(db.Text, nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                               nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           index=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    target_user = db.relationship('User', foreign_keys=[target_user_id])
    resolved_by = db.relationship('User', foreign_keys=[resolved_by_id])

    @property
    def reason_label(self):
        return REPORT_REASONS.get(self.reason, self.reason)


# ==========================================
# Admin productivity: reply macros, saved views, triage rules
# ==========================================

class ReplyMacro(db.Model):
    """Reusable canned-reply template for the admin console.

    Supports {{placeholders}} substituted at send time (see app.py
    MACRO_PLACEHOLDERS). Macros are shared across all admins.
    """
    __tablename__ = 'reply_macros'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(db.DateTime, nullable=True)

    def render(self, context=None):
        """Fill known placeholders; leave unknown {{tokens}} intact."""
        from string import Template
        return Template(self.body).safe_substitute(context or {})


class SavedView(db.Model):
    """Admin-saved filter combination (users list or abuse reports)."""
    __tablename__ = 'saved_views'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    page = db.Column(db.String(20), nullable=False, default='users')  # users|reports
    query_string = db.Column(db.String(300), nullable=False, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())

    def url_args(self):
        return dict(parse_qsl(self.query_string or ''))


class TriageRule(db.Model):
    """Auto-action applied to incoming abuse reports (admin automation).

    Matching is AND-based on optional fields: reason, domain (substring of
    any reported link URL or the target profile's links), and a keyword in
    the report details. When matched, the report's status is set and an
    optional message category/audience note is recorded. Rules run in
    priority order; first match wins unless `apply_all` is set.
    """
    __tablename__ = 'triage_rules'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    # Optional matchers (blank = matches anything)
    match_reason = db.Column(db.String(30), nullable=True)
    match_domain = db.Column(db.String(120), nullable=True)   # substring, lowercased
    match_keyword = db.Column(db.String(80), nullable=True)   # substring in details
    # Actions
    set_status = db.Column(db.String(20), nullable=False, default='reviewing')
    note = db.Column(db.String(300), nullable=True)           # appended to admin_note
    is_active = db.Column(db.Boolean, default=True, nullable=False,
                          server_default='1')
    priority = db.Column(db.Integer, default=0, nullable=False,
                         server_default='0')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())

    def matches(self, report):
        if self.match_reason and (report.reason or '') != self.match_reason:
            return False
        if self.match_domain:
            dom = self.match_domain.strip().lower()
            # Domain can appear on the reported link itself or on any of
            # the target profile's links.
            hosts = [_url_host(l.url) or '' for l in report.target_user.links] \
                if report.target_user else []
            blob = ' '.join(hosts).lower()
            if dom not in blob:
                return False
        if self.match_keyword:
            kw = self.match_keyword.strip().lower()
            if kw and kw not in (report.details or '').lower():
                return False
        return True
