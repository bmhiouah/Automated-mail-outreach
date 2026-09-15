"""Progressive harvester. Run it repeatedly; the database only grows.

Two providers, each used for what it is actually good at:

  prospeo  -> FINDING the right people. Filters by job title and location, so we
              get quants in London rather than whoever a firm happens to employ.
              Also supplies career history, phone and the firm profile.
  hunter   -> the firm's EMAIL CONVENTION (`data.pattern`), which Prospeo does
              not give us, plus bulk domain discovery.

The point of the whole design is that the database compounds:
  - a fetch ledger means a request already made is never made (or paid for) again;
  - one real (name, address) pair teaches the firm's convention, so every later
    person at that firm can be addressed for free;
  - a credit budget stops the run mid-way, and the next run resumes.

Usage
    python3 app/harvest.py --set-key HUNTER_KEY        # store a key (gitignored)
    python3 app/harvest.py --check-key                 # quota
    python3 app/harvest.py --source prospeo --pages 2 --dry-run
    python3 app/harvest.py --source prospeo --locations "London,Paris" --budget 10
    python3 app/harvest.py --source hunter --tier 1 --budget 5
    python3 app/harvest.py --status
    python3 app/harvest.py --reconstruct               # fill addresses from patterns

Nothing is sent anywhere except the provider APIs. Everything lands in SQLite.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db            # noqa: E402
import people_store  # noqa: E402
from providers import hunter, prospeo  # noqa: E402

DEFAULT_REFRESH_DAYS = 30

# The audience we are actually looking for. Defaulted from the Prospeo notebook.
DEFAULT_TITLES = [
    "Quantitative Researcher", "Quantitative Research Analyst",
    "Quantitative Trader", "Quantitative Analyst", "Quantitative Developer",
    "Systematic Trader", "Systematic Researcher", "Algorithmic Trader",
    "Algorithmic Researcher", "Portfolio Manager", "Systematic Portfolio Manager",
]
DEFAULT_LOCATIONS = ["London", "Paris", "New York"]


def now():
    return __import__("datetime").datetime.now().isoformat(timespec="seconds")


def dedupe_locations(locations):
    """Drop a broad location already covered by a narrower one.

    "London, United Kingdom" cannot be looked up as a single string, so it
    splits into "London" + "United Kingdom". Keeping both silently widens the
    search to the whole country (13,560 people instead of 6,576) and spends
    credits on the wrong geography. If one resolved name contains another, the
    narrower one wins.
    """
    return [a for a in locations
            if not any(a != b and a.lower() in b.lower() for b in locations)]


def parse_locations(raw, api_key=None):
    """Split a location list without breaking names that contain commas.

    "London, United Kingdom" is one place, not two - but splitting on the comma
    turns it into "London" + " United Kingdom" and the search then covers a
    whole country. Try the whole string first; only fall back to splitting when
    it does not resolve as a single location.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    resolved, _ = prospeo.resolve_location(raw, api_key=api_key)
    if resolved:
        return [resolved]
    return [x.strip() for x in raw.split(",") if x.strip()]


