#!/bin/sh
set -eu

echo "Installing Python dependencies..."

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REQ_FILE="$SCRIPT_DIR/requirements.txt"

if [ ! -f "$REQ_FILE" ]; then
	echo "ERROR: requirements file not found at $REQ_FILE"
	exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r "$REQ_FILE"

echo "Installation Finished successfully."
