"""Turn a pasted blob of people text into contact candidates.

You never get clean CSV out of LinkedIn or a firm's team page - you get a blob.
This module reads that blob and proposes one record per person, each carrying
the raw line it came from so you can check it before saving anything.

Nothing is invented. A line that cannot be read confidently lands in
`unparsed` with a reason rather than being guessed at.

    from people_parse import parse_people
    out = parse_people(text, default_company="Jane Street", resolve=db.resolve_company)
    out["candidates"]      # reviewable records
    out["unparsed"]        # lines we refused to guess at
    out["pattern_samples"] # real addresses with no name - feed the pattern engine
"""
import re

# ------------------------------------------------------------------ vocabulary
# Multi-word entries matter: a naive .split() would shred "united kingdom" into
# two tokens and silently break every location check.

TITLE_WORDS = frozenset("""
analyst analyste researcher research trader developer engineer manager director
partner associate assistant intern internship graduate strategist structurer
quant quantitative scientist portfolio head chief officer president founder
consultant specialist lead vp vice managing executive sales risk compliance
operations product software data trading desk deskhead structuration
stagiaire ingenieur ingénieur chercheur gestionnaire directeur responsable
student summer campus phd doctorate professor lecturer fellow
""".split())

CREDENTIALS = frozenset("""
cfa cfp caia frm cpa aca acca cim mba msc bsc ba bs ma ms meng mfe mfin phd
dphil mphil mres llm jd md pe pmp
""".split())

PARTICLES = frozenset("""
de van von del della di da la le el al bin ibn ter ten op den der dos du
st saint san mac mc o
""".split())

COUNTRIES = frozenset([
    "france", "united kingdom", "uk", "england", "scotland", "wales", "ireland",
    "germany", "switzerland", "netherlands", "belgium", "spain", "italy",
    "portugal", "luxembourg", "sweden", "norway", "denmark", "finland",
    "austria", "poland", "greece", "czech republic", "hungary", "romania",
    "bulgaria", "united states", "usa", "us", "canada", "singapore",
    "hong kong", "japan", "australia", "india", "china", "uae",
    "united arab emirates", "dubai", "qatar", "saudi arabia", "israel",
    "brazil", "mexico", "south africa", "turkey", "russia", "jersey",
    "guernsey", "switzerland",
])

REGIONS = frozenset([
    "ile-de-france", "île-de-france", "greater london", "england", "scotland",
    "new york", "california", "massachusetts", "illinois", "texas",
    "connecticut", "new jersey", "pennsylvania", "florida", "ontario",
    "quebec", "bavaria", "zurich", "zürich", "geneva", "vaud",
    "north holland", "south holland", "catalonia", "madrid", "lombardy",
    "lazio", "berkshire", "surrey", "kent", "essex", "ile de france",
])

CITIES = frozenset([
    "paris", "london", "new york", "zurich", "zürich", "geneva", "amsterdam",
    "frankfurt", "dublin", "singapore", "tokyo", "boston", "chicago",
    "greenwich", "stamford", "jersey city", "luxembourg", "madrid", "milan",
    "stockholm", "copenhagen", "oslo", "dubai", "sydney", "hong kong",
    "munich", "berlin", "vienna", "brussels", "lisbon", "barcelona",
    "edinburgh", "manchester", "lyon",
])

NOISE_LINES = frozenset([
    "follow", "message", "connect", "pending", "save", "more", "see more",
    "show all", "show less", "load more", "experience", "education", "skills",
    "about", "activity", "posts", "people also viewed", "people you may know",
    "interests", "contact info", "sign in", "join now", "linkedin", "home",
    "my network", "jobs", "messaging", "notifications", "me", "for business",
    "try premium", "settings", "help", "licenses & certifications",
    "recommendations", "endorsements", "open to work", "provide services",
    "hire", "search", "filter", "sort by", "clear", "next", "previous",
    "details", "summary", "highlights", "featured", "volunteering",
])

NOISE_RE = re.compile(
    r"^(?:"
    r"\d[\d,.\s]*\+?\s*(?:connections?|followers?|results?|mutual)"
    r"|\d+\s*(?:yr|yrs|year|years|mo|mos|month|months)\b"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}\b"
    r"|\d{1,2}(?:st|nd|rd|th)"
    r"|[-–—·|\s]+"
    r")",
    re.I,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9\-_%.]+", re.I)
