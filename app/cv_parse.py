"""Extract a profile from pasted CV text.

Deliberately stdlib-only and deliberately conservative: it proposes, it does not
decide. Every field comes back with the evidence that produced it, so the UI can
show you *why* it thinks your degree is an M2 and let you accept or edit.

A CV is a semi-structured document written by a human for a human. Anything that
claims to parse one perfectly is lying. The goal here is to get 80% of the boring
fields filled so you only have to correct, not type.

    python3 app/cv_parse.py path/to/cv.txt
"""
import re
import sys
import unicodedata
from datetime import date

# ---------------------------------------------------------------- vocabulary
# Languages, tools and degrees are recognised from a fixed vocabulary rather
# than guessed, so a "hit" is something we can defend.

# Canonical English name -> the words that indicate it. A French CV says
# "Français / Anglais / Espagnol", and the profile has to read "English (fluent)"
# because that is what the English templates interpolate.
LANGUAGES = [
    ("English", [r"english", r"anglais", r"inglés", r"englisch"]),
    ("French", [r"french", r"fran[çc]ais", r"francés", r"franz[öo]sisch"]),
    ("Spanish", [r"spanish", r"espagnol", r"espa[ñn]ol", r"spanisch"]),
    ("German", [r"german", r"allemand", r"alem[áa]n", r"deutsch"]),
    ("Italian", [r"italian", r"italien", r"italiano"]),
    ("Portuguese", [r"portuguese", r"portugais", r"portugu[êe]s"]),
    ("Dutch", [r"dutch", r"n[ée]erlandais", r"neerlandes"]),
    ("Mandarin", [r"mandarin", r"chinese", r"chinois"]),
    ("Japanese", [r"japanese", r"japonais"]),
    ("Korean", [r"korean", r"cor[ée]en"]),
    ("Arabic", [r"arabic", r"arabe"]),
    ("Russian", [r"russian", r"russe"]),
    ("Hindi", [r"hindi"]),
    ("Polish", [r"polish", r"polonais"]),
    ("Swedish", [r"swedish", r"su[ée]dois"]),
    ("Norwegian", [r"norwegian", r"norv[ée]gien"]),
    ("Danish", [r"danish", r"danois"]),
    ("Greek", [r"greek", r"grec"]),
    ("Turkish", [r"turkish", r"turc"]),
    ("Hebrew", [r"hebrew", r"h[ée]breu"]),
]
LEVEL_WORDS = (r"native|bilingual|fluent|mother tongue|professional|full professional|"
               r"advanced|intermediate|conversational|basic|beginner|elementary|"
               r"c2|c1|b2|b1|a2|a1")
# Levels are normalised to English too, for the same reason.
LEVEL_MAP = [
    (r"langue maternelle|mother tongue|maternelle|natif|native", "native"),
    (r"bilingue|bilingual", "bilingual"),
    (r"courant|couramment|fluent|full professional|professional working", "fluent"),
    (r"avanc[ée]|advanced|c1\b|c2\b", "advanced"),
    (r"interm[ée]diaire|intermediate|b1\b|b2\b", "intermediate"),
    (r"notions|scolaire|basic|beginner|elementary|a1\b|a2\b", "basic"),
]

TOOLS = [
    "Python", "C++", "C#", "C", "Java", "Scala", "Julia", "Rust", "Go", "Kotlin",
    "MATLAB", "R", "SQL", "NoSQL", "PostgreSQL", "MySQL", "MongoDB", "kdb+", "q",
    "VBA", "Excel", "Pandas", "NumPy", "SciPy", "scikit-learn", "TensorFlow",
    "PyTorch", "Keras", "XGBoost", "LightGBM", "JAX", "Spark", "Hadoop", "Kafka",
    "Airflow", "Docker", "Kubernetes", "Git", "Linux", "Bash", "AWS", "GCP",
    "Azure", "Tableau", "Power BI", "Bloomberg", "Reuters", "Refinitiv",
    "QuantLib", "Murex", "Sophis", "VBA", "Cython", "Numba", "Dask", "Ray",
    "Snowflake", "Databricks", "Snowpark", "CUDA", "OpenCL", "FPGA", "Verilog",
]
# things that are not languages/tools even though they appear in lists
TOOL_STOPWORDS = {"C", "R", "q", "Go"}

