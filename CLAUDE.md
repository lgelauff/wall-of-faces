# CLAUDE.md — Wall of Faces

Project-specific instructions and lessons for AI assistants working on this codebase.

---

## Working style

- Do not summarize completed work at the end of a response. The output speaks for itself.
- Consult the user before making big-picture design changes (card layout, data model shape, deployment architecture).
- Go top-down: card template → PDF render → backend → frontend.
- Keep CSS theme architecture separate: `card_content.html` must stay style-free; all visual decisions belong in `card-theme-*.css`.
- Use English names in code; give Dutch translations in parentheses when communicating with the user.
- Model subagent tasks by complexity: independent reviews → opus, general research → sonnet, simple extraction → haiku.

---

## Key technical decisions (do not revisit without discussion)

- WeasyPrint runs via subprocess (`weasyprint_worker.py`) with `safe_url_fetcher` enforced.
- WeasyPrint worker uses the venv Python derived from `weasyprint.__file__` (not `sys.executable` — that returns the uWSGI binary on Toolforge).
- Badge icons: short text ≤4 chars, NOT emoji (Cairo cannot embed Apple Color Emoji in PDF).
- CSS is inlined in PDF HTML — no URL resolution issues in WeasyPrint.
- `UTCDateTime` TypeDecorator — MySQL `DateTime(timezone=True)` is a silent no-op.
- uWSGI `processes=1 threads=8` — eliminates coordinator races.
- `nh3` for signature sanitisation (bleach is deprecated).
- Commons thumbnail API (`iiurlwidth=N`) — always returns PNG, eliminates cairosvg dependency.
- Flask-Session with DB backend; Alembic for migrations.

---

## Toolforge deployment gotchas

- **Venv must use the system Python**: `uv venv --python /usr/bin/python3 ~/www/python/venv`. Do not use the project's local Python version.
- **`webservice restart` must be run from `~`**, not from inside the repo.
- **`db stamp head` on first deploy**, not `db upgrade` — Flask-Session's `create_all` runs during upgrade and causes "table already exists" errors. `wsgi.py` now handles this automatically.
- **`toolforge envvars list` masks values** — secrets cannot be retrieved after creation.
- **uWSGI `lseek: Illegal seek` in logs** — harmless noise, filter it out when reading logs.
- **Replica databases are Toolforge-only** — code that queries `*.labsdb` will fail locally; the gather flow skips those steps gracefully.
- **Flask-Session actual table name is `sessions`** (not `flask_sessions`) — raw SQL queries must use `sessions`.
- **`NOW()` is MySQL-only** — all raw SQL uses `:now` bound parameter with `utcnow().replace(tzinfo=None)`.
- **WeasyPrint needs native libs not in the container** — `libgobject`, `libpango` etc. must be pre-downloaded to `~/deps/` and `LD_LIBRARY_PATH` set via `toolforge envvars`. On a fresh install: `cd ~/deps && apt-get download libglib2.0-0t64 libpango-1.0-0 libpangoft2-1.0-0 libfontconfig1 libfreetype6 libharfbuzz0b libffi8 libpcre2-8-0 libpixman-1-0 libpng16-16t64 libcairo2 libgraphite2-3 libexpat1 libbrotli1 libuuid1 && for deb in *.deb; do dpkg-deb -x "$deb" .; done && rm *.deb`, then `toolforge envvars create LD_LIBRARY_PATH /data/project/profile-creator-nlwiki/deps/usr/lib/x86_64-linux-gnu`. May need additional libs — check with `toolforge jobs run test-ldd --image python3.13 --command "LD_LIBRARY_PATH=... ldd .../libpango-1.0.so.0"`.
- **`SNAPSHOT_ROOT`** defaults to `/data/project/profile-creator-nlwiki/snapshots` — create this directory on first deploy: `mkdir -p /data/project/profile-creator-nlwiki/snapshots`.

---

## Local development

- Use `127.0.0.1:5000`, not `localhost` — on macOS, `localhost` may be intercepted by AirPlay Receiver.
- `/dev-login?username=X` bypasses OAuth in debug mode only.
- Use `flask db stamp head` (not `db upgrade`) for fresh local setup.
- Coordinator suppression: only `WALL_OF_FACES_CLI` env var, not `FLASK_RUN_FROM_CLI`.

---

## Flask-Session + SQLAlchemy 2.0 quirks

- `Table.create(bind=engine)` is a silent no-op in SQLAlchemy 2.0; workaround is `db.metadata.create_all(conn)` in `create_app()` after `Session(app)`.
- The `alembic_version` table not existing means "untracked", not "empty DB".

---

## String files

- `strings/nl.yml` is the primary file; `strings/en.yml` is the English reference.
- Both files must stay in sync at all times — same keys, same structure.

---

## Notes

- `notes.txt` (gitignored) is used for session handoff notes. When read: process the items, then archive them to the bottom under a `---` line with the date, and clear the top section.
- Temp files, scratch work, and Claude-produced artefacts go in `.claude/`, never in the main project tree.
