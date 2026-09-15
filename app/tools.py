"""Command-line toolbox: morning briefing, duplicate report, backups, firm intel.

    python3 app/tools.py brief
    python3 app/tools.py dupes
    python3 app/tools.py backup
    python3 app/tools.py test           # the whole suite, on a temp database
    python3 app/tools.py hooks          # the hook line for every firm that has one
    python3 app/tools.py hooks --missing  # tier-1 firms still without a brief
"""
import os
import shutil
import sqlite3
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

BACKUP_DIR = os.path.join(db.BASE, "data", "backups")


def _q(sql, args=None):
    return db.query(sql, args or [])


def brief():
    today = date.today().isoformat()
    print(f"\n  Cold approach briefing — {today}\n")

    due = _q("SELECT o.*, c.first_name, c.last_name, c.company_name FROM outreach o "
             "LEFT JOIN contacts c ON c.id=o.contact_id WHERE o.next_followup_at IS NOT NULL "
             "AND o.next_followup_at <= ? AND o.status NOT IN ('replied','positive','closed') "
             "ORDER BY o.next_followup_at", [today])
    print(f"  Follow-ups due: {len(due)}")
    for d in due:
        print(f"    - {d['first_name']} {d['last_name']} ({d['company_name']}) "
              f"due {d['next_followup_at']}")

    drafts = _q("SELECT COUNT(*) n FROM outreach WHERE status IN ('draft','approved')")[0]["n"]
    contacts = _q("SELECT COUNT(*) n FROM contacts")[0]["n"]
    ready = _q("SELECT COUNT(*) n FROM contacts WHERE status='ready'")[0]["n"]
    no_mail = _q("SELECT COUNT(*) n FROM contacts WHERE email IS NULL OR email=''")[0]["n"]
    print(f"\n  Contacts: {contacts} ({ready} ready to send, {no_mail} still without an address)")
    print(f"  Drafts waiting: {drafts}")

    untouched = _q("SELECT name, tier FROM companies WHERE id NOT IN "
                   "(SELECT company_id FROM contacts WHERE company_id IS NOT NULL) "
                   "AND tier=1 ORDER BY name LIMIT 10")
    print(f"\n  Next tier-1 firms with no contacts yet ({len(untouched)} shown):")
    for c in untouched:
        print(f"    - {c['name']}")

    no_pattern = _q("SELECT COUNT(*) n FROM companies WHERE email_pattern IS NULL "
                    "OR email_pattern=''")[0]["n"]
    firms = _q("SELECT COUNT(*) n FROM companies")[0]["n"]
    print(f"\n  Email coverage: {firms - no_pattern}/{firms} firms have a known pattern")

    briefs = _q("SELECT COUNT(*) n FROM companies WHERE research IS NOT NULL "
                "AND research!=''")[0]["n"]
    careers = _q("SELECT COUNT(*) n FROM companies WHERE careers_url IS NOT NULL "
                 "AND careers_url!=''")[0]["n"]
    t1 = _q("SELECT COUNT(*) n FROM companies WHERE tier=1")[0]["n"]
    t1c = _q("SELECT COUNT(*) n FROM companies WHERE tier=1 AND careers_url IS NOT NULL "
             "AND careers_url!=''")[0]["n"]
    t1b = _q("SELECT COUNT(*) n FROM companies WHERE tier=1 AND research IS NOT NULL "
             "AND research!=''")[0]["n"]
    print(f"  Firm intel:     {briefs} firms have a brief ({t1b}/{t1} of tier 1)")
    print(f"  Careers pages:  {careers}/{firms} firms ({t1c}/{t1} of tier 1)")
    print()


def dupes():
    for label, sql in (("same email", "SELECT lower(email) k, COUNT(*) n, GROUP_CONCAT(id) ids "
                                      "FROM contacts WHERE email IS NOT NULL AND email!='' "
                                      "GROUP BY k HAVING n>1"),
                       ("same name", "SELECT lower(first_name||' '||last_name) k, COUNT(*) n, "
                                     "GROUP_CONCAT(id) ids FROM contacts GROUP BY k HAVING n>1")):
        rows = _q(sql)
        print(f"\n  {label}: {len(rows)}")
        for r in rows:
            print(f"    - {r['k']} (ids {r['ids']})")
    print()


def hooks(missing=False):
    """Firm intel: what you know about each firm, or which ones you still don't."""
    import email_gen
    if missing:
        rows = _q("SELECT name, tier, type FROM companies "
                  "WHERE (research IS NULL OR research='') AND tier=1 ORDER BY name")
        print(f"\n  Tier-1 firms with no brief yet: {len(rows)}")
        for r in rows:
            print(f"    - {r['name']}  ({r['type']})")
        print("\n  Write one into data/firm_briefs.csv as `name,research`, "
              "or click the Brief cell in the Companies tab.\n")
        return
    rows = _q("SELECT name, research FROM companies "
              "WHERE research IS NOT NULL AND research!='' ORDER BY tier, name")
    print(f"\n  Firm intel on file: {len(rows)} firms\n")
    for r in rows:
        print(f"  {r['name']}")
        print(f"    hook: {email_gen._brief_hook(r['research'])}")
    print()


def backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    dest = os.path.join(BACKUP_DIR, f"cold_approach-{stamp}.db")
    src = sqlite3.connect(db.DB_PATH)
    out = sqlite3.connect(dest)
    src.backup(out)
    out.close()
    src.close()
    size = os.path.getsize(dest)
    print(f"  backed up to {dest} ({size//1024} KB)")


def test():
    """Run every test. Lives here so tools.py is the one command you need.

    Tests live in tests/, one module per area, sharing a single temp database
    (see tests/harness.py). Nothing here touches data/cold_approach.db.
    """
    suite = unittest.TestLoader().discover(os.path.join(db.BASE, "tests"),
                                           top_level_dir=db.BASE)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "brief"
    rest = sys.argv[2:]
    table = {"brief": brief, "dupes": dupes, "backup": backup, "test": test,
             "hooks": lambda: hooks(missing="--missing" in rest)}
    fn = table.get(cmd)
    if not fn:
        print("usage: python3 app/tools.py brief|dupes|backup|test|hooks")
        sys.exit(2)
    sys.exit(fn() or 0)
