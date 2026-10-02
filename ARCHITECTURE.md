# Architecture

How the tracker is put together, and the rules new code is expected to keep.

## The layers

```
browser            web/index.html + web/app.js + web/style.css (vanilla, no build)
   |  JSON over HTTP (59 routes)
app/server.py      the shell: socket, static files, entry point. No SQL, no shapes.
   |  dispatch(method, parts, params, payload) -> (status, JSON-able | Raw)
app/api/           one module per surface + the ROUTES table in __init__.py
   |  db.query / db.execute only
app/taxonomy.py    shared rules (seniority/desk) - the data layer imports this,
                   never the web layer: a function reading a lookup table must
                   not need the HTTP server to do it
app/db.py          the SQLite door: connect, seed/alias/brief/careers loaders,
                   ensure_schema, and the fetch_log credit ledger
app/net.py         the one HTTP door: retry, backoff, per-host throttle
app/llm.py         the one LLM door: chat calls, metered in fetch_log like any
                   other paid source, so a re-run re-uses what you already paid for
app/mailer.py      the one SMTP door. send_mode defaults to 'dry'
```

Everything above `db.py` speaks only in dicts; nothing below it touches the network
except `app/net.py`. The harvesters (`app/harvest.py`, `app/providers/`) write
through `app/people_store.py`, and the UI writes through `app/api/` - they meet at
the same tables, never directly.

## The send path

The queue is the only route from a draft to a person, and it is deliberately a
narrow one:

```
llm.py (or a template)  ->  mail_queue (pending)
                              |  you may edit it freely
                              +-- cancel --> the row is DELETED
                              +-- skip   --> untouched, comes back later
                              +-- send   --> outreach row --> mailer.send()
                                                    |  accepted by SMTP?
                                                    +-- yes --> contact_touchpoints
                                                    +-- no  --> nothing recorded
```

Three properties this shape is responsible for:

* **Nothing sends itself.** `mailer.send` is called from exactly one function,
  `queue.api_validate`, which only a per-row Send button reaches. There is no
  batch-send route, no timer and no background job, and `tests/test_queue.py`
  asserts the single-caller property so a future "send all" cannot be added quietly.
  A follow-up is queued like any other draft rather than being a one-click send.
* **The review walks itself.** The UI holds a cursor on one draft at a time;
  Send, Cancel and Skip all reload the next pending row. `Skip` deliberately
  changes nothing on disk — a skip that cancelled would quietly drop people out
  of the queue — so the list underneath stays a way back to what you deferred.
  `Cancel` deletes the row outright: an identical prompt is cached in `fetch_log`,
  so a discarded draft can be regenerated for free and keeping it buys nothing.
  It still refuses to touch a `sent` row, which is the only record that a given
  text actually reached someone.
* **A dry run is not a send.** `send_mode` defaults to `dry`: the message is built
  and every check runs, but nothing is transmitted and **no touchpoint is written**.
  Were a dry run to append to the ledger, "who is left to contact" would start
  lying about real people, which is the one failure this whole design must not have.
* **The ledger cannot be edited away.** `contact_touchpoints` is append-only and is
  written only on real SMTP acceptance. `outreach` is the workflow row you edit;
  the touchpoint is the fact. Clearing your notes on a thread cannot make someone
  reappear as un-contacted, and a test covers exactly that.

## The invariants this refactor exists to protect

1. **Provenance beats convenience.** `position_raw` is set once, verbatim, never
   rewritten. A reconstructed address is `email_status='guessed'`; only a real
   address earns `'verified'`, and a guess never overwrites anything real.
2. **A machine never overwrites a human.** Merge fills empty fields only; the one
   documented exception is a real address replacing a guess.
3. **Never pay twice.** Every provider call goes through the `fetch_log` ledger and
   keeps its raw payload, so a re-run resumes instead of re-spending credits. The
   LLM is metered the same way, keyed on a hash of the prompt: drafting the same
   person twice costs one call, not two.
4. **Refuse rather than invent.** No known pattern means no address, not a plausible
   one. A firm the resolver cannot prove is left unknown and flagged for review. No
   LLM key means drafts come from your templates, not from a canned string dressed
   up as machine prose.
5. **Tables are a whitelist.** The table name that reaches SQL comes from a map in
   `api/rows.py` or `app/taxonomy.py`, never from a request path or a pasted cell.
6. **Under-match is a feature.** The paste reader and the resolver propose with a
   confidence and a "why"; anything under 0.6 arrives unticked.
7. **Nothing is sent without a decision, and nothing generated is trusted
   un-reviewed.** A mail leaves only from `queue.api_validate`, one row at a time. An
   LLM draft and an LLM CV variant both arrive `pending` and are re-graded or
   re-read by a human before they can go anywhere.

## Adding an endpoint

Write the function in the right `app/api/` module, add one line to that module's
`ROUTES`, and `tests/test_api.py` will walk it automatically. Raw files go through
`domain.Raw`; anything else must be JSON-able.

## Tests

`python3 app/tools.py test` runs the Python suite against one throwaway database
(`tests/harness.py` builds it exactly the way `server.main()` builds the real one).
`node tests/check_ui.mjs` does the same job for the front end: every inline
handler and looked-up id must resolve, or the split-era UI fails the same way an
endpoint test fails. New logic gets a test that would fail if the logic were
deleted - this project improves by accumulating sentences like that one.
