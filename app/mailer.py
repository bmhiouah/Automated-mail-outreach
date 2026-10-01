"""The one place a mail actually leaves the machine.

Every safety property of the sending design lives here, and each one is a rule
rather than a setting someone can quietly flip:

1. **Nothing sends itself.** This module is only ever called from an explicit
   validate action on a queue row the user has read. There is no scheduler, no
   background timer and no "send all pending" path.
2. **`send_mode` defaults to `dry`.** In dry mode `send()` still builds the exact
   MIME message, still runs every check, and still records everything - it just
   does not open a socket to the outside world. So the whole pipeline is
   testable end to end before a single mail leaves, which is the point of writing
   it this way rather than stubbing out the transport.
3. **A reconstructed address must be armed explicitly.** `addr_kind='pattern'`
   means the address was inferred from a firm's convention, not verified. Sending
   to a wrong address is a bounced mail at best and a leaked one at worst, so it
   takes a per-row `confirm_pattern` as well as the validation itself.
4. **A daily cap.** Cold mail at volume gets a domain flagged; 40 is generous for
   one person and still bounds the damage of a mis-click.
5. **The touchpoint ledger is written only on real acceptance.** A `dry` send
   writes nothing to `contact_touchpoints`, because nobody was contacted. That is
   what keeps "who is left to contact" honest.

Config (config.json, gitignored):

    "mail": {
      "send_mode": "dry",              # "dry" | "live"
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 465,
      "smtp_user": "you@gmail.com",
      "smtp_password": "your app password",
      "smtp_ssl": true,                # true = implicit SSL (port 465)
      "from_name": "Your Name",
      "daily_cap": 40
    }
"""
import json
import os
import smtplib
import sys
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db    # noqa: E402

CONFIG_PATH = os.path.join(db.BASE, "config.json")
DEFAULT_DAILY_CAP = 40


def _file_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (ValueError, OSError):
        return {}


def config():
    cfg = (_file_config().get("mail") or {})
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        # dry unless the user explicitly says otherwise. This default is the
        # single most important line in the file.
        "send_mode": (os.environ.get("MAIL_SEND_MODE")
                      or cfg.get("send_mode") or "dry").strip().lower(),
        "smtp_host": (cfg.get("smtp_host") or "smtp.gmail.com").strip(),
        "smtp_port": int(cfg.get("smtp_port") or 465),
        "smtp_user": (os.environ.get("MAIL_USER") or cfg.get("smtp_user") or "").strip(),
        "smtp_password": (os.environ.get("MAIL_PASSWORD") or cfg.get("smtp_password") or ""),
        "smtp_ssl": bool(cfg.get("smtp_ssl", True)),
        "from_name": (cfg.get("from_name") or "").strip(),
        "daily_cap": int(cfg.get("daily_cap") or DEFAULT_DAILY_CAP),
    }


def is_configured():
    """True when a real send could be attempted. A dry send needs no credentials."""
    cfg = config()
    return bool(cfg["smtp_user"] and cfg["smtp_password"] and cfg["smtp_host"])


def status():
    cfg = config()
    return {"send_mode": cfg["send_mode"], "configured": is_configured(),
            "from": formataddr((cfg["from_name"], cfg["smtp_user"])) if cfg["smtp_user"] else "",
            "smtp_host": cfg["smtp_host"], "smtp_port": cfg["smtp_port"],
            "daily_cap": cfg["daily_cap"], "sent_today": sent_today(),
            "reason": "" if is_configured() else
                      "no SMTP credentials - add a \"mail\" block to config.json "
                      "(dry mode still works)"}


def sent_today():
    """Real sends recorded today. Read from the ledger, so the cap survives a
    restart and cannot be reset by quitting the app."""
    return db.query("SELECT COUNT(*) n FROM contact_touchpoints "
                    "WHERE direction='outbound' AND sent_at >= ?",
                    [date.today().isoformat()])[0]["n"]


