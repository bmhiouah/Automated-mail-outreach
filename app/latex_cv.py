"""LaTeX resume -> plain text, for the profile's `cv_text` field.

The reader in `cv_parse.py` works on plain text, and the model needs plain text
too - it is cheaper, and a wall of `\resumeItem{...}` teaches it nothing about
what you have actually done.

The conversion keeps every word, in order. It never rewrites, reorders or
summarises: a CV is the one document here that must reach a reader exactly as its
author wrote it, so anything ambiguous is left as text rather than guessed at.

Macro arguments are read with a brace COUNTER rather than a regex, and that is not
an optimisation. `\resumeItem{Partnered with \textbf{stakeholders} to identify...}`
contains a nested group, so `\{(.*?)\}` stops at the first closing brace, eats
"Partnered with", and silently deletes the bullet. An earlier version of this file
did exactly that and lost a line from every role that had bold text in its first
bullet.
"""
import difflib
import re


def _braced(text, start):
    """Return (content, end_index) for the `{...}` group starting at `start`.

    The opening brace must be adjacent apart from whitespace. Searching for the
    next `{` anywhere ahead would let `\resumeItemListStart` swallow the brace of
    the `\resumeItem` that follows it, which is how a whole bullet disappears.

    Handles `\{` and `\}` so a literal brace does not throw off the count.
    """
    i = start
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text) or text[i] != "{":
        return None, len(text)
    depth, j = 0, i
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return text[i + 1:], len(text)


def _expand(text, name, render, nargs=1):
    """Rewrite every `\name{...}` using a brace counter. Longest name wins.

    `nargs` is the number of consecutive braced groups the macro takes.
    `\resumeSubheading` takes FOUR (role, dates, team, location), and consuming
    only the first left the other three as raw braces in the output - which is
    why roles came out glued to their dates: "Data ScientistJun.2025 -- Jul.2026".
    """
    token = "\\" + name
    out, i = "", 0
    while True:
        idx = text.find(token, i)
        if idx < 0:
            out += text[i:]
            return out
        after = idx + len(token)
        # A longer macro may share this prefix; leave it for its own pass.
        if after < len(text) and (text[after].isalpha() or text[after] == "*"):
            out += text[i:idx] + token
            i = after
            continue
        if nargs <= 1 and (name.endswith("Start") or name.endswith("End")):
            # The *ListStart/End macros take no argument at all.
            if _braced(text, after)[0] is None:
                out += text[i:idx]
                i = after
                continue
        args, pos = [], after
        for _ in range(nargs):
            content, end = _braced(text, pos)
            if content is None:
                break
            args.append(content)
            pos = end
        if len(args) < nargs:
            out += text[i:idx] + token
            i = after
            continue
        out += text[i:idx] + render(*args)
        i = pos


def _strip_list(name, content):
    """The `*ListStart` / `*ListEnd` macros take no argument at all."""
    return ""


def _clean_inline(text):
    """Remove the inline formatting commands, keeping the visible words.

    Loops because they nest: `\textsc{\Huge Badre}`.
    """
    for _ in range(8):
        before = text
        text = re.sub(r"\\(?:href|url)\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", text)
        text = re.sub(r"\\(?:textbf|textit|emph|underline|textsc|texttt|mbox|textrm|"
                      r"mathrm|small|large|Large|Huge|huge|scshape|bfseries|itshape)"
                      r"\s*\{([^{}]*)\}", r"\1", text)
        text = re.sub(r"\\(?:Huge|huge|Large|large|small|scshape|bfseries|itshape)\b", "", text)
        if text == before:
            break
    return text


def _subheading(m):
    """Four braced arguments: role, dates, team, location -> one readable line."""
    role, dates, team, place = (" ".join(g.split()) for g in m.groups())
    bits = [role]
    if team and team != "{}":
        bits.append(team)
    if place and place != "{}":
        bits.append(place)
    line = " - ".join(b for b in bits if b)
    return "%s%s\n" % (line, ("  [%s]" % dates) if dates else "")


