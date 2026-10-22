"""
Database models for AllMyProfiles.

Import `db` from this module when creating tables / running migrations:

    from models import db, User, Link, Setting, Activity
"""

import re
from datetime import datetime, timedelta
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
    'max_upload_size_kb': '2048',      # profile-image upload size limit (KB)
    # Broken-link notification spam guard: minimum days between reports
    # about the *same* still-broken link set (used by check_links.py).
    'link_check_notify_days': '7',
    # Public directory (/directory/<source>) display rules.
    'directory_page_size': '24',      # non-featured members per page
    'directory_featured_max': '5',    # paid "featured" slots at the top
    # Country search (/countries, /country/<iso2>) display rules.
    'country_page_size': '24',        # profiles per country page
    'country_sidebar_top': '10',      # "Top Countries" leaderboard size
    # Show buckets for locations that don't resolve to a real country
    # (e.g. "Springfield"). Off by default: only genuine countries with
    # opted-in searchable profiles appear on /countries.
    'country_show_unrecognized': 'false',
    # Stripe billing. Price IDs entered here override the environment
    # variables (see config.Config.STRIPE_PRICE_*); create the prices in the
    # Stripe dashboard (Products -> your plan -> Add price) and paste the
    # "price_..." IDs below. Leave blank to use the env values.
    'stripe_price_expanded': '',      # Expanded tier subscription price
    'stripe_price_full': '',          # Full tier subscription price
    'stripe_customer_portal_return': '/dashboard',  # portal "back to site" URL
    # Google Tag Manager container ID (GTM-XXXXXXX). Blank disables the tag.
    'gtm_container_id': '',
    # Cookie consent banner. Bump the version to re-ask every visitor.
    'cookie_banner_enabled': 'true',
    'consent_policy_version': '1',
    # --- Outbound email (Gmail SMTP or any provider) ----------------------
    # Configured from Admin -> Settings -> "Email delivery (SMTP)".
    # Values can also be supplied via MAIL_* env vars (see mail_util.py);
    # a saved DB value takes precedence over the env var.
    # Gmail: host smtp.gmail.com, port 465 + security "ssl", and an APP
    # PASSWORD (myaccount.google.com/apppasswords) — not the account password.
    'smtp_enabled': 'false',        # master switch; off = inbox-only mode
    'smtp_host': '',                # e.g. smtp.gmail.com
    'smtp_port': '465',             # 465 ssl / 587 starttls / 2525 relay
    'smtp_security': 'ssl',         # ssl | starttls | none
    'smtp_username': '',            # full Gmail address
    'smtp_password': '',            # App Password (masked in the admin UI)
    'mail_from': '',                # From: address (blank = use username)
    'mail_from_name': 'AllMyProfiles',  # From: display name
}


