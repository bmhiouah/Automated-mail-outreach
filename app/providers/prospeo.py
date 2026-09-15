"""Prospeo.io provider.

Why this one matters more than Hunter for finding *people*: Hunter's Domain
Search returns whoever it happens to have at a firm - in practice that meant
"Vice President Middle Office" when we wanted quants. Prospeo can filter by
job title and location, so we get the actual target audience: 6,576 quant
people across London / New York / Chicago on the first query.

It also returns things Hunter simply does not sell:
  - job_history      up to 5 past roles -> the "professional background" half
  - mobile           phone number with a verification status
  - location         structured city/state/country/timezone
  - company          full firm profile (revenue, funding, tech, job postings)

The catch: search results come back MASKED. `s********@blackrock.com` is not an
address, and storing it would poison the column and the pattern engine. Only
`enrich-person` reveals a real one, and that costs a credit - so we keep the
masked value out of the database entirely and record the person_id instead, so
the address can be revealed later when you actually want to write to them.

    from providers import prospeo
    prospeo.search_person(["Quantitative Researcher"], locations=["London"])
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db      # noqa: E402
import net     # noqa: E402

API = "https://api.prospeo.io"
PROVIDER = "prospeo"
CONFIG_PATH = os.path.join(db.BASE, "config.json")

# Costs. Prospeo bills per reveal, not per search for most plans, but we count
# a search too so a budget cannot silently run away. Adjust here if your plan
# differs - this is the one number worth checking against your dashboard.
CREDIT_COST = {
    "search-person": 1.0,
    "enrich-person": 1.0,
    "search-suggestions": 0.0,
}

ENDPOINT_PATHS = {
    "search-person": "search-person",
    "enrich-person": "enrich-person",
    "search-suggestions": "search-suggestions",
}


class ProspeoError(Exception):
    pass


def load_key(explicit=None):
    """Argument -> env -> config.json. Never stored in the database."""
    if explicit:
        return explicit.strip()
    env = (os.environ.get("PROSPEO_API_KEY") or "").strip()
    if env:
        return env
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                return (json.load(fh).get("prospeo_api_key") or "").strip()
        except (ValueError, OSError):
            return ""
    return ""


def _key(explicit=None):
    k = load_key(explicit)
    if not k:
        raise ProspeoError("no Prospeo key. Add prospeo_api_key to config.json "
                           "or set PROSPEO_API_KEY.")
    return k


def _call(endpoint, payload, api_key=None, credits=0.0, use_cache=True, force=False):
    """One cached, logged POST. Same ledger discipline as Hunter."""
    key = _key(api_key)
    request_key = _request_key(endpoint, payload)

    if use_cache and not force:
        seen = db.fetch_seen(PROVIDER, endpoint, request_key)
        if seen and seen["ok"] and seen.get("raw_path"):
            full = os.path.join(db.BASE, seen["raw_path"])
            if os.path.exists(full):
                with open(full, "r", encoding="utf-8") as fh:
                    return json.load(fh), dict(seen, cached=True)

    url = f"{API}/{ENDPOINT_PATHS.get(endpoint, endpoint)}"
    res = net.request_json(url, method="POST", throttle=True,
                           data=json.dumps(payload).encode("utf-8"),
                           headers={"X-KEY": key, "Content-Type": "application/json"})

    raw_path = sha = None
    if res["body"]:
        raw_path, sha = db.save_raw(PROVIDER, endpoint, request_key, res["body"])

    body = res["json"] or {}
    if not res["ok"] or body.get("error"):
        # Prospeo reports failures as {"error": true, "error_code": ...,
        # "filter_error": ...}. Surfacing "True" would be useless, so prefer
        # the specific message when there is one.
        err = (body.get("filter_error") or body.get("error_code")
               or body.get("error") or res["error"])
        db.record_fetch(PROVIDER, endpoint, request_key, res["status"], credits=0,
                        ok=False, error=str(err)[:300], raw_path=raw_path,
                        response_hash=sha)
        return None, {"ok": False, "status": res["status"], "error": err,
                      "cached": False}

    row = db.record_fetch(PROVIDER, endpoint, request_key, res["status"],
                          credits=credits, ok=True, raw_path=raw_path,
                          response_hash=sha)
    return body, dict(row, cached=False)


def _request_key(endpoint, payload):
    """A stable identity for the request, so paging and filters never collide."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)[:400]


# ------------------------------------------------------------------- endpoints
def search_person(job_titles, locations=None, websites=None, page=1,
                  api_key=None, force=False):
    """Find people by job title and location. This is the targeting Hunter lacks."""
    if not job_titles:
        return {"ok": False, "error": "at least one job title is required"}
    filters = {"person_job_title": {"include": list(job_titles),
                                    "match_mode": "CONTAINS"}}
    if locations:
        # `person_location` expects an internal location id, not free text.
        # `person_location_search` is the one that accepts "London".
        filters["person_location_search"] = {"include": list(locations)}
    if websites:
        filters["company"] = {"websites": {"include": list(websites)}}
    payload = {"page": int(page), "filters": filters}

    body, row = _call("search-person", payload, api_key=api_key,
                      credits=CREDIT_COST["search-person"], force=force)
    if not body:
        return {"ok": False, "error": row.get("error") or "search failed",
                "status": row.get("status"), "cached": row.get("cached", False)}
    pagination = body.get("pagination") or {}
    return {
        "ok": True,
        "results": body.get("results") or [],
        "total_count": pagination.get("total_count"),
        "total_page": pagination.get("total_page"),
        "page": pagination.get("current_page") or page,
        "credits": row.get("credits", 0.0),
        "cached": row.get("cached", False),
    }


