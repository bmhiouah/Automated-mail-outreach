"""Core logic tests. Runs against a temporary database, never the real one.

    python3 app/test_core.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    db.init_db()
    db.load_seed(verbose=False)
    db.load_aliases()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
