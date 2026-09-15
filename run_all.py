#!/usr/bin/env python3
"""Feed the database across all sources, in the right order.

  1. email-format.com across every firm with a domain — free.
  2. reconstruct — free. Masked Prospeo addresses -> candidates.
  3. Hunter across tier-1 firms with a domain — metered.
  4. Prospeo across the quant titles in target cities — metered.
  5. reconstruct again — free. Any new pattern fills more rows.

Keys come from config.json (gitignored). Nothing leaves your machine.
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "app"))

import db                              # noqa: E402
from harvest import (                  # noqa: E402
    cmd_emailformat,
    cmd_reconstruct,
    run_hunter,
    run_prospeo,
    pick_firms,
)


def count_firms_with_domain(tier=None, company=None, limit=None):
    firms = pick_firms(tier=tier, company=company, limit=limit, only_missing=False)
    return [f for f in firms if (f.get("domain") or "").strip()], len(firms)


def load_keys():
    path = os.path.join(db.BASE, "config.json")
    keys = {"hunter": "", "prospeo": ""}
    if os.path.exists(path):
        try:
            import json
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            keys["hunter"] = (data.get("hunter_api_key") or "").strip()
            keys["prospeo"] = (data.get("prospeo_api_key") or "").strip()
        except (ValueError, OSError):
            pass
    return keys


class NS:
    """Minimal argparse.Namespace stand-in for the harvest functions."""


def run(args):
    keys = load_keys()
    dry = args.dry_run

    print("=" * 70)
    print("STEP 1 - email-format.com (free) across every firm with a domain")
    print("=" * 70)
    ef_firms, _ = count_firms_with_domain(
        tier=args.tier, company=args.company, limit=args.limit)
    total = len(pick_firms(
        tier=args.tier, company=args.company, limit=args.limit,
        only_missing=False))
    print(f"  firms with a domain: {len(ef_firms)} of {total}")
    if dry:
        print(f"  [DRY RUN] would fetch {len(ef_firms)} pages, 0 credits")
    else:
        if not keys["hunter"] and not keys["prospeo"]:
            print("  no API keys in config.json - email-format needs none, continuing")
        a = NS()
        a.tier = args.tier
        a.company = args.company
        a.limit = args.limit
        a.force_refresh = args.force_refresh
        cmd_emailformat(a)

    print()
    print("=" * 70)
    print("STEP 2 - reconstruct (free): masked addresses -> candidates")
    print("=" * 70)
    if dry:
        print("  [DRY RUN] would run the reconstruction pass")
    else:
        cmd_reconstruct(NS())

    hunter_ok = bool(keys["hunter"])
    prospeo_ok = bool(keys["prospeo"])

    if hunter_ok or prospeo_ok:
        print()
        print("=" * 70)
        print("STEP 3 - Hunter (metered) across tier-1 firms with a domain")
        print("=" * 70)
        h_firms, h_total = count_firms_with_domain(
            tier="1", company=args.company, limit=args.limit)
        print(f"  tier-1 firms with a domain: {len(h_firms)} (of {h_total} tier-1)")
        if dry:
            print(f"  [DRY RUN] would spend up to {args.hunter_budget} credits "
                  f"across {len(h_firms)} firms")
        elif hunter_ok:
            a = NS()
            a.tier = "1"
            a.company = args.company
            a.limit = args.limit
            a.limit_emails = args.limit_emails
            a.budget = args.hunter_budget
            a.refresh_days = args.refresh_days
            a.force_refresh = args.force_refresh
            a.only_missing = args.only_missing
            a.enrich = args.enrich
            a.dry_run = False
            a.api_key = keys["hunter"] or None
            run_hunter(a)
        else:
            print("  no Hunter key in config.json - skipping (add hunter_api_key)")

    if prospeo_ok or (not hunter_ok and not prospeo_ok and not dry):
        print()
        print("=" * 70)
        print("STEP 4 - Prospeo (metered): quant titles, target cities")
        print("=" * 70)
        if dry:
            print(f"  [DRY RUN] would spend up to {args.prospeo_budget} credits, "
                  f"titles={args.titles}, locations={args.locations}, "
                  f"pages={args.pages}")
        else:
            titles = (args.titles or
                      "Quantitative Researcher,Quantitative Research Analyst,"
                      "Quantitative Trader,Quantitative Analyst,"
                      "Quantitative Developer,Systematic Trader,"
                      "Algorithmic Trader,Algorithmic Researcher,"
                      "Portfolio Manager,Systematic Portfolio Manager")
            a = NS()
            a.titles = titles
            a.locations = args.locations or "London,Paris"
            a.pages = args.pages
            a.reveal = args.reveal
            a.budget = args.prospeo_budget
            a.dry_run = False
            a.force_refresh = args.force_refresh
            a.only_missing = args.only_missing
            a.refresh_days = args.refresh_days
            a.api_key = keys["prospeo"] or None
            run_prospeo(a)

    print()
    print("=" * 70)
    print("STEP 5 - reconstruct again (free): any new pattern fills more rows")
    print("=" * 70)
    if dry:
        print("  [DRY RUN] would run the reconstruction pass again")
    else:
        cmd_reconstruct(NS())

    print()
    print("Done. Current state:")
    db.ensure_schema()
    n = db.query("SELECT COUNT(*) n FROM contacts")[0]["n"]
    mailed = db.query(
        "SELECT COUNT(*) n FROM contacts WHERE email IS NOT NULL AND email!=''")[0]["n"]
    masked = db.query(
        "SELECT COUNT(*) n FROM contacts WHERE email_masked IS NOT NULL "
        "AND email_masked!=''")[0]["n"]
    patterned = db.query(
        "SELECT COUNT(*) n FROM companies WHERE email_pattern IS NOT NULL "
        "AND email_pattern!=''")[0]["n"]
    print(f"  contacts:         {n}")
    print(f"  with an address:  {mailed}")
    print(f"  masked-only:      {masked}")
    print(f"  firms w/ pattern: {patterned}")
    print(f"  credits spent:    {db.credits_spent():g}")


def main():
    p = argparse.ArgumentParser(
        description="Feed the database across all sources.")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would happen, spend nothing")
    p.add_argument("--tier", default=None,
                   help="filter firms by tier (e.g. 1). email-format and hunter only.")
    p.add_argument("--company", default=None,
                   help="one exact company name to target")
    p.add_argument("--limit", type=int, default=None,
                   help="max firms to consider")
    p.add_argument("--force-refresh", action="store_true",
                   help="re-fetch even fresh cached rows")
    # Hunter
    p.add_argument("--hunter-budget", type=float, default=25.0,
                   help="credit ceiling for the Hunter run (default 25)")
    p.add_argument("--limit-emails", type=int, default=10,
                   help="Hunter: max emails per firm")
    p.add_argument("--enrich", action="store_true",
                   help="Hunter: enrich each address (extra cost)")
    p.add_argument("--only-missing", action="store_true",
                   help="only firms without a domain")
    # Prospeo
    p.add_argument("--prospeo-budget", type=float, default=25.0,
                   help="credit ceiling for the Prospeo run (default 25)")
    p.add_argument("--titles", default=None,
                   help="comma-separated job titles (default: the quant list)")
    p.add_argument("--locations", default=None,
                   help="comma-separated locations (default: London,Paris)")
    p.add_argument("--pages", type=int, default=2,
                   help="Prospeo: pages of 25 results")
    p.add_argument("--reveal", action="store_true",
                   help="Prospeo: pay to unmask addresses")
    p.add_argument("--refresh-days", type=int, default=30,
                   help="days after which a cached row is considered stale")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
