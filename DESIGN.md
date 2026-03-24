# Wall of Faces — Design

> Last updated: 2026-03-21
> Status: **DRAFT**

See `PLAN.md` for decisions and goals. This document covers the *how*.

---

## File & Module Structure

```
wall-of-faces/
├── app.py                  # Flask app + community config block at top
├── requirements.txt
├── wsgi.py                 # Toolforge Kubernetes entry point
├── uwsgi.ini
├── generate_pdfs.py        # Standalone export script (reads DB, outputs/re-uses snapshots)
│
├── migrations/             # Alembic migration scripts
│   └── versions/
│
├── strings/                # UI translation files
│   ├── nl.yml              # Default (Dutch)
│   └── en.yml
│
├── templates/
│   ├── base.html
│   ├── index.html          # Landing / login
│   ├── gather.html         # Progress screen during data gathering
│   ├── buffet.html         # Discovery buffet
│   ├── card.html           # Card preview + editor (browser wrapper; embeds card_content.html)
│   ├── card_content.html   # THE card template — pure semantic content, no inline styles
│   │                       # Used by both browser preview and WeasyPrint
│   ├── admin.html          # Admin overview
│   └── privacy.html
│
├── static/
│   ├── css/
│   │   ├── card-theme-wikipedia.css   # Design A — Wikipedia article style
│   │   ├── card-theme-editorial.css   # Design B — modern conference style
│   │   ├── card-theme-minimal.css     # Design C — typographic minimal
│   │   └── card-print.css             # Print additions: @page, print-color-adjust, no-screen elements
│   └── js/
│
└── src/
    ├── auth.py             # (not used — OAuth 2.0 flow is in app.py directly)
    ├── db.py               # SQLAlchemy models
    ├── i18n.py             # Load strings/<lang>.yml, t() helper
    ├── gather.py           # Background job: orchestrates all data sources
    ├── wiki.py             # MediaWiki API calls
    ├── replicas.py         # Toolforge replica DB queries (counts only)
    ├── barnstars.py        # Barnstar registry + wikitext detection
    ├── achievements.py     # Achievement badge logic
    ├── llm.py              # Mistral API integration
    ├── render.py           # render_card_html() → HTML string; render_card() → PDF bytes via subprocess
    ├── weasyprint_worker.py  # Subprocess entry point: applies safe_url_fetcher, writes PDF to stdout
    ├── snapshot.py         # Finalization: download images, render PDF, write to disk
    ├── utils.py            # utcnow(), TypeDecorator for UTC datetime, shared helpers
    └── export.py           # ZIP + merged PDF assembly (uses pypdf for merging)
```

---

## Data Model

### `UserProfile` table

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| username | String(255) | Unique, indexed |
| avatar_filename | String(500) | Commons filename of chosen avatar |
| registration_date | UTCDateTime | Account registration date — fetched from CentralAuth during gather; used in infobox |
| total_edits | Integer | Total edit count across all wikis — fetched from replicas during gather |
| user_rights | Text | JSON list of display-ready right names (e.g. `["Beheerder"]`) — fetched from MediaWiki API during gather |
| home_base | String(100) | Where the user is based — the only vibe field; mission/role dropped in favour of free-form biography |
| biography | Text | User-written lead text (third person) |
| biography_image_filename | String(500) | Legacy: single floated biography image (superseded by `content_image_filenames`) |
| content_image_filenames | Text | JSON list of filenames (max 3) shown as a row of images in the content area |
| extra_sections | Text | JSON list of `{title, text}` — no hard limit; card preview warns on overflow |
| selected_achievements | Text | JSON list of accepted achievement IDs |
| userboxes | Text | JSON ordered list of accepted userbox entries |
| barnstars | Text | JSON list of chosen barnstar entries `{filename, name}` |
| signature_html | Text | Rendered HTML of chosen signature |
| proud_of | Text | JSON list of `{title, filename}` for "Trots op" row — Commons filenames |
| snapshot_at | DateTime | Timestamp of latest user-triggered finalization; NULL if never finalized |
| snapshot_version | String(30) | Version dir of latest snapshot (e.g. `20260501_143022_412381`) — 30 chars fits microsecond format |
| user_approved_snapshot_version | String(30) | Last snapshot version explicitly triggered by the user (not by `--rerender` or admin); NULL until first user finalization |
| gather_status | String(20) | `idle` / `running` / `done` / `error` |
| gather_progress | Integer | 0–100 |
| llm_consent | Boolean | Whether the user has opted in to LLM processing; NULL = not yet asked; False = declined |
| gather_error | String(500) | Human-readable error from last failed gather; NULL on success |
| last_gathered_at | DateTime | Timestamp of the last completed (done or error) gather; used for cooldown enforcement |
| processed_at | DateTime | Set by admin on export; NULL until organiser acts |
| created_at | DateTime | UTC |
| updated_at | DateTime | UTC |

**Server-side field length limits** (enforced at `POST /api/save-profile`):

| Field | Max length |
|-------|-----------|
| `home_base` | 100 characters |
| `biography` | 2 000 characters |
| `extra_sections[*].title` | 100 characters |
| `extra_sections[*].text` | 500 characters |
| `proud_of` | max 10 items |
| `barnstars` | max 2 items |
| `userboxes` | max 20 items |
| `content_image_filenames` | max 3 items |

### `GatherQueue` table

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| username | String(255) | Indexed |
| status | String(20) | `waiting` / `running` / `done` / `error` |
| created_at | DateTime | UTC — determines queue position |
| started_at | DateTime | Set when job is dispatched; NULL until then |
| finished_at | DateTime | Set on completion or error; NULL until then |

All five tables (`UserProfile`, `GatherCache`, `GatherQueue`, `SnapshotQueue`, `sessions`)
must be present before the app serves requests. `UserProfile`, `GatherCache`,
`GatherQueue`, and `SnapshotQueue` are managed by Alembic. `sessions` is created
by Flask-Session — `create_app()` calls `db.metadata.create_all(conn)` after
`Session(app)` to ensure it exists before the first request.

Note: Flask-Session 0.8 names the table `sessions` (not `flask_sessions`).

**DateTime columns — UTC TypeDecorator.** MySQL `DATETIME` does not store timezone
info. `DateTime(timezone=True)` in SQLAlchemy on MySQL is a no-op and causes
`TypeError` when comparing stored naive datetimes against `datetime.now(timezone.utc)`.
All datetime columns use a `UTCDateTime` TypeDecorator (in `src/utils.py`) that
strips tzinfo on write and re-attaches `timezone.utc` on read:

```python
from sqlalchemy import TypeDecorator, DateTime as SADateTime
from datetime import datetime, timezone

class UTCDateTime(TypeDecorator):
    impl = SADateTime
    cache_ok = True
    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            return value.replace(tzinfo=None)  # store as naive UTC
        return value
    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=timezone.utc)
        return value
```

Use `UTCDateTime` for every `DateTime` column in all models.

---

### `SnapshotQueue` table

Same schema as `GatherQueue` — used for admin-triggered snapshot jobs dispatched
by the coordinator. Kept as a separate table so gather and snapshot jobs can be
prioritised independently.

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| username | String(255) | Indexed |
| status | String(20) | `waiting` / `running` / `done` / `error` |
| created_at | UTCDateTime | UTC |
| started_at | UTCDateTime | Set when dispatched |
| finished_at | UTCDateTime | Set on completion or error |

---

### `GatherCache` table

Stores the raw buffet suggestions produced by the last gather run.
Never shown directly to users — feeds the buffet UI, which records accepted items into `UserProfile`.

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| user_id | Integer FK | `user_profile.id ON DELETE CASCADE` — cascade ensures rows are removed with the account |
| section | String(20) | `identity` / `achievements` / `biography` |
| item_type | String(30) | `userbox` / `barnstar` / `badge` / `avatar` / `signature` / `proud_of` |
| payload | Text | JSON — item-specific data |
| source | String(20) | `api` / `replica` / `llm` |
| gathered_at | DateTime | UTC |

---

## Routes

