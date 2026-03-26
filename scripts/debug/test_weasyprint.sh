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
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPS="$HOME/deps/usr/lib/x86_64-linux-gnu"
PYTHON="$HOME/www/python/venv/bin/python"

export LD_LIBRARY_PATH=$DEPS
exec $PYTHON "$SCRIPT_DIR/test_weasyprint.py"
