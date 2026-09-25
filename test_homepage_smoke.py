"""End-to-end smoke test for the new public home page (specs 1-7)."""
import os
import sys
import tempfile

os.environ['FLASK_ENV'] = 'testing'
_tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmpdb.close()
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmpdb.name

from app import create_app            # noqa: E402
from models import db, User, Link, Setting  # noqa: E402

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

    # Seed data: one paid user w/ avatar url + links, one free user w/ links
    u1 = User(username='paidstar', email='p@x.io', display_name='Paid Star',
              tier='Full', avatar_url='https://example.com/a.png')
    u1.set_password('password123')
    db.session.add(u1)
    u2 = User(username='freeuser', email='f@x.io', display_name='Free User',
              tier='Free')
    u2.set_password('password123')
    db.session.add(u2)
    db.session.flush()
    for i in range(3):
        db.session.add(Link(user_id=u1.id, title=f'L{i}',
                            url=f'https://a{i}.com', position=i,
                            click_count=10 * (i + 1)))
        db.session.add(Link(user_id=u2.id, title=f'F{i}',
                            url=f'https://f{i}.com', position=i,
                            click_count=5))
    db.session.commit()

client = app.test_client()

print('\n== Hero / claim box ==')
r = client.get('/')
body = r.get_data(as_text=True)
check('GET / -> 200', r.status_code == 200)
check('H1 value proposition present',
      'One Link for All Your' in body)
check('Subheadline present', 'unify your digital footprint' in body)
check('Claim box input present', 'id="claimInput"' in body)
check('Claim CTA button present', 'Claim My Link' in body)
check('Phone mockup present', 'phone-frame' in body)

print('\n== Availability API ==')
r = client.get('/api/check-username?username=virginhandle')
j = r.get_json()
check('fresh name available', j and j['available'] is True)
r = client.get('/api/check-username?username=paidstar')
j = r.get_json()
check('taken name rejected', j and j['available'] is False and 'taken' in j['message'])
r = client.get('/api/check-username?username=admin')
j = r.get_json()
check('reserved name rejected', j and j['available'] is False and 'reserved' in j['message'])
r = client.get('/api/check-username?username=%3Cscript%3E')
j = r.get_json()
check('invalid chars rejected', j and j['available'] is False)
# register form prefills from ?username=
r = client.get('/register?username=myhand le')
check('register prefill lowercases/strips', 'value="myhandle"' in r.get_data(as_text=True))
# reserved username blocked at registration POST
r = client.post('/register', data={'username': 'pricing', 'email': 'z@z.io',
                                   'password': 'password123'})
check('register rejects reserved username',
      r.status_code == 400 and 'reserved' in r.get_data(as_text=True))

print('\n== Social proof ==')
body = client.get('/').get_data(as_text=True)
check('profiles counter rendered', 'data-count="2"' in body)
check('links counter rendered (6 total)', 'data-count="6"' in body)
check('clicks counter rendered (75, no thousands sep.)', 'data-count="75"' in body)
check('featured showcase includes paid user', '@paidstar' in body)
check('testimonials section present', 'Loved by creators' in body)

print('\n== Features / pricing / demo / FAQ ==')
for needle, label in [('SEO &amp; AI search optimization', 'SEO/AI differentiator'),
                      ('Built-in traffic intelligence', 'traffic intelligence'),
                      ('Modular customization', 'modular customization'),
                      ('Simple, transparent pricing', 'pricing heading'),
                      ('billing-toggle', 'monthly/annual toggle'),
                      ('Most popular', 'popular badge'),
                      ('demoStack', 'live sandbox demo'),
                      ('faqAccordion', 'FAQ accordion'),
                      ('Can I use my own domain name?', 'FAQ question 1'),
                      ('Ready to unify your online presence?', 'footer CTA banner')]:
    check(label, needle in body)
check('Free plan copy uses admin setting (3)', 'Up to 3 links' in body)

with app.app_context():
    Setting.set_value('free_tier_max_links', '5')
    db.session.commit()
body = client.get('/').get_data(as_text=True)
check('Free plan copy tracks live setting change', 'Up to 5 links' in body)

print('\n== Footer nav ==')
for path, needle in [('/help', 'Help Center'), ('/terms', 'Terms of Service'),
                     ('/privacy', 'Privacy Policy'), ('/contact', 'Contact us')]:
    r = client.get(path)
    check(f'{path} -> 200 with content',
          r.status_code == 200 and needle in r.get_data(as_text=True))
r = client.post('/contact', data={'name': 'Ada', 'email': 'ada@x.io',
                                  'message': 'Hello there, need help please'})
check('contact POST -> redirect', r.status_code == 302)
r = client.post('/contact', data={'name': 'Ada', 'email': 'bad',
                                  'message': 'short'})
check('contact invalid -> 400', r.status_code == 400)

print('\n== Auth redirect on home ==')
client.post('/login', data={'email': 'p@x.io', 'password': 'password123'})
r = client.get('/')
check('logged-in user redirected to dashboard',
      r.status_code == 302 and r.headers.get('Location', '').endswith('/dashboard'))
client.get('/logout')

print('\n== Reserved-name alias no longer 500s ==')
r = client.get('/u/paidstar')
check('canonical profile route works', r.status_code == 200)
r = client.get('/pricing')
check('/pricing is 404 (reserved), not someone\u2019s profile', r.status_code == 404)

os.unlink(_tmpdb.name)
print(f'\nRESULT: {passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
