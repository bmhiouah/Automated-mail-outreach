"""The address conventions, the renderer and the quality score."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

import db
import email_gen
import email_pattern
import people_store
import unittest


def setUpModule():
    bootstrap()

class TestEmailGuessing(unittest.TestCase):
    def test_patterns(self):
        c = {"first_name": "Anna", "last_name": "Smith"}
        for pattern, expected in (("first.last", "anna.smith@x.com"),
                                  ("firstlast", "annasmith@x.com"),
                                  ("f.last", "a.smith@x.com"),
                                  ("flast", "asmith@x.com"),
                                  ("first", "anna@x.com")):
            self.assertEqual(email_pattern.guess_email(c, {"domain": "x.com", "email_pattern": pattern}),
                             expected)

    def test_no_pattern_no_guess(self):
        self.assertEqual(email_pattern.guess_email({"first_name": "A", "last_name": "B"},
                                            {"domain": "x.com", "email_pattern": ""}), "")

    def test_domain_cleanup(self):
        self.assertEqual(email_pattern._clean_domain("https://www.X.com/careers"), "x.com")


class TestPatternInference(unittest.TestCase):
    def setUp(self):
        self.co = db.resolve_company("Jane Street")
        db.execute("UPDATE companies SET email_pattern=NULL, domain='janestreet.com' WHERE id=?",
                   [self.co["id"]])
        db.execute("INSERT OR REPLACE INTO contacts (first_name,last_name,company_id,company_name)"
                   " VALUES ('Anna','Smith',?,'Jane Street')", [self.co["id"]])

    def tearDown(self):
        db.execute("DELETE FROM contacts WHERE source IS NULL OR source!='demo'")
        db.execute("DELETE FROM pattern_evidence")

    def test_exact_match(self):
        r = email_pattern.infer_pattern(self.co["id"], "anna.smith@janestreet.com")
        self.assertEqual(r["pattern"], "first.last")
        self.assertGreaterEqual(r["confidence"], 0.85)

    def test_heuristic_lower_confidence(self):
        # nobody in contacts matches this local part -> shape heuristic only
        r = email_pattern.infer_pattern(self.co["id"], "z.smith@janestreet.com")
        self.assertEqual(r["pattern"], "f.last")
        self.assertLess(r["confidence"], 0.8)
        self.assertIn("shape heuristic", r["source"])

    def test_initial_matches_known_contact(self):
        # a.smith IS Anna Smith, so this is a confident exact match, not a guess
        r = email_pattern.infer_pattern(self.co["id"], "a.smith@janestreet.com")
        self.assertEqual(r["pattern"], "f.last")
        self.assertGreaterEqual(r["confidence"], 0.85)

    def test_bad_input(self):
        self.assertIn("error", email_pattern.infer_pattern(self.co["id"], "notanemail"))


class TestEmailConvention(unittest.TestCase):
    """Learn a firm's address format once, then resolve any name for free."""

    def setUp(self):
        db.ensure_schema()
        db.execute("UPDATE companies SET email_pattern='', pattern_confidence=0, "
                   "pattern_source='', pattern_sample='' WHERE name='Amundi'")
        self.co = db.resolve_company("Amundi")

    def tearDown(self):
        db.execute("UPDATE companies SET email_pattern='', pattern_confidence=0, "
                   "pattern_source='', pattern_sample='' WHERE name='Amundi'")

    def test_one_real_address_teaches_the_whole_firm(self):
        """The user's example: firstname.lastname@amundi.com."""
        import email_pattern
        r = email_pattern.learn_from_person("Jane", "Doe", "jane.doe@amundi.com", self.co)
        self.assertTrue(r["learned"], r)
        self.assertEqual(r["pattern"], "first.last")
        self.assertGreaterEqual(r["confidence"], 0.9)

        firm = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
        self.assertEqual(firm["email_pattern"], "first.last")
        self.assertEqual(firm["pattern_sample"], "jane.doe@amundi.com")

    def test_a_later_name_is_resolved_without_any_api_call(self):
        import email_pattern
        email_pattern.learn_from_person("Jane", "Doe", "jane.doe@amundi.com", self.co)
        firm = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
        self.assertEqual(email_pattern.reconstruct("Marc", "Dupont", firm),
                         "marc.dupont@amundi.com")

    def test_other_conventions_are_recognised(self):
        import email_pattern
        for addr, expected in (("jdoe@amundi.com", "flast"),
                               ("janedoe@amundi.com", "firstlast"),
                               ("j.doe@amundi.com", "f.last"),
                               ("doe.jane@amundi.com", "last.first")):
            db.execute("UPDATE companies SET email_pattern='', pattern_confidence=0 "
                       "WHERE name='Amundi'")
            firm = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
            r = email_pattern.learn_from_person("Jane", "Doe", addr, firm)
            self.assertEqual(r["pattern"], expected, addr)

    def test_an_address_that_does_not_match_the_name_teaches_nothing(self):
        """Guessing here would silently mis-address everyone at the firm."""
        import email_pattern
        firm = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
        r = email_pattern.learn_from_person("Jane", "Doe", "jane.doe@amundi.com", firm)
        self.assertTrue(r["learned"])
        before = db.query("SELECT email_pattern FROM companies WHERE name='Amundi'")[0]
        r2 = email_pattern.learn_from_person("Jane", "Doe", "totally unrelated@amundi.com",
                                             firm)
        self.assertFalse(r2["learned"])
        after = db.query("SELECT email_pattern FROM companies WHERE name='Amundi'")[0]
        self.assertEqual(before["email_pattern"], after["email_pattern"])

    def test_a_stronger_pattern_is_never_downgraded(self):
        import email_pattern
        firm = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
        email_pattern.learn_from_person("Jane", "Doe", "jane.doe@amundi.com", firm)
        self.assertFalse(email_pattern.store(firm, "flast", 0.5, "shape heuristic"))
        self.assertEqual(
            db.query("SELECT email_pattern FROM companies WHERE name='Amundi'")[0]["email_pattern"],
            "first.last")

    def test_a_second_pattern_is_recorded_not_discarded(self):
        """A firm can use more than one convention; each goes in the evidence."""
        import email_pattern
        firm = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
        email_pattern.learn_from_person("Jane", "Doe", "jane.doe@amundi.com", firm)
        # a different, well-evidenced convention at the same firm
        email_pattern.learn_from_person("Marc", "Dupont", "mdupont@amundi.com", firm)
        pats = {r["pattern"] for r in db.query(
            "SELECT DISTINCT pattern FROM pattern_evidence WHERE company_id=?",
            [firm["id"]])}
        self.assertIn("first.last", pats)
        self.assertIn("flast", pats)
        # both halves of the address are recorded
        row = db.query("SELECT * FROM pattern_evidence WHERE company_id=? AND pattern='flast' "
                       "ORDER BY id DESC LIMIT 1", [firm["id"]])[0]
        self.assertEqual(row["pattern_before"], "flast")
        self.assertEqual(row["pattern_after"], "amundi.com")

    def test_a_masked_address_guides_which_pattern_fits(self):
        """'j********@amundi.com' fits jane.doe (9 letters), not 'jdoe' (4)."""
        import email_pattern
        firm = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
        email_pattern.learn_from_person("Jane", "Doe", "jane.doe@amundi.com", firm)
        email_pattern.store(firm, "flast", 0.6, "second office")
        cid = db.execute(
            "INSERT INTO contacts (first_name,last_name,company_id,company_name,"
            "email_masked,source) VALUES ('Jane','Doe',?,?,"
            "'j********@amundi.com','prospeo')", [firm["id"], firm["name"]])
        res = people_store.reconstruct_missing(firm["id"])
        self.assertGreaterEqual(res["from_masked"], 1)
        row = db.query("SELECT * FROM contacts WHERE id=?", [cid])[0]
        self.assertEqual(row["email"], "jane.doe@amundi.com")

    def test_a_domain_is_not_matched_on_a_loose_suffix(self):
        """'brandnewfirm.com' must not resolve to some firm ending in 'firm.com'.

        A person filed under the wrong employer is worse than a person we fail
        to file at all, so the lookup walks real dot boundaries only.
        """
        import email_pattern
        hit = email_pattern.company_for_domain("notaqrfirm.com")
        self.assertIsNone(hit, hit)

    def test_a_mail_subdomain_still_finds_its_firm(self):
        import email_pattern
        hit = email_pattern.company_for_domain("mail.bnpparibas.com")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["name"], "BNP Paribas")

    def test_no_convention_means_no_address_not_a_plausible_one(self):
        import email_pattern
        firm = db.query("SELECT * FROM companies WHERE name='Amundi'")[0]
        self.assertEqual(email_pattern.reconstruct("Jane", "Doe", firm), "")


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.ctx = {"first_name": "Anna", "hook": "she published X", "company": "Optiver"}

    def test_good_email_scores_well(self):
        body = ("Hi Anna,\n\nI read your paper on market microstructure and tried it on CAC40 names. "
                "The decay was faster than you report for US equities.\n\n"
                "Are you hiring a researcher this year?\n\nBest,\nSample")
        r = email_gen.score_email("Question about your microstructure work", body, self.ctx)
        self.assertGreaterEqual(r["score"], 85, r["issues"])

    def test_banned_phrases_penalised(self):
        body = "Hi Anna,\n\nI hope this email finds you well. I am passionate about markets and would be a great fit."
        r = email_gen.score_email("Opportunity", body, self.ctx)
        self.assertTrue(any("finds you well" in i for i in r["issues"]))
        self.assertLess(r["score"], 70)

    def test_unresolved_placeholders_penalised(self):
        body = "Hi {{first_name}},\n\nI work on {{my_key_skills}}. Are you hiring?"
        r = email_gen.score_email("Hi", body, self.ctx)
        self.assertTrue(any("unresolved" in i for i in r["issues"]))

    def test_no_question_penalised(self):
        body = "Hi Anna,\n\nI have done lots of work on volatility surfaces. I would love to talk."
        r = email_gen.score_email("Hello", body, self.ctx)
        self.assertTrue(any("no question" in i for i in r["issues"]))

    def test_missing_hook_penalised(self):
        r = email_gen.score_email("Hello", "Hi Anna, are you hiring?\n\nBest", {})
        self.assertTrue(any("hook" in i for i in r["issues"]))


