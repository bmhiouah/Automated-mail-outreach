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
    }
    # Confidence and verification dates live in the per-source tables now;
    # the unified contact keeps only the verdict (email_status).
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


# --------------------------------------------------------------- per-source rows
# The raw provider payload lands in its own table before the unified merge, so
# nothing a provider offers is ever lost - masked phone variants included.
# `contacts` is the curated vertical concat of these two, with `source` set.

HUNTER_ROW = ["domain", "email", "email_type", "confidence", "first_name",
              "last_name", "position", "position_raw", "seniority", "department",
              "decision_maker", "linkedin", "twitter", "phone_number",
              "verification_status", "verification_date", "sources", "company_name"]

PROSPEO_ROW = ["person_id", "first_name", "last_name", "full_name", "linkedin_url",
               "linkedin_member_id", "current_job_title", "current_job_key",
               "headline", "last_job_change_detected_at", "email", "email_revealed",
               "email_status", "email_verification_method", "email_mx_provider",
               "mobile", "mobile_national", "mobile_international", "mobile_status",
               "mobile_revealed", "mobile_country", "mobile_country_code",
               "city", "state", "country", "country_code", "time_zone",
               "skills", "job_history", "company"]

# provider normaliser key -> contacts_hunter column
HUNTER_MAP = {
    "domain": "domain", "email": "email", "email_type": "email_type",
    "confidence": "confidence", "first_name": "first_name", "last_name": "last_name",
    "position": "position", "position_raw": "position_raw", "seniority": "seniority",
    "department": "department", "decision_maker": "decision_maker",
    "linkedin": "linkedin", "twitter": "twitter", "phone": "phone_number",
    "verification_status": "verification_status",
    "verification_date": "verification_date", "email_sources": "sources",
}

# provider normaliser key -> contacts_prospeo column
PROSPEO_MAP = {
    "person_id": "person_id", "first_name": "first_name", "last_name": "last_name",
    "full_name": "full_name", "linkedin_url": "linkedin_url",
    "linkedin_member_id": "linkedin_member_id",
    "job_title": "current_job_title", "current_job_key": "current_job_key",
    "headline": "headline", "last_job_change": "last_job_change_detected_at",
    "email_raw": "email", "email_revealed": "email_revealed",
    "email_verification": "email_status",
    "email_verification_method": "email_verification_method",
    "email_mx_provider": "email_mx_provider",
    "phone_masked_or_real": "mobile",           # verbatim: masked or not
    "mobile_national": "mobile_national",
    "mobile_international": "mobile_international",
    "phone_status": "mobile_status", "mobile_revealed": "mobile_revealed",
    "mobile_country": "mobile_country", "mobile_country_code": "mobile_country_code",
    "city": "city", "state": "state", "country": "country",
    "country_code": "country_code", "timezone": "time_zone",
}


def save_source_row(person, company, provider):
    """Write one provider payload to contacts_<provider>, verbatim.

    Runs inside upsert(), before the unified merge. Masked or empty values are
    stored as-is: this table is the record of what the API actually said.
    Returns the row id, or None when there is nothing to key on.
    """
    if provider == "hunter":
        table, cols, mapping = "contacts_hunter", HUNTER_ROW, HUNTER_MAP
        person = dict(person, domain=(company or {}).get("domain") or "",
                      company_name=(company or {}).get("name") or "")
        key = "email"
    else:
        table, cols, mapping = "contacts_prospeo", PROSPEO_ROW, PROSPEO_MAP
        person = dict(person)
        # mobile: the verbatim value - masked or real - beats either alone
        person["phone_masked_or_real"] = (person.get("email_raw") and "") or \
            person.get("phone_masked") or person.get("phone")
        key = "person_id"
    keyval = person.get(key)
    if keyval is None or (isinstance(keyval, str) and not keyval.strip()):
        return None
    values = []
    for col in cols:
        src = next((k for k, v in mapping.items() if v == col), None)
        v = person.get(src)
        if provider == "prospeo" and col in ("job_history", "company"):
            v = person.get(col)               # dicts/lists, stored as JSON
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        values.append(v)
    placeholders = ", ".join("?" for _ in cols)
    return db.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        values)


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
    masked_email = _norm(person.get("email_masked"))
    # An address whose local part is masked ("s****@firm.com") is not an
    # address. It is evidence, though: kept in email_masked, out of `email`,
    # so the pattern engine and the composer never see a half-address.
    if email and "*" in email.split("@")[0]:
        masked_email = masked_email or email
        email = ""

    # The full payload is recorded in its own table first, verbatim - masked
    # values included - before the unified merge keeps only what `contacts`
    # holds. If this fails there is nothing to merge, so no row either way.
    save_source_row(person, company, provider)

    # A masked address still proves the domain after the @: "s****@amundi.com"
    # is enough to attach a person to the right firm before anyone is revealed,
    # and that domain is the pattern_after half of the convention.
    if masked_email and company and not (company.get("domain") or "").strip():
        from email_pattern import clean_domain
        dom = clean_domain(masked_email.rsplit("@", 1)[-1])
        if dom:
            db.execute("UPDATE companies SET domain=?, updated_at=? WHERE id=? "
                       "AND (domain IS NULL OR domain='')", [dom, now(), company["id"]])
            company["domain"] = dom

    values = _values(person, company)
    # A masked mobile is a hint, not a number: it stays out of `phone`.
    if "*" in (values.get("phone") or ""):
        values["phone"] = ""
    values["email_masked"] = masked_email
    existing = find(person, company)

    if existing:
        sets, args = _merge(existing, values)

        # Address: fill when it is empty, never overwrite. A pasted or already
        # harvested address outranks anything a later source claims; the
        # per-source tables keep every variant for the audit anyway.
        if email and not (existing.get("email") or "").strip():
            sets += ["email=?", "email_source=?"]
            args += [email, provider]
            # A real address retires the masked evidence.
            sets.append("email_masked=?")
            args.append("")

        if not sets:
            return "unchanged", existing["id"]
        sets += ["updated_at=?"]
        args += [now(), existing["id"]]
        db.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", args)
        contact_id = existing["id"]
        outcome = "updated"
    else:
        cols = ["first_name", "last_name", "company_id", "company_name", "source",
                "email", "email_source", "updated_at"]
        vals = [first, last, company["id"], company["name"], provider,
                email, provider if email else "", now()]
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
            "is_current,departments,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [contact_id, title, j.get("company_name") or "", co["id"] if co else None,
             j.get("seniority") or "", j.get("start_year"), j.get("start_month"),
             j.get("end_year"), j.get("end_month"), j.get("duration_months"),
             1 if j.get("is_current") else 0,
             ", ".join(j.get("departments") or []), provider])
        n += 1
    return n


def note_source_id(contact_id, provider, external_id):
    """No longer needed: the per-source tables (contacts_prospeo etc.) hold the
    provider's own id, keyed by person_id / (domain, email). Kept as a no-op so
    older callers do not break."""
    return None


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
    """Give an address to everyone we can, from the firm's known convention.

    Two passes, in this order: empty addresses first (plain fills), then the
    masked ones - because every address the first pass writes is evidence the
    second pass can lean on when the pattern engine re-learns the firm.
    """
    from email_pattern import apply_guesses, apply_masked
    out = apply_guesses(company_id)
    masked = apply_masked(company_id)
    return {"updated": out["updated"] + masked["updated"],
            "from_masked": masked["updated"]}