def latex_to_text(src):
    """Best-effort plain text from a LaTeX resume. Never raises."""
    if not src:
        return ""
    text = src

    # Comments first: the resume template ships a commented-out example header and
    # several commented-out \usepackage lines, and later patterns would match them.
    text = re.sub(r"(?<!\\)%[^\n]*", "", text)
    # Everything before \begin{document} is preamble, macros and layout.
    head = text.find(r"\begin{document}")
    if head >= 0:
        text = text[head + len(r"\begin{document}"):]

    text = re.sub(r"\\section\*?\s*\{([^{}]*)\}",
                  lambda m: "\n\n%s\n%s\n" % (m.group(1).strip(),
                                             "-" * max(len(m.group(1).strip()), 3)), text)
    for macro in ("resumeSubItemListStart", "resumeItemListStart", "resumeItemListEnd",
                  "resumeSubHeadingListStart", "resumeSubHeadingListEnd"):
        text = _expand(text, macro, _strip_list)
    text = _expand(text, "resumeSubItem", lambda c: "\n- %s\n" % " ".join(c.split()))
    text = _expand(text, "resumeItem", lambda c: "\n- %s\n" % " ".join(c.split()))
    # Marked rather than parsed here: the arguments still contain the inline
    # markup, which is stripped below.
    text = _expand(text, "resumeSubheading",
                   lambda *a: "\x01" + "\x02\x01".join(
                       x.replace("\x01", "").strip() for x in a) + "\x02",
                   nargs=4)

    # `\begin{tabularx}{...}` carries a column specification in braces which is pure
    # layout; it has to be consumed with the brace counter, since it is nested.
    # These environments take brace arguments that are pure layout: the tabular
    # width and column spec, and adjustwidth's two indents. Consume them with the
    # brace counter (they nest), and take up to three groups because tabularx
    # has both \textwidth and the column specification.
    for _env, _maxargs in (("tabularx", 3), ("tabular", 3), ("adjustwidth", 3)):
        while True:
            idx = text.find("\\begin{%s}" % _env)
            if idx < 0:
                break
            pos = idx + len("\\begin{%s}" % _env)
            moved = False
            for _ in range(_maxargs):
                content, end = _braced(text, pos)
                if content is None:
                    break
                pos = end
                moved = True
            text = text[:idx] + text[pos:] if moved else text[:idx]
    text = re.sub(r"\\(?:begin|end)\s*\{(?:tabular\*?|tabularx|itemize|center|"
                  r"adjustwidth|minipage|figure|document)\}\s*(?:\[[^\]]*\])?\s*", "", text)
    text = re.sub(r"\\(?:usepackage|documentclass|newcommand|renewcommand|labelitem\w*|"
                  r"titleformat|pagestyle|fancyhead\w*|setlength|addtolength|ragged\w*|"
                  r"urlstyle|pdfgentounicode|input)\b[^\n]*", "", text)
    text = re.sub(r"\\(?:new|clear)page|\\hrulefill|\\vfill", "", text)
    text = re.sub(r"\\item(?:\[[^\]]*\])?", "", text)

    text = _clean_inline(text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", text)   # leftovers

    # The four subheading arguments are marked, but LaTeX puts newlines and
    # indentation between them; squash that so they are adjacent.
    text = re.sub(r"\x02\s*\x01", "\x02\x01", text)
    text = re.sub(r"\x01(.*?)\x02\x01(.*?)\x02\x01(.*?)\x02\x01(.*?)\x02",
                  _subheading, text, flags=re.S)
    text = text.replace("\x01", "").replace("\x02", "")

    text = re.sub(r"\\([%&_#$])", r"\1", text)
    # LaTeX spacing and separator idioms that are not markup: `$|$` is how the
    # template writes a pipe between two contact links, `\ ` is an explicit
    # space, and `\&` is an escaped ampersand.
    text = text.replace("$|$", "|").replace("\\ ", " ")
    # `\\` ends a row inside a tabular, and the skills table is the only tabular
    # left holding content, so a row break has to become a real newline. The `&`
    # cell separator is left alone: it is readable as-is, and replacing it breaks
    # real ampersands in headings ("Quantitative Finance & Data Science").
    text = text.replace("\\\\", "\n")
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
}


def _escape_latex(text):
    """Plain-text characters that would otherwise be TeX commands."""
    return "".join(_LATEX_SPECIALS.get(ch, ch) for ch in (text or ""))


def _forms(phrase):
    """The phrase as typed, and as the source spells it. Ampersands differ.

    The words pane shows `&` where the source writes `\\&`, `100%` where it
    writes `100\\%`. Searching for the typed form alone therefore fails on any
    sentence containing one, and the anchor silently degrades to a shorter -
    or more ambiguous - phrase.
    """
    escaped = _escape_latex(phrase)
    return (phrase,) if escaped == phrase else (phrase, escaped)


