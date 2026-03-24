"""
app.py — Flask application factory for Wall of Faces.

Community-specific settings are in the config block immediately below.
Edit only that block when deploying for a different event or community.
"""

import os as _os  # needed before config block for SNAPSHOT_ROOT env override

# ── Community configuration ───────────────────────────────────────────────────
# Edit this block for each deployment; do not touch anything below it.

TOOL_NAME              = "profile-creator-nlwiki"  # Toolforge tool name; used for secrets path

HOME_WIKI              = "nlwiki"
HOME_WIKI_URL          = "https://nl.wikipedia.org"
HOME_WIKI_LABEL        = "nl.wikipedia.org · Wikimedia Nederland"
EVENT_NAME             = "Wall of Faces 2026"
EVENT_COMMONS_CATEGORY = "Wikimedia_Event_2026"
SUBMISSION_DEADLINE    = "2026-05-01"   # Shown to users; informational only
UI_LANGUAGE            = "nl"           # Loads strings/<lang>.yml
CARD_THEME             = "wikipedia"    # CSS theme: wikipedia | editorial | minimal
ADMIN_USERS            = ["Effeietsanders"]

# Gather & finalize limits
GATHER_COOLDOWN_SECONDS        = 3600   # Minimum seconds between gather runs per user
GATHER_MAX_QUALIFYING_WIKIS    = 5      # Max wikis fed to LLM per gather (cost cap)
FINALIZE_COOLDOWN_SECONDS      = 60     # Minimum seconds between finalizations per user
MISTRAL_MAX_WIKITEXT_CHARS     = 50000  # Max wikitext chars sent to LLM (GDPR minimisation)
MAX_QUEUE_DEPTH                = 20     # Max waiting gather jobs across all users
MAX_WORKERS                    = 4      # ThreadPoolExecutor workers (gather + snapshot)
ADMIN_SNAPSHOT_BATCH_SIZE      = 50     # Max SnapshotQueue rows per admin snapshot request
SERVER_NAME_URL                = "https://tools.wmcloud.org/wall-of-faces"

# Storage & retention
# Override via SNAPSHOT_ROOT env var for local development (e.g. /tmp/wof-snapshots)
SNAPSHOT_ROOT                  = _os.environ.get('SNAPSHOT_ROOT', '/data/project/wall-of-faces/snapshots')
SNAPSHOT_STORAGE_WARNING_GB    = 5
SNAPSHOT_MAX_VERSIONS_PER_USER = 10
SNAPSHOT_INACTIVE_PRUNE_DAYS   = 365

# ── End of community configuration ───────────────────────────────────────────

import base64
import functools
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import nh3
import requests
from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_migrate import Migrate
from flask_session import Session
from sqlalchemy import text

from src.db import GatherCache, GatherQueue, SnapshotQueue, UserProfile, db, recover_stale_jobs
from src.i18n import get_card_strings, load_strings, make_t
from src.render import CardOverflowError, render_card
from src.utils import utcnow

# ── Signature sanitisation constants ─────────────────────────────────────────

_SIG_ALLOWED_TAGS  = {'a', 'b', 'i', 'span', 'sup', 'sub', 'abbr'}
_SIG_ALLOWED_ATTRS = {
    'a':    {'href', 'title'},
    'abbr': {'title'},
    # span[style] intentionally omitted — CSS injection risk (security review N-H6)
}

def sanitise_signature(html: str) -> str:
    """Sanitise raw signature HTML using nh3. Returns empty string on error."""
    return nh3.clean(html, tags=_SIG_ALLOWED_TAGS,
                     attributes=_SIG_ALLOWED_ATTRS, strip_comments=True)


# ── Secret loading ────────────────────────────────────────────────────────────

