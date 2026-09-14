"""Discover each firm's real careers page and write it to data/careers_urls.csv.

Two strategies, in order:
  1. fetch the firm's homepage and follow the first link whose text or href looks
     like a careers/jobs page (this finds /join-us, /working-at-x, etc.)
  2. fall back to trying a list of common paths and keeping the first HTTP 200.

Nothing is guessed blindly: a URL is only written if the server actually answered
200 for it. Firms where nothing resolves are reported, not invented.

    python3 app/discover_careers.py            # tier 1 only
    python3 app/discover_careers.py --tier 1,2
    python3 app/discover_careers.py --limit 5  # smoke test
"""
import argparse
import csv
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

OUT = os.path.join(db.BASE, "data", "careers_urls.csv")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 12

# Context that does not verify certificates: many bank sites have chain issues
# that have nothing to do with whether the page exists.
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

COMMON_PATHS = [
    "/careers", "/careers/", "/jobs", "/jobs/", "/en/careers", "/en/careers/",
    "/about/careers", "/company/careers", "/careers/students",
    "/join-us", "/joinus", "/work-with-us", "/working-here", "/opportunities",
    "/en/about-us/careers", "/about-us/careers", "/careers/home",
]

LINK_HINTS = ("career", "jobs", "join-us", "joinus", "join_us", "work-with-us",
              "working-at", "working-here", "opportunities", "vacancies",
              "graduates", "students", "recruit", "hiring")
BAD_HINTS = ("privacy", "cookie", "terms", "legal", "contact", "login", "signin",
             "linkedin.com", "twitter", "facebook", "instagram", "youtube",
             "glassdoor", "wikipedia", "newsroom", "investor", "sustainability")

# A URL is only accepted if its *path* contains one of these. The hint list above
# is deliberately generous (it matches link text too), but a generous match alone
# produced wrong answers: Bank of America resolved to /student-banking/, BlackRock
# to /corporate/home, Morgan Stanley to /people. Requiring the keyword in the URL
# itself is what makes the result trustworthy.
STRONG_HINTS = ("career", "job", "join", "work-with", "working-at", "working-here",
                "workingat", "opportunit", "vacanc", "recruit", "hiring",
                "graduate", "placement")
# short segments that only count as a whole path element
SEGMENT_HINTS = ("hr", "jobs", "join", "careers", "talent", "people-careers")


# Applicant tracking systems: the whole host *is* a job board, so the path
# carries no keyword (e.g. job-boards.greenhouse.io/exoduspoint). Several funds
# only advertise roles here, so these must be accepted.
ATS_HOSTS = ("greenhouse.io", "lever.co", "myworkdayjobs.com", "workday.com",
             "smartrecruiters.com", "ashbyhq.com", "icims.com", "taleo.net",
             "successfactors.com", "jobvite.com", "recruitee.com", "teamtailor.com")


def _path_ok(url):
    """True if the URL looks like a careers page."""
    p = urllib.parse.urlparse(url)
    host = p.netloc.lower()
    if any(a in host for a in ATS_HOSTS):
        return True
    path = p.path.lower()
    if any(h in path for h in STRONG_HINTS):
        return True
    segments = [s for s in path.split("/") if s]
    return any(s in SEGMENT_HINTS for s in segments)


def _tidy(url):
    """Drop default ports and fragments; keep a trailing path."""
    p = urllib.parse.urlparse(url)
    netloc = p.netloc.replace(":443", "").replace(":80", "")
    return urllib.parse.urlunparse((p.scheme, netloc, p.path or "/", "", p.query, ""))


