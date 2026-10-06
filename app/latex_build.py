"""main.tex -> a PDF, so a tailored CV can leave the app as the thing you send.

The notebook did this in four lines and died on the first typo: pdflatex exits
non-zero, the exception escapes, and the generated .tex is left on disk with no
indication of which line broke. Here a failure is a *result* - the log excerpt
is returned to the tab where you can read it - because a model that produced
one unbalanced brace must not take the request down with it.

Two details that matter:

  * The compiler runs with the project root as its working directory, so an
    `\\input{...}` in main.tex resolves the same way it does when you compile
    it by hand. Output goes to a temp directory and only the PDF is kept.
  * Files live in data/cvs/ and every read is re-checked against that
    directory, because the path arrives from a database row that a name edit
    could have made stale.
"""
import os
import re
import json
import shutil
import subprocess
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CV_DIR = os.path.join(BASE, "data", "cvs")
CONFIG_PATH = os.path.join(BASE, "config.json")
DEFAULT_TEX = "main.tex"
SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(value):
    """A filesystem-safe stem. Empty input falls back rather than writing '..'."""
    s = SLUG_RE.sub("-", str(value or "").strip()).strip("-._")
    return s[:60] or "cv"


def _cv_cfg():
    """The optional `cv` block of config.json. Absent or broken is not an error."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except (OSError, ValueError):
        return {}
    block = cfg.get("cv")
    return block if isinstance(block, dict) else {}


def base_tex_path():
    """Where the master LaTeX CV lives. Overridable as cv.base_tex."""
    rel = (_cv_cfg().get("base_tex") or DEFAULT_TEX).strip()
    return rel if os.path.isabs(rel) else os.path.join(BASE, rel)


def base_tex():
    """The master document, or '' when it is missing - never an exception."""
    try:
        with open(base_tex_path(), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def engine():
    """The TeX binary, or None. Reported rather than raised: the tab says so."""
    want = (_cv_cfg().get("engine") or "pdflatex").strip()
    return shutil.which(want) or shutil.which("pdflatex") or shutil.which("tectonic")


def _failure(log_path, default):
    """The first `!` error in the .tex log, with the two lines after it.

    A TeX log is thousands of lines of font noise; the only part a human can
    act on is the `!` diagnostic and the source line it points at.
    """
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return default
    for i, line in enumerate(lines):
        if line.startswith("!"):
            tail = [ln.rstrip() for ln in lines[i:i + 3]]
            return "\n".join(t for t in tail if t.strip())
    return default


FENCE_RE = re.compile(r"```(?:latex|tex)?\s*(.*?)```", re.S)


def strip_fences(tex):
    """Remove a ```latex wrapper. Models wrap LaTeX about half the time."""
    t = tex or ""
    m = FENCE_RE.search(t)
    if m:
        t = m.group(1)
    return t.strip()


def ensure_document(tex, base):
    """Turn whatever the model returned into a compilable full document.

    Three cases, because all three happen:

      * a complete document - used as is;
      * just the body (no \\documentclass) - main.tex's preamble is prepended,
        which is what actually protects the document class, the packages and
        the custom macros from being "tidied up" by the model;
      * a body that already opens \\begin{document} - preamble prefixed, no
        second environment added.

    An unpreambleable reply returns '' rather than a document that would fail
    to compile for a reason the user cannot see.
    """
    t = strip_fences(tex)
    if not t.strip():
        return ""
    if r"\documentclass" in t:
        if r"\end{document}" not in t:
            t = t.rstrip() + "\n\\end{document}\n"
        return t

    head = base or ""
    if r"\begin{document}" in head:
        preamble = head.split(r"\begin{document}")[0]
    else:
        preamble = ""
    if not preamble:
        return ""
    if r"\begin{document}" in t:
        out = preamble + t
    else:
        out = f"{preamble}\\begin{{document}}\n{t.strip()}\n\\end{{document}}\n"
    if r"\end{document}" not in out:
        out = out.rstrip() + "\n\\end{document}\n"
    return out


def compile_latex(latex, slug="cv"):
    """Write `latex`, run pdflatex twice, return {ok, pdf, tex, error, runs}.

    Two passes because a first pass writes the aux file that a second pass
    reads; a CV with no cross-references costs one extra second, and one with
    them costs a broken PDF otherwise.
    """
    bin_path = engine()
    if not bin_path:
        return {"ok": False, "error": "no TeX engine found - install MacTeX/TeX "
                                      "Live (pdflatex) or set cv.engine in config.json"}
    if not (latex or "").strip():
        return {"ok": False, "error": "nothing to compile: the document is empty"}

    os.makedirs(CV_DIR, exist_ok=True)
    stem = slugify(slug)
    tex_path = os.path.join(CV_DIR, stem + ".tex")
    with open(tex_path, "w", encoding="utf-8") as fh:
        fh.write(latex)

    out_dir = tempfile.mkdtemp(prefix="cvbuild-")
    try:
        runs = 0
        for _ in range(2):
            subprocess.run(
                [bin_path, "-interaction=nonstopmode", "-output-directory", out_dir,
                 tex_path],
                cwd=BASE, capture_output=True, text=True, timeout=180)
            runs += 1
            pdf_tmp = os.path.join(out_dir, stem + ".pdf")
            if not os.path.exists(pdf_tmp):
                log = os.path.join(out_dir, stem + ".log")
                # the log lives in out_dir, which is about to be removed
                return {"ok": False, "tex": tex_path, "error":
                        _failure(log, "pdflatex produced no PDF"), "runs": runs}

        pdf_final = os.path.join(CV_DIR, stem + ".pdf")
        shutil.move(os.path.join(out_dir, stem + ".pdf"), pdf_final)
        return {"ok": True, "pdf": os.path.relpath(pdf_final, BASE).replace(os.sep, "/"),
                "tex": os.path.relpath(tex_path, BASE).replace(os.sep, "/"),
                "error": None, "runs": runs}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pdflatex did not finish within 180s - "
                                      "the document probably has an infinite loop"}
    except OSError as exc:
        return {"ok": False, "error": f"could not run pdflatex: {exc}"}
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def pdf_exists(pdf_path):
    """True when the file is really there - without reading it into memory.

    The queue asks this for every row it renders; pulling a 140 KB PDF per row
    just to draw a badge would be a waste of I/O.
    """
    if not pdf_path:
        return False
    target = pdf_path if os.path.isabs(pdf_path) else os.path.join(BASE, pdf_path)
    real_dir = os.path.realpath(CV_DIR)
    real = os.path.realpath(target)
    if not (real == real_dir or real.startswith(real_dir + os.sep)):
        return False
    return os.path.isfile(real)


def pdf_bytes(pdf_path):
    """Read a stored PDF, refusing anything outside data/cvs/.

    `pdf_path` came from the database, so it is trusted-but-checked: a stray
    '../' would otherwise turn this endpoint into a file reader.
    """
    if not pdf_path:
        return b""
    target = pdf_path if os.path.isabs(pdf_path) else os.path.join(BASE, pdf_path)
    real_dir = os.path.realpath(CV_DIR)
    real = os.path.realpath(target)
    if not (real == real_dir or real.startswith(real_dir + os.sep)):
        return b""
    try:
        with open(real, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def remove_variant_files(vid, pdf_path=None):
    """Delete one variant's compiled artefacts. Best-effort; never raises.

    A deleted variant should not leave a PDF behind that a stale link could
    still serve, so the .tex and .pdf are removed with it. Only paths that
    resolve inside data/cvs/ are touched: `pdf_path` came from a database row,
    and a stray '../' must not turn a delete into a shredder. A file that is
    already gone is not an error - the row is what the user asked to remove.
    """
    removed = []
    candidates = [os.path.join(CV_DIR, f"cv-{vid}.tex"),
                  os.path.join(CV_DIR, f"cv-{vid}.pdf")]
    if pdf_path:
        candidates.append(pdf_path if os.path.isabs(pdf_path)
                          else os.path.join(BASE, pdf_path))
    real_dir = os.path.realpath(CV_DIR)
    for candidate in candidates:
        real = os.path.realpath(candidate)
        if not (real == real_dir or real.startswith(real_dir + os.sep)):
            continue
        try:
            os.remove(real)
        except OSError:
            continue
        removed.append(os.path.relpath(real, BASE).replace(os.sep, "/"))
    return removed
