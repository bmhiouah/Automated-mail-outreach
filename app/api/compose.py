"""Composing: generate from a template, re-score a hand-edited mail, propose profile fields from a CV."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db         # noqa: E402
import cv_parse   # noqa: E402
import email_gen  # noqa: E402
from domain import enrich_contact, get_profile  # noqa: E402


def api_generate(payload):
    cid = payload.get("contact_id")
    tid = payload.get("template_id")
    if not cid:
        return {"error": "contact_id required"}
    rows = db.query("SELECT * FROM contacts WHERE id=?", [cid])
    if not rows:
        return {"error": "contact not found"}
    contact = enrich_contact(dict(rows[0]))
    templates = db.query("SELECT * FROM templates WHERE id=?", [tid]) if tid else []
    if not templates:
        templates = db.query("SELECT * FROM templates WHERE active=1 ORDER BY id LIMIT 1")
    if not templates:
        return {"error": "no template available"}
    tpl = templates[0]
    ctx = email_gen.build_context(contact, contact.get("company"), get_profile())
    subject = email_gen.render(tpl["subject_tpl"], ctx)
    body = email_gen.render(tpl["body_tpl"], ctx)

    # Variables that resolved to nothing. Invisible in the output but fatal to
    # the mail ("I'm , - on ,"), so they are surfaced as the first flags.
    empty = []
    for tpl_text in (tpl["subject_tpl"], tpl["body_tpl"]):
        for k in email_gen.empty_placeholders(tpl_text, ctx):
            if k not in empty:
                empty.append(k)
    flags = email_gen.quality_flags(ctx, body)
    if empty:
        flags.insert(0, "empty fields - these placeholders rendered as nothing: "
                        + ", ".join(empty))
    return {
        "subject": subject,
        "body": body,
        "quality": email_gen.score_email(subject, body, ctx),
        "template_id": tpl["id"],
        "to": contact.get("email") or contact.get("email_guess") or "",
        "contact_name": f"{contact.get('first_name','')} {contact.get('last_name','')}".strip(),
        "company": contact.get("company_name") or "",
        "company_id": (contact.get("company") or {}).get("id"),
        "brief": ctx.get("company_research") or "",
        "brief_hook": ctx.get("company_hook") or "",
        "empty_fields": empty,
        "flags": flags,
    }

def api_score(payload):
    rows = db.query("SELECT * FROM contacts WHERE id=?", [payload.get("contact_id")]) \
        if payload.get("contact_id") else []
    contact = enrich_contact(dict(rows[0])) if rows else {}
    ctx = email_gen.build_context(contact, contact.get("company"), get_profile())
    subject = payload.get("subject") or ""
    body = payload.get("body") or ""
    result = email_gen.score_email(subject, body, ctx)
    # A hand-edited body no longer has a template to diff against, so check the
    # critical variables directly - these are the ones whose absence mangles a
    # sentence rather than merely leaving a gap.
    critical = ["my_full_name", "my_key_skills", "my_projects", "my_pitch", "first_name"]
    missing = [k for k in critical if not str(ctx.get(k, "") or "").strip()]
    if missing:
        result["issues"].insert(0, "empty fields behind this text: " + ", ".join(missing))
        result["notes"].append("fill the profile before sending - empty values are invisible.")
    return result

def api_parse_cv(payload):
    """Read a pasted CV and propose profile fields. Proposes - never saves.

    The user reviews the extraction in the UI and decides what to keep, which is
    the only honest design for something this heuristic.
    """
    text = (payload.get("cv_text") or "").strip()
    if len(text) < 40:
        return {"error": "paste more of your CV - at least a few lines"}
    result = cv_parse.parse_cv(text)
    # Tell the UI which proposals are usable and which would damage a template.
    critical = {"full_name", "key_skills", "projects", "pitch", "education"}
    result["critical_missing"] = sorted(critical & set(result["missing"]))
    result["confidence"] = round(
        100.0 * (len(result["fields"]) - len(result["missing"])) / max(1, len(result["fields"])))
    return result


ROUTES = [
    ("POST", "generate", lambda p, rest, body: api_generate(body)),
    ("POST", "score", lambda p, rest, body: api_score(body)),
    ("POST", "parse-cv", lambda p, rest, body: api_parse_cv(body)),
]