class TestRendering(unittest.TestCase):
    def test_substitution(self):
        ctx = email_gen.build_context({"first_name": "Anna", "company_name": "Optiver"},
                                      {"name": "Optiver", "type": "prop_hft"},
                                      {"full_name": "Test User", "key_skills": "C++"})
        out = email_gen.render("Hi {{first_name}} at {{company}}, {{my_full_name}} {{my_key_skills}}", ctx)
        self.assertEqual(out, "Hi Anna at Optiver, Test User C++")

    def test_unknown_left_visible(self):
        self.assertEqual(email_gen.render("{{nope}}", {}), "{{nope}}")

    def test_empty_placeholders_are_detected(self):
        """A known variable that resolves to '' is invisible in the output -
        'I'm {{my_full_name}}, {{my_education}}' becomes 'I'm , '. That must be
        reported, because the scorer cannot see it in the rendered text."""
        ctx = email_gen.build_context({}, {}, {})
        got = email_gen.empty_placeholders("I'm {{my_full_name}}, {{my_education}}.", ctx)
        self.assertIn("my_full_name", got)
        self.assertIn("my_education", got)

    def test_empty_placeholders_ignores_filled_ones(self):
        ctx = email_gen.build_context({}, {}, {"full_name": "Real Name"})
        self.assertNotIn("my_full_name", email_gen.empty_placeholders("{{my_full_name}}", ctx))

    def test_empty_placeholders_deduplicates(self):
        ctx = email_gen.build_context({}, {}, {})
        got = email_gen.empty_placeholders("{{my_pitch}} and {{my_pitch}}", ctx)
        self.assertEqual(got, ["my_pitch"])


