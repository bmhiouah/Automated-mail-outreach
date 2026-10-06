"""The LLM door: one chat call, stdlib only, metered like every other source.

Design decisions that follow the project's own rules rather than convenience:

* **It goes through `net.py`.** One retry/backoff/throttle implementation, not a
  second one that forgets the 429-is-not-retryable lesson.
* **It is metered in `fetch_log` like Hunter and Prospeo.** A draft costs real
  tokens, so the same prompt is never paid for twice: the response is cached
  under `data/raw/llm/` and re-used. `force=True` is the only way to pay again.
  That makes "draft the whole tier-1 list" safe to run twice after a crash.
* **It refuses rather than invents.** No key configured is an error, not a
  fallback to a canned string that would look like machine prose.
* **The base CV is never a target.** `generate_cv` reads it; nothing writes it.

Config (config.json, gitignored) - any OpenAI-compatible endpoint works:

    "llm": {
      "api_key": "sk-...",
      "base_url": "https://api.openai.com/v1",   # or http://localhost:11434/v1
      "model": "gpt-4o-mini",
      "temperature": 0.7,
      "max_tokens": 700
    }

Environment variables win over the file, so a key never has to be written to
disk: OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL.
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db    # noqa: E402
import net   # noqa: E402
import taxonomy  # noqa: E402

CONFIG_PATH = os.path.join(db.BASE, "config.json")

# The endpoint name in the ledger. Separate from the model, because the model is
# an attribute of the call and this is the call site.
ENDPOINT = "chat"

SYSTEM_PROMPT = (
    "You write cold outreach email for a junior quant-finance candidate "
    "approaching people at banks, hedge funds, prop trading firms and asset "
    "managers in Paris and London.\n"
    "\n"
    "The mail must read as written for ONE person at ONE firm, never pasted "
    "at anyone. The way to earn that is a genuine link built from BOTH sides "
    "of the facts: something specific about them - their desk, their career, "
    "what the firm is known for or hiring for - joined to something simple "
    "and true about the candidate, taken from their own words in the facts, "
    "not from the CV summary. Both sides must be present: them in paragraph "
    "1, the candidate in paragraph 2.\n"
    "\n"
    "SPECIFIC ABOUT THEM, PLAIN ABOUT YOU. Every sentence about the firm, "
    "the team or the person must carry a concrete detail from the facts: a "
    "name, a team, a career step, a live signal. The candidate's side stays "
    "human and simple - what they genuinely love doing, one project named at "
    "most. No achievement metrics about the candidate: never a percentage, "
    "a P&L number or a run of CV bullets - that reads as hard sell. Their "
    "side could not be sent to anyone else; your side must not sound like "
    "an advertisement.\n"
    "\n"
    "SHAPE. Four short paragraphs, one blank line between them, nothing "
    "added, nothing merged, nothing reordered:\n"
    "\n"
    "  Hi <their first name>,\n"
    "  (1) Why this firm and this person. Name their actual function - the "
    "      desk, the team, the asset class, the mandate - and one specific, "
    "      genuine reason this firm caught your attention: what they are "
    "      known for, what they are building, a live hiring signal.\n"
    "  (2) The link. One or two plain sentences in the candidate's voice: "
    "      what they genuinely love doing - code, data, quantitative "
    "      problems - joined to something specific about this firm, team or "
    "      problem. At most one short project mention, with the GitHub link "
    "      only if it fits naturally. This is the paragraph that makes the "
    "      mail theirs.\n"
    "  (3) The ask, in one breath: whether their team, or another team "
    "      there, is currently looking for someone with this background - "
    "      and if they are building the team out, that you would love to be "
    "      considered. Then 10-15 minutes, happy to work around their "
    "      schedule.\n"
    "  (4) One line that your LinkedIn or CV is included - only if the "
    "      facts give one - then thank them by first name for their time.\n"
    "  Best,\n"
    "\n"
    "The code writes the sign-off after 'Best,'. Stop there.\n"
    "\n"
    "RULES THAT MATTER MORE THAN FLUENCY.\n"
    "- 110 to 140 words, leaning toward the lower end. These are read on a "
    "phone between meetings.\n"
    "- Simple and clear beats clever: short sentences, one idea each, "
    "everyday words. No nested clauses, no thesaurus, no register you would "
    "not use out loud.\n"
    "- Subject: specific to this firm or this person, under 65 characters, "
    "never a generic 'Hello' or 'Introduction'.\n"
    "- Every sentence must survive being pasted to someone else at another "
    "firm. If it would, cut it.\n"
    "- Never invent: no project, employer, date, skill, number, strategy or "
    "compliment that is not in the facts. If a fact is '(unknown)', write "
    "around it and make the mail shorter.\n"
    "- The candidate's ten answers, under \"In the writer's own words\", are "
    "the richest facts there are. Prefer them over the CV summary for the "
    "link in paragraph 2.\n"
    "- No markdown, no asterisks, no bold, no bullet points.\n"
    "- No filler. Banned: 'I hope this email finds you well', 'I am writing "
    "to', 'I wanted to reach out', 'reaching out', 'passionate', "
    "'hard-working', 'team player', 'detail-oriented', 'perfect fit', "
    "'touching base', 'circle back', 'at your earliest convenience', "
    "'to whom it may concern', 'I came across your impressive'.\n"
    "- British spelling, no exclamation marks, no emoji.\n"
    "\n"
    "Reply with JSON only, no prose and no code fence: "
    '{"subject": "...", "body": "...", '
    '"specifics": ["which fact justified paragraph 1", '
    '"which of the candidate\'s own words justified the link in paragraph 2"]}'
)


def _file_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (ValueError, OSError):
        return {}


def config():
    """Resolved LLM settings. Env wins over the file, so no key need be written."""
    cfg = (_file_config().get("llm") or {})
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "api_key": (os.environ.get("OPENAI_API_KEY") or cfg.get("api_key") or "").strip(),
        "base_url": (os.environ.get("OPENAI_BASE_URL") or cfg.get("base_url")
                     or "https://api.openai.com/v1").strip().rstrip("/"),
        "model": (os.environ.get("OPENAI_MODEL") or cfg.get("model")
                  or "gpt-4o-mini").strip(),
        "temperature": float(cfg.get("temperature", 0.7)),
        # Generous by default, and deliberately so. Reasoning models such as
        # gpt-oss spend the budget on thinking before they emit a single character
        # of the answer: at 700 tokens a reply came back with `content: None`,
        # `finish_reason: "length"` and 700 tokens of private reasoning. The
        # visible answer was never cut short - the thinking was.
        "max_tokens": int(cfg.get("max_tokens", 2400)),
    }


def is_configured():
    """True only when a call could actually be made. Checked before every batch."""
    cfg = config()
    return bool(cfg["api_key"] and cfg["base_url"] and cfg["model"])


def available_models():
    """What this endpoint will actually accept, for the UI's model picker.

    Worth having because a provider gateway lists models it does not actually
    serve. On this Bedrock endpoint, `/models` returns `openai.gpt-5.6-terra` and
    every Claude model; the OpenAI ones answer `access_denied` and the Anthropic
    ones reject `/v1/chat/completions` outright. Reading the list is the only way
    to know that before writing a mail and being told afterwards.
    """
    cfg = config()
    if not cfg["api_key"]:
        return []
    res = net.get_json(f"{cfg['base_url']}/models",
                       headers={"Authorization": f"Bearer {cfg['api_key']}"},
                       retries=1, timeout=20)
    if not res["ok"] or not isinstance(res.get("json"), dict):
        return []
    return sorted({m["id"] for m in res["json"].get("data", [])
                   if isinstance(m, dict) and m.get("id")
                   and m.get("status", "available") == "available"})


def check_model(model=None):
    """One real call, to prove the endpoint answers. Costs a token or two.

    Separate from `status()`, which never spends anything: "is it configured" and
    "does it work" are different questions and only the second one costs.

    The budget is 900 rather than something tiny because reasoning models spend
    their tokens thinking first. At 64 the probe always failed with an unclosed
    JSON object - which says nothing about whether the endpoint works, only that
    the model was still deliberating.
    """
    r = chat("Reply with JSON only: {\"subject\": \"ok\", \"body\": \"ok\"}",
             max_tokens=900, temperature=0.0)
    return {k: v for k, v in r.items() if k not in ("text", "prompt")}


def status():
    """What the UI shows in the Queue tab's status line."""
    cfg = config()
    if not cfg["api_key"]:
        return {"configured": False,
                "reason": "no API key - set OPENAI_API_KEY or add an \"llm\" block to config.json",
                "model": cfg["model"], "base_url": cfg["base_url"]}
    # `fetch_log.credits` is where the token count is recorded (the ledger has one
    # numeric cost column, shared with the paid providers). There is no
    # `prompt_tokens` column, and asking for one took the Queue status endpoint
    # down with a 500 rather than returning anything useful.
    spent = db.query("SELECT COUNT(*) n, COALESCE(SUM(tokens),0) tok "
                 "FROM fetch_log WHERE provider='llm'")[0]
    # Surface a recent failure. Without this the Queue simply stops working when
    # the key lapses, and a dead button reads as a bug in the app rather than a
    # credential that expired.
    last_bad = db.query("SELECT error, fetched_at FROM fetch_log WHERE provider='llm' "
                        "AND ok=0 ORDER BY fetched_at DESC LIMIT 1")
    last_ok = db.query("SELECT fetched_at FROM fetch_log WHERE provider='llm' "
                       "AND ok=1 ORDER BY fetched_at DESC LIMIT 1")
    bad_at = last_bad[0]["fetched_at"] if last_bad else None
    ok_at = last_ok[0]["fetched_at"] if last_ok else None
    failing = bool(bad_at and (not ok_at or str(bad_at) > str(ok_at)))
    return {"configured": True, "model": cfg["model"], "base_url": cfg["base_url"],
            "calls": spent["n"], "prompt_tokens": int(spent["tok"] or 0),
            "max_tokens": cfg["max_tokens"],
            "failing": failing,
            "last_error": _explain_http(last_bad[0]["error"], cfg["model"]) if failing else "",
            "cached_hits": db.query("SELECT COUNT(*) n FROM fetch_log WHERE provider='llm' "
                                    "AND tokens=0 AND ok=1")[0]["n"]}


