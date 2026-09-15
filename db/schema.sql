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
  -- harvested company profile (blank = Unknown, never a blocker)
  description        TEXT,
  founded_year       INTEGER,
  headcount          TEXT,                 -- band, e.g. 51-200
  employee_count     INTEGER,
  industry           TEXT,
  company_type       TEXT,                 -- public company | privately held | ...
  keywords           TEXT,
  address            TEXT,
  linkedin_url       TEXT,
  twitter            TEXT,
  ticker             TEXT,
  -- Prospeo firm data
  website            TEXT,
  logo_url           TEXT,
  revenue_printed    TEXT,                 -- "10B+"
  funding_total      INTEGER,
  technologies       TEXT,                 -- comma separated stack
  job_postings_count INTEGER,              -- live hiring signal
  prospeo_id         TEXT,
  -- The email convention, learned once and reused forever. A firm's address
  -- format is a property of the FIRM, not the person, so it lives here: once
  -- one real address is known, every future first+last name can be resolved.
  pattern_source     TEXT,                 -- which call taught us this
  pattern_sample     TEXT,                 -- the address that proved it
  source             TEXT,                 -- which provider filled this
  source_updated     TEXT,
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
  full_name     TEXT,
  company_id    INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  company_name  TEXT,                     -- kept denormalised so imports never fail
  hook          TEXT,                     -- one-line personalisation angle, injected in emails
  source        TEXT,                     -- linkedin|alumni|conference|referral|website|other
  priority      INTEGER DEFAULT 3,        -- 1 = contact first
  status        TEXT DEFAULT 'identified', -- identified|ready|contacted|replied|positive|negative|closed|blacklist
  tags          TEXT,
  notes         TEXT,
  -- ------------------------------------------------- what they do
  job_title         TEXT,                 -- cleaned current title
  position_raw      TEXT,                 -- VERBATIM, exactly as the source wrote it.
                                          -- Never tidied, never overwritten by a
                                          -- cleaner guess: it is the audit trail
                                          -- behind every derived field below.
  headline          TEXT,                 -- LinkedIn headline; richer than the title,
                                          -- often names the desk and the firm
  department        TEXT,                 -- provider taxonomy: it|finance|research|...
  seniority_level   TEXT,                 -- the provider's own word: senior|executive|...
  seniority         TEXT,                 -- this app's vocabulary: analyst|VP|head|...
  desk              TEXT,                 -- derived: Rates|Credit|Systematic|...
  -- ------------------------------------------------- where they are
  city              TEXT,
  state             TEXT,
  country           TEXT,
  country_code      TEXT,
  location_raw      TEXT,                 -- "New York City Metropolitan Area"
  timezone          TEXT,
  -- ------------------------------------------------- how to reach them
  email             TEXT,
  email_status      TEXT DEFAULT 'unknown', -- unknown|guessed|verified|bounced|missing
  email_source      TEXT,
  email_confidence  INTEGER,              -- 0-100 as reported by the provider
  email_verified_at TEXT,
  phone             TEXT,
  linkedin_url      TEXT,
  -- ------------------------------------------------- provenance
  sources           TEXT,                 -- "hunter,prospeo" — who contributed
  source_ids        TEXT,                 -- JSON {"prospeo": "id"} so we can re-enrich
  last_seen_at      TEXT,                 -- provider's last-activity signal
  enriched_at       TEXT,                 -- when we last enriched this person
  source_updated    TEXT,
  created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at        TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (first_name, last_name, company_name)
);

-- Career history. This is the "professional background" half of the database and
-- the thing Hunter cannot supply at all; Prospeo returns up to 5 past roles per
-- person. Stored as rows, not JSON, so "who has moved from a bank to a fund" is
-- a join and not a text search.
CREATE TABLE IF NOT EXISTS person_job_history (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id      INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  title           TEXT,
  company_name    TEXT,
  company_id      INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  seniority       TEXT,
  start_year      INTEGER,
  start_month     INTEGER,
  end_year        INTEGER,
  end_month       INTEGER,
  duration_months INTEGER,
  is_current      INTEGER DEFAULT 0,
  source          TEXT,
  created_at      TEXT DEFAULT CURRENT_TIMESTAMP
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

-- ---------------------------------------------------------------- FETCH LEDGER
-- Every external API call is recorded here before its result is trusted. This
-- is what stops a metered source (Hunter: 50 credits/month on the free tier)
-- from being paid for twice: harvest.py checks this table first and only spends
-- a credit on a request key it has not made before. It is also the resume point,
-- so a run killed halfway continues instead of starting over.
CREATE TABLE IF NOT EXISTS fetch_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  provider    TEXT NOT NULL,          -- hunter
  endpoint    TEXT NOT NULL,          -- domain-finder | domain-search | people-find
  request_key TEXT NOT NULL,          -- normalised params, e.g. "domain=janestreet.com"
  http_status INTEGER,
  credits     REAL DEFAULT 0,         -- what this call cost, 0 for free endpoints
  ok          INTEGER DEFAULT 1,
  error       TEXT,
  raw_path    TEXT,                   -- file under data/raw/ holding the response
  response_hash TEXT,
  fetched_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (provider, endpoint, request_key)
);

-- Where the raw payloads live. Kept in the DB (not just files) so a query can
-- find the original response behind any row without guessing a filename.
CREATE TABLE IF NOT EXISTS raw_payload (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  provider    TEXT NOT NULL,
  endpoint    TEXT NOT NULL,
  request_key TEXT NOT NULL,
  path        TEXT NOT NULL,          -- relative to the project root
  sha256      TEXT,
  bytes       INTEGER,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (provider, endpoint, request_key)
);

CREATE INDEX IF NOT EXISTS idx_fetch_log_key       ON fetch_log(provider, endpoint, request_key);
CREATE INDEX IF NOT EXISTS idx_contacts_company    ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_status     ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_outreach_contact    ON outreach(contact_id);
CREATE INDEX IF NOT EXISTS idx_outreach_status     ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_companies_type      ON companies(type);
