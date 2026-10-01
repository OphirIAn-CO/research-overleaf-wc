"""LaTeX manuscript -> chunks with exact file/line ranges. Pure functions, stdlib only.

* follows \\input / \\include / \\subfile (inside the paper folder only), skips the preamble, comments and bibliography;
* splits at \\section ... \\subparagraph, plus the abstract, title and keywords;
* never splits inside an environment; chunks are paragraph runs of about CHUNK_TOKENS inside one file;
* ids are stable under edits elsewhere: <file>#<section-path-slug>#k<n>, so a change touches only that section's chunks.
"""
import re
from pathlib import Path

from . import store, taxonomy

CHUNK_TOKENS, CHUNK_MAX = 350, 520
MIN_BODY_WORDS = 3
LEVELS = {"part": -1, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4, "subparagraph": 5}
_HEAD = re.compile(r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\s*(?:\[[^\]]*\])?\s*\{")
_INC = re.compile(r"^\s*\\(?:input|include|subfile)\s*\{([^}]+)\}")
_BEGIN, _END = re.compile(r"\\begin\{([A-Za-z*]+)\}"), re.compile(r"\\end\{([A-Za-z*]+)\}")
_STOP_BIB = re.compile(r"\\(?:bibliography|printbibliography|bibliographystyle)\b|\\begin\{thebibliography\}")
_NO_SPLIT_SKIP = {"document"}


def strip_comment(line):
    """Remove an unescaped % comment, keeping the line (and so the line numbering) intact."""
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            return line[:i]
        i += 1
    return line


def expand(root, main):
    """-> list of (relfile, lineno, raw, stripped) in reading order with \\input files spliced in. Also returns warnings."""
    root = Path(root).resolve()
    out, warnings = [], []

    def resolve(ref, base):
        ref = ref.strip()
        for cand in ([ref] if ref.endswith(".tex") else [ref + ".tex", ref]):
            for d in (base, root):
                p = (d / cand).resolve()
                if p.is_file() and (p == root or root in p.parents):
                    return p
        return None

    def walk(path, stack):
        rel = str(path.relative_to(root))
        if path in stack:
            warnings.append(f"include cycle at {rel}")
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError as e:
            warnings.append(f"cannot read {rel}: {e}")
            return
        in_comment_env = False
        for n, raw in enumerate(lines, 1):
            s = strip_comment(raw)
            if re.match(r"\s*\\begin\{comment\}", s):
                in_comment_env = True
            if in_comment_env:
                if re.match(r"\s*\\end\{comment\}", s):
                    in_comment_env = False
                continue
            m = _INC.match(s)
            if m:
                target = resolve(m.group(1), path.parent)
                if target:
                    walk(target, stack | {path})
                else:
                    warnings.append(f"{rel}:{n}: cannot find included file {m.group(1)!r}")
                continue
            out.append((rel, n, raw, s))

    walk(Path(main).resolve(), frozenset())
    return out, warnings


def find_main(root):
    root = Path(root)
    tex = sorted(p for p in root.glob("*.tex"))
    if (root / "main.tex").exists():
        return root / "main.tex"
    for p in tex:
        if "\\documentclass" in p.read_text(encoding="utf-8", errors="replace")[:4000]:
            return p
    return tex[0] if tex else None


def _group(text, start):
    """text[start] == '{' -> (content, index after the matching brace) or None."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return None


def detex(s):
    """Plain text for search: drop math, citations, commands; keep the words."""
    s = re.sub(r"\\begin\{(equation|align|eqnarray|gather|multline|displaymath)\*?\}.*?\\end\{\1\*?\}", " ", s, flags=re.S)
    s = re.sub(r"\$\$.*?\$\$|\$[^$\n]*\$|\\\(.*?\\\)|\\\[.*?\\\]", " ", s, flags=re.S)
    s = re.sub(r"\\(?:cite[a-z]*|ref|eqref|autoref|cref|Cref|label|url|href)\*?(?:\[[^\]]*\])*\{[^}]*\}", " ", s)
    s = re.sub(r"\\(?:begin|end)\{[^}]*\}", " ", s)
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    s = re.sub(r"[{}\\~&]|\\\\", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slug(s, n=60):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "section"


def _headings(lines):
    """Scan the stripped stream. -> list of dicts: line index of the heading, level, title, last line index it covers."""
    text = "\n".join(l[3] for l in lines)
    starts, pos = [], 0
    for i, l in enumerate(lines):
        starts.append(pos)
        pos += len(l[3]) + 1

    def line_of(off):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo

    found = []
    for m in _HEAD.finditer(text):
        g = _group(text, m.end() - 1)
        if not g:
            continue
        title = re.sub(r"\s+", " ", detex(g[0]) or g[0]).strip()
        found.append({"idx": line_of(m.start()), "level": LEVELS[m.group(1)], "title": title or m.group(1),
                      "last": line_of(g[1] - 1), "kind": "section"})
    for m in re.finditer(r"\\begin\{abstract\}", text):
        e = text.find("\\end{abstract}", m.end())
        if e > 0:
            found.append({"idx": line_of(m.start()), "level": 1, "title": "Abstract", "last": line_of(m.start()),
                          "kind": "abstract", "end_idx": line_of(e)})
    for m in re.finditer(r"\\title(?:\[[^\]]*\])?\s*\{", text):
        g = _group(text, m.end() - 1)
        if g:
            found.append({"idx": line_of(m.start()), "level": 1, "title": "Title", "last": line_of(g[1] - 1), "kind": "title",
                          "end_idx": line_of(g[1] - 1)})
    for m in re.finditer(r"\\keywords?\s*\{", text):
        g = _group(text, m.end() - 1)
        if g:
            found.append({"idx": line_of(m.start()), "level": 1, "title": "Keywords", "last": line_of(g[1] - 1), "kind": "keywords",
                          "end_idx": line_of(g[1] - 1)})
    found.sort(key=lambda h: (h["idx"], h["level"]))
    return found


def _body_range(lines):
    """Indices [a, b) of the document body: after \\begin{document} (title/abstract may precede in some templates, so
    those are found on the full stream) and before \\end{document}."""
    a, b = 0, len(lines)
    for i, l in enumerate(lines):
        if "\\begin{document}" in l[3]:
            a = i + 1
        if "\\end{document}" in l[3]:
            b = i
            break
    return a, b


def paragraphs(lines, lo, hi):
    """Paragraph runs (start, end) of line indices in [lo, hi): split on blank lines, but never inside an environment."""
    out, start, depth = [], None, 0
    for i in range(lo, hi):
        s = lines[i][3]
        blank = not s.strip()
        if not blank:
            for m in _BEGIN.finditer(s):
                depth += 0 if m.group(1) in _NO_SPLIT_SKIP else 1
            for m in _END.finditer(s):
                depth -= 0 if m.group(1) in _NO_SPLIT_SKIP else 1
            depth = max(depth, 0)
        if blank and depth == 0:
            if start is not None:
                out.append((start, i - 1))
                start = None
        elif start is None and not blank:
            start = i
    if start is not None:
        out.append((start, hi - 1))
    return out


def _pack(lines, paras, target=CHUNK_TOKENS, maximum=CHUNK_MAX):
    def tok(a, b):
        return store.approx_tokens("\n".join(l[2] for l in lines[a:b + 1]))

    groups, cur = [], None
    for a, b in paras:
        if cur is None:
            cur = [a, b]
        elif tok(cur[0], b) > target and tok(cur[0], cur[1]) >= 80:
            groups.append(tuple(cur))
            cur = [a, b]
        else:
            cur[1] = b
    if cur:
        groups.append(tuple(cur))
    return groups


def chunk_stream(lines, paper):
    """-> (chunks, outline). Each chunk: id, file, line_start, line_end, level, title, section_path, part, tags, text, plain, hash, tokens, ord."""
    a, b = _body_range(lines)
    heads = [h for h in _headings(lines)]
    # title/abstract/keywords may sit before \begin{document}; sections must be in the body
    heads = [h for h in heads if h["kind"] != "section" or a <= h["idx"] < b]
    bib = next((i for i in range(a, b) if _STOP_BIB.search(lines[i][3])), b)
    bounds = sorted({h["idx"] for h in heads} | {bib, b})
    chunks, outline, used_ids, ord_ = [], [], {}, 0
    stack = []  # (level, title)
    skip_until = -1
    nodes = []
    for h in heads:
        nodes.append(h)
    for ni, h in enumerate(nodes):
        if h["idx"] < skip_until:
            continue
        nxt = [x for x in bounds if x > h["idx"]]
        end = min(nxt[0] if nxt else b, bib if h["idx"] < bib else b)
        if h["kind"] in ("abstract", "title", "keywords"):
            lo, hi = h["idx"], h["end_idx"] + 1
            if h["kind"] == "abstract":
                skip_until = hi
            path = [h["title"]]
            part = {"abstract": "abstract", "title": "title", "keywords": "keywords"}[h["kind"]]
            tags = [part]
        else:
            while stack and stack[-1][0] >= h["level"]:
                stack.pop()
            stack.append((h["level"], h["title"]))
            path = [t for _, t in stack]
            lo, hi = h["idx"], end
            tags = taxonomy.classify(h["title"])
            for _, t in reversed(stack[:-1]):
                if tags:
                    break
                tags = taxonomy.classify(t)
            part = taxonomy.parents(tags)[0] if taxonomy.parents(tags) else None
        if h["kind"] == "section" and h["level"] <= 0:
            outline.append({"title": h["title"], "level": h["level"], "part": part})
            continue
        # contiguous single-file runs
        runs, s = [], lo
        for i in range(lo + 1, hi + 1):
            if i == hi or lines[i][0] != lines[s][0] or lines[i][1] != lines[i - 1][1] + 1:
                runs.append((s, i))
                s = i
        base_slug = slug(" > ".join(path))
        k = 0
        for rs, re_ in runs:
            paras = paragraphs(lines, rs, re_)
            if not paras:
                continue
            for ca, cb in _pack(lines, paras):
                raw = "\n".join(l[2] for l in lines[ca:cb + 1])
                plain = detex("\n".join(l[3] for l in lines[ca:cb + 1]))  # comment-free: search never sees % notes
                if k == 0 and h["kind"] == "section" and len(plain.split()) - len(h["title"].split()) < MIN_BODY_WORDS:
                    continue  # a heading with (almost) nothing under it: it stays in the outline but is not a chunk
                k += 1
                f = lines[ca][0]
                cid_base = f"P:{paper}:{f}#{base_slug}#k{k:02d}"
                n = used_ids.get(cid_base, 0) + 1
                used_ids[cid_base] = n
                cid = cid_base if n == 1 else f"{cid_base}~{n}"
                ord_ += 1
                chunks.append({
                    "id": cid, "kind": "tex", "file": f, "line_start": lines[ca][1], "line_end": lines[cb][1],
                    "level": h["level"], "title": h["title"], "section_path": " > ".join(path), "part": part, "tags": tags,
                    "text": raw, "plain": plain, "tokens": store.approx_tokens(raw),
                    "hash": store.sha256_text(" > ".join(path) + "\n" + raw), "ord": ord_,
                })
        outline.append({"title": h["title"], "level": h["level"], "part": part, "path": " > ".join(path), "kind": h["kind"],
                        "file": lines[lo][0], "line": lines[lo][1], "chunks": k})
    return chunks, outline


def parse_paper(root, paper, main=None):
    """-> dict(chunks, outline, files, warnings, main). Raises FileNotFoundError when there is no .tex at all."""
    root = Path(root)
    main = Path(main) if main else find_main(root)
    if not main:
        raise FileNotFoundError(f"no .tex file in {root}")
    lines, warnings = expand(root, main)
    chunks, outline = chunk_stream(lines, paper)
    return {"chunks": chunks, "outline": outline, "files": sorted({l[0] for l in lines}),
            "warnings": warnings, "main": str(main.relative_to(root.resolve()) if main.is_absolute() else main)}


# ---------------------------------------------------------------------- other sources: memory, bib, reviews

def parse_markdown(text, paper, relpath, kind="memory"):
    """memory.md / review reports: every '## ' / '### ' block (e.g. the Project snapshot, each dated log entry) is a chunk."""
    out, ord_ = [], 0
    parts = re.split(r"(?m)^(?=#{2,3} )", text)
    seen = {}
    start_line = 1
    for part in parts:
        n_lines = part.count("\n")
        head = part.split("\n", 1)[0].strip()
        if head.startswith("#") and len(part.split()) > 8 and not head.startswith("# "):
            title = head.lstrip("#").strip()
            base = f"P:{paper}:{kind}#{slug(title)}"
            seen[base] = seen.get(base, 0) + 1
            cid = base if seen[base] == 1 else f"{base}~{seen[base]}"
            ord_ += 1
            out.append({"id": cid, "kind": kind, "file": relpath, "line_start": start_line, "line_end": start_line + n_lines,
                        "level": 2, "title": title, "section_path": title, "part": None, "tags": [], "text": part.strip(),
                        "plain": detex(part), "tokens": store.approx_tokens(part), "hash": store.sha256_text(part), "ord": ord_})
        start_line += n_lines
    return out


def parse_memory(text, paper, relpath):
    return parse_markdown(text, paper, relpath, "memory")


def parse_bib(text, paper, relpath):
    out, ord_ = [], 0
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        depth, i = 0, text.index("{", m.start())
        j = i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[i:j + 1]

        def field(name):
            fm = re.search(rf"\b{name}\s*=\s*", body, re.I)
            if not fm:
                return ""
            k = fm.end()
            if k < len(body) and body[k] == "{":
                g = _group(body, k)
                return re.sub(r"[{}\s]+", " ", g[0]).strip() if g else ""
            q = re.match(r'"([^"]*)"', body[k:])
            return q.group(1).strip() if q else re.match(r"[^,\n]*", body[k:]).group(0).strip()

        key, title, year = m.group(2), field("title"), field("year")
        author = field("author").split(" and ")[0]
        summary = f"{key} | {author} {year} | {title}".strip()
        line = text.count("\n", 0, m.start()) + 1
        ord_ += 1
        out.append({"id": f"P:{paper}:bib#{key}", "kind": "bib", "file": relpath, "line_start": line,
                    "line_end": text.count("\n", 0, j) + 1, "level": 0, "title": key, "section_path": key, "part": "references",
                    "tags": ["references"], "text": summary, "plain": f"{author} {title} {year}", "tokens": store.approx_tokens(summary),
                    "hash": store.sha256_text(body), "ord": ord_})
    return out
