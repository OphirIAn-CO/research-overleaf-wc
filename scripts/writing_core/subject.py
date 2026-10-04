"""What a request is about, as opposed to how it asks for it.

A drafting request says two kinds of things: what to write about (the outline's points, the paper's abstract, the literature) and how to write it (follow the book's rules, good
grammar, cohesion, cite in this format, use the outline file). Only the first kind may steer a search of the literature library or of the internet. The writing book is a source of
style rules and nothing else, so it is never a subject, and a document that is a guide to writing is never a reference for a paper about something else.

This module has no model calls and no network. It is used by the draft (library search), by the search for references online, and by the author outline (which sections of it a
request asks for).
"""
import json
import re
from pathlib import Path

from . import store

# ------------------------------------------------------------------------------------------------ guides to writing: style rules, never a subject

# a title that is about writing itself
WRITING_GUIDE = re.compile(
    r"\b(?:academic|scientific|scholarly|technical|research|thesis|dissertation|english|professional)\s+(?:writing|prose|style|english)\b"
    r"|\bwriting\s+(?:guide|guidelines?|manual|handbook|style|skills?|tips|well|clearly|rules|a\s+(?:paper|thesis|dissertation|journal\s+article|research\s+paper))\b"
    r"|\b(?:style\s+(?:guide|manual)|manual\s+of\s+style|elements\s+of\s+style|how\s+to\s+write|writing\s+for\s+(?:publication|science|scientists|researchers))\b"
    r"|\bgrammar\s+(?:and|&)\s+(?:punctuation|style|usage|spelling)\b|\b(?:english|writing)\s+grammar\b|\bgrammar\s+(?:guide|handbook|rules|book)\b|\bspelling\s+and\s+punctuation\b"
    r"|\b(?:structure|structuring)\s+(?:of\s+|in\s+)?(?:an?\s+)?(?:introduction|abstract|research\s+papers?|scientific\s+papers?|journal\s+articles?|academic\s+papers?)\b"
    r"|\b(?:introduction|abstract|conclusion|discussion|literature\s+review|methods?)\s+(?:section\s+)?(?:structure|writing|guidelines?|format)\b"
    r"|\b(?:research|scientific|academic|journal)\s+(?:papers?|articles?)\s+(?:structure|writing|format|organi[sz]ation)\b|\bhow\s+to\s+(?:structure|organi[sz]e)\b", re.I)


def is_writing_guide(*texts):
    """True when a title (or file name) is about how to write rather than about a subject: such a document gives the writing rules and is not cited."""
    return any(WRITING_GUIDE.search(re.sub(r"[_\-.]+", " ", str(t or ""))) for t in texts)


def book_files():
    """The file names of the writing books the author imported, lower case: a copy of one in the literature library is the same book, and it stays a style guide."""
    from . import config
    out = set()
    try:
        for b in config.list_books():
            m = json.loads(Path(config.book_dir(b) / "manifest.json").read_text(encoding="utf-8"))
            if m.get("source_name"):
                out.add(str(m["source_name"]).lower())
    except (OSError, ValueError, KeyError, AttributeError):                 # a missing or damaged book must not stop a search
        pass
    return out


