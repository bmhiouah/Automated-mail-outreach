"""The paste reader, the import path and identity resolution."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

from api import people
import db
import people_parse
import people_store
import unittest


def setUpModule():
    bootstrap()

class TestPeopleParsing(unittest.TestCase):
    """The paste reader. Every case here was a real bug first."""

    def setUp(self):
        self.resolve = db.resolve_company

    def parse(self, text, **kw):
        return people_parse.parse_people(text, resolve=self.resolve, **kw)

    def one(self, text, **kw):
        out = self.parse(text, **kw)
        self.assertEqual(len(out["candidates"]), 1, out)
        return out["candidates"][0]

    def test_tab_separated_row(self):
        """Tabs are structural. Collapsing them to spaces loses the columns."""
        c = self.one("Marc Dupont\tQuantitative Analyst\tBNP Paribas\tmarc.dupont@bnpparibas.com")
        self.assertEqual((c["first_name"], c["last_name"]), ("Marc", "Dupont"))
        self.assertEqual(c["job_title"], "Quantitative Analyst")
        self.assertEqual(c["company_name"], "BNP Paribas")
        self.assertEqual(c["email"], "marc.dupont@bnpparibas.com")

    def test_dash_separated_keeps_title_and_firm_apart(self):
        """A title and a firm separated by an em dash must not merge."""
        c = self.one("Anaïs Lefèvre — Quantitative Developer — Marshall Wace")
        self.assertEqual(c["job_title"], "Quantitative Developer")
        self.assertEqual(c["company_name"], "Marshall Wace")

    def test_linkedin_profile_paste_is_stitched(self):
        text = ("Jane Doe · 2nd\n"
                "Quantitative Researcher at Jane Street\n"
                "Paris, Île-de-France, France\n")
        c = self.one(text)
        self.assertEqual(c["full_name"], "Jane Doe")
        self.assertEqual(c["job_title"], "Quantitative Researcher")
        self.assertEqual(c["company_name"], "Jane Street")
        self.assertEqual(c["city"], "Paris")

    def test_linkedin_chrome_is_dropped(self):
        text = ("Willem de Vries · 3rd\n"
                "Portfolio Manager at AQR Capital Management\n"
                "London, England, United Kingdom\n"
                "500+ connections\n")
        out = self.parse(text)
        self.assertEqual(len(out["candidates"]), 1, out)
        self.assertEqual(out["unparsed"], [])

    def test_noise_lines_are_dropped(self):
        out = self.parse("Experience\nEducation\n1,234 followers\n"
                         "Jan 2020 - Present · 3 yrs\nSee more\n")
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["unparsed"], [])

    def test_credentials_and_honorifics_are_stripped(self):
        c = self.one("Dr. Jane Doe, CFA, FRM · 2nd\nHead of Rates Structuring at Barclays\n")
        self.assertEqual((c["first_name"], c["last_name"]), ("Jane", "Doe"))

    def test_particles_stay_with_the_surname(self):
        c = self.one("Willem de Vries — Portfolio Manager — AQR Capital Management")
        self.assertEqual(c["first_name"], "Willem")
        self.assertEqual(c["last_name"], "de Vries")

    def test_commas_in_a_location_survive(self):
        """Rebuilding the string on commas turned Paris into a person."""
        out = self.parse("Jane Doe\nQuantitative Researcher at Jane Street\n"
                         "Paris, Île-de-France, France\n")
        self.assertEqual(out["candidates"][0]["city"], "Paris")
        self.assertEqual(len(out["candidates"]), 1)

    def test_space_separated_row(self):
        c = self.one("Tom Baker Trader Citadel")
        self.assertEqual(c["full_name"], "Tom Baker")
        self.assertEqual(c["job_title"], "Trader")
        self.assertEqual(c["company_name"], "Citadel")

    def test_email_only_lines_become_pattern_samples(self):
        out = self.parse("elena.moreau@barclays.com\nj.smith@janestreet.com\n")
        self.assertEqual(out["candidates"], [])
        self.assertEqual([s["email"] for s in out["pattern_samples"]],
                         ["elena.moreau@barclays.com", "j.smith@janestreet.com"])

    def test_names_only_use_the_default_firm(self):
        out = self.parse("Alice Martin\nBen Okafor\n", default_company="Jane Street")
        self.assertEqual(len(out["candidates"]), 2)
        for c in out["candidates"]:
            self.assertEqual(c["company_name"], "Jane Street")
            self.assertLess(c["confidence"], 0.7, "a bare name is weak evidence")

    def test_a_lone_desk_word_is_not_a_firm(self):
        """'Research' resolves to Qube Research via substring. It must not."""
        for word in ("research", "trading", "portfolio", "quantitative"):
            self.assertIsNone(people_parse.company_match(word, self.resolve), word)

    def test_a_lone_firm_name_still_resolves(self):
        for name in ("Citadel", "Optiver", "Barclays"):
            hit = people_parse.company_match(name, self.resolve)
            self.assertIsNotNone(hit, name)
            self.assertEqual(hit["name"], name)

    def test_a_title_is_not_swallowed_by_a_substring_firm(self):
        self.assertIsNone(
            people_parse.company_match("Quantitative Developer Marshall Wace", self.resolve))

    def test_duplicates_are_collapsed(self):
        out = self.parse("Jane Doe — Quantitative Researcher — Jane Street\n"
                         "Jane Doe — Quantitative Researcher — Jane Street\n")
        self.assertEqual(len(out["candidates"]), 1)

    def test_unreadable_lines_are_reported_not_guessed(self):
        out = self.parse("Barclays\n")
        self.assertEqual(out["candidates"], [])
        self.assertTrue(out["unparsed"])


class TestPasteImport(unittest.TestCase):
    """Saving reviewed candidates, and the pattern the paste teaches."""

    def setUp(self):
        db.execute("DELETE FROM contacts WHERE source='test'")
        db.execute("DELETE FROM pattern_evidence")
        db.execute("UPDATE companies SET email_pattern='', pattern_confidence=0 "
                   "WHERE name IN ('BNP Paribas','Barclays')")

    def tearDown(self):
        db.execute("DELETE FROM contacts WHERE source='test'")
        db.execute("DELETE FROM pattern_evidence")
        db.execute("UPDATE companies SET email_pattern='', pattern_confidence=0 "
                   "WHERE name IN ('BNP Paribas','Barclays')")

    def test_import_saves_the_person_and_derives_desk(self):
        r = people.api_import_people({
            "source": "test",
            "candidates": [{"first_name": "Marc", "last_name": "Dupont",
                            "job_title": "Quantitative Analyst",
                            "company_name": "BNP Paribas"}]})
        self.assertEqual(r["added"], 1)
        row = db.query("SELECT * FROM contacts WHERE source='test'")[0]
        self.assertEqual(row["desk"], "Quant")
        self.assertEqual(row["seniority"], "analyst")
        self.assertEqual(row["company_name"], "BNP Paribas")

    def test_a_pasted_address_is_kept_as_is(self):
        people.api_import_people({
            "source": "test",
            "candidates": [{"first_name": "Marc", "last_name": "Dupont",
                            "job_title": "Quantitative Analyst",
                            "company_name": "BNP Paribas",
                            "email": "marc.dupont@bnpparibas.com"}]})
        row = db.query("SELECT * FROM contacts WHERE source='test'")[0]
        self.assertEqual(row["email"], "marc.dupont@bnpparibas.com")
        self.assertEqual(row["email_source"], "pasted")

    def test_a_matching_address_teaches_the_firm_pattern(self):
        """The address matches the contact we just created, so we can learn."""
        r = people.api_import_people({
            "source": "test",
            "candidates": [{"first_name": "Marc", "last_name": "Dupont",
                            "job_title": "Quantitative Analyst",
                            "company_name": "BNP Paribas",
                            "email": "marc.dupont@bnpparibas.com"}],
            "pattern_samples": [{"email": "marc.dupont@bnpparibas.com"}]})
        learned = r["pattern_learning"]["learned"]
        self.assertEqual(len(learned), 1, r)
        self.assertEqual(learned[0]["pattern"], "first.last")
        self.assertGreaterEqual(learned[0]["confidence"], 0.85)
        co = db.query("SELECT email_pattern FROM companies WHERE name='BNP Paribas'")[0]
        self.assertEqual(co["email_pattern"], "first.last")

    def test_an_unmatched_address_does_not_teach_a_guess(self):
        """One address tells you the domain, not the convention."""
        r = people.api_import_people({
            "source": "test",
            "candidates": [{"first_name": "Tom", "last_name": "Baker",
                            "job_title": "Trader", "company_name": "Citadel"}],
            "pattern_samples": [{"email": "elena.moreau@barclays.com"}]})
        self.assertEqual(r["pattern_learning"]["learned"], [])
        self.assertTrue(r["pattern_learning"]["skipped"])
        co = db.query("SELECT email_pattern FROM companies WHERE name='Barclays'")[0]
        self.assertEqual(co["email_pattern"] or "", "")

    def test_reimport_updates_instead_of_duplicating(self):
        payload = {"source": "test",
                   "candidates": [{"first_name": "Marc", "last_name": "Dupont",
                                   "job_title": "Quantitative Analyst",
                                   "company_name": "BNP Paribas"}]}
        people.api_import_people(payload)
        payload["candidates"][0]["job_title"] = "Senior Quantitative Analyst"
        r = people.api_import_people(payload)
        self.assertEqual((r["added"], r["updated"]), (0, 1))
        rows = db.query("SELECT * FROM contacts WHERE source='test'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_title"], "Senior Quantitative Analyst")

    def test_parse_endpoint_refuses_empty_input(self):
        self.assertIn("error", people.api_parse_people({"text": "  "}))


class TestPeopleStore(unittest.TestCase):
    """Identity and merge: one human, one row, and never overwrite a human."""

    def setUp(self):
        db.ensure_schema()
        db.execute("DELETE FROM contacts WHERE company_name='Amundi'")
        db.execute("DELETE FROM person_job_history")
        self.co = db.resolve_company("Amundi")

    def tearDown(self):
        db.execute("DELETE FROM contacts WHERE company_name='Amundi'")
        db.execute("DELETE FROM person_job_history")

    def test_the_same_person_from_two_providers_is_one_row(self):
        import people_store
        people_store.upsert({"first_name": "Jane", "last_name": "Doe",
                             "linkedin_url": "https://www.linkedin.com/in/janedoe",
                             "job_title": "Quantitative Researcher"}, self.co, "hunter")
        outcome, _ = people_store.upsert(
            {"first_name": "Jane", "last_name": "Doe",
             "linkedin_url": "http://linkedin.com/in/janedoe/",
             "job_title": "Quantitative Researcher",
             "city": "London"}, self.co, "prospeo")
        self.assertEqual(outcome, "updated")
        rows = db.query("SELECT * FROM contacts WHERE company_name='Amundi'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["city"], "London")
        # Both providers' full payloads stay queryable in their own tables.
        # (The Hunter person here has no email, so there is nothing to key the
        # hunter row on; the Prospeo person has no person_id, so there is
        # nothing to key the prospeo row on. Rows with provider ids land in
        # the test_each_source_keeps_its_full_payload and
        # test_a_hunter_row_lands_in_contacts_hunter tests instead.)
        self.assertEqual(rows[0]["city"], "London")

    def test_harvested_data_never_overwrites_a_curated_field(self):
        import people_store
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "job_title,city,linkedin_url,source) VALUES "
                   "('Jane','Doe',?,?,'Head of Research','Paris','my-url','pasted')",
                   [self.co["id"], self.co["name"]])
        people_store.upsert({"first_name": "Jane", "last_name": "Doe",
                             "linkedin_url": "my-url", "job_title": "Trader",
                             "city": "London"}, self.co, "prospeo")
        row = db.query("SELECT * FROM contacts WHERE company_name='Amundi'")[0]
        self.assertEqual(row["job_title"], "Head of Research")
        self.assertEqual(row["city"], "Paris")

    def test_a_real_address_is_never_overwritten(self):
        import people_store
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "email,source) VALUES ('Jane','Doe',?,?,"
                   "'jdoe@amundi.com','pattern')", [self.co["id"], self.co["name"]])
        people_store.upsert({"first_name": "Jane", "last_name": "Doe",
                             "email": "jane.doe@amundi.com",
                             "email_verification": "valid"}, self.co, "hunter")
        row = db.query("SELECT * FROM contacts WHERE company_name='Amundi'")[0]
        self.assertEqual(row["email"], "jdoe@amundi.com")

    def test_each_source_keeps_its_full_payload(self):
        """The per-source tables hold everything the API returned - phone
        variants, masked forms, the lot - even when `contacts` keeps less."""
        people_store.upsert(
            {"first_name": "Stefano", "last_name": "Iannalfo",
             "email_masked": "s********@amundi.com",
             "email_raw": "s********@amundi.com", "email_revealed": 0,
             "email_verification": "verified",
             "email_verification_method": "bounceban",
             "email_mx_provider": "Proofpoint",
             "phone_masked": "+44 7477 ******",
             "phone_status": "verified",
             "mobile_national": "07477 ******",
             "mobile_international": "+44 7477 ******",
             "mobile_revealed": 0, "mobile_country": "United Kingdom",
             "mobile_country_code": "GB",
             "person_id": "aaaad3b9aaec9a96bbcecc54",
             "linkedin_member_id": "ACoAAA123", "current_job_key": "10686032",
             "city": "London", "country_code": "GB",
             "job_history": [{"title": "Analyst", "company_name": "DWS"}],
             "company": {"name": "Amundi", "domain": "amundi.com"}},
            self.co, "prospeo")
        row = db.query("SELECT * FROM contacts_prospeo WHERE person_id=?",
                       ["aaaad3b9aaec9a96bbcecc54"])[0]
        # the masked phone survives in every variant Prospeo gives
        self.assertEqual(row["mobile"], "+44 7477 ******")
        self.assertEqual(row["mobile_national"], "07477 ******")
        self.assertEqual(row["mobile_international"], "+44 7477 ******")
        self.assertEqual(row["email"], "s********@amundi.com")
        self.assertEqual(row["email_verification_method"], "bounceban")
        self.assertEqual(row["linkedin_member_id"], "ACoAAA123")
        self.assertIn("DWS", row["job_history"])
        # the unified contact keeps only the clean fields
        c = db.query("SELECT * FROM contacts WHERE first_name='Stefano'")[0]
        self.assertEqual(c["email"] or "", "")
        self.assertEqual(c["phone"] or "", "")
        self.assertEqual(c["source"], "prospeo")

    def test_a_hunter_row_lands_in_contacts_hunter(self):
        people_store.upsert(
            {"email": "meghan.bossone@aqr.com", "first_name": "Meghan",
             "last_name": "Bossone", "position": "Vice President",
             "position_raw": "Vice President Middle Office",
             "seniority": "executive", "department": "executive",
             "decision_maker": 1, "email_type": "personal",
             "email_sources": '[{"domain": "linkedin.com"}]',
             "confidence": 99},
            self.co, "hunter")
        row = db.query("SELECT * FROM contacts_hunter WHERE email=?",
                       ["meghan.bossone@aqr.com"])[0]
        self.assertEqual(row["decision_maker"], 1)
        self.assertEqual(row["email_type"], "personal")
        self.assertEqual(row["position_raw"], "Vice President Middle Office")
        c = db.query("SELECT * FROM contacts WHERE last_name='Bossone'")[0]
        self.assertEqual(c["source"], "hunter")

    def test_a_masked_address_is_kept_as_evidence_not_as_an_address(self):
        """'s****@firm.com' is not an address - but it is a person we hold.

        It lives in email_masked, never in `email`, and it retires as soon as
        the firm's convention lets us reconstruct a real candidate.
        """
        import people_store
        people_store.upsert({"first_name": "Stefano", "last_name": "Iannalfo",
                             "email": "s********@amundi.com"}, self.co, "prospeo")
        row = db.query("SELECT * FROM contacts WHERE company_name='Amundi'")[0]
        self.assertEqual(row["email"] or "", "")
        self.assertEqual(row["email_masked"], "s********@amundi.com")

    def test_a_masked_address_becomes_a_candidate_once_the_pattern_is_known(self):
        """The reconstruction pass: masked evidence + known pattern = guessed mail."""
        import people_store
        people_store.upsert({"first_name": "Stefano", "last_name": "Iannalfo",
                             "email": "s********@amundi.com"}, self.co, "prospeo")
        # One real address teaches the firm its convention...
        people_store.upsert({"first_name": "Jane", "last_name": "Doe",
                             "email": "jane.doe@amundi.com",
                             "email_verification": "valid"}, self.co, "hunter")
        co = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
        self.assertEqual(co["email_pattern"], "first.last")
        # ...then the reconstruction turns the masked person into a candidate.
        people_store.reconstruct_missing(co["id"])
        row = db.query("SELECT * FROM contacts WHERE first_name='Stefano'")[0]
        self.assertEqual(row["email"], "stefano.iannalfo@amundi.com")
        self.assertEqual(row["email_source"], "pattern")
        self.assertEqual(row["email_masked"], "")

    def test_career_history_is_stored_and_replaced_not_duplicated(self):
        import people_store
        outcome, cid = people_store.upsert(
            {"first_name": "Jane", "last_name": "Doe", "job_title": "Quant"}, self.co, "prospeo")
        hist = [{"title": "Analyst", "company_name": "BNP Paribas", "start_year": 2018,
                 "end_year": 2020, "is_current": False},
                {"title": "Quant", "company_name": "Amundi", "start_year": 2020,
                 "is_current": True}]
        self.assertEqual(people_store.save_job_history(cid, hist, "prospeo"), 2)
        self.assertEqual(people_store.save_job_history(cid, hist, "prospeo"), 2)
        rows = db.query("SELECT * FROM person_job_history WHERE contact_id=?", [cid])
        self.assertEqual(len(rows), 2, "re-enrichment must not duplicate roles")
        current = [r for r in rows if r["is_current"]]
        self.assertEqual(current[0]["company_name"], "Amundi")

    def test_position_raw_survives_verbatim(self):
        import people_store
        people_store.upsert({"first_name": "Jane", "last_name": "Doe",
                             "position_raw": "VP - Index Equity Portfolio Management",
                             "job_title": "VP"}, self.co, "prospeo")
        row = db.query("SELECT * FROM contacts WHERE company_name='Amundi'")[0]
        self.assertEqual(row["position_raw"], "VP - Index Equity Portfolio Management")
