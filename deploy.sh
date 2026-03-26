#!/usr/bin/env bash
# deploy.sh — pull latest code and restart the Toolforge webservice.
# Run from any directory as the tool account (become profile-creator-nlwiki).
# Migrations are applied automatically on startup by wsgi.py.
set -euo pipefail

cd ~/wall-of-faces
git pull
echo "Deploying commit: $(git log -1 --format='%h %s (%ci)')"
uv pip install --python ~/www/python/venv/bin/python -r requirements.txt

# Regenerate fonts.conf for WeasyPrint (points to bundled native fonts in ~/deps)
mkdir -p ~/deps/etc/fonts
cat > ~/deps/etc/fonts/fonts.conf <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>$HOME/deps/usr/share/fonts</dir>
  <cachedir>$HOME/.fontconfig</cachedir>
</fontconfig>
EOF

cd ~
webservice --backend=kubernetes python3.13 restart
echo "Deploy done at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