class Setting(db.Model):
    """Simple key/value store for admin-editable site settings."""
    __tablename__ = 'settings'

    # Module-level defaults exposed as a class attribute so callers can use
    # Setting.DEFAULT_SETTINGS (see app.py admin_settings route).
    DEFAULT_SETTINGS = DEFAULT_SETTINGS

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
    def get_bool(cls, key, fallback=False):
        """Truthy values: 'true'/'1'/'yes'/'on' (case-insensitive)."""
        raw = (cls.get(key, 'true' if fallback else 'false') or '').strip()
        return raw.lower() in ('1', 'true', 'yes', 'on')

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
    # Display Name doubles as the public profile handle: the SEO-friendly
    # URL is /<display-name-slug>, so it must be provided to create a public profile and
    # be unique across all claimed display names (case-insensitive).
    display_name = db.Column(db.String(120), nullable=True, unique=True,
                             index=True)
    headline = db.Column(db.String(250), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    about_section = db.Column(db.Text, nullable=True)
    # Externally-hosted avatar URL (legacy / optional). Local uploads take
    # precedence via avatar_path below.
    avatar_url = db.Column(db.String(500), nullable=True)
    # Relative path (inside UPLOAD_FOLDER) of a locally-stored uploaded
    # avatar, e.g. "avatars/3-a1b2c3d4.jpg". Set by the dashboard upload form.
    avatar_path = db.Column(db.String(300), nullable=True)
    # Relative path of a paid-tier custom profile background image.
    background_path = db.Column(db.String(300), nullable=True)

    # Tiering & Monetization
    tier = db.Column(db.String(20), default='Free')  # Free, Expanded, Full, Custom
    tier_changed_at = db.Column(db.DateTime, nullable=True)
    # --- Stripe billing identifiers -------------------------------------
    # Populated by the checkout/webhook flow (see stripe_billing.py). The
    # local ``tier`` is kept in sync with the subscription status so all
    # existing tier-gated features (link limits, pinning, themes, featured
    # slots) work unchanged: active sub -> paid tier; canceled/incomplete ->
    # back to Free.
    stripe_customer_id = db.Column(db.String(64), nullable=True, index=True)
    stripe_subscription_id = db.Column(db.String(64), nullable=True)
    # Cached subscription status ('active', 'trialing', 'past_due',
    # 'canceled', ...) mirrored from Stripe webhooks for fast admin views.
    stripe_subscription_status = db.Column(db.String(32), nullable=True)
    # Unix timestamp (seconds) of the current period end, stored as int so
    # no timezone conversion is needed at write time.
    stripe_current_period_end = db.Column(db.BigInteger, nullable=True)

    # Profile extras (from Colab mockup spec)
    # Skills: short comma-separated list rendered as badges on the public
    # profile (e.g. "Python, Flask, UI/UX"). Normalized/capped in app.py.
    skills = db.Column(db.String(300), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    # Normalized country derived from ``location`` at save time (see
    # app.match_country). Two-letter ISO code for recognized countries
    # ("US", "VA"), otherwise a slug of the free-text place name so every
    # location still maps to exactly one bucket. Only ever used to power
    # the public /countries search — and only for users who opted in via
    # ``directory_visible`` (the "make my profile searchable" box).
    country_code = db.Column(db.String(3), nullable=True, index=True)
    country_name = db.Column(db.String(120), nullable=True)
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
    # Directory visibility preference (dashboard privacy toggle). Default
    # False = opted out: none of this user's links are listed on the public
    # /directory pages. Personal-website links are always excluded from the
    # directory regardless of this flag (see LinkSource.is_directory_excluded).
    directory_visible = db.Column(db.Boolean, default=False, nullable=False,
                                  server_default='0')
    # Set once a logged-in user passes the 18+ age gate (see /directory and
    # profile's /<handle>/confirm-age route). Remembers verification per account so the
    # gate is not re-shown on every device session; the session flag
    # ``age_ok`` remains the short-lived, cookie-based equivalent.
    age_verified = db.Column(db.Boolean, default=False, nullable=False,
                             server_default='0')
    # Featured-slot placement on the public directory pages. Paid users are
    # slotted at the top of /directory/<source> results (max N shown, chosen
    # at random — see Setting 'directory_featured_max'). Admins can pin a
    # free-tier account into the featured slots or suppress a paying account
    # without changing anyone's billing tier.
    is_featured = db.Column(db.Boolean, default=False, nullable=False,
                            server_default='0')
    # Marks rows created by the dev-only dummy-data seeder (see dev_seed.py
    # and Admin → Dev data). Never set for real signups; lets the seeder
    # wipe exactly what it created. Added to _ensure_schema() ALTERs too.
    is_demo = db.Column(db.Boolean, default=False, nullable=False,
                        server_default='0')

    created_at = db.Column(db.DateTime, default=lambda: datetime.now())

    def can_view_adult(self):
        """Whether this account may see 18+ content (directory cards, etc.).

        AC: a user must be logged in with at least a free account — no
        profile creation required — and verified as 18+. Registration now
        asserts "I am 18 or older" (see ``templates/register.html``), so new
        accounts are verified from signup onwards; legacy accounts flip the
        flag via the one-time age gate (/directory/age-gate,
        profile's /<handle>/confirm-age route). Admins bypass it so they can QA the
        catalogue.
        """
        return bool(self.is_admin or self.age_verified)

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

    @property
    def background_src(self):
        """Public URL for a locally uploaded profile background image."""
        return url_for_upload(self.background_path) if self.background_path else None


def url_for_upload(rel_path):
    """Public URL for a file inside UPLOAD_FOLDER (served via /uploads)."""
    from flask import url_for
    return url_for('uploaded_file', filename=rel_path)


class UserOAuthAccount(db.Model):
    """Third-party (social) login identities attached to a local user account.

    Kept in its own table so the standard email/password auth on ``users``
    is never modified or destroyed — OAuth data is strictly *appended*:
      * one row per (provider, provider account id) pair;
      * a user may link several providers at once (google + github + ...);
      * unlinking deletes only this row, never the user record.

    ``to_public_dict()`` exposes a safe view for templates/admin screens —
    tokens are deliberately excluded from it.
    """
    __tablename__ = 'user_oauth_accounts'

    # Providers supported by the auth epic (see oauth_login.OAUTH_PROVIDERS).
    PROVIDERS = ('google', 'facebook', 'microsoft', 'github')

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)

    # Lowercase provider key: 'google' | 'facebook' | 'microsoft' | 'github'
    provider = db.Column(db.String(20), nullable=False)
    # Stable unique id returned by the provider (sub / id / user_id).
    provider_user_id = db.Column(db.String(191), nullable=False)
    # Email reported by the provider (may be None if scope not granted).
    email = db.Column(db.String(255), nullable=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False,
                               server_default='0')
    # Display name / avatar reported by the provider at link time.
    name = db.Column(db.String(255), nullable=True)
    picture_url = db.Column(db.String(500), nullable=True)

    # Raw profile payload returned by the provider's userinfo endpoint
    # (JSON text) — handy for debugging and future fields without a schema
    # change.
    raw_profile = db.Column(db.Text, nullable=True)

    # OAuth tokens. Stored as JSON text ("{...}"). NOTE: the upstream plan
    # called for encryption at rest; until an app-level cipher is added,
    # treat the DB (and its backups) as secret material. Refresh tokens are
    # long-lived; access tokens expire quickly and are mostly informational.
    access_token = db.Column(db.Text, nullable=True)
    refresh_token = db.Column(db.Text, nullable=True)
    token_expires_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    last_used_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User',
                           backref=db.backref('oauth_accounts',
                                              lazy='dynamic',
                                              cascade='all, delete-orphan'))

    __table_args__ = (
        # A provider account can only ever be linked to one local user.
        db.UniqueConstraint('provider', 'provider_user_id',
                            name='uq_oauth_provider_uid'),
    )

    def to_public_dict(self):
        """Safe serialisation for templates / admin views (no tokens)."""
        return {
            'id': self.id,
            'provider': self.provider,
            'email': self.email,
            'name': self.name,
            'email_verified': bool(self.email_verified),
            'linked_at': (self.created_at.strftime('%Y-%m-%d')
                          if self.created_at else None),
            'last_used_at': (self.last_used_at.strftime('%Y-%m-%d %H:%M')
                             if self.last_used_at else None),
        }

    def store_tokens(self, token):
        """Persist the token dict returned by the provider's token endpoint."""
        import json as _json
        token = token or {}
        self.access_token = _json.dumps(token) if token else None
        expires_in = token.get('expires_in')
        if isinstance(expires_in, (int, float)):
            self.token_expires_at = datetime.now() + timedelta(
                seconds=int(expires_in))
        # Refresh token lives inside the response when scope asks for it.
        rt = token.get('refresh_token')
        if rt:
            self.refresh_token = rt

    def __repr__(self):
        return (f'<UserOAuthAccount {self.provider}:{self.provider_user_id} '
                f'user_id={self.user_id}>')


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

    # ---------------------------------------------------------------
    # Public directory eligibility
    # ---------------------------------------------------------------
    # A link may be listed on the /directory pages only when ALL of these
    # hold:
    #   1. the link itself is active (hidden links stay private),
    #   2. its owner opted in via the dashboard privacy toggle
    #      (``User.directory_visible``, default False), and
    #   3. the source is a real network profile -- personal websites are
    #      strictly excluded no matter what the toggle says.
    # ``is_adult`` (on LinkCategory / LinkSource) is layered on top of this
    # by the directory routes for tier-based display control.
    @property
    def is_directory_eligible(self):
        """True when this link may appear in the public directory."""
        if not self.is_active:
            return False
        owner = self.owner
        if owner is None or not owner.directory_visible:
            return False
        src = self.matched_source
        if src is None or src.is_deleted or not src.is_active:
            return False
        return not src.is_directory_excluded

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
    def placeholder_url(self):
        """Example URL hint for this link's source (network-specific shape).

        Networks call the same thing different names — GitHub wants a
        username, Steam a profile id, Discord an invite code — so when a
        source defines a {handle} profile pattern we show its real shape.
        """
        src = self.matched_source
        if src and src.profile_pattern:
            return src.profile_pattern.replace('{handle}', 'username')
        if src and src.url:
            return src.url
        return 'https://…'

    @property
    def placeholder_handle(self):
        """Example handle hint for this link's source ('@…' line)."""
        src = self.matched_source
        if not src:
            return '@handle (optional)'
        hints = {
            'GitHub': '@username',
            'Steam': '@steamid64 or /id/vanity',
            'Discord': 'server name or invite code',
            'Twitch': '@channelname',
            'X / Twitter': '@handle',
            'Threads': '@handle',
            'Instagram': '@handle',
            'TikTok': '@username',
            'YouTube': '@channelhandle',
            'Reddit': 'u/username',
            'SoundCloud': '/username',
            'Bandcamp': '/artistname',
            'Medium': '@username',
            'Substack': 'publication name',
            'Etsy': '/shopname',
            'Patreon': '/creatorname',
            'Ko-fi': '/username',
            'Buy Me a Coffee': '/username',
        }
        hint = hints.get(src.name)
        return f'@{hint}' if hint and not hint.startswith(('u/', '/', 'server')) \
            else (hint or '@handle (optional)')

    @property
    def safe_utm(self):
        """UTM string only if it looks like genuine UTM params."""
        u = (self.utm_params or '').strip()
        if u.startswith('?utm_') and len(u) <= 300 and '"' not in u \
                and '<' not in u and ' ' not in u:
            return u
        return ''

    # ------------------------------------------------------------------
    # Directory detail cards (/directory/<source>)
    # ------------------------------------------------------------------
    @property
    def network_profile_url(self):
        """The member's profile URL on the network, cleaned for display.

        Stored URLs sometimes carry tracking junk or a trailing slash; we
        normalise scheme/host case and append the link's own UTM params when
        present (they are the owner's chosen attribution).
        """
        from urllib.parse import urlsplit, urlunsplit
        raw = (self.url or '').strip()
        if not raw:
            return '#'
        if '://' not in raw:
            raw = 'https://' + raw
        try:
            parts = urlsplit(raw)
        except ValueError:
            return raw
        netloc = (parts.hostname or '').lower()
        if parts.port:
            netloc = f'{netloc}:{parts.port}'
        path = parts.path.rstrip('/') or '/'
        query = parts.query
        utm = self.safe_utm
        if utm:
            sep = '&' if query else ''
            query = f'{query}{sep}{utm.lstrip("?")}'
        return urlunsplit((parts.scheme.lower() or 'https', netloc, path,
                           query, parts.fragment))

    @property
    def network_handle(self):
        """Best-effort '@handle' for directory cards (subhandle or last path
        segment of the network profile URL)."""
        sh = (self.subhandle or '').strip()
        if sh:
            return sh if sh.startswith('@') else '@' + sh.lstrip('u/')
        from urllib.parse import urlsplit
        try:
            path = urlsplit(self.network_profile_url).path.strip('/')
        except ValueError:
            return ''
        return path.split('/')[-1] if path else ''


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

