"""Local web app for the cold approach tracker.

No third-party dependencies: stdlib HTTP server + SQLite + vanilla JS frontend.
Run:  python3 app/server.py      then open http://127.0.0.1:8765

This file is the HTTP shell: the socket, the static files, the entry point. The
endpoints themselves live in app/api/, one module per surface, because they are
the part that changes - and forty branches of if/elif was hiding them.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                                      # noqa: E402
from api import Raw, dispatch                  # noqa: E402
from api.templates import api_seed_templates   # noqa: E402
from domain import json_bytes                  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE, "web")
PORT = int(os.environ.get("PORT", 8765))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send(code, json_bytes(obj))

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def route(self, method):
        """Hand an /api request to the endpoint table; serve files; nothing else.

        The endpoints moved to app/api/. This method only decides *where* the
        request goes: it owns no query, no SQL and no response shape beyond
        "JSON, unless the endpoint handed back bytes to send as-is".
        """
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        parts = [p for p in path.split("/") if p]

        if path == "/" or path == "/index.html":
            return self.serve_file(os.path.join(WEB_DIR, "index.html"),
                                   "text/html; charset=utf-8")
        if parts and parts[0] == "static":
            # basename() is what stops this walking out of web/ - keep it.
            fname = os.path.basename("/".join(parts[1:]))
            fpath = os.path.join(WEB_DIR, fname)
            if os.path.exists(fpath):
                ext = os.path.splitext(fpath)[1]
                ctype = {".js": "application/javascript", ".css": "text/css",
                         ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
                return self.serve_file(fpath, ctype)
            return self.send(404, b"not found", "text/plain")

        if not parts or parts[0] != "api":
            return self.send(404, b"not found", "text/plain")

        payload = self.read_json() if method in ("POST", "PUT", "PATCH") else None
        status, value = dispatch(method, parts[1:], parse_qs(parsed.query),
                                 payload or {})
        if isinstance(value, Raw):
            return self.send(status, value.body, value.ctype, value.headers)
        return self.send_json(value, status)

    def serve_file(self, path, ctype):
        try:
            with open(path, "rb") as fh:
                self.send(200, fh.read(), ctype)
        except FileNotFoundError:
            self.send(404, b"not found", "text/plain")

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")


def main():
    db.init_db()
    # Additive columns for harvested data. executescript() only runs
    # CREATE TABLE IF NOT EXISTS, so an existing database needs ALTER TABLE for
    # anything added to schema.sql since it was created.
    db.ensure_schema(verbose=True)
    if not db.query("SELECT id FROM templates LIMIT 1"):
        api_seed_templates()
    if not db.query("SELECT id FROM companies LIMIT 1"):
        db.load_seed(verbose=True)
    db.load_aliases()
    db.load_briefs(verbose=True)
    db.load_careers_urls(verbose=True)
    print(f"\n  Cold approach tracker running at http://127.0.0.1:{PORT}")
    print("  Ctrl+C to stop\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