def is_style_source(title="", rel="", books=None):
    """A library document that is a guide to writing, or a copy of the imported writing book."""
    name = str(rel or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return is_writing_guide(title, name) or (bool(name) and name in (books if books is not None else book_files()))


# ------------------------------------------------------------------------------------------------ words about the task

# words that describe the task or the way of writing; never what the paper is about
META_RAW = """book books rule rules style styles stylistic grammar grammatical grammatically orthography orthographic spelling punctuation cohesion cohesive cohesiveness coherence coherent
flow flows fluent fluency readable readability tone wording phrasing prose academic writing write writes written rewrite rewriting draft drafts drafting outline outlines outlined brief prompt
prompts follow follows following followed section sections paragraph paragraphs intro introduction introductory part parts folder folders file files markdown cite cited citing citation citations
cites reference references referenced referencing bibliography bib bibtex in-text intext library libraries literature review reviews rag source sources based using use used complete completely
properly correctly proper correct paper papers text manuscript article articles right wrong get gets getting make makes made keep keeps check checks checking good better best well guidelines guideline guide guides instructions instruction format formatting structure structured logic logical order ordered
funnel keep stay make sure ensure please need want would like should must shall also each every any all the""".split()
META = frozenset(META_RAW)

# a clause that only says how to write, so it is not read for its subject
HOW_CLAUSE = re.compile(r"[^.;:\n]*\b(?:rules?|style|grammar|grammatical|orthography|spelling|cohesi\w+|coheren\w+|flow|tone|wording|cit(?:e|es|ed|ing|ation|ations)|in-?text|bibliograph\w+|bibtex)\b[^.;:\n]*[.;:]?", re.I)


# "the book Academic Writing for Graduate Students", 'the guide "Writing Science"': the title of a book is not a subject
BOOK_TITLE = re.compile(r"\b(?:book|textbook|guide|manual|handbook|reference\s+work)\s*(?:called|titled|named|entitled|:|,)?\s*(?:[\"“‘'][^\"”’']{2,120}[\"”’']|(?:[A-Z][\w'-]*(?:\s+(?:of|for|and|the|to|in|on|a)\b)?\s*){2,8})")


def drop_how(text):
    """The text without the sentences that only say how to write, and without the title of a book it names. A sentence that also names a subject is kept whole (it is the subject
    that counts, and the stoplist removes the rest)."""
    text = WRITING_GUIDE.sub(" ", BOOK_TITLE.sub(" the book ", text or ""))
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = sentence.strip()
        if not s:
            continue
        words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]+", s)
        rest = [w for w in words if w.lower() not in META and len(w) > 3]
        if HOW_CLAUSE.fullmatch(s) is not None and not rest:
            continue
        out.append(s)
    return " ".join(out)


# ------------------------------------------------------------------------------------------------ which sections of an outline were asked for

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "first": 1, "second": 2, "third": 3, "fourth": 4}
_NOUN = r"(?:sections?|parts?|points?|items?|steps?|paragraphs?|blocks?|topics?|numbers?|nos?\.?)"
_NUM = r"(\d{1,2})"
_RANGE = re.compile(rf"\b{_NOUN}\s*{_NUM}\s*(?:-|–|—|to|through|thru|until|and\s+up\s+to)\s*{_NUM}\b", re.I)
_LIST = re.compile(rf"\b{_NOUN}\s*(\d{{1,2}}(?:\s*(?:,|;|&|and|or)\s*\d{{1,2}})+)\b", re.I)
_ONE = re.compile(rf"\b{_NOUN}\s*{_NUM}\b(?!\s*(?:-|–|—|to|through)\s*\d)", re.I)
_FIRST = re.compile(rf"\b(?:the\s+)?first\s+(\d{{1,2}}|{'|'.join(k for k in NUMBER_WORDS if k not in ('first', 'second', 'third', 'fourth'))})\s+{_NOUN}\b", re.I)
_NAMED_FILE = re.compile(r"\b[\w.-]+\.(?:md|txt|docx|pdf|tex)\b", re.I)


def requested_sections(instruction):
    """The numbers of the outline's sections a request asks for ("sections 1-4", "points 2 and 3", "the first three sections"), or None when it names none (the whole outline is meant)."""
    text = _NAMED_FILE.sub(" ", instruction or "")
    got = set()
    for m in _RANGE.finditer(text):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        got.update(range(a, min(b, a + 30) + 1))
    for m in _LIST.finditer(text):
        got.update(int(x) for x in re.findall(r"\d+", m.group(1)))
    for m in _ONE.finditer(text):
        got.add(int(m.group(1)))
    for m in _FIRST.finditer(text):
        w = m.group(1).lower()
        n = int(w) if w.isdigit() else NUMBER_WORDS.get(w, 0)
        got.update(range(1, n + 1))
    return got or None


# a numbered heading of an outline: "1. Context", "## 2 Methods", "Section 3: ...", "**4. ...**"
_NUMBERED = re.compile(r"^\s*(?P<mark>#{1,6}\s*|\*\*\s*|__\s*)?(?P<word>(?:section|part|point|step|paragraph|block)\s+)?(?P<n>\d{1,2})\s*(?P<d>[.):\-–—]|\s)\s*(?=\S)", re.I)


