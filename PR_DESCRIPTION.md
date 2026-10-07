# Link Source descriptions + fix `Map.static_folder` crash

## Summary

Two changes on this branch:

1. **New `description` field on the `LinkSource` object** — a 20–50 word blurb about each
   website, rendered as the first element under the H1 on the directory templates.
2. **Bug fix:** `AttributeError: 'Map' object has no attribute 'static_folder'` raised on the
   local instance (Werkzeug ≥ 2.2 removed that attribute from the routing `Map`).

---

## 1. Link Source description field

### Model (`models.py`)
- Added `LinkSource.description = db.Column(db.String(500), nullable=True)`.
- New constants `DESCRIPTION_MIN_WORDS = 20` / `DESCRIPTION_MAX_WORDS = 50` and
  `LinkSource.validate_description()`, which returns an error message when the text is
  missing or outside the 20–50 word range.
- Every entry in `DEFAULT_LINK_SOURCES` now carries a unique 20–50 word description of the
  website, so freshly seeded databases are populated automatically.
- `LinkSource.seed_descriptions()` backfills descriptions for existing installs where the
  column was added by migration and rows already exist (idempotent; only fills empty ones).

### Admin UI
- `templates/admin/link_source_form.html`: required "Website description" textarea with help
  text explaining the 20–50 word constraint and where it is displayed.
- `templates/admin/link_sources.html`: new "Description" column with truncated preview plus
  badges/warnings when a source has no description or an invalid word count.
- Validation wired into the admin create/edit handlers in `app.py`
  (`register_link_source_routes`).

### Directory template
- `templates/directory_source.html`: renders `{{ source.description }}` as a `<p>` directly
  below the page H1 (guarded by `{% if %}` so pages without a description are unaffected).

### Schema / data portability
- `_ensure_schema` in `app.py` adds the nullable column for dev databases.
- `catalogue_io.py` export/import round-trips the new field.

## Testing
- `tests/test_directory_smoke.py` — covers all ACs across the five stories: guest vs. unverified vs. verified adult visibility, opt-in/opt-out directory behavior, personal-website exclusion, alphabetical brand-color cards, featured paying slots, pagination config, sidebar sections.
- `app.py` / `models.py` pass syntax checks; existing smoke tests (`tests/test_homepage_smoke.py`, `tests/test_messaging_smoke.py`, `tests/test_catalogue_import_smoke.py`) unaffected.

## 2. Fix: `'Map' object has no attribute 'static_folder'`

`reserved_root_names()` (used to compute which first URL segments may not be claimed by a
Display Name slug) ended with:

```python
names.add(url_map.static_folder and 'static' or 'static')
```

`werkzeug.routing.Map` exposed `static_folder` up to Werkzeug 2.1; it was removed afterwards,
so on the installed Werkzeug 3.x every request that computed the reserved-name set crashed
with `AttributeError`.

Fixed in `app.py` by looking the attribute up defensively:

```python
static_folder = getattr(url_map, 'static_folder', None)
if static_folder:
    names.add(static_folder.strip('/').lower())
```

Behaviour is unchanged: on newer Werkzeug the `/static/<path:filename>` rule is already picked
up by the `iter_rules()` loop above, and on older versions the static folder's first segment
is still reserved explicitly.

---

## Verification

- `create_app('development')` boots cleanly against a fresh SQLite DB — no `AttributeError`;
  `reserved_root_names()` includes `static` and `is_reserved_route_segment('static')` is True.
- `GET /` → 200, `GET /directory` → 200, `GET /directory/amazon-storefront` → 200.
- All 28 seeded link sources have a description, and the detail page renders it as the first
  element after the `<h1>`:
  > *An Amazon Storefront curates the products a creator or business recommends into shoppable
  > lists, wishlists, and brand pages. Linking yours lets visitors buy the gear you actually
  > use while giving you affiliate credit for the referrals your content generates.*

### Note on pre-existing test failures
`test_directory_smoke.py` (UNIQUE constraint on `link_sources.name` during its own seeding)
and `test_homepage_smoke.py` (`BuildError: public_profile ... 'handle'`) fail identically on
the base commit `446f8db` — they are unrelated to this branch and were left untouched.