# Fallback category names used when the database has no LinkCategory rows
# yet (fresh test DB, migrations not run). The live source of truth is the
# link_categories table, managed via Admin -> Link Categories.
LINK_CATEGORIES = ['Gaming & Dev', 'Content', 'Social', 'Store', 'Portfolio',
                   'Music', 'Other']


class LinkCategory(db.Model):
    """Admin-managed grouping buckets for link sources (profile tabs).

    Sources reference a category *by name* (``LinkSource.category``) so the
    catalogue keeps working even before this table is seeded/migrated; the
    row here carries the presentation metadata (icon, brand colors) and the
    ordering used by public-profile tab grouping. Deleting a category does
    NOT cascade: sources in it fall back to the catch-all 'Other'.
    """
    __tablename__ = 'link_categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), unique=True, nullable=False)
    icon_code = db.Column(db.String(50), default='tag', nullable=False)  # bi-*
    bg_color = db.Column(db.String(9), default='#f8f9fa', nullable=False)
    text_color = db.Column(db.String(9), default='#212529', nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    # Display-control flag for the whole category (and its linked sources).
    # Free-tier users should not see adult content: categories marked here —
    # or sources carrying their own ``LinkSource.is_adult`` flag — are hidden
    # behind gating logic. Reserved for use in the next-phase tier features.
    is_adult = db.Column(db.Boolean, default=False, nullable=False,
                         server_default='0')
    is_deleted = db.Column(db.Boolean, default=False, nullable=False,
                           server_default='0')  # soft delete (admin CRUD)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    DEFAULT_ICON = 'tag'

    def __repr__(self):
        return f'<LinkCategory {self.name}>'

    @classmethod
    def ordered(cls):
        """Live categories, admin sort order first then alphabetical."""
        return (cls.query.filter_by(is_deleted=False)
                .order_by(cls.sort_order, cls.name).all())

    @classmethod
    def names(cls):
        """Ordered list of live category names, or the static fallback."""
        rows = cls.ordered()
        if rows:
            return [c.name for c in rows]
        return list(LINK_CATEGORIES)

    @classmethod
    def get_by_name(cls, name):
        if not name:
            return None
        return cls.query.filter_by(name=name.strip(),
                                   is_deleted=False).first()

    @classmethod
    def seed_defaults(cls):
        """Populate with the starter set on first run (idempotent)."""
        if cls.query.first() is not None:
            return
        for idx, (name, icon, bg, txt) in enumerate(DEFAULT_LINK_CATEGORIES):
            db.session.add(cls(name=name, icon_code=icon, bg_color=bg,
                               text_color=txt, sort_order=idx))
        db.session.commit()


DEFAULT_LINK_CATEGORIES = [
    # name, bootstrap icon (bi-* slug without prefix), bg, text
    ('Gaming & Dev', 'controller',   '#e7f1e7', '#1e4620'),
    ('Content',      'play-btn',     '#fdeaea', '#7a1f1f'),
    ('Social',       'people',       '#e8f0fe', '#1a4f8b'),
    ('Store',        'bag',          '#fff3e0', '#8a4b08'),
    ('Portfolio',    'briefcase',    '#ede7f6', '#4527a0'),
    ('Music',        'music-note',   '#e0f2f1', '#00695c'),
    ('Other',        'tag',          '#f8f9fa', '#212529'),
]

# Directory blurb length limits (words) enforced by
# ``LinkSource.validate_description()`` and documented for admins.
DESCRIPTION_MIN_WORDS = 20
DESCRIPTION_MAX_WORDS = 50

DEFAULT_LINK_SOURCES = [
    # name, domains (comma-sep), bg, text, border, bootstrap icon, category,
    # description (20-50 words, shown under the H1 on directory templates)
    ('GitHub',    'github.com,gitlab.com',            '#24292e', '#ffffff', '#24292e', 'github',        'Gaming & Dev',
     'GitHub is the world\'s largest home for open source code, where developers host repositories, review pull requests, and ship software together. Link your profile here to showcase the projects, contributions, and organisations that define your engineering work.'),
    ('Steam',     'store.steampowered.com,steamcommunity.com', '#1b2838', '#c7d5e0', '#171a21', 'steam', 'Gaming & Dev',
     'Steam is Valve\'s PC gaming storefront and social network, bundling a vast game library, community hubs, reviews, and trading cards into one client. Share your Steam profile to let visitors see the games you play and the badges you have earned.'),
    ('Xbox Live', 'xbox.com,xboxlive.com',            '#107c10', '#ffffff', '#0b5a0b', 'controller',    'Gaming & Dev',
     'Xbox Live connects console and PC players through gamertags, achievements, parties, and cloud saves across Microsoft\'s gaming ecosystem. A linked profile lets friends find your gamer score, favourite titles, and multiplayer history at a glance.'),
    ('PlayStation Network', 'playstation.com',        '#00439c', '#ffffff', '#002d6b', 'controller',    'Gaming & Dev',
     'PlayStation Network is Sony\'s online service for PlayStation consoles, hosting friend lists, trophies, plus subscriptions, and digital game libraries. Linking your PSN profile gives visitors a verified window onto the games you play and the trophies you collect.'),
    ('Discord',   'discord.gg,discord.com',           '#5865f2', '#ffffff', '#4752c4', 'discord',       'Social',
     'Discord is a voice, video, and text chat platform built around servers that communities run themselves, from gaming guilds to study groups and dev teams. Add your invite or profile link so people can join the conversations you are already part of.'),
    ('YouTube',   'youtube.com,youtu.be',             '#ff0000', '#ffffff', '#cc0000', 'youtube',       'Content',
     'YouTube is the dominant video platform for creators, educators, musicians, and broadcasters, reaching billions of viewers each month. Linking your channel lets audiences jump straight to your uploads, Shorts, live streams, and subscriber milestones without searching.'),
    ('Twitch',    'twitch.tv',                        '#9146ff', '#ffffff', '#772ce8', 'twitch',        'Content',
     'Twitch is Amazon\'s live-streaming network, best known for gaming broadcasts but also home to music, art, cooking, and just-chatting creators. A linked channel shows your followers count, streaming schedule, and clips so visitors know exactly when to catch you live.'),
    ('Vimeo',     'vimeo.com',                        '#1ab7ea', '#ffffff', '#1494bd', 'vimeo',         'Content',
     'Vimeo is a video hosting platform favoured by filmmakers, animators, and brands for its ad-free player, privacy controls, and high-quality playback standards. Link your account to present curated reels and portfolio pieces in a clean, professional setting.'),
    ('Medium',    'medium.com',                       '#12100e', '#ffffff', '#000000', 'medium',        'Content',
     'Medium is a publishing platform where independent writers, engineers, and analysts publish essays and serialized stories to large built-in audiences. Linking your profile highlights your claps, publications, and reading interests, giving readers a direct path to your latest articles.'),
    ('Substack',  'substack.com',                     '#ff6719', '#ffffff', '#d95511', 'envelope-paper', 'Content',
     'Substack lets journalists, authors, and specialists run paid or free email newsletters with a simple subscription model and portable audience. Sharing your publication link helps readers sign up directly and discover the other writers you recommend on the network.'),
    ('Spotify',   'spotify.com',                      '#1db954', '#0b2a14', '#169c46', 'spotify',       'Music',
     'Spotify is the leading music streaming service, hosting tens of millions of tracks, podcasts, and algorithmic playlists. Linking your artist page or public profile lets fans stream your releases instantly and follow your listening taste, playlists, and monthly listeners.'),
    ('Apple Music','music.apple.com',                 '#fa243c', '#ffffff', '#c91e31', 'apple-music',   'Music',
     'Apple Music combines lossless streaming, radio, and deep integration across Apple devices, with artist pages that surface discographies and verified profiles. Link it to send listeners straight to your album releases, singles, and curated playlists in their native app.'),
    ('SoundCloud','soundcloud.com',                   '#ff5500', '#ffffff', '#d64800', 'cloud-fill',    'Music',
     'SoundCloud is a creator-first audio platform where DJs, producers, and podcasters upload mixes and demos directly to listeners without label gatekeeping. Your linked profile showcases track counts, plays, and reposts, making it easy for fans to hear your newest experiments.'),
    ('Bandcamp',  'bandcamp.com',                     '#629aa9', '#ffffff', '#4f7d87', 'music-note-beamed', 'Music',
     'Bandcamp is an artist-friendly marketplace where musicians sell albums, merch, and direct-to-fan subscriptions while keeping most of the revenue. Linking your page lets supporters buy records, download discographies, and follow tour updates from one fan-funded storefront.'),
    ('X / Twitter','twitter.com,x.com',               '#000000', '#ffffff', '#333333', 'twitter-x',     'Social',
     'X, formerly Twitter, is the real-time public square for news, commentary, and niche communities built around short posts and reposts. Adding your handle lets visitors follow your takes, threads, and live event coverage wherever the conversation happens first.'),
    ('LinkedIn',  'linkedin.com',                     '#0a66c2', '#ffffff', '#085299', 'linkedin',      'Social',
     'LinkedIn is the professional network where careers, companies, and industry insights live side by side through profiles, endorsements, and articles. Link yours so recruiters, clients, and colleagues can verify your experience, recommendations, and current role in one click.'),
    ('Reddit',    'reddit.com',                       '#ff4500', '#ffffff', '#d63a00', 'reddit',        'Social',
     'Reddit is a vast network of topic-focused communities where members vote, discuss, and moderate discussions ranging from hobbies to research. Linking your account shows your post history, awards, and favourite subreddits, proving active participation in the communities you care about.'),
    ('Quora',     'quora.com',                        '#b92b27', '#ffffff', '#97231f', 'question-circle', 'Social',
     'Quora is a question-and-answer knowledge network where experts and enthusiasts write long-form answers that keep surfacing in search results. Sharing your profile highlights the topics you explain best, your answer view counts, and any credentials you have verified.'),
    ('Facebook',  'facebook.com',                     '#1877f2', '#ffffff', '#125ecc', 'facebook',      'Social',
     'Facebook remains one of the largest social networks for personal pages, interest Groups, local events, and business presence. Linking your public page or profile helps family, customers, and community members find the place where you actually post and organise.'),
    ('Instagram', 'instagram.com',                    '#c13584', '#ffffff', '#9a2a69', 'instagram',     'Social',
     'Instagram is a photo and short-video network built around feeds, Stories, Reels, and creator commerce tools. Linking your handle gives visitors instant access to your visual portfolio, follower community, and behind-the-scenes updates from wherever you are shooting.'),
    ('TikTok',    'tiktok.com',                       '#010101', '#ffffff', '#25f4ee', 'camera-reels',  'Social',
     'TikTok is the vertical short-video platform whose recommendation engine can turn unknown creators into global names overnight. Add your profile so fans can watch your latest clips, duets, and trending sounds, and follow the series that grow your audience week over week.'),
    ('Threads',   'threads.net',                      '#000000', '#ffffff', '#333333', 'at',            'Social',
     'Threads is Meta\'s text-forward social app, tightly connected to Instagram and designed for quick replies, conversations, and public updates. Linking it lets readers continue discussions started elsewhere and follow your day-to-day writing in a calmer, chronological feed.'),
    ('Patreon',   'patreon.com',                      '#f96854', '#ffffff', '#c94f3f', 'heart-fill',    'Store',
     'Patreon powers membership businesses: creators offer tiered subscriptions with bonus episodes, early access, and community perks. Linking your campaign shows how many patrons back your work and gives supporters the shortest possible path to a recurring contribution.'),
    ('Ko-fi',     'ko-fi.com',                        '#ff5e5b', '#ffffff', '#d94c49', 'cup-hot',       'Store',
     'Ko-fi lets artists, developers, and writers accept one-off tips, run small shops, and offer memberships without taking a cut of donations. Your Ko-fi page is a low-friction way for appreciative visitors to buy you a coffee and support ongoing projects.'),
    ('Buy Me a Coffee', 'buymeacoffee.com',           '#ffd43b', '#2b2b2b', '#e0b92f', 'cash-coin',     'Store',
     'Buy Me a Coffee provides a simple support button for creators, adding memberships, digital products, and comment widgets to any site. Linking your page turns casual readers into paying supporters with minimal friction and no complex store setup required.'),
    ('Etsy',      'etsy.com',                         '#f16521', '#ffffff', '#c95119', 'shop',          'Store',
     'Etsy is the global marketplace for handmade goods, vintage finds, and craft supplies, run largely by independent studio sellers. Linking your shop lets buyers browse listings, check reviews, and favourite your products without leaving the profile you are already viewing.'),
    ('Amazon Storefront', 'amazon.com,amzn.to',       '#ff9900', '#131921', '#e08700', 'bag',           'Store',
     'An Amazon Storefront curates the products a creator or business recommends into shoppable lists, wishlists, and brand pages. Linking yours lets visitors buy the gear you actually use while giving you affiliate credit for the referrals your content generates.'),
    ('Personal Website', '',                            '#f8f9fa', '#212529', '#dee2e6', 'globe2',      'Portfolio',
     'Your own domain is the one profile you fully control: a personal website hosts your portfolio, writing, case studies, and contact details without algorithm interference. Link it as the anchor of your footprint so every network points back to your home base.'),
    # 18+ by nature — flagged in ADULT_SOURCE_NAMES so it is seeded with
    # ``is_adult=True`` and hidden from guests / unverified accounts.
    ('AdultWorld',  'adultworld.example',               '#4d0a0a', '#ffffff', '#2f0606', 'shield-lock', 'Other',
     'AdultWorld is an example adults-only network used to exercise the age-gating rules of this directory. Access requires a logged-in account verified as eighteen or older, and listings from this source stay hidden from guests and unverified visitors entirely.'),
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
    home_url = db.Column(db.String(300), nullable=True)  # site home page
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
    # Short, admin-written blurb describing the network itself (not a user's
    # profile). Directory templates render it as the first element under the
    # H1; keep it between DESCRIPTION_MIN_WORDS and DESCRIPTION_MAX_WORDS.
    description = db.Column(db.String(500), nullable=True)
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

    # Sources that represent a personal site rather than a social-network
    # profile. They are strictly excluded from the public directory -- even
    # when the owner opted in with ``User.directory_visible`` -- so the
    # directory stays an index of networks instead of a link farm.
    NON_DIRECTORY_SOURCES = frozenset({'Personal Website'})

    @property
    def is_directory_excluded(self):
        """True when links from this source never appear in the directory."""
        return (self.name or '').strip() in self.NON_DIRECTORY_SOURCES

    # ---------------------------------------------------------------
    # Directory description ("Website Description" field)
    # ---------------------------------------------------------------
    @staticmethod
    def description_word_count(text):
        """Whitespace-delimited word count used by the 20-50 rule."""
        return len((text or '').split())

    @classmethod
    def validate_description(cls, text, label='Description'):
        """Return an error string when *text* breaks the blurb rules.

        The directory templates render this copy directly under the H1, so
        it must exist and stay within DESCRIPTION_MIN_WORDS..MAX_WORDS to
        keep card/page layouts consistent. Returns ``None`` when valid.
        """
        words = cls.description_word_count(text)
        if not words:
            return (f'{label} is required — write a '
                    f'{DESCRIPTION_MIN_WORDS}-{DESCRIPTION_MAX_WORDS} word '
                    f'description of the website for the directory pages.')
        if words < DESCRIPTION_MIN_WORDS:
            return (f'{label} is too short ({words} words). Write at least '
                    f'{DESCRIPTION_MIN_WORDS} words so the directory page '
                    f'has useful context under the heading.')
        if words > DESCRIPTION_MAX_WORDS:
            return (f'{label} is too long ({words} words). Keep it under '
                    f'{DESCRIPTION_MAX_WORDS} words so it fits the directory '
                    f'template.')
        if len(text) > 500:
            return f'{label} must be 500 characters or fewer.'
        return None

    @property
    def description_is_valid(self):
        """True when the stored description meets the length contract."""
        return self.validate_description(self.description) is None

    # ------------------------------------------------------------------
    # Adult catalogue bootstrap (see "User Object Addition & Age Gate")
    # ------------------------------------------------------------------
    # Networks whose content is 18+ by nature. Flagged on first run so the
    # directory gating rules have real data to enforce; admins can change
    # any of these per-source in Admin -> Link Sources, and flag whole
    # categories via Admin -> Link Categories (``LinkCategory.is_adult``).
    ADULT_SOURCE_NAMES = frozenset({'AdultWorld'})

    @classmethod
    def seed_adult_flags(cls):
        """Flag the known adult networks (idempotent, additive only)."""
        changed = False
        for src in cls.query.filter(cls.name.in_(cls.ADULT_SOURCE_NAMES)).all():
            if not src.is_adult:
                src.is_adult = True
                changed = True
        if changed:
            db.session.commit()

    # ---------------------------------------------------------------
    # Adult / 18+ display control
    # ---------------------------------------------------------------
    # A source counts as adult when it is flagged directly (``is_adult``,
    # admin-managed per source) OR when its LinkCategory is flagged
    # ``is_adult`` (see the previous story's category-level field). The
    # public /directory hides those cards from logged-out visitors and
    # from logged-in users who have not been verified as 18+.
    @property
    def is_adult_effective(self):
        """True when this source must be treated as adult content."""
        if self.is_adult:
            return True
        cat = LinkCategory.get_by_name(self.category)
        return bool(cat and cat.is_adult)

    @classmethod
    def visible_to(cls, include_adult):
        """Live sources for public listings; adult ones only if allowed."""
        out = []
        for src in cls.active():
            if src.is_adult_effective and not include_adult:
                continue
            out.append(src)
        return out

    @classmethod
    def directory_source_ids(cls, include_adult=False):
        """Ids of live sources that *are* allowed in the directory.

        Personal-website sources are always dropped; adult sources are only
        included when ``include_adult`` is true (i.e. the viewer has been
        verified as 18+).
        """
        return [r.id for r in cls.visible_to(include_adult)
                if not r.is_directory_excluded]

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
    def seed_descriptions(cls):
        """Backfill/populate the directory ``description`` field.

        Idempotent and additive-only: a source is written when it has no
        description yet, or when its current text is still an untouched
        copy of the seeded default (so admin edits are never stomped).
        Returns the number of rows changed.
        """
        defaults = {row[0]: row[7] for row in DEFAULT_LINK_SOURCES
                    if len(row) > 7 and row[7]}
        changed = 0
        for src in cls.query.all():
            text = defaults.get((src.name or '').strip())
            if not text:
                continue
            current = src.description or ''
            stale_default = any(current == d for d in defaults.values())
            if current and not stale_default:
                continue  # admin-authored copy wins
            src.description = text
            changed += 1
        if changed:
            db.session.commit()
        return changed

    @staticmethod
    def derive_home_url(example_url):
        """Home page (scheme://host/) for an example profile URL."""
        from urllib.parse import urlsplit
        parts = urlsplit((example_url or '').strip())
        if parts.scheme in ('http', 'https') and parts.netloc:
            return f'{parts.scheme}://{parts.netloc}/'
        return None

    @classmethod
    def seed_defaults(cls):
        """Populate the catalogue on first run (idempotent).

        Sources listed in ``ADULT_SOURCE_NAMES`` are created with
        ``is_adult=True``; on an existing database the flags are re-applied
        so a catalogue seeded before this field existed still enforces the
        18+ directory rules. The directory ``description`` blurbs are
        backfilled the same way (see :meth:`seed_descriptions`).
        """
        if cls.query.first() is not None:
            cls.seed_adult_flags()
            cls.seed_descriptions()
            return
        for idx, row in enumerate(DEFAULT_LINK_SOURCES):
            name, domains, bg, txt, brd, icon, cat = row[:7]
            description = row[7] if len(row) > 7 else None
            first_domain = (domains or '').split(',')[0].strip()
            url = f'https://{first_domain}/' if first_domain else None
            pattern = (f'https://{first_domain}/{{handle}}'
                       if first_domain else None)
            db.session.add(cls(name=name, domains=domains, bg_color=bg,
                               text_color=txt, border_color=brd,
                               icon_code=icon, category=cat,
                               description=description,
                               sort_order=idx, url=url,
                               profile_pattern=pattern,
                               is_adult=name in cls.ADULT_SOURCE_NAMES))
        db.session.commit()
        cls.seed_adult_flags()
        cls.seed_descriptions()


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
    # Review invitations (Story 2): automated "share your experience"
    # requests dispatched by an admin to one or many accounts. Delivered
    # like any other inbox message but tagged so they can be audited and
    # attributed back to the originating invitation record.
    'review_invite': {'label': 'Review invitation',       'default_severity': 'success'},
    # Direct replies to a testimonial author (Story 3): outreach sent from
    # inside a testimonial record while resolving negative feedback.
    'review_reply':  {'label': 'Review reply / outreach', 'default_severity': 'info'},
    'site_suggestion': {'label': 'Site suggestion',      'default_severity': 'info'},
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


# ==========================================
# Testimonials (admin-managed social proof)
# ==========================================

class Testimonial(db.Model):
    """A piece of user feedback shown as social proof on the marketing
    home page and managed from Admin -> Testimonials.

    Display rules baked into this story:
      * ``is_published`` is the master visibility switch — only published
        rows are ever rendered on the site.
      * ``exclude_from_home`` lets an admin keep a testimonial published
        (e.g. for other placements) while hiding it from the home page —
        the "specifically marked" exclusion of the homepage filtering rule.
      * ``is_incomplete`` marks drafts/placeholder quotes; the home page
        display logic automatically excludes them even when published.
      * ``profile_consent`` records the author's explicit consent to link
        their public profile. The clickable profile icon additionally
        requires an active paid subscription (tier in PAID_TIERS) — both
        conditions must hold (see ``profile_link_url`` helper in app.py).
    """
    __tablename__ = 'testimonials'

    id = db.Column(db.Integer, primary_key=True)
    author_name = db.Column(db.String(80), nullable=False)
    # Short role/context line under the quote, e.g. "Content Creator".
    author_role = db.Column(db.String(120), nullable=True)
    quote = db.Column(db.Text, nullable=False)
    rating = db.Column(db.Integer, nullable=False, default=5, server_default='5')

    # Optional association with a registered account — powers the profile
    # link icon and avatar. Nullable so manual/legacy testimonials work.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True,
                        index=True)
    # Explicit user consent to show a link to their public profile.
    profile_consent = db.Column(db.Boolean, default=False, nullable=False,
                                server_default='0')

    # Visibility / display-rule flags.
    is_published = db.Column(db.Boolean, default=False, nullable=False,
                             server_default='0')
    exclude_from_home = db.Column(db.Boolean, default=False, nullable=False,
                                  server_default='0')
    is_incomplete = db.Column(db.Boolean, default=False, nullable=False,
                              server_default='0')

    sort_order = db.Column(db.Integer, default=0, nullable=False,
                           server_default='0')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           index=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    # --- Moderation / review-resolution workflow (Story 3) -------------
    # 'pending'   — freshly submitted (e.g. via a review invitation), not
    #               yet reviewed by an admin; surfaces in the moderation
    #               queue together with flagged rows.
    # 'in_resolution' — negative/flagged feedback the admin is actively
    #               handling after direct outreach to the author.
    # 'resolved'  — issue addressed; eligible for normal publishing again.
    # 'dismissed' — reviewed and intentionally kept unpublished.
    REVIEW_STATUSES = ('pending', 'in_resolution', 'resolved', 'dismissed')
    resolution_status = db.Column(db.String(20), default='pending',
                                  nullable=False, server_default='pending',
                                  index=True)
    # Low ratings are auto-flagged so they land in the moderation queue
    # before ever reaching the home page.
    is_flagged = db.Column(db.Boolean, default=False, nullable=False,
                           server_default='0')
    # Internal admin note about the issue / outcome of the outreach.
    admin_note = db.Column(db.Text, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    # Set when the admin replies directly to the author from the record
    # page (Story 3 "Direct Reply Workflow").
    replied_at = db.Column(db.DateTime, nullable=True)
    replied_by_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                              nullable=True)
    # Attribution when the quote arrived through a review invitation.
    invitation_id = db.Column(db.Integer,
                              db.ForeignKey('review_invitations.id',
                                            use_alter=True,
                                            name='fk_testimonials_invitation_id'),
                              nullable=True, index=True)

    author = db.relationship('User', foreign_keys=[user_id])
    replied_by = db.relationship('User', foreign_keys=[replied_by_id])
    invitation = db.relationship('ReviewInvitation',
                                 foreign_keys=[invitation_id])

    @property
    def resolution_label(self):
        return self.RESOLUTION_LABELS.get(self.resolution_status,
                                          self.resolution_status or 'pending')

    RESOLUTION_LABELS = {
        'pending': 'Pending review',
        'in_resolution': 'In Resolution',
        'resolved': 'Resolved',
        'dismissed': 'Dismissed',
    }
    # Badge colour per status (bootstrap classes used in admin templates).
    RESOLUTION_BADGES = {
        'pending': 'secondary',
        'in_resolution': 'warning',
        'resolved': 'success',
        'dismissed': 'dark',
    }

    @classmethod
    def moderation_queue(cls):
        """Pending submissions and flagged reviews awaiting admin action."""
        return cls.query.filter(db.or_(
            cls.resolution_status == 'pending',
            cls.is_flagged.is_(True),
        )).order_by(cls.created_at.desc())

    @classmethod
    def home_display(cls):
        """Homepage display logic (single source of truth).

        Returns the query for testimonials currently eligible for the
        marketing home page: published, not specifically marked for
        exclusion, not incomplete — and never negative feedback that is
        still under moderation (flagged rows must first be resolved or
        dismissed). Also used for the live "published on display" counter
        in the admin section.
        """
        return cls.query.filter(
            cls.is_published.is_(True),
            cls.exclude_from_home.is_(False),
            cls.is_incomplete.is_(False),
            db.or_(cls.is_flagged.is_(False),
                   cls.resolution_status.in_(('resolved', 'dismissed'))),
        ).order_by(cls.sort_order, cls.created_at.desc())

    @classmethod
    def section_display(cls, section):
        """Return testimonials eligible to render in a content section."""
        query = cls.query.join(cls.content_sections).filter(
            ContentSection.id == section.id,
            ContentSection.is_active.is_(True),
            cls.is_published.is_(True),
            cls.is_incomplete.is_(False),
            db.or_(cls.is_flagged.is_(False),
                   cls.resolution_status.in_(('resolved', 'dismissed'))),
        )
        if section.slug == 'homepage-testimonials':
            query = query.filter(cls.exclude_from_home.is_(False))
        return query.order_by(cls.sort_order, cls.created_at.desc())

    def __repr__(self):
        return f'<Testimonial #{self.id} {self.author_name!r}>'


class TestimonialReply(db.Model):
    """An admin response and resolution-history entry for a testimonial."""
    __tablename__ = 'testimonial_replies'

    RESOLUTION_STATUSES = ('pending', 'in_resolution', 'resolved', 'dismissed')

    id = db.Column(db.Integer, primary_key=True)
    testimonial_id = db.Column(
        db.Integer, db.ForeignKey('testimonials.id', ondelete='CASCADE'),
        nullable=False, index=True)
    admin_user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                              nullable=True, index=True)
    message = db.Column(db.Text, nullable=False)
    dispatched_at = db.Column(db.DateTime, nullable=True, index=True)
    resolution_status = db.Column(db.String(20), nullable=False,
                                  default='pending',
                                  server_default='pending', index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           nullable=True)

    testimonial = db.relationship(
        'Testimonial',
        backref=db.backref('replies', cascade='all, delete-orphan',
                           order_by='TestimonialReply.created_at'))
    admin_user = db.relationship('User')

    def __repr__(self):
        return f'<TestimonialReply #{self.id} testimonial={self.testimonial_id}>'


