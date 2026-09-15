"""The data layer must not depend on the web layer."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

import db
import os
import server
import sys
import taxonomy
import unittest


def setUpModule():
    bootstrap()

class TestLayering(unittest.TestCase):
    """The data layer must not depend on the web layer.

    people_store imported `server` purely to call derive_from_title, which dragged
    the HTTP server (and cv_parse and people_parse) into every harvest run. The
    vocabulary now lives in taxonomy.py, and this test is what keeps it there.

    Run in a fresh interpreter on purpose: by the time these tests run, other
    modules have already imported server, so the leak would be invisible here.
    """

    APP_DIR = os.path.dirname(os.path.abspath(db.__file__))

    def _run(self, code):
        import subprocess
        src = "import sys; sys.path.insert(0, %r); " % self.APP_DIR + code
        out = subprocess.run([sys.executable, "-c", src],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_people_store_does_not_import_the_web_layer(self):
        got = self._run("import people_store; "
                        "print('LEAKED' if 'server' in sys.modules else 'clean')")
        self.assertEqual(got, "clean")

    def test_harvest_does_not_import_the_web_layer(self):
        got = self._run("import harvest; "
                        "print('LEAKED' if 'server' in sys.modules else 'clean')")
        self.assertEqual(got, "clean")

    def test_taxonomy_is_importable_on_its_own(self):
        # ('VP', 'FX'): the fx rule precedes options in DESK_RULES, so an options
        # desk on an FX title resolves to FX. Verified against the real table
        # rather than assumed - the first version of this test asserted 'Quant'.
        got = self._run("import taxonomy; "
                        "print(taxonomy.derive_from_title('VP, FX Options'))")
        self.assertEqual(got, "('VP', 'FX')")

    def test_the_vocabulary_lives_only_in_taxonomy(self):
        """The shell must not grow a second copy of the rules.

        Two versions of "a Vice President is a VP" is exactly how the same person
        ends up labelled differently depending on which module reached them first.
        """
        for name in ("COMPANY_FIELDS", "CONTACT_FIELDS", "OUTREACH_FIELDS",
                     "TEMPLATE_FIELDS", "APPLICATION_FIELDS", "PROFILE_FIELDS",
                     "TITLES_BY_TYPE", "CONTACT_IMPORT_MAP", "SENIORITY_RULES",
                     "DESK_RULES", "derive_from_title"):
            self.assertTrue(hasattr(taxonomy, name), name)
            self.assertFalse(hasattr(server, name),
                             "%s is defined in server.py as well as taxonomy.py" % name)

    def test_server_declares_no_rules_of_its_own(self):
        import inspect
        src = inspect.getsource(server)
        for name in ("SENIORITY_RULES", "DESK_RULES", "COMPANY_FIELDS", "PATTERNS"):
            self.assertNotIn(name + " = ", src, "%s is defined inside server.py" % name)