def enrich_person(person_id, only_verified_email=True, api_key=None, force=False):
    """Reveal the real email and mobile for one person. Costs a credit."""
    payload = {"only_verified_email": bool(only_verified_email),
               "data": {"person_id": person_id}}
    body, row = _call("enrich-person", payload, api_key=api_key,
                      credits=CREDIT_COST["enrich-person"], force=force)
    if not body:
        return {"ok": False, "found": False, "error": row.get("error"),
                "status": row.get("status"), "cached": row.get("cached", False)}
    return {"ok": True, "found": True, "cached": row.get("cached", False),
            "person": body.get("person") or {}, "company": body.get("company") or {}}


def search_suggestions(location, api_key=None, force=False):
    """Resolve "London" into the location token Prospeo actually wants. Free."""
    body, row = _call("search-suggestions", {"location_search": location},
                      api_key=api_key, credits=0.0, force=force)
    if not body:
        return {"ok": False, "error": row.get("error")}
    return {"ok": True, "data": body.get("data") or body, "cached": row.get("cached", False)}


def resolve_location(query, api_key=None):
    """Turn free text into a location Prospeo will accept.

    The API rejects anything that did not come from its own suggestions, so
    "London" has to become "London, United Kingdom" before we can filter on it.
    Prefer an exact match; otherwise take the first suggestion starting with the
    query, which for a city name is the best-known one. Returns (name, raw).
    """
    if not query:
        return None, None
    r = search_suggestions(query, api_key=api_key)
    if not r.get("ok"):
        return None, None
    sugg = (r.get("data") or {}).get("location_suggestions") or []
    q = query.strip().lower()
    for s in sugg:
        if (s.get("name") or "").strip().lower() == q:
            return s["name"], s
    for s in sugg:
        if (s.get("name") or "").strip().lower().startswith(q):
            return s["name"], s
    return None, None


# ------------------------------------------------------------------- shaping
def _unmasked(value):
    """A masked value is not a value. Prospeo hides data until it is revealed."""
    if not value:
        return ""
    return "" if "*" in str(value) else str(value).strip()


def normalise_person(result):
    """Turn one search result into our flat person shape.

    Masked email/phone are dropped rather than stored: a half-address looks
    like data and would break the pattern learner that reconstructs addresses.
    """
    person = result.get("person") or {}
    company = result.get("company") or {}
    location = person.get("location") or {}
    email = person.get("email") or {}
    mobile = person.get("mobile") or {}

    full = person.get("full_name") or ""
    first = person.get("first_name") or ""
    last = person.get("last_name") or ""
    if not first and not last and full:
        parts = full.split()
        first, last = parts[0], " ".join(parts[1:])

    history = normalise_job_history(person)
    # Prospeo puts seniority on the job, not the person. Take it from the
    # current role so "Vice President" is not lost.
    seniority = ""
    for j in history:
        if j.get("is_current"):
            seniority = j.get("seniority") or ""
            break

    return {
        "person_id": person.get("person_id"),
        "first_name": first, "last_name": last, "full_name": full,
        "job_title": person.get("current_job_title") or "",
        # Verbatim: Prospeo's current_job_title is already the source's own
        # string, and headline is the longer LinkedIn one. Both kept.
        "position_raw": person.get("current_job_title") or "",
        "headline": person.get("headline") or "",
        "seniority_level": seniority,
        "seniority": seniority,
        "linkedin_url": person.get("linkedin_url") or "",
        "email": _unmasked(email.get("email")),
        "email_verification": (email.get("status") or "").lower(),
        "email_masked": not _unmasked(email.get("email")) and bool(email.get("email")),
        "phone": _unmasked(mobile.get("mobile")),
        "city": location.get("city") or "",
        "state": location.get("state") or "",
        "country": location.get("country") or "",
        "country_code": location.get("country_code") or "",
        "timezone": location.get("time_zone") or "",
        "location_raw": ", ".join(
            p for p in (location.get("city"), location.get("state"),
                        location.get("country")) if p),
        "job_history": history,
        "company": normalise_company(company),
    }


def normalise_job_history(person):
    """Career history - the background Hunter cannot give us."""
    out = []
    for j in (person.get("job_history") or []):
        out.append({
            "title": j.get("title"), "company_name": j.get("company_name"),
            "seniority": j.get("seniority"), "start_year": j.get("start_year"),
            "start_month": j.get("start_month"), "end_year": j.get("end_year"),
            "end_month": j.get("end_month"),
            "duration_months": j.get("duration_in_months"),
            "is_current": bool(j.get("current")),
        })
    return out


def normalise_company(company):
    loc = company.get("location") or {}
    return {
        "prospeo_id": company.get("company_id"),
        "name": company.get("name"),
        "domain": company.get("domain") or "",
        "website": company.get("website") or "",
        "description": company.get("description") or "",
        "founded_year": company.get("founded"),
        "employee_count": company.get("employee_count"),
        "headcount": company.get("employee_range") or "",
        "industry": company.get("industry") or "",
        "company_type": company.get("type") or "",
        "address": loc.get("raw_address") or "",
        "hq_city": loc.get("city") or "",
        "hq_country": loc.get("country") or "",
        "linkedin_url": company.get("linkedin_url") or "",
        "twitter": company.get("twitter_url") or "",
        "logo_url": company.get("logo_url") or "",
        "revenue_printed": company.get("revenue_range_printed") or "",
        "funding_total": (company.get("funding") or {}).get("total_funding"),
        "technologies": (company.get("technology") or {}).get("technology_names") or [],
        "job_postings_count": (company.get("job_postings") or {}).get("active_count"),
    }
