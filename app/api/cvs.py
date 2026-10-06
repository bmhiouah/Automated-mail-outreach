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

import os
import db                       # noqa: E402
import llm                      # noqa: E402
import latex_cv                 # noqa: E402
import latex_build              # noqa: E402
from domain import get_profile, now_iso, Raw  # noqa: E402

# What a client may set by hand. `status` is not here on purpose: validation is a
# decision the user makes through api_cv_review, not a field the UI can flip while
# saving an edit. `latex` is here so the LaTeX editor can save the source; when
# it changes, the compiled PDF is immediately rebuilt so the preview shows what
# was just written, not what used to be there.
CV_FIELDS = ["name", "body", "latex", "parent_id", "company_id", "contact_id",
             "role_target", "notes"]


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
        "v.pdf_path, v.pdf_at, length(v.latex) AS latex_bytes, "
        "co.name AS firm, c.first_name, c.last_name, "
        "length(v.body) AS bytes, "
        "(SELECT COUNT(*) FROM mail_queue q WHERE q.cv_id=v.id) AS used_by "
        "FROM cv_variants v "
        "LEFT JOIN companies co ON co.id=v.company_id "
        "LEFT JOIN contacts c ON c.id=v.contact_id "
        "ORDER BY v.status='pending' DESC, v.created_at DESC")
    fresh = [_base_variant()] + [dict(r) for r in rows]
    for v in fresh:
        v["stale"] = (not v.get("pdf_path")) or not latex_build.pdf_exists(
            v.get("pdf_path"))
    return {"variants": fresh, "base_bytes": len(base_cv()),
            "base_present": bool(base_cv())}


def api_cv_get(payload):
    if payload.get("id") == 0:
        # The base CV is not stored as a variant: it IS main.tex. Anything the
        # UI shows for it (LaTeX editor, compiled PDF) is read or built live.
        v = _base_variant()
        v["stale"] = (not v.get("pdf_path")) or not latex_build.pdf_exists(
            v.get("pdf_path"))
        return v
    rows = db.query("SELECT * FROM cv_variants WHERE id=?", [payload.get("id")])
    if not rows:
        return {"error": "that CV variant no longer exists"}
    v = dict(rows[0])
    if v.get("company_id"):
        co = db.query("SELECT name FROM companies WHERE id=?", [v["company_id"]])
        v["firm"] = co[0]["name"] if co else ""
    if v.get("pdf_path"):
        v["pdf_url"] = _pdf_url(v)
    v["stale"] = (not v.get("pdf_path")) or not latex_build.pdf_exists(
        v.get("pdf_path"))
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
                          role_target=role, force=bool(payload.get("force")),
                          model=(payload.get("model") or None))
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
    """Write your own edits to a variant. Marks it edited, not validated.

    Word edits are written back into the LaTeX they came from, then the PDF is
    rebuilt: the preview must show what was just typed, not what used to be
    there. A failed build does not undo the text edit - the words are yours,
    the layout is what broke.
    """
    vid = payload.get("id")
    rows = db.query("SELECT * FROM cv_variants WHERE id=?", [vid])
    if not rows:
        return {"error": "that CV variant no longer exists"}
    before = dict(rows[0])
    payload = dict(payload)
    old_latex = before.get("latex") or ""
    old_body = before.get("body") or ""
    latex_in = "latex" in payload
    body_in = "body" in payload
    new_latex = payload["latex"] if latex_in else old_latex
    new_body = payload["body"] if body_in else old_body
    latex_changed = latex_in and new_latex != old_latex
    body_changed = body_in and new_body != old_body
    # Words pane is a rendering of the source. If the user edited words and
    # left the LaTeX tab untouched, patch the .tex so compile sees the edit.
    unplaceable = False
    if body_changed and not latex_changed and old_latex.strip():
        patched = latex_cv.apply_text_edits(old_latex, old_body, new_body)
        if patched != old_latex:
            payload["latex"] = patched
            latex_changed = True
        else:
            # The words are still saved - they are the user's - but no phrase
            # could be placed in the source. Saying so is the point: the PDF is
            # unchanged, and a save that said "saved, PDF rebuilt" here would be
            # a lie that costs them the discovery at the attachment.
            unplaceable = True
    elif latex_changed and not body_changed:
        payload["body"] = _text_of(new_latex)
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
    out = {"variant": api_cv_get({"id": vid})}
    if unplaceable:
        out["words_not_applied"] = (
            "saved, but the change could not be written into the .tex, so the PDF "
            "is unchanged - make the edit on the LaTeX source tab to see it")
    if latex_changed:
        compiled = compile_variant(vid)
        out["compiled"] = compiled
        out["variant"] = api_cv_get({"id": vid})
        out["pdf_url"] = _pdf_url(out["variant"])
    return out


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


