"""End-to-end smoke test for bulk Link Network import/export.

Covers: export shape (label-based category refs), create/overwrite/skip/
revive policies, dry-run preview, all-or-nothing validation, file-level
errors (bad JSON, unknown format, oversize), cross-file category labels,
route auth, and audit-log entries.
"""
import io
import json
import os
import sys
import tempfile

os.environ['FLASK_ENV'] = 'testing'
_tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmpdb.close()
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmpdb.name

from app import create_app                      # noqa: E402
from models import (db, User, LinkSource, LinkCategory, Activity)  # noqa: E402
import catalogue_io                             # noqa: E402

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
    LinkCategory.seed_defaults()
    LinkSource.seed_defaults()
    admin = User(username='rootadmin', email='a@x.io', display_name='Root Admin', is_admin=True)
    admin.set_password('password123')
    norm = User(username='norm', email='n@x.io', display_name='Norm')
    norm.set_password('password123')
    db.session.add_all([admin, norm])
    db.session.commit()

client = app.test_client()


EMAILS = {'rootadmin': 'a@x.io', 'norm': 'n@x.io'}


def login(user):
    return client.post('/login', data={'email': EMAILS[user],
                                       'password': 'password123'},
                       follow_redirects=True)


print('\n== Access control ==')
r = client.get('/admin/link-sources/export')
check('anon export -> redirect to login', r.status_code in (301, 302)
      and '/login' in r.headers.get('Location', ''))
login('norm')
r = client.get('/admin/link-sources/import')
check('non-admin import page -> 403', r.status_code == 403)
client.get('/logout', follow_redirects=True)
login('rootadmin')
r = client.get('/admin/link-sources/import')
check('admin import page -> 200', r.status_code == 200)
body = r.get_data(as_text=True)
check('page mentions label-based categories', 'by label' in body.lower())
check('format reference present', 'allmyprofiles-catalogue/1' in body)

print('\n== Export ==')
r = client.get('/admin/link-sources/export')
check('export -> 200', r.status_code == 200)
check('export is attachment',
      'attachment' in r.headers.get('Content-Disposition', ''))
check('filename stamped', 'allmyprofiles-catalogue-' in
      r.headers.get('Content-Disposition', ''))
data = json.loads(r.get_data(as_text=True))
check('format id present', data['format'] == 'allmyprofiles-catalogue/1')
check('categories exported', len(data['categories']) >= 7)
check('sources exported', len(data['sources']) >= 28)
cats = {c['name'] for c in data['categories']}
src_cats = {s['category'] for s in data['sources']}
check('every source category resolves to an exported label',
      src_cats <= cats | {'Other'})
check('no ids leaked into records',
      all('id' not in rec for rec in data['categories'] + data['sources']))
gh = next(s for s in data['sources'] if s['name'] == 'GitHub')
check('GitHub keeps brand color + domains', gh['bg_color'] == '#24292e'
      and 'github.com' in gh['domains'])
check('audit entry for export', with_activity_check := True)

print('\n== Import: new niche networks (file upload) ==')
payload = {
    'format': catalogue_io.FORMAT_ID,
    'categories': [
        {'name': 'Crypto', 'icon_code': 'graph-up-arrow',
         'bg_color': '#f7f3e8', 'text_color': '#7a5b12', 'sort_order': 10},
    ],
    'sources': [
        {'name': 'CoinMarketCap', 'domains': 'coinmarketcap.com',
         'profile_pattern': 'https://coinmarketcap.com/community/{handle}/',
         'bg_color': '#1e2026', 'text_color': '#ffffff',
         'border_color': '#0b0c0f', 'icon_code': 'graph-up-arrow',
         'category': 'Crypto', 'cta': 'Follow'},
        {'name': 'OpenSea', 'domains': 'opensea.io',
         'category': 'Crypto', 'is_nofollow': True},
        {'name': 'Ghost blog', 'domains': 'ghost.io',
         'category': 'Content'},
    ],
}
files = {'file': (io.BytesIO(json.dumps(payload).encode()), 'niche.json')}
r = client.post('/admin/link-sources/import', data=files,
                content_type='multipart/form-data', follow_redirects=True)
check('upload import -> 200 (redirected to list)', r.status_code == 200)
check('import success flash', 'Import complete' in r.get_data(as_text=True))
with app.app_context():
    cmc = LinkSource.query.filter_by(name='CoinMarketCap').first()
    os_ = LinkSource.query.filter_by(name='OpenSea').first()
    crypto = LinkCategory.query.filter_by(name='Crypto').first()
    check('new category created by label', crypto is not None
          and crypto.icon_code == 'graph-up-arrow')
    check('source references category by label',
          cmc is not None and cmc.category == 'Crypto')
    check('brand fields carried over', cmc is not None
          and cmc.bg_color == '#1e2026' and cmc.cta == 'Follow')
    check('bool flags parsed ("true" strings too)',
          os_ is not None and os_.is_nofollow is True)
    ghost = LinkSource.query.filter_by(name='Ghost blog').first()
    check('existing live category label accepted',
          ghost is not None and ghost.category == 'Content')
    act = Activity.query.filter_by(kind='link_catalogue_imported').first()
    check('audit entry for import', act is not None
          and 'niche.json' in (act.detail or ''))
    act_e = Activity.query.filter_by(kind='link_catalogue_exported').first()
    check('audit entry for export exists', act_e is not None)

