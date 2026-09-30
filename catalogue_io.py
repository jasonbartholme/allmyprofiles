"""Bulk import/export for the admin link catalogue (LinkSource + LinkCategory).

The format is JSON so it stays diffable and hand-editable:

    {
      "format": "allmyprofiles-catalogue/1",
      "exported_at": "2026-09-30T12:00:00",
      "categories": [
        {"name": "Gaming & Dev", "icon_code": "controller",
         "bg_color": "#e7f1e7", "text_color": "#1e4620", "sort_order": 0}
      ],
      "sources": [
        {"name": "GitHub", "domains": "github.com,gitlab.com",
         "category": "Gaming & Dev", ...}
      ]
    }

Categories are referenced *by label* (``"category": "Music"``), never by
database id — ids differ between environments, while labels are the stable
key ``LinkSource.category`` already uses. Import validates everything up
front and applies nothing unless the whole file is clean (all-or-nothing),
with a "dry run" preview mode built on the same code path.

Conflict policy per record name (both tables):
  * new name            -> create
  * existing, deleted   -> restore + overwrite fields ("revive")
  * existing, live      -> "overwrite" updates in place; "skip" leaves it.
"""

import json
import re
from datetime import datetime

from models import db, LinkSource, LinkCategory, HEX_COLOR_RE

FORMAT_ID = 'allmyprofiles-catalogue/1'
MAX_IMPORT_BYTES = 500 * 1024  # 500 KB cap on uploaded files


class ImportError_(Exception):
    """Raised for fatal, file-level import problems (bad JSON, unknown
    format, duplicate names within the file). Named with a trailing
    underscore to avoid shadowing the builtin."""


# ---------------------------------------------------------------- helpers

def _clean_str(value, maxlen):
    return str(value).strip()[:maxlen] if value is not None else ''


def _clean_bool(value, default=False):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ('true', '1', 'yes', 'on', 'y'):
        return True
    if s in ('false', '0', 'no', 'off', 'n'):
        return False
    raise ValueError(f'not a boolean: {value!r}')


def _clean_int(value, default=0):
    if value is None or value == '':
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f'not an integer: {value!r}')


def _clean_color(value, fallback):
    v = _clean_str(value, 9).lower()
    if not v:
        return fallback
    if not HEX_COLOR_RE.match(v):
        raise ValueError(f'not a hex color like #ff0000: {value!r}')
    return v


def _clean_domains(value):
    """Lowercase bare hostnames, comma-separated; reject URLs/spaces."""
    raw = _clean_str(value, 300).lower()
    if not raw:
        return ''
    out = []
    for d in [x.strip() for x in raw.replace(';', ',').split(',') if x.strip()]:
        if any(c in d for c in (' ', '/', ':', '@')):
            raise ValueError(f'domain "{d}" must be a bare hostname '
                             f'(e.g. github.com), no http:// or spaces')
        out.append(d)
    # de-duplicate, keep order
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return ','.join(uniq)


def _clean_pattern(value):
    raw = _clean_str(value, 300)
    if not raw:
        return ''
    if not raw.startswith(('http://', 'https://')):
        raise ValueError('profile pattern must start with http:// or https://')
    if '{handle}' not in raw:
        raise ValueError('profile pattern must contain the {handle} placeholder')
    return raw


ICON_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,48}$')


def _clean_icon(value, default):
    v = _clean_str(value, 50).lower()
    if not v:
        return default
    if not ICON_SLUG_RE.match(v):
        raise ValueError(f'icon code "{v}" must be a Bootstrap Icons slug '
                         f'(lowercase letters, digits, hyphens)')
    return v


# ---------------------------------------------------------------- export

SOURCE_EXPORT_FIELDS = (
    'name', 'domains', 'url', 'profile_pattern', 'bg_color', 'text_color',
    'border_color', 'icon_code', 'is_nofollow', 'cta', 'category',
    'sort_order', 'is_active', 'is_adult',
)
CATEGORY_EXPORT_FIELDS = ('name', 'icon_code', 'bg_color', 'text_color',
                          'sort_order')


