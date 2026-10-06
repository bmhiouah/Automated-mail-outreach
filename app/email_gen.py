"""Template rendering for cold outreach.

Placeholders are {{name}} style. Two families:

  contact side : first_name, last_name, contact_name, job_title, desk,
                 company, company_type, city, hook
  firm intel   : company_research (full brief), company_hook (the
                 "Best hook:" line, ready to paraphrase)
  your side    : my_full_name, my_first_name, my_headline, my_email, my_phone,
                 my_linkedin, my_github, my_website, my_city, my_target_roles,
                 my_years_exp, my_education, my_key_skills, my_projects,
                 my_achievements, my_languages, my_availability, my_pitch

Unknown placeholders are left visible on purpose, so gaps are obvious instead
of silently producing an email that reads like it was written by a robot.
"""
import re

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _brief_hook(research):
    """Pull the 'Best hook: ...' sentence out of a firm brief. Falls back to
    the first sentence so the placeholder is never empty when a brief exists."""
    text = (research or "").strip()
    if not text:
        return ""
    m = re.search(r"best hook:\s*(.+?)(?:\.\s|$)", text, re.I)
    if m:
        return m.group(1).strip().rstrip(".")
    first = re.split(r"(?<=[.!?])\s+", text)[0]
    return first.strip().rstrip(".")


def _short_skills(skills, n=4):
    """First n skills, for use inside a sentence rather than a list."""
    parts = [p.strip() for p in re.split(r"[,;|/]", skills or "") if p.strip()]
    return ", ".join(parts[:n])


def build_context(contact=None, company=None, profile=None):
    contact = contact or {}
    company = company or {}
    profile = profile or {}

    my_full_name = (profile.get("full_name") or "").strip()

    ctx = {
        # contact side
        "first_name": contact.get("first_name") or "",
        "last_name": contact.get("last_name") or "",
        "contact_name": f"{contact.get('first_name') or ''} {contact.get('last_name') or ''}".strip(),
        "job_title": contact.get("job_title") or "",
        "desk": contact.get("desk") or contact.get("job_title") or "",
        "company": contact.get("company_name") or company.get("name") or "",
        "company_type": company.get("type") or "",
        "company_subtype": company.get("subtype") or "",
        "city": contact.get("city") or company.get("hq_city") or "",
        "hook": contact.get("hook") or "",
        # firm intel - the "why you" line. `company_brief` is the full note,
        # `company_hook` is just the sentence after "Best hook:", which is the
        # part you actually want to paraphrase in a first line.
        "company_research": company.get("research") or "",
        "company_brief": company.get("research") or "",
        "company_hook": _brief_hook(company.get("research")),
        # your side
        "my_full_name": my_full_name,
        "my_first_name": my_full_name.split(" ")[0] if my_full_name else "",
        "my_headline": profile.get("headline") or "",
        "my_email": profile.get("email") or "",
        "my_phone": profile.get("phone") or "",
        "my_linkedin": profile.get("linkedin") or "",
        "my_github": profile.get("github") or "",
        "my_website": profile.get("website") or "",
        "my_city": profile.get("city") or "",
        "my_target_roles": profile.get("target_roles") or "",
        "my_years_exp": profile.get("years_exp") or "",
        "my_education": profile.get("education") or "",
        "my_key_skills": profile.get("key_skills") or "",
        # Interpolated mid-sentence, so a full 12-item list makes an unreadable
        # line. `_short_skills` keeps the first few for use inside a sentence.
        "my_key_skills_short": _short_skills(profile.get("key_skills")),
        "my_projects": profile.get("projects") or "",
        "my_achievements": profile.get("achievements") or "",
        "my_languages": profile.get("languages") or "",
        "my_availability": profile.get("availability") or "",
        "my_pitch": profile.get("pitch") or "",
    }
    # convenience aliases
    ctx["full_name"] = ctx["my_full_name"]
    ctx["linkedin"] = ctx["my_linkedin"]
    return ctx


def render(template, ctx):
    def sub(match):
        key = match.group(1)
        return str(ctx.get(key, match.group(0)))
    return PLACEHOLDER.sub(sub, template or "")


def missing_placeholders(text):
    """Return the placeholders that could not be resolved - a writing checklist."""
    return sorted({m for m in PLACEHOLDER.findall(text or "")})


