"""Careers URLs: what counts as one, and the false positives the first crawler produced."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

from api import sourcing
import db
import os
import unittest


def setUpModule():
    bootstrap()

class TestCareersUrls(unittest.TestCase):
    def test_path_ok_accepts_real_careers_paths(self):
        import discover_careers as dc
        for url in ("https://www.aqr.com/Our-Firm/Careers",
                    "https://about.amundi.com/join-us",
                    "https://www.mwam.com/join-us/",
                    "https://www.man.com/careers",
                    "https://www.jumptrading.com/hr/overview",
                    "https://job-boards.greenhouse.io/exoduspoint"):
            self.assertTrue(dc._path_ok(url), url)

    def test_path_ok_rejects_the_wrong_pages_found_earlier(self):
        """These were real false positives from the first crawler run."""
        import discover_careers as dc
        for url in ("https://www.bankofamerica.com/student-banking/",
                    "https://www.blackrock.com/corporate/home",
                    "https://www.morganstanley.com/people",
                    "https://www.exoduspoint.com/",
                    "https://www.optiver.com/link/7ece625de63542c6a8f947b55eae6913.aspx"):
            self.assertFalse(dc._path_ok(url), url)

    def test_tidy_strips_default_port(self):
        import discover_careers as dc
        self.assertEqual(dc._tidy("https://www.imc.com:443/eu/search-careers"),
                         "https://www.imc.com/eu/search-careers")

    def test_sourcing_falls_back_to_search_when_no_careers_url(self):
        co = db.resolve_company("BlackRock")
        db.execute("UPDATE companies SET careers_url='' WHERE id=?", [co["id"]])
        r = sourcing.sourcing_links(co["id"])
        self.assertFalse(r["careers_is_verified"])
        self.assertIn("google.com/search", r["careers_url"])
        self.assertIn("google.com/search", r["careers_search_url"])

    def test_sourcing_prefers_a_verified_careers_url(self):
        co = db.resolve_company("Jane Street")
        db.execute("UPDATE companies SET careers_url=? WHERE id=?",
                   ["https://www.janestreet.com/join-jane-street/", co["id"]])
        r = sourcing.sourcing_links(co["id"])
        self.assertTrue(r["careers_is_verified"])
        self.assertEqual(r["careers_url"], "https://www.janestreet.com/join-jane-street/")
        # the search link is still offered alongside it
        self.assertIn("google.com/search", r["careers_search_url"])
        db.execute("UPDATE companies SET careers_url='' WHERE id=?", [co["id"]])

    def test_careers_urls_file_is_consistent(self):
        path = os.path.join(db.BASE, "data", "careers_urls.csv")
        if not os.path.exists(path):
            self.skipTest("careers_urls.csv not present")
        import csv as _csv
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(_csv.DictReader(fh))
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(r["careers_url"].startswith("http"), r)
            # every recorded URL must have been seen answering 200 or 403
            self.assertIn(r.get("http_status") or "200", ("200", "403"), r)

    def test_careers_file_keeps_its_status_column(self):
        """Regression: a tier-2 crawl once rewrote the file as name,careers_url
        and silently dropped the http_status column."""
        path = os.path.join(db.BASE, "data", "careers_urls.csv")
        if not os.path.exists(path):
            self.skipTest("careers_urls.csv not present")
        with open(path, encoding="utf-8-sig") as fh:
            header = fh.readline().strip()
        self.assertIn("http_status", header)

    def test_careers_file_accumulates_across_tiers(self):
        """Regression: the crawler used to open the CSV with 'w', so running it
        for tier 2 wiped every tier-1 result. The file must hold both."""
        path = os.path.join(db.BASE, "data", "careers_urls.csv")
        if not os.path.exists(path):
            self.skipTest("careers_urls.csv not present")
        import csv as _csv
        with open(path, encoding="utf-8-sig") as fh:
            names = {r["name"].strip() for r in _csv.DictReader(fh)}
        for expected in ("Jane Street", "Goldman Sachs", "Optiver", "Two Sigma"):
            self.assertIn(expected, names, "tier-1 entry lost from careers_urls.csv")
        self.assertGreater(len(names), 41, "tier-2 results missing from careers_urls.csv")

    def test_load_careers_urls_reaches_the_database(self):
        path = os.path.join(db.BASE, "data", "careers_urls.csv")
        if not os.path.exists(path):
            self.skipTest("careers_urls.csv not present")
        db.load_careers_urls(path)
        js = db.resolve_company("Jane Street")
        self.assertTrue((js.get("careers_url") or "").startswith("http"))