def build_export(include_deleted=False):
    """Serialize the catalogue to a plain dict ready for json.dumps()."""
    def rows(model, fields, deleted_flag=False):
        q = model.query
        if not include_deleted:
            q = q.filter_by(is_deleted=False)
        q = q.order_by(model.sort_order, model.name)
        return [{f: getattr(r, f) for f in fields} for r in q.all()]

    return {
        'format': FORMAT_ID,
        'exported_at': datetime.now().isoformat(timespec='seconds'),
        'counts': {
            'categories': len(rows(LinkCategory, CATEGORY_EXPORT_FIELDS)),
            'sources': len(rows(LinkSource, SOURCE_EXPORT_FIELDS)),
        },
        'categories': rows(LinkCategory, CATEGORY_EXPORT_FIELDS),
        'sources': rows(LinkSource, SOURCE_EXPORT_FIELDS),
    }


def export_json(include_deleted=False):
    return json.dumps(build_export(include_deleted), indent=2,
                      ensure_ascii=False)


# ---------------------------------------------------------------- import

def _normalize_category_entry(entry, idx, errors, seen):
    name = _clean_str(entry.get('name'), 40)
    if not name:
        errors.append(f'categories[{idx}]: name is required.')
        return None
    if name in seen:
        errors.append(f'categories[{idx}]: "{name}" appears more than once '
                      f'in this file.')
        return None
    seen.add(name)
    data = {'name': name}
    try:
        data['icon_code'] = _clean_icon(entry.get('icon_code'), 'tag')
        data['bg_color'] = _clean_color(entry.get('bg_color'), '#f8f9fa')
        data['text_color'] = _clean_color(entry.get('text_color'), '#212529')
        data['sort_order'] = _clean_int(entry.get('sort_order'), idx)
    except ValueError as e:
        errors.append(f'categories[{idx}] ("{name}"): {e}')
        return None
    return data


def _normalize_source_entry(entry, idx, errors, seen):
    name = _clean_str(entry.get('name'), 80)
    if not name:
        errors.append(f'sources[{idx}]: name is required.')
        return None
    if name.lower() in seen:
        errors.append(f'sources[{idx}]: "{name}" appears more than once '
                      f'in this file.')
        return None
    seen.add(name.lower())
    data = {'name': name}
    try:
        data['domains'] = _clean_domains(entry.get('domains'))
        url = _clean_str(entry.get('url'), 300)
        if url and not url.startswith(('http://', 'https://')):
            raise ValueError('example URL must start with http:// or https://')
        data['url'] = url
        data['profile_pattern'] = _clean_pattern(entry.get('profile_pattern'))
        data['bg_color'] = _clean_color(entry.get('bg_color'), '#ffffff')
        data['text_color'] = _clean_color(entry.get('text_color'), '#212529')
        data['border_color'] = _clean_color(entry.get('border_color'),
                                            '#dee2e6')
        data['icon_code'] = _clean_icon(entry.get('icon_code'), 'link-45deg')
        data['category'] = _clean_str(entry.get('category'), 40) or 'Other'
        data['cta'] = _clean_str(entry.get('cta'), 40)
        data['sort_order'] = _clean_int(entry.get('sort_order'), idx)
        data['is_active'] = _clean_bool(entry.get('is_active'), True)
        data['is_adult'] = _clean_bool(entry.get('is_adult'), False)
        data['is_nofollow'] = _clean_bool(entry.get('is_nofollow'), False)
    except ValueError as e:
        errors.append(f'sources[{idx}] ("{name}"): {e}')
        return None
    return data


def parse_payload(raw):
    """Parse + structurally validate the uploaded text. Returns the payload
    dict; raises ImportError_ for file-level problems."""
    if raw is None:
        raise ImportError_('No data supplied.')
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8-sig', errors='replace')
    raw = raw.strip()
    if not raw:
        raise ImportError_('The file is empty.')
    if len(raw.encode('utf-8')) > MAX_IMPORT_BYTES:
        raise ImportError_('File is too large (max 500 KB).')
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ImportError_(f'Not valid JSON: {e}')
    if not isinstance(payload, dict):
        raise ImportError_('Top level of the file must be an object with '
                           '"categories" and/or "sources" arrays.')
    fmt = payload.get('format')
    if fmt and fmt != FORMAT_ID:
        raise ImportError_(f'Unknown format "{fmt}". Expected "{FORMAT_ID}" '
                           f'(use Export to download a correctly shaped '
                           f'template).')
    cats = payload.get('categories', [])
    srcs = payload.get('sources', [])
    if not isinstance(cats, list) or not isinstance(srcs, list):
        raise ImportError_('"categories" and "sources" must be arrays.')
    if not cats and not srcs:
        raise ImportError_('The file contains no categories and no sources.')
    return payload


