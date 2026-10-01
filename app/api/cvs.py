"""CV variants: one base document, many adaptations, all reviewed by you.

The base CV lives in `profile.cv_text` and this module never writes to it. That
is the whole design. A tailoring that quietly drops a Master's, or that arrives
having invented a Python library you have never used, must cost a click to throw
away - not a rewrite to recover. So:

  * `llm.generate_cv` proposes; it cannot save.
  * A proposal lands as `status='pending'` and is not attachable to a queued mail
    until you mark it `validated`. (`queue._cv_text` refuses anything else.)
  * `parent_id` records what it was derived from, so "back to the base" is a
    lookup rather than a reconstruction.

The variants are validated exactly the way mails are: one row, one decision.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                       # noqa: E402
import llm                      # noqa: E402
from domain import get_profile, now_iso   # noqa: E402

# What a client may set by hand. `status` is not here on purpose: validation is a
# decision the user makes through api_cv_review, not a field the UI can flip while
# saving an edit.
CV_FIELDS = ["name", "body", "parent_id", "company_id", "contact_id", "role_target",
             "notes"]


def base_cv():
    """The master document. Read-only as far as this module is concerned."""
    return get_profile().get("cv_text") or ""


def _company_for(contact_id, company_id):
    """Resolve the firm + person a variant is written for, whichever was given."""
    if company_id:
        rows = db.query("SELECT * FROM companies WHERE id=?", [company_id])
        if rows:
            return dict(rows[0]), {}
    if not contact_id:
        return {}, {}
    rows = db.query("SELECT * FROM contacts WHERE id=?", [contact_id])
    if not rows:
        return {}, {}
    c = dict(rows[0])
    if c.get("company_id"):
        co = db.query("SELECT * FROM companies WHERE id=?", [c["company_id"]])
        if co:
            return dict(co[0]), c
    return {}, c


def _variant_name(company, role):
    """A usable default name, because a blank label makes the picker unusable."""
    parts = [p for p in [(company or {}).get("name"), role or "tailored CV"] if p]
    return " - ".join(parts[:2])


def api_cv_list(params):
    """Every variant with its lineage, so the tree is visible at a glance."""
    rows = db.query(
        "SELECT v.id, v.name, v.status, v.generation, v.role_target, v.edited, "
        "v.company_id, v.contact_id, v.parent_id, v.created_at, v.reviewed_at, "
        "co.name AS firm, c.first_name, c.last_name, "
        "length(v.body) AS bytes, "
        "(SELECT COUNT(*) FROM mail_queue q WHERE q.cv_id=v.id) AS used_by "
        "FROM cv_variants v "
        "LEFT JOIN companies co ON co.id=v.company_id "
        "LEFT JOIN contacts c ON c.id=v.contact_id "
        "ORDER BY v.status='pending' DESC, v.created_at DESC")
    return {"variants": rows, "base_bytes": len(base_cv()),
            "base_present": bool(base_cv())}


def api_cv_get(payload):
    rows = db.query("SELECT * FROM cv_variants WHERE id=?", [payload.get("id")])
    if not rows:
        return {"error": "that CV variant no longer exists"}
    v = dict(rows[0])
    if v.get("company_id"):
        co = db.query("SELECT name FROM companies WHERE id=?", [v["company_id"]])
        v["firm"] = co[0]["name"] if co else ""
    return v


def api_cv_propose(payload):
    """Ask the model to tailor the base CV. Proposes; approves nothing.

    The variant is saved as `pending` so the text is there to read and edit, but
    it cannot be attached to a mail until it is validated. Refuses when there is
    no base CV: tailoring an empty document produces confident filler.
    """
    if not llm.is_configured():
        return {"error": llm.status().get("reason") or "the LLM is not configured",
                "llm": llm.status()}
    source = base_cv()
    if not source.strip():
        return {"error": "there is no base CV yet - paste it in the My profile tab "
                         "first. Tailoring an empty document produces filler, not a CV."}
    company, contact = _company_for(payload.get("contact_id"), payload.get("company_id"))
    role = payload.get("role_target") or contact.get("job_title") or ""
    out = llm.generate_cv(contact, company, get_profile(), source,
                          role_target=role, force=bool(payload.get("force")))
    if not out.get("ok"):
        return {"error": out.get("error") or "the tailoring failed", "llm": out}
    name = (payload.get("name") or _variant_name(company, role)).strip()
    new_id = db.execute(
        "INSERT INTO cv_variants (name, body, parent_id, company_id, contact_id, "
        "role_target, generation, status, updated_at) VALUES (?,?,?,?,?,?,?,'pending',?)",
        [name, out["body"], payload.get("parent_id"), company.get("id"),
         contact.get("id"), role, f"llm:{out.get('model') or ''}", now_iso()])
    return {"variant": db.query("SELECT * FROM cv_variants WHERE id=?", [new_id])[0],
            "cached": bool(out.get("cached")), "tokens": out.get("tokens") or 0,
            "note": "proposed, not applied - review it, then Validate to allow it on a mail"}


def api_cv_save(payload):
    """Write your own edits to a variant. Marks it edited, not validated."""
    vid = payload.get("id")
    rows = db.query("SELECT * FROM cv_variants WHERE id=?", [vid])
    if not rows:
        return {"error": "that CV variant no longer exists"}
    sets, args = [], []
    for f in CV_FIELDS:
        if f in payload:
            sets.append(f"{f}=?")
            args.append(payload[f])
    if not sets:
        return {"error": "nothing to update"}
    sets.append("edited=1")
    sets.append("updated_at=?")
    args.extend([now_iso(), vid])
    db.execute(f"UPDATE cv_variants SET {', '.join(sets)} WHERE id=?", args)
    return api_cv_get({"id": vid})


def api_cv_review(payload):
    """Accept or reject a variant. Your decision, recorded with a timestamp."""
    vid = payload.get("id")
    decision = payload.get("decision")        # validated | rejected
    rows = db.query("SELECT * FROM cv_variants WHERE id=?", [vid])
    if not rows:
        return {"error": "that CV variant no longer exists"}
    if decision not in ("validated", "rejected"):
        return {"error": "decision must be 'validated' or 'rejected'"}
    if rows[0]["status"] == decision:
        return {"ok": True, "id": vid, "status": decision, "already": True}
    db.execute("UPDATE cv_variants SET status=?, reviewed_at=?, updated_at=? WHERE id=?",
               [decision, now_iso(), now_iso(), vid])
    return {"ok": True, "id": vid, "status": decision}


def api_cv_create(payload):
    """Start a variant by hand, e.g. by pasting a CV you tailored elsewhere."""
    name = (payload.get("name") or "").strip()
    if not name:
        return {"error": "give the variant a name so you can tell them apart"}
    company, contact = _company_for(payload.get("contact_id"), payload.get("company_id"))
    new_id = db.execute(
        "INSERT INTO cv_variants (name, body, company_id, contact_id, role_target, "
        "generation, status, edited, updated_at) VALUES (?,?,?,?,?,'manual',?,1,?)",
        [name, payload.get("body") or "", company.get("id"), contact.get("id"),
         payload.get("role_target") or "",
         "validated" if payload.get("validated") else "pending", now_iso()])
    return db.query("SELECT * FROM cv_variants WHERE id=?", [new_id])[0]


ROUTES = [
    ("GET", "cvs", lambda p, rest, body: api_cv_list(p)),
    ("POST", "cvs/get", lambda p, rest, body: api_cv_get(body)),
    ("POST", "cvs/propose", lambda p, rest, body: api_cv_propose(body)),
    ("POST", "cvs/create", lambda p, rest, body: api_cv_create(body)),
    ("POST", "cvs/save", lambda p, rest, body: api_cv_save(body)),
    ("POST", "cvs/review", lambda p, rest, body: api_cv_review(body)),
]