DEGREE_RE = re.compile(r"\s*·\s*(?:1st|2nd|3rd|following|connected|pending)\b.*$", re.I)
PAREN_RE = re.compile(r"\s*\((?:she|he|they)/[a-z/]+\)", re.I)
HONORIFIC_RE = re.compile(r"^(?:dr|mr|mrs|ms|miss|prof|sir|dame)\.?\s+", re.I)
WORD_RE = re.compile(r"[a-zà-ÿ0-9']+")

# Whitespace except tab: tabs are structural separators and must survive.
_WS_RE = re.compile(r"[^\S\t]+")


def _norm(s):
    return _WS_RE.sub(" ", (s or "").replace("\u00a0", " ")).strip()


def _tokens(s):
    return WORD_RE.findall((s or "").lower())


# ------------------------------------------------------------------ classifiers

def is_name(part):
    """True if `part` reads like a human name, not a title or a company."""
    p = _norm(part)
    if not p or "@" in p or any(ch.isdigit() for ch in p):
        return False
    p = HONORIFIC_RE.sub("", p)
    p = PAREN_RE.sub("", p)
    p = _strip_credentials(p)
    tokens = [t for t in p.split() if t]
    if not 2 <= len(tokens) <= 5:
        return False
    real = [t for t in tokens if t.strip(".,'’").lower() not in PARTICLES]
    if len(real) < 2:
        return False
    for t in tokens:
        core = t.strip(".,'’()")
        if not core:
            return False
        if core.lower() in TITLE_WORDS:
            return False
        if core.lower() not in PARTICLES and not core[0].isupper():
            return False
    return True


def _strip_credentials(p):
    """'Jane Doe, CFA, FRM' -> 'Jane Doe'. Leaves other commas alone.

    Rebuilding with a plain space here shredded 'Paris, Ile-de-France, France'
    into 'Paris Ile-de-France France', which then read as a person's name.
    """
    pieces = [x.strip() for x in p.split(",")]
    popped = False
    while len(pieces) > 1 and pieces[-1].lower().rstrip(".") in CREDENTIALS:
        pieces.pop()
        popped = True
    return ", ".join(pieces) if popped else p


def is_title(part):
    p = _norm(part).lower()
    if not p:
        return False
    return any(w in TITLE_WORDS for w in _tokens(p))


def is_location(part):
    """True if `part` looks like 'Paris, Ile-de-France, France'."""
    p = _norm(part)
    if not p or is_title(p):
        return False
    pieces = [x.strip().lower() for x in p.split(",") if x.strip()]
    if not pieces or len(pieces) > 4:
        return False
    if all(x in COUNTRIES or x in REGIONS or x in CITIES for x in pieces):
        return True
    # one recognisable piece plus unknowns is still a location line
    hits = sum(1 for x in pieces if x in COUNTRIES or x in REGIONS or x in CITIES)
    return hits >= 1 and len(pieces) == 1


def is_email(part):
    return bool(EMAIL_RE.search(_norm(part)))


def is_url(part):
    return bool(LINKEDIN_RE.search(_norm(part)))


def company_match(chunk, resolve):
    """Resolve `chunk` to a known firm, but only on a clean match.

    The resolver does substring matching, which is far too eager here. Two
    traps it sets:

      * 'Quantitative Developer Marshall Wace' resolves to Marshall Wace and
        swallows the job title.
      * Bare desk words resolve to firms - 'trading' -> Jump Traders,
        'research' -> Qube Research, 'portfolio' -> IPM Informed Portfolio
        Management - so a desk label would silently become a company.

    So: the firm's own tokens must cover most of the chunk, and a single-token
    chunk must match a single-token firm exactly. Under-matching is fine - the
    raw text is kept and flagged for review. Over-matching is silent and wrong.
    """
    if not resolve:
        return None
    hit = resolve(chunk)
    if not hit:
        return None
    cparts = set(_tokens(hit.get("name") or ""))
    tparts = set(_tokens(chunk))
    if not cparts or not tparts:
        return None
    if len(tparts) == 1:
        return hit if tparts == cparts else None
    if len(cparts & tparts) / len(tparts) >= 0.75:
        return hit
    return None


