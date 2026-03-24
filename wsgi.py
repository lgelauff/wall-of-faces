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
