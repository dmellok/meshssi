#!/bin/sh
# Runs meshssi straight from this checkout, so it works even if macOS hides the venv's .pth file.
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$DIR/.venv/bin/python" -m meshssi "$@"
