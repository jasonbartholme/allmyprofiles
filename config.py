"""
Application configuration.

Development -> SQLite (zero setup, file-based).
Production  -> PostgreSQL (via DATABASE_URL environment variable).

Set FLASK_ENV=production (or use the ProductionConfig class directly on
your host) and export DATABASE_URL, e.g.:

    export DATABASE_URL=postgresql://user:password@localhost:5432/allmyprofiles

Note: Heroku-style "postgres://" URLs are normalized to "postgresql://".
"""

import os


class Config:
    """Base configuration shared by all environments."""

    SECRET_KEY = os.environ.get('SECRET_KEY', 'default-dev-key-change-in-production')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- User uploads (avatars and other images) ---------------------------
    # Stored on disk under UPLOAD_FOLDER, served read-only via /uploads/<path>.
    # The folder is git-ignored (see .gitignore: uploads/) — back it up
    # separately in production (or switch to S3/GCS storage).
    UPLOAD_FOLDER = os.environ.get(
        'UPLOAD_FOLDER', os.path.join(os.getcwd(), 'uploads'))
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # hard 5 MB request-size ceiling

    # --- Stripe (subscriptions & payments) --------------------------------
    # Credentials come from the Stripe dashboard (Developers -> API keys).
    # Leave them unset in development and the billing features degrade
    # gracefully: upgrade buttons show a "billing not configured" notice
    # instead of crashing, and the admin SaaS overview shows guidance.
    #   STRIPE_SECRET_KEY      : sk_test_... / sk_live_...  (secret key)
    #   STRIPE_WEBHOOK_SECRET  : whsec_...  (from `stripe listen` or the
    #                            dashboard Webhooks page -> signing secret)
    #   STRIPE_PRICE_EXPANDED  : price_... monthly price for the Expanded tier
    #   STRIPE_PRICE_FULL      : price_... monthly price for the Full tier
    # The price IDs can also be stored/edited as site settings
    # (stripe_price_expanded / stripe_price_full), which take precedence —
    # see stripe_billing.PRICE_SETTING_KEYS.
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
    STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    STRIPE_PRICE_EXPANDED = os.environ.get('STRIPE_PRICE_EXPANDED', '')
    STRIPE_PRICE_FULL = os.environ.get('STRIPE_PRICE_FULL', '')


class DevelopmentConfig(Config):
    """SQLite for local development."""

    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DEV_DATABASE_URL', 'sqlite:///app.db'
    )


class ProductionConfig(Config):
    """PostgreSQL for production."""

    DEBUG = False

    def __init__(self):
        super().__init__()
        database_url = os.environ.get(
            'DATABASE_URL',
            'postgresql://username:password@localhost:5432/allmyprofiles',
        )
        # Some platforms (e.g. older Heroku buildpacks) hand out "postgres://"
        # which SQLAlchemy < 2.x does not recognize.
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        self.SQLALCHEMY_DATABASE_URI = database_url


class TestingConfig(Config):
    """In-memory SQLite for automated tests."""

    TESTING = True
    # Shared-cache in-memory DB so every connection (and every test-client
    # request) sees the same database; a bare sqlite:///:memory: URI gives
    # each connection its own empty DB. Individual test scripts may still
    # override with DATABASE_URL (e.g. a temp file).
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///file:test_db?mode=memory&cache=shared&uri=true')
    WTF_CSRF_ENABLED = False


config_by_name = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}
