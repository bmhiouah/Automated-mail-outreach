#!/bin/zsh
# Start the cold approach tracker.
cd "$(dirname "$0")" || exit 1
PY="/Users/badremhiouah/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
echo "  Cold approach tracker -> http://127.0.0.1:8765  (Ctrl+C to stop)"
open "http://127.0.0.1:8765" 2>/dev/null &
exec "$PY" app/server.py
