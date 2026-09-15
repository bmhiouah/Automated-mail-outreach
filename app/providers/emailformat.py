"""Email-format.com provider: the public convention directory.

Some mail conventions are public information - sites like email-format.com
publish them per domain ("first_name . last_name 82%"). The harvester's two
metered providers teach us conventions only as a side effect; this source
goes straight at them, free, one page per domain:

    https://www.email-format.com/d/<domain>/

Each page carries, in plain HTML:
  - "Identified Name Formats": (descriptor, share %, one decoded example),
    e.g. "first_name . last_name 82%" with example "John.Smith@gs.com".
    Anything under 20% share is a minority variant, not the convention.
  - "Representative Email Addresses": sample addresses (obfuscated, decodable,
    exactly like the format examples).

The example addresses are Cloudflare-obfuscated ("[email protected]" plus a
`data-cfemail` hex attribute): key byte + XORed bytes. Decoding is a dozen
lines of stdlib - no key, no credits.

Mapping to this app's vocabulary (email_pattern.PATTERNS): the page writes
"first_name", "last_name" and "first_initial"/"last_initial" with the literal
separator between them; the mapping table below turns those into our names.

One throttle per host, the same ledger as the metered sources (cost 0), every
payload kept raw: free does not mean unaccountable.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db      # noqa: E402
import net     # noqa: E402

BASE = "https://www.email-format.com"
PROVIDER = "emailformat"
ENDPOINT = "domain-page"

# Minimum share for a format to be treated as the firm's convention rather
# than a minority variant. email-format.com itself prints "high confidence"
# only above ~50%; we take 20% so genuine dual-format firms keep both, while
# the 1-3% long tail stays evidence, not canon.
MIN_SHARE = 20.0

# Their words -> ours. The descriptors always use "first_name", "last_name",
# "first_initial", "last_initial" and the separator between them.
DESCRIPTOR_MAP = {
    "first_name.last_name": "first.last",
    "first_namelast_name": "firstlast",
    "first_name-last_name": "first-last",
    "first_namelast_initial": "firstl",
    "first_initiallast_name": "flast",
    "first_initial.last_name": "f.last",
    "last_namefirst_initial": "lastf",
    "last_name.first_name": "last.first",
    "last_namefirst_name": "lastfirst",
    "first_name": "first",
    "last_name": "last",
}

CREDIT_COST = {ENDPOINT: 0.0}


def _cfdecode(hexstr):
    """Decode a Cloudflare data-cfemail attribute: first byte is the XOR key."""
    try:
        raw = bytes.fromhex(hexstr)
    except (ValueError, TypeError):
        return ""
    key, data = raw[0], raw[1:]
    return "".join(chr(b ^ key) for b in data)


def map_descriptor(descriptor):
    """Turn 'first_name . last_name' into 'first.last'. None if unmapped."""
    if not descriptor:
        return None
    words = re.findall(r"[a-z]+(?:_[a-z]+)?", descriptor.lower())
    if len(words) == 1:
        return DESCRIPTOR_MAP.get(words[0])
    if len(words) == 2:
        first, last = words
        # the separator is whatever sits between the two words on the page
        i = descriptor.lower().find(first) + len(first)
        j = descriptor.lower().find(last, i)
        sep = descriptor[i:j].strip() or ""
        return DESCRIPTOR_MAP.get(first + sep + last)
    return None


def parse_domain_page(html, domain):
    """Extract formats and samples from one /d/<domain> page.

    Returns {"ok", "formats", "best", "samples"}. Formats are in page order
    (best first); descriptors this app cannot speak are kept with pattern
    None so the caller can report what it skipped.
    """
    html = html or ""
    formats = []
    # one block per format: descriptor, share %, one cfencoded example
    for raw, share, cf in re.findall(
            r"format fl'>(.*?)</div>.*?confidence_value fl'>(\d+)%.*?"
            r"data-cfemail=\"([0-9a-fA-F]+)\"", html, re.S):
        descriptor = " ".join(raw.split())
        example = _cfdecode(cf)
        try:
            share = float(share)
        except ValueError:
            share = 0.0
        formats.append({"descriptor": descriptor,
                        "pattern": map_descriptor(descriptor),
                        "share": share, "example": example})
    samples, seen = [], set()
    for cf in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html):
        email = _cfdecode(cf).strip().lower()
        if "@" in email and "@" + (domain or "").lower() in email \
                and email not in seen:
            seen.add(email)
            samples.append(email)
    confirmed = [f for f in formats if f["pattern"]]
    best = max(confirmed, key=lambda f: f["share"]) if confirmed else None
    return {"ok": True, "formats": formats, "best": best, "samples": samples}


def _fetch(domain, use_cache=True, force=False):
    """One page fetch: cache-first through the fetch ledger, cost 0."""
    request_key = f"domain={domain}"
    if use_cache and not force:
        seen = db.fetch_seen(PROVIDER, ENDPOINT, request_key)
        if seen and seen["ok"] and seen.get("raw_path"):
            full = os.path.join(db.BASE, seen["raw_path"])
            if os.path.exists(full):
                with open(full, "r", encoding="utf-8") as fh:
                    return fh.read(), dict(seen, cached=True)
    url = f"{BASE}/d/{domain}/"
    res = net.request_json(url, headers={"Accept": "text/html"},
                           throttle=True, retries=2)
    if not res.get("ok") or not res.get("text"):
        row = db.record_fetch(PROVIDER, ENDPOINT, request_key, res.get("status"),
                              credits=0, ok=False, error=res.get("error"))
        return "", dict(row, cached=False)
    text = res["text"]
    rel, digest = db.save_raw(PROVIDER, ENDPOINT, request_key, res["body"])
    row = db.record_fetch(PROVIDER, ENDPOINT, request_key, res.get("status"),
                          credits=0, ok=True, raw_path=rel, response_hash=digest)
    return text, dict(row, cached=False)


def domain_conventions(domain, force=False):
    """Fetch one domain's page and parse it. Free, logged, cached."""
    html, row = _fetch((domain or "").strip().lower(), force=force)
    if not html:
        return {"ok": False, "status": row.get("status"),
                "error": row.get("error") or "no page",
                "cached": row.get("cached", False), "credits": 0.0}
    parsed = parse_domain_page(html, domain)
    parsed.update(cached=row.get("cached", False), credits=0.0)
    return parsed