### Page routes

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` | — | Landing (logged out); logged-in users are redirected based on `gather_status`: `idle/error` → `/gather`, `queued/running` → `/gather` (progress), `done` + no card → `/buffet`, `done` + card exists → `/card` |
| GET | `/login` | — | Initiate OAuth handshake |
| GET | `/oauth-callback` | — | Complete OAuth, set session |
| GET | `/logout` | user | Clear session |
| GET | `/gather` | user | Progress screen; polls `/api/gather-status` |
| GET | `/buffet` | user | Discovery buffet |
| GET | `/card` | user | Card preview + editor |
| GET | `/privacy` | — | Privacy statement |
| GET | `/healthz` | — | Liveness/readiness probe: checks DB connectivity; returns `{"ok": true}` or 503 with no detail |
| POST | `/api/finalize` | user | Trigger card finalization snapshot |
| POST | `/admin/mark-processed` | admin | Set `processed_at` on one or all profiles (alias: use `POST /admin/snapshot` which sets it on export) |
| GET | `/admin` | admin | Overview: all profiles, export buttons |

| GET | `/admin/export?format=zip` | admin | ZIP of individual PDFs, one per user, named `<username>.pdf` |
| GET | `/admin/export?format=merged` | admin | Single PDF with all cards, one card per page, alphabetical by username |

---

### API contracts

---

#### `POST /api/gather`
Enqueue a background data-gathering job for the logged-in user.
If a job is already running, returns the current status without starting a new one.

**Auth:** user session required

**Request body**
```json
{ "llm_consent": true }
```
`llm_consent` is required on first gather. On subsequent gathers, the stored value is used
if the field is omitted. If `llm_consent` is `false`, the gather runs without the LLM step
(no wikitext is sent to Mistral); all other data sources are unaffected. The consent
choice is stored in `UserProfile.llm_consent` and shown to the user on the gather screen
with a clear description of what Mistral receives and the EU API endpoint used.

**Response `400`** — first gather and `llm_consent` missing
```json
{ "error": "llm_consent_required", "detail": "You must indicate whether to allow LLM processing." }
```

**Response `200`**
```json
{
  "status": "queued" | "running" | "done",
  "queue_position": 2
}
```

**Response `429`** — user has triggered a gather within the cooldown period
```json
{ "error": "too_soon", "retry_after_seconds": 3540 }
```

---

#### `GET /api/gather-status`
Poll the current gather job state. Called every 3 seconds by the progress screen.

**Auth:** user session required

**Response `200`**
```json
{
  "status": "idle" | "queued" | "running" | "done" | "error",
  "progress": 65,
  "queue_position": 0,
  "error_message": null
}
```

---

#### `POST /api/save-profile`
Persist the user's accepted buffet choices to `UserProfile`.
Partial updates are supported — only fields present in the body are updated.

**Auth:** user session required
**Content-Type:** `application/json`

**Request body**
```json
{
  "avatar_filename": "Example.jpg",
  "home_base": "Amsterdam",
  "biography": "Gebruikersnaam is een redacteur...",
  "biography_image_filename": "MyPhoto.jpg",
  "signature_html": "<span>...</span>",
  "userboxes": [
    { "icon": "NL", "label": "Moedertaal Nederlands", "bg": "#dbeafe", "fg": "#1e40af" }
  ],
  "barnstars": [
    { "filename": "Original_Barnstar.png", "name": "Ster van verdienste" }
  ],
  "selected_achievements": ["veteran", "visual_historian"],
  "proud_of": [
    { "title": "Watersnoodramp 1953", "filename": "Watersnood.jpg" }
  ],
  "extra_sections": [
    { "title": "Mijn project", "text": "..." }
  ]
}
```
All fields optional. Arrays replace the existing value entirely (not merged).
Maximum 2 items in `barnstars`. No hard limit on `extra_sections` — the card preview warns when content overflows the A5 boundary.

**Response `200`**
```json
{ "saved": true, "updated_at": "2026-05-01T14:30:22Z" }
```

**Response `422`** — validation error
```json
{ "error": "validation_error", "field": "biography", "detail": "max 2000 characters" }
```

---

#### `POST /api/finalize`
Trigger a finalization snapshot for the logged-in user's card.
Sets `snapshot_at`, `snapshot_version`, and `user_approved_snapshot_version` on success.

**Auth:** user session required
**Content-Type:** `application/json`

**Request body**
```json
{ "confirm_prune": false }
```
`confirm_prune` defaults to `false`. Set to `true` to confirm deletion of old snapshot
versions when the cap has been reached (see response `409` below).

**Response `200`** — snapshot created successfully
```json
{ "snapshot_version": "20260501_143022_412381", "snapshot_at": "2026-05-01T14:30:22Z" }
```

**Response `409`** — snapshot version cap reached; user confirmation required
```json
{
  "error": "snapshot_limit_reached",
  "version_count": 10,
  "limit": 10,
  "detail": "You have reached the maximum of 10 saved versions. Confirming will delete all versions except your current latest, keeping your history clean. Your current latest snapshot will not be deleted."
}
```
The frontend shows a confirmation dialog. If the user confirms, re-send the request
with `{ "confirm_prune": true }`. The backend then deletes all versions except the
current latest (`snapshot_version`), then proceeds to create the new snapshot.
The user ends up with 2 versions: the previous latest + the newly created one.

**Response `422`** — card content exceeds A5 (overflow check failed)
```json
{ "error": "card_overflow", "detail": "Card content exceeds A5 — trim content and try again." }
```

**Response `429`** — finalization rate limit exceeded
```json
{ "error": "too_soon", "retry_after_seconds": 60 }
```

---

#### `GET /api/resolve-image`
Resolve a Commons filename to a thumbnail URL via the MediaWiki API.

**Auth:** user session required
**Query params:** `filename` (string, required), `width` (integer, optional, default 400)

**Response `200`**
```json
{ "filename": "Original_Barnstar.png", "url": "https://upload.wikimedia.org/..." }
```

**Response `404`** — file not found on Commons
```json
{ "error": "not_found" }
```

---

#### `POST /api/delete-account`
Delete all stored data for the logged-in user: `UserProfile`, `GatherCache` rows,
and the entire snapshot directory.

**Auth:** user session required
**Request:** no body required

**Response `200`**
```json
{ "deleted": true }
```

---

#### `POST /admin/snapshot`
Trigger finalization snapshot for one or all profiles.
Sets `processed_at` on affected profiles after snapshot completes.

**Auth:** admin session required
**Content-Type:** `application/json`

**Request body**
```json
{
  "username": "ExampleUser"
}
```
Omit `username` to snapshot all profiles without a current snapshot.

**Response `200`**
```json
{ "queued": ["ExampleUser"] }
```

---

## Image Resolution

All image references in `UserProfile` are stored as **Commons filenames**, never as URLs.
URLs are resolved at display/render time via `GET /api/resolve-image`, which calls
`prop=imageinfo` on the MediaWiki API. Commons handles renamed files via redirects
transparently. Deleted files return a placeholder image.

At snapshot time (see below), images are downloaded and stored locally so the PDF
is rendered from stable local copies, not live URLs.

---

## Snapshot & Finalization

When a card is finalized (either by the admin triggering export, or optionally by the
user explicitly), `src/snapshot.py` runs:

Snapshots are **versioned** — old PDFs are never overwritten.

There are two distinct lifecycle events:

| Event | Who triggers it | Field set | Meaning |
|-------|----------------|-----------|---------|
| **Finalize** | User clicks "Finaliseer kaart" | `snapshot_at`, `snapshot_version` | Card frozen as a PDF; user can still re-finalize later |
| **Process** | Admin exports/downloads | `processed_at` | Organiser has taken the card for printing; user is informed their edits no longer affect the printed version |

```
snapshot(username, triggered_by_user=False):
    1. Resolve all Commons filenames → URLs via MediaWiki API
    2. version = now().strftime("%Y%m%d_%H%M%S_%f")   # microseconds avoid same-second collisions
    3. Download each image to:
           /data/project/wall-of-faces/snapshots/<username>/<version>/images/<filename>
       Images are fetched at a fixed width via the MediaWiki thumbnail API:
         - Avatar: 400 px wide (sufficient for ~35mm print width at 300 dpi)
         - Biography image: 600 px wide (half-column float at A5)
         - Barnstar images: 150 px wide (small icon use)
         - proud_of thumbnails: 200 px wide (thumbnail row)
       These widths are high enough for clean 300 dpi print output and low enough
       to avoid fetching multi-megabyte originals. The `width` parameter is passed
       to `prop=imageinfo&iiprop=url&iiurlwidth=N` on the Commons API — Wikimedia
       Commons generates a thumbnail at that width and returns its URL. The thumbnail
       URL (not the original file URL) is what `fetch_image()` downloads. This keeps
       file sizes small (typically 20–150 KB per image) while preserving print quality.
    4. Render card to PDF using local image paths (WeasyPrint)
    5. Write PDF to:
           /data/project/wall-of-faces/snapshots/<username>/<version>/card.pdf
    6. Update symlink atomically — no window where `latest` is missing:
           tmp = snapshots/<username_id>/latest.tmp
           os.symlink(version, tmp)
           os.replace(tmp, snapshots/<username_id>/latest)
    7. Set UserProfile.snapshot_at = utcnow(), snapshot_version = version
    8. If triggered_by_user:
           Set UserProfile.user_approved_snapshot_version = version
    9. Prune old versions (see below)
    # processed_at is NOT set here — only the admin sets it on export
    # On any exception in steps 3–8: shutil.rmtree the partial version
    # directory before re-raising, so no orphaned files accumulate.
```

**`user_approved_snapshot_version`** tracks the last snapshot explicitly triggered by the user
(via "Finaliseer kaart"), as distinct from versions created by `--rerender` or admin export.
The card UI shows a "card reviewed and approved" indicator once this field is set.
Pruning logic uses this field to determine what is safe to delete.

**`generate_pdfs.py` runs in two modes:**

```
# Default: collect latest snapshots (fast — no re-render)
python generate_pdfs.py --output ./export/

# Force re-render: re-render all profiles from DB + current template
# Creates a new snapshot version for each user; users are not involved
python generate_pdfs.py --rerender --output ./export/
```

Use `--rerender` when the card template has changed (e.g. footer, header, layout
tweaks) and you want to apply the update to all cards without asking users to
re-finalize. Each re-render creates a new versioned snapshot directory and updates
the `latest` symlink, preserving all previous versions.

**On admin export:** `POST /admin/snapshot` sets `processed_at = now()` for each
exported profile. After this, the card UI shows a notice that the printed version
is fixed. The user can still edit and re-finalize, but `processed_at` makes clear
the organiser has already acted on the previous version.

### Snapshot version pruning

**Rule 1 — per-user version cap (user-confirmed):**

When the user clicks "Finaliseer kaart" and already has `SNAPSHOT_MAX_VERSIONS_PER_USER`
(default 10) saved versions, the backend does **not** silently delete anything.
Instead, `POST /api/finalize` returns `409 snapshot_limit_reached`. The frontend
shows a confirmation dialog:

> "Je hebt het maximum van 10 opgeslagen versies bereikt. Als je doorgaat, worden
> alle oudere versies verwijderd. Je huidige laatste versie blijft bewaard."

If the user confirms (re-sends with `confirm_prune: true`), the backend:
1. Lists all version directories, sorted oldest-first.
2. Deletes all except the current latest (`snapshot_version`).
3. Proceeds to create the new snapshot as normal.

Result: the user ends up with 2 versions — their previous latest and the new one.
This effectively resets the version history on their terms, with explicit consent.

No silent pruning ever occurs for the version cap. The inactive-account rule (Rule 2)
below is the only automatic deletion.

**Rule 2 — inactive account pruning** (`SNAPSHOT_INACTIVE_PRUNE_DAYS`, default 365):

For users whose `updated_at` is older than the threshold, only the
`user_approved_snapshot_version` and any versions created **after** it are kept.
All earlier versions are deleted. If `user_approved_snapshot_version` is NULL (the
user never explicitly finalized their card), no pruning occurs — their data is intact
until they either finalize or delete their account.

```python
VERSION_RE = re.compile(r'^\d{8}_\d{6}_\d+$')   # matches 20260501_143022_412381

