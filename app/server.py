"""Local web app for the cold approach tracker.

No third-party dependencies: stdlib HTTP server + SQLite + vanilla JS frontend.
Run:  python3 app/server.py      then open http://127.0.0.1:8765
"""
import csv
import io
import json
import os
import sys
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote_plus

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv_parse
import db
import email_gen
import people_parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE, "web")
PORT = int(os.environ.get("PORT", 8765))

COMPANY_FIELDS = ["name", "domain", "type", "subtype", "hq_city", "hq_country",
                  "market", "email_pattern", "pattern_confidence", "careers_url",
                  "tier", "status", "notes"]
CONTACT_FIELDS = ["first_name", "last_name", "job_title", "desk", "seniority",
                  "company_id", "company_name", "city", "country", "email",
                  "email_status", "email_source", "linkedin_url", "hook",
                  "source", "priority", "status", "tags", "notes"]
OUTREACH_FIELDS = ["contact_id", "channel", "template_id", "subject", "body",
                   "status", "sent_at", "followup_stage", "next_followup_at",
                   "replied_at", "reply_snippet", "outcome", "notes"]
TEMPLATE_FIELDS = ["name", "role_family", "subject_tpl", "body_tpl", "notes", "active"]
APPLICATION_FIELDS = ["company_id", "company_name", "role", "url", "status",
                      "applied_at", "notes"]
TITLES_BY_TYPE = {
    "bank": ["Quantitative Analyst", "Strats", "Structurer", "Trader"],
    "hedge_fund": ["Quantitative Researcher", "Portfolio Manager", "Quant Developer"],
    "prop_hft": ["Quantitative Trader", "Quantitative Developer", "Trading Analyst"],
    "asset_manager": ["Quantitative Analyst", "Systematic Portfolio Manager", "Risk Analyst"],
    "commodity": ["Quantitative Analyst", "Trader", "Origination Analyst"],
    "insurance_am": ["Quantitative Analyst", "Portfolio Manager", "Risk Analyst"],
    "broker": ["Broker", "Trader", "Quantitative Analyst"],
    "crypto": ["Quantitative Trader", "Trader", "Quantitative Researcher"],
    "other": ["Quantitative Analyst", "Quant Researcher"],
}
PROFILE_FIELDS = ["full_name", "email", "phone", "linkedin", "github", "website",
                  "headline", "city", "target_roles", "years_exp", "education",
                  "key_skills", "projects", "achievements", "languages",
                  "availability", "pitch", "cv_text"]
CONTACT_IMPORT_MAP = {
    "first name": "first_name", "firstname": "first_name", "prenom": "first_name",
    "last name": "last_name", "lastname": "last_name", "nom": "last_name",
    "job title": "job_title", "title": "job_title", "role": "job_title",
    "poste": "job_title", "desk": "desk", "team": "desk",
    "company": "company_name", "company name": "company_name", "entreprise": "company_name",
    "email": "email", "mail": "email", "e-mail": "email",
    "linkedin": "linkedin_url", "linkedin url": "linkedin_url",
    "city": "city", "ville": "city", "location": "city",
    "hook": "hook", "notes": "notes", "source": "source",
    "position": "job_title",           # LinkedIn Connections.csv
    "url": "linkedin_url",             # LinkedIn Connections.csv
    "email address": "email",
    "seniority": "seniority", "tags": "tags",
}

SENIORITY_RULES = [
    ("global head", "head"), ("head of", "head"), ("head,", "head"), (" head", "head"),
    ("chief", "C-suite"), ("president", "C-suite"), ("founder", "founder"),
    ("portfolio manager", "PM"), (" pm", "PM"),
    ("partner", "partner"), ("managing director", "MD"), (" md", "MD"),
    ("director", "director"), ("vice president", "VP"), (" vp", "VP"),
    ("principal", "principal"), ("associate", "associate"),
    ("senior analyst", "associate"), ("intern", "intern"), ("graduate", "analyst"),
    ("analyst", "analyst"), ("trader", "trader"), ("researcher", "researcher"),
]

DESK_RULES = [
    ("rates", "Rates"), ("fixed income", "Rates"),
    ("credit", "Credit"), ("convertible", "Credit"),
    ("fx", "FX"), ("foreign exchange", "FX"), ("currency", "FX"),
    ("commodit", "Commodities"), ("energy", "Commodities"), ("power", "Commodities"),
    ("gas", "Commodities"), ("oil", "Commodities"), ("freight", "Commodities"),
    ("volatility", "Vol"), (" vol", "Vol"), ("options", "Vol"),
    ("macro", "Macro"), ("emerging market", "EM"),
    ("equity derivative", "Equity Derivatives"), ("exotic", "Equity Derivatives"),
    ("derivative", "Derivatives"), ("structuring", "Structuring"),
    ("structur", "Structuring"), ("index", "Index"), ("qis", "QIS"),
    ("crypto", "Crypto"), ("digital asset", "Crypto"),
    ("systematic", "Systematic"),
    ("quantitative", "Quant"), ("quant ", "Quant"),
    ("equit", "Equity"), ("execution", "Execution"), ("risk", "Risk"),
]


