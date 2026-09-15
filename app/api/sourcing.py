"""Sourcing: the prioritised worklist and the per-firm search links. Nothing is scraped - only queries are built."""

import os
import sys

from urllib.parse import quote_plus

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db         # noqa: E402
import email_gen  # noqa: E402
from taxonomy import TITLES_BY_TYPE  # noqa: E402


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


ROUTES = [
    ("GET", "sourcing-queue", lambda p, rest, body: api_sourcing_queue(p)),
    ("GET", "sourcing/<id>", lambda p, rest, body: sourcing_links(rest[0])),
]
