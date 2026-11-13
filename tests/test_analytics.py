"""Dashboard cockpit counters: coverage, credits, queue to-do, recent sent."""
from .harness import bootstrap

import db
import unittest

from api import analytics


def setUpModule():
    bootstrap()


class AnalyticsFixture(unittest.TestCase):
    def setUp(self):
        self._clean()

    def tearDown(self):
        self._clean()

    def _clean(self):
        db.execute("DELETE FROM contact_touchpoints WHERE contact_id IN "
                   "(SELECT id FROM contacts WHERE source='test')")
        db.execute("DELETE FROM mail_queue WHERE contact_id IN "
                   "(SELECT id FROM contacts WHERE source='test')")
        db.execute("DELETE FROM outreach WHERE contact_id IN "
                   "(SELECT id FROM contacts WHERE source='test')")
        db.execute("DELETE FROM contacts WHERE source='test'")
        db.execute("DELETE FROM companies WHERE source='test'")
        db.execute("DELETE FROM fetch_log WHERE request_key LIKE 'test-dash-%'")

    def _company(self, name, **kw):
        fields = {
            "name": name, "type": "consulting", "source": "test",
            "active_job_count": None,
        }
        fields.update(kw)
        cols = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        db.execute(f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
                   list(fields.values()))
        return db.query("SELECT id FROM companies WHERE name=? AND source='test'",
                        [name])[0]["id"]

    def _contact(self, first, last, company_id, company_name, **kw):
        fields = {
            "first_name": first, "last_name": last,
            "company_id": company_id, "company_name": company_name,
            "source": "test", "email": None, "schools": None,
        }
        fields.update(kw)
        cols = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        db.execute(f"INSERT INTO contacts ({cols}) VALUES ({placeholders})",
                   list(fields.values()))
        return db.query(
            "SELECT id FROM contacts WHERE first_name=? AND last_name=? "
            "AND source='test'", [first, last])[0]["id"]


class TestDashboardStats(AnalyticsFixture):
    def test_stats_exposes_queue_pending_sent_7d_and_prospeo_credits(self):
        cid = self._contact("Ada", "Dash", None, "Solo",
                            email="ada@example.com")
        db.execute(
            "INSERT INTO mail_queue (contact_id, to_addr, subject, body, status) "
            "VALUES (?, 'ada@example.com', 'Hi', 'Body', 'pending')", [cid])
        db.record_fetch("prospeo", "search-person", "test-dash-stats-1",
                        http_status=200, credits=1.0, ok=True)
        s = analytics.api_stats()
        self.assertGreaterEqual(s["queue_pending"], 1)
        self.assertIn("sent_7d", s)
        self.assertGreaterEqual(s["prospeo_credits"], 1.0)


class TestDashboardCoverage(AnalyticsFixture):
    def test_alumni_cs_splits_with_and_without_email(self):
        co = self._company("CS Firm A")
        self._contact("Alum", "Mail", co, "CS Firm A",
                      email="alum@cs.com", schools="CentraleSupélec")
        self._contact("Alum", "NoMail", co, "CS Firm A",
                      schools="CentraleSupélec")
        cov = analytics.api_analytics()["coverage"]
        self.assertGreaterEqual(cov["alumni_cs_total"], 2)
        self.assertGreaterEqual(cov["alumni_cs_with_email"], 1)
        self.assertGreaterEqual(cov["alumni_cs_without_email"], 1)

    def test_ai_firms_with_and_without_contacts(self):
        with_c = self._company("AI With People", type="applied_ai")
        self._company("AI Empty", type="applied_ai")
        self._contact("Pat", "Contact", with_c, "AI With People",
                      email="pat@ai.com")
        cov = analytics.api_analytics()["coverage"]
        self.assertGreaterEqual(cov["ai_firms_total"], 2)
        self.assertGreaterEqual(cov["ai_firms_with_contacts"], 1)
        self.assertGreaterEqual(cov["ai_firms_without_contacts"], 1)

    def test_never_emailed_and_hiring_no_outreach(self):
        hiring = self._company("Hiring Desk", type="consulting",
                               active_job_count=3)
        self._contact("New", "Lead", hiring, "Hiring Desk",
                      email="new@hire.com")
        cov = analytics.api_analytics()["coverage"]
        self.assertGreaterEqual(cov["never_emailed"], 1)
        self.assertGreaterEqual(cov["hiring_no_outreach"], 1)


class TestDashboardTodoAndSent(AnalyticsFixture):
    def test_todo_lists_pending_queue_rows(self):
        cid = self._contact("Queue", "Pending", None, "Firm",
                            email="q@example.com")
        db.execute(
            "INSERT INTO mail_queue (contact_id, to_addr, subject, body, status) "
            "VALUES (?, 'q@example.com', 'Referral ask', 'Hi', 'pending')",
            [cid])
        todo = analytics.api_analytics()["todo"]
        subjects = [r["subject"] for r in todo]
        self.assertIn("Referral ask", subjects)

    def test_recent_sent_comes_from_touchpoints(self):
        co = self._company("Sent Firm")
        cid = self._contact("Sent", "Person", co, "Sent Firm",
                            email="sent@firm.com")
        db.execute(
            "INSERT INTO contact_touchpoints (contact_id, company_id, channel, "
            "direction, to_addr, subject, sent_at) "
            "VALUES (?, ?, 'email', 'outbound', 'sent@firm.com', "
            "'Hello Sent', datetime('now'))", [cid, co])
        recent = analytics.api_analytics()["recent_sent"]
        self.assertTrue(any(r["subject"] == "Hello Sent" for r in recent))


class TestDashboardCredits(AnalyticsFixture):
    def test_credits_aggregate_by_provider(self):
        db.record_fetch("prospeo", "enrich-person", "test-dash-cr-1",
                        http_status=200, credits=1.0, ok=True)
        db.record_fetch("tavily", "search", "test-dash-cr-2",
                        http_status=200, credits=1.0, ok=True)
        db.record_fetch("llm", "chat", "test-dash-cr-3",
                        http_status=200, credits=0.0, ok=True, tokens=120)
        credits = {r["provider"]: r for r in analytics.api_analytics()["credits"]}
        self.assertGreaterEqual(credits["prospeo"]["credits_total"], 1.0)
        self.assertGreaterEqual(credits["tavily"]["credits_total"], 1.0)
        self.assertGreaterEqual(credits["llm"]["tokens_total"], 120)

    def test_analytics_has_no_reply_funnel(self):
        """Old reply-rate dashboard panels must not come back."""
        a = analytics.api_analytics()
        self.assertNotIn("funnel", a)
        self.assertNotIn("by_template", a)
        self.assertIn("coverage", a)
        self.assertIn("todo", a)
        self.assertIn("credits", a)
        self.assertIn("recent_sent", a)


if __name__ == "__main__":
    unittest.main()
