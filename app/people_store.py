"""One person, one row: identity resolution and merge rules.

Both providers feed this. Without it, Hunter and Prospeo would each create
their own copy of the same human, and the database would fill with duplicates
instead of getting better.

Identity, strongest signal first:
    linkedin_url  >  email  >  (first, last, company)

Merge rule, and it is the one that matters:
    **A machine never overwrites a human.** Fields are filled only when empty.
    An address is only replaced when the existing one is a guess and the new
    one is real. Your curated contacts survive every later harvest.

`position_raw` is the exception that proves the rule: it is set once, verbatim,
and never rewritten, because it is the record of what the source actually said.
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db          # noqa: E402
# The title vocabulary, not the web layer: importing `server` here meant the
# harvester loaded the whole HTTP app to call one lookup function.
from taxonomy import derive_from_title  # noqa: E402
from email_pattern import learn_from_person, reconstruct, company_for_domain  # noqa: E402,E501

# How much we trust an address source, low to high. Used only to decide whether
# a new value is allowed to replace an old one.
EMAIL_TRUST = {"missing": 0, "bounced": 0, "guessed": 1, "unknown": 2, "verified": 3}


def now():
    return datetime.now().isoformat(timespec="seconds")


def _norm(s):
    return (s or "").strip().lower()


def _first(person, *keys):
    """First non-empty value among `keys`.

    The two providers name the same facts differently (Hunter says `linkedin`,
    `confidence`, `seniority`; Prospeo says `linkedin_url`, `seniority_level`).
    Rather than forcing every provider through a translation layer, the store
    accepts both spellings - one less place for a silent mismatch to hide.
    """
    for k in keys:
        v = person.get(k)
        if v not in (None, ""):
            return v
    return ""


def _clean_url(u):
    u = (u or "").strip()
    if not u:
        return ""
    u = u.split("?")[0].rstrip("/")
    for p in ("https://www.", "http://www.", "https://", "http://"):
        if u.lower().startswith(p):
            u = u[len(p):]
    return u.lower()


def find(person, company):
    """Locate the existing row for this person, or None.

    LinkedIn wins because it is a stable global identifier; email is next;
    name+company is the weakest and is what the old importer relied on alone.
    """
    linkedin = _clean_url(person.get("linkedin_url"))
    if linkedin:
        rows = db.query(
            "SELECT * FROM contacts WHERE lower(replace(replace(COALESCE(linkedin_url,''),"
            "'https://www.',''),'http://www.',''))=? OR lower(linkedin_url) LIKE ?",
            [linkedin, f"%{linkedin.split('/')[-1]}%"])
        if rows:
            return rows[0]

    email = _norm(person.get("email"))
    if email:
        rows = db.query("SELECT * FROM contacts WHERE lower(email)=?", [email])
        if rows:
            return rows[0]

    first, last = person.get("first_name"), person.get("last_name")
    if first and last and company:
        rows = db.query(
            "SELECT * FROM contacts WHERE lower(first_name)=lower(?) "
            "AND lower(last_name)=lower(?) AND lower(COALESCE(company_name,''))=lower(?)",
            [first, last, company["name"]])
        if rows:
            return rows[0]
    return None


def _values(person, company):
    """Flatten a provider person into our column names, deriving what we can."""
    title = _first(person, "job_title", "position_raw", "position") or ""
    seniority, desk = derive_from_title(title)
    first = (_first(person, "first_name") or "").strip()
    last = (_first(person, "last_name") or "").strip()

    v = {
        "full_name": _first(person, "full_name") or f"{first} {last}".strip(),
        "job_title": title,
        "position_raw": _first(person, "position_raw", "position") or title,
        "headline": _first(person, "headline") or "",
        "department": _first(person, "department") or "",
        "seniority_level": _first(person, "seniority_level", "seniority") or "",
        "city": _first(person, "city") or "",
        "state": _first(person, "state") or "",
        "country": _first(person, "country") or "",
        "country_code": _first(person, "country_code") or "",
        "location_raw": _first(person, "location_raw") or "",
        "timezone": _first(person, "timezone") or "",
        "linkedin_url": _first(person, "linkedin_url", "linkedin") or "",
        "phone": _first(person, "phone", "phone_number") or "",
        "email_confidence": _first(person, "email_confidence", "confidence") or None,
        "email_verified_at": _first(person, "email_verified_at", "verification_date") or "",
        "last_seen_at": _first(person, "last_seen_at") or "",
    }
    if seniority:
        v["seniority"] = seniority
    if desk:
        v["desk"] = desk
    return v


def _merge(existing, incoming):
    """Keep what a human wrote; fill only the gaps."""
    sets, args = [], []
    for f, v in incoming.items():
        if v in (None, ""):
            continue
        if (existing.get(f) or "") not in (None, ""):
            continue                      # already has a value: leave it alone
        sets.append(f"{f}=?")
        args.append(v)
    return sets, args


def upsert(person, company, provider="hunter"):
    """Insert or update one harvested person. Returns (outcome, contact_id).

    Also teaches the firm's email convention whenever we see a real address
    next to a real name - which is how one contact makes the next one free.
    """
    first = (person.get("first_name") or "").strip()
    last = (person.get("last_name") or "").strip()
    if not first and not last:
        return "skipped", None

    email = _norm(person.get("email"))
    # An address whose local part is masked ("s****@firm.com") is not an
    # address. Storing it would poison the column and the pattern engine.
    if email and "*" in email.split("@")[0]:
        email = ""
    vstatus = _first(person, "email_verification", "verification_status").lower()
    new_status = "verified" if vstatus == "valid" else ("unknown" if email else "missing")

    values = _values(person, company)
    existing = find(person, company)
    source_tag = provider

    if existing:
        sets, args = _merge(existing, values)

        # Address: fill when empty, or upgrade a guess to something real.
        if email:
            cur = (existing.get("email") or "").strip()
            cur_status = (existing.get("email_status") or "").strip()
            if not cur or EMAIL_TRUST.get(new_status, 0) > EMAIL_TRUST.get(cur_status, 0):
                sets += ["email=?", "email_status=?", "email_source=?"]
                args += [email, new_status, provider]

        # Remember which providers contributed to this row.
        seen = [s for s in (existing.get("sources") or "").split(",") if s]
        if source_tag not in seen:
            seen.append(source_tag)
            sets.append("sources=?")
            args.append(",".join(seen))

        if not sets:
            return "unchanged", existing["id"]
        sets += ["source_updated=?", "updated_at=?"]
        args += [now(), now(), existing["id"]]
        db.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", args)
        contact_id = existing["id"]
        outcome = "updated"
    else:
        cols = ["first_name", "last_name", "company_id", "company_name", "source",
                "sources", "status", "priority", "email", "email_status",
                "email_source", "source_updated", "updated_at"]
        vals = [first, last, company["id"], company["name"], provider, source_tag,
                "identified", 3, email, new_status if email else "missing",
                provider if email else "", now(), now()]
        for f, v in values.items():
            if v not in (None, ""):
                cols.append(f)
                vals.append(v)
        contact_id = db.execute(
            f"INSERT INTO contacts ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})", vals)
        outcome = "added"

    # One real (name, address) pair teaches the convention for the whole firm.
    if email:
        learn_from_person(first, last, email, company)

    return outcome, contact_id


def save_job_history(contact_id, rows, provider="prospeo"):
    """Store career history. Replaces the firm's history rather than appending,
    so a re-enrichment updates instead of duplicating the same five roles."""
    if not contact_id or not rows:
        return 0
    db.execute("DELETE FROM person_job_history WHERE contact_id=? AND source=?",
               [contact_id, provider])
    n = 0
    for j in rows:
        title = j.get("title")
        if not title:
            continue
        co = None
        if j.get("company_name"):
            co = db.resolve_company(j["company_name"])
        db.execute(
            "INSERT INTO person_job_history (contact_id,title,company_name,company_id,"
            "seniority,start_year,start_month,end_year,end_month,duration_months,"
            "is_current,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [contact_id, title, j.get("company_name") or "", co["id"] if co else None,
             j.get("seniority") or "", j.get("start_year"), j.get("start_month"),
             j.get("end_year"), j.get("end_month"), j.get("duration_months"),
             1 if j.get("is_current") else 0, provider])
        n += 1
    return n


def note_source_id(contact_id, provider, external_id):
    """Remember the provider's own id so we can re-enrich without re-searching."""
    if not (contact_id and external_id):
        return
    rows = db.query("SELECT source_ids FROM contacts WHERE id=?", [contact_id])
    ids = {}
    if rows and rows[0].get("source_ids"):
        try:
            ids = json.loads(rows[0]["source_ids"])
        except ValueError:
            ids = {}
    ids[provider] = external_id
    db.execute("UPDATE contacts SET source_ids=? WHERE id=?",
               [json.dumps(ids), contact_id])


