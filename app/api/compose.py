"""Re-scoring a mail and reading a pasted CV. Drafting itself lives in `queue.py`.

What is left here is the two things the queue does not own: grading text (the
Compose tab is gone, but re-scoring a draft you just edited is exactly the check
you want before validating) and the CV reader behind the Profile tab.

The template-drafting endpoint that used to live here was removed rather than
left behind. It rendered one of four fixed templates with `{{placeholders}}`,
which is precisely the generic mail the model exists to replace - and a queue full
of it read as "the generator is broken".
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db         # noqa: E402
import cv_parse   # noqa: E402
import email_gen  # noqa: E402
from domain import enrich_contact, get_profile  # noqa: E402


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
    ("POST", "score", lambda p, rest, body: api_score(body)),
    ("POST", "parse-cv", lambda p, rest, body: api_parse_cv(body)),
]