def _read_secret(name: str) -> str:
    """
    Read a secret from /run/secrets/profile-creator-nlwiki/<name> (Kubernetes secret mount),
    falling back to the environment variable NAME (uppercased, hyphens → underscores).
    Returns an empty string if neither is present.
    """
    file_path = f'/run/secrets/{TOOL_NAME}/{name}'
    if os.path.exists(file_path):
        with open(file_path) as f:
            return f.read().strip()
    return os.environ.get(name.upper().replace('-', '_'), '')


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__)

    # Flask core config
    app.config['SECRET_KEY']                    = _read_secret('secret-key') or 'dev-insecure-key'
    app.config['SQLALCHEMY_DATABASE_URI']       = _read_secret('database-url') or \
                                                  'sqlite:///dev.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS']= False
    app.config['SQLALCHEMY_ENGINE_OPTIONS']     = {
        'pool_recycle': 280,    # Toolforge closes idle MySQL connections after ~5 min
        'pool_pre_ping': True,  # Detect stale connections before use
    }

    # Flask-Session: server-side sessions stored in the DB
    app.config['SESSION_TYPE']                  = 'sqlalchemy'
    app.config['SESSION_SQLALCHEMY']            = db
    app.config['SESSION_PERMANENT']             = True
    app.config['PERMANENT_SESSION_LIFETIME']    = timedelta(days=30)
    app.config['SESSION_COOKIE_HTTPONLY']       = True
    app.config['SESSION_COOKIE_SAMESITE']       = 'Lax'
    app.config['SESSION_COOKIE_SECURE']         = not app.debug

    # Community config forwarded into app.config for use by render.py / templates
    app.config['HOME_WIKI']               = HOME_WIKI
    app.config['HOME_WIKI_URL']           = HOME_WIKI_URL
    app.config['HOME_WIKI_LABEL']         = HOME_WIKI_LABEL
    app.config['EVENT_NAME']              = EVENT_NAME
    app.config['EVENT_COMMONS_CATEGORY']  = EVENT_COMMONS_CATEGORY
    app.config['SUBMISSION_DEADLINE']     = SUBMISSION_DEADLINE
    app.config['UI_LANGUAGE']             = UI_LANGUAGE
    app.config['CARD_THEME']              = CARD_THEME
    app.config['ADMIN_USERS']             = ADMIN_USERS
    app.config['SERVER_NAME_URL']         = SERVER_NAME_URL
    app.config['GATHER_COOLDOWN_SECONDS']         = int(_os.environ.get('GATHER_COOLDOWN_SECONDS', GATHER_COOLDOWN_SECONDS))
    app.config['GATHER_MAX_QUALIFYING_WIKIS']     = GATHER_MAX_QUALIFYING_WIKIS
    app.config['FINALIZE_COOLDOWN_SECONDS']       = FINALIZE_COOLDOWN_SECONDS
    app.config['MISTRAL_MAX_WIKITEXT_CHARS']      = MISTRAL_MAX_WIKITEXT_CHARS
    app.config['MAX_QUEUE_DEPTH']                 = MAX_QUEUE_DEPTH
    app.config['MAX_WORKERS']                     = MAX_WORKERS
    app.config['ADMIN_SNAPSHOT_BATCH_SIZE']       = ADMIN_SNAPSHOT_BATCH_SIZE
    app.config['SNAPSHOT_ROOT']                   = SNAPSHOT_ROOT
    app.config['SNAPSHOT_MAX_VERSIONS_PER_USER']  = SNAPSHOT_MAX_VERSIONS_PER_USER
    app.config['SNAPSHOT_INACTIVE_PRUNE_DAYS']    = SNAPSHOT_INACTIVE_PRUNE_DAYS
    app.config['SNAPSHOT_STORAGE_WARNING_GB']     = SNAPSHOT_STORAGE_WARNING_GB

    # Mistral API key
    app.config['MISTRAL_API_KEY'] = _read_secret('mistral-api-key')

    # OAuth 2.0 client credentials
    app.config['OAUTH_CLIENT_ID']     = _read_secret('oauth-client-id')
    app.config['OAUTH_CLIENT_SECRET'] = _read_secret('oauth-client-secret')
    app.config['OAUTH_REDIRECT_URI']  = _read_secret('oauth-redirect-uri')

    # Initialise extensions
    db.init_app(app)
    Migrate(app, db)
    Session(app)

    # Flask-Session 0.8 tries to create sessions via Table.create(bind=engine),
    # but SQLAlchemy 2.0 removed the bind= parameter so that call silently does nothing.
    # Work around it by creating all registered tables (incl. sessions) ourselves.
    with app.app_context():
        with db.engine.begin() as conn:
            db.metadata.create_all(conn)

    # Load i18n strings; store in app.extensions for use by render.py outside requests
    strings = load_strings(UI_LANGUAGE, app.root_path)
    app.extensions['strings'] = strings

    @app.context_processor
    def _inject_globals():
        t = make_t(strings)
        return {
            't':           t,
            'card_theme':  app.config['CARD_THEME'],
            'event_name':  app.config['EVENT_NAME'],
            'deadline':    app.config['SUBMISSION_DEADLINE'],
        }

    @app.template_filter('from_json')
    def _from_json_filter(value, default=None):
        """Jinja2 filter: parse a JSON string field from the DB, return default on error."""
        if not value:
            return default if default is not None else []
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default if default is not None else []

    # Admin logger — separate handler for admin/operational events
    admin_log = logging.getLogger('wof.admin')
    if not admin_log.handlers:
        admin_log.setLevel(logging.WARNING)
        admin_log.addHandler(logging.StreamHandler())
    app.extensions['admin_logger'] = admin_log

    # OAuth 2.0 — mark as configured when client_id is present
    app.extensions['oauth_configured'] = bool(app.config['OAUTH_CLIENT_ID'])

    # On startup: reset any jobs interrupted by a pod restart, then start coordinator
    with app.app_context():
        try:
            recover_stale_jobs()
        except Exception as exc:
            app.logger.warning('recover_stale_jobs failed (DB may not be migrated yet): %s', exc)

    start_coordinator(app)

    # Register routes
    _register_routes(app)

    return app


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    """Decorator: redirect to /login (or 401 for API routes) when not authenticated."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'not_authenticated'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """Decorator: abort with 403 if the logged-in user is not in ADMIN_USERS."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if session.get('username') not in ADMIN_USERS:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


# ── Route registration ────────────────────────────────────────────────────────

