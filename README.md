# AllMyProfiles

A SaaS "link-in-bio" service — one clean, SEO/AI-optimized public page
(`/u/<username>`, also served at the bare `/<username>`) per user, with
click tracking, tiered link limits, an admin-to-user message inbox, a
broken-link checker, an **admin-managed network catalogue** (Link Sources +
Categories) that powers a graphical add-link picker and brand-styled public
buttons, adult-content age gates, abuse reporting with a moderation
workbench (reports, reply macros, triage rules, saved views), contact-form
routing into the inbox, and a full SaaS-style admin panel (metrics, activity
audit log, impersonation, site settings).

## Stack

- **Flask 3** application factory (`app.py` → `create_app()`)
- **Flask-SQLAlchemy** + **Flask-Migrate** (schema migrations)
- **Flask-Login** (sessions), **Pillow** (avatar upload processing),
  **requests** (broken-link checker)
- **Bootstrap 5** + Bootstrap Icons + SortableJS (CDN; no build step)
- **watchdog** pinned `>=4.0,<6.1` in `requirements.txt` — Werkzeug's
  debug reloader imports `EVENT_TYPE_OPENED`, which very old watchdog
  releases lack (caused `ImportError` on `python app.py`). If an
  environment still has a stale copy, run
  `pip install --upgrade "watchdog>=4.0,<6.1"` or use the stat-based
  reloader (`flask run --debug --reloader-type stat`).
- **Development database:** SQLite (zero setup, file-based)
- **Production database:** PostgreSQL (via `DATABASE_URL`)

## Project layout

```
app.py                  # routes: marketing site, auth, dashboard, inbox,
                        # public profiles, admin panel, static pages
config.py               # Development / Production / Testing configs
models.py               # User, Link, LinkSource, LinkCategory, Setting,
                        # Activity, Message, MessageRead, LinkCheckResult,
                        # ContactMessage, AbuseReport, ReplyMacro, SavedView,
                        # TriageRule
uploads_util.py         # image validation / re-encoding / storage helpers
create_admin.py         # CLI: create or promote a site-admin account
check_links.py          # cron script: broken-link checker + inbox notifs
templates/              # Jinja2 templates (incl. templates/admin/*)
test_homepage_smoke.py  # e2e smoke test: landing page & public routes
test_messaging_smoke.py # e2e smoke test: messaging / inbox / link-check
uploads/                # ⚠ user-uploaded images — git-ignored, back this up!
instance/, *.db         # dev SQLite database — git-ignored
```

## Quick start (development — SQLite)

```bash
pip install -r requirements.txt
python create_admin.py --username admin --email you@example.com   # one-time
python app.py                 # http://127.0.0.1:5000
```

Tables and default site settings are auto-created in development.

## Configuration

`FLASK_ENV` selects the config class in `config.py`:

| Env var         | Database                                             |
|-----------------|------------------------------------------------------|
| `development`   | `sqlite:///app.db` (override with `DEV_DATABASE_URL`)|
| `production`    | `DATABASE_URL` env var (PostgreSQL)                  |
| `testing`       | In-memory SQLite                                     |

Other environment variables: `SECRET_KEY`, `UPLOAD_FOLDER`
(default `./uploads`).

## Running in production (PostgreSQL)

```bash
export FLASK_ENV=production
export SECRET_KEY='a-long-random-string'
export DATABASE_URL='postgresql://user:password@localhost:5432/allmyprofiles'
export UPLOAD_FOLDER=/var/data/allmyprofiles/uploads   # persistent disk!

pip install -r requirements.txt
flask db upgrade                       # apply schema migrations
python create_admin.py                 # bootstrap your admin account
gunicorn "app:create_app('production')" -w 4 -b 0.0.0.0:8000
```

Heroku-style `postgres://` URLs are normalized to `postgresql://` automatically.

> **Schema changes:** development auto-creates new tables via
> `db.create_all()`, but **production needs explicit migrations**. Recent
> features added tables — inbox messaging (`messages`, `message_reads`,
> `link_check_results`), the link-source catalogue (`link_sources`,
> `link_categories`), contact messages, abuse reports, reply macros, saved
> views, triage rules, plus new columns on `users`/`links` — before
> deploying, generate and apply the migration:
>
> ```bash
> flask db migrate -m "link categories/sources, moderation"   # run once, commit the result
> flask db upgrade                                # run on each production host
> ```