def derive_from_title(title):
    """Guess seniority and desk from a job title. Never overwrites real values."""
    t = (" " + (title or "").lower() + " ").replace("-", " ")
    seniority = ""
    for kw, val in SENIORITY_RULES:
        if kw in t:
            seniority = val
            break
    desk = ""
    for kw, val in DESK_RULES:
        if kw in t:
            desk = val
            break
    return seniority, desk


# ----------------------------------------------------------------- helpers
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


PATTERNS = {
    "first.last": lambda f, l: f"{f}.{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "f.last": lambda f, l: f"{f[0]}.{l}" if f else "",
    "flast": lambda f, l: f"{f[0]}{l}" if f else "",
    "firstlast": lambda f, l: f"{f}{l}",
    "firstl": lambda f, l: f"{f}{l[0]}" if l else "",
    "last.first": lambda f, l: f"{l}.{f}",
    "lastfirst": lambda f, l: f"{l}{f}",
    "first": lambda f, l: f,
}


def _alpha(s):
    return "".join(ch for ch in (s or "").strip().lower() if ch.isalpha())


def guess_email(contact, company):
    """Build a best-guess address from the company pattern."""
    if not company or not company.get("domain"):
        return ""
    first = _alpha(contact.get("first_name"))
    last = _alpha(contact.get("last_name"))
    if not first or not last:
        return ""
    pattern = (company.get("email_pattern") or "").strip().lower()
    domain = _clean_domain(company["domain"])
    fn = PATTERNS.get(pattern)
    if not fn or not domain:
        return ""
    local = fn(first, last)
    return f"{local}@{domain}" if local else ""


