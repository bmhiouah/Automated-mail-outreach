# Cold approach — quant finance outreach machine

Local, offline, no account. Target: **Paris + London**, profile **0–2 years experience**,
firms: **banks, hedge funds, prop/HFT, asset managers, commodities, insurance AM**.

## Run it

```bash
cd "/Users/badremhiouah/Desktop/Automatic cold approach"
./run.sh
# open http://127.0.0.1:8765
```

First launch creates `data/cold_approach.db`, loads the company seed and the four
default email templates. Nothing leaves your machine. The database is not kept
in git - it is yours, so it grows by harvesting (`python3 app/harvest.py
--status`), and back it up with `python3 app/tools.py backup`.

Your API keys live in `config.json` (gitignored - a credential, never committed).
Copy `config.example.json` the first time: `cp config.example.json config.json`.

## Layout

```
app/db.py          SQLite layer (schema, seed loader, query helpers)
app/domain.py      shared API helpers: time, JSON, company/contact shapes, Raw
app/taxonomy.py    the title vocabulary (seniority/desk rules) - import this,
                   never the web layer, to derive a desk or a seniority
app/email_gen.py   placeholder rendering, quality flags, default templates
app/server.py      stdlib HTTP shell + the entry point (the endpoints live in app/api)
app/api/           one module per surface: companies, contacts, people, outreach,
                   compose, sourcing, patterns, applications, templates, profile,
                   analytics, export, demo, maintenance + the ROUTES table in
                   app/api/__init__.py. To add an endpoint: write the function in
                   the right module, add one line to its ROUTES, done.
app/tools.py       CLI: brief, dupes, backup, test, hooks
app/cv_parse.py    reads a pasted CV and proposes profile fields
app/discover_careers.py   finds each firm's real careers page (HTTP-verified)
app/harvest.py     the harvester: fills the database from the providers
app/net.py         the one HTTP door: retry, backoff, per-host throttle
app/email_pattern.py   learns a firm's address format, rebuilds addresses
app/people_store.py    identity resolution and merge rules
app/llm.py         the LLM door. One chat call, metered in fetch_log like any
                   other paid source, so re-running costs nothing. Env vars
                   OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL, or an "llm"
                   block in config.json
app/mailer.py      the SMTP door. mail.send_mode defaults to "dry": the message
                   is built and fully checked but never transmitted
app/api/queue.py   the review queue: pick who to write to, draft, edit, send or
                   cancel, auto-advance to the next draft. Also /api/todo (who is
                   left, with filters) and /api/history (who you wrote to)
app/api/cvs.py     CV variants. The base CV is never overwritten; a tailored one
                   is proposed, reviewed, and only then attachable to a mail
app/latex_cv.py    LaTeX resume -> plain text for the profile's cv_text
app/providers/hunter.py    Hunter.io — firm email conventions, bulk domains
app/providers/prospeo.py   Prospeo — targeted people, career history, phones
tests/             the suite on a temp DB: tests/harness.py owns the one
                   temporary database; `python3 app/tools.py test` runs it all
tests/check_ui.mjs node check that every inline handler and id still resolves
web/index.html     markup    web/app.js  UI logic    web/style.css  styles
db/schema.sql      full schema
data/companies_seed.csv   365 firms, editable
data/companies_seed_extra.csv  92 more firms
data/company_aliases.csv  137 nickname → official-name mappings
data/firm_briefs.csv      51 firm intel notes (what they do, how they hire, the hook)
data/careers_urls.csv     83 verified careers pages (tier 1 complete, tier 2 partial)
data/cold_approach.db     your data (not in git - back it up)
```

Tables: `companies` · `contacts` · `person_job_history` · `outreach` · `templates` ·
`profile` · `applications` · `pattern_evidence` · `fetch_log` · `raw_payload` ·
`mail_queue` · `cv_variants` · `contact_touchpoints`.

## The Queue — the whole workflow, top to bottom

The **Queue** tab is where you contact people, and it is built around one rule:
**a draft is always on screen, and Send or Cancel moves you to the next one by
itself.** There is no list to click through.

