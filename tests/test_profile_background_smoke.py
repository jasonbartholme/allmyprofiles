"""Smoke test for paid-tier custom profile backgrounds."""
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ['FLASK_ENV'] = 'testing'
_tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmpdb.close()
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmpdb.name
_tmpuploads = tempfile.TemporaryDirectory()
os.environ['UPLOAD_FOLDER'] = _tmpuploads.name

from app import create_app  # noqa: E402
from models import db, Setting, User  # noqa: E402

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


def image_upload():
    image = Image.new('RGB', (16, 9), (50, 100, 150))
    data = BytesIO()
    image.save(data, format='PNG')
    data.seek(0)
    return data


with app.app_context():
    db.create_all()
    Setting.seed_defaults()
    paid = User(username='paidbackground', email='paid@example.com',
                display_name='Paid Background', tier='Expanded')
    paid.set_password('password123')
    free = User(username='freebackground', email='free@example.com',
                display_name='Free Background', tier='Free')
    free.set_password('password123')
    db.session.add_all([paid, free])
    db.session.commit()

client = app.test_client()

print('\n== Paid background upload ==')
client.post('/login', data={'email': 'paid@example.com',
                            'password': 'password123'})
dashboard = client.get('/dashboard').get_data(as_text=True)
check('paid dashboard shows background upload field',
      'name="background_file"' in dashboard)
r = client.post('/dashboard', data={
    'form_action': 'background-upload',
    'background_file': (image_upload(), 'background.png'),
}, content_type='multipart/form-data')
check('paid background upload redirects', r.status_code == 302)
with app.app_context():
    paid = User.query.filter_by(username='paidbackground').first()
    background_path = paid.background_path
    check('paid background is stored in backgrounds directory',
          bool(background_path and background_path.startswith('backgrounds/')))

unsupported = client.post('/dashboard', data={
    'form_action': 'background-upload',
    'background_file': (BytesIO(b'not an image'), 'background.txt'),
}, content_type='multipart/form-data')
check('unsupported background type is rejected', unsupported.status_code == 302)
with app.app_context():
    check('unsupported type does not replace background',
          User.query.filter_by(username='paidbackground').first().background_path
          == background_path)
    Setting.set_value('max_upload_size_kb', '1')
    db.session.commit()

oversized = client.post('/dashboard', data={
    'form_action': 'background-upload',
    'background_file': (BytesIO(b'x' * 2048), 'background.png'),
}, content_type='multipart/form-data')
check('oversized background is rejected', oversized.status_code == 302)
with app.app_context():
    check('oversized upload does not replace background',
          User.query.filter_by(username='paidbackground').first().background_path
          == background_path)

public_profile = client.get('/u/paidbackground').get_data(as_text=True)
check('paid public profile renders custom background',
      background_path in public_profile and 'background-image:' in public_profile)

print('\n== Free-tier restriction ==')
client.get('/logout')
client.post('/login', data={'email': 'free@example.com',
                            'password': 'password123'})
dashboard = client.get('/dashboard').get_data(as_text=True)
check('free dashboard hides background upload field',
      'name="background_file"' not in dashboard)
r = client.post('/dashboard', data={
    'form_action': 'background-upload',
    'background_file': (image_upload(), 'background.png'),
}, content_type='multipart/form-data')
check('free background upload is rejected', r.status_code == 302)
with app.app_context():
    free = User.query.filter_by(username='freebackground').first()
    check('free background is not stored', free.background_path is None)
    free.background_path = background_path
    db.session.commit()

free_profile = client.get('/u/freebackground').get_data(as_text=True)
check('free public profile does not render stored background',
      background_path not in free_profile)

print(f'\nRESULT: {passed} passed, {failed} failed')

_tmpuploads.cleanup()
os.unlink(_tmpdb.name)
sys.exit(1 if failed else 0)