# ------------------------------------------------------------------- prospeo
def run_prospeo(args):
    db.ensure_schema(verbose=True)
    if not prospeo.load_key(args.api_key) and not args.dry_run:
        print("  No Prospeo key. Add prospeo_api_key to config.json.")
        return 1

    titles = [t.strip() for t in (args.titles or "").split(",") if t.strip()] or DEFAULT_TITLES
    wanted = parse_locations(args.locations, api_key=args.api_key) or DEFAULT_LOCATIONS

    # Prospeo only accepts locations from its own suggestion list, and there is
    # a "London" in Kentucky. Resolve each one so we filter on the right place.
    locations, unresolved = [], []
    for name in wanted:
        resolved, _ = prospeo.resolve_location(name, api_key=args.api_key)
        if resolved:
            locations.append(resolved)
        else:
            unresolved.append(name)
    locations = dedupe_locations(locations)
    if unresolved:
        print(f"  could not resolve: {', '.join(unresolved)}")

    print(f"\n  Prospeo harvest — budget {args.budget} credits "
          f"({'DRY RUN' if args.dry_run else 'live'})")
    print(f"  {len(titles)} job titles · {', '.join(locations) or 'no location'} "
          f"· up to {args.pages} pages\n")
    if not locations and not args.dry_run:
        print("  No usable location; aborting rather than searching unfiltered.")
        return 1

    spent = 0.0
    added = updated = unchanged = 0
    new_firms = 0
    history = 0
    revealed = 0

    for page in range(1, args.pages + 1):
        if args.dry_run:
            print(f"  page {page}: would search-person (1 credit)")
            continue
        if spent >= args.budget:
            print(f"  budget reached, stopping before page {page}")
            break
        res = prospeo.search_person(titles, locations=locations, page=page,
                                    api_key=args.api_key)
        if not res.get("ok"):
            print(f"  page {page}: {res.get('error')}")
            break
        spent += 0 if res.get("cached") else prospeo.CREDIT_COST["search-person"]

        people = res.get("results") or []
        if not people:
            print("  no more results")
            break

        for raw in people:
            p = prospeo.normalise_person(raw)
            company, created = people_store.resolve_or_create_company(
                domain=p["company"].get("domain"), name=p["company"].get("name"))
            if created:
                new_firms += 1
            if company and p["company"]:
                people_store.fill_company(company, p["company"], "prospeo")

            # Reveal the real address only when asked: it costs a credit.
            if args.reveal and p.get("person_id") and not p.get("email") \
                    and spent < args.budget:
                en = prospeo.enrich_person(p["person_id"], api_key=args.api_key)
                if en.get("found"):
                    if not en.get("cached"):
                        spent += prospeo.CREDIT_COST["enrich-person"]
                    ep = en.get("person") or {}
                    em = ep.get("email") or {}
                    mob = ep.get("mobile") or {}
                    p["email"] = prospeo._unmasked(em.get("email")) or p["email"]
                    p["email_verification"] = (em.get("status") or "").lower()
                    p["phone"] = prospeo._unmasked(mob.get("mobile")) or p["phone"]
                    if p.get("email"):
                        revealed += 1

            outcome, cid = people_store.upsert(p, company, provider="prospeo")
            if outcome == "added":
                added += 1
            elif outcome == "updated":
                updated += 1
            else:
                unchanged += 1
            if cid:
                if p.get("person_id"):
                    people_store.note_source_id(cid, "prospeo", p["person_id"])
                history += people_store.save_job_history(cid, p.get("job_history"), "prospeo")

        print(f"  page {page}: {len(people)} people "
              f"(total {res.get('total_count')}, {res.get('total_page')} pages) "
              f"[spent {spent:g}/{args.budget}]")
        if page >= (res.get("total_page") or page):
            break

    print(f"\n  Done. {added} added, {updated} updated, {unchanged} unchanged.")
    # Report what the table holds, not how many rows we wrote: re-processing a
    # person replaces their history, so a cumulative count reads as double.
    total_hist = db.query("SELECT COUNT(*) n FROM person_job_history")[0]["n"]
    print(f"  {new_firms} new firms discovered, {history} people with career history "
          f"({total_hist} rows in total), {revealed} addresses revealed.")
    print(f"  Credits spent: {spent:g}\n")
    return 0


# -------------------------------------------------------------------- hunter
def pick_firms(tier=None, company=None, limit=None, refresh_days=DEFAULT_REFRESH_DAYS,
               only_missing=False):
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
    return db.query(sql, args)