def split_outline(text):
    """An outline as [(number or None, block text)]: the text before the first numbered heading has number None (general guidance, kept for every request).
    A heading is a short line that starts with a number. Where some headings are marked (#, bold, the word "Section"), only those count, and a plain numbered line is then a bullet inside
    a section. Where none is marked, a number counts as a new section only when it is higher than the one before (a list that starts again at 1 is a bullet list)."""
    lines = (text or "").splitlines()
    cands = []
    for i, line in enumerate(lines):
        m = _NUMBERED.match(line)
        if m and len(line) <= 160 and not re.match(r"^\s*\d{4}\b", line):
            cands.append((i, int(m.group("n")), bool(m.group("mark") or m.group("word")), m.group("d").strip()))
    if any(c[2] for c in cands):
        cands = [c for c in cands if c[2]]

    def chain(group):
        got, last = {}, 0
        for i, n, _, _ in group:
            if n > last:
                got[i], last = n, n
        return got
    heads = max((chain([c for c in cands if c[3] == d]) for d in {c[3] for c in cands}), key=len, default={})
    blocks, cur_n, cur = [], None, []
    for i, line in enumerate(lines):
        if i in heads:
            if cur:
                blocks.append((cur_n, "\n".join(cur).strip()))
            cur_n, cur = heads[i], [line]
        else:
            cur.append(line)
    if cur:
        blocks.append((cur_n, "\n".join(cur).strip()))
    return [(n, t) for n, t in blocks if t]


def pick_sections(text, wanted):
    """-> (text of the sections asked for plus the general guidance, [numbers kept], [numbers in the outline]). When the outline has no numbered sections, or none of the numbers asked for
    exists in it, the whole outline comes back and `kept` is empty (so the caller can say it could not narrow it down)."""
    blocks = split_outline(text)
    present = [n for n, _ in blocks if n is not None]
    if not wanted or not present:
        return text, [], present
    kept = [n for n in present if n in wanted]
    if not kept:
        return text, [], present
    chosen = [t for n, t in blocks if n is None or n in wanted]
    return "\n\n".join(chosen), kept, present


def trim_to(text, budget):
    """The text cut at a paragraph boundary to fit about `budget` tokens."""
    if store.approx_tokens(text) <= budget:
        return text
    out, used = [], 0
    for para in re.split(r"\n\s*\n|\n(?=\s*(?:#{1,6}\s|\d{1,2}[.)]\s))", text):
        t = store.approx_tokens(para)
        if used + t > budget and out:
            break
        out.append(para.strip())
        used += t
    return "\n\n".join(x for x in out if x)


# ------------------------------------------------------------------------------------------------ the subject

def _flatten(text):
    """Lines of a text as one line, keeping the places where a sentence ends: after a line that ends a sentence, before a heading or a list item, and after a short line without a full stop
    (a heading). A hard-wrapped paragraph is joined with spaces."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    out = []
    for i, ln in enumerate(lines):
        if not ln:
            if out and not out[-1].endswith((".", "!", "?", ":", ";")):
                out[-1] += "."
            continue
        if out and not out[-1].endswith((".", "!", "?", ":", ";")):
            heading_next = bool(re.match(r"(?:#{1,6}\s|[-*•]\s|\d{1,2}[.)]\s)", ln))
            if heading_next or len(out[-1]) < 70:
                out[-1] += "."
        out.append(ln)
    return " ".join(out)


def plain(text):
    """Markdown and LaTeX marks out of a text, to read its words; the [file · place] header of an outline is dropped."""
    t = re.sub(r"(?m)^\[[^\]\n]*\]\s*$", "", text or "")
    t = re.sub(r"\\(?:cite[a-z]*|ref|label|eqref)\*?(?:\[[^\]]*\])*\{[^}]*\}", " ", t)
    t = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"[#*_`>|]+", " ", _flatten(t))
    return re.sub(r"\s+", " ", t).strip()


def of(*, outline="", abstract="", card="", request="", limit=2400):
    """The text that says what the writing is about, in order of trust: the outline sections asked for, the paper's abstract, its card, and last the request itself with the sentences
    about how to write taken out. The search words are made from this text, never from the request alone."""
    parts = [plain(outline)[:limit], plain(abstract)[:900], plain(card)[:600], drop_how(plain(request))[:900]]
    return " ".join(p for p in parts if p).strip()
