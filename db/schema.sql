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
  pattern_before TEXT,                   -- the local-part convention: first.last, f.last, ...
  pattern_after  TEXT,                   -- the domain after the @: amundi.com.
                                          -- A masked Prospeo address ("s****@amundi.com")
                                          -- proves this half even before anyone is revealed.
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
-- The unified table: the vertical concat of contacts_hunter and
-- contacts_prospeo. Column groups, in the order a person is read:
-- who · where they work · what they do · where they are · how to reach them.
CREATE TABLE IF NOT EXISTS contacts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name    TEXT NOT NULL,
  last_name     TEXT NOT NULL,
  full_name     TEXT,
  -- ------------------------------------------------- where they work
  company_id    INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  company_name  TEXT,                     -- kept denormalised so imports never fail
  source        TEXT,                     -- hunter|prospeo for harvested rows
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
  email             TEXT,                 -- a real address, or a reconstruction
  email_masked      TEXT,                 -- Prospeo's masked address ("s****@blackrock.com"),
                                          -- next to the mail it may one day become.
                                          -- NOT an address: never sent, never fed to the
                                          -- pattern engine. The reconstruction pass
                                          -- replaces it once the firm's convention is known.
  email_source      TEXT,
  phone             TEXT,
  linkedin_url      TEXT,
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
  departments     TEXT,                   -- comma separated (Prospeo per-job taxonomy)
  source          TEXT,
  created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------- RAW PER-SOURCE CONTACTS
-- The full provider payload, one table per source, exactly as the API gave it.
-- `contacts` is the curated vertical concat of the two: the unified fields we
-- work with, plus a `source` column saying Hunter or Prospeo. Anything the
-- unified table does not keep still lives here, so nothing a provider offers
-- is ever lost - including masked phone variants we may want later.
--
-- A row here is written on every harvest, before the merge into `contacts`,
-- and is keyed on the provider's own identifier so a re-run updates in place.

CREATE TABLE IF NOT EXISTS contacts_hunter (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  domain          TEXT,                   -- the domain we searched
  email           TEXT,
  email_type      TEXT,                   -- personal|generic
  confidence      INTEGER,                -- Hunter's 0-100
  first_name      TEXT,
  last_name       TEXT,
  position        TEXT,                   -- cleaned title
  position_raw    TEXT,                   -- verbatim
  seniority       TEXT,
  department      TEXT,
  decision_maker  INTEGER,
  linkedin        TEXT,
  twitter         TEXT,
  phone_number    TEXT,
  verification_status TEXT,
  verification_date   TEXT,
  sources         TEXT,                   -- JSON: where the address was seen
  company_name    TEXT,
  fetched_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (domain, email)
);