testimonial_sections = db.Table(
    'testimonial_sections',
    db.Column('testimonial_id', db.Integer,
              db.ForeignKey('testimonials.id', ondelete='CASCADE'),
              primary_key=True),
    db.Column('content_section_id', db.Integer,
              db.ForeignKey('content_sections.id', ondelete='CASCADE'),
              primary_key=True),
)


class ContentSection(db.Model):
    """A site placement where assigned testimonials can be displayed."""
    __tablename__ = 'content_sections'

    DEFAULT_SECTIONS = (
        ('homepage-testimonials', 'Home page testimonials',
         'The home page social-proof area.'),
        ('homepage-features', 'Home page features',
         'The features section on the home page.'),
        ('homepage-demo', 'Home page interactive demo',
         'The interactive demo section on the home page.'),
        ('homepage-pricing', 'Home page pricing',
         'The pricing section on the home page.'),
        ('homepage-faq', 'Home page FAQ',
         'The frequently asked questions section on the home page.'),
        ('homepage-final-cta', 'Home page final call to action',
         'The closing call-to-action section on the home page.'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    slug = db.Column(db.String(100), nullable=False, unique=True, index=True)
    description = db.Column(db.String(250), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0,
                           server_default='0')
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default='1')

    testimonials = db.relationship(
        'Testimonial', secondary=testimonial_sections,
        backref=db.backref('content_sections', lazy='select'))

    @property
    def active_testimonial_count(self):
        return Testimonial.section_display(self).count()

    @classmethod
    def seed_defaults(cls):
        """Seed current site placements and preserve existing homepage cards."""
        existing = {section.slug for section in cls.query.all()}
        homepage_was_missing = 'homepage-testimonials' not in existing
        for position, (slug, name, description) in enumerate(
                cls.DEFAULT_SECTIONS):
            if slug not in existing:
                db.session.add(cls(
                    name=name,
                    slug=slug,
                    description=description,
                    sort_order=position,
                ))
        db.session.flush()
        section = cls.query.filter_by(slug='homepage-testimonials').one()
        if homepage_was_missing:
            for testimonial in Testimonial.home_display().all():
                testimonial.content_sections.append(section)
        db.session.commit()
        return section

    @property
    def active_faq_count(self):
        return len(Faq.for_section(self.slug))

    def __repr__(self):
        return f'<ContentSection {self.slug!r}>'


# ==========================================
# FAQs (admin-managed Q&A with categories, content-section placements and
# schema.org FAQPage markup for search engines / AI crawlers)
# ==========================================

faq_sections = db.Table(
    'faq_sections',
    db.Column('faq_id', db.Integer,
              db.ForeignKey('faqs.id', ondelete='CASCADE'),
              primary_key=True),
    db.Column('content_section_id', db.Integer,
              db.ForeignKey('content_sections.id', ondelete='CASCADE'),
              primary_key=True),
)

FAQ_QUESTION_MAX = 300
FAQ_ANSWER_MAX = 4000

# Placeholders usable in answer copy; filled from live settings so answers
# never go stale when an admin changes a limit.
FAQ_TOKENS = ('{free_max}', '{expanded_max}')


def slugify(text):
    """Lowercase hyphen slug ('Billing & Plans' -> 'billing-plans')."""
    return re.sub(r'[^a-z0-9]+', '-', (text or '').strip().lower()).strip('-')


class FaqCategory(db.Model):
    """Heading under which FAQs are grouped on the /faq page."""
    __tablename__ = 'faq_categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    slug = db.Column(db.String(100), nullable=False, unique=True, index=True)
    description = db.Column(db.String(250), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0,
                           server_default='0')
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default='1')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           nullable=False)

    faqs = db.relationship('Faq', back_populates='category',
                           order_by='Faq.sort_order, Faq.id')

    def __repr__(self):
        return f'<FaqCategory {self.slug!r}>'

    @classmethod
    def ordered(cls, active_only=False):
        q = cls.query
        if active_only:
            q = q.filter_by(is_active=True)
        return q.order_by(cls.sort_order, cls.name).all()

    @property
    def published_faqs(self):
        return [f for f in self.faqs if f.is_published]