def prune_inactive_user(profile: UserProfile) -> None:
    inactive_since = utcnow() - timedelta(days=SNAPSHOT_INACTIVE_PRUNE_DAYS)
    if profile.updated_at > inactive_since:
        return
    approved = profile.user_approved_snapshot_version
    if not approved:
        return   # never approved by user: do not touch
    user_dir = assert_safe_path(
        os.path.join(SNAPSHOT_ROOT, safe_username_dir(profile.username, profile.id))
    )
    # Filter to version directories only — excludes 'latest' symlink and any other entries
    versions = sorted(
        v for v in os.listdir(user_dir)
        if VERSION_RE.match(v) and not os.path.islink(os.path.join(user_dir, v))
    )
    for v in versions:
        if v < approved:
            shutil.rmtree(assert_safe_path(os.path.join(user_dir, v)))
```

### Storage monitoring

At the end of every admin export and during the daily maintenance task,
the total size of `SNAPSHOT_ROOT` is checked:

```python
def check_storage_warning() -> None:
    total_bytes = sum(
        f.stat().st_size
        for f in pathlib.Path(SNAPSHOT_ROOT).rglob('*')
        if f.is_file()
    )
    total_gb = total_bytes / (1024 ** 3)
    if total_gb >= SNAPSHOT_STORAGE_WARNING_GB:
        msg = f"STORAGE WARNING: snapshot dir is {total_gb:.2f} GB (limit {SNAPSHOT_STORAGE_WARNING_GB} GB)"
        admin_logger.warning(json.dumps({
            'ts': datetime.utcnow().isoformat(),
            'event': 'storage_warning',
            'snapshot_gb': round(total_gb, 2),
            'limit_gb': SNAPSHOT_STORAGE_WARNING_GB,
        }))
```

The `/admin` dashboard reads the latest storage figure from the log (or recomputes it
on page load) and displays a red banner if the threshold is exceeded. The admin can
trigger manual pruning from the dashboard or reduce `SNAPSHOT_MAX_VERSIONS_PER_USER`
in the config.

**Storage location:** `/data/project/wall-of-faces/` is the tool's persistent home
directory on Toolforge — survives pod restarts and redeployments.

**Account deletion:** `POST /api/delete-account` removes all DB rows for the user
and deletes `/data/project/wall-of-faces/snapshots/<username>/` in full, including
all versioned PDFs and downloaded images. Each user's images are stored in their own
directory, so there is no risk of affecting other users.

---

## Background Job — Data Gathering

### Pod restart recovery

On application startup, reset any profiles stuck in `gather_status = 'running'`
or `'queued'` back to `'idle'`. This runs inside `create_app()` with an explicit
application context — `@app.before_first_request` was removed in Flask 2.3 and
must not be used.

```python
def create_app():
    app = Flask(__name__)
    # ... configure extensions ...
    with app.app_context():
        recover_stale_jobs()
        start_coordinator(app)   # coordinator starts AFTER recovery
    return app

def recover_stale_jobs():
    db.session.query(UserProfile)\
        .filter(UserProfile.gather_status.in_(['running', 'queued']))\
        .update({'gather_status': 'idle', 'gather_progress': 0})
    db.session.query(GatherQueue)\
        .filter(GatherQueue.status.in_(['running', 'waiting']))\
        .delete()
    db.session.query(SnapshotQueue)\
        .filter(SnapshotQueue.status.in_(['running', 'waiting']))\
        .update({'status': 'error'})   # mark as error, not deleted — partial snapshots may exist
    db.session.commit()
```

`create_app()` also guards against the coordinator starting during CLI commands
(`flask db upgrade`, `flask db migrate`):

```python
def start_coordinator(app):
    import os
    if os.environ.get('FLASK_RUN_FROM_CLI'):
        return   # never start coordinator during migrations or CLI use
    t = threading.Thread(target=coordinator_loop, args=(app,), daemon=True)
    t.start()
```

Users whose gather was interrupted will see the "start gathering" prompt again
rather than a stuck progress bar.

---

### Approach: DB-backed queue + thread pool

No Redis or Celery required. A `GatherQueue` table acts as the queue.
A single coordinator thread (started at app startup) polls it every 2 seconds
and dispatches jobs to a `ThreadPoolExecutor(max_workers=N)`.

**`GatherQueue` table**

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| username | String(255) | Indexed |
| status | String(20) | `waiting` / `running` / `done` / `error` |
| created_at | DateTime | UTC — used to determine queue position |
| started_at | DateTime | Set when job is dispatched |
| finished_at | DateTime | Set on completion or error |

**Queue flow**

```
POST /api/gather:
    1. Cooldown check: if UserProfile.last_gathered_at is within GATHER_COOLDOWN_SECONDS,
       return 429 too_soon
    2. If UserProfile.gather_status in ('running', 'queued'): return current status
    3. Insert GatherQueue row (status='waiting')
    4. Set UserProfile.gather_status = 'queued', gather_progress = 0
    5. Return {status: 'queued', queue_position: N}

Coordinator loop (single thread, polls every 2 seconds):
    -- Atomic claim: single UPDATE; MySQL does not support RETURNING,
    -- so re-fetch immediately by most-recent started_at.
    try:
        claimed = db.session.execute(text("""
            UPDATE gather_queue SET status='running', started_at=NOW()
            WHERE id = (
                SELECT id FROM gather_queue
                WHERE status='waiting'
                ORDER BY created_at ASC LIMIT 1
            )
        """))
        db.session.commit()
        if claimed.rowcount == 0:
            db.session.remove()
            continue   # nothing waiting
        # Re-fetch the row we just claimed — ORDER BY started_at DESC is safe
        # because we just set started_at=NOW() and no other coordinator runs.
        row = db.session.execute(
            text("SELECT id, username FROM gather_queue WHERE status='running' ORDER BY started_at DESC LIMIT 1")
        ).fetchone()
        db.session.remove()   # release connection before dispatching
        future = executor.submit(run_gather_job, row.username, app)
        future.add_done_callback(lambda f, u=row.username: on_gather_done(f, u, app))
    except Exception as e:
        app.logger.error(f"Coordinator tick error: {e}", exc_info=True)
        db.session.remove()   # always release on error

    -- Same pattern applied to SnapshotQueue on each tick.
    -- GatherQueue jobs take priority over SnapshotQueue jobs.
    -- Max in-flight across both queues: MAX_WORKERS = 4
    --   (leaves 4 of the 8 uWSGI threads free for HTTP requests)

run_gather_job(username, app):
    with app.app_context():
        try:
            ...gather steps, each checking profile still exists...
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            raise
        finally:
            db.session.remove()

on_gather_done(future, username, app):
    with app.app_context():
        try:
            profile = UserProfile.query.filter_by(username=username).first()
            if not profile:
                return
            exc = future.exception()
            if exc:
                profile.gather_status = 'error'
                profile.gather_error = str(exc)[:500]
            else:
                profile.gather_status = 'done'
            profile.last_gathered_at = utcnow()
            db.session.commit()
        finally:
            db.session.remove()

GET /api/gather-status:
    raw_position = COUNT(*) WHERE status='waiting'
                   AND created_at < this_user's row created_at
    -- Cap the reported value to avoid leaking active user count:
    queue_position = min(raw_position, 5) if raw_position < 5 else "5+"
```

The uWSGI deployment uses `processes=1 threads=8`. `MAX_WORKERS = 4` for the
`ThreadPoolExecutor` — leaves 4 threads free for HTTP requests.

The coordinator loop never exits — top-level `try/except` catches all errors:

```python
def coordinator_loop(app):
    while True:
        try:
            _coordinator_tick(app)   # handles both GatherQueue and SnapshotQueue
        except Exception as e:
            app.logger.error(f"Coordinator error: {e}", exc_info=True)
        time.sleep(2)
```

**Daily maintenance** runs inside the coordinator loop once per calendar day
(tracked by a module-level `last_maintenance_date`):

```python
_last_maintenance_date = None

def _coordinator_tick(app):
    global _last_maintenance_date
    today = datetime.now(timezone.utc).date()
    if today != _last_maintenance_date:
        run_daily_maintenance(app)
        _last_maintenance_date = today
    # ... dispatch jobs ...
