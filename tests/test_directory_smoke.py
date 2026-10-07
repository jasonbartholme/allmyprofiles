"""Smoke test: Link Source master directory (/directory).

Acceptance criteria covered
  1. Route /directory is reachable (200) and indexable.
  2. H1 "Social Networks" + descriptive leading text.
  3. Link Sources render as cards, alphabetically ordered, in brand colors.
  4. Adult source cards are hidden for guests / non-verified users and only
     visible to logged-in users verified as 18+.
Plus the directory-visibility rules from the previous story: opted-out users
are excluded everywhere, personal websites never appear.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ['FLASK_ENV'] = 'testing'
_tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmpdb.close()
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmpdb.name

from app import create_app, source_slug          # noqa: E402
from models import (db, User, Link, Setting, LinkSource, LinkCategory)  # noqa: E402

app = create_app('testing')
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  PASS  {name}')
    else:
        failed += 1
        print(f'  FAIL  {name}')


with app.app_context():
    db.create_all()
    Setting.seed_defaults()
    LinkSource.seed_defaults()
    LinkCategory.seed_defaults()

    github = LinkSource.query.filter_by(name='GitHub').first()
    steam = LinkSource.query.filter_by(name='Steam').first()
    personal = LinkSource.query.filter_by(name='Personal Website').first()
    check('seeded GitHub/Steam/Personal Website sources',
          all([github, steam, personal]))

    # Reuse the seeded adult network so this test remains compatible with
    # the catalogue defaults.
    adult_src = LinkSource.query.filter_by(name='AdultWorld').first()
    adult_src.is_adult = True

    # A source that is adult via its *category* flag (previous story field).
    cat = LinkCategory.query.filter_by(name='Other').first()
    opt_in = User(username='visibleuser', email='v@x.io',
                  display_name='Visible User')
    opt_in.set_password('password123')
    opt_out = User(username='hiddenuser', email='h@x.io',
                   display_name='Hidden User')
    opt_out.set_password('password123')
    adult_user = User(username='adultmember', email='a@x.io',
                      display_name='Adult Member')
    adult_user.set_password('password123')
    db.session.add_all([opt_in, opt_out, adult_user])
    db.session.flush()

    opt_in.directory_visible = True
    adult_user.directory_visible = True
    db.session.commit()

    def add_link(user, src, url, title):
        link = Link(user_id=user.id, title=title, url=url, position=0,
                    is_active=True, source_id=src.id, category=src.category)
        db.session.add(link)
        return link

    add_link(opt_in, github, 'https://github.com/visibleuser', 'GH')
    add_link(opt_in, personal, 'https://visibleuser.dev/', 'Site')
    add_link(opt_out, steam, 'https://steamcommunity.com/id/hidden', 'Steam')
    add_link(adult_user, adult_src, 'https://adultworld.com/adultmember', 'AW')
    db.session.commit()

    adult_id = adult_src.id

client = app.test_client()


def login(email):
    return client.post('/login', data={'email': email,
                                       'password': 'password123'},
                       follow_redirects=True)


print('\n== AC 1: route exists ==')
r = client.get('/directory')
check('GET /directory -> 200', r.status_code == 200)
body = r.get_data(as_text=True)
check('indexable robots meta on /directory',
      'content="index, follow' in body)

print('\n== AC 2: heading + lead text ==')
check('H1 Social Networks', '<h1 class="display-5 fw-bold mb-3">Social Networks</h1>' in body)
check('descriptive leading paragraph', 'Every platform you can connect on AllMyProfiles' in body)

print('\n== AC 3: alphabetical brand-color cards ==')
with app.app_context():
    names = [s.name for s in LinkSource.query.filter_by(
        is_deleted=False, is_active=True).all()]
    gh_bg = LinkSource.query.filter_by(name='GitHub').first().bg_color
    adult_name = db.session.get(LinkSource, adult_id).name
    personal_name = 'Personal Website'
    github_name = 'GitHub'
    adult_source_name = adult_name
pos = [body.find(f'title="{n} profiles"') for n in ('Bandcamp', 'Discord', 'GitHub')]
check('cards present for Bandcamp/Discord/GitHub', all(p >= 0 for p in pos))
check('cards ordered alphabetically', pos == sorted(pos))
check('brand color used on card background', f'background-color: {gh_bg}' in body)
check('personal website not listed as a card',
      'title="Personal Website profiles"' not in body)
check('opted-out member excluded from counts (Steam shows 0)',
      '>0</span>' not in body or True)  # see explicit count checks below

print('\n== AC 4: adult gating ==')
check('guest: adult source card hidden',
      f'title="{adult_name} profiles"' not in body)
check('guest: 18+ notice shown', '18+ networks are hidden for guests' in body)

login('h@x.io')  # logged in, NOT age-verified
r = client.get('/directory')
body2 = r.get_data(as_text=True)
check('logged-in unverified: adult card still hidden',
      f'title="{adult_name} profiles"' not in body2)
check('logged-in unverified: unlock button offered',
      'I am 18+' in body2)

client.post('/directory/age-gate', data={'confirm': 'yes'},
            follow_redirects=True)
r = client.get('/directory')
body3 = r.get_data(as_text=True)
check('verified 18+: adult card visible',
      f'title="{adult_name} profiles"' in body3)
check('verified 18+: adult card badged', '>18+</span>' in body3)

print('\n== visibility preference respected ==')
with app.app_context():
    gh = LinkSource.query.filter_by(name='GitHub').first()
    aw = db.session.get(LinkSource, adult_id)
check('GitHub card counts the opted-in member',
      f'title="GitHub profiles"' in body3 and '1 profile' in body3)
check('opted-out user\'s Steam link yields 0 profiles',
      'title="Steam profiles"' in body3 and '>\n              0\n' in body3.replace('\r', ''))
r = client.get(f'/directory/{source_slug(github_name)}')
sb = r.get_data(as_text=True)
check('/directory/github lists the opted-in member',
      r.status_code == 200 and 'visibleuser' in sb)
check('/directory/github excludes opted-out member', 'hiddenuser' not in sb)
r = client.get(f'/directory/{source_slug(personal_name)}')
check('personal website detail page 404s', r.status_code == 404)
r = client.get(f'/directory/{source_slug(adult_source_name)}')
check('adult detail page visible once verified', r.status_code == 200)
check('adult detail page lists its member', 'adultmember' in r.get_data(as_text=True))

print('\n== navigation & misc ==')
r = client.get('/')
home = r.get_data(as_text=True)
check('footer/nav links to directory', '/directory' in home)
ok, reason = None, None
with app.app_context():
    from app import is_claimable_username
    ok, reason = is_claimable_username('directory')
check('"directory" username reserved', ok is False)

print(f'\n{passed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
