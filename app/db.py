"""Thin SQLite layer for the cold approach tracker."""
import csv
import glob
import os
import re
import sqlite3
import unicodedata

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "cold_approach.db")
SCHEMA_PATH = os.path.join(BASE, "db", "schema.sql")
SEED_PATH = os.path.join(BASE, "data", "companies_seed.csv")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def query(sql, args=None):
    conn = connect()
    try:
        cur = conn.execute(sql, args or [])
        return rows_to_dicts(cur.fetchall())
    finally:
        conn.close()


def execute(sql, args=None):
    conn = connect()
    try:
        cur = conn.execute(sql, args or [])
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def executescript(path):
    conn = connect()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            conn.executescript(fh.read())
        conn.commit()
    finally:
        conn.close()


def init_db():
    executescript(SCHEMA_PATH)
    return DB_PATH


# --------------------------------------------------------- name normalisation
def norm_text(s):
    """Lowercase, strip accents and punctuation. Used for fuzzy company matching."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve_company(raw_name):
    """Best-effort company resolution: exact -> alias -> longest substring.
    Handles 'Societe Generale Corporate & Investment Banking' and 'SG' alike."""
    conn = connect()
    try:
        raw = (raw_name or "").strip()
        if not raw:
            return None
        r = conn.execute("SELECT * FROM companies WHERE lower(name)=lower(?)", [raw]).fetchall()
        if r:
            return dict(r[0])
        nraw = norm_text(raw)
        r = conn.execute("SELECT c.* FROM company_aliases a JOIN companies c ON c.id=a.company_id "
                         "WHERE lower(a.alias)=? OR lower(a.alias)=?", [raw.lower(), nraw]).fetchall()
        if r:
            return dict(r[0])
        # longest company name contained in the imported string
        rows = conn.execute("SELECT * FROM companies").fetchall()
        best, best_len = None, 0
        for row in rows:
            n = norm_text(row["name"])
            if len(n) >= 5 and n and n in nraw and len(n) > best_len:
                best, best_len = dict(row), len(n)
        if best:
            return best
        # reverse: imported string contained in a company name (e.g. "Optiver UK")
        for row in rows:
            n = norm_text(row["name"])
            if nraw and len(nraw) >= 5 and nraw in n and len(nraw) > best_len:
                best, best_len = dict(row), len(nraw)
        return best
    finally:
        conn.close()


def load_aliases(path=None):
    """Load data/company_aliases.csv (alias,company_name). Idempotent."""
    init_db()
    path = path or os.path.join(BASE, "data", "company_aliases.csv")
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, "r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
            alias = row.get("alias")
            target = row.get("company_name")
            if not alias or not target:
                continue
            co = resolve_company(target) if not query("SELECT id FROM companies WHERE lower(name)=lower(?)", [target]) \
                else query("SELECT * FROM companies WHERE lower(name)=lower(?)", [target])[0]
            if not co:
                continue
            execute("INSERT OR REPLACE INTO company_aliases (alias, company_id) VALUES (?,?)",
                    [alias, co["id"]])
            n += 1
    return n


# --------------------------------------------------------- light migrations
# execsscript() only runs CREATE TABLE IF NOT EXISTS, so columns added to
# schema.sql after a database already exists would never appear. This closes
# that gap for plain "col TYPE" column definitions.
def _columns_of(table):
    return {r["name"] for r in query(f"PRAGMA table_info({table})")}


def ensure_columns(table, wanted):
    """Add any of `wanted` (list of 'name TYPE' fragments) missing from table."""
    init_db()
    have = _columns_of(table)
    added = []
    for spec in wanted:
        col = spec.split()[0]
        if col not in have:
            try:
                execute(f"ALTER TABLE {table} ADD COLUMN {spec}")
                added.append(col)
            except sqlite3.OperationalError:
                pass
    return added


BRIEF_PATH = os.path.join(BASE, "data", "firm_briefs.csv")


def load_briefs(path=None, verbose=False):
    """Load data/firm_briefs.csv (name,research). Upserts on company name.
    Only writes when the brief actually changed, so research_updated stays
    meaningful (it tracks when *you* last touched the firm, not when you
    restarted the server)."""
    init_db()
    ensure_columns("companies", ["research TEXT", "research_updated TEXT"])
    path = path or BRIEF_PATH
    if not os.path.exists(path):
        return 0
    written = 0
    with open(path, "r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            row = {(k or "").strip().lstrip("\ufeff").lower(): (v or "").strip()
                   for k, v in row.items() if k}
            name, brief = row.get("name"), row.get("research")
            if not name or not brief:
                continue
            co = resolve_company(name)
            if not co:
                continue
            if (co.get("research") or "").strip() == brief:
                continue
            execute("UPDATE companies SET research=?, research_updated=CURRENT_TIMESTAMP WHERE id=?",
                    [brief, co["id"]])
            written += 1
    if verbose:
        print(f"briefs: {written} firm briefs loaded")
    return written


CAREERS_PATH = os.path.join(BASE, "data", "careers_urls.csv")


def load_careers_urls(path=None, verbose=False):
    """Load data/careers_urls.csv (name,careers_url). Written by
    app/discover_careers.py, which only records URLs that returned HTTP 200.
    Idempotent; only writes when the URL changed."""
    init_db()
    ensure_columns("companies", ["careers_url TEXT"])
    path = path or CAREERS_PATH
    if not os.path.exists(path):
        return 0
    written = 0
    with open(path, "r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            row = {(k or "").strip().lstrip("\ufeff").lower(): (v or "").strip()
                   for k, v in row.items() if k}
            name, url = row.get("name"), row.get("careers_url")
            if not name or not url:
                continue
            co = resolve_company(name)
            if not co:
                continue
            if (co.get("careers_url") or "").strip() == url:
                continue
            execute("UPDATE companies SET careers_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [url, co["id"]])
            written += 1
    if verbose:
        print(f"careers: {written} careers URLs loaded")
    return written


def table_exists(name):
    r = query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name])
    return bool(r)


def seed_files():
    """All companies_seed*.csv files, in deterministic order."""
    files = sorted(glob.glob(os.path.join(BASE, "data", "companies_seed*.csv")))
    return files or [SEED_PATH]


def load_seed(path=None, verbose=True):
    """Idempotent company import across every seed file. Matches on name."""
    init_db()
    paths = [path] if path else seed_files()
    added = updated = 0
    for p in paths:
        if not os.path.exists(p):
            continue
        a, u = _load_one_seed(p)
        added += a
        updated += u
    if verbose:
        print(f"seed: {added} companies added, {updated} updated")
    return added, updated


def _load_one_seed(path):
    added, updated = 0, 0
    with open(path, "r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            row = {(k or "").strip().lstrip("\ufeff").lower(): (v or "").strip()
                   for k, v in row.items() if k}
            name = row.get("name")
            if not name:
                continue
            existing = query("SELECT id FROM companies WHERE lower(name)=lower(?)", [name])
            fields = ("domain", "type", "subtype", "hq_city", "hq_country",
                      "market", "careers_url", "tier", "notes")
            if existing:
                sets, args = [], []
                for f in fields:
                    if row.get(f):
                        sets.append(f"{f}=?")
                        args.append(row[f])
                if not sets:
                    continue
                args.append(existing[0]["id"])
                execute(f"UPDATE companies SET {', '.join(sets)}, updated_at=CURRENT_TIMESTAMP WHERE id=?", args)
                updated += 1
            else:
                cols = ["name"] + [f for f in fields if row.get(f)]
                placeholders = ",".join("?" for _ in cols)
                execute(
                    f"INSERT INTO companies ({', '.join(cols)}) VALUES ({placeholders})",
                    [row.get("name")] + [row[f] for f in cols[1:]],
                )
                added += 1
    return added, updated


def normalize_company_name(name):
    if not name:
        return ""
    n = name.strip()
    for suffix in ("  ",):
        n = n.replace(suffix, " ")
    return n.strip()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        print("database at", init_db())
    elif len(sys.argv) > 1 and sys.argv[1] == "seed":
        load_seed()
    elif len(sys.argv) > 1 and sys.argv[1] == "briefs":
        print("briefs loaded:", load_briefs())
    else:
        print(__doc__)
        print("usage: python3 app/db.py init|seed")
