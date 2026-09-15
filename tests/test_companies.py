"""Firm resolution, title tagging and the firm briefs."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

import db
import email_gen
import os
import taxonomy
import unittest


def setUpModule():
    bootstrap()

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
        _, desk = taxonomy.derive_from_title("Quantitative Analyst - Exotic Equity Derivatives")
        self.assertEqual(desk, "Equity Derivatives")

    def test_seniority(self):
        self.assertEqual(taxonomy.derive_from_title("Head of Rates Structuring")[0], "head")
        self.assertEqual(taxonomy.derive_from_title("Managing Director")[0], "MD")
        self.assertEqual(taxonomy.derive_from_title("VP, FX Options")[0], "VP")

    def test_portfolio_manager(self):
        self.assertEqual(taxonomy.derive_from_title("Systematic Macro Portfolio Manager")[0], "PM")

    def test_unknown_title(self):
        self.assertEqual(taxonomy.derive_from_title("Chief Vibes Officer"), ("C-suite", ""))


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
        with open(path, encoding="utf-8-sig") as fh:
            bad = [r["name"] for r in _csv.DictReader(fh)
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
