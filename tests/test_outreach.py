"""Sending, the one follow-up, and the due list."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

from api import outreach
import db
import unittest


def setUpModule():
    bootstrap()

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
        r = outreach.api_draft_followup({"outreach_id": self.oid})
        self.assertEqual(r["subject"], "Re: Quick question about the Quant team")
        self.assertEqual(r["follows"], self.oid)

    def test_followup_does_not_double_prefix(self):
        db.execute("UPDATE outreach SET subject='Re: already threaded' WHERE id=?", [self.oid])
        r = outreach.api_draft_followup({"outreach_id": self.oid})
        self.assertEqual(r["subject"], "Re: already threaded")

    def test_followup_uses_the_followup_template(self):
        r = outreach.api_draft_followup({"outreach_id": self.oid})
        tpl = db.query("SELECT role_family FROM templates WHERE id=?", [r["template_id"]])[0]
        self.assertEqual(tpl["role_family"], "followup")

    def test_followup_template_asks_a_question(self):
        """A bump with no question loses 8 points and is easy to ignore."""
        r = outreach.api_draft_followup({"outreach_id": self.oid})
        self.assertIn("?", r["body"])
        self.assertFalse(any("no question" in i for i in r["quality"]["issues"]),
                         r["quality"]["issues"])

    def test_followup_missing_outreach(self):
        self.assertIn("error", outreach.api_draft_followup({"outreach_id": 999999}))

    def test_followup_without_a_contact(self):
        oid = db.execute("INSERT INTO outreach (subject,body,status) VALUES ('x','y','sent')")
        self.assertIn("error", outreach.api_draft_followup({"outreach_id": oid}))

    def test_sending_a_followup_clears_the_original(self):
        db.execute("UPDATE outreach SET next_followup_at=date('now','-1 day') WHERE id=?",
                   [self.oid])
        self.assertTrue(outreach.api_outreach({"due": ["1"]}), "should be due first")
        f = outreach.api_draft_followup({"outreach_id": self.oid})
        new_id = db.execute("INSERT INTO outreach (contact_id,subject,body,status) "
                            "VALUES (?,?,?,'draft')", [self.cid, f["subject"], f["body"]])
        outreach.api_mark_sent({"id": new_id, "followup_days": 7, "follows": self.oid})
        self.assertEqual(outreach.api_outreach({"due": ["1"]}), [],
                         "the original must stop being due once bumped")
        row = db.query("SELECT followup_stage, next_followup_at FROM outreach WHERE id=?",
                       [self.oid])[0]
        self.assertIsNone(row["next_followup_at"])
        self.assertEqual(row["followup_stage"], 1)

    def test_marking_sent_without_follows_leaves_others_alone(self):
        db.execute("UPDATE outreach SET next_followup_at=date('now','-1 day') WHERE id=?",
                   [self.oid])
        outreach.api_mark_sent({"id": self.oid, "followup_days": 7})
        row = db.query("SELECT next_followup_at FROM outreach WHERE id=?", [self.oid])[0]
        self.assertIsNotNone(row["next_followup_at"])
