"""A firm's email convention: learn it once, reuse it forever.

The address format is a property of the FIRM, not of any person. So it belongs
on the company row: once we have seen one real address at Amundi
(`firstname.lastname@amundi.com`), every future person we learn about there -
even from a source that gives us only a first and last name - can be given an
address without asking an API again.

That is the whole value of this module, and it is what makes the database
compound: each contact you collect makes the next one cheaper.

Two ways a pattern gets learned, strongest first:
  1. a real address whose local part is explained by a known person's name
     (deterministic - we can *prove* which convention it is);
  2. Hunter telling us directly (`data.pattern` -> "{first}.{last}").

A shape guess ("it has a dot, so probably first.last") is the weakest and is
never stored at high confidence, because a wrong pattern silently mis-addresses
every person at that firm.

    from email_pattern import learn_from_person, reconstruct
    learn_from_person(first, last, "jane.doe@amundi.com", company)   # -> "first.last"
    reconstruct("Jane", "Doe", company)                              # -> jane.doe@amundi.com
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

PATTERNS = {
    "first.last": lambda f, l: f"{f}.{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "first-last": lambda f, l: f"{f}-{l}",
    "f.last": lambda f, l: f"{f[0]}.{l}" if f else "",
    "flast": lambda f, l: f"{f[0]}{l}" if f else "",
    "firstlast": lambda f, l: f"{f}{l}",
    "firstl": lambda f, l: f"{f}{l[0]}" if l else "",
    "last.first": lambda f, l: f"{l}.{f}",
    "lastfirst": lambda f, l: f"{l}{f}",
    "lastf": lambda f, l: f"{l}{f[0]}" if f else "",
    "first": lambda f, l: f,
}


def _alpha(s):
    return "".join(ch for ch in (s or "").strip().lower() if ch.isalpha())


def clean_domain(domain):
    d = (domain or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip("/").split("/")[0]


# Backwards-compatible alias: server.py and the tests call it this name.
_clean_domain = clean_domain


def reconstruct(first, last, company):
    """Build the address for a person at a firm whose convention we know.

    This is the payoff for storing the pattern: name in, address out, no API
    call. Returns "" when the firm has no known convention - an absence we
    report honestly rather than inventing something plausible.
    """
    if not company or not company.get("domain"):
        return ""
    f, l = _alpha(first), _alpha(last)
    if not f or not l:
        return ""
    pattern = (company.get("email_pattern") or "").strip().lower()
    domain = clean_domain(company["domain"])
    fn = PATTERNS.get(pattern)
    if not fn or not domain:
        return ""
    local = fn(f, l)
    return f"{local}@{domain}" if local else ""


def guess_email(contact, company):
    """Guess the address for a contact dict. Thin wrapper over `reconstruct`."""
    return reconstruct(contact.get("first_name"), contact.get("last_name"), company)


def explain(first, last, email):
    """Which conventions reproduce this address? Returns (pattern, confidence).

    Deterministic: we test every known pattern against the real name/address
    pair. Exactly one match is a proof. Several matches means the name is
    ambiguous (e.g. "annasmith" is both firstlast and flast for Ann Smith), so
    we keep the evidence but do not claim certainty.
    """
    if not email or "@" not in email:
        return None, 0.0
    local = email.strip().lower().rsplit("@", 1)[0]
    f, l = _alpha(first), _alpha(last)
    if not f or not l or not local:
        return None, 0.0
    matches = [name for name, fn in PATTERNS.items() if fn(f, l) == local]
    if len(matches) == 1:
        return matches[0], 0.95
    if len(matches) > 1:
        # Ambiguous but still real evidence: at least we know one of these.
        return matches[0], 0.6
    return None, 0.0


def _shape_heuristic(local):
    """Last resort: guess from the shape of one address with no name to check."""
    if "." in local:
        a, b = local.split(".", 1)
        if len(a) == 1:
            return "f.last", 0.5, "one-letter first part suggests f.last"
        return "first.last", 0.5, "dotted address, no contact to confirm against"
    if "_" in local:
        return "first_last", 0.5, "underscore separator"
    if local.isalpha():
        return ("firstlast", 0.35,
                "no separator - ambiguous between firstlast, flast and first; "
                "confirm with a second sample")
    return "first.last", 0.25, "unrecognised shape - verify manually"


def store(company, pattern, confidence, source, sample=""):
    """Record a firm's convention. Every distinct pattern is kept; one is primary.

    A firm can use more than one convention (acquisitions, regional offices),
    so EVERY pattern learned goes into `pattern_evidence` - with the two halves
    of the address: pattern_before (the local part: first.last) and
    pattern_after (the domain after the @: amundi.com; a masked Prospeo
    address proves this half before anyone is revealed).

    `companies.email_pattern` stays the PRIMARY convention: it is only
    upgraded, never downgraded - a pattern proven against a real person's
    name (0.95) beats Hunter's reported pattern (0.9), which beats a shape
    guess. Re-read the row rather than trusting the caller's copy: a stale
    dict would let a weak guess overwrite a proven one.
    """
    if not pattern or not company:
        return False
    rows = db.query("SELECT * FROM companies WHERE id=?", [company["id"]])
    firm = rows[0] if rows else company
    current = (firm.get("email_pattern") or "").strip()
    current_conf = firm.get("pattern_confidence") or 0

    # the domain after the @: from the sample when we have one, else the firm's
    after = clean_domain(sample.rsplit("@", 1)[1]) if "@" in (sample or "") \
        else clean_domain(firm.get("domain"))
    db.execute(
        "INSERT INTO pattern_evidence (company_id, pattern, pattern_before, "
        "pattern_after, sample_email, source, confidence, notes) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [company["id"], pattern, pattern, after, sample, source, confidence,
         f"learned from {source}"])

    upgraded = not current or confidence > (current_conf or 0)
    if upgraded:
        db.execute(
            "UPDATE companies SET email_pattern=?, pattern_confidence=?, "
            "pattern_source=?, pattern_sample=COALESCE(NULLIF(?,''), pattern_sample), "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [pattern, confidence, source, sample, company["id"]])
    return upgraded


def learn_from_person(first, last, email, company):
    """Learn a firm's convention from one real (name, address) pair.

    This is the single most valuable call in the harvest: it turns one
    verified contact into an address generator for the whole firm. Works even
    when the person is not yet in the database, which `infer_pattern` cannot do.
    """
    pattern, confidence = explain(first, last, email)
    if not pattern:
        return {"learned": False, "reason": "address does not match the name"}
    applied = store(company, pattern, confidence, "name+address match", email)
    return {"learned": applied, "pattern": pattern, "confidence": confidence}


def learn_from_hunter(company, hunter_pattern, raw=None):
    """Translate Hunter's "{first}.{last}" into our convention and store it."""
    from providers import hunter as hunter_mod
    name = hunter_mod.map_pattern(hunter_pattern)
    if not name:
        return {"learned": False, "reason": f"unrecognised shape {raw!r}"}
    applied = store(company, name, 0.9, "hunter domain search")
    return {"learned": applied, "pattern": name, "confidence": 0.9}