## User features

- Register / login, editable profile (display name, headline, bio, about)
- **Avatar upload** — PNG/JPG/GIF/WEBP files stored **locally** on the server
  under `uploads/avatars/` (images are validated, EXIF-stripped, resized to
  ≤512 px and re-encoded as JPEG). An external avatar URL still works as a
  fallback; uploads take precedence.
- Links manager: add / hide / delete, drag-and-drop ordering, click counts
- **Graphical "Add a new link" picker** — instead of a plain text dropdown,
  every network from the admin catalogue appears as one row with its icon
  (Bootstrap icon or admin-uploaded image) and name rendered in official
  brand colors, plus a search filter and URL auto-detect. Selecting a
  network reveals a contextual, brand-tinted form whose fields adapt to that
  network: handle/URL placeholders are generated from the source's profile
  pattern (`https://github.com/{handle}`), per-network terminology
  (gamertag, online ID, shop name…), an optional `@handle` sub-line, a
  custom CTA label pre-filled with the network's default ("Follow",
  "Listen", "Shop"…), and UTM tagging. The dashboard also shows a fully
  populated example link card so users see exactly what their links will
  look like before adding real ones.
- Public profiles group links into **category tabs** using each network's
  admin-defined category, style buttons with the network's brand colors and
  icon, and mark links `rel="nofollow"` where the admin flagged them. Links
  on networks marked **adult** are hidden behind an age-gate confirmation
  page (`profile_agegate.html`) when the viewer hasn't confirmed.
- **Public abuse reporting** — visitors can report a profile via `/report`
  and `/report/<username>`; reports land in the admin moderation queue.
- Public SEO-friendly profile page at `/u/<username>` (OG tags + JSON-LD
  schema.org markup so pages rank in Google and parse cleanly for AI search).
  Machine-readable extras: `/llms.txt`, `/ai-grounding` facts page, XML
  sitemaps (static + per-profile pages), `/robots.txt`, and a
  `/u/<username>/links.xml` feed per profile.
- Tiers: **Free** (default cap 3 active links), **Expanded** (10),
  **Full / Custom** (unlimited) — caps are live-editable from admin settings
- **Inbox** (`/inbox`) — receives messages from the site admin (announcements,
  ToS notices, personal notes, broken-link reports) **and visitor contact
  requests** submitted through a user's public profile (contact form /
  `submit-contact`), each with its own thread view and archive/delete actions.
  Unread messages show a live badge count in the navbar and on the dashboard;
  opening a message marks it read, and messages can be archived / unarchived.
- Profile extras: skills list, featured video embed (YouTube/Vimeo URLs are
  parsed into safe embeds), curated theme picker (higher tiers unlock more
  themes).

## Link source catalogue (networks & categories)

The look and behaviour of every link is driven by two admin-managed tables,
seeded automatically on first run (`LinkCategory.seed_defaults()` /
`LinkSource.seed_defaults()`, idempotent):

- **`link_categories`** — grouping buckets that become tabs on public
  profiles. Each carries presentation metadata: name, Bootstrap Icons code,
  background/text colors, sort order. Categories are referenced *by name*
  from sources, so the catalogue keeps working even before migration/seeding;
  deleting a category never orphans data (see CRUD rules below).
- **`link_sources`** — one row per network (GitHub, Steam, TikTok, Etsy, …):
  matching domains (for URL auto-detect), example URL, `{handle}` profile
  pattern, brand bg/text/border colors, icon (Bootstrap Icons slug or an
  uploaded custom image under `uploads/icons/`), default CTA label, category,
  sort order, `is_active` visibility, `is_adult` (triggers the public-page
  age gate), `is_nofollow`, and soft-delete for recovery.

Both tables support full admin CRUD with soft deletes + restore, validation
(hex colors, icon slugs, `{handle}` patterns, bare hostnames, duplicate
names), rename cascades, and activity-log entries.

### Admin → Link Sources (`/admin/link-sources`)

Create/edit/delete/restore networks; inactive sources disappear from the
user picker but existing links keep their branding. Domain matching powers
"paste a URL, we detect the network" in the add-link flow.

