"""People capture: read a pasted blob, then save the rows that survived review."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db           # noqa: E402
import people_parse  # noqa: E402
from domain import now_iso  # noqa: E402
from taxonomy import derive_from_title  # noqa: E402
from email_pattern import learn_patterns_from_samples  # noqa: E402


def api_parse_people(payload):
    """Read a pasted blob of people text. Proposes only - writes nothing."""
    text = payload.get("text") or ""
    if len(text.strip()) < 3:
        return {"error": "nothing to read"}
    out = people_parse.parse_people(
        text,
        default_company=(payload.get("company_name") or "").strip() or None,
        resolve=db.resolve_company,
    )
    out["critical_missing"] = []
    if not out["candidates"] and not out["pattern_samples"]:
        out["critical_missing"].append(
            "no people found - check that each line has a name in it")
    return out

def api_import_people(payload):
    """Save reviewed candidates. The UI sends back the rows you kept."""
    rows = payload.get("candidates") or []
    if not isinstance(rows, list):
        return {"error": "candidates must be a list"}
    default_company = (payload.get("company_name") or "").strip()
    source = (payload.get("source") or "pasted").strip() or "pasted"
    added = updated = skipped = 0
    for rec in rows:
        if not isinstance(rec, dict):
            skipped += 1
            continue
        first = (rec.get("first_name") or "").strip()
        last = (rec.get("last_name") or "").strip()
        if not first and not last:
            skipped += 1
            continue
        cname = (rec.get("company_name") or "").strip() or default_company
        co = db.resolve_company(cname) if cname else None
        if co:
            cname = co["name"]
        elif not cname:
            cname = "Unknown"
        title = (rec.get("job_title") or "").strip()
        seniority, desk = derive_from_title(title)
        fields = {
            "job_title": title,
            "company_id": co["id"] if co else None,
            "company_name": cname,
            "city": (rec.get("city") or "").strip() or (co.get("hq_city") if co else "") or "",
            "email": (rec.get("email") or "").strip(),
            "linkedin_url": (rec.get("linkedin_url") or "").strip(),
            "source": source,
        }
        if seniority:
            fields["seniority"] = seniority
        if desk:
            fields["desk"] = desk
        if fields["email"]:
            # A pasted address is real evidence, not a guess - keep them apart
            # so 'Fill empty emails from patterns' never overwrites it.
            fields["email_status"] = "verified"
            fields["email_source"] = "pasted"
        existing = db.query(
            "SELECT id FROM contacts WHERE lower(first_name)=lower(?) "
            "AND lower(last_name)=lower(?) AND lower(COALESCE(company_name,''))=lower(?)",
            [first, last, cname])
        if existing:
            sets, args = [], []
            for f, v in fields.items():
                if v not in (None, ""):
                    sets.append(f"{f}=?")
                    args.append(v)
            if not sets:
                skipped += 1
                continue
            sets.append("updated_at=?")
            args.append(now_iso())
            args.append(existing[0]["id"])
            db.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", args)
            updated += 1
        else:
            cols = ["first_name", "last_name"] + [f for f, v in fields.items() if v not in (None, "")]
            vals = [first, last] + [v for f, v in fields.items() if v not in (None, "")]
            db.execute(f"INSERT INTO contacts ({', '.join(cols)}) "
                       f"VALUES ({', '.join('?' for _ in cols)})", vals)
            added += 1

    # Run this after the inserts: the strong path needs a contact to match
    # the address against, and we just created them.
    learning = learn_patterns_from_samples(payload.get("pattern_samples"))
    return {"added": added, "updated": updated, "skipped": skipped,
            "pattern_learning": learning}


ROUTES = [
    ("POST", "parse-people", lambda p, rest, body: api_parse_people(body)),
    ("POST", "import-people", lambda p, rest, body: api_import_people(body)),
]
