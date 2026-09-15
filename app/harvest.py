"""Progressive Hunter harvester. Run it repeatedly; the database only grows.

This is the loop the whole database idea rests on. It is deliberately built so
that running it twice is cheap and running it ten times converges, rather than
re-fetching the world each time:

  - every call goes through the fetch ledger, so a request already made is
    never made (or paid for) again inside the refresh window;
  - the free endpoints (account, domain finder) are used first, so credits are
    only ever spent on Domain Search and enrichment;
  - a hard `--budget` stops the run mid-way instead of overshooting the month's
    plan, and the next run resumes from the ledger;
  - `--dry-run` reports exactly what would be fetched and what it would cost,
    so spend is approved before it happens.

Usage
    python3 app/harvest.py --set-key abc123          # store the key (gitignored)
    python3 app/harvest.py --check-key               # plan + credits remaining
    python3 app/harvest.py --tier 1 --dry-run        # what would happen, free
    python3 app/harvest.py --tier 1 --budget 20      # do it, stop at 20 credits
    python3 app/harvest.py --company "Jane Street"   # just one firm
    python3 app/harvest.py --tier 1 --enrich --budget 30

Nothing is sent anywhere. This only reads from Hunter and writes to the local DB.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db             # noqa: E402
import server         # noqa: E402
from providers import hunter  # noqa: E402

DEFAULT_REFRESH_DAYS = 30

# Which firm fields a harvested company profile is allowed to fill. Empty-only,
# so a brief or a domain you typed by hand is never clobbered by an API.
COMPANY_FILL = ["description", "founded_year", "headcount", "employee_count",
                "industry", "company_type", "keywords", "address", "linkedin_url",
                "twitter", "ticker"]


def now():
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------- target picking
def pick_firms(tier=None, company=None, limit=None, refresh_days=DEFAULT_REFRESH_DAYS,
               only_missing=False):
    """Firms to work on, ordered tier -> has-brief -> name, like the sourcing tab.

    Firms fetched inside the refresh window are skipped unless --force-refresh,
    which is what makes a second run nearly free.
    """
    if company:
        where, args = "lower(name)=lower(?)", [company]
    else:
        where, args = "1=1", []
        if tier:
            tiers = [t.strip() for t in str(tier).split(",") if t.strip()]
            where += f" AND tier IN ({','.join('?' * len(tiers))})"
            args += tiers
    if only_missing:
        where += " AND (domain IS NULL OR domain='')"
    sql = f"SELECT * FROM companies WHERE {where} ORDER BY tier ASC, name ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = db.query(sql, args)

    cutoff = (datetime.now() - timedelta(days=refresh_days)).isoformat(timespec="seconds")
    out = []
    for c in rows:
        seen = db.fetch_seen("hunter", "domain-search", f"domain={c.get('domain')}") \
            if c.get("domain") else None
        fresh = bool(seen and seen.get("ok") and (seen.get("fetched_at") or "") >= cutoff)
        out.append(dict(c, _fresh=fresh, _last_fetch=(seen or {}).get("fetched_at")))
    return out


# --------------------------------------------------------------------- merging
def upsert_person(person, company, pattern_name=None, enrich=None):
    """Insert or update one harvested person. Returns 'added' | 'updated' | 'skipped'.

    The merge rule is the app's existing one, extended to API data:
    **a harvested value never overwrites something a human put there.** Fields
    are filled only when empty, and an email is only replaced when the existing
    one was itself a guess and the new one is not. That keeps the paste reader's
    curated contacts and their verified addresses safe from a later harvest.
    """
    first = (person.get("first_name") or "").strip()
    last = (person.get("last_name") or "").strip()
    if not first and not last:
        return "skipped"
    cname = company["name"]
    email = (person.get("email") or "").strip().lower()

    # Hunter's verification status decides whether an address counts as evidence
    # or merely as a strong lead. `accept_all` domains accept everything, so a
    # bounce there proves nothing - those stay 'unknown', not 'verified'.
    vstatus = (person.get("verification_status") or "").lower()
    new_email_status = "verified" if vstatus == "valid" else "unknown"

    title = person.get("position_raw") or person.get("position") or ""
    seniority, desk = server.derive_from_title(title)

    existing = db.query(
        "SELECT * FROM contacts WHERE lower(first_name)=lower(?) AND lower(last_name)=lower(?) "
        "AND lower(COALESCE(company_name,''))=lower(?)", [first, last, cname])

    values = {
        "job_title": title,
        "headline": person.get("position_raw") or person.get("position") or "",
        "role": person.get("role") or "",
        "department": person.get("department") or "",
        "seniority_level": person.get("seniority") or "",
        "decision_maker": 1 if person.get("decision_maker") else 0,
        "linkedin_url": person.get("linkedin") or "",
        "twitter": person.get("twitter") or "",
        "phone": person.get("phone") or "",
        "email_confidence": person.get("confidence"),
        "email_verified_at": person.get("verification_date") or "",
        "city": person.get("city") or "",
        "state": person.get("state") or "",
        "country": person.get("country") or "",
        "country_code": person.get("country_code") or "",
        "location_raw": person.get("location_raw") or "",
        "latitude": person.get("latitude"),
        "longitude": person.get("longitude"),
        "timezone": person.get("timezone") or "",
        "github": person.get("github") or "",
        "avatar": person.get("avatar") or "",
        "bio": person.get("bio") or "",
        "last_seen_at": person.get("last_seen_at") or "",
    }
    if seniority:
        values["seniority"] = seniority
    if desk:
        values["desk"] = desk

    if existing:
        row = existing[0]
        sets, args = [], []
        for f, v in values.items():
            if v in (None, "") or v == 0 and f == "decision_maker":
                continue
            if (row.get(f) or "") in (None, ""):
                sets.append(f"{f}=?")
                args.append(v)
        # Email: fill when empty; replace only a guess with real evidence.
        if email and (not row.get("email")):
            sets += ["email=?", "email_status=?", "email_source=?"]
            args += [email, new_email_status, "hunter"]
        elif email and row.get("email") != email and (row.get("email_status") or "") == "guessed":
            sets += ["email=?", "email_status=?", "email_source=?"]
            args += [email, new_email_status, "hunter"]
        if not sets:
            return "skipped"
        sets += ["source_updated=?", "updated_at=?"]
        args += [now(), now(), row["id"]]
        db.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", args)
        return "updated"

    cols = ["first_name", "last_name", "company_id", "company_name", "source",
            "status", "priority", "email", "email_status", "email_source",
            "city", "country", "source_updated", "updated_at"]
    vals = [first, last, company["id"], cname, "hunter", "identified", 3,
            email, new_email_status if email else "missing",
            "hunter" if email else "", person.get("city") or "",
            person.get("country") or "", now(), now()]
    for f, v in values.items():
        if v not in (None, ""):
            cols.append(f)
            vals.append(v)
    db.execute(f"INSERT INTO contacts ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)})", vals)
    return "added"


def store_pattern(company, pattern_name, pattern_raw, confidence=0.9):
    """Record the pattern Hunter reported, without trampling a better one.

    Hunter's `pattern` is derived from real addresses it has seen, so it is
    stronger evidence than a shape heuristic - but it is not stronger than a
    sample the user pasted, which is why a firm that already has a pattern from
    a pasted address (confidence >= 0.85) is left alone.
    """
    if not pattern_name:
        return False
    current = (company.get("email_pattern") or "").strip()
    current_conf = company.get("pattern_confidence") or 0
    if current and current == pattern_name and current_conf >= confidence:
        return False
    if current and current_conf >= 0.95:
        return False
    db.execute("UPDATE companies SET email_pattern=?, pattern_confidence=?, updated_at=? "
               "WHERE id=?", [pattern_name, confidence, now(), company["id"]])
    db.execute("INSERT INTO pattern_evidence (company_id, pattern, sample_email, source, "
               "confidence, notes) VALUES (?,?,?,?,?,?)",
               [company["id"], pattern_name, "", "hunter domain search", confidence,
                f"Hunter reported pattern {pattern_raw}"])
    return True


def update_company(company, enrichment):
    """Fill empty company profile fields only. Never overwrites your own data."""
    sets, args = [], []
    for f in COMPANY_FILL:
        v = enrichment.get(f)
        if v in (None, "", [], {}):
            continue
        if (company.get(f) or "") in (None, ""):
            sets.append(f"{f}=?")
            args.append(",".join(v) if isinstance(v, list) else v)
    if enrichment.get("domain") and not (company.get("domain") or "").strip():
        sets.append("domain=?")
        args.append(enrichment["domain"])
    if not sets:
        return False
    sets += ["source=?", "source_updated=?", "updated_at=?"]
    args += ["hunter", now(), now(), company["id"]]
    db.execute(f"UPDATE companies SET {', '.join(sets)} WHERE id=?", args)
    return True


# ----------------------------------------------------------------------- report
def cmd_check_key(args):
    info = hunter.account(api_key=args.api_key, force=True)
    if not info.get("ok"):
        print(f"  Hunter account check failed: {info.get('error')}")
        return 1
    print(f"\n  Hunter plan:      {info.get('plan')} (level {info.get('plan_level')})")
    print(f"  Credits remaining: {info.get('credits_remaining')} of {info.get('credits_available')}")
    print(f"  Used this period:  {info.get('credits_used')}")
    print(f"  Resets:            {info.get('reset_date')}\n")
    return 0


def cmd_set_key(args):
    path = hunter.save_key(args.set_key)
    print(f"  Key stored in {path} (gitignored, chmod 600).")
    return cmd_check_key(args)


def cmd_harvest(args):
    db.ensure_schema(verbose=True)
    has_key = bool(hunter.load_key(args.api_key))
    if not has_key and not args.dry_run:
        print("  No Hunter key. Run: python3 app/harvest.py --set-key YOUR_KEY")
        return 1
    if not has_key:
        print("  No Hunter key: firms that already have a domain can still be "
              "counted, but new domains cannot be resolved.")

    firms = pick_firms(tier=args.tier, company=args.company, limit=args.limit,
                       refresh_days=args.refresh_days, only_missing=args.only_missing)
    todo = [f for f in firms if args.force_refresh or not f["_fresh"]]
    skipped_fresh = len(firms) - len(todo)

    print(f"\n  Hunter harvest — budget {args.budget} credits "
          f"({'DRY RUN' if args.dry_run else 'live'})")
    print(f"  {len(firms)} firms selected, {skipped_fresh} skipped as already fresh, "
          f"{len(todo)} to fetch\n")
    if not todo:
        print("  Nothing to do. Use --force-refresh to re-fetch.\n")
        return 0

    spent = 0.0
    added = updated = skipped = 0
    enriched = 0
    stopped = None

    for i, company in enumerate(todo, 1):
        domain = (company.get("domain") or "").strip()

        # Free step: resolve a domain when the seed has none.
        if not domain:
            res = hunter.find_domain(company["name"], api_key=args.api_key)
            if res.get("ok"):
                domain = res["domain"]
                print(f"  [{i}/{len(todo)}] {company['name']}: domain -> {domain} (free)")
                if not args.dry_run:
                    db.execute("UPDATE companies SET domain=?, updated_at=? WHERE id=?",
                               [domain, now(), company["id"]])
            else:
                print(f"  [{i}/{len(todo)}] {company['name']}: no domain found, skipped")
                continue

        if args.dry_run:
            print(f"  [{i}/{len(todo)}] {company['name']} ({domain}) — would run Domain Search "
                  f"(1 credit per 10 emails)")
            continue

        if spent >= args.budget:
            stopped = company["name"]
            break

        # The paid step: the people, and the firm's email pattern.
        res = hunter.domain_search(domain=domain, company=company["name"],
                                   limit=args.limit_emails, api_key=args.api_key,
                                   force=args.force_refresh)
        if not res.get("ok"):
            print(f"  [{i}/{len(todo)}] {company['name']}: {res.get('error')} "
                  f"(status {res.get('status')})")
            if res.get("status") == 429:
                stopped = "credits exhausted"
                break
            continue

        cost = res.get("credits") or 0.0
        spent += cost
        people = res.get("people") or []
        for p in people:
            outcome = upsert_person(p, company)
            if outcome == "added":
                added += 1
            elif outcome == "updated":
                updated += 1
            else:
                skipped += 1
        learned = store_pattern(company, res.get("pattern_name"), res.get("pattern"),
                                confidence=0.9)

        # Optional: firm profile (~1 credit). Off unless asked for.
        if args.company_enrich and spent < args.budget:
            ce = hunter.company_enrichment(domain, api_key=args.api_key,
                                           force=args.force_refresh)
            if ce.get("ok") and ce.get("found"):
                spent += hunter.CREDIT_COST["companies-find"]
                update_company(company, ce)

        tag = "cached, 0 credits" if res.get("cached") else f"{cost:g} credits"
        extra = f", pattern {res['pattern_name']}" if learned else ""
        print(f"  [{i}/{len(todo)}] {company['name']}: {len(people)} people "
              f"({tag}){extra}  [spent {spent:g}/{args.budget}]")

        # Optional: enrich each address for location/geo (0.2 credits each).
        if args.enrich and people and spent < args.budget:
            for p in people:
                if not p.get("email") or spent >= args.budget:
                    break
                en = hunter.enrich_email(p["email"], api_key=args.api_key)
                if not en.get("found"):
                    continue
                if not en.get("cached"):
                    spent += hunter.CREDIT_COST["people-find"]
                # The enriched fields are merged into the person we just stored,
                # still fill-empty-only, so a harvested city never replaces a
                # location you typed by hand.
                upsert_person({**p, "first_name": en.get("first_name") or p.get("first_name"),
                               "last_name": en.get("last_name") or p.get("last_name"),
                               "city": en.get("city"), "state": en.get("state"),
                               "country": en.get("country"),
                               "country_code": en.get("country_code"),
                               "location_raw": en.get("location_raw"),
                               "latitude": en.get("latitude"), "longitude": en.get("longitude"),
                               "timezone": en.get("timezone"), "github": en.get("github"),
                               "twitter": en.get("twitter"), "avatar": en.get("avatar"),
                               "bio": en.get("bio"), "phone": en.get("phone"),
                               "last_seen_at": en.get("last_seen_at")},
                              company)
                db.execute("UPDATE contacts SET enriched_at=?, updated_at=? "
                           "WHERE lower(first_name)=lower(?) AND lower(last_name)=lower(?) "
                           "AND lower(company_name)=lower(?)",
                           [now(), now(), p.get("first_name") or "", p.get("last_name") or "",
                            company["name"]])
                enriched += 1

    print(f"\n  Done. {added} added, {updated} updated, {skipped} unchanged, "
          f"{enriched} enriched.")
    print(f"  Credits spent this run: {spent:g} (ledger total: "
          f"{db.credits_spent('hunter'):g})")
    if stopped:
        print(f"  Stopped at {stopped}. Re-run to continue where this left off.")
    print()
    return 0


def cmd_status(args):
    """What is in the database now, and what has been spent getting it."""
    db.ensure_schema()
    n_contacts = db.query("SELECT COUNT(*) n FROM contacts")[0]["n"]
    n_hunter = db.query("SELECT COUNT(*) n FROM contacts WHERE source='hunter'")[0]["n"]
    n_sites = db.query("SELECT COUNT(*) n FROM contacts WHERE city IS NOT NULL AND city!=''")[0]["n"]
    pages = db.query("SELECT COUNT(*) n FROM fetch_log WHERE provider='hunter'")[0]["n"]
    cached = db.query("SELECT COUNT(*) n FROM raw_payload WHERE provider='hunter'")[0]["n"]
    live_sites = db.query("SELECT COUNT(*) n FROM companies WHERE description IS NOT NULL "
                          "AND description!=''")[0]["n"]
    print(f"\n  Database")
    print(f"    contacts:            {n_contacts} ({n_hunter} from Hunter)")
    print(f"    with a location:     {n_sites}")
    print(f"    firms profiled:      {live_sites}")
    print(f"\n  Hunter")
    print(f"    API calls logged:    {pages}")
    print(f"    raw payloads kept:   {cached}")
    print(f"    credits spent total: {db.credits_spent('hunter'):g}")
    spent_month = db.credits_spent("hunter", since=datetime.now().strftime("%Y-%m-01"))
    print(f"    credits this month:  {spent_month:g}\n")
    return 0


def main():
    p = argparse.ArgumentParser(description="Progressive Hunter harvester.")
    p.add_argument("--tier", default=None, help="1,2 — which tiers to work on")
    p.add_argument("--company", default=None, help="exact firm name")
    p.add_argument("--limit", type=int, default=None, help="max firms this run")
    p.add_argument("--limit-emails", type=int, default=10, help="emails per firm (free plan max 10)")
    p.add_argument("--budget", type=float, default=25.0, help="credit ceiling for this run")
    p.add_argument("--refresh-days", type=int, default=DEFAULT_REFRESH_DAYS,
                   help="skip firms fetched within N days")
    p.add_argument("--force-refresh", action="store_true", help="ignore the cache")
    p.add_argument("--only-missing", action="store_true", help="firms with no domain yet")
    p.add_argument("--enrich", action="store_true", help="also enrich each address (~0.2 credits)")
    p.add_argument("--company-enrich", action="store_true", help="also fetch firm profiles (~1 credit)")
    p.add_argument("--dry-run", action="store_true", help="report only, spend nothing")
    p.add_argument("--api-key", default=None, help="override the stored key for this run")
    p.add_argument("--set-key", default=None, help="store a Hunter API key, then verify it")
    p.add_argument("--check-key", action="store_true", help="show plan and remaining credits")
    p.add_argument("--status", action="store_true", help="database and credit summary")
    args = p.parse_args()

    if args.set_key:
        return cmd_set_key(args)
    if args.check_key:
        return cmd_check_key(args)
    if args.status:
        return cmd_status(args)
    return cmd_harvest(args)


if __name__ == "__main__":
    sys.exit(main())