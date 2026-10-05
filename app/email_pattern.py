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
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

# Characters Unicode will not decompose on its own, so they are folded by hand.
_FOLD = {"ø": "o", "œ": "oe", "æ": "ae", "ß": "ss", "ł": "l", "đ": "d",
         "ð": "d", "þ": "th", "ı": "i", "ŧ": "t"}


def _fold(s):
    """ASCII-fold a name: 'Zünd' -> 'zund'.

    The local part of an SMTP address is ASCII. 'czünd@bamfunds.com' is not a
    deliverable address, it is a bounce with extra steps - so every name is
    folded before it is ever turned into one.
    """
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return "".join(_FOLD.get(ch, ch) for ch in t.lower())

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

# Firms whose convention we could not learn from your data because you hold no
# verified address there - only a masked one. Rather than leave those people
# unreachable, the convention was looked up on the public email-format
# directories (LeadIQ), keyed by the domain taken from the masked address
# itself, which is observed fact rather than inference.
#
# `confidence` is that directory's own stated share for this pattern, so it is
# honest about "68% flast, 27% first.last" firms. Everything stored through
# this table keeps email_source='pattern', so the mailer still refuses to send
# it without an explicit confirmation.
RESEARCHED = {
    # domain            (pattern,      conf, example)
    "qube-rt.com":          ("first.last", 0.97, "John.Doe@qube-rt.com"),
    "bamfunds.com":         ("flast",      0.68, "JDoe@bamfunds.com"),
    "bnpparibas-am.com":    ("first.last", 0.96, "John.Doe@bnpparibas.com"),
    "columbiathreadneedle.com": ("first.last", 0.96, "John.Doe@columbiathreadneedle.com"),
    "veritionfund.com":     ("flast",      0.74, "JDoe@veritionfund.com"),
    "squarepoint-capital.com": ("first.last", 0.97, "John.Doe@squarepoint-capital.com"),
    "bluebay.com":          ("flast",      0.95, "JDoe@bluebay.com"),
    "pharo.com":            ("flast",      0.77, "JDoe@pharo.com"),
    "statestreet.com":      ("flast",      0.89, "JDoe@statestreet.com"),
    "apollo.com":           ("flast",      0.93, "JDoe@apollo.com"),
}

RESEARCH_SOURCE = "leadiq public email-format page"


def _alpha(s):
    return "".join(ch for ch in _fold((s or "").strip()) if ch.isalpha())


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


def _mask_exact(local, mask_local):
    """The strict reading of a mask: every visible character must match.

    There is deliberately no length check. `_mask_admits` measured 417 length
    disagreements against 13 agreements, so a length test here would reject
    correct conventions almost always and teach the wrong lesson. All that is
    left is the visible prefix - which is why `pattern_from_masked` below refuses
    to establish a convention from masks at all.
    """
    if not mask_local:
        return True
    vis = mask_local.replace("*", "")
    return not vis or local.lower().startswith(vis.lower())


def pattern_from_masked(people):
    """Infer a firm's convention from its masked addresses alone.

    `people` is a list of (first, last, masked_local). Returns
    (pattern, confidence, n_agreeing) or None.

    Two rules keep this honest, and they are why it refuses so much:

    * **One pattern must explain EVERY masked address at the firm.** A pattern that
      fits some of them is a coincidence, not a convention.
    * **Several patterns agreeing means we do not know.** With no ground-truth
      address anywhere, ambiguity cannot be resolved by preference - only by
      refusing. The length check does most of the work and we cannot prove the
      mask preserves length, so uniqueness is the safety net: if length were
      noise, most firms come out ambiguous and are left alone.

    Confidence scales with how many independent people agree. One sample lands at
    0.5, below the project's 0.6 line, so it is never stored as a firm convention.
    """
    usable = [(f, l, m) for f, l, m in people if _alpha(f) and _alpha(l) and m]
    if not usable:
        return None
    winners = []
    for pattern, fn in PATTERNS.items():
        if all(_mask_exact(fn(_alpha(f), _alpha(l)) or "", m) for f, l, m in usable):
            winners.append(pattern)
    if len(winners) != 1:
        return None                      # no fit, or several: refuse, do not guess
    pattern = winners[0]
    n = len(usable)
    return pattern, (0.9 if n >= 3 else (0.75 if n == 2 else 0.5)), n


