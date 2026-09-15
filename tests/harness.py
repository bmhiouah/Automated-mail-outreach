"""One temp database for the whole suite, set up the way the app sets itself up.

This mirrors server.main(): the real entry point creates the schema, migrates it,
seeds the templates and loads the CSV seeds, and a harness that skips any of that
is testing a different program. Two lessons are baked in here, both learned the
hard way in this project:

  * `db.DB_PATH` and `db.RAW_DIR` are read as globals at call time, so the tests
    can point them at a temp directory - and must, or a harvest test writes raw
    provider payloads into the real data/raw/.
  * templates must be seeded, or every test that drafts an email fails with
    "no template available" and it looks like a template bug.

`bootstrap()` is idempotent: unittest calls setUpModule once per module, and the
suite must share ONE database, because some tests build on rows another test left
behind (and clean up after themselves).
"""
import os
import sys
import tempfile

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "providers"))

import db                                     # noqa: E402
from api import templates as api_templates    # noqa: E402

_BOOTSTRAPPED = False


def bootstrap():
    """Create and seed the temp database, once per test run."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return db.DB_PATH

    tmp = tempfile.mkdtemp(prefix="cold-approach-tests-")
    db.DB_PATH = os.path.join(tmp, "test.db")
    db.SEED_PATH = os.path.join(db.BASE, "data", "companies_seed.csv")
    # Raw payloads are written next to the DB path, but the raw folder is a module
    # constant - redirect it too, or the tests litter the real data/raw.
    db.RAW_DIR = os.path.join(tmp, "raw")

    db.init_db()
    db.load_seed(verbose=False)
    db.load_aliases()
    # The server migrates on boot; the test DB must do the same or a harvest test
    # inserts into a column that does not exist yet.
    db.ensure_schema()
    # The server seeds templates on its first boot - so must the test DB.
    api_templates.api_seed_templates()

    _BOOTSTRAPPED = True
    return db.DB_PATH


def is_temporary():
    """True when the suite is pointed at its own database, not the real one.

    Asserted by the tests: silently writing to data/cold_approach.db would be the
    worst failure this harness could have.
    """
    return db.DB_PATH != os.path.join(db.BASE, "data", "cold_approach.db")