"""The literature library: the author's own reading, kept in folders (with subfolders) of books, articles and PDFs.

Each file is read once, cut into small labelled parts (about 350 tokens, with the page or paragraph they come from and the heading above them), and put into a
local search index, so a request can read just the parts that matter ("what do my papers say about well data curation?") and cite them. Nothing is sent anywhere:
reading, splitting and searching all happen on this computer; only the few parts a request uses go to OpenAI, and the request lists them.

  folders / set_folders   which folders are the library (the author's choice; read only: the app never changes a file there)
  update(progress)        read what is new or changed, forget what is gone (a library of hundreds of PDFs takes a few minutes the first time, seconds after)
  status / docs / parts   what is in it, for the Settings page
  query(text)             the best parts for a text, with where they come from and how to cite them
  paper_bib / cite_for    match a document to an entry of a paper's .bib so \\cite{key} can be offered; the DOI is used to add what is missing

The index is a derived cache (library/index.db in the data folder): the folders are the truth, and it can be made again at any time.
"""
import hashlib
import html
import json
import os
import re
import threading
import time
import zipfile
from pathlib import Path

from . import chunk_book, config, files as pfiles, retrieve, store, taxonomy

DIRNAME = "library"
LOGIC_VERSION = 1                  # bump when the reading or splitting changes, so everything is read again
SCHEMA = 1
MAX_FOLDERS = 12
MAX_DOCS = 6000                    # files read besides the entries of .bib files
MAX_SCAN = 200000                  # directory entries looked at before stopping
MAX_PAGES = 1500                   # pages of one PDF that are read
MAX_PARTS = 6000                   # parts kept of one document
BOOK_PAGES = 80                    # a PDF longer than this is treated as a book: its reference lists are kept apart from the text only in articles
EXT_KIND = {"pdf": "pdf", "docx": "docx", "txt": "text", "md": "text", "markdown": "text", "rst": "text", "tex": "text", "html": "html", "htm": "html", "bib": "bib"}
MAX_BYTES = {"pdf": 200 << 20, "docx": 60 << 20, "text": 8 << 20, "html": 8 << 20, "bib": 30 << 20}
SKIP_DIRS = pfiles.SKIP_DIRS | {"__macosx", ".git", "$recycle.bin"}
TOOL_NAMES = {"microsoft word", "pdfmaker", "acrobat", "latex", "pdftex", "xetex", "luatex", "tex output", "dvips", "ghostscript", "scansnap", "adobe", "untitled", "pdf"}
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>{}|\\^`]+", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")

_LOCK = threading.Lock()
_STATE = {"running": False, "done": 0, "total": 0, "current": "", "started": 0.0}


class LibraryError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ where things are kept

def root():
    return config.data_dir() / DIRNAME


def _settings_path():
    return root() / "library.json"


def _db_path():
    return root() / "index.db"


def _read_settings():
    try:
        d = json.loads(_settings_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_settings(d):
    root().mkdir(parents=True, exist_ok=True)
    tmp = _settings_path().with_name(".library.json.tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, _settings_path())


# ------------------------------------------------------------------------------------------------ the folders

def folder_note(p):
    """Why a folder cannot be read, in words that fit where the app runs; '' when it can."""
    why = pfiles.unsafe_folder(p)
    if why:
        return why
    if not p.is_dir():
        if os.environ.get("OVERLEAF_UI_IN_DOCKER") == "1":
            return ("I cannot see this folder. The app runs in Docker and only sees the folders you share with it (read-only is enough, and safest). In a terminal on your computer: "
                    f"./ui.sh folders add --read-only \"{p}\"   then   ./ui.sh down && ./ui.sh up")
        return "This folder does not exist on this computer."
    if not os.access(p, os.R_OK | os.X_OK):
        return "I am not allowed to read this folder."
    return ""


def folders():
    """The library's folders: [{path, ok, note}]."""
    out = []
    for raw in _read_settings().get("folders") or []:
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            out.append({"path": str(raw), "ok": False, "note": "Use the full path of the folder."})
            continue
        note = folder_note(p)
        out.append({"path": str(p), "ok": not note, "note": note})
    return out


def set_folders(wanted):
    """Save the list of folders. A folder that cannot be read now stays in the list with its reason (it may be shared later)."""
    cleaned = []
    for x in wanted or []:
        x = str(x or "").strip()
        if not x:
            continue
        if len(x) > 500 or "\n" in x or "\0" in x:
            raise LibraryError("That does not look like a folder path.")
        x = str(Path(x).expanduser()) if x.startswith("~") else x
        x = x.rstrip("/\\") or x
        if x not in cleaned:
            cleaned.append(x)
    if len(cleaned) > MAX_FOLDERS:
        raise LibraryError(f"At most {MAX_FOLDERS} folders can be in the library.")
    settings = _read_settings()
    settings["folders"] = cleaned
    _write_settings(settings)
    return folders()


# ------------------------------------------------------------------------------------------------ what there is to read

def _doc_id(path):
    return hashlib.sha1(str(path).encode("utf-8", "replace")).hexdigest()[:12]


def discover():
    """-> (files, notes): every file to read under the library's folders, {path, folder, rel, ext, kind, size, mtime_ns, note}. No hidden folders, no links, no secrets."""
    out, notes, seen_real, scanned = [], [], set(), 0
    for f in folders():
        if not f["ok"]:
            continue
        base = Path(f["path"])
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS and not d.endswith((".app", ".photoslibrary")))
            for fn in sorted(filenames):
                scanned += 1
                if scanned > MAX_SCAN or len(out) >= MAX_DOCS:
                    notes.append(f"The library has more files than I read ({MAX_DOCS}); I stopped early.")
                    return out, notes
                ext = pfiles.ext_of(fn)
                kind = EXT_KIND.get(ext)
                p = Path(dirpath) / fn
                if not kind or fn.startswith((".", "~$")) or pfiles.is_secret(fn) or p.is_symlink():
                    continue
                try:
                    st = p.stat()
                    real = str(p.resolve())
                except OSError:
                    continue
                if real in seen_real or st.st_size == 0:
                    continue
                seen_real.add(real)
                note = f"Too big to read (over {MAX_BYTES[kind] >> 20} MB)." if st.st_size > MAX_BYTES[kind] else ""
                out.append({"path": str(p), "folder": str(base), "rel": str(p.relative_to(base)), "ext": ext, "kind": kind, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "note": note})
    return out, notes


def _sig(f):
    return f"{LOGIC_VERSION}:{f['size']}:{f['mtime_ns']}"


# ------------------------------------------------------------------------------------------------ reading a document

def _clean_title(t):
    t = re.sub(r"\s+", " ", (t or "")).strip(" \t-_.")
    t = re.sub(r"^(microsoft word|pdf|untitled)\s*[-:]\s*", "", t, flags=re.I)
    t = re.sub(r"\.(pdf|docx?|tex|txt)$", "", t, flags=re.I)
    return t


def _plausible_title(t):
    t = _clean_title(t)
    if not 8 <= len(t) <= 300 or t.lower() in TOOL_NAMES or any(x in t.lower() for x in ("untitled", "microsoft word", "pdfmaker")):
        return ""
    if re.fullmatch(r"[\w .-]*\.(pdf|docx?|tex)", t, re.I) or not re.search(r"[A-Za-z]{3}", t):
        return ""
    return t


def _plausible_authors(a):
    a = re.sub(r"\s+", " ", (a or "")).strip()
    if not 3 <= len(a) <= 400 or a.lower() in TOOL_NAMES or not re.search(r"[A-Za-z]{2}", a):
        return ""
    if re.search(r"(?i)\b(admin|administrator|user|owner|unknown|microsoft|adobe|elsevier|springer|wiley|ieee|publisher|latex|tex)\b", a) and len(a.split()) < 3:
        return ""
    return a


def author_list(s):
    """'Smith, J. and Lee, K.' / 'Smith, J.; Lee, K.' / 'John Smith, Jane Doe' -> ['Smith, J.', 'Lee, K.'] (as written; only the splitting is guessed)."""
    s = (s or "").strip()
    if not s:
        return []
    if ";" in s or re.search(r"\band\b|&", s):
        return [x.strip() for x in re.split(r";|\band\b|&", s) if x.strip()]
    parts = [x.strip() for x in s.split(",") if x.strip()]
    if len(parts) == 2 and len(parts[1].split()) <= 2 and re.fullmatch(r"(?:[A-Z]\.?\s*){1,3}|[A-Z][a-z]+(?:\s+[A-Z]\.?)?", parts[1]):
        return [s]                                              # "Smith, John": one author, family name first
    return parts


def surname(author):
    a = (author or "").strip()
    if "," in a:
        return a.split(",")[0].strip()
    w = a.split()
    return w[-1] if w else ""


def _label(authors, year, title, name):
    etal = bool(re.search(r"\bet\s+al\.?\s*$", authors or "", re.I))
    people = author_list(re.sub(r"[\s,]*\bet\s+al\.?\s*$", "", authors or "", flags=re.I))
    if people and surname(people[0]) and year:
        return f"{surname(people[0])}{' et al.' if etal or len(people) > 2 else (' and ' + surname(people[1]) if len(people) == 2 else '')} {year}"
    return (title or name)[:60]


_NAME_RE = [re.compile(r"^(?P<au>[^-_]+?)\s+-\s+(?P<yr>(?:19|20)\d{2})\s+-\s+(?P<ti>.+)$"),               # Zotero / Mendeley: "Smith et al. - 2020 - Title"
            re.compile(r"^(?P<au>[A-Za-z][A-Za-z' ]{1,40}?)[_ ](?:et[_ ]al[_ ])?(?P<yr>(?:19|20)\d{2})[_ -]+(?P<ti>.+)$")]            # "Smith_2020_Title"


def _from_name(stem):
    for rx in _NAME_RE:
        m = rx.match(stem.replace("%20", " "))
        if m:
            au = re.sub(r"\s+et\s+al\.?$", " et al.", m.group("au").strip(), flags=re.I)
            return {"authors": au, "year": m.group("yr"), "title": _clean_title(m.group("ti").replace("_", " "))}
    return {"title": _clean_title(stem.replace("_", " "))}


def _doi_of(text):
    for m in DOI_RE.finditer(text or ""):
        d = m.group(0).rstrip(".,;:)]}\"'")
        if len(d) > 9:
            return d
    return ""


_SKIP_FIRST = re.compile(r"(?i)^(?:research\s+article|original\s+(?:article|paper|research)|article|review(?:\s+article)?|short\s+communication|letter|journal|volume|vol\.|issn|doi|https?:|www\.|©|copyright|"
                         r"received|accepted|available\s+online|contents\s+lists|sciencedirect|elsevier|springer|ieee|preprint|arxiv|published|open\s+access|check\s+for\s+updates|"
                         r"citation|cite\s+this|keywords?|abstract|highlights|graphical\s+abstract|article\s+info|a\s+r\s+t\s+i\s+c\s+l\s+e)")


def _title_from_page(text):
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    cand = []
    for i, ln in enumerate(lines[:25]):
        if _SKIP_FIRST.match(ln) or len(ln) < 12 or re.fullmatch(r"[\d\s./-]+", ln) or "@" in ln:
            if cand:
                break
            continue
        if re.search(r"(?i)\b(university|department|institute|faculty|laboratory|school of)\b", ln) and cand:
            break
        if len(ln) > 110 and cand:                         # a title wraps short; a long line is the first sentence of the text
            break
        cand.append(ln)
        joined = " ".join(cand)
        if len(ln) < 70 and i + 1 < len(lines) and len(lines[i + 1]) > len(ln) + 20:      # a title wraps to ever shorter lines: a much longer line next is the text
            break
        if len(joined) >= 40 and (ln[-1:] not in ",:;-" and (len(cand) >= 2 or ln[-1:] in ".?" or len(joined) > 90)):
            break
        if len(cand) >= 3:
            break
    t = _clean_title(" ".join(cand))
    return t if 12 <= len(t) <= 260 else ""


def _year_from_page(text):
    best, score = "", 0
    for m in YEAR_RE.finditer(text[:6000]):
        near = text[max(0, m.start() - 40):m.start()].lower()
        s = 3 if re.search(r"©|copyright|published|accepted|received|available online|revised|\(c\)", near) else 1
        yr = int(m.group(1))
        if yr > time.localtime().tm_year + 1:
            continue
        if s > score or (s == score and yr > int(best or 0) and s > 1):
            best, score = str(yr), s
    return best if score >= 3 else ""


def _pdf_info(path):
    import subprocess
    try:
        r = subprocess.run([chunk_book._poppler("pdfinfo"), str(path)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    info = {}
    for ln in r.stdout.splitlines():
        k, _, v = ln.partition(":")
        info[k.strip().lower()] = v.strip()
    return info


def _loose_heading(lines):
    """A heading in a PDF that is not one of the usual section names: a short line, with no full stop, above lines that are clearly longer (the text wraps wider than it)."""
    if len(lines) < 2:
        return None
    t = lines[0].strip()
    if not 4 <= len(t) <= 70 or len(t.split()) > 9 or t[-1] in ".,;:?!" or not (t[0].isupper() or t[0].isdigit()):
        return None
    letters = sum(c.isalpha() for c in t)
    if letters < 0.75 * len(t.replace(" ", "")) or len(lines[1]) < len(t) + 15 or lines[1].strip().startswith(("(", "[")):
        return None
    return t


def _parts_from_pages(pages, article):
    """Cleaned pages -> parts with their pages and headings. In an article the reference list is left out (it is not text to write from)."""
    blocks, head, skipping, total = [], "", False, len(pages)
    for pno, lines in enumerate(pages, 1):
        for par in re.split(r"\n\s*\n", "\n".join(lines)):
            par = par.strip()
            if not par:
                continue
            lines_ = par.split("\n")
            while lines_:                                      # pdftotext often puts a heading on the line above its text with no blank line between
                h = pfiles.heading_of("\n".join(lines_)) if len(lines_) == 1 else (pfiles.heading_of(lines_[0]) or _loose_heading(lines_))
                if not h:
                    break
                head = h
                key = h.lower().strip(" .:0123456789")
                skipping = article and pno >= total * 0.4 and key in pfiles._STOP_HEADS
                lines_ = lines_[1:]
            if not lines_:
                continue
            text = pfiles._fix_par("\n".join(lines_))
            if skipping or len(text) < 25 or re.fullmatch(r"[\d\s.\-–/]+", text):
                continue
            blocks.append({"text": text, "lo": pno, "hi": pno, "head": head})
    return pfiles._pack(blocks)


def read_pdf(path):
    raw = chunk_book.extract_pages(path, timeout=300)
    if not any(p.strip() for p in raw):
        raise pfiles.FileProblem("This PDF has no text I can read (a scan?). Run it through OCR first.")
    note = ""
    if len(raw) > MAX_PAGES:
        note = f"Only the first {MAX_PAGES} pages are read."
        raw = raw[:MAX_PAGES]
    info = _pdf_info(path)
    first = "\n".join(raw[:2])
    meta = {"title": _plausible_title(info.get("title")) or _title_from_page(raw[0]), "authors": _plausible_authors(info.get("author")), "doi": _doi_of(first) or _doi_of(info.get("subject", "")),
            "year": _year_from_page(first), "pages": len(raw), "source": "text"}
    byname = _from_name(Path(path).stem)
    for k in ("authors", "year"):
        if not meta.get(k) and byname.get(k):
            meta[k], meta["source"] = byname[k], "name"
    if not meta["title"]:
        meta["title"], meta["source"] = byname.get("title", ""), "name"
    parts = _parts_from_pages(chunk_book.clean_pages(raw), article=len(raw) <= BOOK_PAGES)
    return meta, parts, "pages", note


def read_docx(path):
    parts, noun = pfiles.parts_docx(path)
    head = next((p["head"] for p in parts if p.get("head")), "")
    first = " ".join(p["text"] for p in parts[:2])
    byname = _from_name(Path(path).stem)
    meta = {"title": _plausible_title(head) or byname.get("title", ""), "authors": byname.get("authors", ""), "doi": _doi_of(first), "year": byname.get("year") or _year_from_page(first), "pages": 0, "source": "name"}
    return meta, parts, noun, ""


def _html_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<h([1-3])[^>]*>(.*?)</h\1>", lambda m: "\n\n# " + re.sub(r"<[^>]+>", "", m.group(2)) + "\n\n", raw)
    raw = re.sub(r"(?i)</?(p|div|br|li|tr|section|article|h[4-6])[^>]*>", "\n\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def read_text(path, kind):
    raw = pfiles._read_text(Path(path), limit=MAX_BYTES["text"])
    text = _html_text(raw) if kind == "html" else raw
    ext = pfiles.ext_of(Path(path).name)
    parts = pfiles._pack(pfiles._paragraph_blocks(text.splitlines(), heading=pfiles.heading_of if ext in ("txt", "rst", "html", "htm") else None))
    head = next((p["head"] for p in parts if p.get("head")), "")
    byname = _from_name(Path(path).stem)
    meta = {"title": _plausible_title(head) or byname.get("title", ""), "authors": byname.get("authors", ""), "doi": _doi_of(text[:6000]), "year": byname.get("year") or _year_from_page(text), "pages": 0, "source": "name"}
    return meta, parts, "section" if kind == "html" else "lines", ""


def _bib_fields(body):
    out = {}
    for m in re.finditer(r"\b([A-Za-z]+)\s*=\s*", body):
        name, k = m.group(1).lower(), m.end()
        if name in out or k >= len(body):
            continue
        if body[k] == "{":
            depth, j = 0, k
            while j < len(body):
                depth += (body[j] == "{") - (body[j] == "}")
                if depth == 0:
                    break
                j += 1
            val = body[k + 1:j]
        elif body[k] == '"':
            j = body.find('"', k + 1)
            val = body[k + 1:j if j > 0 else len(body)]
        else:
            val = re.match(r"[^,\n]*", body[k:]).group(0)
        out[name] = re.sub(r"\s+", " ", re.sub(r"[{}]", "", val)).strip()
    return out


def bib_entries(text):
    """-> [{key, type, fields, raw}] for every real entry of a BibTeX text."""
    out = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        i = text.index("{", m.start())
        depth, j = 0, i
        while j < len(text):
            depth += (text[j] == "{") - (text[j] == "}")
            if depth == 0:
                break
            j += 1
        raw = text[m.start():j + 1]
        out.append({"key": m.group(2), "type": m.group(1).lower(), "fields": _bib_fields(text[m.end():j]), "raw": raw})
    return out


def read_bib(path):
    """A .bib file is a list of references, not text to read: each entry is one small record (title, authors, abstract, keywords) that can be found and cited."""
    text = pfiles._read_text(Path(path), limit=MAX_BYTES["bib"])
    docs = []
    for e in bib_entries(text):
        f = e["fields"]
        authors = f.get("author", "").replace(" and ", "; ")
        year = f.get("year") or (YEAR_RE.search(f.get("date", "")).group(1) if YEAR_RE.search(f.get("date", "")) else "")
        where = f.get("journal") or f.get("booktitle") or f.get("publisher") or ""
        lines = [f"{authors} ({year}). {f.get('title', '')}. {where}".strip(), f"DOI: {f['doi']}" if f.get("doi") else "", f"Keywords: {f['keywords']}" if f.get("keywords") else "",
                 f"Abstract: {f['abstract']}" if f.get("abstract") else ""]
        text_ = "\n".join(x for x in lines if x)
        docs.append({"title": f.get("title", e["key"]), "authors": authors, "year": year, "doi": _doi_of(f.get("doi", "")) or f.get("doi", ""), "pages": 0, "source": "bib", "bib_key": e["key"], "bibtex": e["raw"],
                     "parts": [{"text": text_, "lo": 1, "hi": 1, "head": ""}], "noun": "entry", "name": e["key"]})
    return docs


def read_doc(f):
    """-> [{title, authors, year, doi, pages, source, parts, noun, note, ...}] for one file (several for a .bib), or raises FileProblem / OSError."""
    kind, path = f["kind"], f["path"]
    if kind == "bib":
        return read_bib(path)
    if kind == "pdf":
        meta, parts, noun, note = read_pdf(path)
    elif kind == "docx":
        meta, parts, noun, note = read_docx(path)
    else:
        meta, parts, noun, note = read_text(path, kind)
    if not parts:
        raise pfiles.FileProblem("There was no text to read in this file.")
    if len(parts) > MAX_PARTS:
        parts, note = parts[:MAX_PARTS], f"Only the first {MAX_PARTS} parts are read."
    return [dict(meta, parts=parts, noun=noun, note=note, name=Path(path).stem)]


# ------------------------------------------------------------------------------------------------ the index

def _connect():
    return store.connect(_db_path())


def _init(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS docs(id TEXT PRIMARY KEY, path TEXT, folder TEXT, rel TEXT, ext TEXT, kind TEXT, size INTEGER, mtime_ns INTEGER, sig TEXT,
            title TEXT, authors TEXT, year TEXT, doi TEXT, label TEXT, pages INTEGER, parts INTEGER, tokens INTEGER, note TEXT, source TEXT, bib_key TEXT, bibtex TEXT, noun TEXT,
            o_title TEXT, o_authors TEXT, o_year TEXT, o_doi TEXT, o_source TEXT, linked TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_docs_path ON docs(path);
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER, lo INTEGER, hi INTEGER, head TEXT, text TEXT, tokens INTEGER, tags TEXT);
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, ord);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, who, head, text, tokenize='porter unicode61');
    """)


def _open():
    """The index, ready to use. One made by older code is dropped (it is only a cache; the next update reads everything again)."""
    root().mkdir(parents=True, exist_ok=True)
    p = _db_path()

    def fresh():
        for suffix in ("", "-journal"):
            try:
                Path(str(p) + suffix).unlink()
            except OSError:
                pass
        con = _connect()
        _init(con)
        store.meta_set(con, "schema", SCHEMA)
        store.meta_set(con, "logic", LOGIC_VERSION)
        con.commit()
        return con

    if not p.exists():
        return fresh()
    try:
        con = _connect()
        _init(con)
        ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok" and store.meta_get(con, "schema") == SCHEMA and store.meta_get(con, "logic") == LOGIC_VERSION
    except Exception:  # noqa: BLE001  (a damaged file: the folders are the truth)
        ok = False
    if not ok:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
        return fresh()
    return con


def _delete_path(con, path):
    ids = [r[0] for r in con.execute("SELECT id FROM docs WHERE path=?", (path,))]
    for i in ids:
        con.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)", (i,))
        con.execute("DELETE FROM chunks WHERE doc_id=?", (i,))
    con.execute("DELETE FROM docs WHERE path=?", (path,))


def _tags(head):
    return ",".join(taxonomy.classify(head)) if head else ""


def _store_doc(con, f, d, n):
    did = _doc_id(f["path"]) if f["kind"] != "bib" else _doc_id(f["path"] + "#" + str(d.get("bib_key", n)))
    parts = d.get("parts") or []
    tokens = sum(store.approx_tokens(p["text"]) for p in parts)
    label = _label(d.get("authors"), d.get("year"), d.get("title"), d.get("name") or Path(f["path"]).stem)
    con.execute("INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'')",
                (did, f["path"], f["folder"], f["rel"], f["ext"], f["kind"], f["size"], f["mtime_ns"], _sig(f), d.get("title") or "", d.get("authors") or "", d.get("year") or "", d.get("doi") or "",
                 label, d.get("pages") or 0, len(parts), tokens, d.get("note") or "", d.get("source") or "", d.get("bib_key") or "", d.get("bibtex") or "", d.get("noun") or "",
                 d.get("title") or "", d.get("authors") or "", d.get("year") or "", d.get("doi") or "", d.get("source") or ""))
    who = " ".join(x for x in (d.get("title"), d.get("authors"), label, d.get("year")) if x)
    for k, p in enumerate(parts, 1):
        cid = f"L:{did}#{k}"
        con.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)", (cid, did, k, p["lo"], p["hi"], p.get("head") or "", p["text"], store.approx_tokens(p["text"]), _tags(p.get("head"))))
        con.execute("INSERT INTO chunks_fts(chunk_id, who, head, text) VALUES(?,?,?,?)", (cid, who, p.get("head") or "", p["text"]))
    return did


def _norm_title(t):
    return " ".join(re.findall(r"[a-z0-9]+", (t or "").lower()))


def _tokens_of(t):
    return set(_norm_title(t).split())


def _same_title(a, b):
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    return len(ta) >= 4 and len(tb) >= 4 and len(ta & tb) / len(ta | tb) >= 0.85


def _set_who(con, doc_id, title, authors, label, year):
    who = " ".join(x for x in (title, authors, label, year) if x)
    con.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
    for c in con.execute("SELECT id, head, text FROM chunks WHERE doc_id=?", (doc_id,)).fetchall():
        con.execute("INSERT INTO chunks_fts(chunk_id, who, head, text) VALUES(?,?,?,?)", (c["id"], who, c["head"], c["text"]))


def _link_records(con):
    """A .bib in the library says more about a document than the document does (right title, authors, year): documents that match one of its entries by DOI or title take
    them over, and the entry stops appearing on its own. What the file itself said is kept, so removing the .bib puts it back."""
    for d in con.execute("SELECT id, o_title, o_authors, o_year, o_doi, o_source, rel FROM docs WHERE kind!='bib' AND source='bib'").fetchall():
        label = _label(d["o_authors"], d["o_year"], d["o_title"], d["rel"])
        con.execute("UPDATE docs SET title=?, authors=?, year=?, doi=?, source=?, label=?, bib_key='', bibtex='' WHERE id=?", (d["o_title"], d["o_authors"], d["o_year"], d["o_doi"], d["o_source"], label, d["id"]))
        _set_who(con, d["id"], d["o_title"], d["o_authors"], label, d["o_year"])
    con.execute("UPDATE docs SET linked='' WHERE kind='bib'")
    recs = [dict(r) for r in con.execute("SELECT * FROM docs WHERE kind='bib' AND parts>0")]
    if not recs:
        return 0
    by_doi = {r["doi"].lower(): r for r in recs if r["doi"]}
    n = 0
    for d in con.execute("SELECT * FROM docs WHERE kind!='bib' AND parts>0").fetchall():
        hit = by_doi.get((d["doi"] or "").lower()) if d["doi"] else None
        if not hit:
            hit = next((r for r in recs if d["title"] and _same_title(r["title"], d["title"])), None)
        if hit:
            label = _label(hit["authors"], hit["year"], hit["title"], d["rel"])
            con.execute("UPDATE docs SET title=?, authors=?, year=?, doi=?, label=?, source='bib', bib_key=?, bibtex=? WHERE id=?",
                        (hit["title"], hit["authors"], hit["year"], hit["doi"] or d["doi"], label, hit["bib_key"], hit["bibtex"], d["id"]))
            con.execute("UPDATE docs SET linked=? WHERE id=? AND linked=''", (d["id"], hit["id"]))
            _set_who(con, d["id"], hit["title"], hit["authors"], label, hit["year"])
            n += 1
    return n


def _why_unread(f, e):
    """What to tell the author about a file that could not be read: short, no tool output."""
    if isinstance(e, pfiles.FileProblem):
        return str(e)[:300]
    if isinstance(e, chunk_book.ToolMissing):
        return "The program that reads this kind of file (poppler) is not installed."
    if isinstance(e, zipfile.BadZipFile) or f["kind"] in ("pdf", "docx"):
        return f"This does not look like a readable {f['ext'].upper()} (it may be damaged)."
    return "This file could not be read."


def running():
    return dict(_STATE) if _STATE["running"] else None


def update(progress=None):
    """Read what is new or changed, forget what is gone. -> {docs, parts, tokens, new, changed, removed, failed, unchanged, seconds, notes}
    progress(done, total) is called after every file."""
    if not _LOCK.acquire(blocking=False):
        raise LibraryError("The library is being updated already.")
    t0 = time.time()
    _STATE.update(running=True, done=0, total=0, current="", started=t0)
    try:
        found, notes = discover()
        if not any(f["ok"] for f in folders()):
            raise LibraryError("None of the library's folders can be read yet. Add a folder first.")
        with store.write_lease(_db_path(), timeout=10, stale=3600):
            con = _open()
            try:
                known = {}
                for r in con.execute("SELECT path, sig FROM docs"):
                    known[r["path"]] = r["sig"]
                here = {f["path"] for f in found}
                removed = [p for p in known if p not in here]
                for p in removed:
                    _delete_path(con, p)
                con.commit()
                res = {"new": 0, "changed": 0, "failed": 0, "unchanged": 0, "removed": len(removed), "notes": list(notes)}
                _STATE["total"] = len(found)
                for i, f in enumerate(found, 1):
                    _STATE.update(done=i - 1, current=f["rel"])
                    if known.get(f["path"]) == _sig(f):
                        res["unchanged"] += 1
                    else:
                        res["new" if f["path"] not in known else "changed"] += 1
                        _delete_path(con, f["path"])
                        try:
                            if f["note"]:
                                raise pfiles.FileProblem(f["note"])
                            for n, d in enumerate(read_doc(f), 1):
                                _store_doc(con, f, d, n)
                        except (pfiles.FileProblem, OSError, RuntimeError, chunk_book.ToolMissing, zipfile.BadZipFile) as e:
                            res["failed"] += 1
                            _store_doc(con, f, {"title": _from_name(Path(f["path"]).stem).get("title", ""), "note": _why_unread(f, e), "source": "name", "parts": []}, 1)
                        con.commit()
                        try:
                            os.utime(str(_db_path()) + ".lease")
                        except OSError:
                            pass
                    if progress:
                        progress(i, len(found))
                if res["new"] or res["changed"] or res["removed"] or not store.meta_get(con, "linked_once"):
                    _link_records(con)
                    store.meta_set(con, "linked_once", True)
                store.meta_set(con, "indexed_at", time.time())
                store.meta_set(con, "folders", [x["path"] for x in folders()])
                con.commit()
                tot = con.execute("SELECT COUNT(*) n, COALESCE(SUM(parts),0) p, COALESCE(SUM(tokens),0) t FROM docs WHERE parts>0 AND linked=''").fetchone()
                res.update(docs=tot["n"], parts=tot["p"], tokens=tot["t"], seconds=round(time.time() - t0, 1))
                return res
            finally:
                con.close()
    finally:
        _STATE.update(running=False)
        _LOCK.release()


def clear():
    """Forget the index (the folders and the files in them are not touched)."""
    for suffix in ("", "-journal", ".lease"):
        try:
            Path(str(_db_path()) + suffix).unlink()
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------ what is in it

def status():
    """Cheap and local: -> {folders, docs, parts, tokens, indexed_at, new, changed, removed, running, notes}. `new/changed/removed` compare the folders with the index by size and date."""
    fs = folders()
    out = {"folders": fs, "docs": 0, "parts": 0, "tokens": 0, "failed": 0, "indexed_at": None, "new": 0, "changed": 0, "removed": 0, "running": running(), "notes": []}
    if not fs:
        return out
    found, notes = discover()
    out["notes"] = notes
    known = {}
    indexed = _db_path().exists()
    if indexed:
        con = _open()
        try:
            for r in con.execute("SELECT path, sig FROM docs"):
                known[r["path"]] = r["sig"]
            tot = con.execute("SELECT COUNT(*) n, COALESCE(SUM(parts),0) p, COALESCE(SUM(tokens),0) t, COALESCE(SUM(CASE WHEN parts=0 THEN 1 ELSE 0 END),0) f FROM docs WHERE linked=''").fetchone()
            out.update(docs=tot["n"] - tot["f"], parts=tot["p"], tokens=tot["t"], failed=tot["f"], indexed_at=store.meta_get(con, "indexed_at"))
            per = {r["folder"]: (r["n"], r["p"]) for r in con.execute("SELECT folder, COUNT(*) n, SUM(parts) p FROM docs WHERE parts>0 AND linked='' GROUP BY folder")}
        finally:
            con.close()
    else:
        per = {}
    here = {f["path"]: f for f in found}
    out["new"] = sum(1 for p in here if p not in known)
    out["changed"] = sum(1 for p, f in here.items() if p in known and known[p] != _sig(f))
    out["removed"] = sum(1 for p in known if p not in here)
    for f in fs:
        n, parts = per.get(f["path"], (0, 0))
        f["docs"], f["parts"] = n, parts or 0
        f["files"] = sum(1 for x in found if x["folder"] == f["path"])
    return out


def docs(query="", limit=2000):
    """The documents, by folder and name: [{id, rel, folder, kind, title, authors, year, doi, label, parts, tokens, pages, note, source, bib_key}]"""
    if not _db_path().exists():
        return []
    con = _open()
    try:
        rows = con.execute("SELECT id, rel, folder, kind, ext, size, title, authors, year, doi, label, parts, tokens, pages, note, source, bib_key, noun FROM docs WHERE linked='' ORDER BY folder, rel LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()
    out = [dict(r) for r in rows]
    q = (query or "").strip().lower()
    if q:
        out = [d for d in out if q in (d["rel"] + " " + d["title"] + " " + d["authors"] + " " + d["year"] + " " + d["doi"]).lower()]
    return out


def get_doc(doc_id):
    con = _open()
    try:
        r = con.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
    finally:
        con.close()
    return dict(r) if r else None


def _where(noun, lo, hi, head):
    word = {"pages": "page", "paragraphs": "paragraph", "lines": "line"}.get(noun)
    loc = f"{word}{'s' if lo != hi else ''} {lo}" + (f" to {hi}" if lo != hi else "") if word else ""
    return " · ".join(x for x in (loc, head) if x) or "entry"


def parts(doc_id, preview=240):
    """The parts of one document in order, without their full text: [{id, where, tokens, preview}]"""
    con = _open()
    try:
        d = con.execute("SELECT noun FROM docs WHERE id=?", (doc_id,)).fetchone()
        rows = con.execute("SELECT id, lo, hi, head, tokens, text FROM chunks WHERE doc_id=? ORDER BY ord", (doc_id,)).fetchall()
    finally:
        con.close()
    noun = d["noun"] if d else "pages"
    return [{"id": r["id"], "where": _where(noun, r["lo"], r["hi"], r["head"]), "tokens": r["tokens"], "preview": " ".join(r["text"].split())[:preview]} for r in rows]


def get_chunks(ids):
    """Full parts by id, with their document: [{id, doc, label, title, authors, year, doi, where, text, tokens}]"""
    if not ids or not _db_path().exists():
        return []
    con = _open()
    try:
        out = []
        for i in ids:
            r = con.execute("SELECT c.*, d.label, d.title, d.authors, d.year, d.doi, d.noun, d.rel, d.bib_key FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE c.id=?", (i,)).fetchone()
            if r:
                out.append(_hit(r, 0.0))
    finally:
        con.close()
    return out


def _hit(r, score):
    return {"id": r["id"], "doc": r["doc_id"], "label": r["label"], "title": r["title"], "authors": r["authors"], "year": r["year"], "doi": r["doi"], "rel": r["rel"], "bib_key": r["bib_key"],
            "where": _where(r["noun"], r["lo"], r["hi"], r["head"]), "pages": f"{r['lo']}" if r["lo"] == r["hi"] else f"{r['lo']}-{r['hi']}", "text": r["text"], "tokens": r["tokens"], "score": float(score)}


def query(text, k=6, budget=2400, per_doc=2, doc_ids=None):
    """The best parts of the library for a text. -> {chunks: [hit], terms, docs, tokens}. A hit says which document it is from and where in it (page or paragraph), and carries its text.
    At most `per_doc` parts of one document, so one long book does not crowd out the articles."""
    terms = retrieve.fts_terms(text)
    out = {"chunks": [], "terms": terms, "docs": 0, "tokens": 0}
    if not terms or not _db_path().exists():
        return out
    con = _open()
    try:
        sql = ("SELECT c.*, d.label, d.title, d.authors, d.year, d.doi, d.noun, d.rel, d.bib_key, bm25(chunks_fts, 0.0, 5.0, 3.0, 1.0) AS score "
               "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.chunk_id JOIN docs d ON d.id = c.doc_id WHERE chunks_fts MATCH ? AND d.linked=''")
        args = [retrieve.fts_match(terms)]
        if doc_ids:
            sql += " AND d.id IN (%s)" % ",".join("?" * len(doc_ids))
            args += list(doc_ids)
        rows = con.execute(sql + " ORDER BY score LIMIT ?", args + [k * 8]).fetchall()
    finally:
        con.close()
    used, per = 0, {}
    for r in rows:
        if len(out["chunks"]) >= k or per.get(r["doc_id"], 0) >= per_doc or (out["chunks"] and used + r["tokens"] > budget):
            continue
        per[r["doc_id"]] = per.get(r["doc_id"], 0) + 1
        used += r["tokens"]
        out["chunks"].append(_hit(r, -r["score"]))
    out["docs"], out["tokens"] = len(per), used
    return out


# ------------------------------------------------------------------------------------------------ citing

def paper_bib(proj):
    """The paper's own bibliography as lookups: {entries: [{key, title, authors, year, doi}], by_doi, by_title}"""
    try:
        text = (Path(proj["path"]) / proj["bib"]).read_text(encoding="utf-8", errors="replace")
    except (OSError, KeyError):
        text = ""
    entries = []
    for e in bib_entries(text):
        f = e["fields"]
        entries.append({"key": e["key"], "title": f.get("title", ""), "authors": f.get("author", "").replace(" and ", "; "), "year": f.get("year", ""), "doi": (f.get("doi") or "").lower().replace("https://doi.org/", "")})
    return {"entries": entries, "by_doi": {x["doi"]: x for x in entries if x["doi"]}, "path": str(Path(proj["path"]) / proj.get("bib", "references.bib"))}


def cite_for(hit, bib):
    """The entry of the paper's bibliography this document is, or None: by DOI, else by a title that matches almost word for word."""
    doi = (hit.get("doi") or "").lower().replace("https://doi.org/", "")
    if doi and doi in bib["by_doi"]:
        return bib["by_doi"][doi]
    title = hit.get("title") or ""
    if len(_tokens_of(title)) >= 4:
        for e in bib["entries"]:
            if _same_title(e["title"], title):
                return e
    return None


def annotate(chunks, proj):
    """Add `cite` ({key, command}) to hits whose document is in the paper's bibliography, `in_bib` False and `can_add` (the DOI) to those that are not."""
    bib = paper_bib(proj)
    seen = {}
    for c in chunks:
        if c["doc"] not in seen:
            seen[c["doc"]] = cite_for(c, bib)
        e = seen[c["doc"]]
        c["in_bib"] = bool(e)
        c["cite_key"] = e["key"] if e else ""
        c["can_add"] = bool(not e and c.get("doi"))
    return chunks


def add_entry_from_record(doc_id, bib_path):
    """Copy the BibTeX entry that came with a document (from a .bib in the library) into a paper's bibliography, under a key that is free there. -> key, or None when the document has none"""
    d = get_doc(doc_id)
    if not d or not d["bibtex"]:
        return None
    text = Path(bib_path).read_text(encoding="utf-8", errors="replace")
    key, n = d["bib_key"], 1
    while re.search(r"@\w+\s*\{\s*" + re.escape(key) + r"\s*,", text):
        if d["doi"] and d["doi"].lower() in text.lower():
            return key
        n += 1
        key = f"{d['bib_key']}{chr(96 + n) if n <= 27 else n}"
    entry = d["bibtex"].replace("{" + d["bib_key"] + ",", "{" + key + ",", 1)
    Path(bib_path).write_text(text.rstrip("\n") + "\n\n" + entry + "\n", encoding="utf-8")
    return key


# ------------------------------------------------------------------------------------------------ for a request

WANTS_LIBRARY = re.compile(
    r"\b(?:my|our|the)\s+(?:library|literature|readings?|articles|books|sources|references|bibliography|pdfs|reading list)\b"
    r"|\buse\s+(?:(?:my|our|the)\s+)?(?:literature\s+)?(?:library|literature|readings?|articles|books|sources|references|bibliography|pdfs|reading list)\b"
    r"|\b(?:according to|based on|supported by|backed up by|support (?:this|it|that|these) with|back (?:this|it|that|these) up with|ground (?:this|it|that) in|"
    r"cite|citations?|add (?:a |some )?(?:source|reference|citation)s?|find (?:a |some )?(?:source|reference|evidence|literature)s?|evidence (?:for|from)|literature (?:says|shows|on|about))\b"
    r"|\bwhat does (?:the )?literature say\b", re.I)
FIRST_TAG = 901                      # tags LX901, LX902, ...: the citations a request may add (the original text never has this many protected pieces)
MAX_CITABLE = 9


def wants(instruction):
    return bool(WANTS_LIBRARY.search(instruction or ""))


_CITE_USED = re.compile(r"\\(cite[a-z]*|parencite|textcite|autocite)\*?(?:\s*\[[^\]]*\]){0,2}\s*\{")


def cite_command(proj):
    """The citation command the paper uses most (\\citep, \\cite, ...), so a citation added for the author reads like the others. \\cite when the paper has none yet."""
    from collections import Counter
    n, root = Counter(), Path(proj["path"])
    try:
        for i, p in enumerate(sorted(root.rglob("*.tex"))):
            if i >= 60:
                break
            if any(part.startswith(".") for part in p.relative_to(root).parts):
                continue
            n.update(m.group(1) for m in _CITE_USED.finditer(p.read_text(encoding="utf-8", errors="replace")[:400000]) if m.group(1) != "nocite")
    except OSError:
        pass
    return "\\" + (n.most_common(1)[0][0] if n else "cite")


_ENTRY_CACHE = {}                    # doi -> (when, bibtex or the error): what doi.org said, so the request and the later save agree on a citation key
ENTRY_TTL, ENTRY_FAIL_TTL = 3600, 60


def _fetched(doi):
    import doi_bot
    hit = _ENTRY_CACHE.get(doi)
    if hit and time.time() - hit[0] < (ENTRY_TTL if isinstance(hit[1], str) else ENTRY_FAIL_TTL):
        if isinstance(hit[1], str):
            return hit[1]
        raise hit[1]
    try:
        bib = doi_bot.fetch_bibtex(doi)
    except Exception as e:  # noqa: BLE001
        _ENTRY_CACHE[doi] = (time.time(), e)
        raise
    _ENTRY_CACHE[doi] = (time.time(), bib)
    return bib


def _bib_text(proj):
    try:
        return (Path(proj["path"]) / proj["bib"]).read_text(encoding="utf-8", errors="replace")
    except (OSError, KeyError):
        return ""


def plan_entry(proj, chunk, text, taken=()):
    """The bibliography entry a library document would get, without writing anything. -> {key, bibtex, doi, title, doc} or None when it has neither a record nor a DOI that doi.org knows."""
    import doi_bot
    d = get_doc(chunk["doc"])
    if not d:
        return None
    try:
        if d["bibtex"]:
            key, n = d["bib_key"], 1
            while re.search(r"@\w+\s*\{\s*" + re.escape(key) + r"\s*,", text) or key in taken:
                n += 1
                key = f"{d['bib_key']}{chr(96 + n) if n <= 27 else n}"
            bib = d["bibtex"].replace("{" + d["bib_key"] + ",", "{" + key + ",", 1)
        elif d["doi"]:
            bib, key = doi_bot.prepare_entry(d["doi"], text, taken, fetch=_fetched)
        else:
            return None
    except Exception:  # noqa: BLE001 - no network, an unknown DOI: this document simply cannot be cited now
        return None
    return {"key": key, "bibtex": bib, "doi": d["doi"] or "", "title": d["title"] or chunk.get("title", ""), "doc": chunk["doc"], "label": chunk.get("label", "")}


def offer_new(proj, lib):
    """Documents of the author's library that are not in the paper's bibliography yet but could be (a record or a DOI): give each the key it will have there, so the request can cite
    it. The entries wait in lib["bib_adds"] and are written to the bibliography only when the text that cites them is saved."""
    adds = lib.setdefault("bib_adds", [])
    text = _bib_text(proj)
    citable = {c["cite_key"] for c in lib["chunks"] if c.get("cite_key")}
    for c in lib["chunks"]:
        if c.get("cite_key") or c.get("online"):
            continue
        prior = next((a for a in adds if a["doc"] == c["doc"]), None)
        if prior is None:
            if len(citable) >= MAX_CITABLE:
                continue
            prior = plan_entry(proj, c, text, {a["key"] for a in adds})
            if not prior:
                continue
            adds.append(prior)
        c.update(cite_key=prior["key"], pending=True, can_add=False)
        citable.add(prior["key"])
    return lib


def relevant(proj, instruction, passage="", mode="auto", k=4, budget=1600, facts="", online=()):
    """The parts of the library a request should see, or None. In "auto" a request uses the library when it asks for sources, citations or the author's reading (in the
    request or in the author's answers to the model's questions); "always" searches for every request; "never" for none. Also the references found online and added to this
    paper's bibliography that fit the request, and always the ones the author picked (online: their DOIs).
    -> {chunks: [hit with in_bib / cite_key], bib_adds: [entries that go to the bibliography when the citing text is saved], why, command}"""
    from . import scholar
    ask = f"{instruction}\n{facts}".strip()
    forced = scholar.recall(proj, "", force=online) if online else []
    if not forced and (mode == "never" or (mode != "always" and not wants(ask))):
        return None
    text = f"{ask} {passage[:800]}"
    chunks = annotate(query(text, k=k, budget=budget, per_doc=2)["chunks"], proj) if mode != "never" and _db_path().exists() else []
    have = {c["cite_key"] for c in forced + chunks if c.get("cite_key")}
    found = [] if mode == "never" else [c for c in scholar.recall(proj, text, k=3) if c["cite_key"] not in have and c["id"] not in {x["id"] for x in forced}]
    every = forced + found + chunks
    if not every:
        return None
    return offer_new(proj, {"chunks": every, "why": "picked" if forced else "always" if mode == "always" else "asked", "command": cite_command(proj)})


def request_block(lib):
    """The library's parts as they go into a request. -> (text, tags {LX901: '\\cite{key}'}, shown [{id, label, where, doc, cite_key, tag}])
    A source that is in the paper's bibliography is offered with a tag the model may write to cite it; any other can inform the wording and cannot be cited."""
    tags, shown, lines = {}, [], []
    for n, c in enumerate(lib["chunks"], 1):
        tag = ""
        if c.get("cite_key"):
            tag = next((t for t, v in tags.items() if v.endswith("{" + c["cite_key"] + "}")), "")
            if not tag and len(tags) < MAX_CITABLE:
                tag = f"LX{FIRST_TAG + len(tags)}"
                tags[tag] = (lib.get("command") or "\\cite") + "{" + c["cite_key"] + "}"
        how = f"Cite it with [[{tag}]]." if tag else "Not in the paper's bibliography: it can inform the wording but must not be cited."
        if c.get("online"):
            how += (" Only its abstract is known, found online: state nothing the abstract does not say." if "abstract" in c["where"] else
                    " Only its title is known, found online: do not state what it found or concluded.")
        lines.append(f"[S{n}] {c['label']}, {c['where']}. {how}\n{c['text']}")
        shown.append({"id": c["id"], "doc": c["doc"], "label": c["label"], "where": c["where"], "tokens": c["tokens"], "cite_key": c.get("cite_key", ""), "tag": tag, "title": c["title"],
                      "online": bool(c.get("online")), "pending": bool(c.get("pending"))})
    return "\n\n".join(lines), tags, shown