```

- The frontend polls `/api/gather-status` every 3 seconds
- Progress is written to `UserProfile.gather_progress` at each step
- `gather_error` (String 500) added to `UserProfile` to surface error detail to the user

### Gather steps and progress mapping

| Step | Progress |
|------|----------|
| Clear GatherCache for user | 0 → 5% |
| Fetch edit counts (replica) | 5 → 15% |
| Compute qualifying wikis | 15 → 20% |
| Fetch avatar candidates (userpages, Commons category, Wikidata) | 20 → 35% |
| Fetch userpages + wikitext | 35 → 55% |
| Detect barnstars (userpage + talkpage + archives) | 55 → 70% |
| Run LLM extraction (userboxes + proud_of) | 70 → 85% |
| Write cache, set status = done | 95 → 100% |

---

## Key Algorithms

### Avatar candidate discovery

Four sources are tried in order. All results are pooled as buffet suggestions.
If the user is unhappy with all candidates, a Commons filename search input is shown.

```
1. Userpage images
   → prop=images on User:USERNAME for each qualifying wiki
   → surfaces any image embedded on the userpage

2. Commons category
   → search for Category:Photographs by USERNAME (and common variants)
     via the Commons API (action=query&list=categorymembers)
   → surfaces up to 10 images from that category

3. Wikidata item
   → check if User:USERNAME on HOME_WIKI has a linked Wikidata item
     (via prop=pageprops&ppprop=wikibase_item)
   → if found, fetch P18 (image) from the Wikidata item via the Wikidata API
   → surfaces the P18 image as a candidate

4. Manual fallback
   → shown when user rejects all candidates or no candidates were found
   → free-text Commons filename input + search link to Commons
```

---

### Qualifying wikis

```python
qualifying = [wiki for wiki in user_wikis
              if wiki.editcount >= user_wikis[HOME_WIKI].editcount * 0.75]
qualifying = [HOME_WIKI] + qualifying  # HOME_WIKI always included, deduped
```

### Barnstar detection

Scan wikitext of: `User:USERNAME`, `Overleg_gebruiker:USERNAME`, and all subpages
of the talk page (discovered via `list=allpages` with prefix, not assumed by name).

Match any `Bestand:<filename>` against the barnstar registry in `src/barnstars.py`.
Registry is a dict: `{filename_lowercase: display_name}`. Matching is case-insensitive.

### LLM extraction (Mistral)

Two separate prompts are run per qualifying wiki userpage. Both treat wikitext as
untrusted input (wrapped in `<wikitext>` delimiters). Output is validated against
a strict schema before being stored in `GatherCache`. Calls that fail or time out
produce zero suggestions for that wiki — they do not fail the whole gather.

**Prompt 1 — userbox-equivalent suggestions:**
```
Given the following Wikipedia userpage wikitext, extract facts about this user
that could appear as a userbox — e.g. spoken languages, areas of interest,
professional roles, tools they use, communities they belong to.
Return a JSON array of objects: [{"icon": "...", "label": "..."}].
Do not invent facts not present in the text.

<wikitext>
{wikitext}
</wikitext>
```

**Prompt 2 — proud_of candidates:**
```
Given the following Wikipedia userpage wikitext, identify any Wikipedia articles,
Commons images, or Wikimedia contributions that this user mentions with pride
or presents as notable personal work.
Return a JSON array of objects: [{"title": "...", "type": "article|image|other"}].
Only include items explicitly mentioned in the text. Do not invent.

<wikitext>
{wikitext}
</wikitext>
```

Output validation: both prompts must return a JSON array. Non-array responses,
malformed objects, or excessively long field values are discarded silently.

**Buffet UI for proud_of:**
In addition to LLM-surfaced candidates, the buffet includes a direct input field:
*"Welke artikelen, afbeeldingen of bijdragen ben je trots op?"*
User types a title; the app resolves it via the MediaWiki API and adds it as a
buffet item. This allows users to add things not mentioned on their userpage.

### PDF rendering

`src/render.py` exposes `render_card(profile: UserProfile) -> bytes`.
Uses WeasyPrint to render the card HTML template with profile data to PDF.
Both the web UI (`/card` → print) and `generate_pdfs.py` call this function.
Output is always 148mm × 210mm A5 portrait.

---

## External Integrations

### Schema management

There is no `/init-db` HTTP route. Schema creation and migrations are run once
via CLI through `kubectl exec` — never exposed over HTTP:

```bash
kubectl exec -it <pod> -- flask db upgrade
kubectl exec -it <pod> -- python3 -c "from app import create_app, db; app = create_app(); app.app_context().push(); db.create_all()"
```

The first command applies Alembic migrations (creates `UserProfile`, `GatherCache`,
`GatherQueue`). The second ensures `flask_sessions` exists — Flask-Session creates
it via `db.create_all()`, which Alembic does not manage. On subsequent deploys only
`flask db upgrade` is needed; `flask_sessions` persists across deploys.

---

### generate_pdfs.py — application context

`generate_pdfs.py` sets `WALL_OF_FACES_CLI=1` before importing from `app`,
preventing the coordinator thread from starting during the batch run:

```python
import os
os.environ['WALL_OF_FACES_CLI'] = '1'   # must be set before `from app import create_app`

from app import create_app

app = create_app()
with app.app_context():
    with app.test_request_context('/', base_url=app.config['SERVER_NAME_URL']):
        main()
```

`SERVER_NAME_URL` (e.g. `https://tools.wmcloud.org/wall-of-faces`) is set in the
config block. Using `base_url=` in `test_request_context` ensures `url_for(...,
_external=True)` produces correct absolute URLs rather than `http://localhost/...`.
All static assets in `card_pdf.html` use `url_for('static', filename='...')` which
resolves to `file:///...` paths via a custom static URL resolver for PDF builds.

`db.session.remove()` is called between user iterations to avoid accumulating
session state across the full batch:

```python
for profile in UserProfile.query.all():
    try:
        render_and_export(profile)
    finally:
        db.session.remove()
```

`start_coordinator` checks `WALL_OF_FACES_CLI`:

```python
def start_coordinator(app):
    if os.environ.get('WALL_OF_FACES_CLI') or os.environ.get('FLASK_RUN_FROM_CLI'):
        return
    ...
```

---

### Toolforge Replica DB connections

`~/replica.my.cnf` is a MySQL option file, not a DSN. Connection strings are
constructed as follows in `src/replicas.py`:

```python
import configparser, os
from sqlalchemy import create_engine

def _replica_engine(db_name: str):
    cfg = configparser.ConfigParser()
    cfg.read(os.path.expanduser('~/replica.my.cnf'))
    user     = cfg['client']['user']
    password = cfg['client']['password']
    host     = f"{db_name.replace('_p', '')}.web.db.svc.wikimedia.cloud"
    return create_engine(
        f"mysql+pymysql://{user}:{password}@{host}/{db_name}",
        pool_recycle=300,    # Toolforge closes idle connections aggressively
        pool_pre_ping=True,  # detect stale connections before use
    )

_centralauth = _replica_engine('centralauth_p')
_commonswiki  = _replica_engine('commonswiki_p')
```

`pool_recycle=300` and `pool_pre_ping=True` are required — Toolforge MySQL
closes idle TCP connections after a few minutes.

---

### Wikimedia OAuth 2.0

Authorization code flow with PKCE (S256). No extra library — implemented with
`requests` + stdlib `secrets` / `hashlib` / `base64`.

```
1. GET /login
   → generate code_verifier + code_challenge (S256), state
   → store in session
   → redirect to meta.wikimedia.org/w/rest.php/oauth2/authorize

2. User approves  →  GET /oauth-callback
   → verify state (CSRF protection)
   → POST oauth2/access_token with code + code_verifier
   → GET oauth2/resource/profile  →  username

3. Fetch signature immediately while access token is available
   (authenticated API call with Authorization: Bearer <token>)

4. Store username + signature_html in session / UserProfile
5. Token is not persisted — discarded after signature fetch
```

Signatures are the only data requiring an authenticated call. All other gather
steps are public and run in the background job without a token.

### Toolforge Replica DBs

Connection via `~/replica.my.cnf` (standard Toolforge credential file).
Two connections: `centralauth_p` and `commonswiki_p`.
Used only for counts — never for content.

### MediaWiki API

Signature fetch uses `Authorization: Bearer <access_token>`.
Avatar and wikitext fetching is unauthenticated (public API).
User-Agent: `WallOfFaces/1.0 (Toolforge; nl.wikipedia.org)`

### Mistral API

Outbound HTTPS from Toolforge is permitted.
API key stored as environment variable `MISTRAL_API_KEY`.
Model: `mistral-small` (cheapest tier sufficient for structured extraction).
One call per qualifying wiki userpage, batched where possible.

---

## i18n

`UI_LANGUAGE` is a deployment-level setting — one language per deployment.
Each community fork sets its own value in the config block and ships a matching
strings file. There is no per-user language switching.

`src/i18n.py` loads `strings/<UI_LANGUAGE>.yml` once at startup into a module-level
dict. Jinja2 templates call `{{ t('key') }}` via a context processor that injects
the `t` function. No hardcoded strings in templates. Missing keys fall back to the
key name itself so a partially translated strings file degrades gracefully.

Example `strings/nl.yml`:
```yaml
nav.logout: Uitloggen
buffet.identity: Identiteit
buffet.achievements: Prestaties
buffet.biography: Biografie
card.proud_of: Trots op
card.member_since: Lid sinds
card.home_base: Thuisbasis
card.extended_rights: Uitgebreide rechten
gather.progress: Gegevens ophalen…
gather.queue_position: "Je staat op positie {n} in de wachtrij"
deadline.notice: "Je kaart is gedownload voor verwerking. Latere wijzigingen worden mogelijk niet afgedrukt."
```

---

## Admin Access

Admin routes (`/admin`, `/admin/export`, `/admin/mark-processed`) are protected by
checking `session['username']` against an `ADMIN_USERS` list in the config block.

```python
ADMIN_USERS = ["YourWikimediaUsername"]
```

---

## Card Overflow Detection

Overflow is checked in two places: the live browser preview and the PDF renderer.

