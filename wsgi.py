"""
wsgi.py — Toolforge Kubernetes entry point.

uWSGI loads this module and calls the `application` callable for every request.

Usage (via uwsgi.ini):
    uwsgi --ini uwsgi.ini

Local development (Flask dev server, not for production):
    FLASK_APP=wsgi:application flask run
"""

from app import create_app

application = create_app()

with application.app_context():
    from sqlalchemy import inspect
    from app import db  # noqa: E402
    from flask_migrate import upgrade, stamp

    if "alembic_version" not in inspect(db.engine).get_table_names():
        # Tables were created by Flask-Session's create_all but Alembic has no
        # record of them — stamp as head so upgrade() doesn't try to re-create.
        stamp()
    else:
        # Apply any pending migrations; no-op if already at head.
        upgrade()
