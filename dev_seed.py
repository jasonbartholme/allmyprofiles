"""Development-only dummy data seeder.

Powers the admin "Dev data" page (/admin/dev-data): one click loads a
realistic-looking dataset — users with country locations, directory-listable
links with click counts, paid tiers, an adult category + source, demo inbox
messages and audit-log entries — so every public surface (directory pages,
country search, dashboards) has something to show without hand-entering data.

Safety rails (defence in depth; the UI also hides this everywhere):
  * both entry points assert ``FLASK_ENV == 'development'`` at call time,
    so hitting the route on a production install raises instead of seeding;
  * every seeded user is tagged ``is_demo=True`` (see models.User), which
    lets "Wipe demo data" delete exactly what this module created;
  * usernames are deterministic (demo_001..demo_NNN), so re-running the
    same batch size skips existing rows instead of duplicating them.

Nothing here talks to Stripe or any external service.
"""

import os
import random

from sqlalchemy import func
from werkzeug.security import generate_password_hash

from models import db, User, Link, LinkSource, LinkCategory, Message, Activity


# ---------------------------------------------------------------------------
# Static fake-content pools
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    'Ada', 'Bo', 'Cleo', 'Dario', 'Elif', 'Farid', 'Greta', 'Henri', 'Ines',
    'Jonas', 'Kira', 'Lior', 'Mina', 'Noor', 'Oskar', 'Petra', 'Quinn',
    'Rosa', 'Sven', 'Talia', 'Ulrich', 'Vera', 'Wren', 'Xenia', 'Yusuf',
    'Zoe', 'Amara', 'Bruno', 'Chiara', 'Dmitri',
]

LAST_NAMES = [
    'Alden', 'Berg', 'Costa', 'Dahl', 'Egan', 'Fontaine', 'Grady', 'Haddad',
    'Iversen', 'Jensen', 'Kessler', 'Lindqvist', 'Moreau', 'Novak', 'Okafor',
    'Petrov', 'Rossi', 'Salgado', 'Tanaka', 'Ueda', 'Vargas', 'Weiss',
    'Xu', 'Yilmaz', 'Zielinski',
]

HEADLINES = [
    'Backend engineer, coffee first',
    'Indie game dev & pixel artist',
    'Product designer who codes',
    'Data nerd / open-source maintainer',
    'Full-stack tinkerer',
    'Writer covering developer tools',
    'Photographer chasing golden hour',
    'Music producer & synth collector',
    'DevRel speaker and workshop lead',
    'Building small useful SaaS things',
]

BIOS = [
    'I build things for the web and write about the process. Currently '
    'exploring local-first software.',
    'Ten years in, still amazed by databases. Reachable best via email.',
    'Making tutorials that respect your time. New video every other week.',
    'Designer turned developer. I care about typography and load times.',
    'Open source contributor, conference speaker, occasional blogger.',
    'I photograph cities at dawn and write code at night.',
]

SKILLS = [
    'Python, Flask, PostgreSQL', 'Figma, CSS, Design systems',
    'Unity, C#, Shaders', 'Node.js, TypeScript, AWS',
    'Writing, SEO, Developer marketing', 'Rust, WebAssembly',
    'Photoshop, Lightroom', 'Ableton, Sound design',
]

# Locations deliberately mix plain countries, city+country strings, the
# Vatican City edge case from the country-search story, and one unrecognized
# place ("Springfield") so both real ISO buckets and slug fallbacks exist.
LOCATIONS = [
    'Canada', 'Germany', 'Japan', 'United States', 'Brazil', 'Australia',
    'Norway', 'India', 'France', 'Netherlands', 'Spain', 'Mexico',
    'Ireland', 'South Korea', 'Sweden', 'Italy', 'Vatican City',
    'Berlin, Germany', 'Tokyo, Japan', 'Lisbon, Portugal',
    'São Paulo, Brazil', 'Springfield',
]

DEMO_PASSWORD = 'demo-passw0rd!'
DEMO_PREFIX = 'demo_'
DEMO_MESSAGE_PREFIX = 'demo-msg-'

# Sources we seed links for: real networks only (personal websites never
# qualify for the directory, so they would just be noise).
POOL_SOURCE_NAMES = ['GitHub', 'Twitter', 'Instagram', 'YouTube', 'LinkedIn',
                     'Mastodon', 'Bluesky', 'Twitch', 'Threads', 'TikTok',
                     'Medium', 'Patreon', 'Ko-fi']

ADULT_CATEGORY_NAME = 'Adult Demo'
ADULT_SOURCE_NAME = 'Demo Adult Network'


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _assert_dev():
    env = os.environ.get('FLASK_ENV', 'development')
    if env != 'development':
        raise RuntimeError(
            'The dummy-data seeder is development-only '
            f'(FLASK_ENV={env!r}). Remove it before deploying.')