def _register_routes(app: Flask) -> None:  # noqa: C901 (intentionally long)

    # ── Dev-only login bypass (debug mode only) ───────────────────────────────
    # Never active in production — Flask debug mode is off by default on Toolforge.

    if app.debug:
        @app.get('/dev-login')
        def dev_login():
            """Skip OAuth in local dev: /dev-login?username=YourWikimediaName"""
            username = request.args.get('username', '').strip()
            if not username:
                return 'Usage: /dev-login?username=YourWikimediaName', 400
            profile = UserProfile.query.filter_by(username=username).first()
            if profile is None:
                profile = UserProfile(username=username)
                db.session.add(profile)
                db.session.commit()
            session['username'] = username
            return redirect(url_for('index'))

    # ── Page routes ───────────────────────────────────────────────────────────

    @app.get('/')
    def index():
        if 'username' not in session:
            return render_template('index.html')
        profile = UserProfile.query.filter_by(username=session['username']).first()
        if profile is None:
            return render_template('index.html')
        status = profile.gather_status
        if status in ('idle', 'error'):
            return redirect(url_for('gather'))
        if status in ('queued', 'running'):
            return redirect(url_for('gather'))
        # status == 'done'
        if not profile.userboxes and not profile.biography:
            return redirect(url_for('buffet'))
        return redirect(url_for('card'))

    @app.get('/login')
    def login():
        if not app.extensions.get('oauth_configured'):
            return 'OAuth not configured (set OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URI)', 503

        # PKCE: generate verifier + S256 challenge
        code_verifier  = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b'=').decode()

        state = secrets.token_urlsafe(32)
        session['oauth_state']         = state
        session['oauth_code_verifier'] = code_verifier

        params = urlencode({
            'response_type':         'code',
            'client_id':             app.config['OAUTH_CLIENT_ID'],
            'redirect_uri':          app.config['OAUTH_REDIRECT_URI'],
            'scope':                 'basic',
            'state':                 state,
            'code_challenge':        code_challenge,
            'code_challenge_method': 'S256',
        })
        return redirect(f'https://meta.wikimedia.org/w/rest.php/oauth2/authorize?{params}')

    @app.get('/oauth-callback')
    def oauth_callback():
        if not app.extensions.get('oauth_configured'):
            abort(503)

        # CSRF check
        if request.args.get('state') != session.pop('oauth_state', None):
            app.logger.warning('OAuth callback: state mismatch')
            return redirect(url_for('index'))

        code          = request.args.get('code', '')
        code_verifier = session.pop('oauth_code_verifier', '')
        if not code:
            return redirect(url_for('index'))

        # Exchange code for access token
        try:
            token_resp = requests.post(
                'https://meta.wikimedia.org/w/rest.php/oauth2/access_token',
                data={
                    'grant_type':    'authorization_code',
                    'code':          code,
                    'redirect_uri':  app.config['OAUTH_REDIRECT_URI'],
                    'client_id':     app.config['OAUTH_CLIENT_ID'],
                    'client_secret': app.config['OAUTH_CLIENT_SECRET'],
                    'code_verifier': code_verifier,
                },
                headers={'User-Agent': _MW_USER_AGENT},
                timeout=10,
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()['access_token']

            # Fetch user identity
            profile_resp = requests.get(
                'https://meta.wikimedia.org/w/rest.php/oauth2/resource/profile',
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'User-Agent':    _MW_USER_AGENT,
                },
                timeout=10,
            )
            profile_resp.raise_for_status()
            identity = profile_resp.json()
        except Exception as exc:
            app.logger.warning('OAuth callback failed: %s', exc)
            return redirect(url_for('index'))

        username = identity.get('username', '').strip()
        if not username:
            return redirect(url_for('index'))

        # Session fixation fix: delete old session row before setting new identity
        old_sid = getattr(session, 'sid', None)
        if old_sid:
            try:
                db.session.execute(
                    text('DELETE FROM sessions WHERE session_id = :sid'),
                    {'sid': old_sid},
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
        session.clear()
        session['username'] = username

        # Create UserProfile row if this is the user's first login
        profile = UserProfile.query.filter_by(username=username).first()
        if profile is None:
            profile = UserProfile(username=username)
            db.session.add(profile)
            db.session.commit()

        # Fetch signature, skin, and gender while we still have the access token
        try:
            opts = _fetch_user_options(username, access_token)
            changed = False
            if opts.get('signature_html'):
                profile.signature_html = sanitise_signature(opts['signature_html'])
                changed = True
            if opts.get('wiki_skin'):
                profile.wiki_skin = opts['wiki_skin']
                changed = True
            if opts.get('wiki_gender'):
                profile.wiki_gender = opts['wiki_gender']
                changed = True
            if changed:
                db.session.commit()
        except Exception as exc:
            app.logger.warning('User options fetch failed for %s: %s', username, exc)

        return redirect(url_for('index'))

    @app.get('/logout')
    @login_required
    def logout():
        old_sid = getattr(session, 'sid', None)
        if old_sid:
            try:
                db.session.execute(
                    text('DELETE FROM sessions WHERE session_id = :sid'),
                    {'sid': old_sid},
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
        session.clear()
        return redirect(url_for('index'))

    def _delete_snapshot_dir(profile: 'UserProfile') -> None:
        """Remove the snapshot directory for a user, logging but not raising on errors."""
        try:
            from src.snapshot import safe_username_dir, assert_safe_path
            import shutil
            snap_dir = os.path.join(
                app.config['SNAPSHOT_ROOT'],
                safe_username_dir(profile.username, profile.id),
            )
            try:
                assert_safe_path(snap_dir)
                if os.path.exists(snap_dir):
                    shutil.rmtree(snap_dir)
            except ValueError:
                pass  # path escapes root — skip
        except Exception as exc:
            app.logger.warning('Snapshot cleanup failed for %s: %s', profile.username, exc)

    @app.post('/api/reset-my-data')
    @login_required
    def api_reset_my_data():
        """Clear all gathered data and card content, but keep the account.

        The user stays logged in and can start a new gather immediately.
        """
        username = session['username']
        profile  = UserProfile.query.filter_by(username=username).first_or_404()

        _delete_snapshot_dir(profile)
        GatherQueue.query.filter_by(username=username).delete(synchronize_session=False)
        GatherCache.query.filter_by(user_id=profile.id).delete(synchronize_session=False)

        profile.registration_date              = None
        profile.total_edits                    = None
        profile.user_rights                    = None
        profile.home_wiki                      = None
        profile.avatar_filename                = None
        profile.home_base                      = None
        profile.biography                      = None
        profile.biography_image_filename       = None
        profile.content_image_filenames        = None
        profile.extra_sections                 = None
        profile.selected_achievements          = None
        profile.userboxes                      = None
        profile.barnstars                      = None
        profile.signature_html                 = None
        profile.proud_of                       = None
        profile.snapshot_at                    = None
        profile.snapshot_version               = None
        profile.user_approved_snapshot_version = None
        profile.processed_at                   = None
        profile.gather_status                  = 'idle'
        profile.gather_progress                = 0
        profile.gather_error                   = None
        profile.last_gathered_at               = None

        db.session.commit()
        return redirect(url_for('index'))

    @app.post('/api/delete-my-account')
    @login_required
    def api_delete_my_account():
        """Permanently delete the account and all associated data.

        Removes snapshot files, clears the session, and deletes the
        UserProfile row (cascade removes GatherCache).
        """
        username = session['username']
        profile  = UserProfile.query.filter_by(username=username).first_or_404()

        _delete_snapshot_dir(profile)
        GatherQueue.query.filter_by(username=username).delete(synchronize_session=False)

        db.session.delete(profile)  # cascade removes GatherCache via relationship
        db.session.commit()

        session.clear()
        return redirect(url_for('index'))

    @app.get('/gather')
    @login_required
    def gather():
        return render_template('gather.html')

    @app.get('/buffet')
    @login_required
    def buffet():
        username = session['username']
        profile  = UserProfile.query.filter_by(username=username).first_or_404()
        rows     = GatherCache.query.filter_by(user_id=profile.id).all()

        def _group(item_type: str) -> list[dict]:
            return [json.loads(r.payload) for r in rows if r.item_type == item_type]

        from src.wiki import dbname_to_url
        home_wiki     = app.config['HOME_WIKI']
        profile_wiki  = profile.home_wiki or home_wiki
        return render_template(
            'buffet.html',
            profile            = profile,
            avatars_cache      = _group('avatar'),
            userboxes_cache    = _group('userbox'),
            barnstars_cache    = _group('barnstar'),
            badges_cache       = _group('badge'),
            proud_of_cache     = _group('proud_of'),
            config_home_wiki   = home_wiki,
            profile_home_wiki  = profile_wiki,
            profile_home_wiki_url = dbname_to_url(profile_wiki) or '',
        )

    @app.get('/card')
    @login_required
    def card():
        profile = UserProfile.query.filter_by(username=session['username']).first_or_404()
        ctx = __import__('src.render', fromlist=['build_card_context']).build_card_context(profile)
        return render_template('card.html', **ctx)

    @app.get('/privacy')
    def privacy():
        return render_template('privacy.html')

    @app.get('/healthz')
    def healthz():
        try:
            db.session.execute(text('SELECT 1'))
            return jsonify({'ok': True})
        except Exception:
            return jsonify({'ok': False}), 503

    # ── API: gather ───────────────────────────────────────────────────────────

    @app.post('/api/gather')
    @login_required
    def api_gather():
        username = session['username']
        data = request.get_json(silent=True) or {}

        profile = UserProfile.query.filter_by(username=username).first_or_404()

        # LLM consent — required on first gather
        llm_consent = data.get('llm_consent')
        if profile.llm_consent is None and llm_consent is None:
            return jsonify({
                'error':  'llm_consent_required',
                'detail': 'You must indicate whether to allow LLM processing.',
            }), 400

        # Cooldown check
        cooldown = app.config['GATHER_COOLDOWN_SECONDS']
        if profile.last_gathered_at is not None:
            elapsed = (utcnow() - profile.last_gathered_at).total_seconds()
            if elapsed < cooldown:
                return jsonify({
                    'error':               'too_soon',
                    'retry_after_seconds': int(cooldown - elapsed),
                }), 429

        # Already in-flight?
        if profile.gather_status in ('queued', 'running'):
            pos = _queue_position(username)
            return jsonify({'status': profile.gather_status, 'queue_position': pos}), 200

        # Queue depth cap
        depth = GatherQueue.query.filter_by(status='waiting').count()
        if depth >= app.config['MAX_QUEUE_DEPTH']:
            return jsonify({'error': 'queue_full', 'detail': 'Try again shortly.'}), 503

        # Update consent if provided
        if llm_consent is not None:
            if profile.llm_consent and not llm_consent:
                # Consent revoked — delete existing LLM cache rows immediately
                GatherCache.query.filter_by(user_id=profile.id, source='llm').delete()
            profile.llm_consent = bool(llm_consent)

        # Enqueue
        db.session.add(GatherQueue(username=username))
        profile.gather_status   = 'queued'
        profile.gather_progress = 0
        profile.gather_error    = None
        db.session.commit()

        pos = _queue_position(username)
        return jsonify({'status': 'queued', 'queue_position': pos}), 200

    @app.get('/api/gather-status')
    @login_required
    def api_gather_status():
        username = session['username']
        profile  = UserProfile.query.filter_by(username=username).first_or_404()
        pos      = _queue_position(username) if profile.gather_status == 'queued' else 0
        return jsonify({
            'status':         profile.gather_status,
            'progress':       profile.gather_progress,
            'queue_position': pos,
            'error_message':  profile.gather_error,
        })

    # ── API: save-profile ─────────────────────────────────────────────────────

    @app.post('/api/save-profile')
    @login_required
    def api_save_profile():
        username = session['username']
        data     = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'invalid_json'}), 400

        profile = UserProfile.query.filter_by(username=username).first_or_404()

        # Helper: return a 422 validation error
        def invalid(field, detail):
            return jsonify({'error': 'validation_error', 'field': field, 'detail': detail}), 422

        if 'avatar_filename'           in data:
            profile.avatar_filename = data['avatar_filename'] or None

        if 'home_wiki'                 in data:
            v = (data['home_wiki'] or '').strip().lower()
            if v and not re.fullmatch(r'[a-z]{1,20}(wiki|wiktionary|wikisource|wikiquote|wikibooks|wikinews|wikiversity|wikivoyage)', v):
                return invalid('home_wiki', 'unrecognised wiki dbname')
            profile.home_wiki = v or None

        if 'home_base'                 in data:
            v = (data['home_base'] or '').strip()
            if len(v) > 100:
                return invalid('home_base', 'max 100 characters')
            profile.home_base = v or None

        if 'biography'                 in data:
            v = (data['biography'] or '').strip()
            if len(v) > 2000:
                return invalid('biography', 'max 2000 characters')
            profile.biography = v or None

        if 'biography_image_filename'  in data:
            profile.biography_image_filename = data['biography_image_filename'] or None

        if 'content_image_filenames'   in data:
            cif = data['content_image_filenames']
            if not isinstance(cif, list):
                return invalid('content_image_filenames', 'must be a list')
            if len(cif) > 3:
                return invalid('content_image_filenames', 'max 3 images')
            profile.content_image_filenames = json.dumps(cif)

        if 'signature_html'            in data:
            raw = data.get('signature_html') or ''
            profile.signature_html = sanitise_signature(raw) if raw else None

        if 'userboxes'                 in data:
            ubs = data['userboxes']
            if not isinstance(ubs, list):
                return invalid('userboxes', 'must be a list')
            if len(ubs) > 20:
                return invalid('userboxes', 'max 20 items')
            profile.userboxes = json.dumps(ubs)

        if 'barnstars'                 in data:
            bs = data['barnstars']
            if not isinstance(bs, list):
                return invalid('barnstars', 'must be a list')
            if len(bs) > 2:
                return invalid('barnstars', 'max 2 items')
            profile.barnstars = json.dumps(bs)

        if 'selected_achievements'     in data:
            ach = data['selected_achievements']
            if not isinstance(ach, list):
                return invalid('selected_achievements', 'must be a list')
            profile.selected_achievements = json.dumps(ach)

        if 'proud_of'                  in data:
            po = data['proud_of']
            if not isinstance(po, list):
                return invalid('proud_of', 'must be a list')
            if len(po) > 10:
                return invalid('proud_of', 'max 10 items')
            profile.proud_of = json.dumps(po)

        if 'extra_sections'            in data:
            sections = data['extra_sections']
            if not isinstance(sections, list):
                return invalid('extra_sections', 'must be a list')
            for i, sec in enumerate(sections):
                if len(sec.get('title', '')) > 100:
                    return invalid(f'extra_sections[{i}].title', 'max 100 characters')
                if len(sec.get('text', '')) > 500:
                    return invalid(f'extra_sections[{i}].text', 'max 500 characters')
            profile.extra_sections = json.dumps(sections)

        db.session.commit()
        return jsonify({'saved': True, 'updated_at': profile.updated_at.isoformat()})

    # ── API: finalize ─────────────────────────────────────────────────────────

    @app.post('/api/finalize')
    @login_required
    def api_finalize():
        username = session['username']
        data     = request.get_json(silent=True) or {}

        profile = UserProfile.query.filter_by(username=username).first_or_404()

        # Rate limit
        cooldown = app.config['FINALIZE_COOLDOWN_SECONDS']
        if profile.snapshot_at is not None:
            elapsed = (utcnow() - profile.snapshot_at).total_seconds()
            if elapsed < cooldown:
                return jsonify({
                    'error':               'too_soon',
                    'retry_after_seconds': int(cooldown - elapsed),
                }), 429

        # Version cap check
        confirm_prune = data.get('confirm_prune', False)
        keep_version  = data.get('keep_version')
        cap           = app.config['SNAPSHOT_MAX_VERSIONS_PER_USER']
        version_count = _count_snapshot_versions(profile)

        if version_count >= cap:
            if not confirm_prune:
                return jsonify({
                    'error':         'snapshot_limit_reached',
                    'version_count': version_count,
                    'limit':         cap,
                    'keep_version':  profile.snapshot_version,
                    'detail': (
                        f'Je hebt het maximum van {cap} opgeslagen versies bereikt. '
                        f'Als je doorgaat, worden alle oudere versies verwijderd. '
                        f'Je huidige laatste versie blijft bewaard.'
                    ),
                }), 409
            # User confirmed — validate keep_version to guard against races
            if keep_version != profile.snapshot_version:
                return jsonify({
                    'error':         'snapshot_limit_reached',
                    'version_count': _count_snapshot_versions(profile),
                    'limit':         cap,
                    'keep_version':  profile.snapshot_version,
                    'detail':        'Card changed since confirmation; please confirm again.',
                }), 409
            # Prune all versions except the current latest
            _prune_all_except(profile, keep=profile.snapshot_version)

        # Perform the snapshot (delegates to snapshot.py once it exists)
        try:
            version, snapshot_at = _do_snapshot(profile, triggered_by_user=True)
        except CardOverflowError as exc:
            return jsonify({'error': 'card_overflow', 'detail': str(exc)}), 422

        return jsonify({
            'snapshot_version': version,
            'snapshot_at':      snapshot_at.isoformat(),
        })

    # ── API: resolve-image ────────────────────────────────────────────────────

    @app.get('/api/resolve-image')
    @login_required
    def api_resolve_image():
        filename = request.args.get('filename', '').strip()
        width    = min(max(request.args.get('width', 400, type=int), 50), 1200)

        if not filename:
            return jsonify({'error': 'missing_filename'}), 400

        try:
            url = _resolve_commons_image(filename, width)
        except Exception as exc:
            app.logger.warning('resolve-image failed for %r: %s', filename, exc)
            return jsonify({'error': 'not_found'}), 404

        if url is None:
            return jsonify({'error': 'not_found'}), 404

        return jsonify({'filename': filename, 'url': url})

    # ── API: delete-account ───────────────────────────────────────────────────

    @app.post('/api/delete-account')
    @login_required
    def api_delete_account():
        username = session['username']
        profile  = UserProfile.query.filter_by(username=username).first_or_404()

        # Remove snapshot directory
        from src.snapshot import safe_username_dir
        snap_dir = os.path.join(
            app.config['SNAPSHOT_ROOT'],
            safe_username_dir(username, profile.id),
        )
        if os.path.exists(snap_dir):
            shutil.rmtree(snap_dir)

        # Remove DB rows (GatherCache cascades via FK)
        db.session.delete(profile)
        db.session.commit()

        # Clear session
        old_sid = getattr(session, 'sid', None)
        if old_sid:
            try:
                db.session.execute(
                    text('DELETE FROM sessions WHERE session_id = :sid'),
                    {'sid': old_sid},
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
        session.clear()

        return jsonify({'deleted': True})

    # ── Admin routes ──────────────────────────────────────────────────────────

    @app.get('/admin')
    @login_required
    @admin_required
    def admin():
        profiles      = UserProfile.query.order_by(UserProfile.username).all()
        storage_gb    = _snapshot_storage_gb()
        storage_warn  = storage_gb >= app.config['SNAPSHOT_STORAGE_WARNING_GB']
        return render_template(
            'admin.html',
            profiles=profiles,
            storage_gb=storage_gb,
            storage_warn=storage_warn,
        )

    @app.get('/admin/export')
    @login_required
    @admin_required
    def admin_export():
        fmt = request.args.get('format', 'zip')
        # Implemented in export.py (not yet written)
        # TODO: call src.export.build_zip() or src.export.build_merged_pdf()
        return jsonify({'error': 'not_implemented'}), 501

    @app.post('/admin/snapshot')
    @login_required
    @admin_required
    def admin_snapshot():
        data     = request.get_json(silent=True) or {}
        username = data.get('username')
        batch    = app.config['ADMIN_SNAPSHOT_BATCH_SIZE']

        if username:
            targets = [username]
        else:
            # All profiles without a current snapshot, up to batch size
            profiles = (
                UserProfile.query
                .filter(UserProfile.snapshot_version.is_(None))
                .filter_by(gather_status='done')
                .limit(batch)
                .all()
            )
            targets = [p.username for p in profiles]

        queued = []
        for uname in targets:
            existing = SnapshotQueue.query.filter(
                SnapshotQueue.username == uname,
                SnapshotQueue.status.in_(['waiting', 'running']),
            ).first()
            if existing:
                continue
            db.session.add(SnapshotQueue(username=uname))
            queued.append(uname)
        db.session.commit()

        return jsonify({'queued': queued})

    @app.post('/admin/mark-processed')
    @login_required
    @admin_required
    def admin_mark_processed():
        data     = request.get_json(silent=True) or {}
        username = data.get('username')
        now      = utcnow()

        if username:
            profile = UserProfile.query.filter_by(username=username).first_or_404()
            profile.processed_at = now
        else:
            UserProfile.query.update({'processed_at': now}, synchronize_session=False)

        db.session.commit()
        return jsonify({'ok': True})

    # ── Security headers ──────────────────────────────────────────────────────

    @app.after_request
    def add_security_headers(response):
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' https://upload.wikimedia.org data:; "
            "script-src 'self';"
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options']        = 'DENY'
        return response


# ── Helpers ───────────────────────────────────────────────────────────────────

_VERSION_RE = re.compile(r'^\d{8}_\d{6}_\d+$')

_MW_USER_AGENT = f'WallOfFaces/1.0 (Toolforge; {HOME_WIKI_URL})'


def _queue_position(username: str) -> int | str:
    """Return the user's 1-based queue position, capped at 5 to avoid leaking active user count."""
    row = GatherQueue.query.filter_by(username=username, status='waiting').first()
    if row is None:
        return 0
    ahead = GatherQueue.query.filter(
        GatherQueue.status == 'waiting',
        GatherQueue.created_at < row.created_at,
    ).count()
    pos = ahead + 1
    return pos if pos < 5 else '5+'


def _count_snapshot_versions(profile: UserProfile) -> int:
    """Count versioned snapshot directories for a user."""
    from src.snapshot import safe_username_dir
    snap_root = os.path.join(
        SNAPSHOT_ROOT,
        safe_username_dir(profile.username, profile.id),
    )
    if not os.path.isdir(snap_root):
        return 0
    return sum(
        1 for entry in os.listdir(snap_root)
        if _VERSION_RE.match(entry)
        and not os.path.islink(os.path.join(snap_root, entry))
    )


def _prune_all_except(profile: UserProfile, keep: str) -> None:
    """Delete all snapshot versions except `keep`. Called when user confirms prune."""
    from src.snapshot import safe_username_dir, assert_safe_path
    snap_root = os.path.join(
        SNAPSHOT_ROOT,
        safe_username_dir(profile.username, profile.id),
    )
    if not os.path.isdir(snap_root):
        return
    for entry in os.listdir(snap_root):
        if not _VERSION_RE.match(entry):
            continue
        if entry == keep:
            continue
        target = assert_safe_path(os.path.join(snap_root, entry))
        shutil.rmtree(target)


def _do_snapshot(profile: UserProfile, triggered_by_user: bool = False):
    """
    Create a versioned snapshot for the given profile.
    Delegates to src.snapshot.create_snapshot().
    Returns (version_string, snapshot_at_datetime).
    """
    try:
        from src.snapshot import create_snapshot
    except ImportError:
        raise NotImplementedError(
            'src/snapshot.py is not yet implemented. '
            'Write create_snapshot(profile, triggered_by_user) -> (version, datetime).'
        )
    return create_snapshot(profile, triggered_by_user=triggered_by_user)


_SKIN_DISPLAY: dict[str, str] = {
    'vector':       'Vector (legacy)',
    'vector-2022':  'Vector (2022)',
    'monobook':     'Monobook',
    'timeless':     'Timeless',
    'minerva':      'Minerva Neue',
    'cologne-blue': 'Cologne Blue',
    'modern':       'Modern',
}


def _fetch_user_options(username: str, access_token: str) -> dict[str, str | None]:
    """
    Fetch the user's options (signature, skin, gender) via an authenticated
    MediaWiki API call. Uses an OAuth 2.0 Bearer token.

    Returns a dict with keys:
        signature_html  str | None   — rendered signature HTML (not yet sanitised)
        wiki_skin       str | None   — raw skin name, e.g. "vector-2022"
        wiki_gender     str | None   — "male" | "female" | None (unknown → None)
    """
    result: dict[str, str | None] = {'signature_html': None, 'wiki_skin': None, 'wiki_gender': None}
    try:
        resp = requests.get(
            f'{HOME_WIKI_URL}/w/api.php',
            params={
                'action':  'query',
                'meta':    'userinfo',
                'uiprop':  'options',
                'format':  'json',
            },
            headers={
                'Authorization': f'Bearer {access_token}',
                'User-Agent':    _MW_USER_AGENT,
            },
            timeout=10,
        )
        data   = resp.json()
        opts   = data.get('query', {}).get('userinfo', {}).get('options', {})

        # Skin
        skin = opts.get('skin', '').strip()
        if skin:
            result['wiki_skin'] = skin

        # Gender — only store meaningful values
        gender = opts.get('gender', '').strip()
        if gender and gender != 'unknown':
            result['wiki_gender'] = gender

        # Signature (nickname = wikitext; parse to HTML)
        raw_sig = opts.get('nickname', '').strip()
        if raw_sig:
            parse_resp = requests.get(
                f'{HOME_WIKI_URL}/w/api.php',
                params={
                    'action': 'parse',
                    'text':   raw_sig,
                    'prop':   'text',
                    'format': 'json',
                },
                headers={'User-Agent': _MW_USER_AGENT},
                timeout=10,
            )
            html = parse_resp.json().get('parse', {}).get('text', {}).get('*', '')
            if html:
                result['signature_html'] = html
    except Exception:
        pass
    return result


def _resolve_commons_image(filename: str, width: int) -> str | None:
    """
    Call the Commons MediaWiki API to get a thumbnail URL at the given width.
    Returns the URL string, or None if the file is not found.
    """
    resp = requests.get(
        'https://commons.wikimedia.org/w/api.php',
        params={
            'action':   'query',
            'prop':     'imageinfo',
            'iiprop':   'url',
            'iiurlwidth': width,
            'titles':   f'File:{filename}',
            'format':   'json',
        },
        headers={'User-Agent': _MW_USER_AGENT},
        timeout=10,
    )
    data    = resp.json()
    pages   = data.get('query', {}).get('pages', {})
    page    = next(iter(pages.values()), {})
    if page.get('missing') is not None:
        return None
    imageinfo = page.get('imageinfo', [{}])[0]
    return imageinfo.get('thumburl') or imageinfo.get('url')


def _snapshot_storage_gb() -> float:
    """Return total size of SNAPSHOT_ROOT in GB, or 0.0 if it doesn't exist."""
    import pathlib
    root = pathlib.Path(SNAPSHOT_ROOT)
    if not root.exists():
        return 0.0
    return sum(f.stat().st_size for f in root.rglob('*') if f.is_file()) / (1024 ** 3)


# ── Background coordinator ────────────────────────────────────────────────────

_executor: ThreadPoolExecutor | None = None
_last_maintenance_date: date | None = None


def start_coordinator(app: Flask) -> None:
    """Start the background coordinator thread. No-op when WALL_OF_FACES_CLI is set."""
    if os.environ.get('WALL_OF_FACES_CLI'):
        return
    global _executor
    _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    t = threading.Thread(target=_coordinator_loop, args=(app,), daemon=True)
    t.start()
    app.logger.info('Coordinator started (max_workers=%d)', MAX_WORKERS)


def _coordinator_loop(app: Flask) -> None:
    """Main coordinator loop — never exits; catches all exceptions."""
    while True:
        try:
            _coordinator_tick(app)
        except Exception as exc:
            app.logger.error('Coordinator tick error: %s', exc, exc_info=True)
        time.sleep(2)


def _coordinator_tick(app: Flask) -> None:
    """Single coordinator iteration: run maintenance if needed, then dispatch one job of each type."""
    global _last_maintenance_date

    # Daily maintenance — once per calendar day
    today = datetime.now(timezone.utc).date()
    if today != _last_maintenance_date:
        _run_daily_maintenance(app)
        _last_maintenance_date = today

    # Dispatch one GatherQueue job (priority over snapshots)
    _dispatch_gather(app)

    # Dispatch one SnapshotQueue job (only if executor has capacity)
    _dispatch_snapshot(app)


def _dispatch_gather(app: Flask) -> None:
    """Claim and dispatch one waiting gather job, if any."""
    try:
        with app.app_context():
            claimed = db.session.execute(text("""
                UPDATE gather_queue SET status='running', started_at=:now
                WHERE id = (
                    SELECT id FROM gather_queue
                    WHERE status='waiting'
                    ORDER BY created_at ASC LIMIT 1
                )
            """), {'now': utcnow().replace(tzinfo=None)})
            db.session.commit()
            if claimed.rowcount == 0:
                db.session.remove()
                return
            row = db.session.execute(
                text("SELECT id, username FROM gather_queue "
                     "WHERE status='running' ORDER BY started_at DESC LIMIT 1")
            ).fetchone()
            db.session.remove()

        if row is None:
            return

        try:
            from src.gather import run_gather_job
        except ImportError:
            app.logger.warning('src/gather.py not yet implemented — gather job dropped')
            return

        future = _executor.submit(run_gather_job, row.username, app)
        future.add_done_callback(
            lambda f, u=row.username: _on_gather_done(f, u, app)
        )
    except Exception:
        with app.app_context():
            db.session.remove()
        raise


def _on_gather_done(future, username: str, app: Flask) -> None:
    """Future callback: update UserProfile and GatherQueue row after a gather job finishes."""
    with app.app_context():
        try:
            profile = UserProfile.query.filter_by(username=username).first()
            if not profile:
                return
            exc = future.exception()
            if exc:
                profile.gather_status = 'error'
                profile.gather_error  = str(exc)[:500]
                app.logger.warning('Gather failed for %s: %s', username, exc)
            else:
                profile.gather_status = 'done'
                profile.gather_error  = None
            profile.last_gathered_at = utcnow()
            # Mark the GatherQueue row done
            db.session.execute(
                text("UPDATE gather_queue SET status=:s, finished_at=:now "
                     "WHERE username=:u AND status='running'"),
                {'s': 'error' if exc else 'done', 'u': username, 'now': utcnow().replace(tzinfo=None)},
            )
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.error('on_gather_done error for %s: %s', username, e)
        finally:
            db.session.remove()


def _dispatch_snapshot(app: Flask) -> None:
    """Claim and dispatch one waiting snapshot job, if any."""
    try:
        with app.app_context():
            claimed = db.session.execute(text("""
                UPDATE snapshot_queue SET status='running', started_at=:now
                WHERE id = (
                    SELECT id FROM snapshot_queue
                    WHERE status='waiting'
                    ORDER BY created_at ASC LIMIT 1
                )
            """), {'now': utcnow().replace(tzinfo=None)})
            db.session.commit()
            if claimed.rowcount == 0:
                db.session.remove()
                return
            row = db.session.execute(
                text("SELECT id, username FROM snapshot_queue "
                     "WHERE status='running' ORDER BY started_at DESC LIMIT 1")
            ).fetchone()
            db.session.remove()

        if row is None:
            return

        try:
            from src.snapshot import run_snapshot_job
        except ImportError:
            app.logger.warning('src/snapshot.py not yet implemented — snapshot job dropped')
            return

        future = _executor.submit(run_snapshot_job, row.username, app)
        future.add_done_callback(
            lambda f, u=row.username: _on_snapshot_done(f, u, app)
        )
    except Exception:
        with app.app_context():
            db.session.remove()
        raise


def _on_snapshot_done(future, username: str, app: Flask) -> None:
    """Future callback: update SnapshotQueue row after a snapshot job finishes."""
    with app.app_context():
        try:
            exc = future.exception()
            status = 'error' if exc else 'done'
            db.session.execute(
                text("UPDATE snapshot_queue SET status=:s, finished_at=:now "
                     "WHERE username=:u AND status='running'"),
                {'s': status, 'u': username, 'now': utcnow().replace(tzinfo=None)},
            )
            if exc:
                app.logger.warning('Snapshot failed for %s: %s', username, exc)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.error('on_snapshot_done error for %s: %s', username, e)
        finally:
            db.session.remove()


def _run_daily_maintenance(app: Flask) -> None:
    """
    Daily housekeeping:
      - Delete old GatherCache rows (>30 days, gather complete)
      - Delete expired sessions
      - Prune snapshot versions for inactive accounts
      - Check storage usage
    """
    with app.app_context():
        try:
            cutoff = utcnow() - timedelta(days=30)
            done_ids = db.session.scalars(
                db.select(UserProfile.id).where(UserProfile.gather_status == 'done')
            ).all()
            if done_ids:
                GatherCache.query.filter(
                    GatherCache.gathered_at < cutoff,
                    GatherCache.user_id.in_(done_ids),
                ).delete(synchronize_session=False)

            db.session.execute(text('DELETE FROM sessions WHERE expiry < :now'),
                               {'now': utcnow().replace(tzinfo=None)})

            # Inactive account snapshot pruning (snapshot.py required)
            try:
                from src.snapshot import prune_inactive_user
                inactive_cutoff = utcnow() - timedelta(
                    days=app.config['SNAPSHOT_INACTIVE_PRUNE_DAYS']
                )
                for profile in UserProfile.query.filter(
                    UserProfile.updated_at < inactive_cutoff
                ):
                    try:
                        prune_inactive_user(profile)
                    except Exception as exc:
                        app.logger.warning('prune_inactive_user(%s) failed: %s',
                                           profile.username, exc)
            except ImportError:
                pass

            db.session.commit()

            # Storage check
            storage_gb = _snapshot_storage_gb()
            warn_gb    = app.config['SNAPSHOT_STORAGE_WARNING_GB']
            if storage_gb >= warn_gb:
                admin_log = app.extensions.get('admin_logger', app.logger)
                admin_log.warning(json.dumps({
                    'ts':          utcnow().isoformat(),
                    'event':       'storage_warning',
                    'snapshot_gb': round(storage_gb, 2),
                    'limit_gb':    warn_gb,
                }))

        except Exception as exc:
            db.session.rollback()
            from sqlalchemy.exc import OperationalError
            if isinstance(exc, OperationalError):
                # Benign during `flask db upgrade` — schema not yet migrated.
                app.logger.debug('Daily maintenance skipped (schema not ready): %s', exc)
            else:
                app.logger.error('Daily maintenance failed: %s', exc, exc_info=True)
        finally:
            db.session.remove()
