#!/usr/bin/env bash
# deploy.sh — pull latest code and restart the Toolforge webservice.
# Run from any directory as the tool account (become profile-creator-nlwiki).
# Migrations are applied automatically on startup by wsgi.py.
set -euo pipefail

cd ~/wall-of-faces
git pull
uv pip install --python ~/www/python/venv/bin/python -r requirements.txt
cd ~
webservice --backend=kubernetes python3.13 restart
