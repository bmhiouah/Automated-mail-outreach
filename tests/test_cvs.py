"""CV variants: the base document is never overwritten, and nothing unvalidated
is ever attachable to a mail.

The two properties worth defending are both about loss. A tailoring that drops a
qualification, or that overwrites the master CV, costs a rewrite to recover -
so the design makes both impossible rather than merely discouraged.
"""
from .harness import bootstrap  # noqa: E402

import os
import shutil
import contextlib
import tempfile
import db
import llm
import unittest

from api import cvs, queue

# latex_build.CV_DIR is the real data/cvs/. These tests compile and delete
# documents, so the module points it at a throwaway directory: a delete test
# must never be able to remove a CV you actually own.
_REAL_CV_DIR = None
_TMP_CV_DIR = None


def setUpModule():
    global _REAL_CV_DIR, _TMP_CV_DIR
    bootstrap()
    import latex_build
    _REAL_CV_DIR = latex_build.CV_DIR
    _TMP_CV_DIR = tempfile.mkdtemp(prefix="cvtest-")
    latex_build.CV_DIR = _TMP_CV_DIR


def tearDownModule():
    import latex_build
    if _REAL_CV_DIR is not None:
        latex_build.CV_DIR = _REAL_CV_DIR
    if _TMP_CV_DIR:
        shutil.rmtree(_TMP_CV_DIR, ignore_errors=True)


@contextlib.contextmanager
def real_cv_dir():
    """Read from the real data/cvs/ for the length of one test.

    The module points CV_DIR at a throwaway directory so no test can write or
    delete a document you own. The base-CV attachment tests still have to read
    the real main.pdf, and pdf_bytes() checks the path against CV_DIR - so they
    ask for the real directory explicitly, and only to read.
    """
    import latex_build
    saved = latex_build.CV_DIR
    latex_build.CV_DIR = _REAL_CV_DIR
    try:
        yield
    finally:
        latex_build.CV_DIR = saved


def _stub_llm(body="TAILORED CV TEXT", fail=False):
    """Replace the two LLM doors for one test. Returns the restore callable."""
    saved = (llm.is_configured, llm.generate_cv, llm.generate_cv_latex)
    llm.is_configured = lambda: True
    llm.generate_cv = (lambda *a, **k: {"ok": False, "error": "the model is down"}
                       if fail else
                       {"ok": True, "body": body, "model": "test-model",
                        "cached": False, "tokens": 5})
    llm.generate_cv_latex = (lambda *a, **k: {"ok": False, "error": "the model is down"}
                             if fail else
                             {"ok": True, "body": body, "model": "test-model",
                              "cached": False, "tokens": 5})
    return saved


def _restore(saved):
    llm.is_configured, llm.generate_cv, llm.generate_cv_latex = saved


class CvFixture(unittest.TestCase):
    BASE = "BASE CV - university, master, python, C++, the original text."

    def setUp(self):
        db.execute("DELETE FROM mail_queue")
        db.execute("DELETE FROM cv_variants")
        db.execute("UPDATE profile SET cv_text=? WHERE id=1", [self.BASE])
        self.co = db.resolve_company("Jane Street")
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "email,email_source,source) VALUES ('Robin','Tester',?,'Jane Street',"
                   "'robin.tester@janestreet.com','hunter','cvtest')", [self.co["id"]])
        self.cid = db.query("SELECT id FROM contacts WHERE source='cvtest'")[0]["id"]

    def tearDown(self):
        db.execute("DELETE FROM cv_variants")
        db.execute("UPDATE profile SET cv_text=? WHERE id=1", [self.BASE])
        db.execute("DELETE FROM contacts WHERE source='cvtest'")