DEGREE_PATTERNS = [
    r"\bph\.?d\b", r"\bdoctorate\b", r"\bdoctorat\b",
    r"\bm2\b", r"\bm1\b", r"\bmaster'?s?\b", r"\bmaster\b", r"\bmastère\b",
    r"\bmsc\b", r"\bm\.?sc\b", r"\bma\b", r"\bmba\b", r"\bms\b",
    r"\bengineer(?:ing)?\b", r"\bécole\b", r"\becole\b", r"\bingenieur\b",
    r"\bingénieur\b", r"\bbachelor'?s?\b", r"\bbsc\b", r"\bb\.?sc\b",
    r"\bgraduat\b", r"\bl[ie]cence\b", r"\bdu\b", r"\bdut\b", r"\bbts\b",
    r"\bcpes\b", r"\bagrégation\b", r"\bagregation\b", r"\bprépa\b", r"\bprepa\b",
]

ROLE_KEYWORDS = [
    "quantitative analyst", "quantitative researcher", "quantitative trader",
    "quantitative developer", "quant developer", "quant researcher", "quant trader",
    "quant", "strats", "strat", "structurer", "structuring", "trader", "trading",
    "portfolio manager", "risk analyst", "market risk", "credit risk",
    "algo trading", "algorithmic trading", "market making", "market maker",
    "research analyst", "data scientist", "machine learning", "derivatives",
    "exotic", "pricing", "model validation", "front office", "sales and trading",
]

CITIES = [
    "Paris", "London", "New York", "Chicago", "Amsterdam", "Frankfurt", "Zurich",
    "Geneva", "Luxembourg", "Brussels", "Madrid", "Barcelona", "Milan", "Dublin",
    "Edinburgh", "Boston", "Singapore", "Hong Kong", "Sydney", "Toronto", "Montreal",
    "Munich", "Berlin", "Lisbon", "Vienna", "Copenhagen", "Stockholm", "Oslo",
]

SECTION_ALIASES = {
    "summary": ["summary", "profile", "objective", "about", "about me", "profil",
                "resume", "résumé", "presentation", "présentation", "professional summary"],
    "education": ["education", "formation", "academic background", "études", "etudes",
                  "academic", "education and training", "diplomes", "diplômes"],
    "experience": ["experience", "expérience", "professional experience", "work experience",
                   "employment", "employment history", "work history", "career",
                   "parcours", "expériences", "experiences", "stage", "internships"],
    "skills": ["skills", "technical skills", "compétences", "competences", "technologies",
               "skills and interests", "technical proficiencies", "it skills",
               "compétences techniques", "outils"],
    "projects": ["projects", "projets", "selected projects", "personal projects",
                 "academic projects", "projets personnels"],
    "achievements": ["achievements", "awards", "honours", "honors", "distinctions",
                     "prix", "awards and honours", "accomplishments"],
    "languages": ["languages", "langues", "language skills", "langues parlées"],
    "certifications": ["certifications", "certificates", "certifications and courses",
                       "certifications et formations", "licences"],
    "interests": ["interests", "centres d'intérêt", "hobbies", "centres d interet",
                  "activités extra-professionnelles", "extracurricular"],
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Loose on purpose - a CV phone can be "+33 6 12 34 56 78", "(020) 7123 4567" or
# "+1-212-555-0188", so single-digit groups must be allowed. Candidates are then
# filtered on total digit count, which is what actually distinguishes a phone
# number from a date range like "2018-2020".
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.\-]?)?(?:\(?\d{1,4}\)?[\s.\-]?){2,8}\d{1,4}")
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+", re.I)
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9\-_]+", re.I)
URL_RE = re.compile(r"https?://[^\s)|,]+|(?<![\w.])www\.[^\s)|,]+", re.I)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
# Non-capturing on purpose: this fragment gets embedded in larger patterns, and a
# capturing group here silently shifts every group index in the outer match.
MONTH_TOKEN = (r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(?:19|20)\d{2}")
PRESENT_TOKEN = r"(?:present|now|current|today|aujourd'hui|ce jour)"
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "un": 1, "deux": 2,
                "trois": 3, "quatre": 4, "cinq": 5, "six_": 6, "sept": 7, "huit": 8,
                "neuf": 9, "dix": 10}


def _strip_accents(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c))