def _masked_people(company_id):
    """(first, last, masked_local) for everyone at a firm we hold only masked."""
    rows = db.query(
        "SELECT c.first_name, c.last_name, c.email_masked FROM contacts c "
        "WHERE c.company_id=? AND c.email_masked IS NOT NULL AND c.email_masked != '' "
        "AND (c.email IS NULL OR c.email = '')", [company_id])
    out = []
    for r in rows:
        local = r["email_masked"].rsplit("@", 1)[0]
        if local:
            out.append((r["first_name"], r["last_name"], local))
    return out


def learn_from_masked(company_id, min_people=2, dry_run=False):
    """Give one firm a convention from its masked addresses.

    Returns {pattern, confidence, n, upgraded}. Nothing is written below
    `min_people`: a single masked address is a hint, a convention is a rule.
    """
    rows = db.query("SELECT * FROM companies WHERE id=?", [company_id])
    if not rows:
        return {}
    firm = rows[0]
    if (firm.get("email_pattern") or "").strip():
        return {}                       # already known; a convention is never downgraded
    guess = pattern_from_masked(_masked_people(company_id))
    if not guess:
        return {}
    pattern, confidence, n = guess
    if n < min_people or dry_run:
        return {"pattern": pattern, "confidence": confidence, "n": n, "upgraded": False}
    return {"pattern": pattern, "confidence": confidence, "n": n,
            "upgraded": store(firm, pattern, confidence, "prospeo-masked",
                              sample=f"{pattern}@masked")}


def learn_all_from_masked(min_people=2, dry_run=False, verbose=True):
    """Sweep every firm with no convention, using only its masked evidence."""
    firms = db.query("SELECT id, name FROM companies WHERE (email_pattern IS NULL "
                     "OR email_pattern = '') AND domain IS NOT NULL AND domain != ''")
    learned = 0
    for f in firms:
        res = learn_from_masked(f["id"], min_people=min_people, dry_run=dry_run)
        if res.get("upgraded"):
            learned += 1
            if verbose:
                print(f"  {f['name']}: {res['pattern']} "
                      f"({res['n']} masked, confidence {res['confidence']})")
    return {"firms": len(firms), "learned": learned}


def learn_from_research(min_people=1, verbose=True):
    """Adopt a publicly documented convention for firms we hold no verified address at.

    The table is keyed by the masked domain, because that half is observed fact:
    we saw it on a real - if redacted - address at that firm. Several company rows
    carry a stale or wrong domain (balyasny.com vs the real bamfunds.com, for
    instance), so the row is corrected too; otherwise reconstruction would write
    the wrong half of the address and every mail would bounce at the door.
    """
    rows = db.query(
        "SELECT co.id, co.name, co.domain, "
        "substr(c.email_masked, instr(c.email_masked,'@')+1) AS mdom, COUNT(*) AS n "
        "FROM companies co JOIN contacts c ON c.company_id = co.id "
        "WHERE (co.email_pattern IS NULL OR co.email_pattern='') "
        "AND (c.email IS NULL OR c.email='') AND c.email_masked != '' "
        "GROUP BY co.id, mdom HAVING n >= ?", [min_people])
    applied = 0
    for row in rows:
        dom = clean_domain(row["mdom"])
        hit = RESEARCHED.get(dom)
        if not hit:
            continue                       # no lookup for this firm: stay honest
        pattern, conf, example = hit
        firm = db.query("SELECT * FROM companies WHERE id=?", [row["id"]])[0]
        if clean_domain(firm.get("domain")) != dom:
            db.execute("UPDATE companies SET domain=? WHERE id=?", [dom, row["id"]])
        if store(firm, pattern, conf, RESEARCH_SOURCE, sample=example):
            applied += 1
            if verbose:
                print(f"  {row['name']}: {pattern} @{dom} (confidence {conf:.0%})")
    return {"applied": applied, "considered": len(rows)}


def learn_from_base_rate(min_people=1, verbose=True):
    """Give a firm with no known convention the convention your own data implies.

    Measured across the 395 Hunter-verified addresses in this database:
    first.last 68%, flast 20%, firstlast 3%, f.last 2%. So for a firm where we
    found nothing, first.last is the honest best guess - a guess, and stored as
    one: confidence 0.681, source 'base-rate (default)', never a claimed fact.

    The masked first letter still acts as a veto afterwards (apply_masked
    refuses a candidate that does not start with the observed letter), which
    rules out the whole last-first family. It cannot separate first.last from
    flast - both begin with the first initial - so a share of these will bounce,
    and every one of them still requires an explicit confirmation to send.
    """
    rows = db.query(
        "SELECT co.id, co.name, substr(c.email_masked, instr(c.email_masked,'@')+1) AS mdom, "
        "COUNT(*) AS n FROM companies co JOIN contacts c ON c.company_id = co.id "
        "WHERE (co.email_pattern IS NULL OR co.email_pattern='') "
        "AND (c.email IS NULL OR c.email='') AND c.email_masked != '' "
        "GROUP BY co.id, mdom HAVING n >= ?", [min_people])
    applied = 0
    for row in rows:
        dom = clean_domain(row["mdom"])
        if not dom:
            continue
        firm = db.query("SELECT * FROM companies WHERE id=?", [row["id"]])[0]
        if clean_domain(firm.get("domain")) != dom:
            db.execute("UPDATE companies SET domain=? WHERE id=?", [dom, row["id"]])
        if store(firm, "first.last", 0.681, "base-rate (default)",
                 sample=f"john.doe@{dom}"):
            applied += 1
            if verbose:
                print(f"  {row['name']}: first.last @{dom} (base rate 68%)")
    return {"applied": applied, "considered": len(rows)}


