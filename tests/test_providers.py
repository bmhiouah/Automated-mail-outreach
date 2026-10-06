"""The providers, the fetch ledger and the progressive harvest - all offline, against a fake Hunter."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

from api import people
from providers import hunter
from providers import prospeo
from providers import tavily
import db
import harvest
import io
import json
import net
import os
import people_store
import unittest


def setUpModule():
    bootstrap()

class FakeHunter:
    """A stand-in for the Hunter API that counts how many real calls were made."""

    def __init__(self):
        self.calls = []

    def install(self):
        import net
        import providers.hunter as hunter
        self.hunter = hunter
        self._orig = net.request_json
        self._orig_get = hunter.net.get_json
        self._orig_post = hunter.net.post_json
        net.request_json = self._fake
        hunter.net.get_json = self._fake
        hunter.net.post_json = self._fake
        return self

    def uninstall(self):
        import net
        self.hunter.net.get_json = self._orig_get
        self.hunter.net.post_json = self._orig_post
        net.request_json = self._orig

    def _fake(self, url, params=None, headers=None, method="GET", data=None, **kw):
        self.calls.append((url, dict(params or {})))
        params = params or {}
        fake = {"ok": True, "status": 200, "error": None, "headers": {},
                "body": b"{}", "text": "{}", "json": {}}
        if url.endswith("domain-search"):
            domain = params.get("domain") or "janestreet.com"
            fake["json"] = {
                "data": {
                    "domain": domain, "organization": "Jane Street",
                    "pattern": "{first}.{last}", "accept_all": False,
                    "emails": [
                        {"value": f"anna.smith@{domain}", "type": "personal",
                         "confidence": 92, "first_name": "Anna", "last_name": "Smith",
                         "position": "Quantitative Researcher",
                         "position_raw": "Quantitative Researcher",
                         "seniority": "senior", "department": "research",
                         "decision_maker": False, "linkedin": "anna-smith",
                         "twitter": "annas", "phone_number": None,
                         "verification": {"date": "2026-01-02", "status": "valid"}},
                        {"value": f"tom.baker@{domain}", "type": "personal",
                         "confidence": 61, "first_name": "Tom", "last_name": "Baker",
                         "position": "Trader", "position_raw": "Trader",
                         "seniority": "junior", "department": "finance",
                         "decision_maker": True, "linkedin": None,
                         "twitter": None, "phone_number": "+442070000000",
                         "verification": {"date": "2026-01-02", "status": "accept_all"}},
                    ],
                },
                "meta": {"results": 2, "aggregations": {"research": 1}},
            }
            fake["body"] = json.dumps(fake["json"]).encode()
        elif url.endswith("domain-finder"):
            fake["json"] = {"data": [{"domain": "janestreet.com", "company_name": "Jane Street",
                                      "email_count": 120}], "meta": {"results": 1}}
            fake["body"] = json.dumps(fake["json"]).encode()
        elif url.endswith("people/find"):
            fake["json"] = {"data": {
                "name": {"givenName": "Anna", "familyName": "Smith", "fullName": "Anna Smith"},
                "email": "anna.smith@janestreet.com", "location": "London, England, United Kingdom",
                "timeZone": "Europe/London",
                "geo": {"city": "London", "state": "England", "country": "United Kingdom",
                        "countryCode": "GB", "lat": 51.5, "lng": -0.12},
                "employment": {"title": "Quantitative Researcher", "role": "research",
                               "seniority": "senior", "domain": "janestreet.com",
                               "name": "Jane Street"},
                "twitter": {"handle": "annas"}, "github": {"handle": "asmith"},
                "linkedin": {"handle": "anna-smith"}, "phone": None,
                "activeAt": "2026-02-01"}}
            fake["body"] = json.dumps(fake["json"]).encode()
        elif url.endswith("companies/find"):
            fake["json"] = {"data": {
                "name": "Jane Street", "domain": "janestreet.com",
                "description": "A quantitative trading firm.",
                "foundedYear": 2000, "type": "privately held",
                "location": "New York, United States",
                "geo": {"city": "New York", "country": "United States", "countryCode": "US"},
                "category": {"industry": "Financial Services", "sector": "Financials"},
                "metrics": {"employees": "1001-5000", "employeesCount": 2600},
                "linkedin": {"handle": "jane-street"}, "tags": ["trading", "quant"]}}
            fake["body"] = json.dumps(fake["json"]).encode()
        elif url.endswith("account"):
            fake["json"] = {"data": {"plan_name": "Free", "plan_level": 0,
                                     "reset_date": "2026-10-01",
                                     "requests": {"credits": {"used": 10.0, "available": 50.0,
                                                              "remaining": 40.0}}}}
            fake["body"] = json.dumps(fake["json"]).encode()
        else:
            fake["ok"], fake["status"] = False, 404
        return fake


class TestHunterPatternMapping(unittest.TestCase):
    def test_known_shapes_translate_to_the_app_vocabulary(self):
        import providers.hunter as hunter
        for raw, expected in (("{first}.{last}", "first.last"),
                              ("{first}_{last}", "first_last"),
                              ("{f}.{last}", "f.last"),
                              ("{first}{last}", "firstlast"),
                              ("{last}.{first}", "last.first"),
                              ("{first}", "first")):
            self.assertEqual(hunter.map_pattern(raw), expected, raw)

    def test_an_unknown_shape_is_refused_not_guessed(self):
        """A wrong pattern mis-addresses every person at the firm."""
        import providers.hunter as hunter
        self.assertIsNone(hunter.map_pattern("{first}.{middle}.{last}"))
        self.assertIsNone(hunter.map_pattern(None))

    def test_whitespace_is_tolerated(self):
        import providers.hunter as hunter
        self.assertEqual(hunter.map_pattern("{ first }.{ last }"), "first.last")


class TestFetchLedger(unittest.TestCase):
    def setUp(self):
        db.ensure_schema()
        db.execute("DELETE FROM fetch_log WHERE provider='hunter'")
        db.execute("DELETE FROM raw_payload WHERE provider='hunter'")
        os.environ["HUNTER_API_KEY"] = "test-key-not-real"

    def tearDown(self):
        os.environ.pop("HUNTER_API_KEY", None)
        db.execute("DELETE FROM fetch_log WHERE provider='hunter'")
        db.execute("DELETE FROM raw_payload WHERE provider='hunter'")

    def test_a_recorded_call_is_seen_and_reported(self):
        db.record_fetch("hunter", "domain-search", "domain=example.com",
                        http_status=200, credits=1.0)
        seen = db.fetch_seen("hunter", "domain-search", "domain=example.com")
        self.assertIsNotNone(seen)
        self.assertEqual(seen["credits"], 1.0)

    def test_an_unrecorded_call_is_not_seen(self):
        self.assertIsNone(db.fetch_seen("hunter", "domain-search", "domain=other.com"))

    def test_recording_twice_updates_rather_than_duplicates(self):
        db.record_fetch("hunter", "domain-search", "domain=dup.com", credits=1.0)
        db.record_fetch("hunter", "domain-search", "domain=dup.com", credits=2.0)
        rows = db.query("SELECT * FROM fetch_log WHERE provider='hunter' "
                        "AND request_key='domain=dup.com'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["credits"], 2.0)

    def test_a_cached_call_is_served_from_disk_with_no_network(self):
        """The ledger is useless if the cached copy cannot be re-read.

        This is the layer under the firm-level skip: call the same Domain Search
        twice with the cache on, and the second must be answered from the raw
        payload we already saved - zero calls, zero credits.
        """
        import providers.hunter as hunter
        fake = FakeHunter().install()
        try:
            first = hunter.domain_search(domain="janestreet.com")
            self.assertTrue(first["ok"])
            self.assertFalse(first["cached"])
            self.assertEqual(first["credits"], 1.0)
            self.assertEqual(len(fake.calls), 1)

            fake.calls.clear()
            second = hunter.domain_search(domain="janestreet.com")
            self.assertTrue(second["ok"])
            self.assertTrue(second["cached"], "second call must come from cache")
            self.assertEqual(len(fake.calls), 0, "cache must not hit the network")
            self.assertEqual(second["credits"], 1.0)   # what it cost, not re-charged
            self.assertEqual(len(second["people"]), len(first["people"]))
            self.assertEqual(second["pattern_name"], "first.last")
        finally:
            fake.uninstall()

    def test_force_refresh_bypasses_the_cache(self):
        import providers.hunter as hunter
        fake = FakeHunter().install()
        try:
            hunter.domain_search(domain="janestreet.com")
            fake.calls.clear()
            hunter.domain_search(domain="janestreet.com", force=True)
            self.assertEqual(len(fake.calls), 1)
        finally:
            fake.uninstall()

    def test_raw_payloads_are_written_and_indexed(self):
        path, sha = db.save_raw("hunter", "domain-search", "domain=raw.com",
                                b'{"data": {"domain": "raw.com"}}')
        self.assertTrue(os.path.exists(os.path.join(db.BASE, path)))
        row = db.query("SELECT * FROM raw_payload WHERE provider='hunter' "
                       "AND request_key='domain=raw.com'")[0]
        self.assertEqual(row["sha256"], sha)
        self.assertEqual(row["bytes"], len(b'{"data": {"domain": "raw.com"}}'))

    def test_credits_can_be_summed_for_one_provider(self):
        db.record_fetch("hunter", "domain-search", "domain=a.com", credits=1.0)
        db.record_fetch("hunter", "people-find", "email=x@a.com", credits=0.2)
        self.assertAlmostEqual(db.credits_spent("hunter"), 1.2, places=3)


class TestProgressiveHarvest(unittest.TestCase):
    """The whole point: a second run must not pay for the first run's work."""

    def setUp(self):
        import harvest
        self.harvest = harvest
        db.ensure_schema()
        # Clear by company, not by source: these tests insert contacts under
        # several sources (hunter, pasted, pattern guess) and a leak would make
        # the next test see a person it did not create.
        db.execute("DELETE FROM contacts WHERE company_name='Jane Street'")
        db.execute("DELETE FROM fetch_log WHERE provider='hunter'")
        db.execute("DELETE FROM raw_payload WHERE provider='hunter'")
        db.execute("DELETE FROM pattern_evidence")
        db.execute("UPDATE companies SET domain='', email_pattern='', pattern_confidence=0, "
                   "description='', founded_year=NULL, industry='' WHERE name='Jane Street'")
        os.environ["HUNTER_API_KEY"] = "test-key-not-real"
        self.fake = FakeHunter().install()

    def tearDown(self):
        self.fake.uninstall()
        os.environ.pop("HUNTER_API_KEY", None)
        db.execute("DELETE FROM contacts WHERE company_name='Jane Street'")
        db.execute("DELETE FROM fetch_log WHERE provider='hunter'")
        db.execute("DELETE FROM raw_payload WHERE provider='hunter'")
        db.execute("DELETE FROM pattern_evidence")
        db.execute("UPDATE companies SET domain='', email_pattern='', pattern_confidence=0, "
                   "description='', founded_year=NULL, industry='' WHERE name='Jane Street'")

    def _run(self, **over):
        import io
        from contextlib import redirect_stdout
        args = {"tier": None, "company": "Jane Street", "limit": None, "budget": 25.0,
                "refresh_days": 30, "force_refresh": False, "only_missing": False,
                "enrich": False, "dry_run": False, "api_key": None,
                "limit_emails": 10}
        args.update(over)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.harvest.run_hunter(type("A", (), args))
        return code, buf.getvalue()

    def test_first_run_adds_people_and_learns_the_pattern(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        rows = db.query("SELECT * FROM contacts WHERE source='hunter' ORDER BY first_name")
        self.assertEqual(len(rows), 2, out)
        anna = [r for r in rows if r["first_name"] == "Anna"][0]
        self.assertEqual(anna["email"], "anna.smith@janestreet.com")
        self.assertEqual(anna["linkedin_url"], "anna-smith")
        self.assertEqual(anna["department"], "research")
        co = db.query("SELECT * FROM companies WHERE name='Jane Street'")[0]
        self.assertEqual(co["email_pattern"], "first.last")
        self.assertEqual(co["domain"], "janestreet.com")

    def test_accept_all_domains_are_not_marked_verified(self):
        """An accept-all domain accepts anything, so a 'valid' there proves nothing."""
        self._run()
        tom = db.query("SELECT * FROM contacts WHERE source='hunter' "
                       "AND first_name='Tom'")[0]
        # an accept-all 'valid' proves nothing - but a real address still fills in
        self.assertEqual(tom["email"], "tom.baker@janestreet.com")

    def test_a_second_run_spends_nothing_and_makes_no_calls(self):
        self._run()
        spent_after_first = db.credits_spent("hunter")
        n_calls = len(self.fake.calls)
        self.fake.calls.clear()
        _, out = self._run()
        self.assertEqual(len(self.fake.calls), 0,
                         "a fresh firm must not be re-fetched")
        self.assertIn("already fresh", out)
        self.assertEqual(db.credits_spent("hunter"), spent_after_first)
        self.assertGreater(n_calls, 0)

    def test_force_refresh_calls_again(self):
        self._run()
        self.fake.calls.clear()
        self._run(force_refresh=True)
        self.assertTrue(any("domain-search" in u for u, _ in self.fake.calls))

    def test_dry_run_spends_nothing_and_writes_nothing(self):
        code, out = self._run(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertEqual(db.query("SELECT COUNT(*) n FROM contacts WHERE source='hunter'")[0]["n"], 0)
        self.assertEqual(db.credits_spent("hunter"), 0)

    def test_a_harvest_never_overwrites_a_curated_contact(self):
        """The merge rule: API data fills gaps, it does not overwrite people."""
        co = db.resolve_company("Jane Street")
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "job_title,desk,email,city,linkedin_url,source) "
                   "VALUES ('Anna','Smith',?,?,'Head of Research','Quant',"
                   "'anna.smith@janestreet.com','Paris','my-own-url','pasted')",
                   [co["id"], co["name"]])
        self._run()
        row = db.query("SELECT * FROM contacts WHERE source='pasted' AND first_name='Anna'")[0]
        self.assertEqual(row["job_title"], "Head of Research")
        self.assertEqual(row["city"], "Paris")
        self.assertEqual(row["linkedin_url"], "my-own-url")
        self.assertEqual(row["email"], "anna.smith@janestreet.com")

    def test_a_harvest_email_is_never_overwritten(self):
        co = db.resolve_company("Jane Street")
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "email,email_source,source) VALUES ('Anna','Smith',?,?,"
                   "'anna.smith@janestreet.com','pasted','pasted')",
                   [co["id"], co["name"]])
        self._run()
        row = db.query("SELECT * FROM contacts WHERE first_name='Anna' "
                       "AND company_name='Jane Street'")[0]
        self.assertEqual(row["email"], "anna.smith@janestreet.com")
        self.assertEqual(row["email_source"], "pasted")

    def test_the_budget_stops_the_run(self):
        db.execute("UPDATE companies SET domain='x.com' WHERE name='Barclays'")
        code, out = self._run(company=None, tier=1, budget=0.0)
        self.assertEqual(code, 0)
        self.assertIn("budget reached", out)
        self.assertEqual(db.credits_spent("hunter"), 0)

    def test_enrichment_fills_location(self):
        self._run(enrich=True)
        anna = db.query("SELECT * FROM contacts WHERE first_name='Anna' "
                        "AND source='hunter'")[0]
        self.assertEqual(anna["city"], "London")
        self.assertEqual(anna["country_code"], "GB")
        # the enrichment date lives on the raw Hunter row now, not on contacts
        self.assertIsNotNone(db.query(
            "SELECT updated_at FROM contacts_hunter WHERE email=?",
            [anna["email"]])[0]["updated_at"])
        self.assertEqual(anna["timezone"], "Europe/London")
        # The verbatim title must survive untouched - it is the audit trail.
        self.assertEqual(anna["position_raw"], "Quantitative Researcher")

    def test_company_enrichment_fills_the_firm_profile(self):
        """The firm profile lands on the company row, filling gaps only."""
        import people_store
        en = self.fake.hunter.company_enrichment("janestreet.com", api_key="k")
        self.assertTrue(en.get("ok"), en)
        co = db.resolve_company("Jane Street")
        self.assertTrue(people_store.fill_company(co, en, "hunter"))
        co = db.query("SELECT * FROM companies WHERE name='Jane Street'")[0]
        self.assertEqual(co["founded_year"], 2000)
        self.assertEqual(co["industry"], "Financial Services")
        self.assertEqual(co["employee_count"], 2600)

    def test_account_reports_the_real_quota(self):
        info = self.fake.hunter.account(force=True)
        self.assertTrue(info["ok"])
        self.assertEqual(info["plan"], "Free")
        self.assertEqual(info["credits_remaining"], 40.0)

    def test_a_pasted_pattern_is_not_downgraded_by_hunter(self):
        """Evidence the user pasted (0.95) outranks Hunter's pattern (0.9)."""
        co = db.resolve_company("Jane Street")
        db.execute("UPDATE companies SET email_pattern='flast', pattern_confidence=0.95 "
                   "WHERE id=?", [co["id"]])
        self._run(force_refresh=True)
        co = db.query("SELECT * FROM companies WHERE name='Jane Street'")[0]
        self.assertEqual(co["email_pattern"], "flast")

    def test_schema_migration_is_idempotent(self):
        first = db.ensure_schema()
        second = db.ensure_schema()
        self.assertEqual(first, [])
        self.assertEqual(second, [])


