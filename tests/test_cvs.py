"""CV variants: the base document is never overwritten, and nothing unvalidated
is ever attachable to a mail.

The two properties worth defending are both about loss. A tailoring that drops a
qualification, or that overwrites the master CV, costs a rewrite to recover -
so the design makes both impossible rather than merely discouraged.
"""
from .harness import bootstrap  # noqa: E402

import db
import llm
import unittest

from api import cvs, queue


def setUpModule():
    bootstrap()


def _stub_llm(body="TAILORED CV TEXT", fail=False):
    """Replace the two LLM doors for one test. Returns the restore callable."""
    saved = (llm.is_configured, llm.generate_cv)
    llm.is_configured = lambda: True
    llm.generate_cv = (lambda *a, **k: {"ok": False, "error": "the model is down"}
                       if fail else
                       {"ok": True, "body": body, "model": "test-model",
                        "cached": False, "tokens": 5})
    return saved


def _restore(saved):
    llm.is_configured, llm.generate_cv = saved


class CvFixture(unittest.TestCase):
    BASE = "BASE CV - university, master, python, C++, the original text."

    def setUp(self):
        db.execute("DELETE FROM mail_queue")
        db.execute("DELETE FROM cv_variants")
        db.execute("UPDATE profile SET cv_text=? WHERE id=1", [self.BASE])
        self.co = db.resolve_company("Jane Street")
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "email,email_source,source) VALUES ('Robin','Tester',?,'Jane Street',"
                   "'robin.tester@janestreet.com','hunter','cvtest')", [self.co["id"]])
        self.cid = db.query("SELECT id FROM contacts WHERE source='cvtest'")[0]["id"]

    def tearDown(self):
        db.execute("DELETE FROM cv_variants")
        db.execute("UPDATE profile SET cv_text=? WHERE id=1", [self.BASE])
        db.execute("DELETE FROM contacts WHERE source='cvtest'")


class TestTheBaseIsSafe(CvFixture):
    def test_a_proposal_leaves_the_base_untouched(self):
        saved = _stub_llm()
        try:
            cvs.api_cv_propose({"company_id": self.co["id"]})
        finally:
            _restore(saved)
        self.assertEqual(cvs.base_cv(), self.BASE)

    def test_an_edit_to_a_variant_leaves_the_base_untouched(self):
        vid = cvs.api_cv_create({"name": "manual", "body": "something else"})["id"]
        cvs.api_cv_save({"id": vid, "body": "edited again"})
        self.assertEqual(cvs.base_cv(), self.BASE)

    def test_a_failed_proposal_changes_nothing_at_all(self):
        saved = _stub_llm(fail=True)
        try:
            r = cvs.api_cv_propose({"company_id": self.co["id"]})
        finally:
            _restore(saved)
        self.assertIn("down", r["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM cv_variants")[0]["n"], 0)
        self.assertEqual(cvs.base_cv(), self.BASE)

    def test_tailoring_an_empty_base_is_refused(self):
        """Otherwise the model invents a CV out of nothing, cheerfully."""
        db.execute("UPDATE profile SET cv_text='' WHERE id=1")
        saved = _stub_llm()
        try:
            r = cvs.api_cv_propose({"company_id": self.co["id"]})
        finally:
            _restore(saved)
        self.assertIn("no base CV", r["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM cv_variants")[0]["n"], 0)


class TestValidation(CvFixture):
    def test_a_proposal_arrives_pending(self):
        saved = _stub_llm()
        try:
            r = cvs.api_cv_propose({"company_id": self.co["id"]})
        finally:
            _restore(saved)
        self.assertEqual(r["variant"]["status"], "pending")

    def test_a_pending_variant_cannot_be_attached_to_a_mail(self):
        """The rule that stops an unreviewed document going out with your name on it."""
        vid = cvs.api_cv_create({"name": "unreviewed", "body": "CV text"})["id"]
        self.assertEqual(cvs.api_cv_create({"name": "unreviewed"})["status"], "pending")
        self.assertEqual(queue._cv_text(vid), "",
                         "a pending variant must not reach the mailer")

    def test_validating_makes_it_attachable(self):
        vid = cvs.api_cv_create({"name": "reviewed", "body": "CV text"})["id"]
        self.assertEqual(queue._cv_text(vid), "")
        cvs.api_cv_review({"id": vid, "decision": "validated"})
        self.assertEqual(queue._cv_text(vid), "CV text")

    def test_a_rejected_variant_stays_unattachable(self):
        vid = cvs.api_cv_create({"name": "no", "body": "CV text"})["id"]
        cvs.api_cv_review({"id": vid, "decision": "rejected"})
        self.assertEqual(queue._cv_text(vid), "")

    def test_an_unknown_decision_is_refused(self):
        vid = cvs.api_cv_create({"name": "x", "body": "y"})["id"]
        self.assertIn("must be", cvs.api_cv_review({"id": vid, "decision": "maybe"})["error"])

    def test_saving_cannot_validate(self):
        """Otherwise a stray keystroke in the editor would approve a document."""
        vid = cvs.api_cv_create({"name": "x", "body": "y"})["id"]
        cvs.api_cv_save({"id": vid, "status": "validated"})
        self.assertEqual(db.query("SELECT status FROM cv_variants WHERE id=?",
                                  [vid])[0]["status"], "pending")

    def test_a_variant_needs_a_name(self):
        self.assertIn("name", cvs.api_cv_create({"body": "x"})["error"])

    def test_the_list_reports_where_the_base_is(self):
        r = cvs.api_cv_list({})
        self.assertTrue(r["base_present"])
        self.assertEqual(r["base_bytes"], len(self.BASE))


if __name__ == "__main__":
    unittest.main()