def run_hunter(args):
    db.ensure_schema(verbose=True)
    if not hunter.load_key(args.api_key) and not args.dry_run:
        print("  No Hunter key. Run: python3 app/harvest.py --set-key YOUR_KEY")
        return 1

    firms = pick_firms(tier=args.tier, company=args.company, limit=args.limit,
                       only_missing=args.only_missing)
    todo = [f for f in firms if args.force_refresh or not _fresh(f, args.refresh_days)]
    print(f"\n  Hunter harvest — budget {args.budget} credits "
          f"({'DRY RUN' if args.dry_run else 'live'})")
    print(f"  {len(firms)} firms selected, {len(firms) - len(todo)} already fresh, "
          f"{len(todo)} to fetch\n")

    spent = 0.0
    added = updated = unchanged = enriched = 0
    patterns = 0

    for i, company in enumerate(todo, 1):
        domain = (company.get("domain") or "").strip()
        if not domain:
            if args.dry_run:
                print(f"  [{i}/{len(todo)}] {company['name']}: would resolve domain (free)")
                continue
            res = hunter.find_domain(company["name"], api_key=args.api_key)
            if not res.get("ok"):
                print(f"  [{i}/{len(todo)}] {company['name']}: no domain")
                continue
            domain = res["domain"]
            db.execute("UPDATE companies SET domain=?, updated_at=? WHERE id=?",
                       [domain, now(), company["id"]])
        if args.dry_run:
            print(f"  [{i}/{len(todo)}] {company['name']} ({domain}): "
                  f"would Domain Search (1 credit per 10 emails)")
            continue
        if spent >= args.budget:
            print(f"  budget reached, stopping before {company['name']}")
            break

        res = hunter.domain_search(domain=domain, limit=args.limit_emails,
                                   api_key=args.api_key, force=args.force_refresh)
        if not res.get("ok"):
            print(f"  [{i}/{len(todo)}] {company['name']}: {res.get('error')}")
            if res.get("status") == 429:
                break
            continue
        spent += 0 if res.get("cached") else (res.get("credits") or 0.0)

        # Hunter hands us the firm's convention directly - free and strong.
        if res.get("pattern_name"):
            from email_pattern import learn_from_hunter
            if learn_from_hunter(company, res.get("pattern"),
                                 res.get("pattern_name")).get("learned"):
                patterns += 1

        for p in res.get("people") or []:
            did_enrich = False
            if args.enrich and p.get("email") and spent < args.budget:
                en = hunter.enrich_email(p["email"], api_key=args.api_key)
                if en.get("found"):
                    if not en.get("cached"):
                        spent += hunter.CREDIT_COST["people-find"]
                    p.update({k: en.get(k) for k in
                              ("city", "state", "country", "country_code",
                               "location_raw", "timezone", "github", "phone",
                               "last_seen_at") if en.get(k)})
                    enriched += 1
                    did_enrich = True
            outcome, cid = people_store.upsert(p, company, provider="hunter")
            added += outcome == "added"
            updated += outcome == "updated"
            unchanged += outcome == "unchanged"
            if did_enrich and cid:
                db.execute("UPDATE contacts SET enriched_at=?, updated_at=? WHERE id=?",
                           [now(), now(), cid])

        print(f"  [{i}/{len(todo)}] {company['name']}: {len(res.get('people') or [])} people "
              f"[spent {spent:g}/{args.budget}]")

    print(f"\n  Done. {added} added, {updated} updated, {unchanged} unchanged, "
          f"{enriched} enriched.")
    print(f"  Firm conventions learned: {patterns}")
    print(f"  Credits spent: {spent:g}\n")
    return 0


def _fresh(company, refresh_days):
    from datetime import datetime, timedelta
    if not company.get("domain"):
        return False
    seen = db.fetch_seen("hunter", "domain-search", f"domain={company['domain']}")
    if not (seen and seen.get("ok") and seen.get("fetched_at")):
        return False
    cutoff = (datetime.now() - timedelta(days=refresh_days)).isoformat(timespec="seconds")
    return (seen["fetched_at"] or "") >= cutoff


# ------------------------------------------------------------------- commands
def cmd_check_key(args):
    out = []
    for name, mod in (("Hunter", hunter), ("Prospeo", prospeo)):
        if not mod.load_key(args.api_key):
            out.append(f"  {name}: no key")
            continue
        if name == "Hunter":
            info = mod.account(api_key=args.api_key, force=True)
            if info.get("ok"):
                out.append(f"  Hunter: plan {info.get('plan')}, "
                           f"{info.get('credits_remaining')} credits left "
                           f"(resets {info.get('reset_date')})")
            else:
                out.append(f"  Hunter: {info.get('error')}")
        else:
            out.append("  Prospeo: key present (no quota endpoint documented)")
    print("\n" + "\n".join(out) + "\n")
    return 0