def strip_decorations(line):
    """Remove LinkedIn chrome from a single line, keeping the real content."""
    s = _norm(line)
    s = DEGREE_RE.sub("", s)
    s = PAREN_RE.sub("", s)
    s = HONORIFIC_RE.sub("", s)
    s = _strip_credentials(s)
    return _norm(s).strip(" -–—|,")


def _clean_lines(text):
    out = []
    for raw in (text or "").splitlines():
        s = strip_decorations(raw)
        if not s:
            continue
        if s.lower() in NOISE_LINES:
            continue
        if NOISE_RE.match(s) and not is_name(s):
            continue
        out.append(s)
    return out


# ------------------------------------------------------------------ splitting

SPLIT_ORDER = [
    ("tab", re.compile(r"\t+")),
    ("pipe", re.compile(r"\s*\|\s*")),
    ("semicolon", re.compile(r"\s*;\s*")),
    ("dash", re.compile(r"\s+[–—]\s+")),
    ("hyphen", re.compile(r"\s+-\s+")),
    ("at", re.compile(r"\s+(?:at|@|chez)\s+", re.I)),
]


def split_chunks(text):
    """Break a record into its parts, keeping every part separate.

    Joining the tail back together was a bug: 'Anaïs Lefèvre — Quantitative
    Developer — Marshall Wace' collapsed into a name plus one blob, and the
    blob then resolved to Marshall Wace, silently dropping the job title.
    """
    for name, rx in SPLIT_ORDER:
        if name == "at":
            m = rx.search(text)
            if m and text[:m.start()].strip() and text[m.end():].strip():
                return [text[:m.start()].strip(), text[m.end():].strip()]
            continue
        parts = [p.strip() for p in rx.split(text) if p.strip()]
        if len(parts) >= 2:
            return parts
    # Comma is only safe when the line opens with a name, otherwise
    # 'Analyst, Rates, BNP Paribas' would be chopped into nonsense.
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2 and is_name(parts[0]):
        return parts
    return [text]


def peel_name(text, max_tokens=3):
    """'Tom Baker Trader Citadel' -> ('Tom Baker', 'Trader Citadel')."""
    tokens = _norm(text).split()
    for n in range(min(max_tokens, len(tokens) - 1), 1, -1):
        head = " ".join(tokens[:n])
        if is_name(head):
            return head, " ".join(tokens[n:])
    return "", text


def peel_company(text, resolve, max_tokens=4):
    """'Trader Citadel' -> ('Trader', <Citadel>)."""
    if not resolve:
        return text, None
    tokens = _norm(text).split()
    for n in range(min(max_tokens, len(tokens)), 0, -1):
        tail = " ".join(tokens[-n:])
        hit = company_match(tail, resolve)
        if hit:
            return " ".join(tokens[:-n]), hit
    return text, None


def _slug_to_name(url):
    """linkedin.com/in/jane-doe-1a2b3c -> 'Jane Doe'."""
    m = re.search(r"linkedin\.com/(?:in|pub)/([A-Za-z0-9\-_%]+)", url or "", re.I)
    if not m:
        return ""
    slug = m.group(1).split("?")[0]
    slug = re.sub(r"-[0-9a-f]{4,}$", "", slug, flags=re.I)
    words = [w for w in re.split(r"[-_]", slug) if w and not w.isdigit()]
    if not words or len(words) > 4:
        return ""
    return " ".join(w.capitalize() for w in words)


def _assign(chunks, fields, resolve, allow_name=True):
    """Sort chunks into fields. Returns whatever could not be classified.

    Order matters. A known firm is checked before the name test because
    'Jane Street' passes is_name() perfectly well - it is two capitalised
    words with no title keyword. The firm list is the only thing that can
    tell the two apart, so it gets first refusal.
    """
    rest = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        hit = company_match(chunk, resolve)
        if hit and not fields["company_name"]:
            fields["company_name"] = hit["name"]
            continue
        if allow_name and not fields["name"] and is_name(chunk):
            fields["name"] = chunk
            continue
        if is_location(chunk) and not fields["city"]:
            fields["city"] = _norm(chunk.split(",")[0])
            continue
        if is_title(chunk) and not fields["job_title"]:
            # 'Quantitative Researcher Two Sigma' is one title chunk with the
            # firm glued on the end. Peel it off before storing the title.
            head, hit = peel_company(chunk, resolve)
            if hit and not fields["company_name"] and is_title(head):
                fields["company_name"] = hit["name"]
                chunk = head.strip()
            if chunk and not fields["job_title"]:
                fields["job_title"] = chunk
            continue
        rest.append(chunk)
    return rest