### Browser preview (frontend)
A `ResizeObserver` watches each zone (identity, extra sections, trots op, achievements)
and the card container. When the total content height exceeds the A5 card height
(210mm at screen DPI), a warning banner appears at the top of the preview:

> ⚠️ Je kaart is te vol — verwijder of verkort inhoud totdat alles past.

Individual sections that overflow their natural zone are highlighted with a red border.
The warning is non-blocking — the user can still save, but cannot finalize until
the overflow is resolved.

### PDF renderer (backend)
After WeasyPrint renders the card, check the output PDF page count.
If the result has more than 1 page, the snapshot is rejected:

```python
pdf_bytes = render_card(profile)
reader = PdfReader(io.BytesIO(pdf_bytes))
if len(reader.pages) > 1:
    raise CardOverflowError("Card content exceeds A5 — snapshot rejected")
```

`CardOverflowError` is caught by `POST /api/finalize` and returned as a `422`
response — it does **not** touch `gather_status` (the gather is already `done`).
The user is notified to trim content and try again.

---

## Security Design

### C2 — Filesystem path safety

All snapshot paths are constructed from the `username` field. Before any filesystem
operation in `snapshot.py`, the username is sanitised and all resulting paths are
asserted to start with the expected base directory.

```python
import re, os

SNAPSHOT_ROOT = "/data/project/wall-of-faces/snapshots"

def safe_username_dir(username: str, user_id: int) -> str:
    """Return a filesystem-safe directory name for the username.
    Appending the DB user_id makes the directory name globally unique
    even when two usernames produce the same cleaned string (e.g. 'User A'
    and 'User_A' both clean to 'User_A')."""
    clean = re.sub(r'[^\w\-]', '_', username)   # no dots — avoids NFS/module path issues
    if not clean or clean.startswith('_'):
        clean = f"user_{user_id}"
    return f"{clean}_{user_id}"

def assert_safe_path(path: str) -> str:
    """Raise if the resolved path escapes the snapshot root."""
    resolved = os.path.realpath(path)
    if not resolved.startswith(os.path.realpath(SNAPSHOT_ROOT) + os.sep):
        raise ValueError(f"Path escapes snapshot root: {path!r}")
    return resolved
```

Every call to `os.makedirs`, `shutil.rmtree`, or file open in `snapshot.py` uses
paths built via `safe_username_dir()` and checked with `assert_safe_path()`.

Snapshot directory permissions are set to `0o700` on creation (see M6).

---

### H1 — Signature HTML sanitisation (XSS)

`signature_html` is fetched from the MediaWiki API as rendered HTML and stored in
`UserProfile`. Before storage, it is sanitised with `nh3` (the maintained successor
to the deprecated `bleach` library — uses the Rust `ammonia` crate):

```python
import nh3

SIGNATURE_ALLOWED_TAGS = {'a', 'b', 'i', 'span', 'sup', 'sub', 'abbr'}
SIGNATURE_ALLOWED_ATTRS = {
    'a':    {'href', 'title'},
    'span': {'style'},
    'abbr': {'title'},
}

def sanitise_signature(html: str) -> str:
    return nh3.clean(
        html,
        tags=SIGNATURE_ALLOWED_TAGS,
        attributes=SIGNATURE_ALLOWED_ATTRS,
        strip_comments=True,
    )
```

`signature_html` is then rendered in templates with `{{ profile.signature_html }}` —
Jinja2 auto-escapes by default. The one intentional raw render in the card preview
uses a template filter that only passes the nh3-sanitised value.

---

### H2 — LLM output sanitisation (XSS)

LLM responses (Mistral) are treated as untrusted. Before any LLM output is stored
in `GatherCache.payload`:

1. The response must be valid JSON (decoded with `json.loads` — no `eval`).
2. Each field value is passed through `html.escape()` to prevent stored XSS.
3. Field values exceeding a per-field character limit are discarded.

```python
import html, json

MAX_LABEL_LEN = 100
MAX_TITLE_LEN = 200

def sanitise_llm_userbox(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    label = item.get('label', '')
    if not isinstance(label, str) or len(label) > MAX_LABEL_LEN:
        return None
    return {'icon': html.escape(item.get('icon', '')[:20]),
            'label': html.escape(label)}

def sanitise_llm_proud_of(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    title = item.get('title', '')
    if not isinstance(title, str) or len(title) > MAX_TITLE_LEN:
        return None
    return {'title': html.escape(title),
            'type': item.get('type', 'other') if item.get('type') in ('article', 'image', 'other') else 'other'}
```

---

### H4 — CSRF protection

`Flask-WTF`'s `CSRFProtect` is applied globally:

```python
from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect(app)
```

All API endpoints that mutate state (`POST /api/gather`, `POST /api/save-profile`,
`POST /api/delete-account`, `POST /admin/snapshot`) require the CSRF token.

**CSRF cookie is intentionally not HttpOnly.** Flask-WTF sets a separate CSRF
cookie (distinct from the session cookie) that JavaScript must be able to read in
order to echo it back as a request header. The session cookie remains `HttpOnly`.
Configuration:

```python
app.config.update(
    WTF_CSRF_ENABLED=True,
    WTF_CSRF_HEADERS=['X-CSRFToken'],
    # The CSRF cookie is NOT HttpOnly — JS must read it
    # SESSION_COOKIE_HTTPONLY=True still applies to the session cookie only
)
```

The JS frontend reads the CSRF cookie and sets the header on every XHR:

```javascript
function getCsrfToken() {
    return document.cookie.split('; ')
        .find(r => r.startsWith('csrf_token='))
        ?.split('=')[1];
}
fetch('/api/save-profile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(payload),
});
```

---

### H5 — Rate limiting

`Flask-Limiter` is applied on the routes most at risk:

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"])
```

Per-endpoint overrides:

| Route | Limit |
|-------|-------|
| `POST /api/gather` | 5 per hour per user (DB-level cooldown also applies) |
| `POST /login` | 20 per hour per IP |
| `GET /api/resolve-image` | 60 per minute per user |
| `POST /admin/snapshot` | 10 per minute per IP |

The DB-level cooldown on `/api/gather` (returned as `429 too_soon`) acts as a
second layer independent of Flask-Limiter.

---

### H6 — Ownership enforcement

On every write or delete endpoint, the target username is **always** derived from
`session['username']` — never from the request body or query parameters.

```python
@app.post('/api/save-profile')
@login_required
def save_profile():
    username = session['username']   # never: request.json.get('username')
    profile = UserProfile.query.filter_by(username=username).first_or_404()
    ...
```

The admin-only endpoints (`/admin/snapshot`, `/admin/export`) derive their target
from the request body, but are gated behind the `ADMIN_USERS` check before any
DB write occurs.

---

### H7 — Image fetch validation (SSRF / disk exhaustion)

In `snapshot.py`, images are downloaded with strict validation before writing to disk:

```python
import requests
from urllib.parse import urlparse

ALLOWED_IMAGE_HOST = "upload.wikimedia.org"
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB per image

def fetch_image(url: str, dest_path: str) -> None:
    parsed = urlparse(url)
    if parsed.hostname != ALLOWED_IMAGE_HOST:
        raise ValueError(f"Disallowed image host: {parsed.hostname}")
    with requests.get(url, stream=True, timeout=15) as resp:
        resp.raise_for_status()
        received = 0
        with open(dest_path, 'wb') as f:
            for chunk in resp.iter_content(8192):
                received += len(chunk)
                if received > MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds size limit")
                f.write(chunk)
```

All image URLs are resolved via the MediaWiki API (`prop=imageinfo`) before this
call — only `upload.wikimedia.org` URLs are produced by that API.

---

### H8 — WeasyPrint SSRF (custom url_fetcher)

WeasyPrint is never given user-controlled HTML directly. It is given the card
template rendered with escaped data. As an additional layer, a custom `url_fetcher`
restricts WeasyPrint to local files and the CDN paths used by the card template:

```python
from weasyprint import HTML, default_url_fetcher

ALLOWED_URL_PREFIXES = (
    "file:///",
    "https://upload.wikimedia.org/",
)

def safe_url_fetcher(url, **kwargs):
    if not any(url.startswith(p) for p in ALLOWED_URL_PREFIXES):
        raise ValueError(f"WeasyPrint blocked URL: {url}")
    return default_url_fetcher(url, **kwargs)

def render_card(profile: UserProfile) -> bytes:
    html = render_template('card_pdf.html', profile=profile)
    return HTML(string=html, url_fetcher=safe_url_fetcher).write_pdf()
```

At snapshot time, images are already downloaded locally (see Snapshot section),
so WeasyPrint only needs `file://` access during PDF render.

---

### H9 — Flask SECRET_KEY

`SECRET_KEY` is never hard-coded or loaded from a configmap. It is mounted from
a Kubernetes Secret as an environment variable:

```bash
kubectl create secret generic wall-of-faces-secrets \
  --from-literal=SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

In `app.py`:
```python
app.config['SECRET_KEY'] = os.environ['SECRET_KEY']  # raises if absent
```

Startup fails immediately if `SECRET_KEY` is not set — no silent fallback.

---

### H10 / N-H6 — SVG images: always request PNG thumbnails from Commons

The previous approach (cairosvg pre-conversion) moves the SVG parsing attack
surface from WeasyPrint to cairosvg, which has the same XXE/SSRF exposure.

The correct fix is to **never download SVG files at all**. The Wikimedia Commons
thumbnail API rasterises any file format — including SVG — when you request a
thumbnail at a specific pixel width. `prop=imageinfo&iiprop=url&iiurlwidth=N` on
an SVG file always returns a PNG URL, never an SVG URL.

```python
# In snapshot.py — resolve all filenames to thumbnail URLs before downloading
def resolve_thumbnail_url(filename: str, width: int) -> str:
    resp = mw_api('commons', action='query', titles=f'File:{filename}',
                  prop='imageinfo', iiprop='url', iiurlwidth=width)
    url = resp['pages'][...]['imageinfo'][0]['thumburl']
    # thumburl is always a rasterised PNG from Commons — never the original SVG
    assert url.startswith('https://upload.wikimedia.org/')
    return url