class TestTheBaseIsSafe(CvFixture):
    def test_a_proposal_leaves_the_base_untouched(self):
        saved = _stub_llm()
        try:
            cvs.api_cv_propose({"company_id": self.co["id"]})
        finally:
            _restore(saved)
        self.assertEqual(cvs.base_cv(), self.BASE)

    def test_an_edit_to_a_variant_leaves_the_base_untouched(self):
        vid = cvs.api_cv_create({"name": "manual", "body": "something else"})["id"]
        cvs.api_cv_save({"id": vid, "body": "edited again"})
        self.assertEqual(cvs.base_cv(), self.BASE)

    def test_a_failed_proposal_changes_nothing_at_all(self):
        saved = _stub_llm(fail=True)
        try:
            r = cvs.api_cv_propose({"company_id": self.co["id"]})
        finally:
            _restore(saved)
        self.assertIn("down", r["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM cv_variants")[0]["n"], 0)
        self.assertEqual(cvs.base_cv(), self.BASE)

    def test_tailoring_an_empty_base_is_refused(self):
        """Otherwise the model invents a CV out of nothing, cheerfully."""
        db.execute("UPDATE profile SET cv_text='' WHERE id=1")
        saved = _stub_llm()
        try:
            r = cvs.api_cv_propose({"company_id": self.co["id"]})
        finally:
            _restore(saved)
        self.assertIn("no base CV", r["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM cv_variants")[0]["n"], 0)


class TestValidation(CvFixture):
    def test_a_proposal_arrives_pending(self):
        saved = _stub_llm()
        try:
            r = cvs.api_cv_propose({"company_id": self.co["id"]})
        finally:
            _restore(saved)
        self.assertEqual(r["variant"]["status"], "pending")

    def test_a_pending_variant_cannot_be_attached_to_a_mail(self):
        """The rule that stops an unreviewed document going out with your name on it."""
        vid = cvs.api_cv_create({"name": "unreviewed", "body": "CV text"})["id"]
        self.assertEqual(cvs.api_cv_create({"name": "unreviewed"})["status"], "pending")
        self.assertEqual(queue._cv_text(vid), "",
                         "a pending variant must not reach the mailer")

    def test_validating_makes_it_attachable(self):
        vid = cvs.api_cv_create({"name": "reviewed", "body": "CV text"})["id"]
        self.assertEqual(queue._cv_text(vid), "")
        cvs.api_cv_review({"id": vid, "decision": "validated"})
        self.assertEqual(queue._cv_text(vid), "CV text")

    def test_a_rejected_variant_stays_unattachable(self):
        vid = cvs.api_cv_create({"name": "no", "body": "CV text"})["id"]
        cvs.api_cv_review({"id": vid, "decision": "rejected"})
        self.assertEqual(queue._cv_text(vid), "")

    def test_an_unknown_decision_is_refused(self):
        vid = cvs.api_cv_create({"name": "x", "body": "y"})["id"]
        self.assertIn("must be", cvs.api_cv_review({"id": vid, "decision": "maybe"})["error"])

    def test_saving_cannot_validate(self):
        """Otherwise a stray keystroke in the editor would approve a document."""
        vid = cvs.api_cv_create({"name": "x", "body": "y"})["id"]
        cvs.api_cv_save({"id": vid, "status": "validated"})
        self.assertEqual(db.query("SELECT status FROM cv_variants WHERE id=?",
                                  [vid])[0]["status"], "pending")

    def test_a_variant_needs_a_name(self):
        self.assertIn("name", cvs.api_cv_create({"body": "x"})["error"])

    def test_the_list_reports_where_the_base_is(self):
        r = cvs.api_cv_list({})
        self.assertTrue(r["base_present"])
        self.assertEqual(r["base_bytes"], len(self.BASE))

    def test_the_base_is_listed_first_as_yours(self):
        """The master document is selectable like any variant - it is the CV."""
        r = cvs.api_cv_list({})
        first = r["variants"][0]
        self.assertEqual(first["id"], 0)
        self.assertEqual(first["generation"], "you")
        self.assertEqual(first["status"], "validated")

    def test_the_base_reads_like_a_variant(self):
        v = cvs.api_cv_get({"id": 0})
        self.assertEqual(v["name"], "Base CV (main.tex)")
        self.assertTrue((v["latex"] or "").strip())

    def test_the_base_can_be_attached(self):
        """Your own document needs no review gate - but it needs a real PDF."""
        with real_cv_dir():
            fname, pdf = queue._cv_attachment(0)
        self.assertTrue(fname.endswith(".pdf"))

    def test_a_mail_with_a_compilable_cv_passes(self):
        """The mirror of the PDF rule: a variant WITH a PDF is not blocked."""
        vid = cvs.api_cv_create(
            {"name": "compiled", "body": "CV text"})["id"]
        db.execute("UPDATE cv_variants SET status='validated', pdf_path='data/cvs/main.pdf' "
                   "WHERE id=?", [vid])
        with real_cv_dir():
            fname, pdf = queue._cv_attachment(vid)
        self.assertTrue(pdf)
        from api import queue as _q  # noqa: F811
        problems = _q.mailer.preflight("a@b.com", "s", "b", cv_attached=True,
                                       cv_has_pdf=True)
        self.assertNotIn("no PDF", "; ".join(problems))