**1 · Who to write to** (top left). Everyone you have *not* contacted, read from
the touchpoint ledger — so nobody you already emailed can appear, not even if the
send failed or you cancelled it. Filter by city, firm type, desk, tier, or search
a name/firm/address; tick people, choose a **model**, and press **Draft**. Each
draft is one model call written from scratch for that person, and the model that
wrote it is recorded on the row.

**2 · The mail** (top right). Address, subject, body, editable as much as you like.
Above the buttons you get:

- **What this draft is built on** — the facts the model says it used, so a claim
  can be checked against the database before you send it.
- The address's trust: `verified`, or `guessed` for a reconstruction, which needs
  an explicit confirmation even after you approve.
- Everything the mail-quality checker objects to.

**Send** sends it. **Cancel** deletes the draft — nothing is sent, and the person
stays in your "not contacted" list (you can always regenerate it for free). **Skip**
moves on and decides nothing — it comes back to you later. Either way the next
draft loads itself.

**3 · Waiting on you.** The full list of drafts, for the ones you skipped. Click
any row to bring it back up. This is a safety net, not the way through the queue.

**4 · Who you have contacted.** The append-only ledger.

### Sending is off by default

`mail.send_mode` is **`dry`** until you change it. In dry mode the message is
built, every check runs, and nothing is transmitted — so you can rehearse the whole
flow against real contacts before anything is at risk. To go live, add a `mail`
block to `config.json` (see `config.example.json`; Gmail wants a 16-character
**app** password, not your account password) and set `"send_mode": "live"`.

Even live, two things still stop a mail: a **reconstructed** address needs an
explicit confirmation, because a guessed address that bounces is worse than no
mail at all; and there is a **daily cap** (40 by default) read from the ledger, so
it survives a restart.

### Follow-ups go through the queue too

A bump is queued like any other draft, with the original subject kept so it
threads. It used to be the one mail you could send without reading it, which is
exactly the thing this workflow exists to prevent.

## The CVs tab — one base document, many adaptations

Your base CV stays in **My profile** and is never overwritten. The CVs tab proposes
a tailoring for a given firm and role; it arrives **pending**, you read and edit
it, and only a **validated** variant can be attached to a queued mail. That gate is
why an unreviewed document can never go out with your name on it.

The base **`Badre_Mhiouah_CV.tex`** is listed alongside the variants, at the top, labelled
**me** — it is a CV like any other and is never itself written to. Clicking any row
opens it and **recompiles it on the spot**, so the preview always reflects your
latest save rather than whatever last happened to be compiled. **Delete** removes a
variant and its PDF; it refuses two things, both because the loss would be silent —
the base document, and a variant an unsent queued mail still points at (detach it in
the Queue tab first).

## Writing the mails