### Admin → Link Categories (`/admin/link-categories`)

Full CRUD designed for a catalogue spanning many niches:

- **List** (`GET`) — live member-source counts per category, plus an
  "orphans" panel showing sources whose category was deleted (re-file them).
- **Create / Edit** (`/new`, `/<id>/edit`) — shared form template
  (`admin/link_category_form.html`) with live preview of name/icon/colors,
  icon-chip picker, suggested palette, sort order, and an optional
  **"Apply to N sources"** rename cascade checkbox.
- **Delete** (`POST /<id>/delete`) — soft delete with explicit reassignment:
  the admin chooses which surviving category absorbs the member sources
  (default `Other`). The catch-all **`Other` is protected** — it cannot be
  deleted or renamed, guaranteeing every source always has a bucket.
- **Restore** (`POST /<id>/restore`) — undeletes a category (name-collision
  checked).
- **Reorder** (`POST /reorder`) — persisted drag-and-drop ordering used by
  public-profile tab grouping.

## Marketing home page (`/`)

A single-page landing experience built for conversion (`templates/index.html`,
served by the `home()` route):

1. **Hero** — value-proposition headline + subheadline, an interactive
   **username claim box** that live-checks availability via
   `GET /api/check-username?username=...` (enforces the reserved-username
   list and redirects straight to `/register?username=...` when available),
   and an animated phone mockup previewing a rendered profile.
2. **Social proof** — animated counters (profiles created, links hosted,
   total clicks — real DB aggregates) plus a featured-profiles showcase
   (active paid users with avatars and ≥2 links, with graceful fallback).
3. **Feature highlights** — cards for customization/themes, **SEO & AI-search
   optimization** (the key differentiator), and built-in click analytics.
4. **Pricing matrix** — four tiers (Free / Expanded $5 / Full $12 / Custom
   $29 per month) with a monthly↔annual toggle showing discounted annual
   rates ($4/$10/$24 per month billed yearly). *Prices are display-only
   placeholders until billing is integrated.*
5. **Live sandbox** — type a title + URL and watch the link stack render in
   real time inside the phone frame.
6. **FAQ accordion** — custom domains, tracking pixels, link limits, SEO.
7. **Footer** — final signup CTA plus links to `/help`, `/contact`,
   `/terms`, `/privacy` and social accounts.

Supporting public routes: `/api/check-username`, `/help`, `/contact`
(form submissions are logged as `contact_request` activity events and
surface on the admin dashboard — no email service wired up yet),
`/terms`, `/privacy`, `/report` + `/report/<username>` (public abuse
reports → admin moderation queue). Reserved usernames (e.g. `admin`,
`help`, `api`) are blocked at registration and in the availability API.

## Admin messaging & user inbox

Admins compose messages from **`/admin/messages`** (or the “Message” button
next to any account on `/admin/users`). Every message has:

| Attribute   | Values                                                                 |
|-------------|------------------------------------------------------------------------|
| Category    | `announcement` (feature news), `broken_links`, `note` (personal), `tos_violation`, `billing` |
| Severity    | `info`, `success`, `warning`, `critical` — drives badge colour and inbox sort order |
| Audience    | one user (DM), all users, **paid users only**, or a single tier         |

Each broadcast is stored once (`messages` table); per-recipient state
(read/unread/archived) lives in `message_reads`, so read-receipts and unread
counts are tracked per user without duplicating content. Admins can see
delivered/read stats per message and **recall** (delete) a broadcast, which
removes it from every recipient's inbox. Recipients who were never addressed
get a 404 — messages are strictly private.

Typical uses covered by the four requested scenarios:

- **Feature announcements for paid users** → compose with audience
  *Paid users only*.
- **Broken links (404s etc.)** → automated, see link checker below.
- **Personal notes** → DM from the users list, category *Personal note*.
- **ToS violation notices** → DM with category *Terms of Service notice*
  (defaults to `critical` severity, rendered as an urgent red alert).

### Broken-link checker cron (`check_links.py`)

A standalone script checks every active user link (HTTP HEAD, falling back to
GET for servers that reject HEAD) and records results in `link_check_results`.
Users with broken links receive a `broken_links` inbox message — but only
once per cooldown window so repeated runs don't spam them. When a link
recovers, its notification clock resets, so a *new* outage notifies again
immediately. The cooldown is configurable from the admin Site settings page
(setting key `link_check_notify_days`, default **7**).