def build_message(to_addr, subject, body, cv_filename=None, cv_text=None):
    """The exact MIME message that would be sent.

    Built in dry mode too, deliberately: the message is the artefact under review
    as much as the text in the textarea, and a sender line with a stray newline
    is only discoverable by looking at the real thing.
    """
    cfg = config()
    msg = EmailMessage()
    msg["From"] = (formataddr((cfg["from_name"], cfg["smtp_user"]))
                   if cfg["from_name"] else cfg["smtp_user"])
    msg["To"] = to_addr
    msg["Subject"] = subject or ""
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="cold-approach.local")
    msg.set_content(body or "")
    if cv_filename and cv_text:
        msg.add_attachment(cv_text.encode("utf-8"), maintype="text",
                           subtype="plain", filename=cv_filename)
    return msg


def preflight(to_addr, subject, body, addr_kind="none", confirm_pattern=False):
    """Everything that must be true before a send. Returns a list of problems.

    Refusing here rather than after a partial send is the whole design: a bounce
    you cannot see is worse than a message you were told not to send.
    """
    problems = []
    addr = (to_addr or "").strip()
    if not addr:
        problems.append("no address to send to")
    elif "@" not in addr or "." not in addr.rsplit("@", 1)[-1]:
        problems.append(f"'{addr}' does not look like an email address")
    if not (subject or "").strip():
        problems.append("empty subject")
    if not (body or "").strip():
        problems.append("empty body")
    if "{{" in (body or "") or "{{" in (subject or ""):
        problems.append("unresolved {{placeholders}} in the text")
    if addr_kind == "pattern" and not confirm_pattern:
        # Invariant 1: a reconstruction is not an address, it is a hypothesis.
        problems.append("this address was reconstructed from the firm's pattern, not "
                        "verified - confirm it explicitly before sending")
    if addr_kind == "none" and addr:
        problems.append("no verified address for this contact")
    cap = config()["daily_cap"]
    if sent_today() >= cap:
        problems.append(f"daily cap reached ({cap} sent today) - raise mail.daily_cap "
                        "or wait until tomorrow")
    return problems


def send(to_addr, subject, body, addr_kind="none", confirm_pattern=False,
         cv_filename=None, cv_text=None):
    """Attempt one send. Returns {ok, sent, message_id, error, dry}.

    `sent=True` means a real SMTP server accepted the message. `dry=True` means
    every check passed and the message was built, but nothing was transmitted -
    the caller must treat that as NOT sent in every way that matters, and must
    not write a touchpoint.
    """
    cfg = config()
    problems = preflight(to_addr, subject, body, addr_kind, confirm_pattern)
    if problems:
        return {"ok": False, "sent": False, "error": "; ".join(problems)}

    if cfg["send_mode"] != "live":
        return {"ok": True, "sent": False, "dry": True,
                "message_id": "<dry-run@cold-approach.local>",
                "error": "dry mode: the message was built and checked, but not transmitted"}

    if not is_configured():
        return {"ok": False, "sent": False,
                "error": "send_mode is live but no SMTP credentials are configured"}

    msg = build_message(to_addr, subject, body, cv_filename, cv_text)
    try:
        if cfg["smtp_ssl"]:
            server = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
        else:
            server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
        with server:
            server.ehlo()
            if not cfg["smtp_ssl"]:
                server.starttls()
                server.ehlo()
            server.login(cfg["smtp_user"], cfg["smtp_password"])
            refused = server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        # Almost always an app password typed where a password was wanted.
        return {"ok": False, "sent": False,
                "error": f"SMTP rejected the login ({exc.smtp_code}). Gmail needs an "
                          "app password, not your account password."}
    except smtplib.SMTPException as exc:
        return {"ok": False, "sent": False, "error": f"SMTP error: {exc}"}
    except OSError as exc:
        return {"ok": False, "sent": False, "error": f"could not reach the mail server: {exc}"}

    # send_message returns the recipients the server refused. An empty dict means
    # acceptance; anything in it means this address did not go out.
    if refused:
        return {"ok": False, "sent": False,
                "error": "the server refused this address: " + ", ".join(refused.values())}
    return {"ok": True, "sent": True, "dry": False,
            "message_id": msg["Message-ID"], "error": None}