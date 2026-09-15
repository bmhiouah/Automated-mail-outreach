"""Maintenance: re-import the seed files without restarting the server."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402


def api_seed_companies():
    """Idempotent: matches on name, so re-running only fills what is missing."""
    return {"result": db.load_seed(verbose=False)}


def api_reload_aliases():
    return {"loaded": db.load_aliases()}


ROUTES = [
    ("POST", "seed/companies", lambda p, rest, body: api_seed_companies()),
    ("POST", "aliases/reload", lambda p, rest, body: api_reload_aliases()),
]