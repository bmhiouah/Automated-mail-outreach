"""Core logic tests. Runs against a temporary database, never the real one.

    python3 app/test_core.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers"))
import db  # noqa: E402
import email_gen  # noqa: E402
import people_parse  # noqa: E402
import server  # noqa: E402

_TMP = None


def setUpModule():
    global _TMP
    _TMP = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(_TMP, "test.db")
    db.SEED_PATH = os.path.join(db.BASE, "data", "companies_seed.csv")
    # Raw payloads are written next to the DB path, but the raw folder is a
    # module constant - redirect it too, or the tests litter the real data/raw.
    db.RAW_DIR = os.path.join(_TMP, "raw")
    db.init_db()
    db.load_seed(verbose=False)
    db.load_aliases()
    # The server migrates on boot; the test DB must do the same or the harvest
    # tests insert into columns that do not exist yet.
    db.ensure_schema()
    # The server seeds templates on first boot; the test DB must do the same or
    # every test that drafts an email gets {"error": "no template available"}.
    server.api_seed_templates()


class TestCompanyResolution(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(db.resolve_company("Jane Street")["name"], "Jane Street")

    def test_alias(self):
        self.assertEqual(db.resolve_company("SG")["name"], "Societe Generale")

    def test_substring_with_suffix(self):
        got = db.resolve_company("Societe Generale Corporate & Investment Banking")
        self.assertEqual(got["name"], "Societe Generale")

    def test_accents(self):
        self.assertEqual(db.resolve_company("Société Générale")["name"] if db.resolve_company("Société Générale") else None,
                         "Societe Generale")

    def test_unknown(self):
        self.assertIsNone(db.resolve_company("Definitely Not A Real Firm Ltd"))


class TestTitleTagging(unittest.TestCase):
    def test_specific_beats_generic(self):
        _, desk = server.derive_from_title("Quantitative Analyst - Exotic Equity Derivatives")
        self.assertEqual(desk, "Equity Derivatives")

    def test_seniority(self):
        self.assertEqual(server.derive_from_title("Head of Rates Structuring")[0], "head")
        self.assertEqual(server.derive_from_title("Managing Director")[0], "MD")
        self.assertEqual(server.derive_from_title("VP, FX Options")[0], "VP")

    def test_portfolio_manager(self):
        self.assertEqual(server.derive_from_title("Systematic Macro Portfolio Manager")[0], "PM")

    def test_unknown_title(self):
        self.assertEqual(server.derive_from_title("Chief Vibes Officer"), ("C-suite", ""))


class TestEmailGuessing(unittest.TestCase):
    def test_patterns(self):
        c = {"first_name": "Anna", "last_name": "Smith"}
        for pattern, expected in (("first.last", "anna.smith@x.com"),
                                  ("firstlast", "annasmith@x.com"),
                                  ("f.last", "a.smith@x.com"),
                                  ("flast", "asmith@x.com"),
                                  ("first", "anna@x.com")):
            self.assertEqual(server.guess_email(c, {"domain": "x.com", "email_pattern": pattern}),
                             expected)

    def test_no_pattern_no_guess(self):
        self.assertEqual(server.guess_email({"first_name": "A", "last_name": "B"},
                                            {"domain": "x.com", "email_pattern": ""}), "")

    def test_domain_cleanup(self):
        self.assertEqual(server._clean_domain("https://www.X.com/careers"), "x.com")


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
        r = server.infer_pattern(self.co["id"], "anna.smith@janestreet.com")
        self.assertEqual(r["pattern"], "first.last")
        self.assertGreaterEqual(r["confidence"], 0.85)

    def test_heuristic_lower_confidence(self):
        # nobody in contacts matches this local part -> shape heuristic only
        r = server.infer_pattern(self.co["id"], "z.smith@janestreet.com")
        self.assertEqual(r["pattern"], "f.last")
        self.assertLess(r["confidence"], 0.8)
        self.assertIn("shape heuristic", r["source"])

    def test_initial_matches_known_contact(self):
        # a.smith IS Anna Smith, so this is a confident exact match, not a guess
        r = server.infer_pattern(self.co["id"], "a.smith@janestreet.com")
        self.assertEqual(r["pattern"], "f.last")
        self.assertGreaterEqual(r["confidence"], 0.85)

    def test_bad_input(self):
        self.assertIn("error", server.infer_pattern(self.co["id"], "notanemail"))


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


class TestFirmBriefs(unittest.TestCase):
    def test_hook_extraction(self):
        brief = ("Amsterdam market maker. Structured graduate pipeline. "
                 "Best hook: something specific about market-making mechanics.")
        self.assertEqual(email_gen._brief_hook(brief),
                         "something specific about market-making mechanics")

    def test_hook_falls_back_to_first_sentence(self):
        self.assertEqual(email_gen._brief_hook("Just one line here. And more."),
                         "Just one line here")

    def test_hook_empty(self):
        self.assertEqual(email_gen._brief_hook(None), "")
        self.assertEqual(email_gen._brief_hook(""), "")

    def test_brief_reaches_template_context(self):
        ctx = email_gen.build_context({}, {"name": "Optiver", "research": "X. Best hook: the Paris hub."}, {})
        self.assertIn("Paris hub", ctx["company_hook"])
        self.assertEqual(ctx["company_research"], "X. Best hook: the Paris hub.")

    def test_company_hook_counts_as_a_hook(self):
        # a firm brief alone should not be flagged as "no personalisation hook"
        r = email_gen.score_email("Question about your desk",
                                  "Hi Anna,\n\nSomething specific. Are you hiring?\n\nBest",
                                  {"first_name": "Anna", "company_hook": "the Paris hub"})
        self.assertFalse(any("hook" in i for i in r["issues"]), r["issues"])

    def test_every_brief_has_a_hook_line(self):
        """A brief with no 'Best hook:' silently degrades to its first sentence,
        which is a whole paragraph of context and useless as a first line."""
        path = os.path.join(db.BASE, "data", "firm_briefs.csv")
        if not os.path.exists(path):
            self.skipTest("firm_briefs.csv not present")
        import csv as _csv
        bad = [r["name"] for r in _csv.DictReader(open(path, encoding="utf-8-sig"))
               if "best hook" not in (r.get("research") or "").lower()]
        self.assertEqual(bad, [], f"briefs missing a 'Best hook:' line: {bad}")

    def test_load_briefs_is_idempotent(self):
        path = os.path.join(db.BASE, "data", "firm_briefs.csv")
        if not os.path.exists(path):
            self.skipTest("firm_briefs.csv not present")
        first = db.load_briefs(path)
        second = db.load_briefs(path)
        self.assertEqual(second, 0, "re-loading unchanged briefs should write nothing")
        self.assertTrue(db.query("SELECT research FROM companies WHERE research IS NOT NULL LIMIT 1"))


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
        r = server.sourcing_links(co["id"])
        self.assertFalse(r["careers_is_verified"])
        self.assertIn("google.com/search", r["careers_url"])
        self.assertIn("google.com/search", r["careers_search_url"])

    def test_sourcing_prefers_a_verified_careers_url(self):
        co = db.resolve_company("Jane Street")
        db.execute("UPDATE companies SET careers_url=? WHERE id=?",
                   ["https://www.janestreet.com/join-jane-street/", co["id"]])
        r = server.sourcing_links(co["id"])
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
        rows = list(_csv.DictReader(open(path, encoding="utf-8-sig")))
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
        header = open(path, encoding="utf-8-sig").readline().strip()
        self.assertIn("http_status", header)

    def test_careers_file_accumulates_across_tiers(self):
        """Regression: the crawler used to open the CSV with 'w', so running it
        for tier 2 wiped every tier-1 result. The file must hold both."""
        path = os.path.join(db.BASE, "data", "careers_urls.csv")
        if not os.path.exists(path):
            self.skipTest("careers_urls.csv not present")
        import csv as _csv
        names = {r["name"].strip() for r in _csv.DictReader(open(path, encoding="utf-8-sig"))}
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


CV_EN = """Camille Dubois
Quantitative Analyst — Rates & Derivatives
Paris, France
camille.dubois@example.com | +33 6 12 34 56 78
linkedin.com/in/camilledubois | github.com/cdubois