def apply_to_company(company, parsed):
    """Store a parsed page on the firm. Returns {"learned", "skipped", ...}.

    The majority format teaches companies.email_pattern (primary); every
    mapped format - majority or minority - is recorded in pattern_evidence
    with its real example address. Samples are matched against contacts at
    the firm; naming pairs are the strongest evidence there is.
    """
    from email_pattern import store, learn_from_person, clean_domain  # noqa: E402
    learned, skipped = [], []
    if not parsed.get("ok"):
        return {"learned": learned, "skipped": skipped,
                "reason": parsed.get("error") or "no page"}
    domain = (company.get("domain") or "").strip()
    for f in parsed.get("formats") or []:
        if not f["pattern"]:
            skipped.append({"descriptor": f["descriptor"],
                            "reason": "not in this app's pattern vocabulary"})
            continue
        confidence = min(0.95, 0.5 + f["share"] / 200.0)
        if f["share"] >= MIN_SHARE:
            primary = bool(store(company, f["pattern"], confidence,
                                 f"email-format.com ({f['share']:.0f}% share)",
                                 f["example"]))
            learned.append({"pattern": f["pattern"], "share": f["share"],
                            "primary": primary})
        else:
            after = clean_domain(f["example"].rsplit("@", 1)[1]) \
                if "@" in (f["example"] or "") else clean_domain(domain)
            db.execute(
                "INSERT INTO pattern_evidence (company_id, pattern, pattern_before, "
                "pattern_after, sample_email, source, confidence, notes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [company["id"], f["pattern"], f["pattern"], after, f["example"],
                 f"email-format.com ({f['share']:.0f}% share)",
                 confidence, "minority variant, evidence only"])
            learned.append({"pattern": f["pattern"], "share": f["share"],
                            "primary": False})
    for sample in parsed.get("samples") or []:
        name_hit = db.query(
            "SELECT first_name, last_name FROM contacts WHERE company_id=? "
            "AND lower(?) LIKE '%'||lower(first_name)||'%' "
            "AND lower(?) LIKE '%'||lower(last_name)||'%'",
            [company["id"], sample, sample])
        if name_hit:
            c = name_hit[0]
            learn_from_person(c["first_name"], c["last_name"], sample, company)
    fresh = db.query("SELECT * FROM companies WHERE id=?", [company["id"]])
    return {"learned": learned, "skipped": skipped,
            "pattern": (fresh[0].get("email_pattern") if fresh else "") or ""}
