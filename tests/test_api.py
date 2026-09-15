"""The API route table: every route resolves and refuses correctly."""

# First: puts app/ on sys.path and owns the shared temp database.
from .harness import bootstrap  # noqa: E402

import api
import server
import unittest


def setUpModule():
    bootstrap()

class TestApiDispatch(unittest.TestCase):
    """The route table is the API's contract with the UI.

    These are the tests that made moving 45 if/elif branches possible: they walk
    the table and prove every route still resolves, rather than trusting that a
    hand-moved branch landed intact.
    """

    @staticmethod
    def _path(pattern):
        return [p if not p.startswith("<") else "1" for p in pattern.split("/")]

    def test_every_declared_route_resolves(self):
        for method, pattern, _ in api.ROUTES:
            with self.subTest(route="%s /api/%s" % (method, pattern)):
                status, _value = server.dispatch(method, self._path(pattern), {}, {})
                self.assertNotEqual(status, 404, "no handler matched this route")

    def test_no_route_crashes_on_an_empty_body(self):
        """An empty payload is a JSON error, never a 500."""
        broken = []
        try:
            for method, pattern, _ in api.ROUTES:
                status, value = server.dispatch(method, self._path(pattern), {}, {})
                if status == 500:
                    broken.append("%s /api/%s -> %s" % (method, pattern, value))
        finally:
            server.dispatch("POST", ["demo", "clear"], {}, {})   # undo demo inserts
        self.assertEqual(broken, [], "routes that fall over on an empty body")

    def test_unknown_paths_are_refused(self):
        self.assertEqual(server.dispatch("GET", ["nope"], {}, {})[0], 404)
        self.assertEqual(server.dispatch("GET", ["stats", "extra"], {}, {})[0], 404)
        self.assertEqual(server.dispatch("POST", ["demo", "load", "extra"], {}, {})[0], 404)

    def test_api_root_answers_ok(self):
        self.assertEqual(server.dispatch("GET", [], {}, {}), (200, {"ok": True}))

    def test_exports_return_bytes_not_json(self):
        status, value = server.dispatch("GET", ["export", "contacts"], {}, {})
        self.assertEqual(status, 200)
        self.assertIsInstance(value, server.Raw)
        self.assertIn("text/csv", value.ctype)

        status, value = server.dispatch("GET", ["export", "sourcing"], {}, {})
        self.assertIsInstance(value, server.Raw)
        self.assertIn("text/markdown", value.ctype)
        self.assertIn("sourcing-worklist.md", value.headers["Content-Disposition"])

    def test_generic_writes_only_touch_whitelisted_tables(self):
        """The table name reaches an SQL string, so it must come from the map."""
        self.assertEqual(server.dispatch("DELETE", ["sqlite_master", "1"], {}, {})[0], 404)
        self.assertEqual(server.dispatch("PUT", ["sqlite_master", "1"], {}, {})[0], 404)
        self.assertEqual(server.dispatch("DELETE", ["vendor_universe", "1"], {}, {})[0], 404)
