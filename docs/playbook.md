# Sourcing playbook

How to fill the `contacts` table with people worth emailing. The tool is useless with an
empty contact list, and this is the part you can start on today without me.

## 1. Who to target, by seniority

Do not email the MD running a 40-person desk. You will not get a reply and you will burn
the firm. Target in this order:

| Who | Why |
|---|---|
| Analysts 1–3 years in | They remember being you, they reply, they forward to the desk head |
| Desk heads at small firms | At a 15-person fund the head IS the hiring decision |
| Quant researchers publishing or speaking | They respond to substance |
| Recruiters | Only as a fallback, and only after a direct contact has gone cold |

Skip: CEOs, HR generic inboxes, anyone clearly senior enough to have an assistant.

## 2. Where the names actually come from

- **LinkedIn X-ray** (works without Sales Navigator): search on Google
  `site:linkedin.com/in "Qube Research" ("Quantitative Researcher" OR "Quant Researcher")`
  Repeat per firm. This is the single highest-yield source.
- **Alumni directories** — your school's LinkedIn alumni tab filtered by company. An
  alumnus is 5x more likely to answer and is a legitimate hook.
- **Conference speaker lists** — Quant Strats, Global Derivatives, RiskMinds, WBS
  Quant Conference, Battle of the Quants. Speakers are public and senior enough to matter.
- **Papers and code** — arXiv/SSRN authors at systematic funds, GitHub contributors at
  prop shops. A specific comment on their work is the best hook there is.
- **Podcasts** — *Flirting with Models*, *Top Traders Unplugged*, *Chat With Traders*.
  Guests are named, quotable, and reachable.
- **Firm websites** — smaller funds still list their team with photos and bios.

Paste whatever you collect into the Contacts tab (CSV import, header auto-detected).

## 3. Desk and title map

Search these exact title strings, per firm type:

- **Banks**: `Quantitative Analyst` · `Strats` · `Quantitative Strategist` ·
  `Exotic Derivatives Trader` · `Structurer` · `Rates Trader` · `Credit Trader` ·
  `Flow Derivatives` · `QIS`
- **Hedge funds**: `Quantitative Researcher` · `Systematic Researcher` ·
  `Portfolio Manager` · `Quant Developer` · `Alpha Researcher`
- **Prop / HFT**: `Quantitative Trader` · `Trading Analyst` · `Quantitative Developer` ·
  `Low Latency Engineer` (only if you are a strong C++ person)
- **Asset managers**: `Quantitative Analyst` · `Systematic PM` · `Risk Analyst` ·
  `Index Research`
- **Commodities**: `Quantitative Analyst` · `Gas/Power Trader` · `Origination Analyst` ·
  `Freight Analyst`

## 4. Writing the hook

The `hook` field is the only thing separating a reply from a deletion. Four archetypes
that work:

1. **Their work** — "I read your paper on intraday momentum and tried it on CAC40
   constituents; the decay was faster than you report on US names."
2. **Shared ground** — "I saw you did the M2 at [school] — I'm finishing the same
   programme now."
3. **Their firm's specific move** — "Your team's shift into systematic credit is the
   exact intersection I've been working on."
4. **A real question** — not flattery, a question only they can answer.

Banned: "I am passionate about financial markets", "I would be a great fit for your
team", anything that would survive being sent to 500 other people.

## 5. Volume and hygiene

- 10–15 highly targeted emails beat 200 generic ones. Every time.
- Never send a guessed address in bulk. Batch of 5, check bounces, adjust.
- One follow-up, +7 days, then stop. The tool enforces this.
- Log every reply, including negative ones. The firm stays warm for next year.
- Professional addresses only, keep it relevant, stop immediately on request (GDPR).

## 6. Weekly cadence

1. Monday: pick 10 firms from tier 1/2, find 2 contacts each.
2. Tuesday–Wednesday: write hooks and generate mails.
3. Thursday: send, in small batches.
4. Friday: log replies, update statuses, queue follow-ups for next week.
5. Sunday: look at the dashboard — reply rate by firm type and template — and shift
   next week's effort toward whatever is working.
