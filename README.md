# Cold Approach — Quant Finance Outreach Tool

A local, privacy-first web app to find, contact, and track outreach to quantitative finance firms in **Paris & London**. Built for junior profiles (0–2 years) targeting banks, hedge funds, prop/HFT shops, asset managers, commodities desks, and insurance AM.

**Everything runs on your machine.** No accounts, no cloud, no data leaves your computer.

---

## What It Does

| Problem | How This Helps |
|---------|----------------|
| Finding the right people | Harvests quants/developers from Prospeo (filtered by title + location), learns firm email patterns from Hunter |
| Writing emails that don't sound generic | Generates drafts from your CV + firm intel + live news; quality-checked before send |
| Tracking who you've contacted | Append-only ledger — nobody slips through, follow-ups thread automatically |
| Managing CV variants | One base CV, tailored versions per firm/role, validated before attach |
| Staying organized | Sourcing worklist turns 365 firms into an ordered, actionable checklist |

---

## Quick Start

```bash
# 1. Clone and enter
git clone <your-repo-url>
cd cold-approach

# 2. Add your API keys (gitignored, never committed)
cp config.example.json config.json
# Edit config.json with your Hunter.io & Prospeo keys
# Optional: add LLM keys for AI drafts, Tavily for news, SMTP for live sending

# 3. Run
./run.sh
# Opens http://127.0.0.1:8765
```

**First run** creates your local database (`data/cold_approach.db`), loads 365 firms, and seeds 4 email templates.

---

## Core Workflow

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  SOURCE     │───▶│  CAPTURE    │───▶│  DRAFT      │───▶│  SEND       │
│  (find people)│   │ (paste into │   │ (queue:     │   │ (dry-run    │
│             │   │  Contacts)  │   │  edit, send,│   │  by default)│
└─────────────┘    └─────────────┘    │  cancel)    │    └─────────────┘
       ▲                              └──────┬──────┘           │
       │                                     │                │
       └──────────── FOLLOW-UP (threads) ◀──┘                │
                                                        ▼
                                               ┌─────────────┐
                                               │  TRACK      │
                                               │ (reply rate,│
                                               │  funnel,    │
                                               │  coverage)  │
                                               └─────────────┘
```

### 1. Source → Sourcing Tab
- 41 Tier-1 firms (top quant hiring), 61 Tier-2 firms
- Each row: firm hook, target titles, LinkedIn X-ray query, careers link, readiness score
- Download as printable checklist

### 2. Capture → Contacts Tab "Paste People"
Paste messy LinkedIn exports, team pages, spreadsheets, bare names — it parses them all into clean, editable rows with confidence scores.

### 3. Draft → Queue Tab
- **Top-left**: Filter uncontacted people (city, desk, tier, firm type)
- **Top-right**: AI draft appears — editable, fact-checked, quality-scored
- **Send / Cancel / Skip** → auto-advances to next draft
- **Waiting on you** = skipped drafts safety net
- **History** = append-only ledger of everything sent

### 4. CV Variants → CVs Tab
- Base CV (`Badre_Mhiouah_CV.tex`) never overwritten
- Generate tailored versions per firm/role → review → validate → attach to mail

---

## Key Features

| Feature | Why It Matters |
|---------|----------------|
| **Email pattern learning** | One real address at a firm → learns convention → addresses everyone there for free |
| **Quality scoring** | Catches generic filler, empty variables, missing hooks, SHOUTING, long subjects |
| **Dry-run by default** | `mail.send_mode: "dry"` builds & checks everything, sends nothing — rehearse safely |
| **Follow-ups that thread** | `Re:` subject, same context, clears original from due list |
| **Firm intel briefs** | 51 firms with "Best hook" lines (e.g., Jane Street → "a concrete estimation puzzle") |
| **CV parser** | Paste your CV → extracts 17 fields (skills, projects, pitch, education…) with source citations |
| **Never pay twice** | Every API call cached in `fetch_log` + `data/raw/` — re-runs cost zero credits |
| **Applications tracker** | Separate channel from cold outreach — don't mix the metrics |

---

## Configuration

All secrets in `config.json` (gitignored, `chmod 600`):

```json
{
  "hunter_api_key": "...",
  "prospeo_api_key": "...",
  "tavily_api_key": "tvly-...",          // optional: live news line in emails
  "llm": {                                // optional: AI drafts
    "api_key": "sk-...",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "temperature": 0.7,
    "max_tokens": 700
  },
  "mail": {                               // optional: live sending
    "send_mode": "dry",                   // "live" to actually transmit
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "you@gmail.com",
    "smtp_password": "16-char-app-password",
    "smtp_ssl": true,
    "from_name": "Your Name",
    "daily_cap": 40
  }
}
```

**Or use environment variables** (they override the file, so keys never touch disk):
```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="gpt-4o-mini"
export TAVILY_API_KEY="tvly-..."
```

---

## Data You Own

| File | Purpose |
|------|---------|
| `data/cold_approach.db` | Your contacts, outreach, templates, CV variants — **back it up**: `python3 app/tools.py backup` |
| `data/companies_seed.csv` | 365 firms (edit freely, re-seed is idempotent) |
| `data/firm_briefs.csv` | 51 firm intel notes — edit inline, survives restarts |
| `data/careers_urls.csv` | 83 verified careers pages |
| `data/raw/<provider>/` | Cached API responses (never pay twice) |

---

## Commands

```bash
# Harvesting (fills the database)
python3 app/harvest.py --source prospeo --pages 2   # find people
python3 app/harvest.py --source hunter  --tier 1    # learn email patterns
python3 app/harvest.py --status                     # coverage report
python3 app/harvest.py --reconstruct                # rebuild guessed addresses

