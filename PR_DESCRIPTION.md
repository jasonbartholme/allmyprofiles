# Public Directory Feature Bundle

Implements five linked stories building the public Link Source directory, its privacy and age-gating controls, and the source detail pages with sidebar content.

## Stories covered

### 1. `is_adult` flag on Link Categories
- New boolean data field `LinkCategory.is_adult`, defaulting to **FALSE**, editable via the admin category form (`/admin/link-categories/<id>/edit`) with a toggle switch and shown as an "18+" badge in the admin list.
- Foundation for tier-based display control (free-tier users will not see adult content) and next-phase features.

### 2. Directory Visibility Preference (User Dashboard)
- New `User.directory_visible` boolean (default FALSE / opt-in) persisted from a dashboard toggle ("List me in the public directory").
- Single eligibility predicate `Link.is_directory_eligible`: active link + owner opted in + live source + **personal websites strictly excluded** (`LinkSource.NON_DIRECTORY_SOURCES`) regardless of toggle state.

### 3. Link Source Master Directory Page (`/directory`)
- H1 **"Social Networks"** with descriptive lead text.
- Link Sources rendered as cards ordered alphabetically, styled with their brand colors (WCAG-AA-safe text via `readable_text_color()`), showing icon, category, and live member count.
- Adult network cards hidden from guests and from logged-in users who are not 18+ verified; visible to verified users/admins with a red "18+" badge and an "I am 18+" unlock flow.

### 4. User Object Addition & Age Gate
- Registration form includes a **required** "I confirm that I am 18 years of age or older" checkbox, validated server-side.
- Persisted as `User.age_verified` (Boolean, NOT NULL, server_default `'0'`); mirrored into `session['age_ok']`.
- Enforcement: adult content requires a logged-in account (free tier is enough — no profile creation required) **and** 18+ verification (`User.can_view_adult()` / `directory_adult_ok()`). Admin user list shows verification status badges.

### 5. Link Source Detail Page (`/directory/<slug>`) + Sidebar Sections
- Dynamic route per source, e.g. `/directory/github`; H1 follows the naming pattern (e.g. "GitHub Profiles on All My Profiles").
- Up to **5 paying users slotted at random at the top**, non-paying users listed alphabetically below with **admin-configurable page size**.
- Cards show user name, small avatar thumbnail, external-link icon to their network profile, and an "AMP Profile" link to their local site profile.
- Sidebar: **"Most Popular on AMP"** (top 10 by click traffic from cached data flushed nightly), **directory FAQ** (how to get listed: create account → add links → enable directory visibility), **"Create an account" CTA card** for guests, plus "Other Networks" quick-filter links.

## Schema changes
| Table | Column | Type | Default |
|---|---|---|---|
| `link_categories` | `is_adult` | BOOLEAN | FALSE |
| `users` | `directory_visible` | BOOLEAN NOT NULL | FALSE (`server_default '0'`) |
| `users` | `age_verified` | BOOLEAN NOT NULL | FALSE (`server_default '0'`) |

Dev databases are upgraded automatically via the `_ensure_schema()` helper (`ALTER TABLE ... ADD COLUMN`). A production Alembic migration should be generated before deploy (`flask db migrate -m "directory feature bundle"`).

## Testing
- `tests/test_directory_smoke.py` — covers all ACs across the five stories: guest vs. unverified vs. verified adult visibility, opt-in/opt-out directory behavior, personal-website exclusion, alphabetical brand-color cards, featured paying slots, pagination config, sidebar sections.
- `app.py` / `models.py` pass syntax checks; existing smoke tests (`tests/test_homepage_smoke.py`, `tests/test_messaging_smoke.py`, `tests/test_catalogue_import_smoke.py`) unaffected.

## Housekeeping
- Untracked `__pycache__/` and `instance/*.db` artifacts (they were previously committed); `.gitignore` updated accordingly.
