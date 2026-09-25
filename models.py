"""
Database models for AllMyProfiles.

Import `db` from this module when creating tables / running migrations:

    from models import db, User, Link, Setting, Activity
"""

from datetime import datetime

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

    # Site administration flag
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

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
        """Count of unread, non-archived inbox messages for this user."""
        return (MessageRead.query
                .filter_by(user_id=self.id, is_read=False, is_archived=False)
                .count())

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

    created_at = db.Column(db.DateTime, default=lambda: datetime.now())


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