Add to `config.json` (or export env vars — the environment wins, so a key need
never touch the file):

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"   # any OpenAI-compatible endpoint
export OPENAI_MODEL="openai.gpt-oss-120b"
```

Check it without spending anything, then spend one real call to be sure:

```bash
python3 app/llm.py           # is it configured?
python3 app/llm.py --probe   # one real call
python3 app/llm.py --models  # what this endpoint will actually serve
```

The model dropdown in the Queue tab is filled from that last list, minus the
models measured to reject `/chat/completions` on your gateway — on a Bedrock
endpoint `/models` advertises many models that answer `access_denied` or reject
the API outright. Pick a different model per batch; each row records which one
wrote it, so you can split reply rate by model later.

The model is given a closed block of facts — the contact, their career history,
the firm's brief and live hiring signal, one recent-news line from Tavily (needs
a key; '(unknown)' otherwise), your profile — and told explicitly not to
invent anything that is not in it. It must follow your shape:

1. `Hi <name>,`
2. how you came across them, what they actually do, one current-news line if
   Tavily found one, and why you specifically
3. a broad professional identity, current focus, and one project (with its GitHub link)
4. **the ask** — *Could you let me know if your team, or a related team, is looking
   for someone with this mix of market and AI experience?*
5. an offer of a brief 10–15 minute chat at their convenience
6. a line saying the CV is included (the LinkedIn lives in the signature)
7. a thank-you by name, then `Best,`
8. the sign-off — **written by the code**, not the model, because models invent
   phone numbers and profile URLs.

Its drafts are graded by the same `email_gen` checker your templates use, and the
facts it says it used are shown above the mail so a claim can be checked against
the database before you send it.

## Harvesting — how the database fills itself

You run one command; the database grows. Run it again and it resumes instead of
starting over.

```bash
python3 app/harvest.py --source prospeo --pages 2        # find quant people
python3 app/harvest.py --source hunter  --tier 1         # firm email conventions
python3 app/harvest.py --status                          # how full is it
python3 app/harvest.py --reconstruct                     # rebuild addresses
```

Two providers, each used for what it is actually good at:

- **Prospeo finds the right people.** It filters by job title *and* location, so
  you get quants in London rather than whoever a firm happens to employ. It also
  supplies career history, phone numbers and the firm profile. Hunter cannot do
  any of this.
- **Hunter knows the firm's address format.** Its Domain Search returns
  `data.pattern` (`{first}.{last}`) for free, which is the seed for the whole
  reconstruction engine below.

### The email convention — the part that compounds

A firm's address format belongs to the **firm**, so it lives on the company row.
Once one real address is known there, every later person can be addressed for
free:

```
one real address  jane.doe@amundi.com  →  companies.email_pattern = "first.last"
                                       →  Marc Dupont → marc.dupont@amundi.com