```bash
# manual run / first-time test (no messages sent):
python check_links.py --dry-run

# cron example — daily at 03:15:
15 3 * * * cd /path/to/app && FLASK_ENV=production python check_links.py >> /var/log/allmyprofiles/linkcheck.log 2>&1
```

Exit code is 0 on success; broken links found are logged but not treated as
script failures. On hosts like PythonAnywhere, use their scheduled-tasks UI
with the same command line.


## Admin panel (`/admin`)

Site admins (`is_admin` flag) get a SaaS-style dashboard:

- **Overview** — instant metrics: total users / links / clicks, signups today
  and in the last 7 days, tier breakdown, top links by clicks, newest accounts
- **Recent activity feed** — audit log of signups, tier upgrades/downgrades,
  admin-flag changes, settings changes, impersonation sessions
- **Users** (`/admin/users`) — searchable/paginated directory; change any
  user's tier, grant/revoke admin, and **account impersonation** (support
  sessions) with a persistent red banner and one-click “Stop impersonating”
- **Messages** (`/admin/messages`) — compose/broadcast inbox messages, view
  delivered & read stats per message, recall broadcasts (see section above)
- **Site settings** (`/admin/settings`) — variable caps without redeploying:
  Free-tier max links (kept at **3** by default), Expanded-tier max links,
  max avatar upload size (KB), broken-link notification cooldown (days),
  site tagline
- **Link Sources** (`/admin/link-sources`) — the network catalogue behind
  the graphical add-link picker and brand-styled buttons: create/edit,
  domains + `{handle}` pattern, brand colors, icon (built-in or uploaded),
  default CTA, category, active/adult/nofollow flags, soft delete + restore
- **Link Categories** (`/admin/link-categories`) — full CRUD on the grouping
  buckets used for public-profile tabs: live source counts, orphan panel,
  rename cascade, protected `Other` fallback, reassignment-on-delete,
  restore, drag-and-drop reorder (see *Link source catalogue* above)
- **Reports / moderation** (`/admin/reports`) — queue of public abuse
  reports with status updates, bulk actions, and one-click "message the
  reporter" (opens compose prefilled); triage rules auto-assign new reports
- **Reply macros** (`/admin/macros`) — canned responses with
  `{{placeholder}}` substitution at send time
- **Triage rules** (`/admin/triage`) — lightweight automation applied to
  incoming abuse reports
- **Saved views** (`POST /admin/saved-views`) — persist filtered user-list
  views for recurring admin workflows
- **Audit log** (`/admin/audit`) — searchable activity history (signups,
  tier changes, catalogue/category CRUD, impersonation, bulk actions…)

Admin privileges are intentionally suspended while impersonating; logging out
also ends any impersonation session. Every impersonation start/stop is written
to the activity log.

### Bootstrapping the first admin

There is no self-service admin signup (deliberate). Run:

```bash
python create_admin.py [--username NAME] [--email EMAIL] [--password PW]
```

It creates the account or promotes an existing one. Additional admins can be
granted from the Users page.

## Tests / smoke checks

Two self-contained end-to-end scripts exercise the app with Flask's test
client (no server or external services needed):

```bash
python test_homepage_smoke.py     # landing page, claim box API, static pages,
                                  # public profiles, auth redirects
python test_messaging_smoke.py    # register/login → paid-only broadcast →
                                  # unread badge → read/archive → link-check
                                  # notifications + cooldown dedupe → recall
```

Both use the `testing` config; they exit non-zero if any check fails.

## Payments / billing

**Not yet integrated.** Tier changes are currently admin-driven (manual
upgrade workflow, e.g. after an invoice). Stripe Billing is the planned
next step: webhook flips `User.tier` + records a `tier_change` activity —
the admin dashboard already tracks exactly that event.

## Files & backups

`uploads/` (all user images) and the SQLite DB are excluded from git via
`.gitignore`. In production, point `UPLOAD_FOLDER` at persistent storage and
include it in backups alongside the PostgreSQL dumps. If you move to a
multi-server or ephemeral-container host, swap the local-disk storage in
`uploads_util.py` for S3/GCS.
