"""Smoke test: FAQ CRUD, categories, section placement and schema.org markup."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ['FLASK_ENV'] = 'testing'
_tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmpdb.close()
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmpdb.name

from app import create_app                      # noqa: E402
from models import (db, User, Setting, ContentSection, Faq,  # noqa: E402
                    FaqCategory)

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


def jsonld(html):
    out = []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',
                         html, re.S):
        out.append(json.loads(m.group(1)))
    return [d for d in out if d.get('@type') == 'FAQPage']


with app.app_context():
    db.create_all()
    Setting.seed_defaults()
    ContentSection.seed_defaults()
    Faq.seed_defaults()
    admin = User(username='rootadmin', email='a@x.io', display_name='Root',
                 is_admin=True)
    admin.set_password('password123')
    db.session.add(admin)
    db.session.commit()
    check('seeded FAQs', Faq.query.count() == 5)

client = app.test_client()

print('\n== Public ==')
r = client.get('/faq')
body = r.get_data(as_text=True)
check('/faq 200', r.status_code == 200)
check('/faq has H1', '<h1' in body and 'Frequently Asked Questions' in body)
check('/faq indexable', 'noindex' not in body)
ld = jsonld(body)
check('/faq FAQPage JSON-LD with 5 questions',
      len(ld) == 1 and len(ld[0]['mainEntity']) == 5)
check('/faq breadcrumb', 'Home' in body and 'Breadcrumb' in body)
r = client.get('/')
body = r.get_data(as_text=True)
check('home shows seeded FAQ + faqAccordion',
      'Can I use my own domain name?' in body and 'faqAccordion' in body)
check('home FAQ JSON-LD', len(jsonld(body)) == 1)
check('sitemap lists /faq', '/faq' in client.get('/sitemap-static.xml').get_data(as_text=True))

print('\n== Admin gate ==')
r = client.get('/admin/faqs')
check('anon admin faqs redirects', r.status_code in (301, 302))
client.post('/login', data={'email': 'a@x.io', 'password': 'password123'})
for url in ('/admin/faqs', '/admin/faqs/new', '/admin/faq-categories',
            '/admin/faq-categories/new', '/admin/faqs/1/edit',
            '/admin/faq-categories/1/edit'):
    check(f'admin GET {url}', client.get(url).status_code == 200)

print('\n== CRUD ==')
r = client.post('/admin/faq-categories/new',
                data={'name': 'Security', 'sort_order': '9',
                      'is_active': 'on'})
check('category created', r.status_code == 302)
r = client.post('/admin/faq-categories/new', data={'name': 'security'})
check('duplicate category rejected', r.status_code == 200)
with app.app_context():
    cat = FaqCategory.query.filter_by(slug='security').first()
    cat_id = cat.id
    custom = ContentSection.query.filter_by(slug='homepage-pricing').first()
    custom_id = custom.id
r = client.post('/admin/faqs/new', data={'question': '', 'answer': 'x'})
check('empty question rejected', r.status_code == 200)
r = client.post('/admin/faqs/new',
                data={'question': 'Is my data safe?',
                      'answer': 'Yes.\n\nWe encrypt it.',
                      'category_id': str(cat_id), 'sort_order': '1',
                      'is_published': 'on', 'section_ids': [str(custom_id)]})
check('faq created', r.status_code == 302)
with app.app_context():
    f = Faq.query.filter_by(question='Is my data safe?').first()
    fid = f.id
    check('section assigned', [s.slug for s in f.content_sections]
          == ['homepage-pricing'])
check('shown on /faq under category',
      'Is my data safe?' in client.get('/faq').get_data(as_text=True))
with app.app_context():
    check('for_section returns it',
          [x.id for x in Faq.for_section('homepage-pricing')] == [fid])
    check('not on homepage-faq',
          fid not in [x.id for x in Faq.for_section('homepage-faq')])
anon = app.test_client()
body = anon.get('/').get_data(as_text=True)
check('not on home page', 'Is my data safe?' not in body)

r = client.post(f'/admin/faqs/{fid}/toggle')
check('toggle ok', r.status_code == 302)
check('unpublished hidden from /faq',
      'Is my data safe?' not in client.get('/faq').get_data(as_text=True))
client.post(f'/admin/faqs/{fid}/toggle')

with app.app_context():
    home_id = ContentSection.query.filter_by(slug='homepage-faq').first().id
r = client.post(f'/admin/faqs/{fid}/edit',
            data={'question': 'Is my data safe?', 'answer': 'Yes.',
                  'category_id': str(cat_id), 'sort_order': '1',
                  'is_published': 'on', 'section_ids': [str(home_id)]})
body = anon.get('/').get_data(as_text=True)
check('assigning homepage-faq puts it on home',
      'Is my data safe?' in body)
check('home JSON-LD includes it',
      any(e['name'] == 'Is my data safe?'
          for e in jsonld(body)[0]['mainEntity']))

r = client.post(f'/admin/faq-categories/{cat_id}/delete')
check('category deleted', r.status_code == 302)
with app.app_context():
    f = db.session.get(Faq, fid)
    check('faq kept, uncategorized', f is not None and f.category_id is None)
r = client.post(f'/admin/faqs/{fid}/delete')
check('faq deleted', r.status_code == 302)
with app.app_context():
    check('faq gone', db.session.get(Faq, fid) is None)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