```

Learned deterministically: we test every known convention against the real
(name, address) pair. Exactly one match is a proof (0.95). Several matches is
genuine but ambiguous evidence (0.6) — `annasmith` is both `firstlast` and
`flast`. No match teaches nothing rather than guessing, because a wrong
convention silently mis-addresses everyone at that firm.

Reconstructed addresses are written as `email_status = 'guessed'`, never
`'verified'`, so a guess can never overwrite real evidence.

### Never pay twice

Every call goes through `fetch_log` and its raw response is kept under
`data/raw/<provider>/`. A request already made is not made again, so a second
run over the same firms makes zero calls and spends zero credits. `--budget` is
a hard stop, and the next run picks up where it left off.

### What nothing can tell us

Neither provider sells **education** or degrees. `seniority` is derived from
titles and is crude — "Vice President Middle Office" is not C-suite — so
`seniority_level` (the provider's own word) and `position_raw` (verbatim) are
kept alongside it, and the verbatim title is never rewritten. If you need
education, that needs a third source (OpenAlex/arXiv).

### Keys

Both keys live in `config.json`, which is gitignored and `chmod 600`:

```json
{ "hunter_api_key": "...", "prospeo_api_key": "..." }
```

They are never written to the database, never printed, and never logged.

## Roadmap

| Phase | Status | What it does |
|---|---|---|
| 0. Foundations | **done** | schema, web app, import, templates, tracking |
| 1. Company universe | **done** — 365 firms | 41/41 tier-1 careers pages verified; expand on demand |
| 2. Email resolution | **done** | paste one real address → pattern learned for the whole firm |
| 3. Contact sourcing | **done** | playbook, per-firm X-ray search, and a prioritised worklist |
| 3b. Firm intel | **done** — 51 briefs | per-firm hook material, editable, available in templates |
| 3c. Contact capture | **done** | paste any shape of people text → reviewed table → contacts, and a real address teaches the pattern |
| 4. Profile + narrative | **waiting on your CV** | paste it in the Profile tab and the reader fills the card |
| 5. Analytics loop | **done** | funnel + reply rate by firm type, tier and template |
| 6. Applications tracker | **done** | separate channel from cold outreach, tracked apart |
| 7. Follow-ups | **done** | threads with `Re:`, asks one question, clears the original from the due list |

## Using it

The loop is: **find a person → capture them → mail them → follow up.**

- **Contacts → Paste people** — copy a block of LinkedIn search results, a firm's team
  page, a spreadsheet selection, or just a list of names, and press Read. It handles
  tabs, pipes, dashes, commas, `Title at Company`, and bare names; it stitches a
  two-line LinkedIn profile (`name` then `Title at Company`) into one person and pulls
  the city off the line below. You get a table with a confidence score and a plain-English
  *why* for each row, every field editable, nothing saved until you press Save. Lines it
  cannot read are listed rather than guessed at.
- **The address that pays for itself** — if a pasted person comes with a real email
  address, that address is matched against the person at that firm and, if it fits,
  the firm's convention is learned and applied to every other contact there. If it does
  not match a known name, the domain is kept and the convention is *not* guessed at.
- **Companies tab** — filter "no contacts yet" to see untouched firms (41 tier-1 firms
  are still empty). "Find people" opens a LinkedIn X-ray search for that firm with the
  right job titles for its type, plus a careers search. Nothing is scraped: it builds
  the query, you do the looking.
- **Applications tab** — formal applications, tracked separately from outreach. Mixing
  the two channels makes you misread which one works.
- **Dashboard** — conversion funnel (found → contacted → sent → replied → positive),
  reply rate by firm type and by template, and coverage (how many firms have contacts
  and a known email pattern).
- **Companies → Verify an email pattern** — one real address teaches the firm's convention.
- **Contacts → Fill empty emails from patterns** — populates guesses, flagged `guessed`.
  A pasted address is stored as `verified`, so a guess never overwrites real evidence.
- **Outreach → Follow up** — bumps the original with `Re:` threading, keeps the same
  contact and firm intel, and stops the original appearing in the due list.

## Phase 1 — what is in the seed

365 firms: 100 hedge funds · 80 asset managers · 68 banks · 41 prop/HFT ·
22 commodities · 20 data/software · 16 insurance AM · 13 crypto · 5 brokers.
Tier 1 = biggest quant hiring and best fit for a Paris/London junior.
Two seed files, both idempotent: `data/companies_seed.csv` and
`data/companies_seed_extra.csv`. Add rows anywhere, re-run the seed, nothing duplicates.

## Firm intel — the hook problem

The hardest part of a cold email is knowing something specific about the firm. A generic
"your firm is a leader in quantitative finance" is why most cold emails get deleted.

`data/firm_briefs.csv` holds 32 briefs, one per high-priority firm — what they actually do,
how they hire, and a **Best hook:** line naming the thing to lead with. Examples: Jane Street
→ "a concrete estimation or trading puzzle you worked through"; Millennium → "a specific
team's strategy, cold outreach to PMs genuinely works here"; Flow Traders → "the Paris
build-out itself, or the mechanics of ETF creation and redemption".

Where it shows up:

- **Companies tab → Brief** opens the note for that firm.
- **Compose tab** shows the brief above the generated mail, with the hook pulled out and a
  copy button.
- In any template, `{{company_research}}` is the full note and `{{company_hook}}` is just
  the hook sentence.

Edit any brief in place — inline cell editing writes back to the database and stamps
`research_updated`. Re-running the loader only overwrites rows whose text actually changed,
so your edits survive a restart. To add a firm, append a row to `firm_briefs.csv`
(`name,research`) and restart, or just type it into the database.

A brief counts as a personalisation hook for scoring purposes: a firm-specific hook is
enough to avoid the "reads as bulk mail" flag, though a per-person hook is still better.

## The CV reader — filling the profile for you

The profile is the one input the tool cannot invent, and typing sixteen fields is the reason
it stays empty. So: **paste your CV into the Profile tab and press "Read my CV".**

`app/cv_parse.py` extracts 17 fields — name, headline, email, phone, LinkedIn, GitHub,
website, city, education, years of experience, skills, languages, target roles, projects,
achievements and a pitch paragraph. For each one it also shows **where it came from**, so a
"hit" is something you can check rather than trust.

The design rules, which matter more than the code:

- **It proposes, it never saves.** You review the table, correct what's wrong, press "Fill
  the form", then save. A CV is a document written by a human for a human; anything claiming
  to parse one perfectly is lying, so the human stays in the loop.
- **Nothing is invented.** A field with no evidence comes back empty and is listed under
  "not found" rather than filled with something plausible.
- **It reports what it *couldn't* find**, and separates the fields that actually matter
  (name, skills, projects, pitch, education) from the ones that are merely nice to have.

Things it gets right that are easy to get wrong:

- **French CVs.** Section headers (`FORMATION`, `COMPÉTENCES TECHNIQUES`, `LANGUES`) and
  language names (`Français`, `Anglais`, `Espagnol`) are understood and normalised to
  English, because the templates interpolate `{{my_languages}}` into English sentences.
  `Français (langue maternelle)` becomes `French (native)`.
- **"two years of experience"** — spelled out, not a digit.
- **Overlapping date ranges** are merged before being totalled, so two concurrent roles are
  not double counted.
- **`2018-2020` is not a phone number.** Candidates are filtered on digit count, and a
  country code or trunk zero wins.
- **`target_roles` and `key_skills` are sentence-ready.** They get interpolated into
  "looking for a {{my_target_roles}} role", so a six-item keyword dump would produce
  gibberish. Roles are capped at two and deduplicated by head noun; skills at twelve, with
  `{{my_key_skills_short}}` (first four) available for use mid-sentence.

## Sourcing worklist — the bridge to actually sending mail

A database is not a job. The **Sourcing** tab turns the company universe into an ordered
worklist of firms to go find people at — 41 tier-1 firms, or 102 across tiers 1–2.

Each row carries everything needed to act without opening another tab:

- the **hook** from the firm brief (or a note that there isn't one yet)
- the **target job titles** for that firm type — e.g. hedge funds get *Quantitative
  Researcher / Portfolio Manager / Quant Developer*, banks get *Strata / Structurer / Trader*
- the **LinkedIn X-ray query**, copyable in one click
- a link to the **careers page**, and a **+ Add contact** button that jumps to the
  Contacts tab with the company pre-filled
- a **readiness score** (0–3: careers page known, hook written, domain known) so you can
  see at a glance which firms are fully armed

Ordering is deliberate: **tier first, then firms that already have a hook** — so the
easiest, highest-value targets are at the top rather than alphabetically buried.

**Download as checklist** writes the whole worklist as Markdown with tick boxes
(`find 2-3 people` / `first email sent` / `follow-up scheduled`), so you can work through it
offline or print it. That's `GET /api/export/sourcing`.

Nothing is scraped. The tool builds the query; you do the looking and paste what you find
into Contacts.

## Careers pages — found, not guessed

`data/careers_urls.csv` holds the real careers page for all **41 tier-1 firms** (plus 42 of
61 tier 2), and `app/discover_careers.py` is what found them:

```bash
python3 app/discover_careers.py --tier 1        # discover and write the CSV
python3 app/discover_careers.py --tier 1,2 --workers 10
```

It fetches each firm's homepage, follows any link that looks like a careers link, then
falls back to trying common paths. **A URL is only recorded if the server actually
answered.** Nothing is invented.

The first version of this produced confident nonsense — Bank of America resolved to
`/student-banking/`, BlackRock to `/corporate/home`, Morgan Stanley to `/people`. Fixed by
requiring the careers keyword to appear in the URL *path*, not just in the link text. Those
five bad results are now regression tests.

Two useful things it turned up:

- **Marshall Wace's real domain is `mwam.com`**, not `marshallwace.com` — the seed had it
  wrong, which is why it kept failing. Corrected in `companies_seed.csv`.
- **ExodusPoint advertises roles only through a Greenhouse job board**
  (`job-boards.greenhouse.io/exoduspoint`), so the marketing site has no careers page at
  all. The validator now recognises ATS hosts (Greenhouse, Lever, Workday, Ashby, …) as
  careers pages in their own right.

A handful of URLs return **403** to a script (Citadel, Citadel Securities, BNP Paribas,
UBS, QRT) — that is a bot firewall, not a missing page. Those are recorded with their HTTP
status so you can tell a verified page from a blocked one.

Companies tab: `Careers ↗` opens the page directly, and the `Brief` modal shows it too.
"Find people" opens the real careers page when one is known, and falls back to a Google
search when it isn't — so the button never opens a blank tab.

**The crawler merges, it does not overwrite.** Running it for tier 2 used to rewrite the
file and silently wipe every tier-1 result (and drop the `http_status` column). It now reads
what's there, updates only what changed, and keeps curated statuses. Both of those are
regression tests.

## Phase 2 — how email resolution works

1. Find **one** real address at a firm (signature block, press release, GitHub, PDF).
2. Companies tab → "Verify an email pattern" → paste it. The tool:
   - if that person is already in your contacts, matches **exactly** (confidence 0.85–0.95);
   - otherwise infers from the shape (`anna.smith@` → `first.last`, confidence 0.5)
     and tells you to double check;
   - stores the sample in `pattern_evidence`, sets the firm's pattern and confidence.
3. "Fill empty emails from patterns" then writes addresses for every contact at firms
   with a known pattern, flagged `email_status = guessed`.
4. Rule: a guess is a lead, not a fact. Send guesses in batches of five, watch bounces.

Supported patterns: `first.last` · `first_last` · `f.last` · `flast` · `firstlast` ·
`firstl` · `last.first` · `lastfirst` · `first`.

## The paste reader — where contacts actually come from

You will never get clean CSV out of LinkedIn or a firm's team page. You get a blob. The
importer above wants strict columns; the reader below takes the blob.

Paste anything and press **Read**. It handles, in one pass:

| Shape | Example |
|---|---|
| Tab-separated from a spreadsheet | `Marc Dupont⇥Quantitative Analyst⇥BNP Paribas⇥marc.dupont@bnpparibas.com` |
| Pipes, semicolons, dashes | `Anaïs Lefèvre — Quantitative Developer — Marshall Wace` |
| A LinkedIn search result | `Jane Doe · 2nd` / `Quantitative Researcher at Jane Street` / `Paris, Île-de-France, France` |
| No separators at all | `Tom Baker Trader Citadel` |
| Just names, with a default firm | `Alice Martin` |
| A list of bare addresses | `elena.moreau@barclays.com` |

It strips LinkedIn chrome (`· 2nd`, `500+ connections`, `See more`), honorifics and
credentials (`Dr. Jane Doe, CFA, FRM` → `Jane Doe`), and keeps European particles with the
surname (`Willem de Vries` → first `Willem`, last `de Vries`). A two-line profile is
stitched into one person, and the city on the line below is attached to them.

Every row gets a **confidence** and a plain-English **why** (`name, title, company (known
firm), real address found`). Anything below 0.6 arrives unticked. Lines it cannot read are
listed under the table with a reason instead of being guessed at.

Two traps the resolver sets, both now closed and both regression-tested:

- `Quantitative Developer Marshall Wace` resolves to *Marshall Wace* and swallows the job
  title. So a firm must cover ≥75% of the chunk's words before it counts as the company.
- Bare desk words resolve to firms — `trading` → Jump Trading, `research` → Qube Research,
  `portfolio` → IPM Informed Portfolio Management. So a single-word chunk must match a
  single-word firm exactly.

The rule throughout: **under-matching is fine, over-matching is silent and wrong.** A firm
we fail to recognise keeps its raw text and is flagged. A firm we wrongly recognise
corrupts the row without telling you.

### The address that pays for itself

If a pasted person comes with a real address, that address is matched against the name at
that firm. If it fits a convention, the firm's pattern is learned and applied to every
other contact there — paste one person with a real address and the whole firm unlocks. If
it does *not* match a known name, the domain is kept and the convention is deliberately
**not** applied: one address tells you the domain, not the pattern. It is reported as
"told us the domain but not the convention" rather than quietly saved as a 0.5 guess.

## Import tolerance

Real exports are messy, so the importer resolves companies three ways:
exact name → **alias table** (`data/company_aliases.csv`, 137 entries: `SG`,
`Societe Generale CIB`, `Merrill Lynch`, `CACIB`, `Millennium`, …) → longest
company name contained in the string. Accents and punctuation are ignored, so
`Société Générale Corporate & Investment Banking` resolves correctly.
LinkedIn's `Connections.csv` format (`First Name, Last Name, Company, Position, URL`)
is recognised natively.

Job titles are auto-tagged on import into **desk** (Rates, Credit, FX, Vol, Macro,
Equity Derivatives, Structuring, Systematic, Quant, Crypto, Commodities…) and
**seniority** (analyst → associate → VP → director → MD → head). Existing values are
never overwritten. Contacts → "Auto-tag desk & seniority" applies it retroactively.

## Email quality scoring

Every generated mail is graded before you send it (and "Check quality" re-scores your
edits). It penalises, by name: banned openers and filler ("I hope this email finds you
well", "passionate", "great fit", "touch base"), a first line that doesn't use their
first name, no question, subject over 65 characters, more than 150 words, more than five
paragraphs, unresolved placeholders, missing hook, and SHOUTING.

It also catches the failure that is invisible to every other check: **a variable that
resolved to nothing.** An empty profile turns `I'm {{my_full_name}}, {{my_education}}` into
`I'm , ` — the placeholders are gone, so the "unresolved placeholders" check stays quiet and
the mail scores 95 while being unsendable. Both the generator and "Check quality" now report
exactly which fields are empty behind the text.

