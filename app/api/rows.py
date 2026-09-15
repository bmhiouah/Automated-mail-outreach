"""Generic row writes. Everything that touches a table by name passes through here, so the field whitelist is one map."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                    # noqa: E402
from domain import now_iso   # noqa: E402
from taxonomy import APPLICATION_FIELDS, COMPANY_FIELDS, CONTACT_FIELDS  # noqa: E402
from taxonomy import OUTREACH_FIELDS, TEMPLATE_FIELDS  # noqa: E402


def update_row(table, row_id, payload, fields):
    sets, args = [], []
    for f in fields:
        if f in payload:
            sets.append(f"{f}=?")
            args.append(payload[f])
    if not sets:
        return {"error": "nothing to update"}
    if "updated_at" in [r["name"] for r in db.query(f"PRAGMA table_info({table})")]:
        sets.append("updated_at=?")
        args.append(now_iso())
    args.append(row_id)
    db.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", args)
    rows = db.query(f"SELECT * FROM {table} WHERE id=?", [row_id])
    return rows[0] if rows else {"ok": True}

def insert_row(table, payload, fields):
    cols = [f for f in fields if payload.get(f) not in (None, "")]
    if not cols:
        return {"error": "no data"}
    vals = [payload[f] for f in cols]
    new_id = db.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals)
    rows = db.query(f"SELECT * FROM {table} WHERE id=?", [new_id])
    return rows[0] if rows else {"id": new_id}


# The tables a generic PUT/DELETE may touch. Deliberately a whitelist rather than
# "whatever the path says": the table name is interpolated into SQL, so it must
# come from here and never from the request.
TABLE_FIELDS = {
    "companies": COMPANY_FIELDS, "contacts": CONTACT_FIELDS,
    "outreach": OUTREACH_FIELDS, "templates": TEMPLATE_FIELDS,
    "applications": APPLICATION_FIELDS,
}


def update_any(table, row_id, payload):
    return update_row(table, row_id, payload, TABLE_FIELDS[table])


def delete_any(table, row_id):
    db.execute(f"DELETE FROM {table} WHERE id=?", [row_id])
    return {"ok": True}


ROUTES = [
    ("PUT", "companies/<id>",
     lambda p, rest, body: update_any("companies", rest[0], body)),
    ("PUT", "contacts/<id>",
     lambda p, rest, body: update_any("contacts", rest[0], body)),
    ("PUT", "outreach/<id>",
     lambda p, rest, body: update_any("outreach", rest[0], body)),
    ("PUT", "templates/<id>",
     lambda p, rest, body: update_any("templates", rest[0], body)),
    ("PUT", "applications/<id>",
     lambda p, rest, body: update_any("applications", rest[0], body)),
    ("DELETE", "companies/<id>",
     lambda p, rest, body: delete_any("companies", rest[0])),
    ("DELETE", "contacts/<id>",
     lambda p, rest, body: delete_any("contacts", rest[0])),
    ("DELETE", "outreach/<id>",
     lambda p, rest, body: delete_any("outreach", rest[0])),
    ("DELETE", "templates/<id>",
     lambda p, rest, body: delete_any("templates", rest[0])),
    ("DELETE", "applications/<id>",
     lambda p, rest, body: delete_any("applications", rest[0])),
]