def _clean_domain(domain):
    d = (domain or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip("/").split("/")[0]


def infer_pattern(company_id, sample_email, min_confidence=0.0):
    """Learn a firm's email convention from one real address.

    Strongest signal: the sample matches a contact we already have at that firm.
    Falls back to shape heuristics (separator, length) with low confidence.

    `min_confidence` lets a caller collect the evidence without applying it -
    the paste importer uses this so a bare address never silently becomes the
    firm's convention on a 0.5 guess.
    """
    sample = (sample_email or "").strip().lower()
    if "@" not in sample:
        return {"error": "not a valid email address"}
    local, domain = sample.rsplit("@", 1)
    local = local.strip()
    domain = _clean_domain(domain)

    rows = db.query("SELECT * FROM companies WHERE id=?", [company_id])
    if not rows:
        return {"error": "company not found"}
    company = rows[0]

    contacts = db.query("SELECT * FROM contacts WHERE company_id=?", [company_id])
    matches = []
    for c in contacts:
        f, l = _alpha(c["first_name"]), _alpha(c["last_name"])
        if not f or not l:
            continue
        for name, fn in PATTERNS.items():
            if fn(f, l) == local:
                matches.append((name, f"{c['first_name']} {c['last_name']}"))

    if matches:
        counts = {}
        for name, _ in matches:
            counts[name] = counts.get(name, 0) + 1
        pattern = max(counts.items(), key=lambda kv: kv[1])[0]
        confidence = 0.95 if counts[pattern] > 1 else 0.85
        evidence_source = "matched " + ", ".join(sorted({m[1] for m in matches})[:3])
        note = f"inferred from sample {sample}"
    else:
        pattern, confidence, note = _shape_heuristic(local)
        evidence_source = "shape heuristic (no contact match)"

    if not company.get("domain"):
        db.execute("UPDATE companies SET domain=?, updated_at=? WHERE id=?",
                   [domain, now_iso(), company_id])
    applied = confidence >= min_confidence
    if applied:
        db.execute("UPDATE companies SET email_pattern=?, pattern_confidence=?, updated_at=? WHERE id=?",
                   [pattern, confidence, now_iso(), company_id])
        db.execute(
            "INSERT INTO pattern_evidence (company_id, pattern, sample_email, source, confidence, notes) "
            "VALUES (?,?,?,?,?,?)",
            [company_id, pattern, sample, evidence_source, confidence, note])

    unlocked = db.query(
        "SELECT COUNT(*) n FROM contacts WHERE company_id=? "
        "AND (email IS NULL OR email='')", [company_id])[0]["n"]
    return {
        "pattern": pattern,
        "domain": domain,
        "confidence": confidence,
        "source": evidence_source,
        "note": note,
        "applied": applied,
        "unlocked_contacts": unlocked,
        "candidates": sorted(PATTERNS.keys()),
    }


def company_for_domain(domain):
    """Find the firm that owns an email domain."""
    d = _clean_domain(domain)
    if not d:
        return None
    rows = db.query("SELECT * FROM companies WHERE lower(domain)=?", [d])
    if rows:
        return rows[0]
    # mail.janestreet.com or janestreet.co.uk should still find Jane Street
    rows = db.query(
        "SELECT * FROM companies WHERE lower(domain) LIKE ? OR ? LIKE '%' || lower(domain)",
        ["%" + d, d])
    return rows[0] if rows else None


def learn_patterns_from_samples(samples):
    """Try to learn a firm's convention from real addresses with no name.

    Only the strong path is applied (an address whose local part matches a
    contact at that firm). A bare address tells you the domain and nothing
    about the convention, so the shape guess is reported, not saved.
    """
    learned, skipped, seen = [], [], set()
    for s in samples or []:
        email = (s.get("email") or "").strip().lower()
        if "@" not in email:
            continue
        domain = _clean_domain(email.rsplit("@", 1)[1])
        if not domain or domain in seen:
            continue
        seen.add(domain)
        co = company_for_domain(domain)
        if not co:
            skipped.append({"email": email, "reason": "no firm in the seed uses that domain"})
            continue
        res = infer_pattern(co["id"], email, min_confidence=0.85)
        if res.get("error"):
            skipped.append({"email": email, "reason": res["error"]})
        elif res.get("applied"):
            learned.append({"company": co["name"], "pattern": res["pattern"],
                            "confidence": res["confidence"], "source": res["source"],
                            "unlocked_contacts": res["unlocked_contacts"]})
        else:
            skipped.append({"email": email, "company": co["name"],
                            "reason": "address does not match a known name at that firm, "
                                      "so the convention is still a guess"})
    return {"learned": learned, "skipped": skipped}


def _shape_heuristic(local):
    """Last resort: guess the convention from the shape of one address only."""
    if "." in local:
        a, b = local.split(".", 1)
        if len(a) == 1:
            return "f.last", 0.5, "one-letter first part suggests f.last"
        return "first.last", 0.5, "dotted address, no contact to confirm against"
    if "_" in local:
        return "first_last", 0.5, "underscore separator"
    if local.isalpha():
        return ("firstlast", 0.35,
                "no separator - ambiguous between firstlast, flast and first; "
                "confirm with a second sample")
    return "first.last", 0.25, "unrecognised shape - verify manually"


def apply_guesses(company_id=None):
    """Write pattern-derived addresses into empty email fields, flagged as guessed."""
    q = ("SELECT c.*, co.domain, co.email_pattern FROM contacts c "
         "JOIN companies co ON co.id = c.company_id "
         "WHERE (c.email IS NULL OR c.email='') AND co.email_pattern IS NOT NULL "
         "AND co.email_pattern != '' AND co.domain IS NOT NULL AND co.domain != ''")
    args = []
    if company_id:
        q += " AND c.company_id=?"
        args.append(company_id)
    rows = db.query(q, args)
    n = 0
    for r in rows:
        email = guess_email(r, {"domain": r["domain"], "email_pattern": r["email_pattern"]})
        if email:
            db.execute("UPDATE contacts SET email=?, email_status='guessed', "
                       "email_source='pattern guess', updated_at=? WHERE id=?",
                       [email, now_iso(), r["id"]])
            n += 1
    return {"updated": n}


# ----------------------------------------------------------------- api
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


def api_contacts(params):
    where, args = [], []
    if params.get("status"):
        where.append("c.status=?")
        args.append(params["status"][0])
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
    sql += " ORDER BY c.priority ASC, c.updated_at DESC"
    rows = db.query(sql, args)
    for r in rows:
        if not r.get("email"):
            r["email_guess"] = guess_email(
                r, {"domain": r.get("company_domain"), "email_pattern": r.get("company_pattern")}
            )
        else:
            r["email_guess"] = r["email"]
    return rows


def api_outreach(params):
    where, args = [], []
    if params.get("status"):
        where.append("o.status=?")
        args.append(params["status"][0])
    if params.get("due"):
        where.append("o.next_followup_at IS NOT NULL AND o.next_followup_at <= ?")
        args.append(date.today().isoformat())
    sql = ("SELECT o.*, c.first_name, c.last_name, c.company_name, c.email AS contact_email, "
           "c.city AS contact_city, c.desk, c.hook "
           "FROM outreach o LEFT JOIN contacts c ON c.id = o.contact_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(o.next_followup_at, o.created_at) DESC"
    return db.query(sql, args)


def api_analytics():
    """Conversion funnel and where the replies actually come from."""
    def rate(num, den):
        return round(100 * num / den, 1) if den else 0.0

    contacts = db.query("SELECT COUNT(*) n FROM contacts")[0]["n"]
    contacted = db.query("SELECT COUNT(*) n FROM contacts WHERE status NOT IN "
                         "('identified','ready','blacklist')")[0]["n"]
    sent = db.query("SELECT COUNT(*) n FROM outreach WHERE status NOT IN "
                    "('draft','approved')")[0]["n"]
    replied = db.query("SELECT COUNT(*) n FROM outreach WHERE status IN "
                       "('replied','positive')")[0]["n"]
    positive = db.query("SELECT COUNT(*) n FROM outreach WHERE status='positive'")[0]["n"]

    funnel = [
        ("contacts found", contacts, 100.0),
        ("contacted", contacted, rate(contacted, contacts)),
        ("emails sent", sent, rate(sent, contacts)),
        ("replies", replied, rate(replied, sent)),
        ("positive", positive, rate(positive, sent)),
    ]

    by_type = db.query(
        "SELECT COALESCE(co.type,'unknown') k, COUNT(*) contacts, "
        "SUM(CASE WHEN c.status NOT IN ('identified','ready','blacklist') THEN 1 ELSE 0 END) contacted, "
        "SUM(CASE WHEN c.status IN ('replied','positive') THEN 1 ELSE 0 END) replied "
        "FROM contacts c LEFT JOIN companies co ON co.id=c.company_id GROUP BY k ORDER BY contacts DESC")
    by_tier = db.query(
        "SELECT COALESCE(co.tier,0) k, COUNT(*) contacts, "
        "SUM(CASE WHEN c.status NOT IN ('identified','ready','blacklist') THEN 1 ELSE 0 END) contacted, "
        "SUM(CASE WHEN c.status IN ('replied','positive') THEN 1 ELSE 0 END) replied "
        "FROM contacts c LEFT JOIN companies co ON co.id=c.company_id GROUP BY k ORDER BY k")
    by_template = db.query(
        "SELECT COALESCE(t.name,'no template') k, COUNT(*) sent, "
        "SUM(CASE WHEN o.status IN ('replied','positive') THEN 1 ELSE 0 END) replied "
        "FROM outreach o LEFT JOIN templates t ON t.id=o.template_id "
        "WHERE o.status NOT IN ('draft','approved') GROUP BY k ORDER BY sent DESC")
    return {
        "funnel": [{"stage": s, "n": n, "pct": p} for s, n, p in funnel],
        "by_type": [dict(r, reply_rate=rate(r["replied"], r["contacted"])) for r in by_type],
        "by_tier": [dict(r, reply_rate=rate(r["replied"], r["contacted"])) for r in by_tier],
        "by_template": [dict(r, reply_rate=rate(r["replied"], r["sent"])) for r in by_template],
    }


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


def sourcing_links(company_id):
    """Search links that find people at a firm. Nothing scraped, just queries."""
    rows = db.query("SELECT * FROM companies WHERE id=?", [company_id])
    if not rows:
        return {"error": "company not found"}
    c = rows[0]
    titles = TITLES_BY_TYPE.get(c["type"], TITLES_BY_TYPE["other"])[:3]
    name = c["name"]
    quoted = '"' + name + '"'
    title_q = "(" + " OR ".join('"' + t + '"' for t in titles) + ")"
    xray = "site:linkedin.com/in " + quoted + " " + title_q
    careers_search = "https://www.google.com/search?q=" + quote_plus(
        f"{name} quant careers graduate programme")
    # Prefer the firm's real careers page when discover_careers.py found one;
    # otherwise fall back to the search so the button never opens a blank tab.
    real = (c.get("careers_url") or "").strip()
    return {
        "xray_url": "https://www.google.com/search?q=" + quote_plus(xray),
        "careers_url": real or careers_search,
        "careers_search_url": careers_search,
        "careers_is_verified": bool(real),
        "query": xray,
        "titles": titles,
    }


def api_sourcing_queue(params):
    """A prioritised worklist of firms to go find people at.

    This is the bridge between 'I have a database' and 'I have people to email'.
    Ordered so the most productive targets come first: tier, then firms we already
    have intel on, then name. Each row carries everything needed to act on it
    without opening another tab - the X-ray query, the careers page, the hook.
    """
    where, args = [], []
    tiers = [t.strip() for t in (params.get("tier", ["1"])[0] or "1").split(",") if t.strip()]
    if tiers:
        where.append(f"tier IN ({','.join('?' * len(tiers))})")
        args += tiers
    if params.get("type"):
        where.append("type=?")
        args.append(params["type"][0])
    if params.get("without_contacts", ["1"])[0] != "0":
        where.append("id NOT IN (SELECT company_id FROM contacts WHERE company_id IS NOT NULL)")
    if params.get("has_brief", ["0"])[0] == "1":
        where.append("research IS NOT NULL AND research != ''")

    sql = ("SELECT id, name, type, subtype, tier, hq_city, domain, careers_url, "
           "research, email_pattern FROM companies")
    if where:
        sql += " WHERE " + " AND ".join(where)
    # intel first, then name - so a firm you already have a hook for beats one you don't
    sql += " ORDER BY tier ASC, (research IS NULL OR research='') ASC, name ASC"
    limit = int(params.get("limit", ["0"])[0] or 0)
    if limit:
        sql += f" LIMIT {limit}"
    rows = db.query(sql, args)

    out = []
    for c in rows:
        titles = TITLES_BY_TYPE.get(c["type"], TITLES_BY_TYPE["other"])[:3]
        link = sourcing_links(c["id"])
        readiness = sum(1 for x in (bool((c.get("research") or "").strip()),
                                    bool((c.get("careers_url") or "").strip()),
                                    bool((c.get("email_pattern") or "").strip()),
                                    bool((c.get("domain") or "").strip())) if x)
        out.append({
            "id": c["id"], "name": c["name"], "type": c["type"],
            "subtype": c["subtype"], "tier": c["tier"], "hq_city": c["hq_city"],
            "domain": c["domain"],
            "careers_url": link["careers_url"],
            "careers_is_verified": link["careers_is_verified"],
            "careers_search_url": link["careers_search_url"],
            "hook": email_gen._brief_hook(c.get("research")),
            "brief": c.get("research") or "",
            "titles": titles,
            "xray_query": link["query"],
            "xray_url": link["xray_url"],
            "readiness": readiness,          # 0-4, how much we already know
        })
    return out


def api_sourcing_markdown(params):
    """The whole queue as a checklist you can print or work through offline."""
    rows = api_sourcing_queue(params)
    n = len(rows)
    lines = ["# Sourcing worklist", "",
             f"{n} firm{'s' if n != 1 else ''}. Work top to bottom - ordered by tier, then by",
             "whether a hook is already written. Tick the boxes as you go.", ""]
    for r in rows:
        lines += [f"## {r['name']}", ""]
        meta = f"- **Tier {r['tier']}** · {r['type']}"
        if r["hq_city"]:
            meta += f" · {r['hq_city']}"
        if r["subtype"]:
            meta += f" · {r['subtype']}"
        lines.append(meta)
        if r["careers_url"]:
            tag = "" if r["careers_is_verified"] else " (search, page not verified)"
            lines.append(f"- Careers: {r['careers_url']}{tag}")
        if r["hook"]:
            lines.append(f"- Hook: {r['hook']}")
        lines += [f"- Titles: {', '.join(r['titles'])}",
                  f"- Search: {r['xray_query']}",
                  "- [ ] find 2-3 people   - [ ] first email sent   - [ ] follow-up scheduled",
                  ""]
    return "\n".join(lines)


DEMO_CONTACTS = [
    ("Sophie", "Laurent", "Head of Equity Derivatives Structuring", "Equity Derivatives",
     "head", "Societe Generale", "Paris",
     "She spoke at Global Derivatives on hybrid issuance - the flow numbers she quoted were the most concrete thing I heard all day.",
     "conference", 1),
    ("James", "Whitfield", "Quantitative Researcher", "Systematic", "researcher",
     "Qube Research and Technologies", "London",
     "QRT's move into mid-frequency equity signals matches the research I did on intraday decay.",
     "linkedin", 1),
    ("Aisha", "Rahman", "Quantitative Trader", "Quant", "trader", "Jane Street", "London",
     "She wrote the public piece on market-making and adverse selection that I keep coming back to.",
     "linkedin", 1),
    ("Marc", "Lefevre", "Portfolio Manager", "Systematic", "PM", "CFM", "Paris",
     "CFM is the only Paris fund doing this kind of research at scale, and he runs part of it.",
     "alumni", 2),
    ("Elena", "Rossi", "Rates Trader", "Rates", "trader", "BNP Paribas", "London",
     "Her desk covers the exact curve segment I built my last model on.",
     "linkedin", 2),
    ("David", "Chen", "Quantitative Analyst", "Quant", "analyst", "Squarepoint Capital",
     "London", "Squarepoint's London build-out means the team is still small enough to answer.",
     "linkedin", 2),
    ("Thomas", "Meyer", "Structurer", "Structuring", "associate", "Deutsche Bank", "London",
     "He moved from rates to equity structuring last year, which is the path I'm considering.",
     "conference", 3),
    ("Camille", "Moreau", "Quantitative Researcher", "Systematic", "researcher",
     "Syquant Capital", "Paris",
     "A Paris systematic fund few people know about - small team, direct access to the PMs.",
     "website", 2),
]

DEMO_PROFILE = {
    "full_name": "Sample Candidate",
    "headline": "M2 quantitative finance, previously rates quant intern",
    "city": "Paris",
    "target_roles": "quantitative researcher",
    "years_exp": "two years",
    "education": "M2 Quantitative Finance",
    "key_skills": "stochastic calculus, Python, C++, options pricing",
    "projects": "a local volatility pricer calibrated on index surfaces",
    "achievements": "",
    "languages": "French, English",
    "availability": "available immediately",
    "pitch": "I build pricing and backtesting tooling end to end, in Python and C++.",
    "phone": "+33 6 00 00 00 00",
    "linkedin": "linkedin.com/in/sample",
}


def load_demo():
    """Sample data so the workflow is visible. Every row is tagged source='demo'."""
    if not db.query("SELECT id FROM contacts WHERE source='demo' LIMIT 1"):
        for (first, last, title, desk, seniority, company, city, hook, src, prio) in DEMO_CONTACTS:
            co = db.resolve_company(company)
            db.execute(
                "INSERT OR IGNORE INTO contacts (first_name,last_name,job_title,desk,seniority,"
                "company_id,company_name,city,email,email_status,hook,source,priority,status) "
                "VALUES (?,?,?,?,?,?,?,?,'','unknown',?,'demo',?,'identified')",
                [first, last, title, desk, seniority, co["id"] if co else None,
                 company, city, hook, prio])
    prof = get_profile()
    if not (prof.get("full_name") or "").strip():
        db.execute("UPDATE profile SET " + ", ".join(f"{k}=?" for k in DEMO_PROFILE),
                   [DEMO_PROFILE[k] for k in DEMO_PROFILE])
    return {"contacts": db.query("SELECT COUNT(*) n FROM contacts WHERE source='demo'")[0]["n"]}


def clear_demo():
    db.execute("DELETE FROM outreach WHERE contact_id IN "
               "(SELECT id FROM contacts WHERE source='demo')")
    db.execute("DELETE FROM contacts WHERE source='demo'")
    prof = get_profile()
    if (prof.get("full_name") or "").strip() == "Sample Candidate":
        db.execute("UPDATE profile SET " +
                   ", ".join(f"{k}=NULL" for k in DEMO_PROFILE if k != "id"))
    return {"ok": True}


def api_score(payload):
    rows = db.query("SELECT * FROM contacts WHERE id=?", [payload.get("contact_id")]) \
        if payload.get("contact_id") else []
    contact = enrich_contact(dict(rows[0])) if rows else {}
    ctx = email_gen.build_context(contact, contact.get("company"), get_profile())
    subject = payload.get("subject") or ""
    body = payload.get("body") or ""
    result = email_gen.score_email(subject, body, ctx)
    # A hand-edited body no longer has a template to diff against, so check the
    # critical variables directly - these are the ones whose absence mangles a
    # sentence rather than merely leaving a gap.
    critical = ["my_full_name", "my_key_skills", "my_projects", "my_pitch", "first_name"]
    missing = [k for k in critical if not str(ctx.get(k, "") or "").strip()]
    if missing:
        result["issues"].insert(0, "empty fields behind this text: " + ", ".join(missing))
        result["notes"].append("fill the profile before sending - empty values are invisible.")
    return result


def api_parse_cv(payload):
    """Read a pasted CV and propose profile fields. Proposes - never saves.

    The user reviews the extraction in the UI and decides what to keep, which is
    the only honest design for something this heuristic.
    """
    text = (payload.get("cv_text") or "").strip()
    if len(text) < 40:
        return {"error": "paste more of your CV - at least a few lines"}
    result = cv_parse.parse_cv(text)
    # Tell the UI which proposals are usable and which would damage a template.
    critical = {"full_name", "key_skills", "projects", "pitch", "education"}
    result["critical_missing"] = sorted(critical & set(result["missing"]))
    result["confidence"] = round(
        100.0 * (len(result["fields"]) - len(result["missing"])) / max(1, len(result["fields"])))
    return result


def api_stats():
    today = date.today().isoformat()
    stats = {}
    stats["companies"] = db.query("SELECT COUNT(*) n FROM companies")[0]["n"]
    stats["contacts"] = db.query("SELECT COUNT(*) n FROM contacts")[0]["n"]
    stats["companies_by_type"] = db.query(
        "SELECT type, COUNT(*) n FROM companies GROUP BY type ORDER BY n DESC")
    stats["contacts_by_status"] = db.query(
        "SELECT status, COUNT(*) n FROM contacts GROUP BY status ORDER BY n DESC")
    stats["outreach_by_status"] = db.query(
        "SELECT status, COUNT(*) n FROM outreach GROUP BY status ORDER BY n DESC")
    stats["with_email"] = db.query(
        "SELECT COUNT(*) n FROM contacts WHERE email IS NOT NULL AND email != ''")[0]["n"]
    stats["followups_due"] = db.query(
        "SELECT COUNT(*) n FROM outreach WHERE next_followup_at IS NOT NULL "
        "AND next_followup_at <= ? AND status NOT IN ('replied','positive','closed')", [today])[0]["n"]
    stats["replies"] = db.query(
        "SELECT COUNT(*) n FROM outreach WHERE status IN ('replied','positive')")[0]["n"]
    stats["sent"] = db.query("SELECT COUNT(*) n FROM outreach WHERE status NOT IN ('draft','approved')")[0]["n"]
    stats["firms_with_pattern"] = db.query(
        "SELECT COUNT(*) n FROM companies WHERE email_pattern IS NOT NULL AND email_pattern != ''")[0]["n"]
    stats["firms_with_contacts"] = db.query(
        "SELECT COUNT(*) n FROM companies WHERE id IN (SELECT company_id FROM contacts "
        "WHERE company_id IS NOT NULL)")[0]["n"]
    prof = get_profile()
    filled = sum(1 for f in ("full_name", "headline", "key_skills", "target_roles",
                             "years_exp", "projects", "pitch") if (prof.get(f) or "").strip())
    stats["profile_completeness"] = round(100 * filled / 7)
    return stats


def api_generate(payload):
    cid = payload.get("contact_id")
    tid = payload.get("template_id")
    if not cid:
        return {"error": "contact_id required"}
    rows = db.query("SELECT * FROM contacts WHERE id=?", [cid])
    if not rows:
        return {"error": "contact not found"}
    contact = enrich_contact(dict(rows[0]))
    templates = db.query("SELECT * FROM templates WHERE id=?", [tid]) if tid else []
    if not templates:
        templates = db.query("SELECT * FROM templates WHERE active=1 ORDER BY id LIMIT 1")
    if not templates:
        return {"error": "no template available"}
    tpl = templates[0]
    ctx = email_gen.build_context(contact, contact.get("company"), get_profile())
    subject = email_gen.render(tpl["subject_tpl"], ctx)
    body = email_gen.render(tpl["body_tpl"], ctx)

    # Variables that resolved to nothing. Invisible in the output but fatal to
    # the mail ("I'm , - on ,"), so they are surfaced as the first flags.
    empty = []
    for tpl_text in (tpl["subject_tpl"], tpl["body_tpl"]):
        for k in email_gen.empty_placeholders(tpl_text, ctx):
            if k not in empty:
                empty.append(k)
    flags = email_gen.quality_flags(ctx, body)
    if empty:
        flags.insert(0, "empty fields - these placeholders rendered as nothing: "
                        + ", ".join(empty))
    return {
        "subject": subject,
        "body": body,
        "quality": email_gen.score_email(subject, body, ctx),
        "template_id": tpl["id"],
        "to": contact.get("email") or contact.get("email_guess") or "",
        "contact_name": f"{contact.get('first_name','')} {contact.get('last_name','')}".strip(),
        "company": contact.get("company_name") or "",
        "company_id": (contact.get("company") or {}).get("id"),
        "brief": ctx.get("company_research") or "",
        "brief_hook": ctx.get("company_hook") or "",
        "empty_fields": empty,
        "flags": flags,
    }


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


def api_mark_sent(payload):
    oid = payload.get("id")
    days = int(payload.get("followup_days") or 7)
    follows = payload.get("follows")           # the mail this one is a follow-up to
    db.execute(
        "UPDATE outreach SET status='sent', sent_at=COALESCE(sent_at, ?), "
        "next_followup_at=?, updated_at=? WHERE id=?",
        [now_iso(), (date.today() + timedelta(days=days)).isoformat(), now_iso(), oid])
    db.execute("UPDATE contacts SET status='contacted', updated_at=? WHERE id=(SELECT contact_id FROM outreach WHERE id=?)",
               [now_iso(), oid])
    if follows:
        # The follow-up has gone out, so the original must stop being "due" -
        # otherwise the dashboard keeps nagging about a thread already bumped.
        db.execute("UPDATE outreach SET next_followup_at=NULL, "
                   "followup_stage=COALESCE(followup_stage,0)+1, updated_at=? WHERE id=?",
                   [now_iso(), follows])
    return {"ok": True}


def api_draft_followup(payload):
    """Build the follow-up to a mail you already sent.

    Keeps the original subject so it threads instead of starting a new
    conversation - replying in-thread is most of why a bump gets read at all.
    """
    oid = payload.get("outreach_id")
    rows = db.query("SELECT * FROM outreach WHERE id=?", [oid])
    if not rows:
        return {"error": "that outreach row no longer exists"}
    o = rows[0]
    if not o.get("contact_id"):
        return {"error": "this mail has no contact attached, so there is nothing to follow up"}
    crows = db.query("SELECT * FROM contacts WHERE id=?", [o["contact_id"]])
    if not crows:
        return {"error": "the contact for this mail was deleted"}
    contact = enrich_contact(dict(crows[0]))

    tpl = (db.query("SELECT * FROM templates WHERE active=1 AND (role_family='followup' "
                    "OR lower(name) LIKE '%follow%') ORDER BY id LIMIT 1")
           or db.query("SELECT * FROM templates WHERE active=1 ORDER BY id LIMIT 1"))
    if not tpl:
        return {"error": "no template available"}
    tpl = tpl[0]

    ctx = email_gen.build_context(contact, contact.get("company"), get_profile())
    ctx["original_subject"] = o.get("subject") or ""
    tpl_subject = email_gen.render(tpl["subject_tpl"], ctx)
    body = email_gen.render(tpl["body_tpl"], ctx)

    subject = (o.get("subject") or "").strip()
    if subject:
        subject = subject if subject.lower().startswith("re:") else "Re: " + subject
    else:
        subject = tpl_subject

    return {
        "contact_id": contact["id"],
        "contact_name": f"{contact.get('first_name','')} {contact.get('last_name','')}".strip(),
        "company": contact.get("company_name") or "",
        "template_id": tpl["id"],
        "outreach_id": oid,
        "follows": oid,
        "original_subject": o.get("subject") or "",
        "to": contact.get("email") or contact.get("email_guess") or "",
        "subject": subject,
        "body": body,
        "brief": ctx.get("company_research") or "",
        "brief_hook": ctx.get("company_hook") or "",
        "empty_fields": [],
        "quality": email_gen.score_email(subject, body, ctx),
        "flags": email_gen.quality_flags(ctx, body),
    }


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


# ----------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send(code, json_bytes(obj))

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
        parts = [p for p in path.split("/") if p]

        if path == "/" or path == "/index.html":
            return self.serve_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        if parts and parts[0] == "static":
            fname = os.path.basename("/".join(parts[1:]))
            fpath = os.path.join(WEB_DIR, fname)
            if os.path.exists(fpath):
                ext = os.path.splitext(fpath)[1]
                ctype = {".js": "application/javascript", ".css": "text/css",
                         ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
                return self.serve_file(fpath, ctype)
            return self.send(404, b"not found", "text/plain")

        if not parts or parts[0] != "api":
            return self.send(404, b"not found", "text/plain")
        parts = parts[1:]
        if not parts:
            return self.send_json({"ok": True})

        resource = parts[0]
        rest = parts[1:]
        payload = self.read_json() if method in ("POST", "PUT", "PATCH") else None

        try:
            # ---------------- GET
            if method == "GET":
                if resource == "stats":
                    return self.send_json(api_stats())
                if resource == "analytics":
                    return self.send_json(api_analytics())
                if resource == "applications":
                    return self.send_json(db.query(
                        "SELECT * FROM applications ORDER BY "
                        "CASE status WHEN 'interview' THEN 0 WHEN 'online_test' THEN 1 "
                        "WHEN 'applied' THEN 2 WHEN 'to_apply' THEN 3 ELSE 4 END, updated_at DESC"))
                if resource == "sourcing-queue":
                    return self.send_json(api_sourcing_queue(params))
                if resource == "sourcing" and rest:
                    return self.send_json(sourcing_links(rest[0]))
                if resource == "companies":
                    return self.send_json(api_companies(params))
                if resource == "contacts":
                    return self.send_json(api_contacts(params))
                if resource == "outreach":
                    return self.send_json(api_outreach(params))
                if resource == "templates":
                    return self.send_json(db.query("SELECT * FROM templates ORDER BY id"))
                if resource == "profile":
                    return self.send_json(get_profile())
                if resource == "export" and rest and rest[0] == "sourcing":
                    body = api_sourcing_markdown(params).encode("utf-8")
                    return self.send(200, body, "text/markdown; charset=utf-8",
                                     {"Content-Disposition":
                                      "attachment; filename=sourcing-worklist.md"})
                if resource == "export" and rest and rest[0] == "contacts":
                    rows = api_contacts({})
                    buf = io.StringIO()
                    cols = ["first_name", "last_name", "job_title", "desk", "company_name",
                            "email", "email_status", "linkedin_url", "city", "hook",
                            "priority", "status", "source", "notes"]
                    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
                    w.writeheader()
                    for r in rows:
                        w.writerow(r)
                    self.send(200, buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8")
                    return
            # ---------------- POST
            if method == "POST":
                if resource == "companies":
                    return self.send_json(insert_row("companies", payload, COMPANY_FIELDS))
                if resource == "contacts":
                    return self.send_json(insert_row("contacts", payload, CONTACT_FIELDS))
                if resource == "outreach":
                    return self.send_json(insert_row("outreach", payload, OUTREACH_FIELDS))
                if resource == "templates":
                    return self.send_json(insert_row("templates", payload, TEMPLATE_FIELDS))
                if resource == "applications":
                    if payload and not payload.get("company_id") and payload.get("company_name"):
                        hit = db.query("SELECT id FROM companies WHERE lower(name)=lower(?)",
                                       [payload["company_name"]])
                        if hit:
                            payload["company_id"] = hit[0]["id"]
                    return self.send_json(insert_row("applications", payload, APPLICATION_FIELDS))
                if resource == "generate":
                    return self.send_json(api_generate(payload or {}))
                if resource == "import" and rest and rest[0] == "contacts":
                    return self.send_json(api_import_contacts(payload or {}))
                if resource == "mark-sent":
                    return self.send_json(api_mark_sent(payload or {}))
                if resource == "seed" and rest and rest[0] == "templates":
                    return self.send_json(api_seed_templates())
                if resource == "seed" and rest and rest[0] == "companies":
                    return self.send_json({"result": db.load_seed(verbose=False)})
                if resource == "infer-pattern":
                    return self.send_json(infer_pattern(payload.get("company_id"),
                                                        payload.get("sample_email")))
                if resource == "apply-guesses":
                    return self.send_json(apply_guesses(payload.get("company_id")))
                if resource == "retag":
                    return self.send_json(retag_contacts(bool((payload or {}).get("overwrite"))))
                if resource == "duplicates":
                    return self.send_json(find_duplicates())
                if resource == "score":
                    return self.send_json(api_score(payload or {}))
                if resource == "parse-cv":
                    return self.send_json(api_parse_cv(payload or {}))
                if resource == "parse-people":
                    return self.send_json(api_parse_people(payload or {}))
                if resource == "import-people":
                    return self.send_json(api_import_people(payload or {}))
                if resource == "draft-followup":
                    return self.send_json(api_draft_followup(payload or {}))
                if resource == "demo" and rest and rest[0] == "load":
                    return self.send_json(load_demo())
                if resource == "demo" and rest and rest[0] == "clear":
                    return self.send_json(clear_demo())
                if resource == "aliases" and rest and rest[0] == "reload":
                    return self.send_json({"loaded": db.load_aliases()})
            # ---------------- PUT
            if method == "PUT":
                if resource == "profile":
                    sets, args = [], []
                    for f in PROFILE_FIELDS:
                        if f in (payload or {}):
                            sets.append(f"{f}=?")
                            args.append(payload[f])
                    if sets:
                        sets.append("updated_at=?")
                        args.append(now_iso())
                        db.execute(f"UPDATE profile SET {', '.join(sets)} WHERE id=1", args)
                    return self.send_json(get_profile())
                if rest:
                    rid = rest[0]
                    table_fields = {
                        "companies": COMPANY_FIELDS, "contacts": CONTACT_FIELDS,
                        "outreach": OUTREACH_FIELDS, "templates": TEMPLATE_FIELDS,
                        "applications": APPLICATION_FIELDS,
                    }
                    if resource in table_fields:
                        return self.send_json(update_row(resource, rid, payload or {}, table_fields[resource]))
            # ---------------- DELETE
            if method == "DELETE" and rest:
                db.execute(f"DELETE FROM {resource} WHERE id=?", [rest[0]])
                return self.send_json({"ok": True})
        except Exception as exc:  # keep the server alive, surface the error
            return self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        return self.send_json({"error": "unknown route"}, 404)

    def serve_file(self, path, ctype):
        try:
            with open(path, "rb") as fh:
                self.send(200, fh.read(), ctype)
        except FileNotFoundError:
            self.send(404, b"not found", "text/plain")

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")


def main():
    db.init_db()
    if not db.query("SELECT id FROM templates LIMIT 1"):
        api_seed_templates()
    if not db.query("SELECT id FROM companies LIMIT 1"):
        db.load_seed(verbose=True)
    db.load_aliases()
    db.load_briefs(verbose=True)
    db.load_careers_urls(verbose=True)
    print(f"\n  Cold approach tracker running at http://127.0.0.1:{PORT}")
    print("  Ctrl+C to stop\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
