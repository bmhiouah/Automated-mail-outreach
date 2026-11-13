"""Dashboard cockpit: coverage, credits, queue to-do, recent sends."""

import os
import sys

from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                       # noqa: E402
from domain import get_profile  # noqa: E402

# School tag written by scripts/import_prospeo_csv.py for the CS export.
CS_SCHOOL = "CentraleSupélec"
# Prestige AI consulting + applied AI firm types from the seed list.
AI_FIRM_TYPES = ("consulting", "applied_ai")


def _n(sql, args=None):
    return db.query(sql, args or [])[0]["n"]


def api_analytics():
    """Job-hunt cockpit panels: to-do, coverage, credits, recent sent."""
    since_30 = (date.today() - timedelta(days=30)).isoformat()

    todo = db.query(
        "SELECT q.id, q.subject, q.created_at, q.status, "
        "c.first_name, c.last_name, "
        "COALESCE(c.company_name, co.name, '') AS firm "
        "FROM mail_queue q "
        "LEFT JOIN contacts c ON c.id=q.contact_id "
        "LEFT JOIN companies co ON co.id=q.company_id "
        "WHERE q.status='pending' "
        "ORDER BY q.created_at DESC LIMIT 8")

    alumni_total = _n(
        "SELECT COUNT(*) n FROM contacts WHERE schools LIKE ?",
        [f"%{CS_SCHOOL}%"])
    alumni_with_email = _n(
        "SELECT COUNT(*) n FROM contacts WHERE schools LIKE ? "
        "AND email IS NOT NULL AND email != ''",
        [f"%{CS_SCHOOL}%"])

    ai_types = ",".join("?" * len(AI_FIRM_TYPES))
    ai_firms = _n(
        f"SELECT COUNT(*) n FROM companies WHERE type IN ({ai_types})",
        list(AI_FIRM_TYPES))
    ai_with_contacts = _n(
        f"SELECT COUNT(*) n FROM companies WHERE type IN ({ai_types}) "
        "AND id IN (SELECT company_id FROM contacts WHERE company_id IS NOT NULL)",
        list(AI_FIRM_TYPES))

    never_emailed = _n(
        "SELECT COUNT(*) n FROM contacts c "
        "WHERE c.email IS NOT NULL AND c.email != '' "
        "AND c.id NOT IN (SELECT contact_id FROM contact_touchpoints "
        "WHERE direction='outbound' AND contact_id IS NOT NULL)")

    hiring_no_outreach = _n(
        "SELECT COUNT(*) n FROM companies co "
        "WHERE COALESCE(co.active_job_count, 0) > 0 "
        "AND co.id NOT IN (SELECT company_id FROM contact_touchpoints "
        "WHERE company_id IS NOT NULL AND direction='outbound')")

    credit_rows = db.query(
        "SELECT provider, "
        "COALESCE(SUM(credits),0) credits_total, "
        "COALESCE(SUM(CASE WHEN fetched_at >= ? THEN credits ELSE 0 END),0) "
        "credits_30d, "
        "COALESCE(SUM(tokens),0) tokens_total "
        "FROM fetch_log GROUP BY provider ORDER BY credits_total DESC, "
        "tokens_total DESC",
        [since_30])

    recent_sent = db.query(
        "SELECT t.sent_at, t.subject, t.to_addr, "
        "c.first_name, c.last_name, "
        "COALESCE(c.company_name, co.name, '') AS firm, "
        "t.outreach_id "
        "FROM contact_touchpoints t "
        "LEFT JOIN contacts c ON c.id=t.contact_id "
        "LEFT JOIN companies co ON co.id=t.company_id "
        "WHERE t.direction='outbound' "
        "ORDER BY t.sent_at DESC LIMIT 10")

    return {
        "todo": todo,
        "coverage": {
            "alumni_cs_total": alumni_total,
            "alumni_cs_with_email": alumni_with_email,
            "alumni_cs_without_email": max(0, alumni_total - alumni_with_email),
            "ai_firms_total": ai_firms,
            "ai_firms_with_contacts": ai_with_contacts,
            "ai_firms_without_contacts": max(0, ai_firms - ai_with_contacts),
            "never_emailed": never_emailed,
            "hiring_no_outreach": hiring_no_outreach,
        },
        "credits": [
            {
                "provider": r["provider"],
                "credits_total": float(r["credits_total"] or 0),
                "credits_30d": float(r["credits_30d"] or 0),
                "tokens_total": int(r["tokens_total"] or 0),
            }
            for r in credit_rows
        ],
        "recent_sent": recent_sent,
    }


def api_stats():
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    stats = {}
    stats["companies"] = _n("SELECT COUNT(*) n FROM companies")
    stats["contacts"] = _n("SELECT COUNT(*) n FROM contacts")
    stats["companies_by_type"] = db.query(
        "SELECT type, COUNT(*) n FROM companies GROUP BY type ORDER BY n DESC")
    stats["contacts_by_status"] = db.query(
        "SELECT status, COUNT(*) n FROM outreach GROUP BY status ORDER BY n DESC")
    stats["outreach_by_status"] = db.query(
        "SELECT status, COUNT(*) n FROM outreach GROUP BY status ORDER BY n DESC")
    stats["with_email"] = _n(
        "SELECT COUNT(*) n FROM contacts WHERE email IS NOT NULL AND email != ''")
    # Kept for Outreach / CLI; Dashboard strip no longer shows these.
    stats["followups_due"] = _n(
        "SELECT COUNT(*) n FROM outreach WHERE next_followup_at IS NOT NULL "
        "AND next_followup_at <= ? AND status NOT IN ('replied','positive','closed')",
        [today])
    stats["replies"] = _n(
        "SELECT COUNT(*) n FROM outreach WHERE status IN ('replied','positive')")
    stats["sent"] = _n(
        "SELECT COUNT(*) n FROM outreach WHERE status NOT IN ('draft','approved')")
    stats["sent_7d"] = _n(
        "SELECT COUNT(*) n FROM contact_touchpoints "
        "WHERE direction='outbound' AND sent_at >= ?", [week_ago])
    stats["queue_pending"] = _n(
        "SELECT COUNT(*) n FROM mail_queue WHERE status='pending'")
    stats["prospeo_credits"] = float(db.credits_spent("prospeo") or 0)
    stats["firms_with_pattern"] = _n(
        "SELECT COUNT(*) n FROM companies WHERE email_pattern IS NOT NULL "
        "AND email_pattern != ''")
    stats["firms_with_contacts"] = _n(
        "SELECT COUNT(*) n FROM companies WHERE id IN "
        "(SELECT company_id FROM contacts WHERE company_id IS NOT NULL)")
    prof = get_profile()
    filled = sum(1 for f in ("full_name", "headline", "key_skills", "target_roles",
                             "years_exp", "projects", "pitch") if (prof.get(f) or "").strip())
    stats["profile_completeness"] = round(100 * filled / 7)
    return stats


ROUTES = [
    ("GET", "stats", lambda p, rest, body: api_stats()),
    ("GET", "analytics", lambda p, rest, body: api_analytics()),
]
