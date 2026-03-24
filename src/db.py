"""
db.py — SQLAlchemy models and startup helpers.

Tables managed by Alembic:
    user_profile, gather_cache, gather_queue, snapshot_queue

Table created by Flask-Session (not Alembic):
    flask_sessions  — created via db.create_all() in the deploy runbook

Startup helpers called from create_app():
    recover_stale_jobs()  — reset jobs interrupted by a pod restart
"""

from flask_sqlalchemy import SQLAlchemy

from src.utils import UTCDateTime, utcnow

db = SQLAlchemy()


# ── UserProfile ────────────────────────────────────────────────────────────────

class UserProfile(db.Model):  # type: ignore[misc, name-defined]
    __tablename__ = 'user_profile'

    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False, index=True)

    # ── Infobox metadata (populated during gather, read-only after) ────────────
    # These never appear in the buffet — they are shown automatically on the card.
    registration_date = db.Column(UTCDateTime, nullable=True)
    total_edits       = db.Column(db.Integer,  nullable=True)
    user_rights       = db.Column(db.Text,     nullable=True)  # JSON list of strings
    # home_wiki: dbname of the user's most-edited wiki (set by gather, editable in buffet).
    # Used to prefix username in the infobox when different from HOME_WIKI.
    home_wiki         = db.Column(db.String(50), nullable=True)

    # ── Preferences fetched at OAuth time ─────────────────────────────────────
    wiki_skin   = db.Column(db.String(50), nullable=True)  # raw skin name, e.g. "vector-2022"
    wiki_gender = db.Column(db.String(20), nullable=True)  # "male" | "female" | "unknown"

    # ── User-chosen card content (populated via POST /api/save-profile) ────────
    avatar_filename          = db.Column(db.String(500), nullable=True)
    home_base                = db.Column(db.String(100), nullable=True)
    biography                  = db.Column(db.Text,        nullable=True)
    biography_image_filename   = db.Column(db.String(500), nullable=True)  # legacy; superseded by content_image_filenames
    content_image_filenames    = db.Column(db.Text,        nullable=True)  # JSON [filename, ...] max 3
    extra_sections             = db.Column(db.Text,        nullable=True)  # JSON [{title, text}]
    selected_achievements    = db.Column(db.Text,        nullable=True)  # JSON [id, ...]
    userboxes                = db.Column(db.Text,        nullable=True)  # JSON [{icon,label,bg,fg}]
    barnstars                = db.Column(db.Text,        nullable=True)  # JSON [{filename,name}]
    signature_html           = db.Column(db.Text,        nullable=True)  # pre-sanitised HTML
    proud_of                 = db.Column(db.Text,        nullable=True)  # JSON [{title,filename}]

    # ── Snapshot / finalization state ──────────────────────────────────────────
    snapshot_at                     = db.Column(UTCDateTime,    nullable=True)
    snapshot_version                = db.Column(db.String(30),  nullable=True)
    # Last version explicitly triggered by the user (not by --rerender or admin).
    # Pruning logic uses this field to decide what is safe to delete.
    # NULL until the user clicks "Finaliseer kaart" for the first time.
    user_approved_snapshot_version  = db.Column(db.String(30),  nullable=True)
    processed_at                    = db.Column(UTCDateTime,    nullable=True)

    # ── Gather job state ───────────────────────────────────────────────────────
    # gather_status: idle → queued → running → done | error
    gather_status   = db.Column(db.String(20),  nullable=False, default='idle')
    gather_progress = db.Column(db.Integer,     nullable=False, default=0)
    # llm_consent: NULL = not yet asked, False = declined, True = opted in
    llm_consent     = db.Column(db.Boolean,     nullable=True)
    gather_error    = db.Column(db.String(500), nullable=True)
    last_gathered_at= db.Column(UTCDateTime,    nullable=True)

    # ── Timestamps ─────────────────────────────────────────────────────────────
    created_at = db.Column(UTCDateTime, nullable=False, default=utcnow)
    updated_at = db.Column(UTCDateTime, nullable=False, default=utcnow,
                           onupdate=utcnow)

    # ── Relationship ───────────────────────────────────────────────────────────
    gather_cache = db.relationship(
        'GatherCache',
        backref='profile',
        cascade='all, delete-orphan',
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f'<UserProfile {self.username!r} status={self.gather_status}>'


# ── GatherCache ────────────────────────────────────────────────────────────────

class GatherCache(db.Model):  # type: ignore[misc, name-defined]
    """Raw buffet suggestions from the last gather run.

    Never shown directly — feeds the buffet UI.
    Cleared and repopulated on each gather run.

    Column semantics:
        section:   ``identity`` | ``achievements`` | ``biography``
        item_type: ``userbox`` | ``barnstar`` | ``badge`` | ``avatar``
                   | ``signature`` | ``proud_of``
        source:    ``api`` | ``replica`` | ``llm``
        payload:   item-specific JSON; schema varies by *item_type*
    """
    __tablename__ = 'gather_cache'

    id        = db.Column(db.Integer,    primary_key=True)
    user_id   = db.Column(
        db.Integer,
        db.ForeignKey('user_profile.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    section   = db.Column(db.String(20), nullable=False)
    item_type = db.Column(db.String(30), nullable=False)
    payload   = db.Column(db.Text,       nullable=False)  # JSON
    source    = db.Column(db.String(20), nullable=False)
    gathered_at = db.Column(UTCDateTime, nullable=False, default=utcnow)

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f'<GatherCache user_id={self.user_id} {self.item_type} [{self.source}]>'


# ── GatherQueue ────────────────────────────────────────────────────────────────

class GatherQueue(db.Model):  # type: ignore[misc, name-defined]
    """DB-backed job queue for background data-gathering.

    A single coordinator thread polls this table every 2 s and dispatches
    jobs to a :class:`~concurrent.futures.ThreadPoolExecutor`.

    Lifecycle: ``waiting`` -> ``running`` -> ``done`` | ``error``.
    ``created_at`` determines queue position (FIFO).
    """
    __tablename__ = 'gather_queue'

    id          = db.Column(db.Integer,    primary_key=True)
    username    = db.Column(db.String(255), nullable=False, index=True)
    status      = db.Column(db.String(20),  nullable=False, default='waiting')
    created_at  = db.Column(UTCDateTime,   nullable=False, default=utcnow)
    started_at  = db.Column(UTCDateTime,   nullable=True)
    finished_at = db.Column(UTCDateTime,   nullable=True)

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f'<GatherQueue {self.username!r} {self.status}>'


# ── SnapshotQueue ──────────────────────────────────────────────────────────────

class SnapshotQueue(db.Model):  # type: ignore[misc, name-defined]
    """DB-backed job queue for admin-triggered snapshot jobs.

    Same schema as :class:`GatherQueue`; kept separate so gather and snapshot
    jobs can be prioritised independently by the coordinator.

    GatherQueue jobs take priority over SnapshotQueue jobs.
    On pod restart, ``waiting``/``running`` rows are set to ``'error'``
    (partial snapshots may exist on disk — not deleted).
    """
    __tablename__ = 'snapshot_queue'

    id          = db.Column(db.Integer,    primary_key=True)
    username    = db.Column(db.String(255), nullable=False, index=True)
    status      = db.Column(db.String(20),  nullable=False, default='waiting')
    created_at  = db.Column(UTCDateTime,   nullable=False, default=utcnow)
    started_at  = db.Column(UTCDateTime,   nullable=True)
    finished_at = db.Column(UTCDateTime,   nullable=True)

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f'<SnapshotQueue {self.username!r} {self.status}>'


# ── Startup helper ─────────────────────────────────────────────────────────────

def recover_stale_jobs() -> None:
    """
    Reset jobs that were in-flight when the process last died.

    Called once inside create_app() after db.init_app(app), within an explicit
    app context.  Must run before start_coordinator().

    GatherQueue:   waiting/running rows are deleted (gather will be re-triggered
                   by the user when they see the 'idle' prompt).
    SnapshotQueue: waiting/running rows are marked 'error' rather than deleted —
                   partial snapshot directories may exist on disk and should not
                   be silently abandoned (the admin can re-trigger).
    UserProfile:   any profile stuck in 'running' or 'queued' is reset to 'idle'
                   so the user sees the "start gathering" prompt again.
    """
    (
        db.session.query(UserProfile)
        .filter(UserProfile.gather_status.in_(['running', 'queued']))
        .update({'gather_status': 'idle', 'gather_progress': 0},
                synchronize_session=False)
    )
    (
        db.session.query(GatherQueue)
        .filter(GatherQueue.status.in_(['running', 'waiting']))
        .delete(synchronize_session=False)
    )
    (
        db.session.query(SnapshotQueue)
        .filter(SnapshotQueue.status.in_(['running', 'waiting']))
        .update({'status': 'error'}, synchronize_session=False)
    )
    db.session.commit()