class Faq(db.Model):
    """One question/answer pair.

    Visibility: ``is_published`` plus (for /faq) an active category. Sections
    placements (``content_sections``) decide where else the FAQ is rendered;
    assigning it to the ``homepage-faq`` section is what puts it on the home
    page accordion.
    """
    __tablename__ = 'faqs'

    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(FAQ_QUESTION_MAX), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    category_id = db.Column(db.Integer,
                            db.ForeignKey('faq_categories.id',
                                          ondelete='SET NULL'),
                            nullable=True, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0,
                           server_default='0')
    is_published = db.Column(db.Boolean, nullable=False, default=True,
                             server_default='1')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    category = db.relationship('FaqCategory', back_populates='faqs')
    content_sections = db.relationship(
        'ContentSection', secondary=faq_sections,
        backref=db.backref('faqs', lazy='select'))

    def __repr__(self):
        return f'<Faq {self.id} {self.question[:30]!r}>'

    @property
    def is_visible(self):
        """Published and not hidden by an inactive category."""
        return bool(self.is_published and
                    (self.category is None or self.category.is_active))

    @classmethod
    def public_query(cls):
        """Published FAQs whose category (if any) is active."""
        return (cls.query.outerjoin(FaqCategory)
                .filter(cls.is_published.is_(True),
                        db.or_(cls.category_id.is_(None),
                               FaqCategory.is_active.is_(True))))

    @classmethod
    def for_section(cls, slug):
        """Visible FAQs placed on the content section ``slug``, in order."""
        return (cls.public_query()
                .join(faq_sections, faq_sections.c.faq_id == cls.id)
                .join(ContentSection,
                      ContentSection.id == faq_sections.c.content_section_id)
                .filter(ContentSection.slug == slug,
                        ContentSection.is_active.is_(True))
                .order_by(cls.sort_order, cls.id).all())

    @classmethod
    def grouped_for_page(cls):
        """[(category_or_None, [faqs])] for the public /faq page."""
        faqs = cls.public_query().order_by(cls.sort_order, cls.id).all()
        buckets = {}
        for faq in faqs:
            buckets.setdefault(faq.category_id, []).append(faq)
        groups = [(c, buckets[c.id])
                  for c in FaqCategory.ordered(active_only=True)
                  if c.id in buckets]
        if None in buckets:
            groups.append((None, buckets[None]))
        return groups

    def rendered_answer(self):
        """Answer text with live-setting tokens filled in."""
        text = self.answer or ''
        if '{' in text:
            text = (text
                    .replace('{free_max}',
                             str(Setting.get_int('free_tier_max_links', 3)))
                    .replace('{expanded_max}',
                             str(Setting.get_int('expanded_tier_max_links',
                                                 10))))
        return text

    @staticmethod
    def jsonld(faqs):
        """schema.org FAQPage for exactly the FAQs rendered on the page."""
        if not faqs:
            return None
        return {
            '@context': 'https://schema.org',
            '@type': 'FAQPage',
            'mainEntity': [{
                '@type': 'Question',
                'name': f.question,
                'acceptedAnswer': {'@type': 'Answer',
                                   'text': f.rendered_answer()},
            } for f in faqs],
        }

    @classmethod
    def seed_defaults(cls):
        """Starter FAQs, shown on the home page FAQ section (idempotent:
        only runs while the faqs table is empty)."""
        if cls.query.first() is not None:
            return
        section = ContentSection.query.filter_by(
            slug='homepage-faq').first()
        cats = {}
        for idx, (name, desc) in enumerate(DEFAULT_FAQ_CATEGORIES):
            cat = FaqCategory.query.filter_by(slug=slugify(name)).first()
            if cat is None:
                cat = FaqCategory(name=name, slug=slugify(name),
                                  description=desc, sort_order=idx)
                db.session.add(cat)
            cats[name] = cat
        db.session.flush()
        for idx, (cat_name, question, answer) in enumerate(DEFAULT_FAQS):
            faq = cls(question=question, answer=answer,
                      category_id=cats[cat_name].id, sort_order=idx)
            if section is not None:
                faq.content_sections.append(section)
            db.session.add(faq)
        db.session.commit()