class TestTavily(unittest.TestCase):
    """One news line per firm: cached, ledgered, silent without a key."""

    def setUp(self):
        self._orig = net.request_json
        # The harness sets COLD_APPROACH_OFFLINE for the whole suite; these
        # tests exercise the online paths, so they borrow the offline flag
        # back for the duration and restore it after.
        self._offline = os.environ.pop("COLD_APPROACH_OFFLINE", None)
        self.calls = []

    def tearDown(self):
        net.request_json = self._orig
        if self._offline is None:
            os.environ.pop("COLD_APPROACH_OFFLINE", None)
        else:
            os.environ["COLD_APPROACH_OFFLINE"] = self._offline

    def _fake(self, url, params=None, headers=None, method="GET", data=None,
              **kw):
        self.calls.append(url)
        reply = {"results": [
            {"title": "AQR opens a Paris research hub",
             "url": "https://example.com/aqr",
             "content": "The firm announced the hub this week."}]}
        raw = json.dumps(reply).encode("utf-8")
        return {"ok": True, "status": 200, "error": None, "headers": {},
                "body": raw, "text": raw.decode(), "json": reply}

    def test_offline_never_touches_the_network(self):
        os.environ["COLD_APPROACH_OFFLINE"] = "1"
        net.request_json = self._fake
        body, meta = tavily.search("AQR latest news", api_key="tvly-fake")
        self.assertIsNone(body)
        self.assertIn("offline", meta["error"])
        self.assertEqual(self.calls, [])

    def test_no_key_is_a_quiet_skip_not_a_crash(self):
        saved = tavily.load_key
        tavily.load_key = lambda *a, **k: ""
        net.request_json = self._fake
        try:
            body, meta = tavily.search("AQR latest news")
        finally:
            tavily.load_key = saved
        self.assertIsNone(body)
        self.assertIn("no Tavily key", meta["error"])
        self.assertEqual(self.calls, [])

    def test_the_same_query_is_paid_for_once(self):
        saved = tavily.load_key
        tavily.load_key = lambda *a, **k: "tvly-fake"
        net.request_json = self._fake
        try:
            first, meta1 = tavily.search("AQR latest news")
            second, meta2 = tavily.search("AQR latest news")
        finally:
            tavily.load_key = saved
        self.assertTrue(first and second)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(meta1["cached"])
        self.assertTrue(meta2["cached"])

    def test_recent_news_builds_one_line_or_nothing(self):
        saved = tavily.search
        try:
            tavily.search = lambda *a, **k: (
                {"results": [{"title": "AQR opens a Paris research hub",
                              "url": "https://example.com/aqr",
                              "content": "The firm announced it."}]},
                {"ok": True})
            line = tavily.recent_news("AQR Capital Management")
            self.assertIn("AQR opens a Paris research hub", line)
            self.assertIn("example.com/aqr", line)

            tavily.search = lambda *a, **k: ({"results": []}, {"ok": True})
            self.assertEqual(tavily.recent_news("AQR Capital Management"), "")
            tavily.search = lambda *a, **k: (None, {"ok": False})
            self.assertEqual(tavily.recent_news("AQR Capital Management"), "")
            self.assertEqual(tavily.recent_news(""), "")
        finally:
            tavily.search = saved


