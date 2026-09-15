"""Job applications: tracked apart from outreach, so the two channels never get
confused about what went where."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                                # noqa: E402
from taxonomy import APPLICATION_FIELDS  # noqa: E402
from api.rows import insert_row          # noqa: E402


def api_applications(params=None):
    """Open applications first: interview > online test > applied > to apply."""
    return db.query(
        "SELECT * FROM applications ORDER BY "
        "CASE status WHEN 'interview' THEN 0 WHEN 'online_test' THEN 1 "
        "WHEN 'applied' THEN 2 WHEN 'to_apply' THEN 3 ELSE 4 END, updated_at DESC")


def api_application_insert(payload):
    """Resolve the firm by name first, so the tracker can group by company later."""
    payload = payload or {}
    if not payload.get("company_id") and payload.get("company_name"):
        hit = db.query("SELECT id FROM companies WHERE lower(name)=lower(?)",
                       [payload["company_name"]])
        if hit:
            payload["company_id"] = hit[0]["id"]
    return insert_row("applications", payload, APPLICATION_FIELDS)


ROUTES = [
    ("GET", "applications", lambda p, rest, body: api_applications(p)),
    ("POST", "applications", lambda p, rest, body: api_application_insert(body)),
]