def empty_placeholders(template, ctx):
    """Placeholders present in the template that resolved to an empty string.

    These are worse than unknown placeholders, because they are invisible. An
    empty profile turns "I'm {{my_full_name}}, {{my_education}}" into
    "I'm , " and nothing warns you. This is what surfaces them.
    """
    out = []
    for key in PLACEHOLDER.findall(template or ""):
        if key in out:
            continue
        if not str(ctx.get(key, "") or "").strip():
            out.append(key)
    return out


# Acronyms that are correctly written in capitals. Anything here is not "shouting".
OK_ACRONYMS = {
    # tools and tech
    "MATLAB", "SQL", "NOSQL", "AWS", "GCP", "CUDA", "FPGA", "VBA", "IDE", "API",
    "APIS", "GPU", "CPU", "RAM", "SSD", "HTTP", "HTTPS", "JSON", "XML", "CSV",
    "PDF", "HTML", "CSS", "YAML", "SQLITE", "SPARK", "KAFKA", "DOCKER", "LINUX",
    "KDB", "SWIFT", "FIX", "ISDA", "LLM", "LLMS", "ML", "AI", "NLP", "STEM",
    # finance
    "FX", "ETF", "ETFS", "ETP", "HFT", "OTC", "VAR", "CVAR", "PNL", "ROE", "ROI",
    "KPI", "ALM", "CDS", "IRS", "ABS", "MBS", "EMIR", "MIFID", "UCITS", "NAV",
    "AUM", "IPO", "ESG", "IBOR", "SOFR", "SONIA", "EONIA", "LIBOR", "EURIBOR",
    # places and orgs
    "USA", "UK", "EU", "EMEA", "APAC", "NYC", "GMT", "UTC", "CET", "GDPR",
    "CFA", "FRM", "MBA", "PHD", "CEO", "CFO", "CTO", "COO", "MD", "VP", "AVP",
    "BAU", "TBC", "TBD", "FYI", "ASAP", "II", "III", "IV",
}

BANNED_PHRASES = {
    "i hope this email finds you well": "the most deleted opener in existence",
    "i hope this finds you well": "the most deleted opener in existence",
    "i am writing to": "just say the thing",
    "i wanted to reach out": "empty preamble - start with the substance",

    "hard-working": "unverifiable adjective",
    "team player": "unverifiable adjective",
    "detail-oriented": "unverifiable adjective",
    "synergy": "corporate filler",
    "to whom it may concern": "you didn't find a name - go find one",
    "dear sir": "you didn't find a name",
    "dear madam": "you didn't find a name",
    "kindly": "sounds like a spam filter",
    "please find attached": "say what the attachment is",
    "circle back": "vague",
    "touch base": "vague",
    "at your earliest convenience": "passive and vague",
    "perfect fit": "unproven claim",
    "great fit": "unproven claim",
    "i would be grateful": "unnecessary deference",
    "do not hesitate to contact me": "empty closing",
    "looking forward to hearing from you": "empty closing",
}


def score_email(subject, body, ctx=None):
    """Grade a cold email before it leaves the machine. Returns issues, not praise."""
    ctx = ctx or {}
    issues, notes = [], []
    score = 100
    text = (body or "")
    low = text.lower()
    words = len(re.findall(r"\b[\w'%-]+\b", text))

    banned_hits = [p for p in BANNED_PHRASES if p in low]
    for phrase in banned_hits:
        issues.append(f'"{phrase}" — {BANNED_PHRASES[phrase]}')
    score -= min(8 * len(banned_hits), 25)

    first_line = text.strip().split("\n")[0].lower() if text.strip() else ""
    if ctx.get("first_name") and ctx["first_name"].lower() not in first_line:
        issues.append("opening line doesn't use their first name")
        score -= 10

    unresolved = missing_placeholders(subject + " " + text)
    if unresolved:
        issues.append("unresolved placeholders: " + ", ".join(unresolved))
        score -= 20

    if "?" not in text:
        issues.append("no question - a cold email with no ask gets no reply")
        score -= 8

    if words > 200:
        issues.append(f"{words} words - far too long, this will not be read")
        score -= 25
    elif words > 185:
        issues.append(f"{words} words - too long, nobody reads this on a phone")
        score -= 15
    elif words < 40:
        issues.append(f"{words} words - too thin to be worth answering")
        score -= 10

    sub = (subject or "").strip()
    if len(sub) > 65:
        issues.append("subject over 65 characters - it will be truncated on mobile")
        score -= 8
    elif len(sub) < 12:
        issues.append("subject too short to earn an open")
        score -= 4

    if not ctx.get("hook") and not ctx.get("company_hook"):
        issues.append("no personalisation hook - reads as bulk mail")
        score -= 10

    # Legitimate all-caps words. Flagging MATLAB or SQL as "shouting" was a false
    # positive that would train you to ignore the warning - the worst outcome for
    # a checker, so the acronym list matters more than it looks.
    caps = [w for w in re.findall(r"\b[A-Z][A-Z0-9+#]{3,}\b", text)
            if w.upper() not in OK_ACRONYMS]
    if caps:
        issues.append("shouting: " + ", ".join(sorted(set(caps))[:3]))
        score -= 5

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) > 8:
        issues.append(f"{len(paragraphs)} paragraphs - cut to eight or fewer")
        score -= 5

    score = max(0, min(100, score))
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"
    if not issues:
        notes.append("nothing obviously wrong. send it.")
    return {"score": score, "grade": grade, "issues": issues,
            "notes": notes, "words": words, "paragraphs": len(paragraphs)}