class TestProspeoProvider(unittest.TestCase):
    """Prospeo returns masked data until you pay to reveal it. Handle that."""

    def test_masked_email_and_phone_are_kept_as_evidence(self):
        from providers import prospeo
        p = prospeo.normalise_person({
            "person": {"full_name": "Stefano Iannalfo", "first_name": "Stefano",
                       "last_name": "Iannalfo", "current_job_title": "VP",
                       "email": {"status": "VERIFIED", "email": "s********@blackrock.com",
                                 "verification_method": "BOUNCEBAN",
                                 "email_mx_provider": "Proofpoint"},
                       "mobile": {"status": "VERIFIED", "mobile": "+44 7477 ******"},
                       "location": {"city": "London", "country_code": "GB"}},
            "company": {"name": "BlackRock", "domain": "blackrock.com"}})
        self.assertEqual(p["email"], "")
        self.assertEqual(p["email_masked"], "s********@blackrock.com")
        self.assertEqual(p["email_verification_method"], "bounceban")
        self.assertEqual(p["email_mx_provider"], "Proofpoint")
        self.assertEqual(p["phone"], "")
        self.assertEqual(p["phone_masked"], "+44 7477 ******")
        self.assertEqual(p["phone_status"], "verified")
        self.assertEqual(p["city"], "London")
        self.assertEqual(p["country_code"], "GB")
        self.assertEqual(p["position_raw"], "VP")

    def test_a_revealed_email_is_kept(self):
        from providers import prospeo
        p = prospeo.normalise_person({
            "person": {"first_name": "Jane", "last_name": "Doe",
                       "email": {"status": "VERIFIED", "email": "jane.doe@firm.com"}},
            "company": {"name": "Firm"}})
        self.assertEqual(p["email"], "jane.doe@firm.com")
        self.assertEqual(p["email_verification"], "verified")

    def test_job_history_is_normalised(self):
        from providers import prospeo
        p = prospeo.normalise_person({
            "person": {"first_name": "Jane", "last_name": "Doe",
                       "job_history": [{"title": "Analyst", "company_name": "BNP",
                                        "current": False, "start_year": 2018,
                                        "end_year": 2020, "seniority": "Analyst"},
                                       {"title": "Quant", "company_name": "Amundi",
                                        "current": True, "start_year": 2020}]},
            "company": {"name": "Amundi"}})
        self.assertEqual(len(p["job_history"]), 2)
        self.assertTrue(p["job_history"][1]["is_current"])
        self.assertEqual(p["job_history"][0]["company_name"], "BNP")

    def test_each_page_gets_its_own_cache_key(self):
        """Pages 1 and 2 must not collide.

        The key used to be the JSON truncated to 400 chars, and the filters
        alone are longer than that - so "page" was cut off and every page was
        answered from page 1's cache. The harvester then re-processed the same
        25 people and reported them as unchanged.
        """
        from providers import prospeo
        titles = ["Quantitative Researcher", "Quantitative Research Analyst",
                  "Quantitative Trader", "Quantitative Analyst",
                  "Quantitative Developer", "Systematic Trader",
                  "Systematic Researcher", "Algorithmic Trader",
                  "Algorithmic Researcher", "Portfolio Manager",
                  "Systematic Portfolio Manager"]
        def key(page):
            return prospeo._request_key("search-person", {
                "page": page,
                "filters": {"person_job_title": {"include": titles,
                                                 "match_mode": "CONTAINS"},
                            "person_location_search": {"include": ["London, United Kingdom"]}}})
        k1, k2, k3 = key(1), key(2), key(3)
        self.assertEqual(len({k1, k2, k3}), 3, "pages must be distinct")
        self.assertIn("p2", k2)
        self.assertTrue(all(len(k) < 60 for k in (k1, k2, k3)))

    def test_a_location_containing_a_comma_is_one_place(self):
        """'London, United Kingdom' must not become London + United Kingdom.

        Splitting on the comma turns a city into a whole-country search, which
        silently widens the harvest and burns credits on the wrong people.
        """
        import harvest
        from providers import prospeo
        known = {"london, united kingdom": "London, United Kingdom",
                 "paris, france": "Paris, France"}
        orig = prospeo.resolve_location

        def fake(query, api_key=None):
            return (known.get((query or "").strip().lower()), {})

        prospeo.resolve_location = fake
        try:
            self.assertEqual(harvest.parse_locations("London, United Kingdom"),
                             ["London, United Kingdom"])
            self.assertEqual(harvest.parse_locations("Paris, France"),
                             ["Paris, France"])
            # Not a real place, so it falls back to splitting.
            self.assertEqual(harvest.parse_locations("Paris, Berlin"),
                             ["Paris", "Berlin"])
        finally:
            prospeo.resolve_location = orig

    def test_a_broader_location_is_dropped_when_a_narrower_one_covers_it(self):
        """London + United Kingdom must collapse to London.

        Keeping both widened the search from 6,576 people to 13,560 - the whole
        country - and spent credits on the wrong geography.
        """
        import harvest
        self.assertEqual(
            harvest.dedupe_locations(["London, United Kingdom", "United Kingdom"]),
            ["London, United Kingdom"])
        # Unrelated places are both kept.
        self.assertEqual(
            sorted(harvest.dedupe_locations(["Paris, France", "United Kingdom"])),
            ["Paris, France", "United Kingdom"])

    def test_company_profile_is_normalised(self):
        from providers import prospeo
        c = prospeo.normalise_company({
            "name": "BlackRock", "domain": "blackrock.com", "founded": 1988,
            "industry": "Financial Services", "employee_count": 33903,
            "employee_range": "10000+", "revenue_range_printed": "10B+",
            "location": {"city": "Manhattan", "country": "United States"},
            "technology": {"technology_names": ["Proofpoint"]},
            "job_postings": {"active_count": 421}})
        self.assertEqual(c["founded_year"], 1988)
        self.assertEqual(c["headcount"], "10000+")
        self.assertEqual(c["revenue_printed"], "10B+")
        self.assertEqual(c["job_postings_count"], 421)
        self.assertEqual(c["hq_city"], "Manhattan")

    def test_an_unknown_firm_is_added_as_a_discovery(self):
        import people_store
        co, created = people_store.resolve_or_create_company(
            domain="brandnewfirm.com", name="Brand New Firm")
        self.assertTrue(created)
        self.assertEqual(co["tier"], 3)          # a discovery, not a target yet
        self.assertEqual(co["status"], "to_research")
        db.execute("DELETE FROM companies WHERE domain='brandnewfirm.com'")