DEFAULT_FAQ_CATEGORIES = [
    ('Getting Started', 'Set up your profile and share your link.'),
    ('Plans & Billing', 'Plans, limits, and custom domains.'),
    ('Search & AI Visibility',
     'How profiles are discovered by search engines and AI assistants.'),
    ('Links & Analytics', 'Managing links, tracking, and link health.'),
]

DEFAULT_FAQS = [
    # category, question, answer (plain text; blank line = new paragraph)
    ('Plans & Billing', 'Can I use my own domain name?',
     'Yes — custom domains are available on the Custom plan. Point your '
    'domain\'s DNS at us and we\'ll serve your profile page with SSL '
    'automatically. On lower plans you get a clean canonical URL like '
    '/yourname.'),
    ('Links & Analytics',
     'How do I add my Google Analytics or Meta tracking pixel?',
     'On the Full and Custom plans, open the Dashboard → Integrations '
     'section, paste your GA4 Measurement ID, Google Tag Manager '
     'container, or Meta Pixel ID, and save. Pixels load only on your '
     'public profile pages and never track logged-in editing sessions.'),
    ('Plans & Billing',
     'Is there a limit on how many links I can add on paid tiers?',
     'The Free plan includes {free_max} active links and Expanded includes '
     '{expanded_max}. The Full and Custom plans are unlimited — publish as '
     'many links, embeds, and sections as you like. Hidden (inactive) '
     'links never count against your limit.'),
    ('Search & AI Visibility',
     'How does the platform help my profile show up in Google search '
     'results?',
     'Every public profile ships with semantic HTML, Open Graph meta tags, '
     'and JSON-LD schema.org Person markup generated from your display '
     'name, headline, bio, and links. That means richer search snippets — '
     'and AI assistants like ChatGPT and Perplexity can accurately cite '
     'who you are and where you publish.'),
    ('Links & Analytics', 'What happens if one of my links breaks?',
     'Our automated link checker visits your links on a schedule. If a '
     'destination returns a 404 or errors out, you receive a heads-up in '
     'your inbox (throttled so you never get spammed about the same '
     'issue) so you can fix or hide it fast.'),
]