def api_cv_delete(payload):
    """Throw a variant away, along with its compiled PDF.

    Two refusals, both because the loss would otherwise be silent:

      * the base (id 0) is main.tex, which this module never writes to - there
        is nothing to delete, and offering to would be a lie;
      * a variant an unsent queued mail still points at. Deleting it would
        leave the draft attaching nothing, and it would go out bare with no
        warning. Detaching it in the Queue tab is the visible way to do that.

    A variant only a *sent* mail used may be deleted: the mail is already out,
    and keeping an unused document around is clutter, not provenance.
    """
    vid = payload.get("id")
    if vid in (0, "0"):
        return {"error": "the base CV cannot be deleted - it is main.tex, and "
                         "this tool never writes to it"}
    rows = db.query("SELECT id, name, pdf_path FROM cv_variants WHERE id=?", [vid])
    if not rows:
        return {"error": "that CV variant no longer exists"}
    held = db.query("SELECT COUNT(*) n FROM mail_queue WHERE cv_id=? "
                    "AND status<>'sent'", [vid])[0]["n"]
    if held:
        return {"error": f"this CV is attached to {held} unsent mail(s) - detach "
                         "it in the Queue tab first, so a draft cannot go out "
                         "without the attachment you chose"}
    removed = latex_build.remove_variant_files(vid, rows[0]["pdf_path"])
    db.execute("DELETE FROM cv_variants WHERE id=?", [vid])
    # mail_queue.cv_id carries no foreign key (the base CV is id 0, not a
    # variant row, so a FK would reject every base-CV draft). The cascade a
    # deleted variant used to get from the FK is done here instead: only sent
    # mails can still point at it - unsent ones were refused above - and those
    # are already out, so their link to the deleted document is history.
    db.execute("UPDATE mail_queue SET cv_id=NULL WHERE cv_id=?", [vid])
    return {"ok": True, "id": vid, "name": rows[0]["name"], "removed": removed}


def api_cv_create(payload):
    """Start a variant by hand, e.g. by pasting a CV you tailored elsewhere.

    A pasted LaTeX source is compiled at once: the list and the preview stay in
    agreement instead of describing a document that was never built. `latex`
    wins over `body` when both are given, because the source is what mail must
    render.
    """
    name = (payload.get("name") or "").strip()
    if not name:
        return {"error": "give the variant a name so you can tell them apart"}
    company, contact = _company_for(payload.get("contact_id"), payload.get("company_id"))
    latex = (payload.get("latex") or "").strip()
    body = (payload.get("body") or "").strip() or (_text_of(latex) if latex else "")
    new_id = db.execute(
        "INSERT INTO cv_variants (name, body, latex, company_id, contact_id, role_target, "
        "generation, status, edited, updated_at) VALUES (?,?,?,?,?,?,'manual',?,1,?)",
        [name, body, latex or "", company.get("id"), contact.get("id"),
         payload.get("role_target") or "",
         "validated" if payload.get("validated") else "pending", now_iso()])
    row = db.query("SELECT * FROM cv_variants WHERE id=?", [new_id])[0]
    if not latex:
        return row
    compiled = compile_variant(new_id)
    row = db.query("SELECT * FROM cv_variants WHERE id=?", [new_id])[0]
    row["pdf_url"] = _pdf_url(row)
    row["compiled"] = compiled
    return row


DEFAULT_INSTRUCTION = (
    "Keep every qualification, date, employer and number exactly as they are - "
    "never invent or drop anything. Reorder and re-weight the document so the "
    "most relevant material comes first for this firm and role, trim sections "
    "that do not help, and make the summary at the top specific to them."
)


def api_cv_instruction(params):
    """The standing instruction, and whether the two things it needs are there."""
    prof = get_profile()
    saved = (prof.get("cv_instruction") or "").strip()
    engine = latex_build.engine()
    return {
        "instruction": saved or DEFAULT_INSTRUCTION,
        "saved": bool(saved),
        "default": DEFAULT_INSTRUCTION,
        "base_tex_present": bool(latex_build.base_tex()),
        "base_tex_path": os.path.relpath(latex_build.base_tex_path(), latex_build.BASE),
        "engine_ok": bool(engine),
        "engine": engine or "",
    }


