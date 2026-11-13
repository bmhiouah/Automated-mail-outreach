#!/usr/bin/env python3
"""Import a Prospeo dashboard person CSV into the local database.

Designed for exports like `prospeo_person_export_Centralesupelec.csv`:
  * never calls the Prospeo API (no extra credits)
  * keeps only the columns we agreed to keep
  * tags every row with a school label (default: CentraleSupélec)
  * writes Active job count/titles onto the company row

Usage (from project root):
  python3 scripts/import_prospeo_csv.py data/imports/prospeo_person_export_Centralesupelec.csv
  python3 scripts/import_prospeo_csv.py path/to/file.csv --school Dauphine
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import db                         # noqa: E402
import people_store as store      # noqa: E402


SOURCE = "prospeo_csv"


def _clean(v):
    v = (v or "").strip()
    if v.lower() in ("", "unknown", "not revealed", "n/a", "null"):
        return ""
    return v


def _int(v):
    v = _clean(v)
    if not v:
        return None
    try:
        return int(float(v.replace(",", "")))
    except ValueError:
        return None


def append_school(contact_id, school):
    """Add a school tag without duplicating; never clears other schools."""
    school = (school or "").strip()
    if not contact_id or not school:
        return
    rows = db.query("SELECT schools FROM contacts WHERE id=?", [contact_id])
    if not rows:
        return
    existing = [s.strip() for s in (rows[0]["schools"] or "").split(",") if s.strip()]
    if school in existing:
        return
    existing.append(school)
    db.execute("UPDATE contacts SET schools=?, updated_at=? WHERE id=?",
               [", ".join(existing), store.now(), contact_id])


def update_company_hiring(company, row):
    """Refresh live hiring signal on the firm (company-level, not per person)."""
    if not company:
        return
    count = _int(row.get("Active job count"))
    titles = _clean(row.get("Active job titles"))
    sets, args = [], []
    if count is not None:
        sets.append("active_job_count=?")
        args.append(count)
        sets.append("job_postings_count=?")
        args.append(count)
    if titles:
        sets.append("active_job_titles=?")
        args.append(titles)
    domain = _clean(row.get("Company domain") or row.get("Company email domain"))
    if domain and not (company.get("domain") or "").strip():
        sets.append("domain=?")
        args.append(domain.lower())
    website = _clean(row.get("Company website"))
    if website and not (company.get("website") or "").strip():
        sets.append("website=?")
        args.append(website)
    industry = _clean(row.get("Company industry"))
    if industry and not (company.get("industry") or "").strip():
        sets.append("industry=?")
        args.append(industry)
    desc = _clean(row.get("Company description"))
    if desc and not (company.get("description") or "").strip():
        sets.append("description=?")
        args.append(desc)
    founded = _int(row.get("Company founded"))
    if founded and not company.get("founded_year"):
        sets.append("founded_year=?")
        args.append(founded)
    headcount = _clean(row.get("Company employee range"))
    if headcount and not (company.get("headcount") or "").strip():
        sets.append("headcount=?")
        args.append(headcount)
    emp = _int(row.get("Company employee count"))
    if emp and not company.get("employee_count"):
        sets.append("employee_count=?")
        args.append(emp)
    pid = _clean(row.get("Prospeo Company ID"))
    if pid and not (company.get("prospeo_id") or "").strip():
        sets.append("prospeo_id=?")
        args.append(pid)
    linkedin = _clean(row.get("Company LinkedIn URL"))
    if linkedin and not (company.get("linkedin_url") or "").strip():
        sets.append("linkedin_url=?")
        args.append(linkedin)
    hq_city = _clean(row.get("Company city"))
    if hq_city and not (company.get("hq_city") or "").strip():
        sets.append("hq_city=?")
        args.append(hq_city)
    hq_country = _clean(row.get("Company country"))
    if hq_country and not (company.get("hq_country") or "").strip():
        sets.append("hq_country=?")
        args.append(hq_country)
    if not sets:
        return
    sets.append("updated_at=?")
    args.append(store.now())
    args.append(company["id"])
    db.execute(f"UPDATE companies SET {', '.join(sets)} WHERE id=?", args)


def row_to_person(row):
    """Map kept Prospeo CSV columns into the people_store person shape."""
    email = _clean(row.get("Email"))
    if email and "@" not in email:
        email = ""
    city = _clean(row.get("Person city"))
    state = _clean(row.get("Person state"))
    country = _clean(row.get("Person country"))
    title = _clean(row.get("Job title"))
    return {
        "first_name": _clean(row.get("First name")),
        "last_name": _clean(row.get("Last name")),
        "full_name": _clean(row.get("Full name")),
        "linkedin_url": _clean(row.get("Person LinkedIn URL")),
        "email": email,
        "job_title": title,
        "position_raw": title,
        "headline": _clean(row.get("Person headline")),
        "department": _clean(row.get("Job department")),
        "seniority_level": _clean(row.get("Job seniority")),
        "city": city,
        "state": state,
        "country": country,
        "country_code": _clean(row.get("Person country code")),
        "timezone": _clean(row.get("Person time zone")),
        "location_raw": ", ".join(p for p in (city, state, country) if p),
        "person_id": _clean(row.get("Prospeo Person ID")),
    }


def import_csv(path, school="CentraleSupélec"):
    db.ensure_schema()
    added = updated = unchanged = skipped = 0
    emails = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            first = _clean(row.get("First name"))
            last = _clean(row.get("Last name"))
            if not first and not last:
                skipped += 1
                continue
            co_name = _clean(row.get("Company name")) or "Unknown"
            domain = _clean(row.get("Company domain") or row.get("Company email domain"))
            company, _created = store.resolve_or_create_company(
                domain=domain.lower() if domain else None,
                name=co_name,
            )
            if not company:
                skipped += 1
                continue
            # Re-read after resolve so updates see current fields
            fres = db.query("SELECT * FROM companies WHERE id=?", [company["id"]])
            company = dict(fres[0]) if fres else dict(company)
            update_company_hiring(company, row)

            person = row_to_person(row)
            outcome, cid = store.upsert(person, company, provider=SOURCE)
            if outcome == "added":
                added += 1
            elif outcome == "updated":
                updated += 1
            elif outcome == "unchanged":
                unchanged += 1
            else:
                skipped += 1
                continue
            append_school(cid, school)
            if person.get("email"):
                emails += 1
            # Current role into job history (year/month from CSV when present)
            start_year = _int(row.get("Job start year"))
            start_month = _int(row.get("Job start month"))
            if cid and person.get("job_title"):
                store.save_job_history(cid, [{
                    "title": person["job_title"],
                    "company_name": co_name,
                    "seniority": person.get("seniority_level") or "",
                    "start_year": start_year,
                    "start_month": start_month,
                    "is_current": True,
                    "departments": [person["department"]] if person.get("department") else [],
                }], provider=SOURCE)

    return {
        "added": added, "updated": updated, "unchanged": unchanged,
        "skipped": skipped, "with_email": emails, "school": school,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path", help="Prospeo person export CSV")
    ap.add_argument("--school", default="CentraleSupélec",
                    help="School tag to append on every imported contact")
    args = ap.parse_args()
    path = args.csv_path
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    if not os.path.exists(path):
        raise SystemExit(f"file not found: {path}")
    stats = import_csv(path, school=args.school)
    print(f"imported {path}")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