class TestMaskedReconstruction(unittest.TestCase):
    """The two ways a reconstructed address can be wrong before it is sent.

    Both were live in the data: 17 addresses were written with accents in the
    local part (undeliverable), and firm rows carrying a stale domain silently
    sent mail to a domain nobody uses.
    """

    def setUp(self):
        db.ensure_schema()
        before = db.resolve_company("Amundi")
        self.orig_domain = (db.query("SELECT domain FROM companies WHERE id=?",
                                     [before["id"]])[0] or {}).get("domain")
        db.execute("UPDATE companies SET email_pattern='', pattern_confidence=0, "
                   "pattern_source='', pattern_sample='', domain='stale.example' "
                   "WHERE name='Amundi'")
        self.co = db.resolve_company("Amundi")
        self.cid = db.execute(
            "INSERT INTO contacts (first_name,last_name,company_id,company_name,"
            "email_masked) VALUES ('Anna','Zund',?,'Amundi','a*****@amundi.com')",
            [self.co["id"]])

    def tearDown(self):
        db.execute("DELETE FROM contacts WHERE id=?", [self.cid])
        db.execute("DELETE FROM pattern_evidence WHERE company_id=?", [self.co["id"]])
        db.execute("UPDATE companies SET email_pattern='', pattern_confidence=0, "
                   "pattern_source='', pattern_sample='', domain=? WHERE id=?",
                   [self.orig_domain, self.co["id"]])

    def test_the_observed_domain_beats_the_stale_firm_row(self):
        """The masked address is what we actually saw; the row is bookkeeping."""
        db.execute("UPDATE companies SET email_pattern='first.last', "
                   "pattern_confidence=0.9 WHERE id=?", [self.co["id"]])
        email_pattern.apply_masked(self.co["id"])
        row = db.query("SELECT email FROM contacts WHERE id=?", [self.cid])[0]
        self.assertEqual(row["email"], "anna.zund@amundi.com")

    def test_an_accent_never_reaches_the_local_part(self):
        """'czund@bamfunds.com' is an address; 'czünd@...' is a bounce."""
        self.assertEqual(
            email_pattern.reconstruct("Christian", "Zünd",
                                      {"domain": "bamfunds.com",
                                       "email_pattern": "flast"}),
            "czund@bamfunds.com")
        self.assertTrue(email_pattern.reconstruct(
            "Dorothée", "Freymann",
            {"domain": "socgen.com", "email_pattern": "first.last"}).isascii())

    def test_names_without_accents_are_untouched(self):
        """Folding must be a no-op for the 99% of names that are already ASCII."""
        self.assertEqual(email_pattern._alpha("Smith"), "smith")
        self.assertEqual(email_pattern._fold("Zünd"), "zund")