def _find_unique(src, phrase):
    """(start, length) of the only occurrence of `phrase` in `src`, else None.

    Uniqueness is the whole safety property here. A CV repeats itself - "Power
    BI" twice, "Experience" twice, a job title in a summary and in a heading -
    and patching the first match would edit a sentence the user never touched,
    then compile it and call it success. Nothing is placed without this.
    """
    if not phrase.strip():
        return None
    for form in _forms(phrase):
        if src.count(form) == 1:
            return src.index(form), len(form)
    return None


def _seam(left, right):
    """One space between two pieces of a sentence, unless one supplies it.

    The diff decides which side of the junction a space belongs to, so the side
    the user did not type may already carry it. Adding a second is harmless -
    both TeX and latex_to_text collapse runs - but a lone space is what keeps
    "quant2" from becoming one token when the addition is a bare word.
    """
    if left and right and not left[-1:].isspace() and not right[:1].isspace():
        return " "
    return ""


_MAX_CONTEXT_WORDS = 8


def _place(src, text, i1, i2, addition):
    """Put `addition` where `text[i1:i2]` sits in `src`, or leave src alone.

    The changed words are rarely unique on their own, so the window around them
    grows outwards one word at a time - closest neighbour first - until it
    matches in exactly one place. The window is replaced wholesale, which is why
    a deleted sentence and an added one are the same operation.

    Returns src unchanged when no window is unique, and that is the honest
    outcome: the caller tells the user the .tex did not move, which they can fix
    on the source tab, rather than a wrong line being silently rewritten.
    """
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    if not spans:
        return src
    n = len(spans)
    lo = 0
    while lo < n and spans[lo][1] <= i1:
        lo += 1
    hi = lo
    while hi + 1 < n and spans[hi + 1][0] < i2:
        hi += 1
    # An insertion at the very end has no word after it; anchor on the last one.
    lo, hi = min(lo, n - 1), min(hi, n - 1)
    if hi < lo:
        hi = lo
    base = hi - lo + 1
    for extra in range(min(_MAX_CONTEXT_WORDS, n - base) + 1):
        width = base + extra
        for left in range(lo, max(-1, lo - extra) - 1, -1):
            right = left + width - 1
            if right > n - 1 or right < hi:
                continue
            a, b = spans[left][0], spans[right][1]
            found = _find_unique(src, text[a:b])
            if not found:
                continue
            at, length = found
            new_win = (text[a:i1] + _seam(text[a:i1], addition) + addition
                       + _seam(addition, text[i2:b]) + text[i2:b])
            return (src[:at] + _seam(src[:at], new_win) + new_win
                    + src[at + length:])
    return src


_WORD = re.compile(r"\S+")


def _word_edits(old_text, new_text):
    """(start, end, replacement) per changed run of words.

    The diff runs over words, not characters. A character diff of "Mhiouah" ->
    "Benali" reports an insert and a delete that share no anchor, and each has
    to be placed on its own; over words it is one edit with a whole word around
    it, which is the granularity the words pane was typed at anyway.
    """
    old_spans = [(m.start(), m.end()) for m in _WORD.finditer(old_text)]
    new_words = [m.group(0) for m in _WORD.finditer(new_text)]
    old_words = [old_text[a:b] for a, b in old_spans]
    out = []
    for tag, i, j, k, l in difflib.SequenceMatcher(
            None, old_words, new_words, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        start = old_spans[i][0] if i < len(old_spans) else len(old_text)
        end = old_spans[j - 1][1] if j > i else start
        out.append((start, end, " ".join(new_words[k:l])))
    return out


def apply_text_edits(latex, old_text, new_text):
    """Write word-level edits back into the LaTeX they came from.

    The words pane is a rendering of the source. Saving it has to update the
    `.tex` (and then the PDF) or the preview keeps showing the previous run.

    Edits are applied right to left, so each one is located in the source as it
    stands before any later edit has moved the text around, and each is placed
    only where its context is unique - see `_place`. An edit that cannot be
    placed with certainty is skipped rather than guessed into the wrong macro.
    """
    if not latex or (old_text or "") == (new_text or ""):
        return latex or ""
    out = latex
    for start, end, replacement in reversed(_word_edits(old_text or "", new_text or "")):
        addition = _escape_latex(replacement) if replacement.strip() else ""
        out = _place(out, old_text or "", start, end, addition)
    return out


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "Badre_Mhiouah_CV.tex"
    print(latex_to_text(open(path, encoding="utf-8", errors="replace").read()))