```

`cairosvg` is removed from the dependency list entirely. WeasyPrint only ever
receives local PNG files. No SVG content is processed anywhere in the stack.

---

### M1 — SQL injection (replica DBs)

All queries in `src/replicas.py` use SQLAlchemy's parameterised form — no string
formatting or f-strings in query text:

```python
# Correct — parameterised
result = conn.execute(
    text("SELECT lu_wiki, lu_editcount FROM localuser WHERE lu_name = :username"),
    {"username": username}
)

# Never this
result = conn.execute(f"... WHERE lu_name = '{username}'")  # BANNED
```

Raw `text()` queries are permitted only in `replicas.py`. All `UserProfile` and
`GatherCache` access goes through SQLAlchemy ORM only.

---

### M2 — Session cookie flags

```python
app.config.update(
    SESSION_COOKIE_SECURE=True,       # HTTPS only
    SESSION_COOKIE_HTTPONLY=True,     # No JS access
    SESSION_COOKIE_SAMESITE='Lax',    # CSRF mitigation
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)
```

---

### M3 — Server-side sessions

Client-side Flask sessions cannot be revoked at logout. Sessions are stored
server-side using `Flask-Session` backed by the ToolsDB MySQL connection:

```python
from flask_session import Session

app.config.update(
    SESSION_TYPE='sqlalchemy',
    SESSION_SQLALCHEMY=db,
    SESSION_SQLALCHEMY_TABLE='flask_sessions',
)
Session(app)
```

`session.clear()` alone does not delete the server-side row — it only clears
the in-memory dict. To actually invalidate the session at logout:

```python
@app.get('/logout')
def logout():
    # Delete the server-side row explicitly before clearing the session
    sid = session.sid  # Flask-Session exposes the session ID
    db.session.execute(
        text("DELETE FROM flask_sessions WHERE session_id = :sid"),
        {"sid": sid}
    )
    db.session.commit()
    session.clear()
    return redirect('/')
```

This makes the session immediately invalid even if the cookie is captured.

---

### M4 — GatherCache TTL + cascade delete

`GatherCache` has a cascade foreign key to `UserProfile` so rows are deleted
automatically when the user account is deleted:

```python
class GatherCache(db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey('user_profile.id', ondelete='CASCADE'))
```

A background cleanup task runs daily (or on pod startup) to remove `GatherCache`
rows older than 30 days where `gather_status = 'done'`:

```python
def run_daily_maintenance(app):
    with app.app_context():
        try:
            cutoff = utcnow() - timedelta(days=30)
            # Only purge cache for completed gathers — not active or queued
            completed_user_ids = db.session.scalars(
                select(UserProfile.id).where(UserProfile.gather_status == 'done')
            ).all()
            GatherCache.query.filter(
                GatherCache.gathered_at < cutoff,
                GatherCache.user_id.in_(completed_user_ids)
            ).delete(synchronize_session=False)
            # Purge expired Flask-Session rows
            db.session.execute(text("DELETE FROM flask_sessions WHERE expiry < NOW()"))
            # Inactive snapshot pruning
            inactive_cutoff = utcnow() - timedelta(days=SNAPSHOT_INACTIVE_PRUNE_DAYS)
            for profile in UserProfile.query.filter(UserProfile.updated_at < inactive_cutoff):
                prune_inactive_user(profile)
            db.session.commit()
        finally:
            db.session.remove()
```

---

### M5 — YAML safe loading

`src/i18n.py` uses `yaml.safe_load()` — never `yaml.load()`:

```python
with open(strings_path) as f:
    strings = yaml.safe_load(f)   # not yaml.load()
```

---

### M6 — Snapshot directory permissions

All snapshot directories are created with `mode=0o700`:

```python
os.makedirs(version_dir, mode=0o700, exist_ok=False)
```

This prevents other Toolforge users from reading snapshot files via the shared NFS.

---

### M7 — Mistral data residency

The Mistral EU API endpoint is used to keep data within the EU for GDPR compliance:

```python
MISTRAL_API_BASE = "https://api.mistral.ai"   # EU-hosted endpoint
```

The privacy statement notes that userpage wikitext is sent to Mistral for
processing, is subject to Mistral's data retention policy, and users can avoid this
by not triggering data gathering.

---

### M8 — MISTRAL_API_KEY as Kubernetes Secret

`MISTRAL_API_KEY` is mounted as a file from a Kubernetes Secret — never in a
configmap:

```bash
kubectl create secret generic wall-of-faces-secrets \
  --from-literal=MISTRAL_API_KEY="..."
```

In `app.py`:
```python
MISTRAL_API_KEY = os.environ['MISTRAL_API_KEY']   # raises if absent
```

---

### M9 — `/api/resolve-image` input validation

The `filename` parameter is validated against a strict pattern before being passed
to the MediaWiki API, preventing open-proxy abuse:

```python
import re

COMMONS_FILENAME_RE = re.compile(r'^[\w\-. ()\[\]]+\.(jpg|jpeg|png|gif|svg|webp)$', re.IGNORECASE)

@app.get('/api/resolve-image')
@login_required
def resolve_image():
    filename = request.args.get('filename', '')
    if not COMMONS_FILENAME_RE.match(filename):
        return jsonify(error='invalid_filename'), 400
    ...
```

---

### M10 — extra_sections / userboxes at render time

User-supplied fields (`extra_sections`, `userboxes`, `biography`, etc.) are stored
as-is in the DB but are **always** rendered through Jinja2's auto-escaping.
The `| safe` filter is never used on user-controlled data. All template variables
that contain user content use `{{ variable }}` (escaped), never `{{ variable | safe }}`.

---

### A1 — WeasyPrint render timing

WeasyPrint renders happen at snapshot time (background job, triggered by user
finalization or admin export) — **never** on-demand per HTTP request. The `/card`
route returns HTML rendered in the browser; PDF rendering only happens in
`snapshot.py` and `generate_pdfs.py`. This prevents per-request memory spikes.

---

### N-P3 — Flask-Session expired row cleanup

A daily maintenance task deletes expired session rows alongside the GatherCache purge:

```python
def daily_maintenance(app):
    with app.app_context():
        db.session.execute(text("DELETE FROM flask_sessions WHERE expiry < NOW()"))
        GatherCache.query.filter(GatherCache.gathered_at < cutoff).delete()
        db.session.commit()
```

---

### N-P4 — Replica-counts-only enforcement

`src/replicas.py` is architecturally constrained to return only scalar values
(integers, dates). All functions must have return types annotated as `int`, `datetime`,
or `dict[str, int]`. Any function returning a string or list is a code review blocker.
An integration test asserts that no `GatherCache` row has `source='replica'` combined
with a non-numeric payload.

---

### N-P5 — Access logs and privacy statement

uWSGI access logs are configured to log only the HTTP method, path, and response
code — **not** the IP address or User-Agent. In `uwsgi.ini`:

```ini
log-format = %(method) %(uri) %(status)
```

The privacy statement notes that server-side session identifiers are stored in
ToolsDB for the session lifetime (7 days), and that no IP addresses are logged.

---

### N-H2 — Admin account hardening

`ADMIN_USERS` is loaded from the `ADMIN_USERS` environment variable (Kubernetes
Secret), not hardcoded in `app.py`:

```python
ADMIN_USERS = os.environ.get('ADMIN_USERS', '').split(',')
```

This allows rotation without redeployment. Additionally:
- Admin routes check that the session was established within the last 30 minutes
  before allowing export actions (re-auth prompt otherwise).
- The privacy statement and admin documentation require that the admin Wikimedia
  account has 2FA enabled.
- All admin actions are logged to the admin audit log (L4).

---

### N-H3 — Delete-account race with running gather

`POST /api/delete-account` checks for an active gather job before deleting:

```python
def delete_account(username):
    profile = UserProfile.query.filter_by(username=username).first_or_404()
    if profile.gather_status in ('running', 'queued'):
        return jsonify(error='gather_in_progress',
                       detail='A data gather is running. Wait for it to finish or try again shortly.'), 409
    # Safe to delete
    GatherCache.query.filter_by(user_id=profile.id).delete()
    shutil.rmtree(assert_safe_path(snapshot_dir(username)), ignore_errors=True)
    db.session.delete(profile)
    db.session.commit()
```

The gather worker checks at each step whether the profile still exists:

```python
profile = UserProfile.query.filter_by(username=username).first()
if not profile:
    return   # account deleted mid-gather; exit cleanly
```

---

### N-H7 — WeasyPrint url_fetcher: file:// only at render time

The `safe_url_fetcher` is tightened for the PDF render path. At snapshot time,
all images are already local, so only `file://` URIs are needed:

```python
ALLOWED_URL_PREFIXES = ("file:///",)   # upload.wikimedia.org removed

def safe_url_fetcher(url, **kwargs):
    if not url.startswith("file:///"):
        raise ValueError(f"WeasyPrint blocked URL: {url}")
    return default_url_fetcher(url, **kwargs)
```

---

### N-H8 — OAuth 2.0 state + PKCE binding

`/login` generates a random `state` (CSRF token) and a `code_verifier` + `code_challenge`
(PKCE S256), stores both in the Flask session, and includes `state` and
`code_challenge` in the authorization redirect. At callback, `state` is verified
before the code exchange, and `code_verifier` is included in the token request.
This prevents CSRF and authorization code interception attacks.

