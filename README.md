# Wall of Faces

A Toolforge web tool that lets Wikimedia editors build a personal A5 profile card for a community event. Editors log in with their Wikimedia account, and the tool automatically gathers data from their edit history — barnstars, userboxes, edit counts, avatar images — and presents it as a "buffet" of suggestions. The editor picks what goes on their card, then downloads a print-ready PDF. Cards are printed and handed out at the event.

---

## Running locally

Running locally is useful for development and testing. It uses SQLite instead of MySQL and skips the Toolforge-specific infrastructure. You will not need a server or a Wikimedia account to get started.

### Prerequisites

**Python 3.11.** Check whether you have it:

```bash
python3 --version
```

If the output is `Python 3.11.x` you are good. If not, install it from [python.org](https://www.python.org/downloads/) or via your system package manager (e.g. `brew install python@3.11` on macOS).

**uv** — the package manager used by this project. Check whether you have it:

```bash
uv --version
```

If the command is not found, install it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then restart your terminal so the `uv` command is available.

**WeasyPrint system dependencies** (only needed if you want to test PDF rendering). WeasyPrint requires Cairo and Pango. Check whether they are present:

```bash
python3 -c "import cairo; import gi; gi.require_version('Pango', '1.0'); print('ok')"
```

If you see `ok`, you are set. If you see an error, install the libraries:

On macOS:

```bash
brew install cairo pango
```

On Debian/Ubuntu:

```bash
sudo apt-get install libcairo2 libpango-1.0-0 libpangocairo-1.0-0
```

PDF rendering will simply fail with an error if these are missing — the rest of the app works fine without them.

### Step 1 — Clone the repository and install dependencies

All commands in this guide must be run from the **project root** — the `wall-of-faces/` directory that contains `app.py`. Flask needs to find `wsgi.py`, `templates/`, and `static/` relative to the current directory, so running from the wrong folder will cause errors.

```bash
git clone https://github.com/yourorg/wall-of-faces.git
cd wall-of-faces
uv sync
```

`uv sync` reads `pyproject.toml`, creates a virtual environment in `.venv/`, and installs all dependencies. This takes about 30 seconds the first time.

### Step 2 — Create a directory for snapshot files

The app downloads images and renders PDFs into a "snapshot" directory. Locally, a temporary directory works fine:

```bash
mkdir -p /tmp/wof-snapshots
```

### Step 3 — Initialise the database

The app uses SQLite locally. Run this command from the project root to create `dev.db` and set up all tables:

```bash
uv run flask --app wsgi:application db stamp head
```

> If you have not created `.env` yet (Step 4 below), prefix with `SNAPSHOT_ROOT=/tmp/wof-snapshots` for this command only.

You should see a line ending in `-> (head)` (the exact revision ID will vary) with no errors.

> **Why `stamp head` and not `db upgrade`?** Flask-Session creates all tables automatically when the app starts up (via `db.create_all()`), which happens even during a `flask db upgrade` run. This means the tables already exist by the time Alembic tries to create them, causing an error. `stamp head` tells Alembic "the database is already in the correct state" without trying to run the migration script again. When you update the app later and new migrations are added, use `flask db upgrade` as normal — that only applies changes that are not yet in the database.

### Step 4 — Create a `.env` file

Create `.env` in the project root (it is already in `.gitignore`):

```
SECRET_KEY=dev-only-not-secret
SNAPSHOT_ROOT=/tmp/wof-snapshots
```

`uv run` picks this up automatically. You can add `MISTRAL_API_KEY` and OAuth credentials here later — see the optional sections below.

### Step 5 — Start the development server

```bash
FLASK_DEBUG=1 uv run flask --app wsgi:application run
```

You should see:

```
 * Running on http://127.0.0.1:5000
 * Debug mode: on
```

The app is now available at **http://127.0.0.1:5000**.

> **Note:** use `127.0.0.1` rather than `localhost`. On macOS, `localhost` may be intercepted by AirPlay Receiver or another system service on port 5000, causing a 403 error in the browser even though Flask is running fine.

### Step 6 — Log in

Because `FLASK_DEBUG=1` is set, a special login shortcut is available that bypasses OAuth entirely. Visit:

```
http://127.0.0.1:5000/dev-login?username=YourWikimediaName
```

Replace `YourWikimediaName` with any username you want to test with (it does not need to be a real account — the profile is created locally in `dev.db`). This route **does not exist in production** and is only active in debug mode.

You will be redirected to the home page and logged in. From there you can explore the gather flow, buffet, and card preview.

### Local limitations

The following features behave differently locally compared to production:

| Feature | Local behaviour |
|---------|----------------|
| **Login** | Use `/dev-login?username=…` — no OAuth needed |
| **Edit counts & registration date** | Not available — the Toolforge replica databases that provide this data are only accessible from within Toolforge. The gather job skips these steps gracefully and continues without them. |
| **Barnstar and userbox detection** | Works — data comes from the public MediaWiki API, which is accessible from anywhere |
| **LLM extraction (Mistral)** | Skipped unless you set `MISTRAL_API_KEY` in your environment. This is fine; users can opt out of LLM anyway. |
| **PDF rendering** | Works if WeasyPrint and its system dependencies (Cairo, Pango) are installed; see prerequisites above |
| **Snapshots** | Work fully; files are written to `SNAPSHOT_ROOT` |
| **Database** | SQLite (`dev.db`) instead of MySQL — sufficient for all development work |

### Testing with a real Wikimedia login locally (optional)

If you want to test the full OAuth login flow locally, you need to register an OAuth 2.0 client with Wikimedia.

1. Go to [Special:OAuthConsumerRegistration/propose](https://meta.wikimedia.org/wiki/Special:OAuthConsumerRegistration/propose) on meta.wikimedia.org
2. Fill in the form in order:
   - **Application name:** anything (e.g. "Wall of Faces dev")
   - **Consumer version:** OAuth 2.0
   - **Application description:** local development -- creating a profile page (for print) of a user based on some information about them and their own input. This requires authentication that they are indeed said user, but also would collect signature. 
   - **This consumer is for use only by `<YourWikimediaUsername>`** — check this box; no admin approval needed, active immediately
   - **Confidential client:** yes
   - **Callback URL:** `http://127.0.0.1:5000/oauth-callback`
   - **Applicable project:** leave blank (all wikis)
   - **Allowed grants:** check **Authorization code** only — leave Refresh token and Client credentials unchecked
   - **Grants:** select **"User identity verification only"**
3. Note the **client ID** and **client secret** shown on the confirmation page

Save everything in `.env` in the project root (already in `.gitignore` — never commit this file). This file covers all secrets needed for local development:

```
# Flask session signing key — generate once with:
#   python3 -c "import secrets; print(secrets.token_hex(64))"
SECRET_KEY=your_64char_hex_here

# OAuth 2.0 (local dev client — separate from the production client)
OAUTH_CLIENT_ID=your_local_client_id
OAUTH_CLIENT_SECRET=your_local_client_secret
OAUTH_REDIRECT_URI=http://127.0.0.1:5000/oauth-callback

# Optional: Mistral AI for LLM-based suggestions (users can opt out without it)
MISTRAL_API_KEY=your_mistral_api_key

# Optional: override snapshot directory (defaults to /data/project/... which doesn't exist locally)
SNAPSHOT_ROOT=/tmp/wof-snapshots
```

`uv run` picks up `.env` automatically. Then start the app:

```bash
FLASK_DEBUG=1 uv run flask --app wsgi:application run
```

> **Note:** keep `FLASK_DEBUG=1` even when testing OAuth — without it, a bad client ID produces a cryptic 500 error instead of a readable message. Remove it only when you want to test production-like error handling.

---

## Deploying on Toolforge

Toolforge is the Wikimedia Foundation's hosting platform for community tools. These instructions assume you have a Wikimedia developer account and can SSH into Toolforge. If you have not done that yet, follow the [Toolforge quickstart](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Quickstart) first.

### Step 1 — Register an OAuth 2.0 client

The app uses Wikimedia OAuth 2.0 so editors can log in with their Wikimedia account. You need to register a client before deploying.

1. Go to [Special:OAuthConsumerRegistration/propose](https://meta.wikimedia.org/wiki/Special:OAuthConsumerRegistration/propose) on meta.wikimedia.org
2. Fill in the form in order:
   - **Application name:** Wall of Faces (or your event name)
   - **Consumer version:** OAuth 2.0
   - **Application description:** brief description of the tool and event
   - **This consumer is for use only by `<YourWikimediaUsername>`** — leave **unchecked** for a public tool that all editors can use
   - **Confidential client:** yes
   - **Callback URL:** `https://wall-of-faces.toolforge.org/oauth-callback`
   - **Applicable project:** leave blank (all wikis)
   - **Allowed grants:** check **Authorization code** only — leave Refresh token and Client credentials unchecked
   - **Grants:** select **"User identity verification only"**
3. Submit. A Wikimedia admin must approve the client before it works. This typically takes a few days.
4. While you wait for approval, continue with the remaining steps.
5. Once approved, you will receive a **client ID** and **client secret**. Keep these safe.

### Step 2 — Create the Toolforge tool

SSH into the Toolforge login node:

```bash
ssh your-wikimedia-username@login.toolforge.org
```

Create the tool account. The tool name becomes part of the URL (`wall-of-faces.toolforge.org`), so choose carefully:

```bash
toolforge tools create wall-of-faces
```

Switch to the tool account:

```bash
become wall-of-faces
```

Your shell prompt will change to `tools.wall-of-faces@...`. All remaining commands in this guide should be run as the tool account unless stated otherwise.

### Step 3 — Check out the code

Clone the repository into the tool's home directory:

```bash
git clone https://github.com/yourorg/wall-of-faces.git ~/wall-of-faces
cd ~/wall-of-faces
```

Install dependencies:

```bash
uv sync --no-dev
```

### Step 4 — Create the database

The tool needs its own MySQL database on Toolforge. Request one at [Toolsadmin → Databases](https://toolsadmin.wikimedia.org/tools/tool/wall-of-faces) (you may need to be logged in as your personal account, not the tool account).

Once created, Toolforge provides a credential file at `~/replica.my.cnf`. Find your database name and credentials there:

```bash
cat ~/replica.my.cnf
```

The database URL you will need looks like this — fill in the values from `replica.my.cnf`:

```
mysql+pymysql://s12345:yourpassword@tools.db.svc.wikimedia.cloud/s12345__wall_of_faces
```

The database name (`s12345__wall_of_faces`) must be created via Toolsadmin. The part before `__` is your tool's database user prefix.

### Step 5 — Create a directory for snapshot files

Snapshots are versioned directories containing downloaded images and rendered PDFs, one per user. Create a dedicated directory in the tool's home:

```bash
mkdir -p ~/snapshots
chmod 700 ~/snapshots
```

### Step 6 — Store secrets

The app reads secrets from files under `/run/secrets/wall-of-faces/`. On Toolforge Kubernetes, you store these as [tool secrets](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Kubernetes/Secrets) using the `toolforge` CLI.

First, generate a random secret key for Flask session signing:

```bash
python3 -c "import secrets; print(secrets.token_hex(64))"
```

Copy the output — you will use it in the next command.

Create each secret (run these one at a time, replacing the placeholder values):

```bash
toolforge secrets create secret-key --from-literal=value="paste_your_64char_hex_here"
toolforge secrets create database-url --from-literal=value="mysql+pymysql://s12345:password@tools.db.svc.wikimedia.cloud/s12345__wall_of_faces"
toolforge secrets create oauth-client-id --from-literal=value="your_client_id"
toolforge secrets create oauth-client-secret --from-literal=value="your_client_secret"
toolforge secrets create oauth-redirect-uri --from-literal=value="https://wall-of-faces.toolforge.org/oauth-callback"
toolforge secrets create mistral-api-key --from-literal=value="your_mistral_api_key"
```

If you do not have a Mistral API key yet, skip that last line. The app works without it — LLM-based suggestions will be disabled.

Verify all secrets are stored:

```bash
toolforge secrets list
```

You should see `secret-key`, `database-url`, `oauth-client-id`, `oauth-client-secret`, `oauth-redirect-uri`, and (optionally) `mistral-api-key`.

### Step 7 — Configure the app

Edit the community configuration block at the top of `app.py`. This is the only section you should need to change:

```python
HOME_WIKI              = "nlwiki"            # dbname of the primary wiki
HOME_WIKI_URL          = "https://nl.wikipedia.org"
HOME_WIKI_LABEL        = "nl.wikipedia.org · Wikimedia Nederland"  # shown on the card footer
EVENT_NAME             = "Wall of Faces 2026"   # shown on the card footer
EVENT_COMMONS_CATEGORY = "Wikimedia_Event_2026" # Commons category for event photos
SUBMISSION_DEADLINE    = "2026-05-01"           # informational; shown to users
ADMIN_USERS            = ["YourWikimediaUsername"]  # who can access /admin
```

Set `ADMIN_USERS` to your own Wikimedia username so you can access the admin panel after deployment.

The snapshot directory defaults to `/data/project/wall-of-faces/snapshots` but can be overridden via the `SNAPSHOT_ROOT` environment variable. The simplest approach on Toolforge is to set it to the directory you created in step 5:

```bash
export SNAPSHOT_ROOT=/data/tool-wall-of-faces/snapshots
```

Or add it to `~/wall-of-faces/.env` if you prefer to keep it in the project.

### Step 8 — Initialise the database

For a fresh deployment, use `db stamp head`. Flask-Session creates all tables automatically when the app starts (via `db.create_all()`), so Alembic does not need to run the migration script — it just needs to record the current state:

```bash
cd ~/wall-of-faces
SNAPSHOT_ROOT=/data/tool-wall-of-faces/snapshots \
uv run flask --app wsgi:application db stamp head
```

You should see a line ending in `-> (head)` with no errors.

If you see an error about the database connection, double-check the `database-url` secret and that your database exists in Toolsadmin.

> **For future updates:** when new migrations are added, use `flask db upgrade` instead. That command only applies migrations that are not yet recorded in the database, so it is safe to run after any code update.

### Step 9 — Start the webservice

```bash
cd ~/wall-of-faces
webservice --backend=kubernetes python3.11 start
```

Toolforge will find `uwsgi.ini` automatically and use it to configure the server. The service runs on a single process with 8 threads so the background job coordinator works correctly.

To check whether it started:

```bash
webservice --backend=kubernetes python3.11 status
```

### Step 10 — Verify

Open these URLs in your browser:

- **Health check:** `https://wall-of-faces.toolforge.org/healthz` — should return `{"ok": true}`
- **Home page:** `https://wall-of-faces.toolforge.org/`
- **Login:** `https://wall-of-faces.toolforge.org/login` — redirects to Wikimedia OAuth
- **Admin panel:** `https://wall-of-faces.toolforge.org/admin` — only accessible with your username

If the health check fails, check the logs:

```bash
webservice --backend=kubernetes python3.11 logs
```

---

### Updating a running deployment

When you push new code, update the deployment like this:

```bash
become wall-of-faces
cd ~/wall-of-faces

# Pull the latest code
git pull

# Install any new dependencies
uv sync --no-dev

# Apply any new database migrations (safe to run even if there are none)
uv run flask --app wsgi:application db upgrade

# Restart the webservice to pick up the new code
webservice --backend=kubernetes python3.11 restart
```

The restart takes a few seconds. Check `healthz` afterwards to confirm it came back up cleanly.
