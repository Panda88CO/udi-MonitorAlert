#!/bin/sh
set -eu

echo "Installing Python dependencies..."

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REQ_FILE="$SCRIPT_DIR/requirements.txt"

if [ ! -f "$REQ_FILE" ]; then
	echo "ERROR: requirements file not found at $REQ_FILE"
	exit 1
fi

# Clean up broken user-level pip if previous pip upgrade corrupted vendored dependencies
if [ -n "${HOME:-}" ] && [ -d "$HOME/.local/lib" ]; then
	find "$HOME/.local/lib" -type d -name "pip*" -maxdepth 4 -exec rm -rf {} + 2>/dev/null || true
fi

# Install dependencies to user site-packages without upgrading pip
PYTHONNOUSERSITE=1 python3 -m pip install --user -r "$REQ_FILE" || \
python3 -m pip install --user -r "$REQ_FILE" || \
pip3 install --user -r "$REQ_FILE"

echo "Installation finished successfully."
