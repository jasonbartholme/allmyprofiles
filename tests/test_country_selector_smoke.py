"""Smoke test for the canonical profile country selector."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ['FLASK_ENV'] = 'testing'
_tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmpdb.close()
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmpdb.name

from app import create_app, source_slug  # noqa: E402
from models import db, Link, LinkCategory, LinkSource, Setting, User  # noqa: E402

app = create_app('testing')
passed = failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f'  PASS  {name}')
    else:
        failed += 1
        print(f'  FAIL  {name}')


with app.app_context():
    db.create_all()
    Setting.seed_defaults()
    LinkCategory.seed_defaults()
    LinkSource.seed_defaults()
    source = LinkSource.query.filter_by(name='Instagram').first()
    user = User(username='countryuser', email='country@example.com',
                display_name='Country User', directory_visible=True,
                headline='Photographer')
    user.set_password('password123')
    db.session.add(user)
    db.session.flush()
    db.session.add(Link(user_id=user.id, source_id=source.id,
                        title='Instagram',
                        url='https://instagram.com/countryuser',
                        is_active=True))
    db.session.commit()
    source_slug_value = source_slug(source.name)

client = app.test_client()
client.post('/login', data={'email': 'country@example.com',
                            'password': 'password123'})

dashboard = client.get('/dashboard').get_data(as_text=True)
check('dashboard shows the country selector',
      'for="country_code">Country</label>' in dashboard)
check('country selector includes canonical countries',
      'value="US"' in dashboard and 'United States' in dashboard)

client.post('/dashboard', data={
    'form_action': 'profile',
    'display_name': 'Country User',
    'headline': 'Photographer',
    'country_code': 'CA',
    'directory_visible': 'on',
})
with app.app_context():
    user = User.query.filter_by(username='countryuser').first()
    check('country selection stores canonical values',
          (user.country_code, user.country_name, user.location)
          == ('CA', 'Canada', 'Canada'))

profile = client.get('/u/countryuser').get_data(as_text=True)
check('public profile displays the country label',
      'Canada' in profile and 'addressCountry' in profile)

directory = client.get(f'/directory/{source_slug_value}').get_data(as_text=True)
check('directory card displays the country flag',
      'title="Canada"' in directory and '🇨🇦' in directory)

client.post('/dashboard', data={
    'form_action': 'profile',
    'display_name': 'Country User',
    'headline': 'Photographer',
    'country_code': 'invalid',
    'directory_visible': 'on',
})
with app.app_context():
    user = User.query.filter_by(username='countryuser').first()
    check('invalid country input does not overwrite selection',
          user.country_code == 'CA')

print(f'\nRESULT: {passed} passed, {failed} failed')

os.unlink(_tmpdb.name)
sys.exit(1 if failed else 0)