def api_cv_instruction_save(payload):
    """Remember the wording. It is the spec, not a comment - it should survive.

    A body that does not carry the field at all is refused rather than written:
    clearing the box deliberately sends `instruction: ""`, but a stray empty
    POST should not be able to wipe the specification you spent time on.
    """
    if "instruction" not in (payload or {}):
        return {"error": "no instruction given"}
    text = (payload.get("instruction") or "").strip()
    db.execute("UPDATE profile SET cv_instruction=?, updated_at=CURRENT_TIMESTAMP "
               "WHERE id=1", [text])
    return {"ok": True, "instruction": text}


def _text_of(tex):
    """The plain text a compiled document says, for scoring and for reading."""
    try:
        return latex_cv.latex_to_text(tex)
    except Exception:
        # The PDF is the deliverable; a conversion failure must not lose it.
        return tex


def _pdf_url(variant):
    return f"/api/cvs/pdf/{variant['id']}" if (variant or {}).get("pdf_path") else ""


def compile_variant(vid):
    """Compile one variant's LaTeX. Failure is reported, never raised.

    A previous PDF is left alone on failure: it still corresponds to the LaTeX
    that produced it, and quietly deleting it would turn one bad edit into an
    unsendable variant. `stale` says the preview no longer matches the source.
    """
    rows = db.query("SELECT * FROM cv_variants WHERE id=?", [vid])
    if not rows:
        return {"ok": False, "error": "that CV variant no longer exists"}
    v = dict(rows[0])
    if not (v.get("latex") or "").strip():
        return {"ok": False, "error": "this variant has no LaTeX source - it was "
                                      "pasted as text, so there is nothing to compile"}
    res = latex_build.compile_latex(v["latex"], f"cv-{vid}")
    if res.get("ok"):
        db.execute("UPDATE cv_variants SET pdf_path=?, pdf_at=?, updated_at=? WHERE id=?",
                   [res["pdf"], now_iso(), now_iso(), vid])
        res["variant_id"] = vid
        return res
    res["variant_id"] = vid
    res["kept_previous"] = bool(v.get("pdf_path"))
    res["stale"] = bool(v.get("pdf_path"))
    return res


def api_cv_latex_propose(payload):
    """main.tex + your instruction -> a new variant, compiled to a PDF.

    The base document is read fresh and never written; the variant carries the
    LaTeX, the plain-text rendering of it, and the instruction that produced it,
    so a CV can be re-derived rather than remembered.
    """
    base = latex_build.base_tex()
    if not base.strip():
        return {"error": "no base LaTeX CV found - expected "
                         f"{os.path.relpath(latex_build.base_tex_path(), latex_build.BASE)} "
                         "in the project root"}
    # Something to tailor FOR. This is not just a guard: an empty POST would
    # otherwise spend a real model call to produce a CV for no one, which is
    # also why "an empty body must be a JSON error, never a 500" holds here.
    if not (payload.get("company_id") or payload.get("contact_id")
            or (payload.get("role_target") or "").strip()):
        return {"error": "choose a firm or give a target role before generating"}
    if not latex_build.engine():
        return {"error": "no TeX engine found - install MacTeX/TeX Live, or set "
                         "cv.engine in config.json"}
    if not llm.is_configured():
        return {"error": "no LLM API key configured - drafting cannot start"}

    company, contact = _company_for(payload.get("contact_id"), payload.get("company_id"))
    role = (payload.get("role_target") or "").strip()
    instruction = (payload.get("instruction") or "").strip() or DEFAULT_INSTRUCTION

    out = llm.generate_cv_latex(contact, company, get_profile(), base, instruction,
                                role_target=role,
                                force=bool(payload.get("force")),
                                model=(payload.get("model") or None))
    if not out.get("ok"):
        return {"error": out.get("error") or "the tailoring failed",
                "model": out.get("model"), "tokens": out.get("tokens") or 0}

    tex = latex_build.ensure_document(out.get("body") or "", base)
    if not tex.strip():
        return {"error": "the model returned neither a complete document nor a "
                         "body I can attach to main.tex - try again"}

    name = (payload.get("name") or _variant_name(company, role)).strip()
    new_id = db.execute(
        "INSERT INTO cv_variants (name, body, latex, parent_id, company_id, "
        "contact_id, role_target, generation, instruction, status, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
        [name, _text_of(tex), tex, payload.get("parent_id"), company.get("id"),
         contact.get("id"), role, f"llm:{out.get('model') or ''}",
         instruction, now_iso()])
    compiled = compile_variant(new_id)
    variant = db.query("SELECT * FROM cv_variants WHERE id=?", [new_id])[0]
    return {"variant": variant, "compiled": compiled,
            "cached": bool(out.get("cached")), "tokens": out.get("tokens") or 0,
            "pdf_url": _pdf_url(variant),
            "note": "proposed, not applied - review it, then Validate to allow it "
                    "on a mail"}


