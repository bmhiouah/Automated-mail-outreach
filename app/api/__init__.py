"""The JSON API: one module per surface, plus the table that maps a request to one.

A route is `(method, pattern, handler)`. The pattern is the path after `/api`,
with `<name>` standing for a captured segment. The handler is called as
`handler(params, rest, payload)` and returns JSON-able data - or a `Raw` when it
wants its bytes sent as-is (the two export endpoints).

Two deliberate tightenings versus the old if/elif chain, both in the direction of
refusing rather than guessing:

  * a path with trailing segments no pattern accounts for
    (`GET /api/sourcing/5/extra`) is a 404 instead of being silently ignored;
  * `PUT`/`DELETE` only accept tables on the field whitelist in `api/rows.py`,
    so an unknown table is a 404 rather than an interpolated SQL error.

Everything else - status codes included - is unchanged, and `tests/test_api.py`
walks this table to prove it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from domain import Raw  # noqa: E402,F401  (re-exported: server.py sends it)

from api import analytics, applications, companies, compose, contacts  # noqa: E402
from api import demo, export, maintenance, outreach, patterns, people  # noqa: E402
from api import profile, rows, sourcing, templates  # noqa: E402

MODULES = (analytics, applications, companies, compose, contacts, demo, export,
           maintenance, outreach, patterns, people, profile, rows, sourcing,
           templates)

# One flat table, in module order. Order is not load-bearing: no two patterns
# differ only in what a <capture> would match.
ROUTES = [route for mod in MODULES for route in getattr(mod, "ROUTES", [])]


def match(pattern, parts):
    """Captured segments for this pattern, or None if it does not fit."""
    pat = pattern.split("/")
    if len(pat) != len(parts):
        return None
    captured = []
    for want, got in zip(pat, parts):
        if want.startswith("<") and want.endswith(">"):
            captured.append(got)
        elif want != got:
            return None
    return captured


def dispatch(method, parts, params, payload):
    """Resolve one API call. Returns (status, value); value is JSON-able or Raw."""
    if not parts:
        return 200, {"ok": True}
    for route_method, pattern, handler in ROUTES:
        if route_method != method:
            continue
        captured = match(pattern, parts)
        if captured is None:
            continue
        try:
            return 200, handler(params, captured, payload or {})
        except Exception as exc:      # keep the server alive, surface the error
            return 500, {"error": f"{type(exc).__name__}: {exc}"}
    return 404, {"error": "unknown route"}


def routes():
    """The table, as plain strings - for the docs and the dispatch test."""
    return ["%s /api/%s" % (method, pattern) for method, pattern, _ in ROUTES]