def fetch(url, method="GET"):
    """Return (status, body, final_url). Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-GB,en;q=0.9"})
    req.get_method = lambda: method
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            raw = b"" if method == "HEAD" else r.read(400_000)
            return r.status, raw, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, b"", url
    except Exception:
        return 0, b"", url


def candidates_from_homepage(home):
    """Pull plausible careers links out of the homepage HTML."""
    status, body, final = fetch(home)
    if status != 200 or not body:
        return []
    html = body.decode("utf-8", "ignore")
    found = []
    for href, text in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                                 html, re.S | re.I):
        blob = (href + " " + re.sub(r"<[^>]+>", " ", text)).lower()
        if any(b in blob for b in BAD_HINTS):
            continue
        if not any(h in blob for h in LINK_HINTS):
            continue
        absolute = urllib.parse.urljoin(final, href)
        if absolute.startswith("http") and urllib.parse.urlparse(absolute).netloc == \
                urllib.parse.urlparse(final).netloc:
            found.append(absolute.split("#")[0])
    # keep order, drop duplicates
    seen, out = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:6]


def verify(url, require_path=True):
    """Return the final URL if it answers 200 and looks like a careers page."""
    status, _, final = fetch(url)
    if status != 200:
        return None
    final = _tidy(final)
    if require_path and not _path_ok(final):
        return None
    return final


def discover(company):
    name, domain = company["name"], (company["domain"] or "").strip()
    if not domain:
        return name, None, "no domain"
    domain = re.sub(r"^https?://", "", domain).strip("/")

    tried = []
    # Some domains only serve properly with the www prefix; try both.
    bases = ["https://" + domain]
    if not domain.startswith("www."):
        bases.append("https://www." + domain)

    for base in bases:
        for url in candidates_from_homepage(base):
            tried.append(url)
            hit = verify(url)
            if hit:
                return name, hit, "from homepage link"
        for path in COMMON_PATHS:
            url = base + path
            tried.append(url)
            hit = verify(url)
            if hit:
                return name, hit, "common path"
    return name, None, f"nothing resolved ({len(tried)} tried)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="1", help="comma-separated tiers, e.g. 1,2")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    tiers = [t.strip() for t in args.tier.split(",") if t.strip()]
    db.init_db()
    db.ensure_columns("companies", ["careers_url TEXT"])
    q = ("SELECT id, name, domain, tier FROM companies WHERE domain IS NOT NULL AND domain!='' "
         f"AND tier IN ({','.join('?' * len(tiers))}) ORDER BY tier, name")
    rows = db.query(q, tiers)
    if args.limit:
        rows = rows[:args.limit]

    print(f"probing {len(rows)} firms across tier {','.join(tiers)} "
          f"({args.workers} at a time)\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name, url, note in pool.map(discover, rows):
            results.append((name, url, note))
            mark = "OK  " if url else "MISS"
            print(f"  {mark} {name:<38} {url or note}", flush=True)

    found = [(n, u) for n, u, _ in results if u]
    print(f"\nresolved {len(found)}/{len(results)}")

    if found:
        # MERGE, never overwrite. A tier-2 run must not wipe the tier-1 results,
        # and a curated row (with its http_status) must keep its status when the
        # crawler re-confirms the same URL.
        existing = {}
        if os.path.exists(OUT):
            with open(OUT, newline="", encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    if r.get("name"):
                        existing[r["name"].strip()] = (r.get("careers_url", "").strip(),
                                                       (r.get("http_status") or "").strip())
        added = updated = 0
        for name, url in found:
            old_url, old_status = existing.get(name, ("", ""))
            if old_url == url:
                existing[name] = (url, old_status or "200")
            else:
                existing[name] = (url, "200")
                if old_url:
                    updated += 1
                else:
                    added += 1
        with open(OUT, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["name", "careers_url", "http_status"])
            for name in sorted(existing):
                url, status = existing[name]
                if url:
                    w.writerow([name, url, status or "200"])
        print(f"wrote {OUT} ({added} new, {updated} updated, "
              f"{len(existing)} firms total)")

    missed = [n for n, u, _ in results if not u]
    if missed:
        print(f"\nstill unresolved ({len(missed)}): " + ", ".join(missed))


if __name__ == "__main__":
    main()
