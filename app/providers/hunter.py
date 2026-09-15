"""Hunter.io provider. Reuses app/net.py, stores raw payloads, records credits.

This is Hunter_API.ipynb's logic made safe: same endpoints, but with a cache, a
ledger, a credit budget, and the field names the API actually returns today.

What Hunter can and cannot tell us, because it shapes what the DB can answer:
  gives us  -> email, name, job title, department, seniority, location, LinkedIn,
               Twitter, phone, email confidence, and the firm's email pattern
  never     -> education, degree, career history, papers. Those need a second
               source (OpenAlex/arXiv); nothing here pretends otherwise.

Endpoints used, and why each one:
  /v2/account          free   - plan + remaining credits, so we never guess
  /v2/domain-finder    free   - company name -> domain
  /v2/domain-search    1/10   - the people, plus the firm's email pattern
  /v2/people/find      0.2ish - location/geo/socials for one address
  /v2/companies/find   ~1     - firm profile (industry, founded, headcount, ...)

Field names are the live v2 ones (`name.givenName`, `employment.title`), not the
flat `first_name`/`position` the notebook reads - the Enrichment response was
restructured and the notebook's flat reads now silently return None.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import net         # noqa: E402

API = "https://api.hunter.io/v2"
PROVIDER = "hunter"
CONFIG_PATH = os.path.join(db.BASE, "config.json")

# Our short endpoint name -> the real URL path. These differ ("people-find" is
# served at /people/find), and using the key name directly sent enrichment to
# /people-find, which 404s silently - every enrichment looked like "no data".
ENDPOINT_PATHS = {
    "account": "account",
    "domain-finder": "domain-finder",
    "domain-search": "domain-search",
    "people-find": "people/find",
    "companies-find": "companies/find",
}

# Credit costs we can state from Hunter's docs. Domain Search charges per email
# found, so it is computed from the response, not assumed here.
CREDIT_COST = {
    "account": 0.0,
    "domain-finder": 0.0,
    "domain-search": None,       # per email returned
    "people-find": 0.2,          # Email Enrichment, charged only when it returns data
    "companies-find": 1.0,       # Company Enrichment (not in the free list)
}

# Hunter's `pattern` placeholder syntax -> this app's pattern names (server.PATTERNS).
# "{first}.{last}" is the friendly form of "first.last", which is what the email
# guesser, infer_pattern and the whole existing pattern engine already speak.
PATTERN_MAP = {
    "{first}.{last}": "first.last",
    "{first}_{last}": "first_last",
    "{first}-{last}": "first.last",
    "{f}.{last}": "f.last",
    "{f}{last}": "flast",
    "{first}{last}": "firstlast",
    "{first}{l}": "firstl",
    "{last}.{first}": "last.first",
    "{last}{first}": "lastfirst",
    "{last}": "last",
    "{first}": "first",
    "{f}{l}": "flast",
}


class HunterError(Exception):
    pass


# --------------------------------------------------------------------- api key
def load_key(explicit=None):
    """Find the API key: argument -> env -> config.json. Never stored in the DB.

    The key is a credential, so it stays out of the SQLite file (which gets
    backed up and copied) and out of the repo (config.json is gitignored).
    """
    if explicit:
        return explicit.strip()
    env = (os.environ.get("HUNTER_API_KEY") or "").strip()
    if env:
        return env
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            return (cfg.get("hunter_api_key") or "").strip()
        except (ValueError, OSError):
            return ""
    return ""


def save_key(api_key):
    """Write the key to the gitignored config.json, leaving any other keys intact."""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg = json.load(fh) or {}
        except (ValueError, OSError):
            cfg = {}
    cfg["hunter_api_key"] = (api_key or "").strip()
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return CONFIG_PATH


def _key_arg(api_key=None):
    key = load_key(api_key)
    if not key:
        raise HunterError(
            "no Hunter API key. Set HUNTER_API_KEY, or run:\n"
            "  python3 app/harvest.py --set-key YOUR_KEY")
    return key


# ------------------------------------------------------------------- plumbing
def _call(endpoint, params, api_key=None, method="GET", body=None,
          credits=0.0, use_cache=True, force=False):
    """One cached, logged Hunter call. Returns (json_or_None, fetch_row).

    The cache check happens *before* the network call, which is the entire point:
    Hunter does not charge twice for the same month's Domain Search, but we
    should not even spend the round trip, and other endpoints would charge.
    """
    key = _key_arg(api_key)
    clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    request_key = "&".join(f"{k}={clean[k]}" for k in sorted(clean))

    if use_cache and not force:
        seen = db.fetch_seen(PROVIDER, endpoint, request_key)
        if seen and seen["ok"]:
            payload = None
            if seen.get("raw_path"):
                full = os.path.join(db.BASE, seen["raw_path"])
                if os.path.exists(full):
                    try:
                        with open(full, "r", encoding="utf-8") as fh:
                            payload = json.load(fh)
                    except (ValueError, OSError):
                        payload = None
            return payload, dict(seen, cached=True)

    url = f"{API}/{ENDPOINT_PATHS.get(endpoint, endpoint)}"
    if method == "POST":
        res = net.post_json(url, payload=body, params={**clean, "api_key": key},
                            throttle=True)
    else:
        res = net.get_json(url, params={**clean, "api_key": key}, throttle=True)

    raw_path = sha = None
    if res["body"]:
        raw_path, sha = db.save_raw(PROVIDER, endpoint, request_key, res["body"])

    if not res["ok"]:
        # 404 on enrichment is "no data for this person", not a failure to retry.
        err = None
        if res["json"] and isinstance(res["json"], dict):
            errs = res["json"].get("errors")
            if errs:
                err = errs[0].get("details") or errs[0].get("id")
        db.record_fetch(PROVIDER, endpoint, request_key, res["status"], credits=0,
                        ok=False, error=err or res["error"], raw_path=raw_path,
                        response_hash=sha)
        return None, {"ok": False, "status": res["status"],
                      "error": err or res["error"], "request_key": request_key}

    cost = credits
    if endpoint == "domain-search" and res["json"]:
        emails = ((res["json"].get("data") or {}).get("emails") or [])
        n = len(emails)
        cost = 0.0 if n == 0 else 1.0 if n <= 10 else float((n + 9) // 10)
    row = db.record_fetch(PROVIDER, endpoint, request_key, res["status"],
                          credits=cost, ok=True, raw_path=raw_path, response_hash=sha)
    return res["json"], dict(row, cached=False)


def account(api_key=None, use_cache=True, force=False):
    """Plan and remaining credits. Free, so it is always safe to call."""
    payload, row = _call("account", {}, api_key=api_key, credits=0.0,
                         use_cache=use_cache, force=force)
    if not payload:
        return {"ok": False, "error": row.get("error") or "account call failed",
                "status": row.get("status")}
    data = payload.get("data") or {}
    req = (data.get("requests") or {})
    which = req.get("credits") or req.get("searches") or {}
    return {
        "ok": True,
        "plan": data.get("plan_name"),
        "plan_level": data.get("plan_level"),
        "reset_date": data.get("reset_date"),
        "credits_remaining": which.get("remaining"),
        "credits_used": which.get("used"),
        "credits_available": which.get("available"),
        "cached": row.get("cached", False),
    }


def find_domain(company, limit=5, perfect_match=True, api_key=None, force=False):
    """Company name -> domain. Free. Prefers the candidate with the most emails."""
    if not company or len(company.strip()) < 3:
        return {"ok": False, "error": "company name too short"}
    payload, _ = _call("domain-finder",
                       {"company": company.strip(), "limit": limit,
                        "perfect_match": str(bool(perfect_match)).lower()},
                       api_key=api_key, credits=0.0, force=force)
    if not payload:
        return {"ok": False, "error": "domain finder returned nothing"}
    candidates = payload.get("data") or []
    if not candidates:
        return {"ok": False, "error": f"no domain found for {company!r}", "candidates": []}
    best = max(candidates, key=lambda c: c.get("email_count") or 0)
    return {"ok": True, "domain": best.get("domain"),
            "company_name": best.get("company_name"),
            "email_count": best.get("email_count"),
            "candidates": [{"domain": c.get("domain"), "company_name": c.get("company_name"),
                            "email_count": c.get("email_count")} for c in candidates]}


def domain_search(domain=None, company=None, limit=10, offset=0, type_="personal",
                  seniority=None, department=None, decision_maker=None,
                  required_field=None, verification_status=None, job_titles=None,
                  location=None, aggregations=True, api_key=None, force=False):
    """The people at one firm, plus its email pattern.

    `limit` defaults to 10 because the free plan rejects limit+offset > 10. On a
    paid plan raise it; pagination is otherwise by offset.

    `type_="personal"` filters out role addresses (info@, careers@), which is
    what we want: a person to write to, not an inbox that will never answer.
    """
    if not domain and not company:
        return {"ok": False, "error": "need a domain or a company name"}
    params = {"limit": limit, "offset": offset}
    if domain:
        params["domain"] = domain
    else:
        params["company"] = company
    if type_:
        params["type"] = type_
    for name, val in (("seniority", seniority), ("department", department),
                      ("required_field", required_field),
                      ("verification_status", verification_status),
                      ("job_titles", job_titles)):
        if val:
            params[name] = val if isinstance(val, str) else ",".join(val)
    if decision_maker is not None:
        params["decision_maker"] = str(bool(decision_maker)).lower()
    if aggregations:
        params["aggregations"] = "true"

    body = None
    method = "GET"
    if location:
        # Hunter requires POST as soon as the location filter is used.
        method, body = "POST", {"location": location}
        params.pop("aggregations", None)

    payload, row = _call("domain-search", params, api_key=api_key, method=method,
                         body=body, force=force)
    if not payload:
        return {"ok": False, "error": row.get("error") or "domain search failed",
                "status": row.get("status"), "cached": row.get("cached", False)}
    data = payload.get("data") or {}
    meta = payload.get("meta") or {}
    emails = data.get("emails") or []
    return {
        "ok": True,
        "domain": data.get("domain"),
        "organization": data.get("organization"),
        "pattern": data.get("pattern"),
        "pattern_name": map_pattern(data.get("pattern")),
        "accept_all": data.get("accept_all"),
        "disposable": data.get("disposable"),
        "webmail": data.get("webmail"),
        "results": meta.get("results"),
        "aggregations": meta.get("aggregations"),
        "people": [_person(e) for e in emails],
        "credits": row.get("credits", 0.0),
        "cached": row.get("cached", False),
    }


def enrich_email(email, api_key=None, force=False):
    """Location, geo, phone and social handles for one address. 404 = no data."""
    payload, row = _call("people-find", {"email": (email or "").strip().lower()},
                         api_key=api_key, credits=CREDIT_COST["people-find"], force=force)
    if not payload:
        return {"ok": False, "status": row.get("status"),
                "error": row.get("error"), "found": False,
                "cached": row.get("cached", False)}
    d = payload.get("data") or {}
    name = d.get("name") or {}
    emp = d.get("employment") or {}
    geo = d.get("geo") or {}
    return {
        "ok": True, "found": True, "cached": row.get("cached", False),
        "first_name": name.get("givenName"), "last_name": name.get("familyName"),
        "full_name": name.get("fullName"),
        "headline": emp.get("title"), "role": emp.get("role"),
        "seniority": emp.get("seniority"), "department": emp.get("subRole"),
        "employment_domain": emp.get("domain"), "employment_name": emp.get("name"),
        "location_raw": d.get("location"), "timezone": d.get("timeZone"),
        "city": geo.get("city"), "state": geo.get("state"),
        "country": geo.get("country"), "country_code": geo.get("countryCode"),
        "latitude": geo.get("lat"), "longitude": geo.get("lng"),
        "twitter": (d.get("twitter") or {}).get("handle"),
        "github": (d.get("github") or {}).get("handle"),
        "linkedin": (d.get("linkedin") or {}).get("handle"),
        "avatar": d.get("avatar"), "bio": d.get("bio"), "phone": d.get("phone"),
        "fuzzy": 1 if d.get("fuzzy") else 0,
        "last_seen_at": d.get("activeAt"), "raw": d,
    }


def company_enrichment(domain, api_key=None, force=False):
    """Firm profile: industry, description, founded year, headcount, HQ, socials."""
    if not domain:
        return {"ok": False, "error": "domain required"}
    payload, row = _call("companies-find", {"domain": domain}, api_key=api_key,
                         credits=CREDIT_COST["companies-find"], force=force)
    if not payload:
        return {"ok": False, "status": row.get("status"), "error": row.get("error"),
                "cached": row.get("cached", False)}
    d = payload.get("data") or {}
    cat = d.get("category") or {}
    geo = d.get("geo") or {}
    metrics = d.get("metrics") or {}
    return {
        "ok": True, "found": bool(d), "cached": row.get("cached", False),
        "name": d.get("name"), "legal_name": d.get("legalName"),
        "domain": d.get("domain"), "description": d.get("description"),
        "founded_year": d.get("foundedYear"), "headcount": metrics.get("employees"),
        "employee_count": metrics.get("employeesCount"),
        "industry": cat.get("industry"), "sector": cat.get("sector"),
        "industry_group": cat.get("industryGroup"), "company_type": d.get("companyType") or d.get("type"),
        "keywords": d.get("tags"), "ticker": d.get("ticker"),
        "address": d.get("location"), "city": geo.get("city"), "state": geo.get("state"),
        "country": geo.get("country"), "country_code": geo.get("countryCode"),
        "linkedin": (d.get("linkedin") or {}).get("handle"),
        "twitter": (d.get("twitter") or {}).get("handle"),
        "logo": d.get("logo"), "raw": d,
    }


# ------------------------------------------------------------------- shaping
def map_pattern(hunter_pattern):
    """Translate Hunter's '{first}.{last}' into this app's 'first.last'.

    If Hunter ever returns a shape we do not recognise we return None rather
    than guessing: a wrong pattern silently mis-addresses every contact at that
    firm, which is worse than having no pattern at all.
    """
    if not hunter_pattern:
        return None
    p = hunter_pattern.strip().lower()
    if p in PATTERN_MAP:
        return PATTERN_MAP[p]
    # tolerate spacing and unknown separators by normalising the tokens
    simplified = re.sub(r"[\s{}]", "", p)
    for k, v in PATTERN_MAP.items():
        if re.sub(r"[\s{}]", "", k) == simplified:
            return v
    return None


def _person(e):
    """Normalise one Domain Search email row into our flat person shape.

    Names follow the shared contact vocabulary (see people_store._values):
    Hunter's `type` becomes email_type, its `sources` list is kept as JSON
    in email_sources so we can always show where an address came from.
    """
    sources = e.get("sources") or []
    return {
        "email": (e.get("value") or "").strip().lower() or None,
        "first_name": e.get("first_name"), "last_name": e.get("last_name"),
        "position": e.get("position"), "position_raw": e.get("position_raw"),
        "seniority": e.get("seniority"), "department": e.get("department"),
        "decision_maker": 1 if e.get("decision_maker") else 0,
        "email_type": e.get("type"),
        "email_sources": json.dumps(sources) if sources else "",
        "linkedin": e.get("linkedin"), "twitter": e.get("twitter"),
        "phone": e.get("phone_number"),
        "confidence": e.get("confidence"),
        "verification_status": (e.get("verification") or {}).get("status"),
        "verification_date": (e.get("verification") or {}).get("date"),
        "raw": e,
    }