This is not decoration. Running it against the original default template scored **B-87**
and flagged a 66-character subject and six paragraphs — the templates were rewritten
because of it. Current defaults score 100 with the sample profile filled in.

## Tests

```bash
python3 app/tools.py test     # the suite, on a temp DB, never touches your data
node tests/check_ui.mjs       # the front-end half of the same promise
```

They cover company resolution (exact/alias/substring/accents), title tagging, email pattern
guessing and inference, scoring rules, firm-brief hook extraction, empty-variable detection,
careers-URL validation (including the false positives the first crawler produced and the
overwrite bug), CV parsing in both English and French, template rendering, and the paste
reader (tabs, dashes, LinkedIn stitching, credential stripping, the desk-word trap, and the
pattern a pasted address does and does not teach). Plus the guarantees the layout above
rests on: the data layer never imports the web layer, and every one of the 45 API routes
resolves and refuses correctly. Run them after any edit to the logic — the
whole point is that a future change can't silently relabel your contacts.

## Command line

```bash
python3 app/tools.py brief           # follow-ups due, coverage, next firms to work on
python3 app/tools.py dupes           # contacts sharing an email or a name
python3 app/tools.py backup          # timestamped copy of the database
python3 app/tools.py hooks           # the hook line for every firm you have intel on
python3 app/tools.py hooks --missing # tier-1 firms still without a brief
```

