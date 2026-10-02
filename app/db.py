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


# --------------------------------------------------------------- harvester schema
# Columns added to existing tables after they were first created. executescript()
# only runs CREATE TABLE IF NOT EXISTS, so these must be applied with ALTER TABLE
# on any database that already exists. ensure_schema() is idempotent and safe to
# call on every boot.
COMPANY_EXTRA_COLUMNS = [
    "description TEXT", "founded_year INTEGER", "headcount TEXT", "employee_count INTEGER",
    "industry TEXT", "company_type TEXT", "keywords TEXT", "address TEXT",
    "linkedin_url TEXT", "twitter TEXT", "ticker TEXT", "source TEXT",
    "source_updated TEXT",
    # Prospeo firm data
    "website TEXT", "logo_url TEXT", "revenue_printed TEXT", "funding_total INTEGER",
    "technologies TEXT", "job_postings_count INTEGER", "prospeo_id TEXT",
    # email convention provenance
    "pattern_source TEXT", "pattern_sample TEXT",
]

CONTACT_EXTRA_COLUMNS = [
    "full_name TEXT", "headline TEXT", "department TEXT",
    "seniority_level TEXT", "location_raw TEXT", "state TEXT",
    "country_code TEXT", "timezone TEXT", "phone TEXT",
    # position_raw is the verbatim title - the one field that must never be
    # normalised, because it is what every derived field can be checked against.
    "position_raw TEXT",
    # email_masked stays: the reconstruction pass reads it to turn masked
    # Prospeo evidence into guessed candidates.
    "email_masked TEXT",
]

# Columns that carry nothing in the UNIFIED table. Since the per-source raw
# tables (contacts_hunter / contacts_prospeo) now hold every field the APIs
# return, contacts only keeps what the user works with: identity, company,
# what they do, where they are, mail, phone, LinkedIn - plus the operational
# columns. Dates, confidence and the re-enrichment ids all live with the
# source that produced them, or nowhere. Dropped on boot.
CONTACT_DEAD_COLUMNS = [
    "middle_name", "role", "twitter", "evidence", "latitude", "longitude",
    "email_type", "email_verification_method", "email_mx_provider",
    "email_sources", "phone_masked", "phone_status", "github", "bio",
    "avatar", "skills", "decision_maker", "fuzzy", "last_job_change",
    "email_confidence", "email_verified_at", "last_seen_at", "enriched_at",
    "source_ids", "source_updated",
    # workflow columns retired at the user's request - the table is a clean
    # directory of people, nothing else
    "tags", "notes", "priority", "hook", "status", "sources", "email_status",
]

# The canonical column order of `contacts` - who, then where they work, then
# what they do, then where they are, then how to reach them. SQLite cannot
# reorder columns in place, so when a live table drifted from this order it is
# rebuilt once (every value copied, nothing recomputed).
CONTACTS_ORDER = [
    "id", "first_name", "last_name", "full_name",
    "company_id", "company_name", "source",
    "job_title", "position_raw", "headline", "department",
    "seniority_level", "seniority", "desk",
    "city", "state", "country", "country_code", "location_raw", "timezone",
    "email", "email_masked", "email_source",
    "phone", "linkedin_url",
    "created_at", "updated_at",
]


def _rebuild_contacts_if_drifted():
    """Rebuild `contacts` when its column order no longer matches the schema.

    Column order is cosmetic to SQLite but not to a human reading the table:
    position_raw belongs next to job_title, email_masked next to email. The
    copy preserves every value; only the storage order changes. Returns True
    when a rebuild happened.
    """
    have = _columns_of("contacts")
    expected = [c for c in CONTACTS_ORDER if c in have]
    if have != expected or len(have) != len(CONTACTS_ORDER):
        conn = connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            # Build the new shape under a temp name, copy, then swap. The old
            # table is dropped (never renamed): renaming the referenced table
            # rewrites other tables' foreign keys and leaves them pointing at
            # a ghost, whereas a drop keeps their "REFERENCES contacts" intact.
            with open(SCHEMA_PATH, encoding="utf-8") as fh:
                schema = fh.read()
            block = schema[schema.index("CREATE TABLE IF NOT EXISTS contacts ("):]
            block = block[:block.index(");") + 2].replace(
                "CREATE TABLE IF NOT EXISTS contacts (", "CREATE TABLE contacts_new (")
            conn.executescript(block)
            common = ", ".join(c for c in CONTACTS_ORDER if c in have)
            conn.execute(f"INSERT OR REPLACE INTO contacts_new ({common}) "
                         f"SELECT {common} FROM contacts")
            conn.execute("DROP TABLE contacts")
            conn.execute("ALTER TABLE contacts_new RENAME TO contacts")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id)")
            conn.commit()
        finally:
            conn.close()
        return True
    return False