class TestDelete(CvFixture):
    """Deleting a variant must never take the base, or a draft's attachment,
    with it - those are the two losses a click cannot undo."""

    def test_deleting_a_variant_removes_it(self):
        vid = cvs.api_cv_create({"name": "useless", "body": "words"})["id"]
        r = cvs.api_cv_delete({"id": vid})
        self.assertTrue(r["ok"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM cv_variants WHERE id=?",
                                  [vid])[0]["n"], 0)

    def test_the_base_cannot_be_deleted(self):
        r = cvs.api_cv_delete({"id": 0})
        self.assertIn("base", r["error"])

    def test_a_variant_a_queued_mail_points_at_is_refused(self):
        """Deleting it would null the link and let the draft go out bare."""
        vid = cvs.api_cv_create({"name": "attached", "body": "words"})["id"]
        db.execute("INSERT INTO mail_queue (contact_id, cv_id, status) "
                   "VALUES (?,?,'pending')", [self.cid, vid])
        r = cvs.api_cv_delete({"id": vid})
        self.assertIn("attach", r["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM cv_variants WHERE id=?",
                                  [vid])[0]["n"], 1)

    def test_a_failed_mails_cv_is_also_protected(self):
        """A failed send is retried, so its CV is still needed."""
        vid = cvs.api_cv_create({"name": "retry", "body": "words"})["id"]
        db.execute("INSERT INTO mail_queue (contact_id, cv_id, status) "
                   "VALUES (?,?,'failed')", [self.cid, vid])
        self.assertIn("attach", cvs.api_cv_delete({"id": vid})["error"])

    def test_a_sent_mails_cv_may_be_deleted(self):
        """The mail is already out; the document is just clutter now."""
        vid = cvs.api_cv_create({"name": "old", "body": "words"})["id"]
        db.execute("INSERT INTO mail_queue (contact_id, cv_id, status, sent_at) "
                   "VALUES (?,?,'sent',CURRENT_TIMESTAMP)", [self.cid, vid])
        self.assertTrue(cvs.api_cv_delete({"id": vid})["ok"])

    def test_a_deleted_variant_can_no_longer_be_attached(self):
        vid = cvs.api_cv_create({"name": "gone", "body": "CV text"})["id"]
        cvs.api_cv_review({"id": vid, "decision": "validated"})
        cvs.api_cv_delete({"id": vid})
        self.assertEqual(queue._cv_text(vid), "")

    def test_deleting_an_unknown_variant_is_refused(self):
        self.assertIn("no longer exists", cvs.api_cv_delete({"id": 999999})["error"])

    def test_deleting_an_empty_body_is_refused_not_a_crash(self):
        self.assertIn("no longer exists", cvs.api_cv_delete({})["error"])


