"""The funnel and the coverage counters."""

import os
import sys

from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                       # noqa: E402
from domain import get_profile  # noqa: E402


def api_analytics():
    """Conversion funnel and where the replies actually come from."""
    def rate(num, den):
        return round(100 * num / den, 1) if den else 0.0

    contacts = db.query("SELECT COUNT(*) n FROM contacts")[0]["n"]
    # outreach carries the workflow state now that contacts is a clean directory
    sent = db.query("SELECT COUNT(*) n FROM outreach WHERE status NOT IN "
                    "('draft','approved')")[0]["n"]
    replied = db.query("SELECT COUNT(*) n FROM outreach WHERE status IN "
                       "('replied','positive')")[0]["n"]
    positive = db.query("SELECT COUNT(*) n FROM outreach WHERE status='positive'")[0]["n"]

    funnel = [
        ("contacts found", contacts, 100.0),
        ("emails sent", sent, rate(sent, contacts)),
        ("replies", replied, rate(replied, sent)),
        ("positive", positive, rate(positive, sent)),
    ]

    by_type = db.query(
        "SELECT COALESCE(co.type,'unknown') k, COUNT(*) contacts, "
        "SUM(CASE WHEN c.email IS NOT NULL AND c.email != '' THEN 1 ELSE 0 END) emailed "
        "FROM contacts c LEFT JOIN companies co ON co.id=c.company_id GROUP BY k ORDER BY contacts DESC")
    by_tier = db.query(
        "SELECT COALESCE(co.tier,0) k, COUNT(*) contacts, "
        "SUM(CASE WHEN c.email IS NOT NULL AND c.email != '' THEN 1 ELSE 0 END) emailed "
        "FROM contacts c LEFT JOIN companies co ON co.id=c.company_id GROUP BY k ORDER BY k")
    by_template = db.query(
        "SELECT COALESCE(t.name,'no template') k, COUNT(*) sent, "
        "SUM(CASE WHEN o.status IN ('replied','positive') THEN 1 ELSE 0 END) replied "
        "FROM outreach o LEFT JOIN templates t ON t.id=o.template_id "
        "WHERE o.status NOT IN ('draft','approved') GROUP BY k ORDER BY sent DESC")
    return {
        "funnel": [{"stage": s, "n": n, "pct": p} for s, n, p in funnel],
        "by_type": [dict(r, reply_rate=rate(r["emailed"], r["contacts"])) for r in by_type],
        "by_tier": [dict(r, reply_rate=rate(r["emailed"], r["contacts"])) for r in by_tier],
        "by_template": [dict(r, reply_rate=rate(r["replied"], r["sent"])) for r in by_template],
    }

def api_stats():
    today = date.today().isoformat()
    stats = {}
    stats["companies"] = db.query("SELECT COUNT(*) n FROM companies")[0]["n"]
    stats["contacts"] = db.query("SELECT COUNT(*) n FROM contacts")[0]["n"]
    stats["companies_by_type"] = db.query(
        "SELECT type, COUNT(*) n FROM companies GROUP BY type ORDER BY n DESC")
    stats["contacts_by_status"] = db.query(
        "SELECT status, COUNT(*) n FROM outreach GROUP BY status ORDER BY n DESC")
    stats["outreach_by_status"] = db.query(
        "SELECT status, COUNT(*) n FROM outreach GROUP BY status ORDER BY n DESC")
    stats["with_email"] = db.query(
        "SELECT COUNT(*) n FROM contacts WHERE email IS NOT NULL AND email != ''")[0]["n"]
    stats["followups_due"] = db.query(
        "SELECT COUNT(*) n FROM outreach WHERE next_followup_at IS NOT NULL "
        "AND next_followup_at <= ? AND status NOT IN ('replied','positive','closed')", [today])[0]["n"]
    stats["replies"] = db.query(
        "SELECT COUNT(*) n FROM outreach WHERE status IN ('replied','positive')")[0]["n"]
    stats["sent"] = db.query("SELECT COUNT(*) n FROM outreach WHERE status NOT IN ('draft','approved')")[0]["n"]
    stats["firms_with_pattern"] = db.query(
        "SELECT COUNT(*) n FROM companies WHERE email_pattern IS NOT NULL AND email_pattern != ''")[0]["n"]
    stats["firms_with_contacts"] = db.query(
        "SELECT COUNT(*) n FROM companies WHERE id IN (SELECT company_id FROM contacts "
        "WHERE company_id IS NOT NULL)")[0]["n"]
    prof = get_profile()
    filled = sum(1 for f in ("full_name", "headline", "key_skills", "target_roles",
                             "years_exp", "projects", "pitch") if (prof.get(f) or "").strip())
    stats["profile_completeness"] = round(100 * filled / 7)
    return stats


ROUTES = [
    ("GET", "stats", lambda p, rest, body: api_stats()),
    ("GET", "analytics", lambda p, rest, body: api_analytics()),
]