---

### N-M1 — Server-side field length limits

See the data model section. All limits are enforced in `POST /api/save-profile`
before any DB write. Violations return `422` with a field-level error.

---

### N-M2 — Gather cooldown on error

The cooldown check uses `UserProfile.last_gathered_at`, which is set on both
`done` and `error` outcomes (see coordinator `on_gather_done`). A user whose
gather errored must wait the full `GATHER_COOLDOWN_SECONDS` before re-trying,
preventing repeated Mistral calls on rapid retry loops.

---

### N-M3 — fetch_image redirect safety

```python
def fetch_image(url: str, dest_path: str) -> None:
    parsed = urlparse(url)
    if parsed.hostname != ALLOWED_IMAGE_HOST:
        raise ValueError(f"Disallowed host: {parsed.hostname}")
    with requests.get(url, stream=True, timeout=5,
                      allow_redirects=False) as resp:
        if resp.is_redirect:
            redirect_url = resp.headers.get('Location', '')
            if not urlparse(redirect_url).hostname == ALLOWED_IMAGE_HOST:
                raise ValueError(f"Redirect to disallowed host: {redirect_url}")
            # Follow the single redirect manually with the same validation
            return fetch_image(redirect_url, dest_path)
        resp.raise_for_status()
        received = 0
        with open(dest_path, 'wb', opener=lambda p, f: os.open(p, f, 0o600)) as fh:
            for chunk in resp.iter_content(8192):
                received += len(chunk)
                if received > MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds size limit")
                fh.write(chunk)
```

Timeout reduced to 5 seconds per image (CDN-hosted images). Files are written
with `0o600` permissions (N-T3 umask fix).

---

### N-A1 — Fake barnstar injection via talk page (known limitation)

Any Wikimedia user can add content to another user's talk page, including barnstar
templates. The buffet will surface these as suggestions; the victim user must still
accept them. This is documented as a known limitation. Each barnstar suggestion in
the buffet UI displays its source page (e.g. "Overleg gebruiker:X") so the user
can verify provenance before accepting.

---

### N-A2 — Mistral API usage cap

`GATHER_MAX_QUALIFYING_WIKIS = 5` caps the number of wikis fed to the LLM per
gather (two prompts × max 5 wikis = 10 Mistral calls maximum per gather run).
Key rotation procedure: update the `wall-of-faces-secrets` Kubernetes Secret and
roll the pod. The admin documentation describes this procedure.

---

### N-A3 — WeasyPrint OOM/hang via crafted content

Three defences:

1. **Server-side field limits** (N-M1) cap biography, extra_sections, and proud_of
   before content reaches the renderer.
2. **Whitespace normalisation** — all text fields are passed through
   `re.sub(r'\s+', ' ', value.strip())` before storage, preventing unbroken strings.
3. **Subprocess render with timeout** — `render_card()` runs WeasyPrint in a
   subprocess via `subprocess.run(..., timeout=60)`. If the render exceeds 60 seconds
   the subprocess is killed and `CardOverflowError` is raised.

```python
import subprocess, tempfile

def render_card(profile: UserProfile) -> bytes:
    html = render_card_html(profile)   # template rendering, no WeasyPrint
    with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
        f.write(html.encode())
        html_path = f.name
    pdf_path = html_path.replace('.html', '.pdf')
    try:
        subprocess.run(
            ['python3', '-m', 'weasyprint', html_path, pdf_path],
            timeout=60, check=True, capture_output=True
        )
        return open(pdf_path, 'rb').read()
    finally:
        os.unlink(html_path)
        if os.path.exists(pdf_path):
            os.unlink(pdf_path)
```

---

### N-A5 — Admin username normalisation

Wikimedia usernames are stored and compared in their canonical form: first letter
uppercase, remainder as provided by `handshaker.identify()`. The `ADMIN_USERS`
list entries must use the exact canonical form. The admin check normalises both
sides before comparison:

```python
def is_admin(username: str) -> bool:
    normalised = username[0].upper() + username[1:] if username else ''
    return normalised in [u[0].upper() + u[1:] for u in ADMIN_USERS]
```

---

### N-T1 — replica.my.cnf blast radius (documentation)

`~/replica.my.cnf` grants read access to all Toolforge replica DBs, not just
the two this tool uses. This is a Toolforge infrastructure constraint and cannot
be mitigated at the application level. It is documented in the security model:
a compromise of this tool's code gives read access to all replicas accessible
by the tool's Toolforge account. Mitigation: keep the attack surface small —
no user-controlled input ever reaches `replicas.py` query construction.

---

### N-T2 — Single coordinator thread per process

A module-level `threading.Event` ensures only one coordinator thread runs per
process, even if `create_app()` is called multiple times (e.g. during testing
or hot-reload):

```python
_coordinator_started = threading.Event()

def start_coordinator(app):
    if _coordinator_started.is_set():
        return
    _coordinator_started.set()
    t = threading.Thread(target=coordinator_loop, args=(app,), daemon=True)
    t.start()
```

`uwsgi.ini` sets `py-autoreload = 0` in production.

---

### N-T3 — File permissions and umask

Application startup sets `os.umask(0o077)` so all files created without an
explicit mode default to `0o600` (owner read/write only). Snapshot directories
use `os.makedirs(path, mode=0o700)`. Image files use `open(..., opener=...)`
with `0o600` (see N-M3 snippet above).

---

### N-T4 — generate_pdfs.py audit logging

`generate_pdfs.py` writes to the same admin audit log on every run:

```python
admin_logger.info(json.dumps({
    'ts': datetime.utcnow().isoformat(),
    'event': 'generate_pdfs',
    'mode': 'rerender' if args.rerender else 'latest',
    'output': str(args.output),
    'count': len(profiles_exported),
}))
```

---

### N-T5 — Kubernetes Secrets as files, not env vars

All secrets (`SECRET_KEY`, `MISTRAL_API_KEY`, `OAUTH_CLIENT_SECRET`,
`OAUTH_REDIRECT_URI`, `ADMIN_USERS`) are mounted as files via a Kubernetes Secret
volume rather than environment variables. This prevents exposure via `/proc/<pid>/environ`.

```yaml
volumes:
  - name: app-secrets
    secret:
      secretName: wall-of-faces-secrets
      defaultMode: 0400
volumeMounts:
  - name: app-secrets
    mountPath: /run/secrets/wall-of-faces
    readOnly: true
```

`app.py` reads them from files:
```python
def read_secret(name: str) -> str:
    return open(f'/run/secrets/wall-of-faces/{name}').read().strip()

SECRET_KEY           = read_secret('SECRET_KEY')
MISTRAL_API_KEY      = read_secret('MISTRAL_API_KEY')
```

---

### Flask deployment — uWSGI configuration

`uwsgi.ini` must specify single-process multi-thread mode to avoid coordinator
races and rate-limiter inconsistency:

```ini
[uwsgi]
processes = 1
threads   = 8
py-autoreload = 0
```

Flask-Limiter is configured to use ToolsDB as its storage backend so rate limits
survive pod restarts (and would be consistent across processes if ever scaled):

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=app.config['SQLALCHEMY_DATABASE_URI'],
)
```

---

### F1 — WeasyPrint temp files: use TemporaryDirectory

`render_card()` uses a `TemporaryDirectory` context manager so all temp files
(HTML input, PDF output) are cleaned up on any exit path including `atexit`:

```python
import tempfile, subprocess

def render_card(profile: UserProfile) -> bytes:
    html = render_card_html(profile)   # pure Jinja2, no WeasyPrint
    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = os.path.join(tmpdir, 'card.html')
        pdf_path  = os.path.join(tmpdir, 'card.pdf')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        try:
            result = subprocess.run(
                ['python3', 'src/weasyprint_worker.py', html_path, pdf_path],
                timeout=60, check=True, capture_output=True
            )
        except subprocess.TimeoutExpired:
            raise CardOverflowError("PDF render timed out")
        except subprocess.CalledProcessError as e:
            app.logger.warning(f"WeasyPrint error: {e.stderr.decode(errors='replace')}")
            raise
        return open(pdf_path, 'rb').read()
    # TemporaryDirectory.__exit__ deletes tmpdir even if an exception is raised
```

`src/weasyprint_worker.py` applies the `safe_url_fetcher` (restricted to `file://`):

```python
# src/weasyprint_worker.py — called as a subprocess
import sys
from weasyprint import HTML, default_url_fetcher

def safe_url_fetcher(url, **kwargs):
    if not url.startswith('file:///'):
        raise ValueError(f"Blocked: {url}")
    return default_url_fetcher(url, **kwargs)

html_path, pdf_path = sys.argv[1], sys.argv[2]
HTML(filename=html_path, url_fetcher=safe_url_fetcher).write_pdf(pdf_path)
```

This restores the SSRF protection that was lost when switching to the CLI invocation.

---

### F2 — fetch_image: redirect depth cap and scheme enforcement

```python
def fetch_image(url: str, dest_path: str, _depth: int = 0) -> None:
    if _depth > 1:
        raise ValueError("Too many redirects fetching image")
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise ValueError(f"Only https allowed, got: {parsed.scheme}")
    if parsed.hostname != ALLOWED_IMAGE_HOST:
        raise ValueError(f"Disallowed host: {parsed.hostname}")
    with requests.get(url, stream=True, timeout=5, allow_redirects=False) as resp:
        if resp.is_redirect:
            return fetch_image(resp.headers['Location'], dest_path, _depth + 1)
        resp.raise_for_status()
        received = 0
        with open(dest_path, 'wb', opener=lambda p, f: os.open(p, f, 0o600)) as fh:
            for chunk in resp.iter_content(8192):
                received += len(chunk)
                if received > MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds size limit")
                fh.write(chunk)
```

