"""Contacts: the import paths, the desk/seniority backfill and the duplicate report."""

import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                            # noqa: E402
from domain import now_iso           # noqa: E402
from taxonomy import CONTACT_FIELDS  # noqa: E402
from taxonomy import CONTACT_IMPORT_MAP  # noqa: E402
from taxonomy import derive_from_title   # noqa: E402
from email_pattern import guess_email    # noqa: E402
from api.rows import insert_row          # noqa: E402


def api_contacts(params):
    where, args = [], []
    if params.get("company_id"):
        where.append("c.company_id=?")
        args.append(params["company_id"][0])
    if params.get("q"):
        where.append("(lower(c.first_name||' '||c.last_name) LIKE ? OR lower(COALESCE(c.company_name,'')) LIKE ? OR lower(COALESCE(c.job_title,'')) LIKE ?)")
        args += [f"%{params['q'][0].lower()}%"] * 3
    sql = ("SELECT c.*, co.type AS company_type, co.domain AS company_domain, "
           "co.email_pattern AS company_pattern, co.tier AS company_tier "
           "FROM contacts c LEFT JOIN companies co ON co.id = c.company_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.updated_at DESC"
    rows = db.query(sql, args)
    for r in rows:
        if not r.get("email"):
            r["email_guess"] = guess_email(
                r, {"domain": r.get("company_domain"), "email_pattern": r.get("company_pattern")}
            )
        else:
            r["email_guess"] = r["email"]
    return rows

def retag_contacts(overwrite=False):
    """Fill seniority and desk from job titles across the whole contact list."""
    rows = db.query("SELECT id, job_title, seniority, desk FROM contacts")
    n = 0
    for r in rows:
        seniority, desk = derive_from_title(r["job_title"])
        sets, args = [], []
        if seniority and (overwrite or not (r["seniority"] or "").strip()):
            sets.append("seniority=?")
            args.append(seniority)
        if desk and (overwrite or not (r["desk"] or "").strip()):
            sets.append("desk=?")
            args.append(desk)
        if not sets:
            continue
        args.append(now_iso())
        args.append(r["id"])
        db.execute(f"UPDATE contacts SET {', '.join(sets)}, updated_at=? WHERE id=?", args)
        n += 1
    return {"updated": n}

def find_duplicates():
    """Contacts that are probably the same person, or share an address."""
    by_email = db.query(
        "SELECT lower(email) k, COUNT(*) n, GROUP_CONCAT(id) ids FROM contacts "
        "WHERE email IS NOT NULL AND email!='' GROUP BY lower(email) HAVING n>1")
    by_name = db.query(
        "SELECT lower(first_name||' '||last_name) k, COUNT(*) n, GROUP_CONCAT(id) ids "
        "FROM contacts GROUP BY k HAVING n>1")
    return {"same_email": by_email, "same_name": by_name}

def api_import_contacts(payload):
    text = payload.get("csv_text") or ""
    default_company = (payload.get("company_name") or "").strip()
    delimiter = payload.get("delimiter") or ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return {"error": "empty input"}
    header = [c.strip().lower() for c in rows[0]]
    has_header = any(h in CONTACT_IMPORT_MAP for h in header)
    mapping = []
    if has_header:
        for h in header:
            mapping.append(CONTACT_IMPORT_MAP.get(h))
        data_rows = rows[1:]
    else:
        # assume: first,last,title,company,email,(linkedin)
        mapping = ["first_name", "last_name", "job_title", "company_name", "email", "linkedin_url"]
        data_rows = rows
    added, updated, skipped = 0, 0, 0
    for row in data_rows:
        rec = {}
        for i, field in enumerate(mapping):
            if field and i < len(row):
                rec[field] = row[i].strip()
        first = rec.get("first_name", "").strip()
        last = rec.get("last_name", "").strip()
        if not first and not last:
            skipped += 1
            continue
        cname = rec.get("company_name") or default_company
        if not cname:
            cname = "Unknown"
        co = db.resolve_company(cname)
        rec["company_name"] = co["name"] if co else cname
        rec["company_id"] = co["id"] if co else None
        if co and co.get("hq_city") and not rec.get("city"):
            rec["city"] = co["hq_city"]
        seniority, desk = derive_from_title(rec.get("job_title"))
        if seniority and not rec.get("seniority"):
            rec["seniority"] = seniority
        if desk and not rec.get("desk"):
            rec["desk"] = desk
        existing = db.query(
            "SELECT id FROM contacts WHERE lower(first_name)=lower(?) AND lower(last_name)=lower(?) "
            "AND lower(COALESCE(company_name,''))=lower(?)", [first, last, cname])
        if existing:
            sets, args = [], []
            for f in CONTACT_FIELDS:
                if f in rec and rec[f]:
                    sets.append(f"{f}=?")
                    args.append(rec[f])
            if sets:
                args.append(existing[0]["id"])
                db.execute(f"UPDATE contacts SET {', '.join(sets)}, updated_at=CURRENT_TIMESTAMP WHERE id=?", args)
                updated += 1
            else:
                skipped += 1
        else:
            cols = ["first_name", "last_name"] + [f for f in CONTACT_FIELDS if f not in ("first_name", "last_name") and rec.get(f)]
            vals = [first, last] + [rec[f] for f in cols[2:]]
            db.execute(f"INSERT INTO contacts ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals)
            added += 1
    return {"added": added, "updated": updated, "skipped": skipped}


ROUTES = [
    ("GET", "contacts", lambda p, rest, body: api_contacts(p)),
    ("POST", "contacts",
     lambda p, rest, body: insert_row("contacts", body, CONTACT_FIELDS)),
    ("POST", "import/contacts", lambda p, rest, body: api_import_contacts(body)),
    ("POST", "retag", lambda p, rest, body: retag_contacts(bool(body.get("overwrite")))),
    ("POST", "duplicates", lambda p, rest, body: find_duplicates()),
]