def _source_pool():
    """Active, non-deleted, directory-eligible sources from the catalogue."""
    rows = (LinkSource.query
            .filter_by(is_deleted=False, is_active=True).all())
    pool = [s for s in rows if not s.is_directory_excluded]
    wanted = {n.lower(): n for n in POOL_SOURCE_NAMES}
    ordered = []
    for low, name in wanted.items():
        for s in pool:
            if s.name.lower() == low:
                ordered.append(s)
                break
    return ordered or pool[:8]


def _seed_adult_catalogue():
    """Idempotently create an adult LinkCategory + LinkSource.

    Gives the age-gate flows (guest vs logged-in vs 18+ verified) actual
    content to hide/show in the dev environment.
    """
    cat = LinkCategory.query.filter_by(name=ADULT_CATEGORY_NAME).first()
    if cat is None:
        cat = LinkCategory(name=ADULT_CATEGORY_NAME, icon_code='shield-lock',
                           bg_color='#4d0a0a', text_color='#ffffff',
                           is_adult=True)
        db.session.add(cat)
    src = LinkSource.query.filter_by(name=ADULT_SOURCE_NAME).first()
    if src is None:
        src = LinkSource(name=ADULT_SOURCE_NAME,
                         domains='demo-adult.example',
                         url='https://demo-adult.example',
                         profile_pattern='https://demo-adult.example/{handle}',
                         bg_color='#4d0a0a', text_color='#ffffff',
                         border_color='#2f0606', icon_code='shield-lock',
                         category='Other', is_adult=True)
        db.session.add(src)
    db.session.flush()
    return cat, src


def _link_url(source, handle):
    if source.profile_pattern and '{handle}' in source.profile_pattern:
        return source.profile_pattern.format(handle=handle)
    base = (source.domains or '').split(',')[0].strip()
    host = base or (source.name.lower().replace(' ', '') + '.example')
    return f'https://{host}/{handle}'


def _make_links(user, sources, rng, max_links=4):
    """Give one demo user 1-4 active links with plausible click counts."""
    n = rng.randint(1, min(max_links, len(sources)))
    for i, src in enumerate(rng.sample(sources, n)):
        handle = f'{user.username.lstrip("demo_").replace("_", "")}{rng.randint(10, 99)}'
        db.session.add(Link(
            user_id=user.id,
            title=f'{src.name} @{handle}',
            url=_link_url(src, handle),
            icon_code=src.icon_code or 'link-45deg',
            position=i,
            is_active=True,
            click_count=rng.randint(0, 480),
            source_id=src.id))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def seed_status():
    """Snapshot of the current demo dataset for the admin page."""
    demo_user_ids = [u.id for u in User.query.filter_by(is_demo=True).all()]
    links = (db.session.query(func.count(Link.id))
             .filter(Link.user_id.in_(demo_user_ids)).scalar() or 0) \
        if demo_user_ids else 0
    opted_in = (User.query.filter_by(is_demo=True, directory_visible=True)
                .count())
    countries = (db.session.query(func.count(func.distinct(User.country_code)))
                 .filter(User.is_demo.is_(True),
                         User.country_code.isnot(None),
                         User.country_code != '')
                 .scalar() or 0)
    return {'users': len(demo_user_ids), 'links': links,
            'opted_in': opted_in, 'countries': countries,
            'password': DEMO_PASSWORD}