def read_line(line, resolve=None, allow_name=True):
    """Read one line into a field dict. Returns None if nothing usable."""
    text = strip_decorations(line)
    if not text:
        return None

    email = ""
    m = EMAIL_RE.search(text)
    if m:
        email = m.group(0)
        text = _norm(text.replace(email, " "))

    url = ""
    m = LINKEDIN_RE.search(text)
    if m:
        url = m.group(0)
        if not url.lower().startswith("http"):
            url = "https://" + url
        text = _norm(text.replace(m.group(0), " "))

    text = _norm(text).strip(" ,|;-\t")
    fields = {"name": "", "job_title": "", "company_name": "", "city": "",
              "email": email, "linkedin_url": url, "kind": "other", "extra": []}

    if not text:
        if url:
            fields["name"] = _slug_to_name(url)
        if fields["name"]:
            fields["kind"] = "person"
            return fields
        if email or url:
            return fields
        return None

    # A line that is purely a place is context for the person above it.
    if is_location(text):
        fields["city"] = _norm(text.split(",")[0])
        fields["kind"] = "location"
        return fields

    chunks = split_chunks(text)

    # A line with no separators at all - 'Tom Baker Trader Citadel' - needs the
    # firm and the name peeled off before the middle can be read as a title.
    if (allow_name and len(chunks) == 1 and not is_name(chunks[0])
            and not company_match(chunks[0], resolve)):
        head, hit = peel_company(chunks[0], resolve)
        if hit:
            fields["company_name"] = hit["name"]
        head, tail = peel_name(head)
        if head:
            fields["name"] = head
            chunks = [tail] if tail else []

    rest = _assign(chunks, fields, resolve, allow_name)

    # 'Tom Baker Trader Citadel' has no separators, so it arrives as one chunk
    # and is rejected as a name because of the word 'Trader'. Peel the name off
    # the front by brute force and re-read what is left.
    if allow_name and not fields["name"]:
        for candidate in (" ".join(rest), text):
            candidate = candidate.strip()
            if not candidate:
                continue
            head, tail = peel_name(candidate)
            if not head:
                continue
            fields["name"] = head
            fields["extra"] = []
            rest = _assign(split_chunks(tail), fields, resolve, allow_name=False) if tail else []
            break

    if rest:
        blob = " ".join(rest).strip()
        head, hit = peel_company(blob, resolve)
        if hit and not fields["company_name"]:
            fields["company_name"] = hit["name"]
            blob = head.strip()
        if blob and not fields["job_title"] and is_title(blob):
            fields["job_title"] = blob
        elif blob and not fields["company_name"]:
            fields["company_name"] = blob
        elif blob:
            fields["extra"] = [blob]

    if not fields["name"] and fields["linkedin_url"]:
        fields["name"] = _slug_to_name(fields["linkedin_url"])
    if fields["name"]:
        fields["kind"] = "person"
    elif not any(fields[k] for k in ("job_title", "company_name", "city", "email")):
        return None
    return fields


# ------------------------------------------------------------------ assembling

def split_name(full):
    """'Willem de Vries' -> ('Willem', 'de Vries')."""
    tokens = [t for t in _norm(full).split() if t]
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], ""
    if len(tokens) == 2:
        return tokens[0], tokens[1]
    for i in range(1, len(tokens) - 1):
        if tokens[i].strip(".,'’").lower() in PARTICLES:
            return " ".join(tokens[:i]), " ".join(tokens[i:])
    return tokens[0], " ".join(tokens[1:])


def _confidence(fields, resolved, default_company):
    score = 0.40
    if fields["job_title"]:
        score += 0.20
    if fields["company_name"]:
        score += 0.25 if resolved else 0.12
    elif default_company:
        score += 0.15
    if fields["email"]:
        score += 0.10
    if fields["linkedin_url"]:
        score += 0.05
    if fields["city"]:
        score += 0.02
    return round(min(score, 0.98), 2)