def _norm(s):
    """Normalise a line for comparison against section names."""
    s = _strip_accents(s or "").lower()
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _lines(text):
    return [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _is_header(line):
    """Return the canonical section name if this line is a section header."""
    raw = (line or "").strip()
    if not raw or len(raw) > 46:
        return None
    n = _norm(raw)
    if not n:
        return None
    for canon, aliases in SECTION_ALIASES.items():
        if n in aliases:
            return canon
    # tolerate "TECHNICAL SKILLS & TOOLS" or "EDUCATION:" style headers
    for canon, aliases in SECTION_ALIASES.items():
        for a in aliases:
            if len(a) >= 5 and (n.startswith(a + " ") or n == a):
                return canon
    return None


def split_sections(text):
    """{'summary': ['line', ...], ...}. Lines before the first header go to '_top'."""
    out, current = {}, "_top"
    out[current] = []
    for line in _lines(text):
        h = _is_header(line)
        if h:
            current = h
            out.setdefault(current, [])
            continue
        out.setdefault(current, []).append(line)
    return out


def _block(sections, name, max_lines=14):
    body = [ln.strip() for ln in sections.get(name, []) if ln.strip()]
    return body[:max_lines]


# ------------------------------------------------------------------ extractors
def find_name(text, email=None):
    """The name is usually the first short, title-case, digit-free line."""
    for line in _lines(text)[:8]:
        s = line.strip()
        if not s or len(s) > 46:
            continue
        if "@" in s or "http" in s.lower() or any(ch.isdigit() for ch in s):
            continue
        if "|" in s or "•" in s or ":" in s:
            continue
        words = [w for w in re.split(r"\s+", s) if w]
        if not 2 <= len(words) <= 4:
            continue
        if not all(re.match(r"^[A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]*$", w) or w.isupper() for w in words):
            continue
        if email:
            local = email.split("@")[0].lower()
            first = _strip_accents(words[0]).lower()
            if first and first not in local and len(first) > 3:
                # name and email don't corroborate, but it is still the best guess
                pass
        return s
    return ""


def find_city(text):
    for city in CITIES:
        if re.search(r"\b" + re.escape(city) + r"\b", text or ""):
            return city
    m = re.search(r"(?:location|based in|ville|localisation)\s*[:\-]\s*([A-Za-zÀ-ÿ\- ]{2,30})",
                  text or "", re.I)
    if m:
        return m.group(1).strip()
    return ""


def find_education(sections, text):
    hits = []
    for line in _block(sections, "education", 12):
        for pat in DEGREE_PATTERNS:
            if re.search(pat, line, re.I):
                hits.append(line.strip())
                break
    if not hits:
        for line in _lines(text):
            if len(line) > 120 or not line.strip():
                continue
            for pat in DEGREE_PATTERNS:
                if re.search(pat, line, re.I):
                    hits.append(line.strip())
                    break
    # de-duplicate, keep the most informative few
    seen, out = set(), []
    for h in hits:
        k = _norm(h)
        if k and k not in seen:
            seen.add(k)
            out.append(h)
    return " | ".join(out[:3])


def _merge_intervals(iv):
    if not iv:
        return []
    iv = sorted(iv)
    out = [list(iv[0])]
    for s, e in iv[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def _months_between(a, b):
    """(year, month) pair -> whole months, so ranges can be merged then totalled."""
    return b[0] * 12 + b[1] - (a[0] * 12 + a[1])


def _parse_point(tok, today):
    tok = (tok or "").strip()
    if re.search(PRESENT_TOKEN, tok, re.I):
        return (today.year, today.month)
    mm = re.search(r"(" + "|".join(MONTHS) + r")[a-z]*\.?\s*((?:19|20)\d{2})", tok, re.I)
    if mm:
        return (int(mm.group(2)), MONTHS[mm.group(1).lower()[:3]])
    ym = YEAR_RE.search(tok)
    if ym:
        return (int(ym.group(0)), 1)
    return None


def find_years_exp(sections, text):
    """Explicit 'N years' (digits or words) wins; otherwise merge date ranges.

    Ranges are merged before totalling so two overlapping roles at the same firm
    are not double counted.
    """
    words = "|".join(NUMBER_WORDS)
    m = re.search(r"\b(\d{1,2}|" + words + r")\s*\+?\s*(?:years?|ans?)\b"
                  r"(?:\s+of)?\s*(?:experience|expérience|exp)?", text or "", re.I)
    if m:
        tok = m.group(1).lower()
        n = int(tok) if tok.isdigit() else NUMBER_WORDS.get(tok, 0)
        if 0 < n <= 45:
            return str(n)

    today = date.today()
    body = "\n".join(_block(sections, "experience", 40))
    if not body.strip():
        body = text
    intervals = []
    pattern = (r"(" + MONTH_TOKEN + r"|(?:19|20)\d{2})\s*"
               r"(?:-|–|—|to|until|au|jusqu'à)\s*"
               r"(" + MONTH_TOKEN + r"|" + PRESENT_TOKEN + r"|(?:19|20)\d{2})")
    for m in re.finditer(pattern, body, re.I):
        a, b = _parse_point(m.group(1), today), _parse_point(m.group(2), today)
        if a and b and b >= a:
            intervals.append((a[0] * 12 + a[1], b[0] * 12 + b[1]))
    total = sum(e - s for s, e in _merge_intervals(intervals))
    if total > 0:
        years = total / 12.0
        return str(int(years)) if years >= 1 else "<1"
    return ""


def find_skills(sections, text):
    """Prefer the skills section, then scan the whole document."""
    pool = "\n".join(_block(sections, "skills", 20)) or ""
    found, seen = [], set()
    for tool in TOOLS:
        if tool in TOOL_STOPWORDS and not re.search(r"(?:^|[\s,;|/])" + re.escape(tool) + r"(?:$|[\s,;|/])", pool):
            continue
        pattern = re.escape(tool) + (r"\b" if tool[-1].isalnum() else "")
        if re.search(r"(?<![\w+#])" + pattern, pool) or \
           (not pool and re.search(r"(?<![\w+#])" + pattern, text or "")):
            key = tool.lower()
            if key not in seen:
                seen.add(key)
                found.append(tool)
    # Capped because this is interpolated mid-sentence ("2 years on <skills>").
    # A 24-item list turns a readable line into a wall of nouns.
    return ", ".join(found[:12])


def _normalise_level(frag):
    for pattern, label in LEVEL_MAP:
        if re.search(pattern, frag, re.I):
            return label
    return ""


def find_languages(sections, text):
    pool = "\n".join(_block(sections, "languages", 12)) or ""
    scope = pool or (text or "")
    out, seen = [], set()
    for canonical, patterns in LANGUAGES:
        if canonical.lower() in seen:
            continue
        for pat in patterns:
            m = re.search(r"\b" + pat + r"\b[^\n]{0,40}", scope, re.I)
            if not m:
                continue
            lvl = _normalise_level(m.group(0))
            out.append(canonical + (f" ({lvl})" if lvl else ""))
            seen.add(canonical.lower())
            break
    return ", ".join(out)


# Words that are technically role keywords but read badly as a target
# ("looking for a Derivatives role"). Used only to rank, never to exclude.
GENERIC_ROLES = {"quant", "strat", "strats", "trader", "trading", "pricing",
                 "derivatives", "exotic", "front office", "research analyst",
                 "model validation", "data scientist", "machine learning"}


def find_roles(sections, text):
    """One or two roles, not a keyword dump.

    This value is interpolated into a sentence ("looking for a {{my_target_roles}}
    role"), so six comma-separated keywords make it unreadable. Intent is read
    from the headline and summary first - that is where someone states what they
    are - then from the document at large.
    """
    lines = [ln.strip() for ln in _lines(text) if ln.strip()]
    headline = " ".join(lines[1:3]) if len(lines) > 1 else ""
    summary = " ".join(_block(sections, "summary", 4))
    for scope in (headline, summary, text or ""):
        low = scope.lower()
        hits = [kw for kw in ROLE_KEYWORDS if kw in low]
        if not hits:
            continue
        specific = [k for k in hits if k not in GENERIC_ROLES]
        # "Quantitative Developer / Quant Developer" is the same job twice - keep
        # only the first phrasing of each head noun.
        chosen, heads = [], set()
        for kw in (specific or hits):
            head = kw.split()[-1]
            if head in heads:
                continue
            heads.add(head)
            chosen.append(kw)
            if len(chosen) == 2:
                break
        return " / ".join(c.title() for c in chosen)
    return ""


def find_headline(sections, name, text):
    """The line right after the name, if it reads like a title rather than contact info."""
    lines = [ln.strip() for ln in _lines(text) if ln.strip()]
    if name in lines:
        i = lines.index(name)
        for cand in lines[i + 1:i + 4]:
            if _is_header(cand):
                break
            if "@" in cand or "http" in cand.lower() or re.search(r"\+?\d[\d\s.\-()]{6,}", cand):
                continue
            if 3 <= len(cand) <= 90:
                return cand
    body = _block(sections, "summary", 3)
    if body:
        first = re.split(r"(?<=[.!?])\s+", " ".join(body))[0]
        if 10 <= len(first) <= 140:
            return first
    return ""


def find_pitch(sections):
    body = _block(sections, "summary", 6)
    if not body:
        return ""
    text = " ".join(x.strip() for x in body if x.strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:600]


def find_projects(sections):
    body = _block(sections, "projects", 8)
    return " | ".join(x.strip() for x in body if x.strip())[:600]


def find_achievements(sections):
    body = _block(sections, "achievements", 8)
    return " | ".join(x.strip() for x in body if x.strip())[:600]


def find_phone(text):
    """A candidate is a phone number if it has 9-15 digits and is not a date range."""
    best = ""
    for m in PHONE_RE.finditer(text or ""):
        cand = m.group(0).strip(" .-")
        digits = re.sub(r"\D", "", cand)
        if not 9 <= len(digits) <= 15:
            continue
        # reject bare year ranges ("2018-2020") and things that are mostly one year
        if re.fullmatch(r"(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}", cand):
            continue
        if len(re.findall(r"(?:19|20)\d{2}", cand)) >= 2:
            continue
        if not best:
            best = cand
        elif cand.startswith("+") or cand.startswith("0"):
            best = cand           # a country code or trunk zero is a stronger signal
    return best


# ----------------------------------------------------------------------- main
def parse_cv(text):
    """Return {fields, evidence, missing}. Nothing is invented - empty means not found."""
    text = text or ""
    sections = split_sections(text)

    email_m = EMAIL_RE.search(text)
    email = email_m.group(0) if email_m else ""
    name = find_name(text, email)

    li_m = LINKEDIN_RE.search(text)
    gh_m = GITHUB_RE.search(text)
    urls = [u for u in URL_RE.findall(text)]
    website = ""
    for u in urls:
        if "linkedin.com" in u.lower() or "github.com" in u.lower():
            continue
        if any(dom in u.lower() for dom in ("mailto:", "twitter.com", "facebook.com")):
            continue
        website = u if u.lower().startswith("http") else "https://" + u
        break

    fields = {
        "full_name": name,
        "headline": find_headline(sections, name, text),
        "email": email,
        "phone": find_phone(text),
        "linkedin": ("https://" + li_m.group(0).lstrip("/")) if li_m and not li_m.group(0).lower().startswith("http") else (li_m.group(0) if li_m else ""),
        "github": ("https://" + gh_m.group(0).lstrip("/")) if gh_m and not gh_m.group(0).lower().startswith("http") else (gh_m.group(0) if gh_m else ""),
        "website": website,
        "city": find_city(text),
        "education": find_education(sections, text),
        "years_exp": find_years_exp(sections, text),
        "key_skills": find_skills(sections, text),
        "languages": find_languages(sections, text),
        "target_roles": find_roles(sections, text),
        "projects": find_projects(sections),
        "achievements": find_achievements(sections),
        "pitch": find_pitch(sections),
    }
    evidence = {}
    for k, v in fields.items():
        if v:
            evidence[k] = _where_it_came_from(k, sections)

    missing = [k for k, v in fields.items() if not str(v or "").strip()]
    return {"fields": fields, "evidence": evidence, "missing": missing,
            "sections_found": sorted(k for k in sections if k != "_top" and
                                     any(x.strip() for x in sections[k])),
            "chars": len(text)}


def _where_it_came_from(key, sections):
    if key in ("education",):
        return "education section" if any(x.strip() for x in sections.get("education", [])) else "pattern match across the document"
    if key in ("key_skills",):
        return "skills section" if any(x.strip() for x in sections.get("skills", [])) else "tool names recognised in the text"
    if key in ("languages",):
        return "languages section" if any(x.strip() for x in sections.get("languages", [])) else "language names recognised in the text"
    if key in ("projects", "achievements", "pitch", "headline"):
        return f"{key} section" if any(x.strip() for x in sections.get(key, [])) else "summary section"
    if key == "years_exp":
        return "explicit statement or merged date ranges"
    if key in ("email", "phone", "linkedin", "github", "website"):
        return "contact details in the text"
    if key == "full_name":
        return "first title-case line"
    if key == "city":
        return "city name recognised"
    if key == "target_roles":
        return "role keywords in the text"
    return "heuristic"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    raw = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
    res = parse_cv(raw)
    for k, v in res["fields"].items():
        mark = "  " if v else "??"
        print(f"{mark} {k:14} {v}")
    print("\nmissing:", ", ".join(res["missing"]))
    print("sections found:", ", ".join(res["sections_found"]))