def quality_flags(ctx, body):
    """Cheap sanity checks before anything leaves the machine."""
    flags = []
    if len(body) > 1200:
        flags.append("body is long (>1200 chars) - desks do not read essays")
    if not ctx.get("first_name"):
        flags.append("no first name - do not send a cold email to 'Hi ,'")
    if not ctx.get("hook") and not ctx.get("company_hook"):
        flags.append("no personalisation hook - this will read as bulk mail")
    if "{{" in body:
        flags.append("unresolved placeholders: " + ", ".join(missing_placeholders(body)))
    if ctx.get("company") and ctx["company"].lower() in body.lower().replace("{{company}}", ""):
        pass
    return flags


SEED_TEMPLATES = [
    {
        "name": "Quant desk - concise cold intro",
        "role_family": "quant",
        "subject_tpl": "Quick question about the {{desk}} team",
        "body_tpl": """Hi {{first_name}},

{{hook}}

I'm {{my_full_name}}, {{my_headline}} - {{my_years_exp}} years on {{my_key_skills_short}}, looking for a {{my_target_roles}} role in {{city}}.

Is the {{desk}} team at {{company}} in a position to meet someone with that profile, or is hiring frozen right now? Happy to send a one-page summary if that's easier to scan than a CV.

Best,
{{my_full_name}}
{{my_phone}} | {{my_linkedin}}""",
        "notes": "Default template. Keep it under 120 words. The hook line is what makes it work - write it per person, not per firm.",
    },
    {
        "name": "Trading / S&T desk - direct ask",
        "role_family": "trading",
        "subject_tpl": "{{desk}} at {{company}}",
        "body_tpl": """Hi {{first_name}},

I'm {{my_full_name}}, {{my_headline}}, based in {{my_city}}.

{{hook}}

I'm looking for a {{my_target_roles}} seat, and {{company}} is one of the few desks where the work is genuinely {{my_target_roles}}-shaped. Rather than guess, two questions:

1. Is the team adding headcount in the coming months?
2. If not now, who owns that decision?

Short on my side: {{my_pitch}}

Either way, thanks for reading.

{{my_full_name}}
{{my_phone}} | {{my_linkedin}}""",
        "notes": "Two questions is the sweet spot. One feels vague, three feels like a survey.",
    },
    {
        "name": "Structuring - show the work",
        "role_family": "structuring",
        "subject_tpl": "{{desk}} at {{company}} - one question",
        "body_tpl": """Hi {{first_name}},

I'm {{my_full_name}}, {{my_headline}}.

{{hook}}

Rather than describe myself, here is the thing I built that is closest to what your desk does: {{my_projects}}

I'm looking for a {{my_target_roles}} role in {{city}} and would value fifteen minutes to hear whether my background is close enough to what the team actually needs. If it isn't, tell me what is missing and I'll go build it.

Best,
{{my_full_name}}
{{my_phone}} | {{my_linkedin}}""",
        "notes": "Structuring desks respond to concrete work. Lead with the artefact, not the adjectives.",
    },
    {
        "name": "Follow-up - bump, once",
        "role_family": "followup",
        "subject_tpl": "Re: Quick question about the {{desk}} team at {{company}}",
        "body_tpl": """Hi {{first_name}},

Bumping this once in case it got buried - I know how quickly these disappear.

Is the {{desk}} team hiring at the junior level this year? If not, that's a perfectly good answer and I'll stop here. If it is, I'd still like to introduce myself properly.

{{my_full_name}}""",
        "notes": "One follow-up, five to seven days later. A second follow-up is rarely worth it.",
    },
]
