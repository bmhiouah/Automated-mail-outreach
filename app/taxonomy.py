"""Job title vocabulary: seniority, desk, and the field lists the API writes.

Extracted from server.py so the data layer can use it without importing the web
layer. people_store.py used to do `import server` purely to call
derive_from_title, which meant the harvester loaded the whole HTTP server to read
a lookup table.

Nothing here performs I/O: pure data, plus one pure function.
"""

COMPANY_FIELDS = ["name", "domain", "type", "subtype", "hq_city", "hq_country",
                  "market", "email_pattern", "pattern_confidence", "careers_url",
                  "tier", "status", "notes",
                  # harvested profile (see providers/hunter.py)
                  "description", "founded_year", "headcount", "employee_count",
                  "industry", "company_type", "keywords", "address",
                  "linkedin_url", "twitter", "ticker"]
CONTACT_FIELDS = ["first_name", "last_name", "job_title", "desk", "seniority",
                  "company_id", "company_name", "city", "country", "email",
                  "email_source", "linkedin_url",
                  "source",
                  # harvested person data (see app/providers/)
                  "full_name", "position_raw", "headline", "department",
                  "seniority_level", "location_raw", "state", "country_code",
                  "timezone", "phone", "email_masked"]
OUTREACH_FIELDS = ["contact_id", "channel", "template_id", "subject", "body",
                   "status", "sent_at", "followup_stage", "next_followup_at",
                   "replied_at", "reply_snippet", "outcome", "notes"]
TEMPLATE_FIELDS = ["name", "role_family", "subject_tpl", "body_tpl", "notes", "active"]
APPLICATION_FIELDS = ["company_id", "company_name", "role", "url", "status",
                      "applied_at", "notes"]
TITLES_BY_TYPE = {
    "bank": ["Quantitative Analyst", "Strats", "Structurer", "Trader"],
    "hedge_fund": ["Quantitative Researcher", "Portfolio Manager", "Quant Developer"],
    "prop_hft": ["Quantitative Trader", "Quantitative Developer", "Trading Analyst"],
    "asset_manager": ["Quantitative Analyst", "Systematic Portfolio Manager", "Risk Analyst"],
    "commodity": ["Quantitative Analyst", "Trader", "Origination Analyst"],
    "insurance_am": ["Quantitative Analyst", "Portfolio Manager", "Risk Analyst"],
    "broker": ["Broker", "Trader", "Quantitative Analyst"],
    "crypto": ["Quantitative Trader", "Trader", "Quantitative Researcher"],
    "other": ["Quantitative Analyst", "Quant Researcher"],
}
PROFILE_FIELDS = ["full_name", "email", "phone", "linkedin", "github", "website",
                  "headline", "city", "target_roles", "years_exp", "education",
                  "key_skills", "projects", "achievements", "languages",
                  "availability", "pitch", "cv_text"]
CONTACT_IMPORT_MAP = {
    "first name": "first_name", "firstname": "first_name", "prenom": "first_name",
    "last name": "last_name", "lastname": "last_name", "nom": "last_name",
    "job title": "job_title", "title": "job_title", "role": "job_title",
    "poste": "job_title", "desk": "desk", "team": "desk",
    "company": "company_name", "company name": "company_name", "entreprise": "company_name",
    "email": "email", "mail": "email", "e-mail": "email",
    "linkedin": "linkedin_url", "linkedin url": "linkedin_url",
    "city": "city", "ville": "city", "location": "city",
    "hook": "hook", "notes": "notes", "source": "source",
    "position": "job_title",           # LinkedIn Connections.csv
    "url": "linkedin_url",             # LinkedIn Connections.csv
    "email address": "email",
    "seniority": "seniority", "tags": "tags",
}

SENIORITY_RULES = [
    ("global head", "head"), ("head of", "head"), ("head,", "head"), (" head", "head"),
    ("chief", "C-suite"), ("president", "C-suite"), ("founder", "founder"),
    ("portfolio manager", "PM"), (" pm", "PM"),
    ("partner", "partner"), ("managing director", "MD"), (" md", "MD"),
    ("director", "director"), ("vice president", "VP"), (" vp", "VP"),
    ("principal", "principal"), ("associate", "associate"),
    ("senior analyst", "associate"), ("intern", "intern"), ("graduate", "analyst"),
    ("analyst", "analyst"), ("trader", "trader"), ("researcher", "researcher"),
]

DESK_RULES = [
    ("rates", "Rates"), ("fixed income", "Rates"),
    ("credit", "Credit"), ("convertible", "Credit"),
    ("fx", "FX"), ("foreign exchange", "FX"), ("currency", "FX"),
    ("commodit", "Commodities"), ("energy", "Commodities"), ("power", "Commodities"),
    ("gas", "Commodities"), ("oil", "Commodities"), ("freight", "Commodities"),
    ("volatility", "Vol"), (" vol", "Vol"), ("options", "Vol"),
    ("macro", "Macro"), ("emerging market", "EM"),
    ("equity derivative", "Equity Derivatives"), ("exotic", "Equity Derivatives"),
    ("derivative", "Derivatives"), ("structuring", "Structuring"),
    ("structur", "Structuring"), ("index", "Index"), ("qis", "QIS"),
    ("crypto", "Crypto"), ("digital asset", "Crypto"),
    ("systematic", "Systematic"),
    ("quantitative", "Quant"), ("quant ", "Quant"),
    ("equit", "Equity"), ("execution", "Execution"), ("risk", "Risk"),
]


def derive_from_title(title):
    """Guess seniority and desk from a job title. Never overwrites real values."""
    t = (" " + (title or "").lower() + " ").replace("-", " ")
    seniority = ""
    for kw, val in SENIORITY_RULES:
        if kw in t:
            seniority = val
            break
    desk = ""
    for kw, val in DESK_RULES:
        if kw in t:
            desk = val
            break
    return seniority, desk