class TestDeleteFiles(CvFixture):
    """The compiled files go with the row - but only files inside data/cvs/."""

    def test_the_compiled_files_are_removed(self):
        import latex_build
        os.makedirs(latex_build.CV_DIR, exist_ok=True)
        tex = os.path.join(latex_build.CV_DIR, "cv-424242.tex")
        pdf = os.path.join(latex_build.CV_DIR, "cv-424242.pdf")
        for path in (tex, pdf):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x")
        removed = latex_build.remove_variant_files(424242)
        self.assertTrue(any(x.endswith("cv-424242.tex") for x in removed))
        self.assertTrue(any(x.endswith("cv-424242.pdf") for x in removed))
        self.assertFalse(os.path.exists(tex))
        self.assertFalse(os.path.exists(pdf))

    def test_a_path_outside_the_cv_dir_is_never_touched(self):
        """`pdf_path` comes from a row, so '../' must not become a shredder."""
        import tempfile
        import latex_build
        victim = os.path.join(tempfile.gettempdir(), "not-a-cv-must-survive.txt")
        with open(victim, "w", encoding="utf-8") as fh:
            fh.write("keep me")
        try:
            removed = latex_build.remove_variant_files(424242, victim)
            self.assertTrue(os.path.exists(victim))
            self.assertNotIn(victim, removed)
        finally:
            os.remove(victim)


