"""The review queue: the surface between "the machine wrote it" and "it was sent".

The workflow this module enforces, in order, with no way to skip a step:

    draft (pending) -> you may edit it, as many times as you like
                   -> validate  -> sent, an outreach row, a touchpoint
                   -> cancel    -> kept forever, marked rejected

There is deliberately no "send all pending" route and no batch-validate. A queue
that can be emptied in one click is a queue that will be, on the day it is most
tempting. Validation is per row, one row at a time, with the text you are
approving on screen.

Two tables are written and they are not interchangeable:

  * `mail_queue`          - the draft and its state. Mutable, edited constantly.
  * `contact_touchpoints` - the append-only memory that somebody was emailed.
                            Written only when a real SMTP server accepted the
                            message, never on a dry run and never on a cancel.

`contacts_to_contact()` reads the ledger, not the queue, so a draft you
cancelled or a mail that never left still counts as "not yet contacted" and stays
in the list.
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                       # noqa: E402
import email_gen                # noqa: E402
import llm                      # noqa: E402
import mailer                   # noqa: E402
from domain import enrich_contact, get_profile, now_iso   # noqa: E402

# Address trust, copied onto the queue row at draft time and never re-derived.
# `pattern` is a reconstruction from the firm's convention; `verified` came from a
# provider that checked it. These two words carry the whole "refuse rather than
# invent" rule into the sending path.
KIND_VERIFIED = "verified"
KIND_PATTERN = "pattern"
KIND_NONE = "none"


def addr_kind_for(contact, address):
    """Classify an address. The contact's own email_source is the evidence.

    `email_source='pattern'` is written by email_pattern.py when it reconstructs
    an address, so it is exactly the set this project already considers guessed.
    """
    if not address:
        return KIND_NONE
    src = (contact.get("email_source") or "").lower()
    if src in ("pattern", "reconstructed", "guess"):
        return KIND_PATTERN
    return KIND_VERIFIED


def _ctx_and_scoring(contact, company, subject, body, cv_text):
    """Score a draft with the existing checker.

    The machine's output is graded by the same rules as a human's, before it is
    ever queued - so a batch with a systemic problem is visible while it is still
    a batch you can throw away, rather than after 30 mails have gone out.
    """
    profile = get_profile()
    ctx = email_gen.build_context(contact, company, profile)
    quality = email_gen.score_email(subject, body, ctx)
    flags = email_gen.quality_flags(ctx, body)
    # The checker asks for a hook. An LLM draft has none by construction, so the
    # firm's brief is the substitute and saying so keeps the flag meaningful.
    if not ctx.get("hook") and not ctx.get("company_hook"):
        flags = [f for f in flags if "personalisation hook" not in f]
    return ctx, quality, flags


def _insert_queue(contact, company, to_addr, subject, body, *, generation, model="",
                  tokens=0, quality=None, addr_kind=None, template_id=None, cv_id=None,
                  specifics=None):
    cid = contact.get("id")
    new_id = db.execute(
        "INSERT INTO mail_queue (contact_id, company_id, to_addr, subject, body, "
        "addr_kind, template_id, cv_id, generation, model, prompt_tokens, quality, "
        "specifics, status, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
        [cid, company.get("id") or contact.get("company_id"), to_addr, subject, body,
         addr_kind if addr_kind is not None else addr_kind_for(contact, to_addr),
         template_id, cv_id, generation, model, tokens,
         json.dumps(quality or {}, ensure_ascii=False),
         json.dumps(specifics or [], ensure_ascii=False), now_iso()])
    return db.query("SELECT * FROM mail_queue WHERE id=?", [new_id])[0]


def _load_contact(contact_id):
    rows = db.query("SELECT * FROM contacts WHERE id=?", [contact_id])
    if not rows:
        return None, {}
    contact = enrich_contact(dict(rows[0]))
    return contact, (contact.get("company") or {})


def _cv_text(cv_id):
    """The CV body for a validated variant, or '' to fall back to the base CV.

    The status filter is the gate: only a variant you approved can reach a mail.
    `or ""` matters because a variant with an empty body must fall back to the
    base rather than attach an empty document.
    """
    if not cv_id:
        return ""
    rows = db.query("SELECT body FROM cv_variants WHERE id=? AND status='validated'",
                    [cv_id])
    return (rows[0]["body"] or "") if rows else ""


# ------------------------------------------------------------------ reading
def api_queue(params):
    """The queue, joined to the person and the firm so the list is readable.

    A pending draft for someone you never meant to contact should be obvious
    without a second click, so the contact name and firm are in the row itself.
    """
    where, args = [], []
    status = _first(params, "status")
    if status:
        if status not in ("pending", "validated", "sent", "failed"):
            return {"error": "unknown queue status"}
        where.append("q.status=?")
        args.append(status)
    else:
        # Default view is the working set: what still needs a decision.
        where.append("q.status IN ('pending','validated','failed')")
    if _first(params, "contact_id"):
        where.append("q.contact_id=?")
        args.append(_first(params, "contact_id"))
    sql = ("SELECT q.*, c.first_name, c.last_name, c.company_name, c.job_title, "
           "c.desk, co.name AS firm, co.type AS firm_type "
           "FROM mail_queue q "
           "LEFT JOIN contacts c ON c.id=q.contact_id "
           "LEFT JOIN companies co ON co.id=q.company_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY q.created_at DESC"
    return db.query(sql, args)


def api_queue_counts(params=None):
    """One row per status, for the tab badges.

    `cancelled` is not a status: cancelling deletes the row. It is still counted
    here so the badge renders as zero rather than being absent, which would make
    the UI's own filter list quietly inconsistent with the data.
    """
    rows = db.query("SELECT status, COUNT(*) n FROM mail_queue GROUP BY status")
    counts = {r["status"]: r["n"] for r in rows}
    for s in ("pending", "validated", "sent", "cancelled", "failed"):
        counts.setdefault(s, 0)
    return counts


def api_queue_get(payload):
    qid = payload.get("id")
    rows = db.query("SELECT * FROM mail_queue WHERE id=?", [qid])
    if not rows:
        return {"error": "that queue row no longer exists"}
    q = dict(rows[0])
    if q.get("quality"):
        try:
            q["quality"] = json.loads(q["quality"])
        except ValueError:
            q["quality"] = {}
    try:
        q["specifics"] = json.loads(q.get("specifics") or "[]")
    except ValueError:
        q["specifics"] = []
    contact, company = ({}, {})
    if q.get("contact_id"):
        contact, company = _load_contact(q["contact_id"])
    q["contact"] = {k: contact.get(k) for k in
                    ("id", "first_name", "last_name", "company_name", "job_title",
                     "position_raw", "headline", "desk", "seniority", "city",
                     "linkedin_url", "email", "email_source")}
    q["company"] = {k: company.get(k) for k in
                    ("id", "name", "type", "hq_city", "research", "careers_url")}
    q["cv"] = db.query("SELECT id, name, status FROM cv_variants WHERE id=?",
                       [q["cv_id"]])[0] if q.get("cv_id") else None
    # Re-score on read: after an edit the stored grade is stale, and a stale grade
    # shown next to edited text is worse than no grade at all.
    ctx, quality, flags = _ctx_and_scoring(contact, company, q["subject"], q["body"],
                                           _cv_text(q.get("cv_id")))
    q["quality"] = quality
    q["flags"] = flags
    q["blocking"] = mailer.preflight(q.get("to_addr"), q.get("subject"), q.get("body"),
                                     q.get("addr_kind") or KIND_NONE,
                                     confirm_pattern=bool(payload.get("confirm_pattern")))
    return q


def _first(params, key, default=""):
    """One query-string value, whether it arrived as a list or a bare string.

    Over HTTP `parse_qs` always gives lists, but a test, a script or a future
    in-process caller can pass `{'city': 'London'}`. The old `(params.get(k) or
    [""])[0]` then returned 'L' - a one-letter city filter that matched nothing
    and looked like an empty queue rather than a bug.
    """
    v = (params or {}).get(key)
    if v is None:
        return default
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v


def _remaining_where(filters):
    """The WHERE clause and args for 'not yet contacted', shared by the page of
    rows and by the total. Two copies of this drift, and when they did the Queue
    tab reported "0 to go" for a city with hundreds of people left."""
    where = ["c.id NOT IN (SELECT contact_id FROM contact_touchpoints "
             "WHERE direction='outbound' AND contact_id IS NOT NULL)"]
    args = []
    if filters.get("company_id"):
        where.append("c.company_id=?")
        args.append(filters["company_id"])
    if filters.get("city"):
        # Case-insensitive and exact: 'London' must not also match 'London Bridge'.
        where.append("lower(c.city) = lower(?)")
        args.append(filters["city"])
    if filters.get("type"):
        where.append("co.type=?")
        args.append(filters["type"])
    if filters.get("tier"):
        where.append("co.tier=?")
        args.append(filters["tier"])
    if filters.get("desk"):
        where.append("c.desk=?")
        args.append(filters["desk"])
    if filters.get("search"):
        where.append("(lower(c.first_name || ' ' || c.last_name) LIKE ? "
                     "OR lower(co.name) LIKE ? OR lower(c.email) LIKE ?)")
        needle = f"%{filters['search'].lower()}%"
        args.extend([needle, needle, needle])
    if filters.get("with_email", True):
        where.append("c.email IS NOT NULL AND c.email != ''")
    if filters.get("no_pending"):
        # Someone already sitting in the review queue does not need a second draft.
        where.append("c.id NOT IN (SELECT contact_id FROM mail_queue "
                     "WHERE status='pending' AND contact_id IS NOT NULL)")
    return where, args


def contacts_to_contact(filters=None, limit=None):
    """Who has NOT been contacted - the list that makes the tool idempotent.

    Reads `contact_touchpoints`, the append-only ledger, not the queue or the
    outreach table. That is the point: a cancelled draft, a failed send and a
    mail that is still sitting in the queue all leave the person in this list,
    because none of them means anybody was actually emailed.

    The filters are the ones you actually pick people by. City first: the whole
    project targets Paris and London, and "everyone in Paris I have not written
    to" is the actual question. `type` is the firm taxonomy from taxonomy.py.
    """
    filters = filters or {}
    where, args = _remaining_where(filters)
    sql = ("SELECT c.*, co.name AS firm, co.tier, co.type AS firm_type, "
           "(SELECT COUNT(*) FROM mail_queue q WHERE q.contact_id=c.id "
           "AND q.status='pending') AS pending_drafts "
           "FROM contacts c LEFT JOIN companies co ON co.id=c.company_id WHERE "
           + " AND ".join(where) + " ORDER BY co.tier, c.company_name, c.last_name")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.query(sql, args)


def count_remaining(filters=None):
    """How many people match, ignoring the page limit.

    This is the number the UI shows as "to go", so it has to be the total and not
    the length of the page that was fetched.
    """
    where, args = _remaining_where(filters or {})
    return db.query("SELECT COUNT(*) n FROM contacts c "
                    "LEFT JOIN companies co ON co.id=c.company_id WHERE "
                    + " AND ".join(where), args)[0]["n"]


def api_todo(params):
    """The 'who is left' view for the picker, with the counts and the facets.

    The facet lists come from the same filtered query, not from a separate
    lookup, so the cities you can pick are exactly the cities that still have
    someone in them. A city list offering only people you already wrote to is a
    filter that wastes a click.
    """
    keys = ("company_id", "city", "type", "tier", "desk", "search", "no_pending")
    filters = {k: _first(params, k) for k in keys}
    filters = {k: v for k, v in filters.items() if v != ""}
    limit = _first(params, "limit")
    rows = contacts_to_contact(filters, limit or None)
    reached = db.query("SELECT COUNT(DISTINCT contact_id) n FROM contact_touchpoints "
                       "WHERE direction='outbound'")[0]["n"]
    pending = db.query("SELECT COUNT(*) n FROM mail_queue WHERE status='pending'")[0]["n"]
    return {"remaining": rows, "n_remaining": count_remaining(filters),
            "n_shown": len(rows),
            "n_contacted": reached,
            "n_contacts": db.query("SELECT COUNT(*) n FROM contacts")[0]["n"],
            "n_pending": pending,
            # Which cities still hold someone - the primary filter.
            "cities": [r["k"] for r in db.query(
                "SELECT c.city k, COUNT(*) n FROM contacts c WHERE "
                "c.id NOT IN (SELECT contact_id FROM contact_touchpoints "
                "WHERE direction='outbound') AND c.email != '' "
                "AND c.city IS NOT NULL AND c.city != '' "
                "GROUP BY c.city ORDER BY n DESC")],
            "types": [r["k"] for r in db.query(
                "SELECT co.type k, COUNT(*) n FROM contacts c "
                "JOIN companies co ON co.id=c.company_id WHERE "
                "c.id NOT IN (SELECT contact_id FROM contact_touchpoints "
                "WHERE direction='outbound') AND c.email != '' "
                "GROUP BY co.type ORDER BY n DESC")],
            "desks": [r["k"] for r in db.query(
                "SELECT c.desk k, COUNT(*) n FROM contacts c WHERE "
                "c.id NOT IN (SELECT contact_id FROM contact_touchpoints "
                "WHERE direction='outbound') AND c.email != '' "
                "AND c.desk IS NOT NULL AND c.desk != '' "
                "GROUP BY c.desk ORDER BY n DESC LIMIT 12")]}


def api_history(params):
    """The touchpoint ledger, newest first: who was contacted, when, and how."""
    limit = int(_first(params, "limit", 200) or 200)
    return db.query(
        "SELECT t.*, c.first_name, c.last_name, c.company_name, co.name AS firm "
        "FROM contact_touchpoints t "
        "LEFT JOIN contacts c ON c.id=t.contact_id "
        "LEFT JOIN companies co ON co.id=t.company_id "
        "ORDER BY t.sent_at DESC LIMIT ?", [limit])


# ------------------------------------------------------------------ writing
def api_draft(payload):
    """Draft one mail for one contact. Queues it; sends nothing.

    The model is the only drafter. An earlier version also accepted `template_id`,
    which turned out to be worse than useless in practice: it looked like it worked,
    and it produced the same generic text for every recipient, so a queue full of
    template drafts read as "the generator is broken" rather than "you asked for a
    template". A template still has its own tab - Compose - and the `generation`
    column records which path produced each row so the two can be compared.

    Refuses loudly when there is no key, and says what to do about it: a silent
    failure here looks identical to a button that does nothing.
    """
    contact_id = payload.get("contact_id")
    if not contact_id:
        return {"error": "contact_id required"}
    contact, company = _load_contact(contact_id)
    if not contact:
        return {"error": "contact not found"}
    to_addr = (contact.get("email") or contact.get("email_guess") or "").strip()
    if not to_addr:
        # Invariant 4: no address means no mail, not a plausible one.
        return {"error": "no address for this contact - their firm's convention is "
                         "unknown, so there is nothing to write to"}
    if not llm.is_configured():
        st = llm.status()
        # `status()` only carries a `reason` when there is no key; if it is
        # configured but unusable (wrong model, endpoint down, no entitlement)
        # the reason is empty and the message must not end in a dangling space.
        why = st.get("reason") or (
            "check the llm block in config.json - base_url, api_key and model must "
            "all be set, and the model must be one this endpoint serves "
            "(python3 app/llm.py --models lists them)")
        return {"error": "The model is not usable, so nothing was drafted. " + why,
                "llm": st, "needs_key": True}
    cv_id = payload.get("cv_id")
    cv_text = _cv_text(cv_id)
    # The model can be chosen per batch, and it is recorded on every row, so a
    # reply rate can later be split by which model wrote the mail.
    model = (payload.get("model") or "").strip() or None
    out = llm.generate_mail(contact, company, get_profile(), cv_text=cv_text,
                            note=payload.get("note") or "",
                            force=bool(payload.get("force")), model=model)
    if not out.get("ok"):
        # A failed draft is recorded as nothing at all: an empty mail in the
        # queue is worse than no mail, because it looks like real work.
        return {"error": out.get("error") or "the draft failed", "llm": out}
    _ctx, quality, flags = _ctx_and_scoring(contact, company, out["subject"],
                                            out["body"], cv_text)
    flags = list(flags)
    # The point of the whole exercise. A draft that names nothing about this
    # person or firm is a draft that will be deleted unread, so it is called out
    # here rather than discovered after a reply rate of zero.
    if not out.get("specifics"):
        flags.append("the model named no specific facts - check it is not a mail "
                     "that could go to anyone")
    row = _insert_queue(contact, company, to_addr, out["subject"], out["body"],
                        generation=f"llm:{out.get('model') or ''}",
                        model=out.get("model") or "", tokens=out.get("tokens") or 0,
                        quality=quality, cv_id=cv_id,
                        specifics=out.get("specifics") or [])
    return {"queue": row, "flags": flags, "cached": bool(out.get("cached")),
            "specifics": out.get("specifics") or []}


def api_edit(payload):
    """Save your corrections to a pending draft.

    Only `pending` rows are editable. Once a mail has been sent, editing the
    stored text would quietly rewrite the record of what a person actually
    received - the same failure the touchpoint ledger exists to prevent.
    """
    qid = payload.get("id")
    rows = db.query("SELECT * FROM mail_queue WHERE id=?", [qid])
    if not rows:
        return {"error": "that queue row no longer exists"}
    q = rows[0]
    if q["status"] != "pending":
        return {"error": f"this draft is {q['status']} and can no longer be edited - "
                         "the record of what was sent does not change"}
    sets, args = [], []
    for f in ("to_addr", "subject", "body", "notes", "cv_id"):
        if f in payload:
            sets.append(f"{f}=?")
            args.append(payload[f])
    if not sets:
        return {"error": "nothing to update"}
    if "to_addr" in payload:
        # A changed address is re-classified, because half the reason to edit the
        # recipient is replacing a guessed address with a real one.
        kind = payload.get("addr_kind")
        if kind not in (KIND_VERIFIED, KIND_PATTERN, KIND_NONE):
            contact, _company = _load_contact(q.get("contact_id"))
            if contact.get("email") == payload["to_addr"]:
                kind = addr_kind_for(contact, payload["to_addr"])
            else:
                # An address the user typed, differing from the stored one, is a
                # human assertion about where to write - so it is trusted the way
                # a manual correction always is, and never downgraded back to a
                # guess just because the contact's email_source still says so.
                kind = KIND_VERIFIED
        sets.append("addr_kind=?")
        args.append(kind)
    sets.append("edited=1")
    sets.append("updated_at=?")
    args.append(now_iso())
    args.append(qid)
    db.execute(f"UPDATE mail_queue SET {', '.join(sets)} WHERE id=?", args)
    return api_queue_get({"id": qid})


def api_cancel(payload):
    """Cancel a draft: the row is deleted.

    It used to be kept with `status='cancelled'`, on the reasoning that "why did I
    not write to this person" is worth being able to answer later. The user asked
    for it to be deleted instead, and the reasoning that answered that was weak:
    an identical prompt is cached in `fetch_log`, so regenerating the same draft
    costs nothing. Keeping a rejected draft therefore buys a question the user has
    to re-ask anyway.

    The person is unaffected either way - `contacts_to_contact` reads the
    touchpoint ledger, not this table - so a cancelled draft always leaves them
    in the "not contacted" list.
    """
    qid = payload.get("id")
    rows = db.query("SELECT * FROM mail_queue WHERE id=?", [qid])
    if not rows:
        return {"error": "that queue row no longer exists"}
    if rows[0]["status"] == "sent":
        return {"error": "this mail was already sent - there is nothing to cancel"}
    db.execute("DELETE FROM mail_queue WHERE id=?", [qid])
    return {"ok": True, "id": qid, "status": "deleted"}


def api_validate(payload):
    """Approve a draft and send it. The only route in the app that sends mail.

    The order matters and is not rearranged for convenience:

      1. re-read the row, so a stale UI cannot approve text that has since changed
      2. apply the edit the user is looking at, if they made one
      3. preflight - a refusal must happen before any outreach row exists
      4. create the outreach row (the mail log), mark the queue row validated
      5. hand it to SMTP
      6. only on real acceptance, append the touchpoint

    Step 6 is the one that matters for honesty: a dry run must leave no trace in
    the ledger, or "who have I contacted" starts lying.
    """
    qid = payload.get("id")
    rows = db.query("SELECT * FROM mail_queue WHERE id=?", [qid])
    if not rows:
        return {"error": "that queue row no longer exists"}
    q = dict(rows[0])
    if q["status"] == "sent":
        return {"error": "this mail has already been sent"}
    if q["status"] == "cancelled":
        return {"error": "this draft was cancelled - generate a new one"}

    # An edit sent with the approval is saved first, so the text that goes out is
    # the text the user was looking at.
    if any(k in payload for k in ("subject", "body", "to_addr")):
        saved = api_edit({"id": qid, **{k: v for k, v in payload.items()
                                        if k in ("subject", "body", "to_addr", "cv_id")}})
        if isinstance(saved, dict) and saved.get("error"):
            return saved
        q = saved

    contact, company = ({}, {})
    if q.get("contact_id"):
        contact, company = _load_contact(q["contact_id"])

    followup_days = int(payload.get("followup_days") or 7)
    addr_kind = payload.get("addr_kind") or q.get("addr_kind") or KIND_NONE
    cv_text = _cv_text(q.get("cv_id"))
    cv_name = ""
    if q.get("cv_id"):
        cvr = db.query("SELECT name FROM cv_variants WHERE id=?", [q["cv_id"]])
        cv_name = (cvr[0]["name"] if cvr else "") + ".txt"

    problems = mailer.preflight(q.get("to_addr"), q.get("subject"), q.get("body"),
                                addr_kind,
                                confirm_pattern=bool(payload.get("confirm_pattern")))
    if problems:
        return {"ok": False, "sent": False, "error": "; ".join(problems),
                "blocking": problems}

    # The mail log, created before the send so a crash mid-send leaves evidence
    # that something was attempted rather than nothing at all.
    outreach_id = db.execute(
        "INSERT INTO outreach (contact_id, channel, template_id, subject, body, "
        "status, notes, updated_at) VALUES (?,?,?,?,?,'approved',?,?)",
        [q.get("contact_id"), q.get("channel") or "email", q.get("template_id"),
         q.get("subject"), q.get("body"),
         f"from the review queue (row {qid})", now_iso()])
    db.execute("UPDATE mail_queue SET status='validated', reviewed_at=?, addr_kind=?, "
               "outreach_id=?, updated_at=? WHERE id=?",
               [now_iso(), addr_kind, outreach_id, now_iso(), qid])

    result = mailer.send(q.get("to_addr"), q.get("subject"), q.get("body"),
                         addr_kind=addr_kind,
                         confirm_pattern=bool(payload.get("confirm_pattern")),
                         cv_filename=cv_name or None, cv_text=cv_text or None)
    if not result["ok"]:
        db.execute("UPDATE mail_queue SET status='failed', send_error=? WHERE id=?",
                   [result.get("error"), qid])
        return {"ok": False, "sent": False, "error": result.get("error"),
                "queue_id": qid, "outreach_id": outreach_id,
                "note": "the draft is kept - fix the problem and validate again"}

    if not result["sent"]:
        # Dry run: validated, deliberately NOT sent, and NOT in the ledger.
        db.execute("UPDATE mail_queue SET send_error=? WHERE id=?", [result.get("error"), qid])
        return {"ok": True, "sent": False, "dry": True, "queue_id": qid,
                "outreach_id": outreach_id, "message_id": result.get("message_id"),
                "error": result.get("error"),
                "note": "validated but not transmitted (dry mode). No touchpoint was "
                        "recorded, so this person is still in the 'left to contact' list."}

    # Real acceptance: the only place a touchpoint is ever written.
    db.execute("INSERT INTO contact_touchpoints (contact_id, company_id, queue_id, "
               "outreach_id, channel, direction, to_addr, subject, addr_kind, sent_at, "
               "message_id) VALUES (?,?,?,?,?,'outbound',?,?,?,?,?)",
               [q.get("contact_id"), company.get("id") or q.get("company_id"), qid,
                outreach_id, q.get("channel") or "email", q.get("to_addr"),
                q.get("subject"), addr_kind, now_iso(), result.get("message_id")])
    db.execute("UPDATE outreach SET status='sent', sent_at=?, next_followup_at=?, "
               "updated_at=? WHERE id=?",
               [now_iso(), (date.today() + timedelta(days=followup_days)).isoformat(),
                now_iso(), outreach_id])
    db.execute("UPDATE mail_queue SET status='sent', sent_at=?, message_id=?, "
               "send_error=NULL, updated_at=? WHERE id=?",
               [now_iso(), result.get("message_id"), now_iso(), qid])
    return {"ok": True, "sent": True, "queue_id": qid, "outreach_id": outreach_id,
            "message_id": result.get("message_id"),
            "note": f"sent. A follow-up is due in {followup_days} days."}


def api_queue_followup(payload):
    """Draft a follow-up into the queue.

    A follow-up used to open the Compose tab and drop the text there, which is
    exactly the tab this workflow replaced - so the bump would have been the one
    mail with no review step at all. It now arrives as a normal pending queue
    row: read it, edit it, send it, same as everything else.

    The subject is threaded (the same subject with `Re:`) so the reply lands in
    the original conversation, which is most of why a bump gets read.
    """
    oid = payload.get("outreach_id")
    rows = db.query("SELECT * FROM outreach WHERE id=?", [oid])
    if not rows:
        return {"error": "that outreach row no longer exists"}
    o = rows[0]
    if not o.get("contact_id"):
        return {"error": "this mail has no contact attached, so there is nothing to follow up"}
    contact, company = _load_contact(o["contact_id"])
    if not contact:
        return {"error": "the contact for this mail was deleted"}

    tpl = (db.query("SELECT * FROM templates WHERE active=1 AND (role_family='followup' "
                    "OR lower(name) LIKE '%follow%') ORDER BY id LIMIT 1")
           or db.query("SELECT * FROM templates WHERE active=1 ORDER BY id LIMIT 1"))
    if not tpl:
        return {"error": "no follow-up template on file"}
    tpl = tpl[0]
    ctx = email_gen.build_context(contact, company, get_profile())
    subject = (o.get("subject") or "").strip()
    if subject:
        subject = subject if subject.lower().startswith("re:") else "Re: " + subject
    else:
        subject = email_gen.render(tpl["subject_tpl"], ctx)
    body = email_gen.render(tpl["body_tpl"], ctx)
    to_addr = (contact.get("email") or contact.get("email_guess") or "").strip()
    if not to_addr:
        return {"error": "no address for this contact any more"}

    _ctx, quality, flags = _ctx_and_scoring(contact, company, subject, body, "")
    # Recorded as template-derived on purpose: a follow-up is deliberately the
    # same bump as last time, so the `generation` column must not imply the model
    # wrote it, and the reply-rate view stays comparable.
    row = _insert_queue(contact, company, to_addr, subject, body,
                        generation=f"template:{tpl['id']}",
                        template_id=tpl["id"], quality=quality)
    return {"queue": row, "flags": flags, "outreach_id": oid}


def api_models():
    """The models this endpoint will actually serve, for the picker's dropdown.

    The filter is measured, not guessed. On this Bedrock endpoint `/models`
    advertises 57: every `anthropic.*` rejects `/v1/chat/completions` with a 400,
    and every `openai.*` answers `access_denied` because the account is not
    entitled. Offering those would be offering most of the list that cannot work.
    `untested` carries the remainder so nothing is hidden - it is just not
    pre-approved.
    """
    models = llm.available_models()
    current = llm.config()["model"]

    def rejected(m):
        return (m.startswith(("anthropic.", "xai.grok", "google.gemma-4",
                              "writer.", "openai.gpt-5", "openai.gpt-6")))

    good = [m for m in models if not rejected(m)]
    untested = [m for m in models if rejected(m) and not m.startswith(
        ("anthropic.", "xai.grok", "google.gemma-4", "writer."))]
    return {"models": good, "current": current, "untested": untested,
            "base_url": llm.config()["base_url"]}


ROUTES = [
    ("GET", "queue", lambda p, rest, body: api_queue(p)),
    ("GET", "queue/counts", lambda p, rest, body: api_queue_counts()),
    ("POST", "queue/get", lambda p, rest, body: api_queue_get(body)),
    ("GET", "todo", lambda p, rest, body: api_todo(p)),
    ("GET", "llm/models", lambda p, rest, body: api_models()),
    ("GET", "history", lambda p, rest, body: api_history(p)),
    ("GET", "queue/status",
     lambda p, rest, body: {"llm": llm.status(), "mail": mailer.status()}),
    ("POST", "queue/draft", lambda p, rest, body: api_draft(body)),
    ("POST", "queue/edit", lambda p, rest, body: api_edit(body)),
    ("POST", "queue/cancel", lambda p, rest, body: api_cancel(body)),
    ("POST", "queue/followup", lambda p, rest, body: api_queue_followup(body)),
    ("POST", "queue/validate", lambda p, rest, body: api_validate(body)),
]