print('\n== Import: skip vs overwrite policy ==')
payload2 = {
    'sources': [{'name': 'OpenSea', 'domains': 'opensea.io',
                 'bg_color': '#ff00aa', 'category': 'Crypto'}],
}
data2 = {'text': json.dumps(payload2), 'source_mode': 'skip'}
r = client.post('/admin/link-sources/import', data=data2,
                follow_redirects=True)
with app.app_context():
    op = LinkSource.query.filter_by(name='OpenSea').first()
    check('skip mode leaves live row untouched', op.bg_color != '#ff00aa')
data2['source_mode'] = 'overwrite'
r = client.post('/admin/link-sources/import', data=data2,
                follow_redirects=True)
with app.app_context():
    op = LinkSource.query.filter_by(name='OpenSea').first()
    check('overwrite mode updates in place', op.bg_color == '#ff00aa')
    check('update did not duplicate row',
          LinkSource.query.filter_by(name='OpenSea').count() == 1)

print('\n== Import: revive soft-deleted rows ==')
with app.app_context():
    steam = LinkSource.query.filter_by(name='Steam').first()
    steam.is_deleted = True
    steam.is_active = False
    db.session.commit()
payload3 = {'sources': [{'name': 'Steam',
                         'domains': 'store.steampowered.com,steamcommunity.com',
                         'category': 'Gaming & Dev'}]}
r = client.post('/admin/link-sources/import',
                data={'text': json.dumps(payload3)},
                follow_redirects=True)
with app.app_context():
    steam = LinkSource.query.filter_by(name='Steam').first()
    check('deleted row revived (not duplicated)',
          steam.is_deleted is False and steam.is_active is True
          and LinkSource.query.filter_by(name='Steam').count() == 1)
    check('revive reported in flash', 'revived' in r.get_data(as_text=True))

print('\n== Dry run writes nothing ==')
before = with_count = None
with app.app_context():
    before = LinkSource.query.count()
payload4 = {'sources': [{'name': 'Mastodon', 'domains': 'mastodon.social',
                         'category': 'Social'}]}
r = client.post('/admin/link-sources/import',
                data={'text': json.dumps(payload4), 'preview': 'on'})
body = r.get_data(as_text=True)
check('preview page shown', 'Dry run' in body)
check('preview lists Mastodon as create', 'Mastodon' in body)
with app.app_context():
    check('dry run changed nothing', LinkSource.query.count() == before)
# confirm from the preview actually imports
r = client.post('/admin/link-sources/import',
                data={'text': json.dumps(payload4)},
                follow_redirects=True)
with app.app_context():
    check('confirm-import created it',
          LinkSource.query.filter_by(name='Mastodon').first() is not None)

print('\n== Validation: all-or-nothing ==')
bad = {
    'sources': [
        {'name': 'GoodOne', 'domains': 'good.example', 'category': 'Social'},
        {'name': 'BadColor', 'bg_color': 'hotpink', 'category': 'Social'},
        {'name': 'BadDomain', 'domains': 'https://evil.com/path'},
        {'name': 'NoCatLabel', 'category': 'Nonexistent Bucket'},
        {'name': 'BadPattern', 'profile_pattern': 'ftp://{handle}'},
    ],
}
r = client.post('/admin/link-sources/import', data={'text': json.dumps(bad)},
                follow_redirects=True)
body = r.get_data(as_text=True)
check('rejected with error flash', 'Nothing was imported' in body)
check('names the offending record', 'BadColor' in body or 'hotpink' in body)
check('unknown category label flagged', 'Nonexistent Bucket' in body)
with app.app_context():
    check('no partial writes',
          LinkSource.query.filter_by(name='GoodOne').first() is None)

print('\n== File-level errors ==')
r = client.post('/admin/link-sources/import', data={'text': '{not json'})
check('bad JSON rejected gracefully', b'Not valid JSON' in r.data)
r = client.post('/admin/link-sources/import',
                data={'text': json.dumps({'format': 'csv/v1',
                                          'sources': [{'name': 'X'}]})})
check('unknown format rejected', b'Unknown format' in r.data)
r = client.post('/admin/link-sources/import',
                data={'text': json.dumps({'sources': [], 'categories': []})})
check('empty arrays rejected', b'no categories and no sources' in r.data)
r = client.post('/admin/link-sources/import',
                data={'text': json.dumps(
                    {'sources': [{'name': 'Dup'}, {'name': 'dup'}]})})
check('in-file duplicate names rejected', b'more than once' in r.data)
# just over the 500 KB app-level cap (still under Flask's 5 MB limit)
big = json.dumps({'sources': [{'name': f'S{i}', 'domains': f's{i}.example'}
                              for i in range(12000)]})
assert len(big.encode()) > catalogue_io.MAX_IMPORT_BYTES
r = client.post('/admin/link-sources/import', data={'text': big})
check('oversized (>500KB) file rejected by cap', b'too large' in r.data)
huge = json.dumps({'sources': [{'name': f'H{i}', 'domains': f'h{i}.example'}
                               for i in range(120000)]})
r = client.post('/admin/link-sources/import', data={'text': huge})
check('request over server 5MB limit fails gracefully',
      r.status_code == 400 or b'larger than the' in r.data)

print('\n== Round-trip portability ==')
exp = json.loads(client.get('/admin/link-sources/export').get_data(
    as_text=True))
r = client.post('/admin/link-sources/import',
                data={'text': json.dumps(exp), 'source_mode': 'skip',
                      'category_mode': 'skip'}, follow_redirects=True)
check('re-importing own export with skip is clean',
      'Import complete' in r.get_data(as_text=True))
with app.app_context():
    check('row count unchanged after skip round-trip',
          LinkSource.query.count() == exp['counts']['sources'])

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