---

### F4 — LLM consent revocation: immediate cache deletion

When `llm_consent` changes from `True` to `False` on re-gather, the API endpoint
deletes all `source='llm'` GatherCache rows for the user **before** queuing the
job — so the buffet immediately shows no LLM suggestions even if the new gather
is delayed in the queue:

```python
if profile.llm_consent and not new_consent:
    GatherCache.query.filter_by(user_id=profile.id, source='llm').delete()
profile.llm_consent = new_consent
db.session.commit()
# then enqueue the gather job
```

---

### F5 — confirm_prune: version echo prevents race

The `409 snapshot_limit_reached` response includes the specific version string
being kept. The `confirm_prune: true` request must echo it back; the backend
validates the match before pruning:

**409 response:**
```json
{
  "error": "snapshot_limit_reached",
  "keep_version": "20260501_143022_412381",
  "detail": "..."
}
```

**Confirmed request:**
```json
{ "confirm_prune": true, "keep_version": "20260501_143022_412381" }
```

If `keep_version` does not match the current `UserProfile.snapshot_version` when
the confirmation arrives, return a fresh `409` with the updated version — the user
sees the dialog again with the new current version.

---

### F6 — Session fixation: regenerate session ID at OAuth callback

After identity is confirmed, regenerate the session ID to prevent session fixation:

```python
@app.get('/oauth-callback')
def oauth_callback():
    # ... verify state, exchange code, fetch username ...
    old_sid = getattr(session, 'sid', None)
    if old_sid:
        db.session.execute(text("DELETE FROM sessions WHERE session_id = :sid"),
                           {"sid": old_sid})
        db.session.commit()
    session.clear()
    session['username'] = username   # Flask-Session creates a new session row
    return redirect('/')
```

---

### F7 — Queue position: cap reported value, add global depth limit

`GET /api/gather-status` caps the reported `queue_position` at 5 to avoid
leaking the total active user count:

```python
queue_position = min(raw_position, 5)   # returns 0–5; "5" means "5 or more"
```

A global maximum queue depth (`MAX_QUEUE_DEPTH = 20`) is enforced at
`POST /api/gather` — if there are already 20 waiting rows across all users,
return `503 queue_full` rather than accepting a new job.

---

### F9 — Signature HTML: remove `style` attribute, add CSP

`style` is removed from the `nh3` allowed attributes. Signatures support only
structural formatting (bold, italic, links, colour via explicit classes defined
in `card.html`'s stylesheet). A Content-Security-Policy header is added to all
page routes that blocks inline styles:

```python
@app.after_request
def add_security_headers(response):
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self'; "   # no 'unsafe-inline'
        "img-src 'self' https://upload.wikimedia.org data:;"
    )
    return response
```

Updated allowlist:
```python
SIGNATURE_ALLOWED_ATTRS = {
    'a':    {'href', 'title'},
    'abbr': {'title'},
    # 'span': {'style'} removed
}
```

---

### F10 — snapshot directory naming: user_id suffix prevents collisions

See `safe_username_dir(username, user_id)` in the C2 section above. The `user_id`
suffix makes every snapshot directory globally unique regardless of username
normalisation. This is set at first `snapshot()` call and never changes for the
lifetime of the account.

---

### F14 — Wikitext size cap before Mistral

Wikitext sent to Mistral is truncated to `MISTRAL_MAX_WIKITEXT_CHARS` characters
before the API call. This enforces GDPR data minimisation and caps token cost:

```python
wikitext = wikitext[:MISTRAL_MAX_WIKITEXT_CHARS]
```

The LLM consent disclosure on the gather screen states: "Up to
[MISTRAL_MAX_WIKITEXT_CHARS] characters of your userpage text will be sent."

**GDPR Article 28 — DPA with Mistral:** Before go-live, a Data Processing
Agreement must be in place with Mistral AI. Mistral provides a standard DPA at
their legal portal. The privacy statement must name Mistral as a data processor,
reference the DPA, and state the EU endpoint used.

---

### F16 — Admin snapshot: batch cap

`POST /admin/snapshot` with no `username` (snapshot all) is capped at inserting
`ADMIN_SNAPSHOT_BATCH_SIZE = 50` rows per request. If there are more qualifying
users, the endpoint returns the next batch cursor and the admin dashboard calls
it in pages. This prevents saturating the `SnapshotQueue` and thread pool in a
single request.

---

### N-P1 — LLM consent (GDPR Article 28)

Before a gather job is dispatched, the gather screen presents an explicit opt-in for
the LLM step. The UI element (checkbox or toggle) must:

1. Name Mistral by name and state that the EU API endpoint (`api.mistral.ai`) is used.
2. Explain what is sent: the text content of the user's Wikimedia userpages.
3. Link to the privacy statement for full details.
4. Default to **unchecked** — the user must actively opt in.

The choice is stored in `UserProfile.llm_consent`. If the user selects "No", the
gather runs without LLM and produces no `userbox` or `proud_of` suggestions from
the LLM source; all other buffet sections (barnstars, avatar, achievements) are
unaffected. The buffet functions fully without LLM output.

On re-gather, the previously stored consent value is pre-filled and the user can
change it. The consent screen is shown again so the user is always aware of the choice.

`POST /api/gather` requires `llm_consent` in the request body on first gather (see
API contract). Subsequent gathers may omit it; the stored value is used.

---

### N-P2 — Exported PDF retention and storage lifecycle

Exported PDFs produced by `generate_pdfs.py` or `/admin/export` must not persist
indefinitely on the pod. The design addresses this in three ways:

1. **Streaming admin export** — `/admin/export` writes directly to a streaming HTTP
   response, not to disk. No PDF file is written to the pod filesystem during export.
   The admin's browser receives the ZIP or merged PDF directly.

2. **`generate_pdfs.py` output directory** — the script writes to `--output ./export/`.
   The admin documentation states that this directory must be deleted after the files
   are transferred and printed. The script logs the output path and count to the admin
   audit log on every run.

3. **`user_approved_snapshot_version` and inactive pruning** — old snapshot versions
   are pruned per the rules in the Snapshot section. The privacy statement notes that
   snapshot PDFs are retained as long as the user account exists (subject to the
   version cap and inactive pruning rules), and are deleted in full on account deletion.

---

### A2 — OAuth token lifecycle

The OAuth 2.0 access token is **not persisted** to the database. It is used once
at callback time (to fetch identity + signature), then discarded. Only the
username and the rendered `signature_html` (sanitised) are stored. There is
no token-at-rest problem to solve.

---

### L1 — OAuth client secret

The OAuth 2.0 client secret (and client ID, redirect URI) are stored as
Toolforge secrets mounted at `/run/secrets/wall-of-faces/`:

```bash
toolforge secrets create oauth-client-id     --from-literal=value="..."
toolforge secrets create oauth-client-secret --from-literal=value="..."
toolforge secrets create oauth-redirect-uri  --from-literal=value="https://wall-of-faces.toolforge.org/oauth-callback"
```

---

### L2 — Supply chain protection

`requirements.txt` is pinned to exact versions. The deployment Dockerfile runs:

```bash
pip install --require-hashes -r requirements.txt
```

Hashes are generated with `pip-compile --generate-hashes` and committed.

---

### L3 — generate_pdfs.py shared modules

`generate_pdfs.py` imports directly from `src/` — it does not duplicate any
sanitisation, rendering, or database logic:

```python
from src.db import UserProfile, db
from src.render import render_card
from src.snapshot import safe_username_dir
```

No security-relevant logic is re-implemented in the script.

---

### L4 — Admin audit log

All admin actions are logged to a structured file at
`/data/project/wall-of-faces/logs/admin.log`:

```python
import logging

admin_logger = logging.getLogger('wall_of_faces.admin')
admin_handler = logging.FileHandler('/data/project/wall-of-faces/logs/admin.log')
admin_logger.addHandler(admin_handler)

# Called on every admin action
admin_logger.info(json.dumps({
    'ts': datetime.utcnow().isoformat(),
    'admin': session['username'],
    'action': 'export',
    'format': fmt,
    'count': len(users),
}))
```

---

## Code Conventions

### Datetime

All datetime values are UTC. Use `datetime.now(timezone.utc)` throughout —
`datetime.utcnow()` is deprecated in Python 3.12 and will be removed. Store
timezone-aware datetimes in SQLAlchemy columns with `timezone=True`.

```python
from datetime import datetime, timezone

def utcnow() -> datetime:
    return datetime.now(timezone.utc)
```

Import `utcnow` from a shared utility module (`src/utils.py`) rather than calling
`datetime.now(timezone.utc)` directly — makes it trivially mockable in tests.

### SnapshotQueue table

Admin-triggered snapshot jobs use a `SnapshotQueue` table with the same schema
as `GatherQueue`. Both are polled by the coordinator tick. This keeps the
dispatcher generic and avoids a second coordinator thread.

---

## Card Layout (from mockup v4)

Three visual zones matching the buffet sections:

1. **Header** — username as Wikipedia-style h1 with rule
2. **Body** — infobox floated right (avatar, lid sinds, thuisbasis, uitgebreide rechten,
   userboxes); lead text + additional content boxes floated left
3. **"Trots op" bar** — horizontal row of article thumbnails with titles
4. **Achievements bar** — barnstar images + circular achievement badges
5. **Footer** — "Wall of Faces · nl.wikipedia.org · Wikimedia Nederland"

Mockup: `.claude/card-mockup.html`