def apply_masked_guesses(company_id=None, dry_run=False):
    """Fill ONE address from ONE masked address, without claiming a convention.

    This is the case the data is full of: one person, one masked address, and
    exactly one pattern that could hide behind it. The firm stays "unknown" - we
    learned one person's address, not a rule - but that person becomes writable.
    The address is written as a reconstruction ('pattern'), which the mailer
    already refuses to send without an explicit confirmation.
    """
    q = ("SELECT c.*, co.domain, co.email_pattern FROM contacts c "
         "JOIN companies co ON co.id = c.company_id "
         "WHERE c.email_masked IS NOT NULL AND c.email_masked != '' "
         "AND (c.email IS NULL OR c.email = '') "
         "AND co.domain IS NOT NULL AND co.domain != ''")
    args = []
    if company_id:
        q += " AND c.company_id=?"
        args.append(company_id)
    n = skipped = 0
    for r in db.query(q, args):
        if (r["email_pattern"] or "").strip():
            continue                     # a known convention is apply_masked()'s job
        mask_local, mask_domain = r["email_masked"].rsplit("@", 1)
        # The masked address observed this person's real domain. Several firm rows
        # carry a different, wrong one (balyasny.com vs bamfunds.com), and
        # reconstructing onto that would send every mail to a domain nobody uses.
        domain = clean_domain(mask_domain) or clean_domain(r["domain"])
        f, l = _alpha(r["first_name"]), _alpha(r["last_name"])
        if not f or not l or not domain:
            continue
        fits = [p for p, fn in PATTERNS.items() if _mask_exact(fn(f, l) or "", mask_local)]
        if len(fits) != 1:
            skipped += 1                # nothing fits, or several do: refuse
            continue
        email = f"{PATTERNS[fits[0]](f, l)}@{domain}"
        if dry_run:
            n += 1
            continue
        db.execute("UPDATE contacts SET email=?, email_source='pattern', "
                   "email_masked='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   [email, r["id"]])
        db.execute("INSERT OR REPLACE INTO pattern_evidence (company_id, pattern, "
                   "pattern_before, pattern_after, sample_email, source, confidence, notes) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   [r["company_id"], fits[0], fits[0], domain, email, "masked-single",
                    0.5, "one masked address; the address is a reconstruction, not a fact"])
        n += 1
    return {"updated": n, "skipped_ambiguous": skipped}


def _mask_admits(local, mask_local):
    """Could this candidate local part be the one behind a Prospeo mask?

    ONLY the visible characters are evidence. The length is not: measured against
    430 real addresses at firms whose convention we already knew, the mask length
    disagreed with the true local part 417 times and matched only 13. Prospeo
    truncates the mask to a handful of stars, so `g****` can hide `gaurav.sonar`.

    That is not a small correction. This function used to reject any candidate
    whose length differed when the mask had 6 or fewer stars, which threw away 189
    of 430 correct candidates - and `apply_masked` then fell back to the firm's
    convention anyway, so the check silently bought nothing and cost half the
    evidence it was meant to weigh.

    What remains is the first letter, which matched 427 of 430. That is enough to
    rule a candidate out and far too little to rule one in - which is precisely why
    a masked address can confirm a convention but never establish one.
    """
    if not mask_local:
        return True
    vis = mask_local.replace("*", "")
    return not vis or local.lower().startswith(vis.lower())


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
        mask_local, mask_domain = r["email_masked"].rsplit("@", 1)
        # observed domain wins over the firm row: see learn_from_research()
        domain = clean_domain(mask_domain) or clean_domain(r["domain"])
        fallback = None
        email = None
        for pattern, _conf in patterns_for_company(r["company_id"]):
            cand = guess_email(r, {"domain": domain, "email_pattern": pattern})
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
