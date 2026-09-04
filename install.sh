#!/bin/sh
set -eu

echo "Installing Python dependencies..."

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REQ_FILE="$SCRIPT_DIR/requirements.txt"

if [ ! -f "$REQ_FILE" ]; then
	echo "ERROR: requirements file not found at $REQ_FILE"
	exit 1
fi

pip3 install --user -r "$REQ_FILE"

echo "Installation finished successfully."
