#!/bin/sh
set -e
cd "$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
if [ -x venv/bin/python ]; then
  PY=venv/bin/python
elif [ -x venv/Scripts/python.exe ]; then
  PY=venv/Scripts/python.exe
else
  echo "project venv python was not found" >&2
  exit 1
fi
exec "$PY" examples/run_match_grid.py
