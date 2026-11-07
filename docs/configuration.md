# Configuration

Everything the app needs lives in one local file. No accounts, no cloud, no data
leaves your machine.

## The two config files

| File | What it is | In git? |
|------|-----------|---------|
| `config/config.example.json` | Template with placeholder values and an explanation of every key | Yes — tracked |
| `config/config.json` | Your real keys, `chmod 600` | No — gitignored |

They are deliberately **not** duplicates: the example is a safe, shareable
starting point; the real file holds credentials and must never be committed.

```bash
cp config/config.example.json config/config.json
chmod 600 config/config.json
# then edit config/config.json with your Hunter.io & Prospeo keys
```

A missing `config.json` is not an error — harvesting simply reports which key
is absent, and the Queue tab falls back to your templates when there is no LLM
key.

## Shape of the file

```json
{
  "hunter_api_key": "...",
  "prospeo_api_key": "...",

  "tavily_api_key": "tvly-...",          // optional: a live-news line in paragraph 1
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
  },
  "cv": {                                 // optional: LaTeX CV settings
    "base_tex": "cv/Badre_Mhiouah_CV.tex",
    "engine": "pdflatex"
  }
}
```

The `//` comments above are for this document only — JSON itself has no
comments. `config/config.example.json` uses `_comment` keys instead, so it is
valid JSON you can copy verbatim.

Notes:

- `mail.send_mode` defaults to `"dry"`: the message is built and every check
  runs, but nothing is transmitted and no touchpoint is written. Keep it until
  you have read a few real drafts.
- `cv.base_tex` overrides the built-in master CV path (default
  `cv/Badre_Mhiouah_CV.tex`). `cv.engine` picks the TeX binary; without a key
  the app tries `pdflatex`, then `tectonic`.
- Every provider call is metered in `fetch_log` and its response is cached in
  `data/raw/<provider>/`. Re-running a harvest costs zero credits.

## Environment variables (keys never touch disk)

Environment variables override the file:

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="gpt-4o-mini"
export TAVILY_API_KEY="tvly-..."
```

The other providers follow the same argument → environment → `config.json`
lookup order: `HUNTER_API_KEY`, `PROSPEO_API_KEY`, `TAVILY_API_KEY`, and for
mail `MAIL_SEND_MODE`, `MAIL_USER`, `MAIL_PASSWORD`. `PORT` overrides the web
server port (default 8765).