def ensure_schema(verbose=False):
    """Create the new tables and add the harvested columns to an existing DB.

    `CREATE TABLE IF NOT EXISTS` in schema.sql handles the new tables; the
    columns need ALTER TABLE because CREATE TABLE IF NOT EXISTS is a no-op once
    the table exists. Without this, every INSERT ... column from the harvester
    would fail on the database the user already has.
    """
    init_db()
    added = []
    added += ensure_columns("companies", COMPANY_EXTRA_COLUMNS)
    added += ensure_columns("contacts", CONTACT_EXTRA_COLUMNS)
    added += ensure_columns("person_job_history", ["departments TEXT"])
    added += ensure_columns("mail_queue", ["specifics TEXT"])
    added += ensure_columns("pattern_evidence",
                            ["pattern_before TEXT", "pattern_after TEXT"])

    # The first harvest stored Hunter's verbatim title in `headline`. It belongs
    # in position_raw now that we have a column for it, and moving it means the
    # existing rows keep their audit trail instead of losing it.
    moved = execute("UPDATE contacts SET position_raw=COALESCE(position_raw, headline) "
                    "WHERE position_raw IS NULL OR position_raw=''")

    dropped = []
    for col in CONTACT_DEAD_COLUMNS:
        if col in _columns_of("contacts"):
            try:
                execute(f"ALTER TABLE contacts DROP COLUMN {col}")
                dropped.append(col)
            except sqlite3.OperationalError:
                pass          # indexed or referenced: leave it, it is harmless
    rebuilt = _rebuild_contacts_if_drifted()
    if verbose:
        if added:
            print(f"schema: {len(added)} columns added ({', '.join(added)})")
        if dropped:
            print(f"schema: {len(dropped)} empty columns dropped ({', '.join(dropped)})")
        if rebuilt:
            print("schema: contacts rebuilt in canonical column order")
        if moved:
            print(f"schema: position_raw backfilled for {moved} existing contacts")
    return added


# --------------------------------------------------------------- fetch ledger
RAW_DIR = os.path.join(BASE, "data", "raw")


def fetch_seen(provider, endpoint, request_key):
    """Has this exact call already been made? Returns the fetch_log row or None.

    The whole point of the ledger: a metered API must never be paid for the same
    request twice, and a repeated run must resume rather than restart.
    """
    rows = query("SELECT * FROM fetch_log WHERE provider=? AND endpoint=? AND request_key=?",
                 [provider, endpoint, request_key])
    return rows[0] if rows else None


def save_raw(provider, endpoint, request_key, body_bytes):
    """Write a raw response to data/raw/<provider>/ and index it. Returns (path, sha)."""
    import hashlib
    digest = hashlib.sha256(body_bytes).hexdigest()
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", request_key)[:120].strip("_") or "response"
    folder = os.path.join(RAW_DIR, provider)
    os.makedirs(folder, exist_ok=True)
    fname = f"{endpoint}__{safe}__{digest[:8]}.json"
    full = os.path.join(folder, fname)
    if not os.path.exists(full):
        with open(full, "wb") as fh:
            fh.write(body_bytes)
    rel = os.path.relpath(full, BASE)
    execute("INSERT OR REPLACE INTO raw_payload (provider,endpoint,request_key,path,sha256,bytes) "
            "VALUES (?,?,?,?,?,?)", [provider, endpoint, request_key, rel, digest, len(body_bytes)])
    return rel, digest


def record_fetch(provider, endpoint, request_key, http_status=None, credits=0.0,
                 ok=True, error=None, raw_path=None, response_hash=None, tokens=0):
    """Log one external call. Idempotent on (provider, endpoint, request_key).

    `credits` is money: what a metered provider charged. `tokens` is a separate
    column because the LLM has no credits at all - storing prompt_tokens in
    `credits` made `credits_spent()` report 28,000 "credits" for 18 model calls,
    which is a number nobody budgeting an API can interpret.
    """
    execute(
        "INSERT OR REPLACE INTO fetch_log (provider,endpoint,request_key,http_status,credits,"
        "tokens,ok,error,raw_path,response_hash,fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
        [provider, endpoint, request_key, http_status, credits, tokens, 1 if ok else 0,
         error, raw_path, response_hash])
    return fetch_seen(provider, endpoint, request_key)


def credits_spent(provider=None, since=None):
    """How much has been spent, optionally for one provider or since a timestamp."""
    sql = "SELECT COALESCE(SUM(credits),0) n FROM fetch_log WHERE 1=1"
    args = []
    if provider:
        sql += " AND provider=?"
        args.append(provider)
    if since:
        sql += " AND fetched_at >= ?"
        args.append(since)
    return query(sql, args)[0]["n"]


def tokens_spent(provider=None):
    """Prompt tokens, which is what the LLM actually consumes."""
    sql = "SELECT COALESCE(SUM(tokens),0) n FROM fetch_log WHERE 1=1"
    args = []
    if provider:
        sql += " AND provider=?"
        args.append(provider)
    return query(sql, args)[0]["n"]


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
    elif len(sys.argv) > 1 and sys.argv[1] == "schema":
        print("columns added:", ensure_schema(verbose=True))
    else:
        print(__doc__)
        print("usage: python3 app/db.py init|seed")
