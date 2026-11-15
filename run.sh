#!/usr/bin/env bash
# Ouranos-Pro launcher (macOS / Linux).
#   ./run.sh            start the app and open the operator screen
#   ./run.sh --kiosk    also open the projector screen in kiosk mode (Chrome/Chromium/Edge)
#   ./run.sh --no-browser
#   PORT=9000 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8765}"
BASE="http://127.0.0.1:$PORT"
KIOSK=0; OPEN=1
for arg in "$@"; do
  case "$arg" in
    --kiosk) KIOSK=1 ;;
    --no-browser) OPEN=0 ;;
  esac
done

if [ ! -x .venv/bin/python ] || [ ! -f data/bible.sqlite ]; then
  echo "Ouranos-Pro is not set up yet. Run ./setup.sh first." >&2
  exit 1
fi

open_url() { if command -v xdg-open >/dev/null 2>&1; then xdg-open "$1"; else open "$1"; fi; }

find_browser() {
  for b in google-chrome chromium chromium-browser microsoft-edge \
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
           "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"; do
    if command -v "$b" >/dev/null 2>&1 || [ -x "$b" ]; then echo "$b"; return; fi
  done
}

(
  # Wait until the server answers, then open the windows.
  for _ in $(seq 1 60); do
    .venv/bin/python -c "import urllib.request,sys; urllib.request.urlopen('$BASE/api/state', timeout=1)" 2>/dev/null && break
    sleep 0.5
  done
  [ "$OPEN" = 1 ] && open_url "$BASE/operator" >/dev/null 2>&1 || true
  if [ "$KIOSK" = 1 ]; then
    BROWSER="$(find_browser)"
    if [ -n "$BROWSER" ]; then
      "$BROWSER" --kiosk "$BASE/display" --user-data-dir="$HOME/.ouranos-pro-kiosk" --no-first-run >/dev/null 2>&1 &
    else
      echo "Chrome/Edge not found: open $BASE/display on the projector and press F11."
    fi
  fi
) &

echo "Ouranos-Pro is starting ..."
echo "  Operator screen : $BASE/operator"
echo "  Projector screen: $BASE/display"
echo "  Stop with Ctrl+C"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning
