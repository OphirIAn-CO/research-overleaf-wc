"""Book PDF -> outline -> named, ordered chunks. Pure functions plus the poppler calls. Stdlib only.

Pipeline
  extract_pages()        pdftotext, one string per PDF page (form-feed split) so page numbers stay exact
  clean_pages()          drop running headers/footers and bare page numbers, undo ligatures
  analyze_outline()      table of contents -> verified against the pages; else font-size headings; else fixed windows
  build_chunks()         outline + pages -> chunks inside sections only, named in reading order

Chunk id:  B:<book>:c07.s02.k03   (chapter 7, section 2, chunk 3; s00 = text before a chapter's first section)
"""
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter

from . import store, taxonomy

TARGET_TOKENS, MAX_TOKENS, MIN_TOKENS, DROP_BELOW_TOKENS = 550, 700, 120, 25
LOGIC_VERSION = 2   # 2: passages are also tagged by what they are about, and exercise questions are set aside
SKIP_TITLES = {"index", "subject index", "author index", "name index", "bibliography", "colophon"}
NOT_HEADINGS = {"contents", "table of contents", "list of figures", "list of tables"}


class ToolMissing(RuntimeError):
    pass


# --------------------------------------------------------------------------- extraction

def _poppler(tool):
    if not shutil.which(tool):
        raise ToolMissing(f"{tool} not found. Install poppler (macOS: brew install poppler; the Docker image has it).")
    return tool


def extract_pages(pdf, layout=False, first=None, last=None):
    cmd = [_poppler("pdftotext"), "-enc", "UTF-8"]
    if layout:
        cmd.append("-layout")
    if first:
        cmd += ["-f", str(first)]
    if last:
        cmd += ["-l", str(last)]
    r = subprocess.run(cmd + [str(pdf), "-"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {r.stderr.strip()[:300]}")
    pages = r.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def page_count(pdf):
    r = subprocess.run([_poppler("pdfinfo"), str(pdf)], capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)", r.stdout, re.M)
    if not m:
        raise RuntimeError("could not read the page count (is this a PDF?)")
    return int(m.group(1))


# --------------------------------------------------------------------------- cleaning

_ROMAN = re.compile(r"^[ivxlcdm]{1,7}$", re.I)


def _shape(line):
    """Normalized form used to recognise repeating headers/footers: digits collapse, case/spacing ignored."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", line.strip().lower()))


def _fix_text(s):
    s = unicodedata.normalize("NFKC", s)  # ligatures (fi, fl) and compatibility forms
    s = s.replace("­", "")  # soft hyphen
    return s


def clean_pages(pages, edge=3, repeat_ratio=0.3):
    """-> list of pages, each a list of non-header/footer lines (blank lines kept as paragraph hints).
    A line is a running header/footer if its shape (digits collapsed) repeats on enough pages IN THE SAME EDGE ZONE
    (top or bottom); bare page numbers (arabic or roman) at an edge are dropped too."""
    split = [[_fix_text(ln).rstrip() for ln in p.splitlines()] for p in pages]
    top, bottom = Counter(), Counter()
    for lines in split:
        content = [ln for ln in lines if ln.strip()]
        top.update(set(_shape(x) for x in content[:edge]))
        bottom.update(set(_shape(x) for x in content[-edge:]))
    threshold = max(3, int(len(pages) * repeat_ratio))
    rep_top = {s for s, n in top.items() if n >= threshold and len(s) < 120}
    rep_bottom = {s for s, n in bottom.items() if n >= threshold and len(s) < 120}
    out = []
    for lines in split:
        idx = [i for i, ln in enumerate(lines) if ln.strip()]
        drop = set()
        for pos, i in enumerate(idx):
            t = lines[i].strip()
            in_top, in_bottom = pos < edge, pos >= len(idx) - edge
            if not (in_top or in_bottom):
                continue
            repeating = (in_top and _shape(t) in rep_top) or (in_bottom and _shape(t) in rep_bottom)
            if repeating or re.fullmatch(r"\d{1,4}", t) or _ROMAN.match(t):
                drop.add(i)
        out.append([ln for i, ln in enumerate(lines) if i not in drop])
    return out


# --------------------------------------------------------------------------- headings

def norm(s):
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def strip_number(s):
    """'1 1 opening with the problem' -> 'opening with the problem' (normalized text in, normalized out)."""
    return re.sub(r"^(chapter\s+)?((\d+|[ivx]+)\s+)+", "", s).strip()


def _title_match(line_norm, title_norm):
    if not line_norm or not title_norm:
        return False
    line_norm = strip_number(line_norm) or line_norm
    if line_norm == title_norm:
        return True
    # a heading that wraps onto a second line: the first line is a prefix of the title
    return len(line_norm) >= 0.6 * len(title_norm) and title_norm.startswith(line_norm)


def find_heading(title, lines, start=0):
    """Index of the line that is this heading, or None. Wrapped headings match on their first line."""
    t = strip_number(norm(title)) or norm(title)
    for i in range(start, len(lines)):
        ln = lines[i].strip()
        if not ln or len(ln) > len(title) + 14:
            continue
        if _title_match(norm(ln), t):
            return i
        if i + 1 < len(lines) and lines[i + 1].strip():
            if strip_number(norm(ln + " " + lines[i + 1])) == t:
                return i
    return None


_NUM = r"(?:\d+(?:\.\d+)*\.?|[A-Z]\.|[IVX]+\.)"
_TOC_DOTS = re.compile(rf"^\s*(?:(?:chapter|part)\s+)?(?P<num>{_NUM})?\s*(?P<title>\S.*?)\s*(?:\.\s?){{3,}}\s*(?P<page>\d{{1,4}}|[ivxlcdm]{{1,7}})\s*$", re.I)
_TOC_GAP = re.compile(rf"^(?P<ind>\s*)(?:(?:chapter|part)\s+)?(?P<num>{_NUM})\s+(?P<title>\S.*?\S)\s{{3,}}(?P<page>\d{{1,4}}|[ivxlcdm]{{1,7}})\s*$", re.I)


def parse_toc(layout_pages, scan_pages=60, min_per_page=5):
    """Find the printed table of contents -> (entries, last_toc_page_index). Entries: level, number, title, page."""
    n = min(len(layout_pages), scan_pages)
    rows_by_page = {}
    for i in range(n):
        rows = []
        for ln in layout_pages[i].splitlines():
            m = _TOC_DOTS.match(ln) or _TOC_GAP.match(ln)
            if m:
                rows.append((ln, m))
        if len(rows) >= min_per_page:
            rows_by_page[i] = rows
    if not rows_by_page:
        return [], -1
    first = min(rows_by_page)
    pages, cur = [], first
    while cur in rows_by_page or cur + 1 in rows_by_page:  # tolerate one sparse page inside the contents
        if cur in rows_by_page:
            pages.append(cur)
        cur += 1
    entries = []
    for i in pages:
        indents = [len(ln) - len(ln.lstrip()) for ln, _ in rows_by_page[i]]
        base = min(indents)
        for ln, m in rows_by_page[i]:
            page = m.group("page")
            if not page.isdigit():  # roman numerals: front matter (preface, ...)
                continue
            num = (m.group("num") or "").rstrip(".")
            title = re.sub(r"\s+", " ", m.group("title")).strip()
            if num:
                level = num.count(".") + 1
            else:
                level = 1 if (len(ln) - len(ln.lstrip())) <= base + 1 else 2
            entries.append({"level": min(level, 4), "number": num, "title": title, "page": int(page)})
    return entries, pages[-1]


def detect_headings_xml(pdf, first=None, last=None, max_len=100):
    """Fallback: headings by font size (pdftohtml -xml). Level 1 = largest heading size, then 2, 3."""
    cmd = [_poppler("pdftohtml"), "-xml", "-i", "-q", "-stdout"]
    if first:
        cmd += ["-f", str(first)]
    if last:
        cmd += ["-l", str(last)]
    r = subprocess.run(cmd + [str(pdf)], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return []
    root = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", r.stdout))
    sizes = {}
    for fs in root.iter("fontspec"):
        sizes[fs.get("id")] = int(fs.get("size", "0"))
    chars = Counter()
    rows = []  # (page, top, left, size, text)
    for page in root.iter("page"):
        pno = int(page.get("number"))
        for t in page.iter("text"):
            txt = "".join(t.itertext()).strip()
            sz = sizes.get(t.get("font"), 0)
            if txt:
                chars[sz] += len(txt)
                rows.append((pno, int(t.get("top")), int(t.get("left")), sz, txt))
    if not chars:
        return []
    body = chars.most_common(1)[0][0]
    cand = [r for r in rows if r[3] >= body * 1.15]
    cand.sort(key=lambda r: (r[0], r[1], r[2]))
    merged = []  # join fragments on the same visual line, and consecutive lines of a wrapped heading
    for r in cand:
        if merged and merged[-1][0] == r[0] and merged[-1][3] == r[3] and abs(merged[-1][1] - r[1]) <= r[3] * 1.8:
            p = merged[-1]
            merged[-1] = (p[0], r[1], p[2], p[3], p[4] + " " + r[4])
        else:
            merged.append(r)
    repeated = Counter(norm(m[4]) for m in merged)
    heads = [m for m in merged if len(m[4]) <= max_len and not re.fullmatch(r"[\d\W]+", m[4]) and repeated[norm(m[4])] < 3]
    heads = [h for h in heads if norm(h[4]) not in NOT_HEADINGS]
    by_size = Counter(h[3] for h in heads)
    if len(heads) >= 8:  # a size used only once or twice (title page, a banner) is not a heading level
        heads = [h for h in heads if by_size[h[3]] >= 3]
    levels = {s: i + 1 for i, s in enumerate(sorted({h[3] for h in heads}, reverse=True)[:3])}
    out = []
    for pno, _top, _left, sz, txt in heads:
        if sz not in levels:
            continue
        m = re.match(rf"^(?P<num>{_NUM})\s+(?P<title>.+)$", txt)
        out.append({"level": levels[sz], "number": (m.group("num").rstrip(".") if m else ""),
                    "title": (m.group("title") if m else txt).strip(), "pdf_page": pno})
    return out


def window_outline(n_pages, window=4):
    return [{"level": 1, "number": "", "title": f"Pages {a}-{min(a + window - 1, n_pages)}", "pdf_page": a}
            for a in range(1, n_pages + 1, window)]


def _is_skip(title):
    return norm(title) in SKIP_TITLES


def analyze_outline(pdf, n_pages=None, window=4):
    """-> outline dict {method, page_offset, n_pdf_pages, warnings, entries[...]} (also written to outline.json by the caller)."""
    n_pages = n_pages or page_count(pdf)
    warnings = []
    pages = clean_pages(extract_pages(pdf))
    layout = extract_pages(pdf, layout=True, first=1, last=min(60, n_pages))
    toc, toc_end = parse_toc(layout)
    outline = None
    if len(toc) >= 4:
        outline = _outline_from_toc(toc, toc_end, pages, warnings)
    if outline is None:
        heads = detect_headings_xml(pdf)
        if len(heads) >= 5:
            warnings.append("no usable table of contents; headings detected by font size, please review outline.json")
            outline = {"method": "font", "page_offset": 0, "entries": [dict(h, page=h["pdf_page"], verified=True) for h in heads]}
    if outline is None:
        warnings.append("no table of contents and no distinguishable headings; using fixed page windows "
                        "(retrieval will be weaker: edit outline.json to add real chapters)")
        outline = {"method": "windows", "page_offset": 0,
                   "entries": [dict(e, page=e["pdf_page"], verified=True) for e in window_outline(n_pages, window)]}
    for e in outline["entries"]:
        e["skip"] = _is_skip(e["title"])
        e.setdefault("number", "")
    outline["n_pdf_pages"] = n_pages
    outline["warnings"] = warnings + outline.pop("warnings", [])
    return outline


def _outline_from_toc(toc, toc_end, pages, warnings):
    """Resolve printed page numbers to PDF pages by finding the headings on the pages themselves."""
    offsets = Counter()
    for e in toc[:12]:
        for p in range(toc_end + 1, len(pages)):
            if find_heading(e["title"], pages[p]) is not None:
                offsets[p + 1 - e["page"]] += 1
                break
    if not offsets:
        return None
    offset, votes = offsets.most_common(1)[0]
    if votes < max(2, min(3, len(toc[:12]) // 2)):
        return None
    entries, unverified = [], 0
    for e in toc:
        expect = e["page"] + offset  # 1-based PDF page
        found = None
        for p in sorted(range(max(1, expect - 2), min(len(pages), expect + 2) + 1), key=lambda x: abs(x - expect)):
            if find_heading(e["title"], pages[p - 1]) is not None:
                found = p
                break
        if found is None:
            unverified += 1
            found = min(max(expect, 1), len(pages))
        entries.append({"level": e["level"], "number": e["number"], "title": e["title"], "pdf_page": found,
                        "page": e["page"], "verified": found is not None and find_heading(e["title"], pages[found - 1]) is not None})
    if unverified > 0.3 * len(toc):
        warnings.append(f"{unverified} of {len(toc)} contents entries could not be found on their pages")
        if unverified > 0.6 * len(toc):
            return None
    elif unverified:
        warnings.append(f"{unverified} contents entries not located exactly; they start at the top of their page")
    entries.sort(key=lambda e: e["pdf_page"])  # stable: equal pages keep contents order
    return {"method": "toc", "page_offset": offset, "entries": entries, "warnings": warnings}


# --------------------------------------------------------------------------- paragraphs and chunks

_END = re.compile(r"[.!?:;][\"')\]”’]*$")


def to_paragraphs(stream):
    """stream: [(pdf_page, line)] -> [{'text','p0','p1'}]. A paragraph ends at a blank line, or at a sentence-final
    line that is clearly shorter than the typical line (pdftotext gives no blank line between paragraphs)."""
    lens = sorted(len(ln.strip()) for _, ln in stream if len(ln.strip()) > 30)
    full = lens[int(len(lens) * 0.7)] if lens else 80
    paras, cur = [], []

    def flush():
        if cur:
            text = ""
            for _, ln in cur:
                ln = ln.strip()
                if text.endswith("-") and ln[:1].islower() and not text.endswith(" -"):
                    text = text[:-1] + ln  # undo end-of-line hyphenation
                else:
                    text = (text + " " + ln).strip()
            paras.append({"text": re.sub(r"\s+", " ", text), "p0": cur[0][0], "p1": cur[-1][0]})
            cur.clear()

    for page, ln in stream:
        if not ln.strip():
            flush()
            continue
        cur.append((page, ln))
        if _END.search(ln.strip()) and len(ln.strip()) < 0.8 * full:
            flush()
    flush()
    return paras


def _split_long(p, limit):
    """A single over-long paragraph -> pieces of about `limit` tokens on sentence boundaries."""
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", p["text"])
    out, cur = [], []
    for s in sents:
        if cur and store.approx_tokens(" ".join(cur + [s])) > limit:
            out.append({"text": " ".join(cur), "p0": p["p0"], "p1": p["p1"]})
            cur = []
        cur.append(s)
    if cur:
        out.append({"text": " ".join(cur), "p0": p["p0"], "p1": p["p1"]})
    return out


def pack_chunks(paras, target=TARGET_TOKENS, maximum=MAX_TOKENS, minimum=MIN_TOKENS):
    pieces = []
    for p in paras:
        pieces += _split_long(p, target) if store.approx_tokens(p["text"]) > maximum else [p]
    chunks, cur = [], []

    def tok(items):
        return store.approx_tokens("\n\n".join(i["text"] for i in items))

    for p in pieces:
        if cur and tok(cur + [p]) > target and tok(cur) >= minimum:
            chunks.append(cur)
            cur = []
        cur.append(p)
    if cur:
        if chunks and tok(cur) < minimum and tok(chunks[-1] + cur) <= maximum:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    return [{"text": "\n\n".join(i["text"] for i in c), "p0": c[0]["p0"], "p1": c[-1]["p1"]} for c in chunks]


def _locate_entries(entries, pages):
    """(page_index0, line_index) for each entry, monotonic non-decreasing in reading order."""
    out, last = [], (0, 0)
    for e in entries:
        p0 = min(max(int(e["pdf_page"]), 1), len(pages)) - 1
        li = find_heading(e["title"], pages[p0])
        pos = (p0, li if li is not None else 0)
        if pos < last:
            pos = last
        out.append(pos)
        last = pos
    return out


_NUMBERED_Q = re.compile(r"(?:^|\s)\d{1,2}[.)]\s+[A-Z][^?]{8,}?\?")


def is_exercise(text):
    """A page of discussion questions or tasks ("3. Do you think...? 4. How is it organized?") is not advice: it must not be sent as a rule."""
    words = max(len(text.split()), 1)
    q = text.count("?")
    return len(_NUMBERED_Q.findall(text)) >= 3 or (q >= 4 and q * 100.0 / words >= 1.5)


def build_chunks(pages, outline, book_id, target=TARGET_TOKENS, maximum=MAX_TOKENS, minimum=MIN_TOKENS):
    """pages: clean_pages() output. outline: dict with entries. -> (chunks, stats). Reading order, section-bounded."""
    entries = sorted(outline["entries"], key=lambda e: e["pdf_page"])
    offset = int(outline.get("page_offset") or 0)
    locs = _locate_entries(entries, pages)
    chunks, seq = [], 0
    chapter = section = 0
    chapter_title = chapter_num = ""
    stack = {}  # level -> title, for heading paths
    dropped_front = sum(len([ln for ln in pg if ln.strip()]) for pg in pages[:locs[0][0]]) if locs else 0
    stats = {"sections": len(entries), "skipped_sections": 0, "tiny_sections": 0, "front_matter_lines": dropped_front}

    for i, e in enumerate(entries):
        lvl = int(e["level"])
        if lvl == 1 or chapter == 0:
            if lvl != 1:  # section before any chapter heading
                chapter_title, chapter_num = "Untitled", ""
                stack = {}
            chapter += 1
            section = 0
            if lvl == 1:
                chapter_title, chapter_num = e["title"], e.get("number", "")
                stack = {1: e["title"]}
        if lvl >= 2:
            section += 1
            stack = {k: v for k, v in stack.items() if k < lvl}
            stack[lvl] = e["title"]
        sec_title = e["title"] if lvl >= 2 else ""
        sec_num = e.get("number", "") if lvl >= 2 else ""
        start = locs[i]
        end = locs[i + 1] if i + 1 < len(locs) else (len(pages), 0)
        if e.get("skip"):
            stats["skipped_sections"] += 1
            continue
        stream = []
        for p in range(start[0], min(end[0] + 1, len(pages))):
            lines = pages[p]
            a = start[1] + 1 if p == start[0] else 0
            b = end[1] if p == end[0] else len(lines)
            stream += [(p + 1, ln) for ln in lines[a:b]]
        paras = to_paragraphs(stream)
        if store.approx_tokens(" ".join(p["text"] for p in paras)) < DROP_BELOW_TOKENS:
            stats["tiny_sections"] += 1
            continue
        path = [stack[k] for k in sorted(stack)]
        heading_path = " > ".join(path)
        tags = taxonomy.classify(sec_title or chapter_title)
        for t in taxonomy.classify(chapter_title):
            if t not in tags:
                tags.append(t)
        if not tags:  # no heading signal: let the text itself vote, at a high bar
            tags = [t for t in taxonomy.classify(" ".join(p["text"] for p in paras)[:1500], min_ratio=0.8)][:2]
        for k, piece in enumerate(pack_chunks(paras, target, maximum, minimum), 1):
            seq += 1
            exercise = is_exercise(piece["text"])
            piece_tags = [] if exercise else list(tags)
            for extra in ([] if exercise else taxonomy.content_tags(piece["text"])):
                if extra not in piece_tags:
                    piece_tags.append(extra)
            ps, pe = piece["p0"] - offset, piece["p1"] - offset
            if ps < 1:
                ps, pe = piece["p0"], piece["p1"]  # front-matter pages: fall back to PDF numbering
            pages_txt = f"pp.{ps}-{pe}" if pe != ps else f"p.{ps}"
            chap_lbl = f"ch{chapter:02d}" + (f" (book ch. {chapter_num})" if chapter_num and str(chapter_num) != str(chapter) else "")
            where = (f"§{sec_num or f'{chapter_num or chapter}.{section}'} " if sec_title else "") + f'"{sec_title or chapter_title}"'
            chunks.append({
                "id": f"B:{book_id}:c{chapter:02d}.s{section:02d}.k{k:02d}", "seq": seq,
                "chapter": chapter, "section": section, "k": k,
                "chapter_number": chapter_num, "section_number": sec_num,
                "chapter_title": chapter_title, "section_title": sec_title, "heading_path": heading_path,
                "pdf_start": piece["p0"], "pdf_end": piece["p1"], "page_start": ps, "page_end": pe,
                "label": f"{chap_lbl} {where} · {pages_txt}",
                "text": piece["text"], "tokens": store.approx_tokens(piece["text"]),
                "parts": piece_tags, "rule_card": "", "kind": "exercise" if exercise else "",
            })
    stats["chunks"] = len(chunks)
    return chunks, stats
