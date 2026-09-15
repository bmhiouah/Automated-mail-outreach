"""Shared helpers for the API layer: time, JSON, and the contact/company shapes every endpoint returns."""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                              # noqa: E402
from email_pattern import guess_email  # noqa: E402


class Raw:
    """A response to send as-is rather than as JSON.

    Only the two export endpoints use this: a CSV and a markdown file are not
    JSON, and pretending otherwise is how a download ends up with quotes round
    every line.
    """

    def __init__(self, body, ctype="application/octet-stream", headers=None):
        self.body = body
        self.ctype = ctype
        self.headers = headers or {}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")

def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")

def get_profile():
    rows = db.query("SELECT * FROM profile WHERE id=1")
    return rows[0] if rows else {}

def enrich_contact(contact):
    """Attach company info + derived email guess."""
    if not contact:
        return contact
    company = None
    cid = contact.get("company_id")
    if cid:
        r = db.query("SELECT * FROM companies WHERE id=?", [cid])
        company = r[0] if r else None
    if not company and contact.get("company_name"):
        r = db.query("SELECT * FROM companies WHERE lower(name)=lower(?)", [contact["company_name"]])
        company = r[0] if r else None
    contact["company"] = company or {}
    if company:
        contact["company_name"] = contact.get("company_name") or company["name"]
    if not contact.get("email"):
        guess = guess_email(contact, company)
        contact["email_guess"] = guess
    else:
        contact["email_guess"] = contact["email"]
    return contact