## What I need from you next

1. **Your CV** — paste it into the Profile tab and press "Read my CV". It will fill most of
   the card for you; you correct and save. This is the current bottleneck and it is now a
   two-minute job. Nothing downstream can be sent until the profile has a name, a pitch and
   some skills in it — the generator will refuse to hand you a mail with holes in it.
2. **Ten target desks**, ranked. "Systematic equity at multi-strategy funds" beats
   "anything in finance". It decides who we contact first.
3. **Any confirmed email addresses** you already have at any firm. One real sample per
   firm unlocks the whole pattern engine for that firm — and pasting it alongside a name
   now does that automatically.
4. **Constraints**: notice period, visa/work authorisation in the UK, languages,
   firms you refuse to work for.

Then the first working session is: pick the top 10 firms off the Sourcing tab, run the
X-ray search for each, paste the results into Contacts → Paste people, and send. Ten
firms is a realistic first sitting.

Meanwhile, `docs/playbook.md` is actionable on its own — LinkedIn X-ray strings,
desk/title maps, and how to write a hook that gets a reply.

## Notes on volume and tone

Cold email works at small volume with high personalisation. The tool is built around a
`hook` field on every contact for exactly that reason — if the hook is empty, the
generator flags the mail before you send it. GDPR: keep outreach to professional
addresses, keep it relevant, and stop on request.