SUMMARY
M2 graduate in quantitative finance with two years of front-office experience in rates
pricing and model validation.

EDUCATION
M2 Quantitative Finance — Universite Paris-Dauphine, 2022
M1 Applied Mathematics — ENS Paris-Saclay, 2021

EXPERIENCE
Quantitative Analyst, Rates Structuring — BNP Paribas, Paris
Sep 2022 - Present
  Built a C++ pricing library for callable range accruals.

SKILLS
Python, C++, SQL, MATLAB, Pandas, NumPy, Docker, Git, Bloomberg, QuantLib, kdb+

LANGUAGES
French (native), English (fluent, C1), German (intermediate, B1)

PROJECTS
Volatility surface calibration from SPX options using SVI.
"""

CV_FR = """Jean-Baptiste Moreau
jean.moreau@mail.com
+44 7700 900123

EXPERIENCE PROFESSIONNELLE
Quantitative Developer — Man Group, London
March 2023 - Present
Junior Quant Developer — Syquant Capital, Paris
September 2021 - February 2023

FORMATION
Master 2 Mathematiques Appliquees, Sorbonne Universite, 2021

COMPETENCES TECHNIQUES
Python, C++, Rust, SQL, Airflow, Docker, Kubernetes, AWS, Git

LANGUES
Francais (langue maternelle), Anglais (courant), Espagnol (notions)
"""


class TestCvParsing(unittest.TestCase):
    def test_sections_are_split(self):
        import cv_parse
        s = cv_parse.split_sections(CV_EN)
        for name in ("summary", "education", "experience", "skills", "languages", "projects"):
            self.assertTrue(any(x.strip() for x in s.get(name, [])), name)

    def test_name_is_not_the_headline(self):
        import cv_parse
        self.assertEqual(cv_parse.parse_cv(CV_EN)["fields"]["full_name"], "Camille Dubois")

    def test_headline_is_the_line_under_the_name(self):
        import cv_parse
        self.assertIn("Quantitative Analyst", cv_parse.parse_cv(CV_EN)["fields"]["headline"])

    def test_contact_details(self):
        import cv_parse
        f = cv_parse.parse_cv(CV_EN)["fields"]
        self.assertEqual(f["email"], "camille.dubois@example.com")
        self.assertIn("33", f["phone"])
        self.assertIn("linkedin.com/in/camilledubois", f["linkedin"])
        self.assertIn("github.com/cdubois", f["github"])

    def test_phone_with_single_digit_groups(self):
        """'+33 6 12 34 56 78' has a 1-digit group - a 2+ digit regex misses it."""
        import cv_parse
        self.assertEqual(cv_parse.find_phone("tel: +33 6 12 34 56 78"), "+33 6 12 34 56 78")

    def test_a_date_range_is_not_a_phone(self):
        import cv_parse
        self.assertEqual(cv_parse.find_phone("Classes preparatoires 2018-2020"), "")

    def test_education(self):
        import cv_parse
        edu = cv_parse.parse_cv(CV_EN)["fields"]["education"]
        self.assertIn("M2", edu)
        self.assertIn("Dauphine", edu)

    def test_years_exp_explicit_digits(self):
        import cv_parse
        self.assertEqual(cv_parse.find_years_exp({}, "3 years of experience in rates"), "3")

    def test_years_exp_explicit_word(self):
        """'two years of front-office experience' - spelled out, so digits alone miss it."""
        import cv_parse
        self.assertEqual(cv_parse.find_years_exp({}, "two years of experience in rates"), "2")

    def test_years_exp_from_merged_ranges(self):
        """Overlapping ranges must not be double counted."""
        import cv_parse
        body = {"experience": ["Jan 2020 - Jan 2022", "Jun 2021 - Jun 2022"]}
        got = cv_parse.find_years_exp(body, "")
        self.assertTrue(got.isdigit() or got == "<1", got)
        # 2020-01 -> 2022-06 is 29 months, not 24+12=36
        self.assertEqual(got, "2")

    def test_skills_are_recognised(self):
        import cv_parse
        sk = cv_parse.parse_cv(CV_EN)["fields"]["key_skills"]
        for tool in ("Python", "C++", "kdb+", "Bloomberg"):
            self.assertIn(tool, sk)

    def test_languages_are_normalised_to_english(self):
        """A French CV lists 'Francais / Anglais / Espagnol'; the profile feeds
        English templates, so the output has to be English."""
        import cv_parse
        langs = cv_parse.parse_cv(CV_FR)["fields"]["languages"]
        self.assertIn("French (native)", langs)
        self.assertIn("English (fluent)", langs)
        self.assertIn("Spanish (basic)", langs)

    def test_french_section_headers_are_understood(self):
        import cv_parse
        s = cv_parse.split_sections(CV_FR)
        for name in ("experience", "education", "skills", "languages"):
            self.assertTrue(any(x.strip() for x in s.get(name, [])), name)

    def test_french_cv_full_parse(self):
        import cv_parse
        f = cv_parse.parse_cv(CV_FR)["fields"]
        self.assertEqual(f["full_name"], "Jean-Baptiste Moreau")
        self.assertIn("Master 2", f["education"])
        self.assertTrue(f["years_exp"])

    def test_nothing_is_invented(self):
        """A field with no evidence must come back empty, not plausible-looking."""
        import cv_parse
        res = cv_parse.parse_cv("Somebody\nnothing useful here at all\n")
        self.assertEqual(res["fields"]["email"], "")
        self.assertEqual(res["fields"]["key_skills"], "")
        self.assertIn("full_name", res["missing"])

    def test_target_roles_is_sentence_ready(self):
        """Interpolated into 'looking for a {{my_target_roles}} role', so it must
        not be a six-item keyword dump."""
        import cv_parse
        roles = cv_parse.parse_cv(CV_EN)["fields"]["target_roles"]
        self.assertLessEqual(roles.count("/"), 1, roles)
        self.assertNotIn(",", roles, roles)
        self.assertIn("Quantitative Analyst", roles)

    def test_target_roles_does_not_repeat_the_head_noun(self):
        import cv_parse
        got = cv_parse.find_roles({}, "Quantitative Developer\nQuant Developer\n")
        self.assertEqual(got.count("Developer"), 1, got)

    def test_skills_are_capped_for_sentence_use(self):
        import cv_parse
        sk = cv_parse.parse_cv(CV_EN)["fields"]["key_skills"]
        self.assertLessEqual(len(sk.split(",")), 12)

    def test_acronyms_are_not_flagged_as_shouting(self):
        """MATLAB and SQL are correct in capitals - flagging them trains you to
        ignore the checker."""
        body = ("Hi Anna,\n\nI built pricing tools in MATLAB and SQL, plus C++ and kdb+. "
                "Are you hiring?\n\nBest")
        r = email_gen.score_email("Question about your desk", body,
                                  {"first_name": "Anna", "hook": "x"})
        self.assertFalse(any("shouting" in i for i in r["issues"]), r["issues"])

    def test_real_shouting_is_still_flagged(self):
        body = "Hi Anna,\n\nTHIS IS URGENT PLEASE RESPOND. Are you hiring?\n\nBest"
        r = email_gen.score_email("Question", body, {"first_name": "Anna", "hook": "x"})
        self.assertTrue(any("shouting" in i for i in r["issues"]), r["issues"])

    def test_short_text_is_rejected(self):
        self.assertIn("error", server.api_parse_cv({"cv_text": "hi"}))

    def test_api_reports_critical_gaps(self):
        r = server.api_parse_cv({"cv_text": "Jane Doe\njane@x.com\nI did some things.\n" * 3})
        self.assertIn("key_skills", r["critical_missing"])
        self.assertIn("confidence", r)


class TestFollowUps(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM outreach")
        db.execute("DELETE FROM contacts WHERE source='test'")
        self.co = db.resolve_company("Jane Street")
        db.execute("INSERT INTO contacts (first_name,last_name,job_title,company_id,company_name,"
                   "hook,source) VALUES ('Test','Subject','Quantitative Researcher',?,'Jane Street',"
                   "'a hook','test')", [self.co["id"]])
        self.cid = db.query("SELECT id FROM contacts WHERE source='test'")[0]["id"]
        self.oid = db.execute(
            "INSERT INTO outreach (contact_id,subject,body,status) VALUES (?,?,?,'sent')",
            [self.cid, "Quick question about the Quant team", "Hi Test, ..."])

    def tearDown(self):
        db.execute("DELETE FROM outreach")
        db.execute("DELETE FROM contacts WHERE source='test'")

    def test_followup_threads_with_re(self):
        r = server.api_draft_followup({"outreach_id": self.oid})
        self.assertEqual(r["subject"], "Re: Quick question about the Quant team")
        self.assertEqual(r["follows"], self.oid)

    def test_followup_does_not_double_prefix(self):
        db.execute("UPDATE outreach SET subject='Re: already threaded' WHERE id=?", [self.oid])
        r = server.api_draft_followup({"outreach_id": self.oid})
        self.assertEqual(r["subject"], "Re: already threaded")

    def test_followup_uses_the_followup_template(self):
        r = server.api_draft_followup({"outreach_id": self.oid})
        tpl = db.query("SELECT role_family FROM templates WHERE id=?", [r["template_id"]])[0]
        self.assertEqual(tpl["role_family"], "followup")

    def test_followup_template_asks_a_question(self):
        """A bump with no question loses 8 points and is easy to ignore."""
        r = server.api_draft_followup({"outreach_id": self.oid})
        self.assertIn("?", r["body"])
        self.assertFalse(any("no question" in i for i in r["quality"]["issues"]),
                         r["quality"]["issues"])

    def test_followup_missing_outreach(self):
        self.assertIn("error", server.api_draft_followup({"outreach_id": 999999}))

    def test_followup_without_a_contact(self):
        oid = db.execute("INSERT INTO outreach (subject,body,status) VALUES ('x','y','sent')")
        self.assertIn("error", server.api_draft_followup({"outreach_id": oid}))

    def test_sending_a_followup_clears_the_original(self):
        db.execute("UPDATE outreach SET next_followup_at=date('now','-1 day') WHERE id=?",
                   [self.oid])
        self.assertTrue(server.api_outreach({"due": ["1"]}), "should be due first")
        f = server.api_draft_followup({"outreach_id": self.oid})
        new_id = db.execute("INSERT INTO outreach (contact_id,subject,body,status) "
                            "VALUES (?,?,?,'draft')", [self.cid, f["subject"], f["body"]])
        server.api_mark_sent({"id": new_id, "followup_days": 7, "follows": self.oid})
        self.assertEqual(server.api_outreach({"due": ["1"]}), [],
                         "the original must stop being due once bumped")
        row = db.query("SELECT followup_stage, next_followup_at FROM outreach WHERE id=?",
                       [self.oid])[0]
        self.assertIsNone(row["next_followup_at"])
        self.assertEqual(row["followup_stage"], 1)

    def test_marking_sent_without_follows_leaves_others_alone(self):
        db.execute("UPDATE outreach SET next_followup_at=date('now','-1 day') WHERE id=?",
                   [self.oid])
        server.api_mark_sent({"id": self.oid, "followup_days": 7})
        row = db.query("SELECT next_followup_at FROM outreach WHERE id=?", [self.oid])[0]
        self.assertIsNotNone(row["next_followup_at"])


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
        r = server.api_import_people({
            "source": "test",
            "candidates": [{"first_name": "Marc", "last_name": "Dupont",
                            "job_title": "Quantitative Analyst",
                            "company_name": "BNP Paribas"}]})
        self.assertEqual(r["added"], 1)
        row = db.query("SELECT * FROM contacts WHERE source='test'")[0]
        self.assertEqual(row["desk"], "Quant")
        self.assertEqual(row["seniority"], "analyst")
        self.assertEqual(row["company_name"], "BNP Paribas")

    def test_a_pasted_address_is_not_marked_as_a_guess(self):
        server.api_import_people({
            "source": "test",
            "candidates": [{"first_name": "Marc", "last_name": "Dupont",
                            "job_title": "Quantitative Analyst",
                            "company_name": "BNP Paribas",
                            "email": "marc.dupont@bnpparibas.com"}]})
        row = db.query("SELECT * FROM contacts WHERE source='test'")[0]
        self.assertEqual(row["email_status"], "verified")
        self.assertEqual(row["email_source"], "pasted")

    def test_a_matching_address_teaches_the_firm_pattern(self):
        """The address matches the contact we just created, so we can learn."""
        r = server.api_import_people({
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
        r = server.api_import_people({
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
        server.api_import_people(payload)
        payload["candidates"][0]["job_title"] = "Senior Quantitative Analyst"
        r = server.api_import_people(payload)
        self.assertEqual((r["added"], r["updated"]), (0, 1))
        rows = db.query("SELECT * FROM contacts WHERE source='test'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_title"], "Senior Quantitative Analyst")

    def test_parse_endpoint_refuses_empty_input(self):
        self.assertIn("error", server.api_parse_people({"text": "  "}))


# --------------------------------------------------------------- harvesting
# These tests never touch the network. net.request_json is monkeypatched to a
# fake, so what is under test is our logic: the pattern translation, the merge
# rule that protects human-entered data, and the fetch ledger that stops us
# paying for the same Hunter call twice.
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
        self.assertEqual(anna["email_status"], "verified")   # Hunter says 'valid'
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
        self.assertEqual(tom["email_status"], "unknown")

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
                   "job_title,desk,email,email_status,city,linkedin_url,source) "
                   "VALUES ('Anna','Smith',?,?,'Head of Research','Quant',"
                   "'anna.smith@janestreet.com','verified','Paris','my-own-url','pasted')",
                   [co["id"], co["name"]])
        self._run()
        row = db.query("SELECT * FROM contacts WHERE source='pasted' AND first_name='Anna'")[0]
        self.assertEqual(row["job_title"], "Head of Research")
        self.assertEqual(row["city"], "Paris")
        self.assertEqual(row["linkedin_url"], "my-own-url")
        self.assertEqual(row["email_status"], "verified")

    def test_a_guessed_email_is_replaced_by_a_real_one(self):
        co = db.resolve_company("Jane Street")
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "email,email_status,source) VALUES ('Anna','Smith',?,?,"
                   "'wrong.guess@janestreet.com','guessed','pattern guess')",
                   [co["id"], co["name"]])
        self._run()
        row = db.query("SELECT * FROM contacts WHERE first_name='Anna' "
                       "AND company_name='Jane Street'")[0]
        self.assertEqual(row["email"], "anna.smith@janestreet.com")
        self.assertEqual(row["email_status"], "verified")

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
        self.assertIsNotNone(anna["enriched_at"])
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
        self.assertIn("hunter", rows[0]["sources"])
        self.assertIn("prospeo", rows[0]["sources"])

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

    def test_a_guessed_address_is_upgraded_but_a_verified_one_is_not(self):
        import people_store
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "email,email_status,source) VALUES ('Jane','Doe',?,?,"
                   "'jdoe@amundi.com','guessed','pattern')", [self.co["id"], self.co["name"]])
        people_store.upsert({"first_name": "Jane", "last_name": "Doe",
                             "email": "jane.doe@amundi.com",
                             "email_verification": "valid"}, self.co, "hunter")
        row = db.query("SELECT * FROM contacts WHERE company_name='Amundi'")[0]
        self.assertEqual(row["email"], "jane.doe@amundi.com")
        self.assertEqual(row["email_status"], "verified")

    def test_a_masked_address_is_not_stored(self):
        """'s****@firm.com' is not an address. Storing it would poison the column."""
        import people_store
        people_store.upsert({"first_name": "Stefano", "last_name": "Iannalfo",
                             "email": "s********@amundi.com"}, self.co, "prospeo")
        row = db.query("SELECT * FROM contacts WHERE company_name='Amundi'")[0]
        self.assertEqual(row["email"] or "", "")
        self.assertEqual(row["email_status"], "missing")

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


class TestProspeoProvider(unittest.TestCase):
    """Prospeo returns masked data until you pay to reveal it. Handle that."""

    def test_masked_email_and_phone_are_dropped(self):
        from providers import prospeo
        p = prospeo.normalise_person({
            "person": {"full_name": "Stefano Iannalfo", "first_name": "Stefano",
                       "last_name": "Iannalfo", "current_job_title": "VP",
                       "email": {"status": "VERIFIED", "email": "s********@blackrock.com"},
                       "mobile": {"status": "VERIFIED", "mobile": "+44 7477 ******"},
                       "location": {"city": "London", "country_code": "GB"}},
            "company": {"name": "BlackRock", "domain": "blackrock.com"}})
        self.assertEqual(p["email"], "")
        self.assertTrue(p["email_masked"])
        self.assertEqual(p["phone"], "")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