# ==========================================
# Review invitations (Story 2: admin dispatches
# "share your experience" requests to users)
# ==========================================

class ReviewInvitation(db.Model):
    """An invitation an admin dispatched to one user asking for a review.

    One row per recipient even when the admin selects a batch of accounts,
    so each link's status can be tracked individually. ``token`` is the
    unguessable key baked into the submission URL; it expires after
    ``expires_days`` days. The invited user opens the dedicated form at
    /review/<token> and their quote lands straight in the ``testimonials``
    table (isolated persistence — reviews never mix with any other table).
    """
    __tablename__ = 'review_invitations'

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    # Dispatch batch id: every recipient of one admin action shares it,
    # which powers the "batch sent to N users" activity log entries.
    batch_id = db.Column(db.String(32), index=True, nullable=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'),
                           nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(),
                           index=True)
    expires_days = db.Column(db.Integer, default=30, nullable=False,
                             server_default='30')
    # 'pending' -> 'submitted' (testimonial recorded) or 'expired'.
    status = db.Column(db.String(20), default='pending', nullable=False,
                       server_default='pending', index=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    testimonial_id = db.Column(db.Integer, db.ForeignKey('testimonials.id'),
                               nullable=True)

    user = db.relationship('User', foreign_keys=[user_id])
    message = db.relationship('Message', foreign_keys=[message_id])
    testimonial = db.relationship('Testimonial',
                                  foreign_keys=[testimonial_id],
                                  backref='originating_invitations')

    @property
    def is_expired(self):
        if self.status != 'pending':
            return False
        days = self.expires_days if self.expires_days and self.expires_days > 0 else 30
        return (datetime.now() - self.created_at).days >= days

    @property
    def is_usable(self):
        return self.status == 'pending' and not self.is_expired

    def __repr__(self):
        return f'<ReviewInvitation #{self.id} user={self.user_id} '\
               f'[{self.status}]>'


class ReviewInviteTemplate(db.Model):
    """Editable copy used when dispatching review invitations.

    Seeded with a sensible default on first use; admins tweak the wording
    from the Testimonials admin screen. ``{{placeholders}}`` are filled
    at send time (see REVIEW_INVITE_PLACEHOLDERS in app.py).
    """
    __tablename__ = 'review_invite_templates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(db.DateTime, nullable=True)

    DEFAULT_NAME = 'default'
    DEFAULT_SUBJECT = 'How has your experience been, {{display_name}}?'
    DEFAULT_BODY = (
        'Hi {{display_name}},\n\n'
        'Thank you for being part of {{site_name}}! We would love to hear '
        'about your experience — it takes less than two minutes.\n\n'
        'Share your review here: {{invite_url}}\n\n'
        'Your feedback may be featured on our home page as social proof. '
        'With your consent we can link your review to your public profile.\n\n'
        'Warm regards,\nThe {{site_name}} team'
    )

    @classmethod
    def get_default(cls):
        """Return the default template row, seeding it on first use."""
        tpl = cls.query.filter_by(name=cls.DEFAULT_NAME).first()
        if tpl is None:
            tpl = cls(name=cls.DEFAULT_NAME, subject=cls.DEFAULT_SUBJECT,
                      body=cls.DEFAULT_BODY)
            db.session.add(tpl)
            db.session.flush()
        return tpl

    def render(self, context=None):
        """Fill known placeholders; leave unknown {{tokens}} intact."""
        from string import Template
        ctx = {k: v for k, v in (context or {}).items()}
        return (Template(self.subject).safe_substitute(ctx),
                Template(self.body).safe_substitute(ctx))

    def __repr__(self):
        return f'<ReviewInviteTemplate {self.name!r}>'
