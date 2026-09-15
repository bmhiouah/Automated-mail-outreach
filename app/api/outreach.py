"""Outreach: the mail log, mark-sent and the one follow-up."""

import os
import sys

from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db         # noqa: E402
import email_gen  # noqa: E402
from domain import enrich_contact, get_profile, now_iso  # noqa: E402
from taxonomy import OUTREACH_FIELDS  # noqa: E402
from api.rows import insert_row       # noqa: E402


def api_outreach(params):
    where, args = [], []
    if params.get("status"):
        where.append("o.status=?")
        args.append(params["status"][0])
    if params.get("due"):
        where.append("o.next_followup_at IS NOT NULL AND o.next_followup_at <= ?")
        args.append(date.today().isoformat())
    sql = ("SELECT o.*, c.first_name, c.last_name, c.company_name, c.email AS contact_email, "
           "c.city AS contact_city, c.desk "
           "FROM outreach o LEFT JOIN contacts c ON c.id = o.contact_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(o.next_followup_at, o.created_at) DESC"
    return db.query(sql, args)

def api_mark_sent(payload):
    oid = payload.get("id")
    days = int(payload.get("followup_days") or 7)
    follows = payload.get("follows")           # the mail this one is a follow-up to
    db.execute("UPDATE outreach SET status='sent', sent_at=COALESCE(sent_at, ?), "
               "next_followup_at=?, updated_at=? WHERE id=?",
               [now_iso(), (date.today() + timedelta(days=days)).isoformat(), now_iso(), oid])
    if follows:
        # The follow-up has gone out, so the original must stop being "due" -
        # otherwise the dashboard keeps nagging about a thread already bumped.
        db.execute("UPDATE outreach SET next_followup_at=NULL, "
                   "followup_stage=COALESCE(followup_stage,0)+1, updated_at=? WHERE id=?",
                   [now_iso(), follows])
    return {"ok": True}

def api_draft_followup(payload):
    """Build the follow-up to a mail you already sent.

    Keeps the original subject so it threads instead of starting a new
    conversation - replying in-thread is most of why a bump gets read at all.
    """
    oid = payload.get("outreach_id")
    rows = db.query("SELECT * FROM outreach WHERE id=?", [oid])
    if not rows:
        return {"error": "that outreach row no longer exists"}
    o = rows[0]
    if not o.get("contact_id"):
        return {"error": "this mail has no contact attached, so there is nothing to follow up"}
    crows = db.query("SELECT * FROM contacts WHERE id=?", [o["contact_id"]])
    if not crows:
        return {"error": "the contact for this mail was deleted"}
    contact = enrich_contact(dict(crows[0]))

    tpl = (db.query("SELECT * FROM templates WHERE active=1 AND (role_family='followup' "
                    "OR lower(name) LIKE '%follow%') ORDER BY id LIMIT 1")
           or db.query("SELECT * FROM templates WHERE active=1 ORDER BY id LIMIT 1"))
    if not tpl:
        return {"error": "no template available"}
    tpl = tpl[0]

    ctx = email_gen.build_context(contact, contact.get("company"), get_profile())
    ctx["original_subject"] = o.get("subject") or ""
    tpl_subject = email_gen.render(tpl["subject_tpl"], ctx)
    body = email_gen.render(tpl["body_tpl"], ctx)

    subject = (o.get("subject") or "").strip()
    if subject:
        subject = subject if subject.lower().startswith("re:") else "Re: " + subject
    else:
        subject = tpl_subject

    return {
        "contact_id": contact["id"],
        "contact_name": f"{contact.get('first_name','')} {contact.get('last_name','')}".strip(),
        "company": contact.get("company_name") or "",
        "template_id": tpl["id"],
        "outreach_id": oid,
        "follows": oid,
        "original_subject": o.get("subject") or "",
        "to": contact.get("email") or contact.get("email_guess") or "",
        "subject": subject,
        "body": body,
        "brief": ctx.get("company_research") or "",
        "brief_hook": ctx.get("company_hook") or "",
        "empty_fields": [],
        "quality": email_gen.score_email(subject, body, ctx),
        "flags": email_gen.quality_flags(ctx, body),
    }


ROUTES = [
    ("GET", "outreach", lambda p, rest, body: api_outreach(p)),
    ("POST", "outreach",
     lambda p, rest, body: insert_row("outreach", body, OUTREACH_FIELDS)),
    ("POST", "mark-sent", lambda p, rest, body: api_mark_sent(body)),
    ("POST", "draft-followup", lambda p, rest, body: api_draft_followup(body)),
]