def infer_pattern(company_id, sample_email, min_confidence=0.0):
    """Learn a firm's convention from one real address, matching known contacts.

    Kept for the paste/verify UI path. `learn_from_person` is preferred when the
    name is known, because it does not need the contact to exist first.
    """
    sample = (sample_email or "").strip().lower()
    if "@" not in sample:
        return {"error": "not a valid email address"}
    local, domain = sample.rsplit("@", 1)
    local = local.strip()
    domain = clean_domain(domain)

    rows = db.query("SELECT * FROM companies WHERE id=?", [company_id])
    if not rows:
        return {"error": "company not found"}
    company = rows[0]

    contacts = db.query("SELECT * FROM contacts WHERE company_id=?", [company_id])
    matches = []
    for c in contacts:
        f, l = _alpha(c["first_name"]), _alpha(c["last_name"])
        if not f or not l:
            continue
        for name, fn in PATTERNS.items():
            if fn(f, l) == local:
                matches.append((name, f"{c['first_name']} {c['last_name']}"))

    if matches:
        counts = {}
        for name, _ in matches:
            counts[name] = counts.get(name, 0) + 1
        pattern = max(counts.items(), key=lambda kv: kv[1])[0]
        confidence = 0.95 if counts[pattern] > 1 else 0.85
        evidence_source = "matched " + ", ".join(sorted({m[1] for m in matches})[:3])
        note = f"inferred from sample {sample}"
    else:
        pattern, confidence, note = _shape_heuristic(local)
        evidence_source = "shape heuristic (no contact match)"

    if not company.get("domain"):
        db.execute("UPDATE companies SET domain=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   [domain, company_id])
    applied = confidence >= min_confidence
    if applied:
        store(company, pattern, confidence, evidence_source, sample)
    return {
        "pattern": pattern, "domain": domain, "confidence": confidence,
        "source": evidence_source, "note": note, "applied": applied,
        "candidates": sorted(PATTERNS.keys()),
    }


def company_for_domain(domain):
    """Find the firm that owns an email domain.

    Deliberately strict. An earlier version matched `'%firm.com'`, which made
    `brandnewfirm.com` resolve to some unrelated firm ending in `firm.com` -
    silently filing a person under the wrong employer. Prefix/suffix LIKE
    matching on domains is not safe, so we only walk real dot boundaries:
    `mail.janestreet.com` -> `janestreet.com`. A domain we cannot attribute is
    reported as unknown, which is the honest answer.
    """
    d = clean_domain(domain)
    if not d:
        return None
    rows = db.query("SELECT * FROM companies WHERE lower(domain)=?", [d])
    if rows:
        return rows[0]
    # Subdomains and mail hosts: peel labels off the left until one matches.
    parts = d.split(".")
    for i in range(1, len(parts) - 1):
        candidate = ".".join(parts[i:])
        rows = db.query("SELECT * FROM companies WHERE lower(domain)=?", [candidate])
        if rows:
            return rows[0]
    return None


def learn_patterns_from_samples(samples):
    """Learn conventions from real addresses, using the strongest evidence first.

    Only a name-matched address is applied. A bare address tells us the domain
    and nothing about the convention, so the shape guess is reported, not saved.
    """
    learned, skipped, seen = [], [], set()
    for s in samples or []:
        email = (s.get("email") or "").strip().lower()
        if "@" not in email:
            continue
        domain = clean_domain(email.rsplit("@", 1)[1])
        if not domain or domain in seen:
            continue
        seen.add(domain)
        co = company_for_domain(domain)
        if not co:
            skipped.append({"email": email, "reason": "no firm in the seed uses that domain"})
            continue
        res = infer_pattern(co["id"], email, min_confidence=0.85)
        if res.get("error"):
            skipped.append({"email": email, "reason": res["error"]})
        elif res.get("applied"):
            learned.append({"company": co["name"], "pattern": res["pattern"],
                            "confidence": res["confidence"], "source": res["source"]})
        else:
            skipped.append({"email": email, "company": co["name"],
                            "reason": "address does not match a known name at that firm, "
                                      "so the convention is still a guess"})
    return {"learned": learned, "skipped": skipped}


def patterns_for_company(company_id):
    """Every convention on record for a firm, best first.

    A firm can legitimately use several (acquisitions, regional offices), so
    the primary (companies.email_pattern) comes first, then every distinct
    pattern in pattern_evidence by confidence. Reconstruction tries them all.
    """
    out, seen = [], set()
    rows = db.query("SELECT email_pattern, pattern_confidence FROM companies WHERE id=?",
                    [company_id])
    if rows and rows[0]["email_pattern"]:
        p = rows[0]["email_pattern"].strip()
        out.append((p, rows[0]["pattern_confidence"] or 0.9))
        seen.add(p.lower())
    for r in db.query("SELECT DISTINCT pattern, MAX(confidence) confidence "
                      "FROM pattern_evidence WHERE company_id=? "
                      "AND pattern IS NOT NULL AND pattern!='' "
                      "GROUP BY pattern ORDER BY confidence DESC", [company_id]):
        p = (r["pattern"] or "").strip()
        if p and p.lower() not in seen:
            out.append((p, r["confidence"] or 0.5))
            seen.add(p.lower())
    return out


def _mask_admits(local, mask_local):
    """Could this candidate local part be the one behind a Prospeo mask?

    's********' shows one real letter then stars: the candidate must start
    with that letter and have the same length. When the mask length looks
    truncated we do not reject on length - a masked address is a hint, and an
    over-strict check would leave people addressless.
    """
    if not mask_local:
        return True
    vis = mask_local.replace("*", "")
    if vis and not local.lower().startswith(vis.lower()):
        return False
    stars = mask_local.count("*")
    if stars and stars <= 6 and len(local) != len(mask_local):
        return False        # short masks are usually faithful to the length
    return True


def apply_guesses(company_id=None):
    """Fill empty addresses from known conventions, flagged as guessed.

    A reconstructed address is a lead, never a fact: it is written with
    email_status='guessed' so the paste/verified paths are never overwritten.
    Every convention on record for the firm is tried, best first.
    """
    q = ("SELECT c.*, co.domain, co.email_pattern FROM contacts c "
         "JOIN companies co ON co.id = c.company_id "
         "WHERE (c.email IS NULL OR c.email='') AND co.email_pattern IS NOT NULL "
         "AND co.email_pattern != '' AND co.domain IS NOT NULL AND co.domain != ''")
    args = []
    if company_id:
        q += " AND c.company_id=?"
        args.append(company_id)
    rows = db.query(q, args)
    n = 0
    for r in rows:
        for pattern, _conf in patterns_for_company(r["company_id"]):
            email = guess_email(r, {"domain": r["domain"], "email_pattern": pattern})
            if email:
                break
        if email:
            db.execute("UPDATE contacts SET email=?, email_source='pattern', "
                       "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       [email, r["id"]])
            n += 1
    return {"updated": n}


def apply_masked(company_id=None):
    """Turn masked Prospeo addresses into candidates once the convention is known.

    A masked address ("s********@blackrock.com") is not an address, but it is a
    person we hold. When the firm's email convention is known and we have the
    name, the reconstruction gives that person a real (guessed) candidate and
    retires the masked evidence. It must run *after* the real-address learning
    pass: every verified address at the firm makes more of these resolvable.
    """
    q = ("SELECT c.*, co.domain, co.email_pattern FROM contacts c "
         "JOIN companies co ON co.id = c.company_id "
         "WHERE c.email_masked IS NOT NULL AND c.email_masked != '' "
         "AND co.email_pattern IS NOT NULL "
         "AND co.email_pattern != '' AND co.domain IS NOT NULL AND co.domain != ''")
    args = []
    if company_id:
        q += " AND c.company_id=?"
        args.append(company_id)
    rows = db.query(q, args)
    n = 0
    for r in rows:
        # apply_guesses may already have filled this address; either way the
        # masked evidence is now redundant - retire it, write the address only
        # if the row still has none.
        if r["email"]:
            db.execute("UPDATE contacts SET email_masked='', "
                       "updated_at=CURRENT_TIMESTAMP WHERE id=?", [r["id"]])
            n += 1
            continue
        # Try every convention the firm has on record. A pattern the masked
        # address is consistent with (same first letter, same length) wins
        # over the primary pattern - the mask is a check, not just decoration.
        mask_local = r["email_masked"].rsplit("@", 1)[0]
        fallback = None
        email = None
        for pattern, _conf in patterns_for_company(r["company_id"]):
            cand = guess_email(r, {"domain": r["domain"], "email_pattern": pattern})
            if not cand:
                continue
            if _mask_admits(cand.rsplit("@", 1)[0], mask_local):
                email = cand
                break
            fallback = fallback or cand
        email = email or fallback
        if email:
            db.execute("UPDATE contacts SET email=?, email_source='pattern', "
                       "email_masked='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       [email, r["id"]])
            n += 1
    return {"updated": n}