def _evidence(fields, resolved):
    bits = ["name"]
    if fields["job_title"]:
        bits.append("title")
    if fields["company_name"]:
        bits.append("company" + (" (known firm)" if resolved else " (unrecognised)"))
    if fields["email"]:
        bits.append("real address found")
    if fields["linkedin_url"]:
        bits.append("LinkedIn URL")
    if fields["city"]:
        bits.append("location")
    return ", ".join(bits)


def parse_people(text, default_company=None, resolve=None):
    """Parse a paste into reviewable candidates. Never writes to the database."""
    lines = _clean_lines(text)
    if not lines:
        return {"candidates": [], "unparsed": [], "pattern_samples": [],
                "stats": {"lines": 0, "people": 0, "unparsed": 0, "with_email": 0,
                          "known_firms": 0, "merged": 0}}

    read = []
    for ln in lines:
        f = read_line(ln, resolve)
        if f:
            read.append((ln, f))

    # Stitch 'name' + 'title at company' pairs (a LinkedIn profile paste).
    # The second line is 'other', not 'person' - it has a title and a firm but
    # no name - so the test is on its contents, not on its kind.
    stitched = []
    i = 0
    while i < len(read):
        ln, f = read[i]
        if f["kind"] == "person" and not (f["job_title"] or f["company_name"]):
            if i + 1 < len(read):
                ln2, f2 = read[i + 1]
                if not f2["name"] and (f2["job_title"] or f2["company_name"] or f2["city"]):
                    merged = dict(f)
                    for k in ("job_title", "company_name", "city"):
                        merged[k] = merged[k] or f2[k]
                    merged["email"] = merged["email"] or f2["email"]
                    merged["linkedin_url"] = merged["linkedin_url"] or f2["linkedin_url"]
                    stitched.append((ln + " / " + ln2, merged))
                    i += 2
                    continue
        stitched.append((ln, f))
        i += 1

    # Absorb a following location line as the city of the person above it.
    absorbed = []
    for ln, f in stitched:
        if f["kind"] == "location" and absorbed and absorbed[-1][1]["kind"] == "person":
            if not absorbed[-1][1]["city"]:
                absorbed[-1][1]["city"] = f["city"]
                continue
        absorbed.append((ln, f))

    candidates, unparsed, samples = [], [], []
    for ln, f in absorbed:
        if f["kind"] == "location":
            continue  # context, not a person - do not report as a failure
        if not f["name"]:
            if f["email"]:
                samples.append({"email": f["email"], "line": ln})
            else:
                unparsed.append({"line": ln, "reason": "no name found"})
            continue
        first, last = split_name(f["name"])
        if not first:
            unparsed.append({"line": ln, "reason": "name could not be split"})
            continue
        company = f["company_name"] or default_company or ""
        resolved = company_match(company, resolve) if company else None
        candidates.append({
            "first_name": first,
            "last_name": last,
            "full_name": _norm(f["name"]),
            "job_title": f["job_title"],
            "company_name": resolved["name"] if resolved else company,
            "company_id": resolved["id"] if resolved else None,
            "company_known": bool(resolved),
            "city": f["city"],
            "email": f["email"],
            "linkedin_url": f["linkedin_url"],
            "confidence": _confidence(f, bool(resolved), default_company),
            "evidence": _evidence(f, bool(resolved)),
            "raw": ln,
        })
        if f["email"]:
            samples.append({"email": f["email"], "line": ln,
                            "person": f["name"], "company_name":
                            resolved["name"] if resolved else company})

    seen = {}
    for c in candidates:
        key = (c["first_name"].lower(), c["last_name"].lower(),
               (c["company_name"] or "").lower())
        if key not in seen or c["confidence"] > seen[key]["confidence"]:
            seen[key] = c

    return {
        "candidates": list(seen.values()),
        "unparsed": unparsed,
        "pattern_samples": samples,
        "stats": {
            "lines": len(lines),
            "people": len(seen),
            "unparsed": len(unparsed),
            "with_email": sum(1 for c in seen.values() if c["email"]),
            "known_firms": sum(1 for c in seen.values() if c["company_known"]),
            "merged": len(read) - len(stitched),
        },
    }
