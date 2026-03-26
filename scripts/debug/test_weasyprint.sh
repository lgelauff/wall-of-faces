#!/bin/bash
# Test WeasyPrint with bundled native libs on Toolforge.
#
# Usage (on bastion):
#   bash ~/wall-of-faces/scripts/debug/test_weasyprint.sh
#
# Usage (via Toolforge jobs):
#   toolforge jobs run test-wp --image python3.13 \
#     --command "bash ~/wall-of-faces/scripts/debug/test_weasyprint.sh" --wait

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS="$HOME/deps/usr/lib/x86_64-linux-gnu"
PYTHON="$HOME/www/python/venv/bin/python"
FONTS_DIR="$HOME/deps/usr/share/fonts"
FONTS_CONF="$HOME/deps/etc/fonts/fonts.conf"

# Generate minimal fonts.conf pointing to our bundled fonts
cat > "$FONTS_CONF" <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>$FONTS_DIR</dir>
  <cachedir>$HOME/.fontconfig</cachedir>
</fontconfig>
EOF

export LD_LIBRARY_PATH="$DEPS"
export FONTCONFIG_FILE="$FONTS_CONF"

exec "$PYTHON" "$SCRIPT_DIR/test_weasyprint.py"
