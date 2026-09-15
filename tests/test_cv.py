"""CV parsing, English and French."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

from api import compose
import email_gen
import unittest


def setUpModule():
    bootstrap()

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
        self.assertIn("error", compose.api_parse_cv({"cv_text": "hi"}))

    def test_api_reports_critical_gaps(self):
        r = compose.api_parse_cv({"cv_text": "Jane Doe\njane@x.com\nI did some things.\n" * 3})
        self.assertIn("key_skills", r["critical_missing"])
        self.assertIn("confidence", r)
