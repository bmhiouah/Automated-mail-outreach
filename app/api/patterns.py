"""Learning a firm's email convention from a real address, and spending it.

Thin: the engine is email_pattern.py. This is only the HTTP shape of the two
buttons in the Companies tab.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from email_pattern import apply_guesses, infer_pattern  # noqa: E402


def api_infer_pattern(payload):
    payload = payload or {}
    return infer_pattern(payload.get("company_id"), payload.get("sample_email"))


def api_apply_guesses(payload):
    return apply_guesses((payload or {}).get("company_id"))


ROUTES = [
    ("POST", "infer-pattern", lambda p, rest, body: api_infer_pattern(body)),
    ("POST", "apply-guesses", lambda p, rest, body: api_apply_guesses(body)),
]