def resolve_or_create_company(domain=None, name=None, enrichment=None):
    """Find the firm, or add it. Harvesting discovers firms we never seeded.

    New firms arrive at tier 3 and status 'to_research' - they are raw
    discoveries, not firms we have decided to target.
    """
    company = None
    if domain:
        company = company_for_domain(domain)
    if not company and name:
        company = db.resolve_company(name)
    if company:
        return company, False

    label = name or domain or "Unknown"
    cid = db.execute(
        "INSERT INTO companies (name, domain, type, tier, status, source) "
        "VALUES (?,?,?,?,?,?)",
        [label, domain or "", "other", 3, "to_research", "harvest"])
    rows = db.query("SELECT * FROM companies WHERE id=?", [cid])
    return (rows[0] if rows else None), True


def fill_company(company, enrichment, provider="prospeo"):
    """Fill empty firm profile fields only. Your own edits are never touched."""
    fields = ["description", "founded_year", "headcount", "employee_count", "industry",
              "company_type", "keywords", "address", "linkedin_url", "twitter",
              "ticker", "website", "logo_url", "revenue_printed", "funding_total",
              "technologies", "job_postings_count", "hq_city", "hq_country"]
    sets, args = [], []
    for f in fields:
        v = enrichment.get(f)
        if v in (None, "", [], {}):
            continue
        if (company.get(f) or "") not in (None, ""):
            continue
        sets.append(f"{f}=?")
        args.append(",".join(v) if isinstance(v, list) else v)
    if enrichment.get("domain") and not (company.get("domain") or "").strip():
        sets.append("domain=?")
        args.append(enrichment["domain"])
    if enrichment.get("prospeo_id") and not (company.get("prospeo_id") or "").strip():
        sets.append("prospeo_id=?")
        args.append(enrichment["prospeo_id"])
    if not sets:
        return False
    sets += ["source=?", "source_updated=?", "updated_at=?"]
    args += [provider, now(), now(), company["id"]]
    db.execute(f"UPDATE companies SET {', '.join(sets)} WHERE id=?", args)
    return True


def reconstruct_missing(company_id=None):
    """Give an address to everyone we can, from the firm's known convention."""
    from email_pattern import apply_guesses
    return apply_guesses(company_id)