def _request_key(model, system, prompt, temperature):
    """Stable identity of one call, so an identical re-run is free."""
    raw = json.dumps([model, system, prompt, round(temperature, 3)],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _extract_json(text):
    """Pull the JSON object out of a reply.

    A model asked for JSON usually complies, but "usually" is why this exists: a
    fenced block, a stray sentence before it, or trailing prose must not lose an
    otherwise good draft. Raises ValueError when there is no object at all - the
    caller records the failure rather than queueing an empty mail.
    """
    if not text:
        raise ValueError("the model returned nothing")
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the reply")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = json.loads(text[start:i + 1])
                if not isinstance(obj, dict):
                    raise ValueError("the JSON was not an object")
                return obj
    raise ValueError("the JSON object was never closed")


def _read_cached(row):
    """Replay a stored reply. Returns None when the file is gone, so the caller
    pays again rather than queueing a mail built from nothing."""
    path = row.get("raw_path")
    if not path:
        return None
    full = os.path.join(db.BASE, path)
    if not os.path.exists(full):
        return None
    try:
        with open(full, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        parsed = _extract_json(text)
        return {"subject": parsed["subject"].strip(), "body": parsed["body"].strip(),
                "specifics": [str(s) for s in (parsed.get("specifics") or [])]}
    except (ValueError, OSError, KeyError, IndexError, TypeError):
        return None


def _explain_http(error, model):
    """Turn a raw HTTP failure into something the user can act on.

    A bare "HTTP 400" in the Queue tells you nothing about which of the four
    likely causes it was, and on this endpoint they are all common: the model is
    not entitled, the model does not speak /chat/completions, the key is wrong,
    or the budget is exhausted.

    The expired-signature case gets its own branch because it is this project's
    real failure mode: an AWS Bedrock key is a SigV4 signature with a clock on
    it, and when it lapses every model fails at once with a message that looks
    like an entitlement problem. Without this the Queue just stops working and
    reads as "the button is broken".
    """
    text = (error or "").strip()
    low = text.lower()
    if "signature expired" in low:
        import re as _re
        # Search the lowercased text for digits only. Matching `\d+T\d+Z` there
        # can never fire, because `low` has already turned the T and Z into
        # lowercase - which is exactly what made this print "expired on some time
        # ago" while the date was sitting in the message.
        when = _re.search(r"signature expired:\s*(\d{4}-\d{2}-\d{2}|\d{8})", low)
        stamp = when.group(1) if when else "an earlier date"
        return (f"Your Bedrock key expired on {stamp}. It is a time-limited AWS "
                f"signature, not a permanent key, so every model fails at once. "
                f"Get a fresh one and put it in config.json (llm.api_key) - no "
                f"code change is needed.")
    if "invalid_api_key" in low or "api key" in low and "401" in low:
        return (f"{text} - the key was rejected outright. Check llm.api_key in "
                f"config.json.")
    if "404" in low:
        return (f"{text} - the endpoint has no /chat/completions at that base_url, "
                f"or the model '{model}' does not exist there. "
                f"python3 app/llm.py --models lists what this endpoint serves.")
    if "401" in low or "403" in low:
        return (f"{text} - the key was refused, or this account is not entitled to "
                f"'{model}'. Try another model, or refresh the key.")
    if "400" in low:
        if "does not support" in low:
            return (f"{text} - '{model}' does not speak /chat/completions on this "
                    f"endpoint. Pick another from python3 app/llm.py --models.")
        return f"{text} - the provider rejected the request; check the model name."
    if "429" in low:
        return f"{text} - rate limited or out of quota. Wait, then re-run."
    return text or "the request failed"


def chat(prompt, system=None, temperature=None, max_tokens=None, force=False,
         model=None, require_subject=True):
    """One chat call. Returns {ok, text, subject, body, model, tokens, cached}.

    `model` overrides the configured one for this call only. It is part of the
    cache key, so asking the same person twice with two different models costs
    two calls and neither is silently reused for the other - which is the whole
    point of being able to compare them.

    `require_subject=False` is for callers that are not writing a mail: a
    tailored CV has no subject line, and insisting on one turned every
    successful LaTeX reply into a parse failure.

    Never raises for an expected failure - a refused key or a rate limit is a
    result the queue can display, not a 500 that loses the whole batch.
    """
    cfg = config()
    if not cfg["api_key"]:
        return {"ok": False, "error": "no LLM API key configured - see config.example.json"}
    system = SYSTEM_PROMPT if system is None else system
    temperature = cfg["temperature"] if temperature is None else temperature
    max_tokens = max_tokens or cfg["max_tokens"]
    model = (model or cfg["model"]).strip()
    key = _request_key(model, system, prompt, temperature)

    # Invariant 3: never pay twice. A cached reply is replayed, not repurchased.
    seen = db.fetch_seen("llm", ENDPOINT, key)
    if seen and seen.get("ok") and not force:
        cached = _read_cached(seen)
        if cached:
            out = dict(cached)
            out.update(ok=True, cached=True, model=model, tokens=0,
                       error=None, _key=key)
            return out

    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    res = net.post_json(f"{cfg['base_url']}/chat/completions", body,
                        headers={"Authorization": f"Bearer {cfg['api_key']}"},
                        retries=2, timeout=90)
    tokens = ((res.get("json") or {}).get("usage") or {}).get("prompt_tokens") or 0

    if not res["ok"]:
        db.record_fetch("llm", ENDPOINT, key, http_status=res.get("status"),
                        tokens=tokens, ok=False,
                        error=res.get("error") or "request failed")
        return {"ok": False, "error": _explain_http(res.get("error"), model),
                "model": model, "tokens": tokens}

    text = ((res.get("json") or {}).get("choices") or [{}])[0].get("message", {}) or {}
    content = text.get("content")
    finish = ((res.get("json") or {}).get("choices") or [{}])[0].get("finish_reason")
    if not content:
        # Almost always a reasoning model that spent the whole budget thinking.
        # Saying "raise max_tokens" is the fix; saying "no content" is not, and
        # the two look identical from the outside unless we check the reason.
        detail = ""
        if finish == "length":
            detail = (f" The model used all {max_tokens} tokens reasoning and never "
                      f"started its answer - raise llm.max_tokens, or use a model "
                      f"that does not think before replying.")
        elif text.get("reasoning"):
            detail = " The model returned only internal reasoning."
        db.record_fetch("llm", ENDPOINT, key, http_status=res.get("status"),
                        tokens=tokens, ok=False,
                        error=f"empty reply (finish_reason={finish})")
        return {"ok": False, "model": model, "tokens": tokens,
                "error": f"the model returned no answer.{detail}",
                "finish_reason": finish}
    try:
        parsed = _extract_json(content)
        subject = (parsed.get("subject") or "").strip()
        bodytext = (parsed.get("body") or "").strip()
        if not bodytext or (require_subject and not subject):
            raise ValueError("the reply had no subject or no body"
                             if require_subject else "the reply had no body")
        # The facts the model says it used. Not decoration: this is what makes a
        # vague draft auditable instead of merely plausible, and it is shown next
        # to the mail so a claim can be checked against the database.
        specifics = [str(s) for s in (parsed.get("specifics") or []) if str(s).strip()]
    except ValueError as exc:
        # Kept anyway: the raw text is in the ledger, so a prompt tweak can be
        # diagnosed from the stored reply instead of guessed at.
        raw_path, sha = db.save_raw("llm", ENDPOINT, key, res["body"])
        db.record_fetch("llm", ENDPOINT, key, http_status=res.get("status"),
                        tokens=tokens, ok=False, error=str(exc), raw_path=raw_path,
                        response_hash=sha)
        return {"ok": False, "error": f"{exc} (raw reply kept at {raw_path})",
                "model": model, "tokens": tokens, "text": content}

    raw_path, sha = db.save_raw("llm", ENDPOINT, key, res["body"])
    db.record_fetch("llm", ENDPOINT, key, http_status=res.get("status"),
                    tokens=tokens, ok=True, raw_path=raw_path, response_hash=sha)
    return {"ok": True, "subject": subject, "body": bodytext, "model": model,
            "tokens": tokens, "cached": False, "text": content, "specifics": specifics,
            "_key": key}


# ----------------------------------------------------------------- prompts
def _line(label, value, limit=600):
    """One labelled fact. An unknown value is written as '(unknown)' rather than
    omitted, so the model can see that it is missing instead of assuming the
    field was irrelevant - which is what invites it to fill the gap itself."""
    v = " ".join(str(value or "").split())
    return f"{label}: {v[:limit] if v else '(unknown)'}"


def _career_path(contact):
    """The person's own track record, as sentences.

    This is the single most useful thing in the database for a cold mail and it
    was being thrown away: 108 of your contacts have up to five past roles each.
    "You went from energy trading to systematic equities" is a real reason to
    write to one person and not to their colleague, and no amount of firm-level
    intel substitutes for it.
    """
    cid = contact.get("id")
    if not cid:
        return ""
    rows = db.query("SELECT title, company_name, start_year, end_year, is_current "
                    "FROM person_job_history WHERE contact_id=? "
                    "ORDER BY is_current DESC, start_year DESC LIMIT 5", [cid])
    if not rows:
        return ""
    parts = []
    for r in rows:
        years = ""
        start, end = r.get("start_year"), r.get("end_year")
        if start and end:
            years = f" ({start}-{end})"
        elif start and r.get("is_current"):
            years = f" (since {start})"
        elif start:
            years = f" (from {start})"
        firm = r.get("company_name") or "an unnamed firm"
        parts.append(f"{r.get('title') or 'a role'} at {firm}{years}")
    return "; ".join(parts)


def _firm_activity(company):
    """What the firm is doing right now, assembled from the harvested columns.

    `job_postings_count` is the one that matters: a firm with 1,400 live postings
    is actively hiring, and stating that plainly beats any hint the model has to
    infer. It is also only ever true, never inferred.

    `technologies` is deliberately NOT used. It was scraped from the firm's careers
    site, so what it actually contains for a bank is "Akamai Bot Manager, Algolia,
    Google Ads" - their CDN and ad vendors, not their trading stack. Handing that to
    a model invites it to tell a Goldman PM that they run Redux, which is both
    absurd and instantly discreditsing. A wrong specific beats no specific here.
    """
    bits = []
    n = company.get("job_postings_count")
    if n:
        # Stated as a firm-wide number on purpose. "1414 live job postings" read
        # out loud to a Goldman PM is a flex that lands badly: it counts the whole
        # bank, not their desk, and it implies you scrolled their careers page.
        # The fact that a firm is hiring is worth having; the exact count usually
        # is not.
        bits.append(f"actively hiring - {n} open roles across the firm as a whole "
                    f"(firm-wide count, NOT their desk)")
    if company.get("headcount"):
        bits.append(f"headcount {company['headcount']}")
    if company.get("founded_year"):
        bits.append(f"founded {company['founded_year']}")
    if company.get("revenue_printed"):
        bits.append(f"reported revenue {company['revenue_printed']}")
    return "; ".join(bits)


def _own_words(profile):
    """The writer's own answers to the ten profile questions.

    These are the richest facts available: written by the candidate,
    for exactly this purpose, so they carry the split behind a number
    and the reason behind a choice that a CV summary leaves out. Each
    answer is shown with its question, because an answer without its
    question is a fact the model cannot weigh.
    """
    try:
        answers = json.loads(profile.get("answers") or "[]")
    except (ValueError, TypeError):
        answers = []
    if not isinstance(answers, list):
        return ""
    lines = ["In the writer's own words (the ten questions they "
             "answered for exactly this - treat as the richest "
             "facts here):"]
    any_answer = False
    for i, a in enumerate(answers):
        a = " ".join(str(a or "").split())
        if not a:
            continue
        any_answer = True
        q = (taxonomy.PROFILE_QUESTIONS[i]
             if i < len(taxonomy.PROFILE_QUESTIONS)
             else f"Question {i + 1}")
        lines.append(f"{i + 1}. {q} -> {a[:400]}")
    return "\n".join(lines) if any_answer else ""


def _facts_block(contact, company, profile):
    """Everything the model may use, as a labelled, explicitly-closed block.

    The closing instruction matters as much as the facts: the cheapest way to get
    a fabricated compliment out of a model is to leave it a gap and hope. Saying
    "do not invent, write around it" turns a thin brief into a shorter honest
    mail instead of an invented one.
    """
    who = f"{contact.get('first_name') or ''} {contact.get('last_name') or ''}".strip()
    phone = profile.get("phone") or ""
    career = _career_path(contact)
    return "\n".join([
        "FACTS. Use only what is written here. Do not invent a fact, a project, a "
        "date, an employer or a compliment that is not below. If something you "
        "need is missing, write around it and keep the mail short and true.",
        "",
        "== WHO YOU ARE WRITING TO ==",
        _line("First name", contact.get("first_name")),
        _line("Last name", contact.get("last_name")),
        _line("Full name", who),
        _line("Current job title (verbatim)", contact.get("position_raw")
              or contact.get("job_title")),
        _line("Their own headline", contact.get("headline")),
        _line("Team or department", contact.get("department")),
        _line("Desk, as this app classifies it", contact.get("desk")),
        _line("Seniority", contact.get("seniority")),
        _line("City", contact.get("city")),
        _line("Their career so far (use this to explain why THEY)", career, 500),
        "",
        "== THE FIRM THEY ARE AT ==",
        _line("Name", company.get("name") or contact.get("company_name")),
        _line("Type", company.get("type")),
        _line("Sub-type", company.get("subtype")),
        _line("Headquarters", company.get("hq_city")),
        _line("What they say they do", company.get("description"), 500),
        _line("What they are known for (this app's research note)",
              company.get("research"), 900),
        _line("Live hiring signal, right now", _firm_activity(company), 300),
        _line("Careers page", company.get("careers_url"), 200),
        "",
        "== YOU, THE PERSON WRITING ==",
        _line("Full name", profile.get("full_name")),
        _line("Headline", profile.get("headline")),
        _line("Target roles", profile.get("target_roles")),
        _line("Years of experience", profile.get("years_exp")),
        _line("City", profile.get("city")),
        _line("Key skills", profile.get("key_skills")),
        _line("Projects", profile.get("projects"), 800),
        _line("Achievements", profile.get("achievements"), 600),
        _line("Education", profile.get("education"), 400),
        _line("Languages", profile.get("languages")),
        _line("Availability", profile.get("availability")),
        _line("One-line pitch", profile.get("pitch"), 400),
        _line("GitHub (a project link may be used once, if natural)",
              profile.get("github")),
        _own_words(profile),
        _line("Contact details for the signature", phone and
              f"{phone} | {profile.get('linkedin') or ''}"),
    ])


def signature(profile):
    """The canonical sign-off: name, LinkedIn, phone.

    Built here rather than asked of the model, because a model will invent a
    phone number or a profile URL and a wrong one in an outbound mail is a real
    problem for a real person. The ordering matches the user's own template:
    name, then LinkedIn, then phone.
    """
    lines = [(profile.get("full_name") or "").strip()]
    linkedin = (profile.get("linkedin") or "").strip()
    phone = (profile.get("phone") or "").strip()
    if linkedin:
        lines.append(linkedin)
    if phone:
        lines.append(phone)
    return "\n".join(l for l in lines if l)


def generate_mail(contact, company, profile, cv_text=None, note="", force=False,
                  model=None):
    """Draft one mail. Returns the `chat` shape plus `prompt`.

    The task restates the structure immediately before the facts, because a model
    follows an instruction far more reliably when it is repeated next to the data
    it applies to. It is the same four-part structure as the system prompt; saying
    it twice is deliberate.
    """
    extra = [f"The candidate's own note for this contact: {note}"] if note else []
    prompt = "\n".join([
        "Write one cold email to the person below. It must read as written for "
        "this person at this firm, not pasted at anyone.",
        "",
        "The link is the job, and it is built from both sides of the FACTS "
        "below: something specific about them - their role, their career, what "
        "the firm is known for or hiring for - joined to something simple and "
        "true about the candidate, taken from their own words (the ten "
        "answers), not the CV summary. Specific about them, plain about you: "
        "a vague mail means a fact about them was ignored, and a mail that "
        "lists your metrics reads as hard sell.",
        "",
        "Follow this exact shape - one blank line between paragraphs, nothing "
        "added, nothing merged:",
        "",
        "Hi <their first name>,",
        "",
        "Paragraph 1: why this firm and this person. Name their actual function - "
        "the desk, the team, the asset class, the mandate. Then one specific, "
        "genuine reason this firm caught your attention: what they are known for, "
        "what they are building, a live hiring signal.",
        "",
        "Paragraph 2: the link. One or two plain sentences in the candidate's "
        "voice - what they genuinely love doing, taken from their own words (the "
        "ten answers): code, data, quantitative problems - joined to something "
        "specific about that firm, team or problem. At most one short project "
        "mention, with the GitHub link only if it fits naturally. No achievement "
        "metrics: never a percentage, a P&L number or a run of CV bullets - that "
        "reads as hard sell. This paragraph is what makes the mail theirs.",
        "",
        "Paragraph 3: the ask, in one breath - whether their team, or another "
        "team there, is currently looking for someone with this background, and "
        "if they are building the team out, that you would love to be "
        "considered; then 10-15 minutes, happy to work around their schedule.",
        "",
        "Paragraph 4: one line that your LinkedIn or CV is included - only if the "
        "facts give one - then thank them by first name for their time.",
        "",
        "Then: Best, and stop. The code writes the sign-off.",
        "",
        "Rules: simple, clear language - short sentences, one idea each, everyday "
        "words, nothing you would not say out loud. Every sentence about the "
        "firm, the team or the person must carry a concrete detail from the "
        "FACTS: a name, a team, a career step, a signal. The candidate's side "
        "stays plain - one human sentence about what they love doing, at most "
        "one project named; never percentages, P&L numbers or CV bullets. "
        "No markdown, no asterisks, no bold, no bullet points. Nothing "
        "that could be pasted to someone else at another firm. The candidate's "
        "ten answers are the richest facts - prefer them for the link. If a fact "
        "is '(unknown)', write around it and make the mail shorter rather than "
        "inventing something. 110 to 140 words, leaning toward the lower end.",
        "",
        _facts_block(contact, company or {}, profile),
        "",
        "CV - you may take facts from it, but do not summarise it in the mail:",
        "---",
        (cv_text or profile.get("cv_text") or "(no CV text on file)")[:4000],
        "---",
        "",
    ] + extra + [
        "",
        'Reply with JSON only: {"subject": "...", "body": "...", '
        '"specifics": ["which fact justified paragraph 1", '
        '"which of the candidate\'s own words justified the link in paragraph 2"]}',
    ])
    out = chat(prompt, force=force, model=model)
    out["prompt"] = prompt
    sig = signature(profile)
    # Only append what is missing. Models happily write their own sign-off, and
    # checking only for the name meant a mail that signed the name twice; checking
    # only for the phone meant a mail with the name twice and no number.
    tail = (out.get("body") or "")[-300:].lower() if out.get("ok") else ""
    if out.get("ok") and sig:
        missing = [ln for ln in sig.split("\n") if ln.lower() not in tail]
        if missing:
            out["body"] = out["body"].rstrip() + "\n\n" + "\n".join(missing)
    return out


CV_SYSTEM_PROMPT = (
    "You tailor CVs for junior quant-finance roles in Paris and London. You are "
    "editing an existing document, never writing a new one: every true fact in "
    "the base must survive. You may reorder and re-weight, but never delete a "
    "qualification and never invent a skill, a date, an employer or a number. "
    "Output plain text: no markdown headings, no tables, no rules. "
    'Reply with JSON only: {"body": "the tailored CV as plain text"}'
)


def generate_cv(contact, company, profile, base_cv, role_target="", force=False,
                model=None):
    """Tailor the CV to one opportunity. Proposes; never overwrites `base_cv`.

    The base document is passed whole and returned untouched, and the system
    prompt forbids dropping a qualification. A tailoring that quietly removes a
    Master's because a prop desk "does not care" is the exact silent loss this
    design refuses - hence `status='pending'` and a human decision afterwards.
    """
    firm = (company or {}).get("name") or (contact or {}).get("company_name") or ""
    who = f"{(contact or {}).get('first_name') or ''} " \
          f"{(contact or {}).get('last_name') or ''}".strip()
    role = role_target or (contact or {}).get("job_title") or ""
    prompt = "\n".join([
        "Tailor this CV for one specific opportunity.",
        "",
        _line("Firm", firm),
        _line("Target role", role),
        _line("Person it is written for", who),
        _line("Firm brief (intel)", (company or {}).get("research"), 900),
        _line("Candidate headline", profile.get("headline")),
        "",
        "BASE CV - all of it, verbatim. This is the master document and your "
        "answer never replaces it:",
        "---",
        (base_cv or "(no CV on file)")[:12000],
        "---",
        "",
        "Return the tailored CV as plain text in the JSON body field.",
    ])
    return chat(prompt, system=CV_SYSTEM_PROMPT, max_tokens=2000, force=force,
                model=model)


CV_LATEX_SYSTEM_PROMPT = (
    "You edit a LaTeX CV for junior quant-finance roles in Paris and London. "
    "You are editing an existing document, never writing a new one: every true "
    "fact in the base must survive. You may reorder, re-weight and rephrase to "
    "fit the opportunity, but never delete a qualification and never invent a "
    "skill, a date, an employer, a metric or a tool. "
    "The output must be valid LaTeX that compiles with pdflatex: keep the "
    "preamble intact if you return one, use only packages the base already "
    "loads, and balance every brace and environment. "
    "Do not add markdown, code fences, commentary or an explanation - the "
    "document is the entire answer. "
    'Reply with JSON only: {"body": "the complete LaTeX document"}'
)


def generate_cv_latex(contact, company, profile, base_tex, instruction,
                      role_target="", force=False, model=None):
    """Tailor `main.tex` to one opportunity. Returns the model's reply.

    The instruction is what the user actually typed in the CVs tab, so it is
    given the top of the prompt: a standing spec ("drop the teaching section,
    lead with the XVA work") should beat any generic default, not compete with
    it. The base document is passed whole and must come back untouched in
    content - only its emphasis changes.
    """
    firm = (company or {}).get("name") or (contact or {}).get("company_name") or ""
    who = f"{(contact or {}).get('first_name') or ''} " \
          f"{(contact or {}).get('last_name') or ''}".strip()
    role = role_target or (contact or {}).get("job_title") or ""
    if not (instruction or "").strip():
        instruction = ("Reorder and re-weight the CV for this opportunity, "
                       "keeping every qualification and every fact.")
    prompt = "\n".join([
        "Produce the tailored CV described below.",
        "",
        "INSTRUCTION FROM THE USER (this is the specification):",
        (instruction or "").strip(),
        "",
        _line("Firm", firm),
        _line("Target role", role),
        _line("Person the CV is written for", who),
        _line("Firm brief (intel)", (company or {}).get("research"), 900),
        _line("Candidate headline", profile.get("headline")),
        _line("Target roles the user is pursuing", profile.get("target_roles"), 400),
        "",
        "BASE LaTeX DOCUMENT - all of it, verbatim. This is the master file:",
        "---",
        (base_tex or "")[:60000] or "(no main.tex on file)",
        "---",
        "",
        "Return the whole document as LaTeX in the JSON body field.",
    ])
    # A CV is ~11k characters of LaTeX and the reply must be at least as long.
    # The mail default (2400) was sized for a 150-word mail and would have cut
    # the document in half mid-environment.
    return chat(prompt, system=CV_LATEX_SYSTEM_PROMPT, max_tokens=16000,
                force=force, model=model, require_subject=False)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="check the LLM configuration")
    ap.add_argument("--probe", action="store_true",
                    help="spend one real call to confirm the endpoint works")
    ap.add_argument("--models", action="store_true",
                    help="list the models this endpoint will actually accept")
    ap.add_argument("--show-prompt", metavar="CONTACT_ID", type=int,
                    help="print the exact prompt that would be sent for one contact")
    a = ap.parse_args()
    if a.models:
        for m in available_models():
            print("   ", m)
        raise SystemExit(0)
    print(json.dumps(status(), indent=2))
    if a.show_prompt:
        from api import queue as api_queue
        from domain import get_profile
        contact, company = api_queue._load_contact(a.show_prompt)
        if not contact:
            raise SystemExit(f"no contact {a.show_prompt}")
        out = generate_mail(contact, company, get_profile())
        print("\n" + (out.get("prompt") or "(not generated - draft the mail first)"))
        raise SystemExit(0)
    if a.probe:
        print(json.dumps(check_model(), indent=2))