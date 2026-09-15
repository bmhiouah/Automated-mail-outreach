"""Sample data, so the workflow is visible before touching real people. Every row is tagged source='demo'."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                       # noqa: E402
from domain import get_profile  # noqa: E402


DEMO_CONTACTS = [
    ("Sophie", "Laurent", "Head of Equity Derivatives Structuring", "Equity Derivatives",
     "head", "Societe Generale", "Paris",
     "She spoke at Global Derivatives on hybrid issuance - the flow numbers she quoted were the most concrete thing I heard all day.",
     "conference", 1),
    ("James", "Whitfield", "Quantitative Researcher", "Systematic", "researcher",
     "Qube Research and Technologies", "London",
     "QRT's move into mid-frequency equity signals matches the research I did on intraday decay.",
     "linkedin", 1),
    ("Aisha", "Rahman", "Quantitative Trader", "Quant", "trader", "Jane Street", "London",
     "She wrote the public piece on market-making and adverse selection that I keep coming back to.",
     "linkedin", 1),
    ("Marc", "Lefevre", "Portfolio Manager", "Systematic", "PM", "CFM", "Paris",
     "CFM is the only Paris fund doing this kind of research at scale, and he runs part of it.",
     "alumni", 2),
    ("Elena", "Rossi", "Rates Trader", "Rates", "trader", "BNP Paribas", "London",
     "Her desk covers the exact curve segment I built my last model on.",
     "linkedin", 2),
    ("David", "Chen", "Quantitative Analyst", "Quant", "analyst", "Squarepoint Capital",
     "London", "Squarepoint's London build-out means the team is still small enough to answer.",
     "linkedin", 2),
    ("Thomas", "Meyer", "Structurer", "Structuring", "associate", "Deutsche Bank", "London",
     "He moved from rates to equity structuring last year, which is the path I'm considering.",
     "conference", 3),
    ("Camille", "Moreau", "Quantitative Researcher", "Systematic", "researcher",
     "Syquant Capital", "Paris",
     "A Paris systematic fund few people know about - small team, direct access to the PMs.",
     "website", 2),
]

DEMO_PROFILE = {
    "full_name": "Sample Candidate",
    "headline": "M2 quantitative finance, previously rates quant intern",
    "city": "Paris",
    "target_roles": "quantitative researcher",
    "years_exp": "two years",
    "education": "M2 Quantitative Finance",
    "key_skills": "stochastic calculus, Python, C++, options pricing",
    "projects": "a local volatility pricer calibrated on index surfaces",
    "achievements": "",
    "languages": "French, English",
    "availability": "available immediately",
    "pitch": "I build pricing and backtesting tooling end to end, in Python and C++.",
    "phone": "+33 6 00 00 00 00",
    "linkedin": "linkedin.com/in/sample",
}

def load_demo():
    """Sample data so the workflow is visible. Every row is tagged source='demo'."""
    if not db.query("SELECT id FROM contacts WHERE source='demo' LIMIT 1"):
        for (first, last, title, desk, seniority, company, city, hook, src, prio) in DEMO_CONTACTS:
            co = db.resolve_company(company)
            db.execute(
                "INSERT OR IGNORE INTO contacts (first_name,last_name,job_title,desk,seniority,"
                "company_id,company_name,city,email,email_status,hook,source,priority,status) "
                "VALUES (?,?,?,?,?,?,?,?,'','unknown',?,'demo',?,'identified')",
                [first, last, title, desk, seniority, co["id"] if co else None,
                 company, city, hook, prio])
    prof = get_profile()
    if not (prof.get("full_name") or "").strip():
        db.execute("UPDATE profile SET " + ", ".join(f"{k}=?" for k in DEMO_PROFILE),
                   [DEMO_PROFILE[k] for k in DEMO_PROFILE])
    return {"contacts": db.query("SELECT COUNT(*) n FROM contacts WHERE source='demo'")[0]["n"]}

def clear_demo():
    db.execute("DELETE FROM outreach WHERE contact_id IN "
               "(SELECT id FROM contacts WHERE source='demo')")
    db.execute("DELETE FROM contacts WHERE source='demo'")
    prof = get_profile()
    if (prof.get("full_name") or "").strip() == "Sample Candidate":
        db.execute("UPDATE profile SET " +
                   ", ".join(f"{k}=NULL" for k in DEMO_PROFILE if k != "id"))
    return {"ok": True}


ROUTES = [
    ("POST", "demo/load", lambda p, rest, body: load_demo()),
    ("POST", "demo/clear", lambda p, rest, body: clear_demo()),
]
