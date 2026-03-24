"""
replicas.py — Toolforge replica DB access.

Used only for numeric counts — never for content (wikitext, templates, etc.).
Content always comes from the MediaWiki API (src/wiki.py).

Two logical connections, created lazily on first use:
    centralauth_p  — global account info (registration date, per-wiki edit counts)

The `~/replica.my.cnf` credential file is the standard Toolforge authentication
mechanism. On a developer machine without this file, ReplicaUnavailableError is
raised and callers (gather.py) fall back to zero/None.

Connection settings:
    pool_recycle=280  — Toolforge closes idle MySQL connections after ~5 min;
                        recycling at 280 s keeps us safely under that threshold.
    pool_pre_ping=True — detect stale connections before use.
"""

from __future__ import annotations

import configparser
import os
from datetime import datetime, timezone

from typing import Any

from sqlalchemy import Engine, create_engine, text


# ── Exceptions ────────────────────────────────────────────────────────────────

class ReplicaUnavailableError(Exception):
    """Raised when replica.my.cnf is missing or the connection fails."""


# ── Connection factory ────────────────────────────────────────────────────────

def _load_replica_credentials() -> tuple[str, str]:
    """
    Parse ~/replica.my.cnf and return (user, password).
    Raises ReplicaUnavailableError if the file is absent or malformed.
    """
    path = os.path.expanduser('~/replica.my.cnf')
    if not os.path.exists(path):
        raise ReplicaUnavailableError(
            f'Replica credentials not found: {path!r}. '
            'This file is only available on Toolforge.'
        )
    cfg = configparser.ConfigParser()
    cfg.read(path)
    try:
        return cfg['client']['user'], cfg['client']['password']
    except KeyError as exc:
        raise ReplicaUnavailableError(
            f'replica.my.cnf is missing expected key: {exc}'
        ) from exc


def _replica_engine(db_name: str) -> Engine:
    """
    Create a SQLAlchemy engine for a Toolforge replica database.

    db_name examples: 'centralauth_p', 'nlwiki_p', 'commonswiki_p'
    Host convention: remove trailing _p and append .web.db.svc.wikimedia.cloud
    """
    user, password = _load_replica_credentials()
    # Strip the trailing '_p' suffix: 'centralauth_p' → 'centralauth.web.db.svc.wikimedia.cloud'
    host = db_name[:-2] + '.web.db.svc.wikimedia.cloud'
    return create_engine(
        f'mysql+pymysql://{user}:{password}@{host}/{db_name}',
        pool_recycle=280,
        pool_pre_ping=True,
        connect_args={'connect_timeout': 10},
    )


# ── Lazy singletons ───────────────────────────────────────────────────────────

_centralauth_engine: Engine | None = None


def _centralauth() -> Engine:
    global _centralauth_engine
    if _centralauth_engine is None:
        _centralauth_engine = _replica_engine('centralauth_p')
    return _centralauth_engine


# ── Public API ────────────────────────────────────────────────────────────────

def get_global_user_info(username: str) -> dict[str, Any] | None:
    """
    Fetch global account info from CentralAuth.

    Returns a dict with:
        registration_date  — datetime (UTC, tz-aware) or None
        global_edit_count  — int or None

    Returns None if the account is not found in CentralAuth.
    Raises ReplicaUnavailableError if the replica is inaccessible.
    """
    with _centralauth().connect() as conn:
        row = conn.execute(
            text("""
                SELECT gu_registration, gu_editcount
                FROM globaluser
                WHERE gu_name = :name
                LIMIT 1
            """),
            {'name': username},
        ).fetchone()

    if row is None:
        return None

    reg = row.gu_registration
    if isinstance(reg, (bytes, bytearray)):
        reg = reg.decode('utf-8', errors='replace')
    if isinstance(reg, str) and reg:
        try:
            # CentralAuth stores as 'YYYYMMDDHHmmss' (MediaWiki timestamp format)
            reg_dt = datetime.strptime(reg[:14], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
        except ValueError:
            reg_dt = None
    elif isinstance(reg, datetime):
        reg_dt = reg.replace(tzinfo=timezone.utc) if reg.tzinfo is None else reg
    else:
        reg_dt = None

    return {
        'registration_date': reg_dt,
        'global_edit_count': int(row.gu_editcount) if row.gu_editcount is not None else None,
    }


def get_wiki_edit_counts(username: str) -> dict[str, int]:
    """
    Fetch per-wiki edit counts for a user from CentralAuth's localuser table.

    Returns a dict mapping wiki dbname → edit count, e.g.:
        {'nlwiki': 12847, 'commonswiki': 543, 'wikidatawiki': 120}

    Wikis with NULL edit count are excluded (old records before CentralAuth
    started tracking counts; treat as zero by omission).

    Returns an empty dict on any error.
    Raises ReplicaUnavailableError if the replica is inaccessible.
    """
    with _centralauth().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT lu_wiki, lu_editcount
                FROM localuser
                WHERE lu_name = :name
                  AND lu_editcount IS NOT NULL
                  AND lu_editcount > 0
            """),
            {'name': username},
        ).fetchall()

    return {row.lu_wiki: int(row.lu_editcount) for row in rows}