# CV & Profile
python3 app/llm.py           # check LLM config
python3 app/llm.py --probe   # one real call
python3 app/llm.py --models  # list available models

# Utilities
python3 app/tools.py brief           # follow-ups due, coverage, next firms
python3 app/tools.py dupes           # contacts sharing email/name
python3 app/tools.py backup          # timestamped DB copy
python3 app/tools.py hooks           # all firm hooks
python3 app/tools.py hooks --missing # tier-1 firms without briefs
python3 app/tools.py test            # full test suite (temp DB, safe)
node tests/check_ui.mjs              # front-end route/id checks
```

---

## Architecture (for contributors)

```
app/
├── api/              # 45 REST endpoints, one module per surface
├── providers/        # hunter.py, prospeo.py, tavily.py
├── harvest.py        # orchestrates providers, caches, resumes
├── email_gen.py      # templates + quality checker
├── llm.py            # single chat call, metered in fetch_log
├── mailer.py         # SMTP door (dry/live modes)
├── latex_cv.py       # LaTeX → plain text for profile
├── cv_parse.py       # CV → 17 profile fields (EN/FR)
├── discover_careers.py  # HTTP-verified careers URLs
├── email_pattern.py  # learns firm conventions from real addresses
├── people_store.py   # identity resolution + merge rules
└── taxonomy.py       # title → desk + seniority (import this, not web)

web/                  # vanilla HTML/JS/CSS (no build step)
tests/                # 296 tests on temp DB + UI checks
db/schema.sql         # full schema
```

---

## Privacy & Ethics

- **GDPR-friendly**: Professional addresses only, relevant outreach, stop on request
- **No tracking**: No analytics, no telemetry, no external calls except APIs you configure
- **Your data**: Database is yours — backup, export, delete anytime

---

## Roadmap Status

| Phase | Status | Description |
|-------|--------|-------------|
| Foundations | ✅ Done | Schema, web app, templates, tracking |
| Company Universe | ✅ Done | 365 firms, 41 Tier-1 careers pages verified |
| Email Resolution | ✅ Done | One real address → firm-wide pattern |
| Contact Sourcing | ✅ Done | X-ray search, prioritized worklist |
| Firm Intel | ✅ Done | 51 briefs with hooks |
| Profile + Narrative | 🔄 Waiting on CV | Paste CV → fills profile |
| Analytics Loop | ✅ Done | Funnel + reply rates by type/template |
| Applications Tracker | ✅ Done | Separate from cold outreach |
| Follow-ups | ✅ Done | Threaded, clears due list |

---

## License

MIT — use it, fork it, improve it.

---

## Why This Exists

Cold email works at **small volume with high personalisation**. This tool exists to make that personalisation systematic: every draft has a hook, every address is learned or verified, every send is a deliberate choice. The queue forces you to read what you send — because the one mail you don't read is the one that damages your reputation.

**Start small.** Pick 10 firms from the Sourcing tab, run the X-ray searches, paste results, send. That's a realistic first session.