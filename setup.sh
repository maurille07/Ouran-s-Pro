#!/usr/bin/env bash
# Ouranos-Pro setup (macOS / Linux).
# Needs internet ONCE. After this, the app works fully offline.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Ouranos-Pro setup ==="

if [ ! -x .venv/bin/python ]; then
  PY=""
  for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      PY="$candidate"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "ERROR: Python 3.11 or newer was not found. Install it and run this setup again." >&2
    exit 1
  fi
  echo "Creating virtual environment with $PY ..."
  "$PY" -m venv .venv
else
  echo "Virtual environment already exists."
fi

echo "Installing libraries (this can take several minutes the first time) ..."
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python scripts/build_bible_db.py

# Offline speech models (about 630 MB): "small" is the default, "base" is faster.
.venv/bin/python scripts/download_models.py --whisper small
.venv/bin/python scripts/download_models.py --whisper base

# Quoted-verse detection: embedding model + one embedding per verse (5-15 minutes, once).
.venv/bin/python scripts/download_models.py --embedding
.venv/bin/python scripts/build_embeddings.py

echo
echo "Setup complete. Start the app with:  ./run.sh"
