"""Tavily search: the one live-web door.

Why this exists: paragraph 1 of a mail may quote one current fact. The model
cannot know this month's news, and guessing would break the project's
"refuse rather than invent" rule - so the fact is fetched here, once per
query, metered in fetch_log like every other paid source and cached under
data/raw/tavily/. Drafting twenty people at one firm costs one search.

The call shape mirrors the one verified in llm_api_internet.ipynb:
POST https://api.tavily.com/search {"api_key", "query", "max_results"}.

A missing key is not an error at the call site: recent_news() returns ''
and the mail simply goes out with no news sentence. COLD_APPROACH_OFFLINE
(set by the test harness) skips the network entirely.

    from providers import tavily
    tavily.recent_news("AQR Capital Management")
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db      # noqa: E402
import net     # noqa: E402

API = "https://api.tavily.com/search"
PROVIDER = "tavily"
ENDPOINT = "search"
# One search is one credit on Tavily's plans; counted so a budget cannot
# silently run away, same reasoning as the other providers.
CREDIT_COST = 1.0
CONFIG_PATH = os.path.join(db.BASE, "config.json")


def load_key(explicit=None):
    """Argument -> env -> config.json. Never stored in the database."""
    if explicit:
        return explicit.strip()
    env = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if env:
        return env
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                return (json.load(fh).get("tavily_api_key") or "").strip()
        except (ValueError, OSError):
            return ""
    return ""


def _request_key(query):
    """Stable identity of one query, so the same news is paid for once."""
    return "q=" + " ".join(query.lower().split())


def search(query, api_key=None, max_results=3, force=False):
    """One cached, logged search. Returns (body, meta), the provider pair."""
    q = " ".join((query or "").split())
    if not q:
        return None, {"ok": False, "error": "empty query", "cached": False}
    if os.environ.get("COLD_APPROACH_OFFLINE"):
        # The harness sets this: a test must never spend a real credit or
        # hang on a network it does not have.
        return None, {"ok": False,
                      "error": "offline (COLD_APPROACH_OFFLINE set)",
                      "cached": False}
    key = load_key(api_key)
    if not key:
        return None, {"ok": False,
                      "error": "no Tavily key - add tavily_api_key to "
                               "config.json or set TAVILY_API_KEY",
                      "cached": False}
    rkey = _request_key(q)
    if not force:
        seen = db.fetch_seen(PROVIDER, ENDPOINT, rkey)
        if seen and seen["ok"] and seen.get("raw_path"):
            full = os.path.join(db.BASE, seen["raw_path"])
            if os.path.exists(full):
                with open(full, "r", encoding="utf-8") as fh:
                    return json.load(fh), dict(seen, cached=True)

    payload = {"api_key": key, "query": q, "max_results": max_results}
    res = net.request_json(API, method="POST", throttle=True,
                           data=json.dumps(payload).encode("utf-8"),
                           headers={"Content-Type": "application/json"})

    raw_path = sha = None
    if res["body"]:
        raw_path, sha = db.save_raw(PROVIDER, ENDPOINT, rkey, res["body"])
    body = res["json"] or {}
    if not res["ok"] or "results" not in body:
        # Tavily reports failures as {"error": "..."} with a 4xx; surface
        # the message, never the dict.
        err = body.get("error") or res["error"] or "no results field in reply"
        db.record_fetch(PROVIDER, ENDPOINT, rkey, res["status"], credits=0,
                        ok=False, error=str(err)[:300], raw_path=raw_path,
                        response_hash=sha)
        return None, {"ok": False, "status": res["status"], "error": err,
                      "cached": False}
    row = db.record_fetch(PROVIDER, ENDPOINT, rkey, res["status"],
                          credits=CREDIT_COST, ok=True, raw_path=raw_path,
                          response_hash=sha)
    return body, dict(row, cached=False)


def _news_line(results):
    """First result that has a title and a URL, as one fact line.

    Title plus a short excerpt gives the model enough to restate the item
    truthfully; the URL stays in the facts for auditing and is never meant
    for the mail body.
    """
    for r in results or []:
        title = " ".join((r.get("title") or "").split())
        url = (r.get("url") or "").strip()
        if title and url:
            snippet = " ".join((r.get("content") or "").split())[:200]
            return (f"{title} - {snippet} ({url})" if snippet
                    else f"{title} ({url})")
    return ""


def recent_news(company_name, api_key=None):
    """One checkable news line for the facts block, or '' when there is none.

    Best-effort by design: no key, offline, network trouble or no results
    all return '' - the mail then carries no news sentence, which is
    "refuse rather than invent" applied to current events.
    """
    name = " ".join((company_name or "").split())
    if not name:
        return ""
    body, _meta = search(f"{name} latest news", api_key=api_key)
    if not body:
        return ""
    return _news_line(body.get("results") or [])
