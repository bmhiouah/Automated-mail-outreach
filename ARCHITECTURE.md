# Architecture

How the tracker is put together, and the rules new code is expected to keep.

## The layers

```
browser            web/index.html + web/app.js + web/style.css (vanilla, no build)
   |  JSON over HTTP (45 routes)
app/server.py      the shell: socket, static files, entry point. No SQL, no shapes.
   |  dispatch(method, parts, params, payload) -> (status, JSON-able | Raw)
app/api/           one module per surface + the ROUTES table in __init__.py
   |  db.query / db.execute only
app/taxonomy.py    shared rules (seniority/desk) - the data layer imports this,
                   never the web layer: a function reading a lookup table must
                   not need the HTTP server to do it
app/db.py          the SQLite door: connect, seed/alias/brief/careers loaders,
                   ensure_schema, and the fetch_log credit ledger
```

Everything above `db.py` speaks only in dicts; nothing below it touches the network
except `app/net.py`. The harvesters (`app/harvest.py`, `app/providers/`) write
through `app/people_store.py`, and the UI writes through `app/api/` - they meet at
the same tables, never directly.

## The invariants this refactor exists to protect

1. **Provenance beats convenience.** `position_raw` is set once, verbatim, never
   rewritten. A reconstructed address is `email_status='guessed'`; only a real
   address earns `'verified'`, and a guess never overwrites anything real.
2. **A machine never overwrites a human.** Merge fills empty fields only; the one
   documented exception is a real address replacing a guess.
3. **Never pay twice.** Every provider call goes through the `fetch_log` ledger and
   keeps its raw payload, so a re-run resumes instead of re-spending credits.
4. **Refuse rather than invent.** No known pattern means no address, not a plausible
   one. A firm the resolver cannot prove is left unknown and flagged for review.
5. **Tables are a whitelist.** The table name that reaches SQL comes from a map in
   `api/rows.py` or `app/taxonomy.py`, never from a request path or a pasted cell.
6. **Under-match is a feature.** The paste reader and the resolver propose with a
   confidence and a "why"; anything under 0.6 arrives unticked.

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