def cmd_set_key(args):
    path = hunter.save_key(args.set_key)
    print(f"  Key stored in {path} (gitignored, chmod 600).")
    return cmd_check_key(args)


def cmd_status(args):
    db.ensure_schema()
    n = db.query("SELECT COUNT(*) n FROM contacts")[0]["n"]
    by_src = db.query("SELECT COALESCE(source,'?') k, COUNT(*) n FROM contacts "
                      "GROUP BY k ORDER BY n DESC")
    located = db.query("SELECT COUNT(*) n FROM contacts WHERE city IS NOT NULL AND city!=''")[0]["n"]
    mailed = db.query("SELECT COUNT(*) n FROM contacts WHERE email IS NOT NULL AND email!=''")[0]["n"]
    verified = db.query("SELECT COUNT(*) n FROM contacts WHERE email_status='verified'")[0]["n"]
    hist = db.query("SELECT COUNT(*) n FROM person_job_history")[0]["n"]
    firms = db.query("SELECT COUNT(*) n FROM companies")[0]["n"]
    patterned = db.query("SELECT COUNT(*) n FROM companies WHERE email_pattern IS NOT NULL "
                         "AND email_pattern!=''")[0]["n"]
    calls = db.query("SELECT COUNT(*) n FROM fetch_log")[0]["n"]
    raws = db.query("SELECT COUNT(*) n FROM raw_payload")[0]["n"]
    print(f"\n  Database")
    print(f"    firms:              {firms} ({patterned} with a known email convention)")
    print(f"    people:             {n}")
    for r in by_src:
        print(f"      {r['k']:>10}: {r['n']}")
    print(f"    with an address:    {mailed} ({verified} verified)")
    print(f"    with a location:    {located}")
    print(f"    career rows:        {hist}")
    print(f"\n  Providers")
    print(f"    API calls logged:   {calls}")
    print(f"    raw payloads kept:  {raws}")
    print(f"    credits spent:      {db.credits_spent():g}\n")
    return 0


def cmd_reconstruct(args):
    """Turn known firm conventions into addresses for everyone missing one."""
    db.ensure_schema()
    res = people_store.reconstruct_missing()
    print(f"\n  {res['updated']} addresses reconstructed from firm conventions "
          f"(marked 'guessed').\n")
    return 0


def main():
    p = argparse.ArgumentParser(description="Progressive harvester.")
    p.add_argument("--source", default="prospeo", choices=["prospeo", "hunter"])
    p.add_argument("--tier", default=None, help="hunter: which tiers, e.g. 1 or 1,2")
    p.add_argument("--company", default=None, help="hunter: one exact firm name")
    p.add_argument("--limit", type=int, default=None, help="hunter: max firms")
    p.add_argument("--limit-emails", type=int, default=10, help="hunter: emails per firm")
    p.add_argument("--titles", default=None, help="prospeo: comma-separated job titles")
    p.add_argument("--locations", default=None, help="prospeo: comma-separated locations")
    p.add_argument("--pages", type=int, default=2, help="prospeo: pages of 25")
    p.add_argument("--reveal", action="store_true", help="prospeo: pay to unmask addresses")
    p.add_argument("--budget", type=float, default=25.0, help="credit ceiling for this run")
    p.add_argument("--enrich", action="store_true", help="hunter: enrich each address")
    p.add_argument("--refresh-days", type=int, default=DEFAULT_REFRESH_DAYS)
    p.add_argument("--force-refresh", action="store_true")
    p.add_argument("--only-missing", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--api-key", default=None)
    p.add_argument("--set-key", default=None)
    p.add_argument("--check-key", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--reconstruct", action="store_true")
    args = p.parse_args()

    if args.set_key:
        return cmd_set_key(args)
    if args.check_key:
        return cmd_check_key(args)
    if args.status:
        return cmd_status(args)
    if args.reconstruct:
        return cmd_reconstruct(args)
    if args.source == "prospeo":
        return run_prospeo(args)
    return run_hunter(args)


if __name__ == "__main__":
    sys.exit(main())