def api_cv_compile(payload):
    """Recompile a variant, e.g. after editing its LaTeX by hand."""
    vid = payload.get("id")
    out = compile_variant(vid)
    row = db.query("SELECT * FROM cv_variants WHERE id=?", [vid])
    return dict(out, variant=(row[0] if row else None),
                pdf_url=(_pdf_url(row[0]) if row else ""))


def api_cv_pdf(vid):
    """The compiled PDF as bytes. An iframe can display plain text on failure."""
    if vid in (0, "0"):
        return api_cv_base_pdf({})
    rows = db.query("SELECT pdf_path, name FROM cv_variants WHERE id=?", [vid])
    if not rows:
        return Raw(b"unknown CV variant", "text/plain; charset=utf-8")
    data = latex_build.pdf_bytes(rows[0]["pdf_path"])
    if not data:
        return Raw(b"No PDF for this variant yet - press Compile.",
                   "text/plain; charset=utf-8")
    fname = latex_build.slugify(rows[0]["name"]) + ".pdf"
    return Raw(data, "application/pdf",
               {"Content-Disposition": f'inline; filename="{fname}"',
                "Cache-Control": "no-store, no-cache, must-revalidate"})


def api_cv_base_pdf(params):
    """The base document, compiled on demand. Never stored: it is the source."""
    base = latex_build.base_tex()
    if not base.strip():
        return Raw(b"main.tex is missing - nothing to show.",
                   "text/plain; charset=utf-8")
    res = latex_build.compile_latex(base, "main")
    if not res.get("ok"):
        return Raw(("main.tex does not compile:\n\n" + (res.get("error") or "")
                    ).encode("utf-8"), "text/plain; charset=utf-8")
    data = latex_build.pdf_bytes(res["pdf"])
    if not data:
        return Raw(b"Compiled, but the PDF could not be read back.",
                   "text/plain; charset=utf-8")
    return Raw(data, "application/pdf",
               {"Content-Disposition": 'inline; filename="main.pdf"',
                "Cache-Control": "no-store, no-cache, must-revalidate"})


def _base_variant():
    """The base CV, shaped like a variant row so the UI can show it anywhere.

    Always id 0, always 'made by you', always compiled on demand. The mail gate
    needs (validated, compiled) before anything can be attached, so the base is
    offered as already approved - the compile step is what makes it sendable.
    This dict is built live from main.tex: main.tex is never itself written to,
    which is the whole reason a bad run costs nothing.
    """
    base = latex_build.base_tex()
    body = _text_of(base)
    return {"id": 0, "name": "Base CV (main.tex)", "body": body,
            "latex": base, "status": "validated", "generation": "you",
            "role_target": "", "firm": "", "first_name": "", "last_name": "",
            "company_id": None, "contact_id": None, "parent_id": None,
            "used_by": 0, "bytes": len(body), "latex_bytes": len(base),
            "pdf_path": "data/cvs/main.pdf", "stale": not latex_build.pdf_exists(
                "data/cvs/main.pdf"), "pdf_url": "/api/cvs/base-pdf"}


def api_cv_base_compile(payload):
    """Compile main.tex itself, after it was edited by hand elsewhere."""
    res = latex_build.compile_latex(latex_build.base_tex(), "main")
    return dict(res, variant=_base_variant(), pdf_url="/api/cvs/base-pdf")


ROUTES = [
    ("GET", "cvs", lambda p, rest, body: api_cv_list(p)),
    ("GET", "cvs/base-pdf", lambda p, rest, body: api_cv_base_pdf(p)),
    ("GET", "cvs/instruction", lambda p, rest, body: api_cv_instruction(p)),
    ("GET", "cvs/pdf/<id>", lambda p, rest, body: api_cv_pdf(rest[0])),
    ("POST", "cvs/instruction", lambda p, rest, body: api_cv_instruction_save(body)),
    ("POST", "cvs/get", lambda p, rest, body: api_cv_get(body)),
    ("POST", "cvs/propose", lambda p, rest, body: api_cv_propose(body)),
    ("POST", "cvs/latex-propose", lambda p, rest, body: api_cv_latex_propose(body)),
    ("POST", "cvs/compile", lambda p, rest, body: api_cv_compile(body)),
    ("POST", "cvs/base-compile", lambda p, rest, body: api_cv_base_compile(body)),
    ("POST", "cvs/create", lambda p, rest, body: api_cv_create(body)),
    ("POST", "cvs/save", lambda p, rest, body: api_cv_save(body)),
    ("POST", "cvs/review", lambda p, rest, body: api_cv_review(body)),
    ("POST", "cvs/delete", lambda p, rest, body: api_cv_delete(body)),
]