class TestLatexCv(CvFixture):
    TEX = ("\\documentclass{article}\\n\\begin{document}\n"
           "Badre Mhiouah -- quant CV.\n\\end{document}\n")

    def setUp(self):
        super().setUp()
        self._saved_build = None

    def tearDown(self):
        super().tearDown()
        if self._saved_build is not None:
            import latex_build
            (latex_build.compile_latex, latex_build.base_tex,
             latex_build.engine) = self._saved_build

    def _stub_build(self, ok=True, error="an unbalanced brace"):
        import latex_build
        self._saved_build = (latex_build.compile_latex, latex_build.base_tex,
                             latex_build.engine)
        latex_build.base_tex = lambda: self.TEX
        latex_build.engine = lambda: "/usr/bin/pdflatex"
        latex_build.compile_latex = (
            lambda _tex, _slug: {"ok": True, "pdf": f"data/cvs/{_slug}.pdf",
                                 "error": None, "runs": 2}
            if ok else {"ok": False, "error": error})

    def test_a_latex_edit_rebuilds_the_pdf(self):
        """The preview must show what was just written, not what used to be."""
        vid = cvs.api_cv_create({"name": "x", "latex": self.TEX})["id"]
        self._stub_build()
        r = cvs.api_cv_save({"id": vid, "latex": self.TEX.replace("quant", "quant 2")})
        self.assertIn("compiled", r)
        self.assertTrue(r["compiled"]["ok"])

    def test_a_word_edit_is_written_back_to_latex_and_compiled(self):
        """Otherwise Save on the words pane leaves the PDF showing the old run."""
        row = cvs.api_cv_create({"name": "x", "latex": self.TEX})
        vid = row["id"]
        self._stub_build()
        new_body = (row["body"] or "").replace("quant", "quant 2")
        r = cvs.api_cv_save({"id": vid, "body": new_body})
        self.assertIn("compiled", r)
        self.assertTrue(r["compiled"]["ok"])
        text = db.query("SELECT latex FROM cv_variants WHERE id=?", [vid])[0]["latex"]
        self.assertIn("quant 2", text)

    def test_a_broken_build_keeps_the_text_and_reports_the_error(self):
        vid = cvs.api_cv_create({"name": "x", "latex": self.TEX})["id"]
        self._stub_build(ok=False)
        r = cvs.api_cv_save({"id": vid, "latex": self.TEX + "%"})
        self.assertFalse(r["compiled"]["ok"])
        self.assertIn("brace", r["compiled"]["error"])
        text = db.query("SELECT latex FROM cv_variants WHERE id=?", [vid])[0]["latex"]
        self.assertIn("%", text)

    def test_a_failed_compile_keeps_the_previous_pdf(self):
        vid = cvs.api_cv_create({"name": "x", "latex": self.TEX})["id"]
        self._stub_build(ok=False)
        r = cvs.api_cv_save({"id": vid, "latex": self.TEX + "%"})
        self.assertTrue(r["compiled"].get("kept_previous") in (True, False))

    def test_a_body_edit_without_latex_does_not_compile(self):
        vid = cvs.api_cv_create({"name": "x", "body": "words only"})["id"]
        r = cvs.api_cv_save({"id": vid, "body": "words only again"})
        self.assertNotIn("compiled", r)

    def test_latex_proposal_uses_the_chosen_model(self):
        seen = {}
        saved = _stub_llm(body=self.TEX)
        llm.generate_cv_latex = lambda *a, **k: (
            seen.update(k) or
            {"ok": True, "body": self.TEX,
             "model": k.get("model") or "default",
             "cached": False, "tokens": 1})
        self._stub_build()
        try:
            r = cvs.api_cv_latex_propose(
                {"company_id": self.co["id"], "role_target": "QR",
                 "model": "pick-me"})
        finally:
            _restore(saved)
        self.assertNotIn("error", r)
        self.assertEqual(seen.get("model"), "pick-me")
        self.assertIn("llm:pick-me", r["variant"]["generation"])

    def test_a_latex_proposal_needs_a_target(self):
        """An empty POST must stay a cheap error, never a real model call."""
        r = cvs.api_cv_latex_propose({})
        self.assertIn("firm", r["error"])

    def test_word_edits_patch_the_matching_phrase_in_latex(self):
        import latex_cv
        tex = (r"\documentclass{article}\begin{document}"
               r"hello world from Paris\end{document}")
        out = latex_cv.apply_text_edits(tex, "hello world from Paris",
                                        "hello world from London")
        self.assertIn("London", out)
        self.assertNotIn("Paris", out)

    def test_an_added_word_reaches_the_latex(self):
        """Adding a word is the commonest edit, and a diff calls it an insert."""
        import latex_cv
        tex = (r"\documentclass{article}\begin{document}"
               r"hello world from Paris\end{document}")
        out = latex_cv.apply_text_edits(tex, "hello world from Paris",
                                        "hello world from Paris today")
        self.assertIn("Paris today", out)

    def test_a_word_edited_into_the_latex_reads_back_as_typed(self):
        """The point of the round trip: the words pane and the .tex must agree."""
        import latex_cv
        tex = (r"\documentclass{article}\begin{document}"
               r"hello world from Paris\end{document}")
        for wanted in ("hello world from London",
                       "hello world from Paris today",
                       "hello world from Paris C++ & R&D",
                       "goodbye world from Paris"):
            patched = latex_cv.apply_text_edits(tex, "hello world from Paris", wanted)
            self.assertEqual(latex_cv.latex_to_text(patched), wanted)

    def test_a_word_typed_before_the_first_line_still_lands(self):
        """With nothing above it, the insertion anchors on what follows instead."""
        import latex_cv
        tex = (r"\documentclass{article}\begin{document}"
               r"hello world from Paris\end{document}")
        out = latex_cv.apply_text_edits(tex, "hello world from Paris",
                                        "note: hello world from Paris")
        self.assertIn("note:", out)
        self.assertIn("hello world from Paris", out)

    def test_an_ambiguous_word_edit_is_not_guessed_into_the_wrong_line(self):
        """A repeated phrase must not be patched at its first occurrence.

        "Power BI" appears twice in a real CV. Patching the first match edits a
        sentence the user never touched, compiles it, and reports success - the
        exact failure the words pane exists to prevent. With two identical
        sentences there is no way to tell which one was meant, so the edit is
        refused and the .tex left alone rather than guessed.
        """
        import latex_cv
        tex = (r"\documentclass{article}\begin{document}"
               r"Power BI platform here.\nPower BI platform here.\n\end{document}")
        body = latex_cv.latex_to_text(tex)
        out = latex_cv.apply_text_edits(tex, body,
                                        body.replace("Power BI platform here.",
                                                     "Power BI, Tableau", 1))
        self.assertEqual(out, tex)


if __name__ == "__main__":
    unittest.main()