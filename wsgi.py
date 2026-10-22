"""WSGI entrypoint for production hosts (e.g. PythonAnywhere).

Point the web app's WSGI configuration file at this module, e.g.:

    # /home/<user>/allmyprofiles/wsgi.py  (PythonAnywhere WSGI file)
    import os, sys
    path = '/home/jbartholme/allmyprofiles'
    if path not in sys.path:
        sys.path.insert(0, path)

    from wsgi import application          # <-- this module provides it

IMPORTANT — environment variables set as plain module-level names below do
NOT become OS environment variables. Anything the app reads through
``os.environ`` (config.py, the FMD flags in app.py) must be assigned into
``os.environ`` explicitly, as done here.
"""

import os
import sys

# Add the project directory to sys.path when this doc is exec'd from
# outside the repo (PythonAnywhere runs the WSGI file from ~/ ).
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# ----------------------------------------------------------------------
# Production configuration
# ----------------------------------------------------------------------
# Selects ProductionConfig in config.py (PostgreSQL, DEBUG off).
os.environ.setdefault('FLASK_ENV', 'production')

# Database connection string. The password contains a literal '!', which
# is percent-encoded as %21 so SQLAlchemy parses the URL unambiguously
# (it decodes back to 'AnnyongHaseyo20!'). Keep these credentials out of
# git if this file ever gains secrets you don't want committed; ideally
# move them to PythonAnywhere's "Web > Environment variables" section and
# delete the assignments here.
os.environ.setdefault(
    'DATABASE_URL',
    'postgresql+psycopg://super:AnnyongHaseyo20%21'
    '@super.postgres.pythonanywhere.com:5432/allmyprofiles',
)

# Session-signing key. REQUIRED in production — without this the app falls
# back to config.py's 'default-dev-key-change-in-production', which lets
# anyone forge session cookies (i.e. log in as any user, including admins).
# Generate once:  python -c "import secrets; print(secrets.token_urlsafe(48))"
# and paste the result below (or export SECRET_KEY in the web app UI).
os.environ.setdefault('SECRET_KEY', 'CHANGE_ME-generate-a-token_urlsafe-48-string')

# Flask-MonitoringDashboard: per-profile traffic analytics (/analytics for
# users, admin-only unfiltered charts at /fmd). Off unless enabled here or
# in the host's env. Metrics DB defaults to sqlite:///flask_monitoringdashboard.db
# relative to the app's instance/ folder — fine on PythonAnywhere.
os.environ.setdefault('FMD_ENABLED', '1')

from app import create_app  # noqa: E402  (must come after env setup)

application = create_app(os.environ.get('FLASK_ENV', 'production'))
