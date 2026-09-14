-- Cold approach / quant finance job outreach tracker
-- SQLite schema. Designed to be edited by hand or by the local web UI.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- COMPANIES
CREATE TABLE IF NOT EXISTS companies (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  name               TEXT NOT NULL UNIQUE,
  domain             TEXT,                 -- email domain, e.g. gs.com
  type               TEXT NOT NULL DEFAULT 'other',   -- bank|hedge_fund|prop_hft|asset_manager|commodity|insurance_am|broker|crypto|other
  subtype            TEXT,                 -- bulge bracket, multi-strategy, market maker...
  hq_city            TEXT,
  hq_country         TEXT,
  market             TEXT,                 -- Global|Europe|France|UK
  email_pattern      TEXT,                 -- first.last / f.last / firstlast / first / lastfirst ...
  pattern_confidence REAL DEFAULT 0,       -- 0..1
  careers_url        TEXT,
  research           TEXT,                 -- firm brief: what they do, how to hook them
  research_updated   TEXT,
  tier               INTEGER DEFAULT 2,    -- 1 = top priority, 3 = long tail
  status             TEXT DEFAULT 'to_research',  -- to_research|researched|has_contacts|approached|dead
  notes              TEXT,
  created_at         TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at         TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Evidence collected for a company's email convention
CREATE TABLE IF NOT EXISTS pattern_evidence (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id   INTEGER REFERENCES companies(id) ON DELETE CASCADE,
  pattern      TEXT,
  sample_email TEXT,
  source       TEXT,
  confidence   REAL DEFAULT 0.5,
  notes        TEXT,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Alternative spellings and abbreviations seen in real exports
-- ("SG", "Societe Generale CIB", "Merrill Lynch", ...)
CREATE TABLE IF NOT EXISTS company_aliases (
  alias      TEXT PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------- CONTACTS
CREATE TABLE IF NOT EXISTS contacts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name    TEXT NOT NULL,
  last_name     TEXT NOT NULL,
  job_title     TEXT,
  desk          TEXT,                     -- Equity Derivatives, Systematic Macro, FX Options...
  seniority     TEXT,                     -- analyst|associate|VP|director|MD|head|partner
  company_id    INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  company_name  TEXT,                     -- kept denormalised so imports never fail
  city          TEXT,
  country       TEXT,
  email         TEXT,
  email_status  TEXT DEFAULT 'unknown',   -- unknown|guessed|verified|bounced|missing
  email_source  TEXT,
  linkedin_url  TEXT,
  hook          TEXT,                     -- one-line personalisation angle, injected in emails
  source        TEXT,                     -- linkedin|alumni|conference|referral|website|other
  priority      INTEGER DEFAULT 3,        -- 1 = contact first
  status        TEXT DEFAULT 'identified', -- identified|ready|contacted|replied|positive|negative|closed|blacklist
  tags          TEXT,
  notes         TEXT,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (first_name, last_name, company_name)
);

-- ---------------------------------------------------------------- TEMPLATES
CREATE TABLE IF NOT EXISTS templates (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  role_family TEXT,        -- quant|trading|structuring|sales|research|generic
  subject_tpl TEXT,
  body_tpl    TEXT,
  notes       TEXT,
  active      INTEGER DEFAULT 1,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------- OUTREACH
CREATE TABLE IF NOT EXISTS outreach (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id       INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  channel          TEXT DEFAULT 'email',     -- email|linkedin|in_person|other
  template_id      INTEGER REFERENCES templates(id) ON DELETE SET NULL,
  subject          TEXT,
  body             TEXT,
  status           TEXT DEFAULT 'draft',     -- draft|approved|sent|replied|positive|negative|closed
  sent_at          TEXT,
  followup_stage   INTEGER DEFAULT 0,        -- 0 = first touch, 1 = first follow-up...
  next_followup_at TEXT,
  replied_at       TEXT,
  reply_snippet    TEXT,
  outcome          TEXT,
  notes            TEXT,
  created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------- PROFILE
CREATE TABLE IF NOT EXISTS profile (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  full_name     TEXT,
  email         TEXT,
  phone         TEXT,
  linkedin      TEXT,
  github        TEXT,
  website       TEXT,
  headline      TEXT,
  city          TEXT,
  target_roles  TEXT,
  years_exp     TEXT,
  education     TEXT,
  key_skills    TEXT,
  projects      TEXT,
  achievements  TEXT,
  languages     TEXT,
  availability  TEXT,
  pitch         TEXT,
  cv_text       TEXT,
  updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO profile (id) VALUES (1);

-- ---------------------------------------------------------------- APPLICATIONS
CREATE TABLE IF NOT EXISTS applications (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id  INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  company_name TEXT,
  role        TEXT,
  url         TEXT,
  status      TEXT DEFAULT 'to_apply',  -- to_apply|applied|online_test|interview|offer|rejected|withdrawn
  applied_at  TEXT,
  notes       TEXT,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_contacts_company    ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_status     ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_outreach_contact    ON outreach(contact_id);
CREATE INDEX IF NOT EXISTS idx_outreach_status     ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_companies_type      ON companies(type);
