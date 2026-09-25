"""Periodic link checker for AllMyProfiles.

Checks every *active* user link with an HTTP HEAD (falling back to GET),
records the result in `link_check_results`, and — when links are broken —
sends the owner a "Broken link report" inbox message.

Anti-spam rules (all configurable from the admin Site settings page):
  * A user is only notified about a given still-broken link set once per
    `link_check_notify_days` days (default 7).
  * When a link recovers, its notification clock resets, so a *new* outage
    later will notify again immediately.

Usage (cron example — daily at 03:15):
    15 3 * * * cd /path/to/app && FLASK_ENV=production python check_links.py

Options:
    --dry-run   Check links and print findings, but send no messages.
    --limit N   Only check the first N links (useful for large installs).
Exit codes: 0 = ok, 1 = errors occurred while checking.
"""

import sys
import argparse
from datetime import datetime, timedelta

import requests

from app import create_app, notify_broken_links
from models import db, Link, Setting, LinkCheckResult

HEADERS = {
    'User-Agent': 'AllMyProfilesLinkChecker/1.0 (+contact site admin)',
    'Accept': '*/*',
}
TIMEOUT = 10  # seconds


def check_url(url):
    """Return (is_broken, http_status). Treats >=400 or unreachable as broken."""
    try:
        resp = requests.head(url, headers=HEADERS, timeout=TIMEOUT,
                             allow_redirects=True)
        if resp.status_code in (403, 405, 501):
            # Some servers reject HEAD; retry with a small GET.
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                                allow_redirects=True, stream=True)
            resp.close()
        return resp.status_code >= 400, resp.status_code
    except requests.RequestException:
        return True, None


def run(dry_run=False, limit=None):
    app = create_app()
    errors = 0
    now = datetime.now()

    with app.app_context():
        # Setting lookups need the Flask app context (SQLAlchemy session),
        # so this must happen *inside* the context block, not before it.
        cooldown_days = Setting.get_int('link_check_notify_days', 7)

        query = Link.query.filter(Link.is_active.is_(True))
        links = query.order_by(Link.id).limit(limit).all() if limit \
            else query.order_by(Link.id).all()

        print(f'{now:%Y-%m-%d %H:%M} — checking {len(links)} active link(s)'
              + (' [DRY RUN]' if dry_run else ''))

        broken_by_user = {}
        for link in links:
            broken, status = check_url(link.url)
            res = LinkCheckResult.query.filter_by(link_id=link.id).first()
            if res is None:
                res = LinkCheckResult(link_id=link.id)
                db.session.add(res)

            if broken:
                errors += 1
                res.is_broken = True
                res.http_status = status
                res.checked_at = now
                if res.broken_since is None:
                    res.broken_since = now
                broken_by_user.setdefault(link.user_id, []).append(res)
                print(f'  ✗ [{status or "ERR"}] {link.url}')
            else:
                was_broken = res.is_broken
                res.is_broken = False
                res.http_status = status
                res.checked_at = now
                if was_broken:
                    # Recovered: reset clocks so a future outage notifies
                    # immediately.
                    res.broken_since = None
                    res.last_notified_at = None
                    print(f'  ✓ recovered: {link.url}')
            db.session.commit()

        sent = 0
        if not dry_run:
            from models import User
            for user_id, results in broken_by_user.items():
                user = db.session.get(User, user_id)
                if user is None:
                    continue
                msg = notify_broken_links(user, results,
                                          days_between_notifications=cooldown_days,
                                          now=now)
                if msg:
                    sent += 1
                    print(f'  → notified @{user.username}: '
                          f'{len(results)} broken link(s)')
            db.session.commit()

        print(f'Done. {errors} broken, {sent} notification(s) sent, '
              f'cooldown {cooldown_days} day(s).')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='report findings without sending messages')
    parser.add_argument('--limit', type=int, default=None,
                        help='max number of links to check')
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, limit=args.limit))