CREATE TABLE IF NOT EXISTS contacts_prospeo (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id       TEXT UNIQUE,            -- Prospeo's stable id; the merge key
  first_name      TEXT,
  last_name       TEXT,
  full_name       TEXT,
  linkedin_url    TEXT,
  linkedin_member_id TEXT,
  current_job_title   TEXT,
  current_job_key     TEXT,
  headline        TEXT,
  last_job_change_detected_at TEXT,
  -- email block, exactly as Prospeo returned it (masked forms included)
  email           TEXT,                   -- masked or real, verbatim
  email_revealed  INTEGER,                -- 0 until enrich-person paid for it
  email_status    TEXT,                   -- VERIFIED / ...
  email_verification_method TEXT,
  email_mx_provider TEXT,
  -- phone block: all three variants the API can give
  mobile              TEXT,               -- international, possibly masked
  mobile_national     TEXT,               -- possibly masked
  mobile_international TEXT,
  mobile_status   TEXT,
  mobile_revealed INTEGER,
  mobile_country  TEXT,
  mobile_country_code TEXT,
  -- location
  city            TEXT,
  state           TEXT,
  country         TEXT,
  country_code    TEXT,
  time_zone       TEXT,
  -- the rest, kept as JSON so nothing is dropped
  skills          TEXT,                   -- JSON list
  job_history     TEXT,                   -- JSON: up to 5 past roles, verbatim
  company         TEXT,                   -- JSON: the firm profile, verbatim
  fetched_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
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
  -- The standing instruction for tailoring main.tex. Saved rather than retyped
  -- because it is the actual specification of what you want changed, and the
  -- same wording is what makes two variants comparable.
  cv_instruction TEXT,
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

-- ---------------------------------------------------------------- MAIL QUEUE
-- A mail the machine drafted, waiting for a human to decide. Nothing is ever sent
-- from here. A row only becomes an `outreach` row when you validate it, and
-- `sent` is set by the mailer that actually handed it to an SMTP server - never
-- by the generator.
--
-- `generation` records HOW the draft was produced (llm:<model> | template:<id> |
-- manual), so an analytics view can compare the machine's prose with your own
-- templates instead of guessing from timestamps.
CREATE TABLE IF NOT EXISTS mail_queue (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id    INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  company_id    INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  -- pending  -> drafted, awaiting review. The only state a new row may sit in.
  -- validated -> you approved the text. Not sent yet - see sent_at.
  -- sent     -> handed to SMTP. sent_at and message_id are set.
  -- failed   -> approved but the SMTP call failed; the reason is in send_error.
  --
  -- There is no `cancelled`: rejecting a draft DELETES the row. The same prompt
  -- is cached in fetch_log, so a discarded draft can be regenerated for free and
  -- keeping it only makes the queue longer. Cancel refuses to touch a `sent` row,
  -- which is the one record that a piece of text actually reached a person.
  -- Editing is not a state: a row being edited is still `pending`.
  status        TEXT DEFAULT 'pending',
  channel       TEXT DEFAULT 'email',      -- email now; linkedin later
  to_addr       TEXT,                      -- resolved at draft time, editable
  subject       TEXT,
  body          TEXT,
  -- Why this address: verified | pattern (a reconstruction) | none. Copied from
  -- the contact at draft time and NOT re-derived, because the decision to send
  -- must be made about the address the user actually saw in the queue.
  addr_kind     TEXT DEFAULT 'none',
  template_id   INTEGER REFERENCES templates(id) ON DELETE SET NULL,
  cv_id         INTEGER REFERENCES cv_variants(id) ON DELETE SET NULL,
  generation    TEXT,                      -- llm:gpt-4o | template:3 | manual
  model         TEXT,
  prompt_tokens INTEGER DEFAULT 0,
  -- The machine's own score, kept so a bad batch can be spotted after the fact.
  quality       TEXT,                      -- JSON from email_gen.score_email
  -- Which facts the model said it used, as JSON. Stored so a draft stays
  -- auditable: "why did it mention that" is answerable months later, and a claim
  -- in a mail can be checked against the row it claims to come from.
  specifics     TEXT,
  -- Your words won over the machine's. Worth counting: the only honest measure
  -- of how much the generator is actually helping.
  edited        INTEGER DEFAULT 0,
  reviewed_at   TEXT,
  sent_at       TEXT,
  message_id    TEXT,                      -- the SMTP Message-ID, for the thread
  outreach_id   INTEGER REFERENCES outreach(id) ON DELETE SET NULL,
  send_error    TEXT,
  notes         TEXT,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------- CV VARIANTS
-- Your CV, tailored. The base stays in `profile.cv_text` and is never overwritten
-- by a variant: one master document, many adaptations, so a bad adaptation costs
-- a click and not a rewrite. `parent_id` is the variant this one was derived
-- from, which makes "revert to base" a lookup instead of a judgement.
CREATE TABLE IF NOT EXISTS cv_variants (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,              -- "Jane Street - quant research"
  body         TEXT,
  parent_id    INTEGER REFERENCES cv_variants(id) ON DELETE SET NULL,
  -- Which contact/company this was written for. Loose on purpose: a variant is
  -- often written for a ROLE at a firm, before any single person is known.
  company_id   INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  contact_id   INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  role_target  TEXT,
  generation   TEXT,                       -- llm:<model> | manual
  -- The LaTeX branch. `body` stays the plain-text rendering of whatever the
  -- document says, because that is what the queue scores and what a reader
  -- sees; `latex` is the source a PDF is compiled from. Only a PDF can be
  -- attached to a mail, so `pdf_path` is what makes a variant sendable at all.
  latex        TEXT,
  pdf_path     TEXT,                       -- data/cvs/<slug>.pdf, project-relative
  pdf_at       TEXT,                       -- when it last compiled successfully
  instruction  TEXT,                       -- the instruction that produced it
  -- pending  -> proposed by the model, never reviewed
  -- validated-> you accepted it; may now be attached to a queued mail
  -- rejected -> you threw it away. Kept for the same reason cancelled mails are.
  status       TEXT DEFAULT 'pending',
  edited       INTEGER DEFAULT 0,
  reviewed_at  TEXT,
  notes        TEXT,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------- TOUCHPOINTS
-- Append-only. One row per real outbound contact, written the moment a mail is
-- accepted by SMTP and never updated afterwards.
--
-- This is the memory of who has been approached, and it is deliberately separate
-- from `outreach`: outreach is a workflow row you edit (status, follow-up date,
-- reply snippet), while this is a ledger. Editing your notes on a thread must
-- never be able to erase the fact that you wrote to someone - otherwise "who
-- have I already contacted" silently starts returning people you already
-- emailed, and the tool mails them twice. That failure is the whole reason this
-- table exists rather than a query over outreach.
CREATE TABLE IF NOT EXISTS contact_touchpoints (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  company_id   INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  queue_id     INTEGER REFERENCES mail_queue(id) ON DELETE SET NULL,
  outreach_id  INTEGER REFERENCES outreach(id) ON DELETE SET NULL,
  channel      TEXT DEFAULT 'email',
  direction    TEXT DEFAULT 'outbound',    -- outbound now; inbound later
  to_addr      TEXT,
  subject      TEXT,
  -- verified | pattern | none: the SAME value the queue row carried, so the
  -- ledger remembers how trustworthy the address was at the moment of sending.
  addr_kind    TEXT DEFAULT 'none',
  sent_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  message_id   TEXT
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
  credits     REAL DEFAULT 0,         -- MONEY: what a metered provider charged.
  -- tokens is separate: the LLM has no credits, and putting prompt_tokens in the
  -- credits column made the running total unreadable as a budget.
  tokens      INTEGER DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_outreach_contact    ON outreach(contact_id);
CREATE INDEX IF NOT EXISTS idx_outreach_status     ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_companies_type      ON companies(type);

-- The queue is read by status ("what is waiting on me") far more often than by
-- anything else, and the touchpoint ledger is only ever asked "has this person
-- been contacted, and when".
CREATE INDEX IF NOT EXISTS idx_queue_status        ON mail_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_contact       ON mail_queue(contact_id);
CREATE INDEX IF NOT EXISTS idx_cv_status           ON cv_variants(status);
CREATE INDEX IF NOT EXISTS idx_touch_contact       ON contact_touchpoints(contact_id);
CREATE INDEX IF NOT EXISTS idx_touch_sent_at       ON contact_touchpoints(sent_at);