def plan_import(payload, source_mode='overwrite', category_mode='overwrite'):
    """Validate every record and classify it against the DB.

    Returns (plan, errors):
      plan   -- dict of action lists (create / revive / update / skip)
      errors -- list of human-readable strings; plan is unusable when non-empty.

    Nothing is written to the session here — safe for dry-run previews.
    """
    errors = []
    cat_entries, src_entries = [], []

    seen_c = set()
    for idx, entry in enumerate(payload.get('categories', [])):
        if not isinstance(entry, dict):
            errors.append(f'categories[{idx}]: each entry must be an object.')
            continue
        data = _normalize_category_entry(entry, idx, errors, seen_c)
        if data is not None:
            cat_entries.append(data)

    seen_s = set()
    for idx, entry in enumerate(payload.get('sources', [])):
        if not isinstance(entry, dict):
            errors.append(f'sources[{idx}]: each entry must be an object.')
            continue
        data = _normalize_source_entry(entry, idx, errors, seen_s)
        if data is not None:
            src_entries.append(data)

    # Cross-file category-label check: a source may only reference a
    # category that exists live after this import, or one created by it.
    live_cat_names = {c.name for c in LinkCategory.query
                      .filter_by(is_deleted=False).all()}
    importing_cats = {d['name'] for d in cat_entries}
    for data in src_entries:
        cat = data['category']
        if cat not in live_cat_names and cat not in importing_cats \
                and cat != 'Other':
            errors.append(
                f'source "{data["name"]}": unknown category label "{cat}" — '
                f'add it to "categories" in this file or change the label. '
                f'(Live categories: {", ".join(sorted(live_cat_names)) or "—"}.)')

    if errors:
        return None, errors

    plan = {'categories': {'create': [], 'revive': [], 'update': [],
                           'skip': []},
            'sources': {'create': [], 'revive': [], 'update': [], 'skip': []}}

    for model, entries, mode, bucket in (
            (LinkCategory, cat_entries, category_mode, plan['categories']),
            (LinkSource, src_entries, source_mode, plan['sources'])):
        for data in entries:
            row = model.query.filter_by(name=data['name']).first()
            if row is None:
                bucket['create'].append(data)
            elif row.is_deleted:
                bucket['revive'].append((row.id, data))
            elif mode == 'skip':
                bucket['skip'].append(data['name'])
            else:
                bucket['update'].append((row.id, data))
    return plan, []


def apply_plan(plan):
    """Execute a validated plan inside a single transaction.

    Returns summary counts. Caller commits/rolls back via exceptions; a
    failure at flush time rolls the whole thing back and re-raises.
    """
    created = revived = updated = skipped = 0

    def apply_rows(model, bucket, extra_defaults=()):
        nonlocal created, revived, updated, skipped
        for data in bucket['create']:
            db.session.add(model(**data))
            created += 1
        for row_id, data in bucket['revive']:
            row = db.session.get(model, row_id)
            for key, value in data.items():
                setattr(row, key, value)
            row.is_deleted = False
            row.updated_at = datetime.now()
            if model is LinkSource:
                row.is_active = data.get('is_active', True)
            revived += 1
        for row_id, data in bucket['update']:
            row = db.session.get(model, row_id)
            for key, value in data.items():
                setattr(row, key, value)
            row.updated_at = datetime.now()
            updated += 1
        skipped += len(bucket['skip'])

    apply_rows(LinkCategory, plan['categories'])
    apply_rows(LinkSource, plan['sources'])
    return {'created': created, 'revived': revived, 'updated': updated,
            'skipped': skipped}


def import_from_text(raw, source_mode='overwrite', category_mode='overwrite',
                     dry_run=False):
    """One-shot convenience wrapper. Returns (result_dict, errors_list).

    On success result_dict has the apply_plan summary (+ 'dry_run': bool);
    on validation failure result_dict is None and errors describe the file.
    Raises ImportError_ for file-level problems (caller flashes them)."""
    payload = parse_payload(raw)
    plan, errors = plan_import(payload, source_mode, category_mode)
    if errors:
        return None, errors
    if dry_run:
        return {'dry_run': True, 'plan': plan}, []
    summary = apply_plan(plan)
    summary['dry_run'] = False
    return summary, []
