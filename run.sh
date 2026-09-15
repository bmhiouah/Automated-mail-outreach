#!/bin/zsh
# Start the cold approach tracker.  Override the interpreter with e.g.
#   PYTHON=/usr/bin/python3 ./run.sh
# Any Python 3.9+ works: the app is stdlib only (CI tests 3.10 and 3.13).
cd "$(dirname "$0")" || exit 1

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in \
    "/Users/badremhiouah/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3" \
    "$(command -v python3)" ; do
    if [ -x "$candidate" ]; then
      PY="$candidate"
      break
    fi
  done
fi
[ -n "$PY" ] || { echo "  No python3 found. Set PYTHON=/path/to/python3"; exit 1; }

echo "  Cold approach tracker -> http://127.0.0.1:8765  (Ctrl+C to stop)"
echo "  interpreter: $PY  ($("$PY" -V 2>&1))"
open "http://127.0.0.1:8765" 2>/dev/null &
exec "$PY" app/server.py
