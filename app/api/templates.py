"""Templates: the four defaults and their editing."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db         # noqa: E402
import email_gen  # noqa: E402
from taxonomy import TEMPLATE_FIELDS  # noqa: E402
from api.rows import insert_row       # noqa: E402


def api_seed_templates():
    existing = {t["name"]: t["id"] for t in db.query("SELECT id, name FROM templates")}
    created = updated = 0
    for t in email_gen.SEED_TEMPLATES:
        if t["name"] in existing:
            db.execute(
                "UPDATE templates SET role_family=?, subject_tpl=?, body_tpl=?, notes=? WHERE id=?",
                [t["role_family"], t["subject_tpl"], t["body_tpl"], t["notes"],
                 existing[t["name"]]])
            updated += 1
        else:
            db.execute(
                "INSERT INTO templates (name, role_family, subject_tpl, body_tpl, notes) "
                "VALUES (?,?,?,?,?)",
                [t["name"], t["role_family"], t["subject_tpl"], t["body_tpl"], t["notes"]])
            created += 1
    return {"created": created, "updated": updated}


def api_templates(params=None):
    return db.query("SELECT * FROM templates ORDER BY id")


ROUTES = [
    ("GET", "templates", lambda p, rest, body: api_templates(p)),
    ("POST", "templates",
     lambda p, rest, body: insert_row("templates", body, TEMPLATE_FIELDS)),
    ("POST", "seed/templates", lambda p, rest, body: api_seed_templates()),
]
