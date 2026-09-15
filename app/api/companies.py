"""Companies: the target universe, its filters and its editable fields."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                            # noqa: E402
from taxonomy import COMPANY_FIELDS  # noqa: E402
from api.rows import insert_row      # noqa: E402


def api_companies(params, payload=None):
    where, args = [], []
    if params.get("ids"):
        ids = [int(x) for x in params["ids"][0].split(",") if x.strip().isdigit()]
        if ids:
            where.append(f"id IN ({','.join('?'*len(ids))})")
            args += ids
    if params.get("type"):
        where.append("type=?")
        args.append(params["type"][0])
    if params.get("status"):
        where.append("status=?")
        args.append(params["status"][0])
    if params.get("tier"):
        where.append("tier=?")
        args.append(params["tier"][0])
    if params.get("q"):
        where.append("(lower(name) LIKE ? OR lower(COALESCE(domain,'')) LIKE ?)")
        args += [f"%{params['q'][0].lower()}%"] * 2
    if params.get("without_contacts"):
        where.append("id NOT IN (SELECT company_id FROM contacts WHERE company_id IS NOT NULL)")
    sql = "SELECT * FROM companies"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY tier ASC, name ASC"
    return db.query(sql, args)


ROUTES = [
    ("GET", "companies", lambda p, rest, body: api_companies(p)),
    ("POST", "companies",
     lambda p, rest, body: insert_row("companies", body, COMPANY_FIELDS)),
]