def seed_dummy_data(count=25, seed=None):
    """Create ``count`` demo users plus supporting catalogue entries.

    Deterministic per username slot: re-running with the same count skips
    rows that already exist. Returns a summary dict. Requires development
    mode (see ``_assert_dev``).
    """
    _assert_dev()
    rng = random.Random(seed)
    summary = {'created': 0, 'skipped': 0, 'links': 0, 'messages': 0,
               'adult_users': 0}

    _seed_adult_catalogue()
    sources = _source_pool()
    if not sources:
        raise RuntimeError('Seed the Link Source catalogue before loading '
                           'demo data (it ships with defaults; check the DB).')
    adult_src = (LinkSource.query
                 .filter_by(name=ADULT_SOURCE_NAME, is_deleted=False).first())

    # Mix in a couple of adult-oriented demo profiles so the 18+ gate has
    # something to hide/show (~15% of the batch).
    adult_share = max(1, round(count * 0.15))

    firsts = FIRST_NAMES[:]
    rng.shuffle(firsts)

    for i in range(count):
        username = f'{DEMO_PREFIX}{i + 1:03d}'
        if User.query.filter_by(username=username).first():
            summary['skipped'] += 1
            continue
        location = LOCATIONS[i % len(LOCATIONS)]
        tier = rng.choices(['Free', 'Expanded', 'Full'], weights=[7, 2, 1])[0]
        wants_adult = i < adult_share
        user = User(
            username=username,
            email=f'{username}@example.com',
            password_hash=generate_password_hash(DEMO_PASSWORD),
            display_name=f'{rng.choice(firsts)} {rng.choice(LAST_NAMES)}',
            headline=rng.choice(HEADLINES),
            bio=rng.choice(BIOS),
            skills=rng.choice(SKILLS),
            tier=tier,
            is_admin=False,
            theme='light',
            # Most demo profiles opt into the directory/searchable listing.
            directory_visible=rng.random() < 0.8,
            # Registration asserts 18+, so most demo accounts are verified;
            # leave a few unverified to exercise the age-gate flow.
            age_verified=rng.random() < 0.75,
            is_adult_oriented=wants_adult,
            is_featured=(tier == 'Full' and rng.random() < 0.5),
            is_demo=True,
        )
        # Same helper the signup/profile routes use -> country buckets.
        from app import apply_country_fields
        apply_country_fields(user, location)
        db.session.add(user)
        db.session.flush()  # need user.id for links/messages
        _make_links(user, sources, rng)
        if wants_adult and adult_src is not None:
            handle = f'{username.lstrip("demo_")}adult'
            db.session.add(Link(
                user_id=user.id, title=f'{adult_src.name} @{handle}',
                url=_link_url(adult_src, handle),
                icon_code=adult_src.icon_code, position=9, is_active=True,
                click_count=rng.randint(0, 250), source_id=adult_src.id))
            summary['adult_users'] += 1
        summary['created'] += 1

    db.session.flush()
    ids = [u.id for u in User.query.filter_by(is_demo=True).all()]
    summary['links'] = (db.session.query(func.count(Link.id))
                        .filter(Link.user_id.in_(ids)).scalar() or 0) \
        if ids else 0

    # A few demo inbox messages from the oldest admin, so messaging views
    # have content. Deduped by key => idempotent across runs.
    admin = (User.query.filter_by(is_admin=True)
             .order_by(User.id.asc()).first())
    if admin and ids:
        newest = (User.query.filter_by(is_demo=True)
                  .order_by(User.id.desc()).limit(3).all())
        for u in newest:
            key = f'{DEMO_MESSAGE_PREFIX}{u.id}'
            if Message.query.filter_by(dedupe_key=key).first():
                continue
            db.session.add(Message(
                subject='Welcome to the demo dataset',
                body=('This account was generated by Admin → Dev data. '
                      'Use the Wipe demo data button to remove every '
                      'seeded profile, link and message.'),
                category='note', severity='info',
                sender_id=admin.id, source='dev_seed', dedupe_key=key))
            summary['messages'] += 1

    for u in (User.query.filter_by(is_demo=True)
              .order_by(User.id.desc()).limit(min(5, count)).all()):
        Activity.record('dev_seed', user=u,
                        detail=f'seeded @{u.username} ({u.tier})')

    db.session.commit()
    return summary


def wipe_dummy_data():
    """Delete everything tagged is_demo (links, messages, audit entries).

    Removes only rows this seeder created — real accounts are untouched.
    Demo catalogue entries (adult category/source) are soft-deleted so the
    next seed can reuse their names.
    """
    _assert_dev()
    users = User.query.filter_by(is_demo=True).all()
    ids = [u.id for u in users]
    links = msgs = acts = 0
    if ids:
        links = (db.session.query(func.count(Link.id))
                 .filter(Link.user_id.in_(ids)).scalar() or 0)
        Link.query.filter(Link.user_id.in_(ids)) \
            .delete(synchronize_session=False)
        keys = [f'{DEMO_MESSAGE_PREFIX}{uid}' for uid in ids]
        msgs = (Message.query.filter(Message.dedupe_key.in_(keys))
                .delete(synchronize_session=False))
    acts = (Activity.query.filter(Activity.kind == 'dev_seed')
            .delete(synchronize_session=False))
    for u in users:
        db.session.delete(u)

    for name in (ADULT_SOURCE_NAME,):
        s = LinkSource.query.filter_by(name=name).first()
        if s:
            s.is_deleted = True
            db.session.flush()
    c = LinkCategory.query.filter_by(name=ADULT_CATEGORY_NAME).first()
    if c:
        c.is_deleted = True

    db.session.commit()
    return {'users': len(ids), 'links': links, 'messages': msgs,
            'activities': acts}
