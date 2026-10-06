"""Files that are not LaTeX: reference files the author uploads (saved in <paper>/references/ and sent to Overleaf with the paper) and the code and
results that sit around the paper (inside the repository, or in folders the author linked). Each file is split into small, labelled parts so a request
can read just the part it needs ("the Methods of that article", "rows 1 to 25 of results.csv", "the function that computes the score").

Everything here is local and read-only apart from saving an upload. Nothing leaves the computer until a request uses a part, and the request says which parts.
The parts are indexed by paper_index as kind "source" (uploads) and "project" (everything else), next to the manuscript's own parts.
"""
import csv
import hashlib
import html
import io
import json
import os
import re
import statistics
import struct
import unicodedata
import zipfile
from pathlib import Path

from . import chunk_book, chunk_tex, config, store, taxonomy

UPLOAD_DIR = "references"
INSTRUCTION_DIR = "instructions"
MAX_UPLOAD = 25 << 20          # one file, bytes
MAX_FOLDER = 60 << 20          # everything in references/, a warning (Overleaf projects have a size limit too)
MAX_PROJECT_FILE = 2_000_000   # bytes of a data or code file that is read
MAX_FILES = 400                # files indexed besides the uploads
MAX_SCAN = 20000               # directory entries looked at in one folder before stopping
MAX_PARTS_PER_FILE = 80
TARGET, MAX_TOKENS, MIN_TOKENS = 350, 600, 60
LOGIC_VERSION = 1              # bump when the splitting changes, so cached parts are made again

# how each kind of file is read
READ = {"pdf": "pdf", "docx": "docx", "csv": "table", "tsv": "table", "json": "json", "ipynb": "notebook",
        "txt": "text", "md": "text", "markdown": "text", "rst": "text", "log": "text", "yaml": "text", "yml": "text", "toml": "text", "tex": "text", "bib": "text",
        "py": "code", "r": "code", "rmd": "code", "m": "code", "jl": "code", "sql": "code", "sh": "code"}
KEEP = {"png", "jpg", "jpeg", "gif", "svg", "webp", "eps"}        # saved and sent, but not read
UPLOAD_OK = set(READ) | KEEP
PROJECT_OK = {k for k in READ if k not in ("pdf", "docx", "tex", "bib", "log")}   # found around the paper: data, code, notes (not LaTeX, not build output, not figure PDFs)
MATERIAL_OK = PROJECT_OK | {"pdf", "docx"}                                        # in a folder the author chose for this paper: articles, interviews and reports are material too
MATERIAL_BIG = {"pdf": 40 << 20, "docx": 40 << 20}                                # bytes: an article is bigger than a table
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}                                 # figures: read (as pictures) only in the folders the author gave as code and results
ANALYSIS_OK = PROJECT_OK | {"log", "tex"} | IMAGE_EXT                             # code, tables, logs and figures of an analysis; a .tex file there is usually a table the code wrote
ANALYSIS_BIG = {e: 25 << 20 for e in IMAGE_EXT}
MAX_IMAGES = 4                                                                    # pictures that go with one request
IMAGE_SIDE = 1024                                                                 # pixels: a picture is shrunk to this before it goes out
IMAGE_RAW_MAX = 1_200_000                                                         # bytes: without a way to shrink it (no Pillow), a bigger picture stays at home
ROLES = {"material": "data_folders", "analysis": "analysis_folders"}              # the two kinds of folders an author links to a paper
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "env", "site-packages", "build", "dist", "target"}
SKIP_NAMES = {"manifest.txt", "changelog.txt", "license", "license.txt", "license.md", "requirements.txt"}
SECRET_WORDS = ("secret", "password", "passwd", "credential", "token", "apikey", "api_key", "id_rsa", "private_key")
SECRET_EXT = {"pem", "key", "p12", "pfx", "env"}
OUTLINE_NAME = re.compile(r"(?:^|[-_ .])(?:promp(?:t)?|outline(?:d)?|brief|writing[-_ ]?plan|instructions?)(?:[-_ .]|$)", re.I)


class FileProblem(Exception):
    """A reason, in plain words, why a file cannot be added or read."""


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_secret(name):
    low = name.lower()
    return low.startswith(".env") or ext_of(low) in SECRET_EXT or any(w in low for w in SECRET_WORDS)


def is_outline_name(name):
    """Author briefs are directions for writing, never literature evidence."""
    return bool(OUTLINE_NAME.search(Path(str(name or "")).stem))


# ------------------------------------------------------------------------------------------------ saving an upload

def clean_name(filename):
    """A file name that is safe to create inside the paper: no folders, no hidden or odd names, a kind of file we know."""
    base = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r"[^\w.\-+,() ]", "_", base, flags=re.U)
    base = re.sub(r"\s+", " ", base).strip(" .")
    if not base or "." not in base:
        raise FileProblem("That file has no name or no ending (for example .pdf). Rename it and try again.")
    if len(base) > 120:
        stem, e = base.rsplit(".", 1)
        base = stem[:100].rstrip() + "." + e
    if ext_of(base) not in UPLOAD_OK:
        raise FileProblem(f"I cannot add .{ext_of(base)} files. I can add: PDF, Word (.docx), text and Markdown, CSV and TSV tables, JSON, notebooks, code (Python, R, MATLAB, Julia), and pictures. "
                          "For an Excel file, save it as CSV first.")
    if is_secret(base):
        raise FileProblem("That file name looks like it holds a password or key. I do not add those; rename it if it is not one.")
    return base


def _looks_ok(ext, data):
    if ext == "pdf":
        return data.lstrip()[:5] == b"%PDF-"
    if ext == "docx":
        return data[:2] == b"PK"
    if ext in KEEP:
        return True
    return b"\0" not in data[:4096]


def save_upload(proj, filename, data, purpose=None):
    """Writes the file to <paper>/references/ (never over another file: a second file with the same name gets (2)). -> {rel, name, size, duplicate}"""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise FileProblem("The file is empty.")
    if len(data) > MAX_UPLOAD:
        raise FileProblem(f"That file is {len(data) // (1 << 20)} MB; the limit is {MAX_UPLOAD >> 20} MB. Overleaf projects have a size limit too, so send only the pages you need.")
    name = clean_name(filename)
    if not _looks_ok(ext_of(name), data):
        raise FileProblem(f"That does not look like a real .{ext_of(name)} file.")
    if purpose not in (None, "evidence", "outline"):
        raise FileProblem("Choose whether this file is evidence or a writing outline.")
    root = Path(proj["path"])
    if purpose == "outline" or (purpose is None and is_outline_name(name)):
        folder = config.ensure_paper_dir(proj) / INSTRUCTION_DIR
        folder.mkdir(exist_ok=True)
        rel = f".overleaf-skill/{INSTRUCTION_DIR}/{{}}"
        digest = hashlib.sha256(data).hexdigest()
        stem, e = name.rsplit(".", 1)
        for i in range(1, 100):
            cand = folder / (name if i == 1 else f"{stem} ({i}).{e}")
            if not cand.exists() and not cand.is_symlink():
                tmp = cand.with_name("." + cand.name + ".part")
                tmp.write_bytes(bytes(data))
                os.replace(tmp, cand)
                break
            if cand.is_file() and hashlib.sha256(cand.read_bytes()).hexdigest() == digest:
                return {"rel": rel.format(cand.name), "name": cand.name, "size": len(data), "duplicate": True, "outline": True}
        else:
            raise FileProblem("There are too many files with that name already.")
        return {"rel": rel.format(cand.name), "name": cand.name, "size": len(data), "duplicate": False, "outline": True}
    folder = root / UPLOAD_DIR
    if folder.exists() and not folder.is_dir():
        raise FileProblem(f"There is a file called {UPLOAD_DIR} in your project, so I cannot make the folder for references. Rename that file first.")
    folder.mkdir(exist_ok=True)
    if folder.resolve().parent != root.resolve():
        raise FileProblem("The references folder is not inside the paper's folder, so I did not use it.")
    digest = hashlib.sha256(data).hexdigest()
    stem, e = name.rsplit(".", 1)
    for i in range(1, 100):
        cand = folder / (name if i == 1 else f"{stem} ({i}).{e}")
        if not cand.exists() and not cand.is_symlink():
            tmp = cand.with_name("." + cand.name + ".part")
            tmp.write_bytes(bytes(data))
            os.replace(tmp, cand)
            break
        if cand.is_file() and hashlib.sha256(cand.read_bytes()).hexdigest() == digest:
            return {"rel": f"{UPLOAD_DIR}/{cand.name}", "name": cand.name, "size": len(data), "duplicate": True}
    else:
        raise FileProblem("There are too many files with that name already.")
    total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    return {"rel": f"{UPLOAD_DIR}/{cand.name}", "name": cand.name, "size": len(data), "duplicate": False,
            "warning": f"The references folder is now {total >> 20} MB. Overleaf projects have a size limit; remove files you no longer need." if total > MAX_FOLDER else ""}


def remove_upload(proj, rel):
    """Deletes an uploaded evidence file or a local author outline. -> True when something was deleted"""
    root = Path(proj["path"]).resolve()
    p = (root / rel).resolve()
    refs = (root / UPLOAD_DIR).resolve()
    outlines = (config.paper_dir(proj) / INSTRUCTION_DIR).resolve()
    if p.parent not in (refs, outlines) or not p.is_file() or p.is_symlink():
        raise FileProblem("I can only remove files that were added as evidence or author outlines.")
    p.unlink()
    return True


# ------------------------------------------------------------------------------------------------ what there is to read

SYSTEM_PREFIXES = ("/etc", "/proc", "/sys", "/dev", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var", "/run", "/boot", "/opt", "/root", "/srv", "/System", "/Library", "/private")


def unsafe_folder(p):
    """Why a linked folder is refused (it is too broad, a system folder, hidden, or holds this app's own settings), or ''."""
    parts = p.parts
    if str(p).startswith(SYSTEM_PREFIXES):
        return "That is a system folder."
    if len(parts) < 4:                                             # '/', '/Users', '/Users/me': far more than a project
        return "That is a very broad folder (it would include everything in it). Pick the folder of this analysis."
    if any(x.startswith(".") for x in parts[1:]):
        return "Hidden folders are never read."
    data = config.data_dir().resolve()
    here = p.resolve()
    if here == data or data in here.parents or here in data.parents:
        return "That folder holds this app's own settings and keys."
    return ""


def set_folders(proj, wanted, role="material"):
    """Save the list of linked folders for a paper (`material`: notes, articles, reports; `analysis`: code, tables, logs and figures). Each is written as an absolute path;
    a folder that is refused stays in the list with its reason (see folders())."""
    cleaned = []
    for x in wanted:
        x = str(x or "").strip()
        if not x:
            continue
        if len(x) > 500 or "\n" in x or "\0" in x:
            raise FileProblem("That does not look like a folder path.")
        x = str(Path(x).expanduser()) if x.startswith("~") else x
        x = x.rstrip("/\\") or x
        if x not in cleaned:
            cleaned.append(x)
    if len(cleaned) > 8:
        raise FileProblem("At most 8 folders can be linked to one paper.")
    config.set_paper_setting(proj, ROLES[role], cleaned)
    return folders(proj, role)


def link_folder(proj, path, role="material"):
    """Terminal use: link one folder that exists on this computer. -> the folder's status {path, ok, note}"""
    p = Path(str(path)).expanduser().resolve()
    if not p.is_dir():
        raise FileProblem(f"{p} is not a folder.")
    why = unsafe_folder(p)
    if why:
        raise FileProblem(why)
    cur = [f["path"] for f in folders(proj, role)]
    set_folders(proj, cur + [str(p)] if str(p) not in cur else cur, role)
    return next(f for f in folders(proj, role) if f["path"] == str(p))


def unlink_folder(proj, path, role="material"):
    p = str(Path(str(path)).expanduser().resolve())
    cur = [f["path"] for f in folders(proj, role)]
    set_folders(proj, [x for x in cur if x not in (p, str(path))], role)


def folders(proj, role=None):
    """The folders the author linked for this paper: [{path, ok, note, role}]. `material` folders hold notes, articles and reports; `analysis` folders hold the code, tables,
    logs and figures of the analysis, which live outside the Overleaf project. role=None: both."""
    out = []
    for r, key in ROLES.items():
        if role not in (None, r):
            continue
        for raw in config.paper_settings(proj).get(key) or []:
            p = Path(str(raw)).expanduser()
            if not p.is_absolute():
                out.append({"path": str(raw), "ok": False, "note": "Use the full path of the folder.", "role": r})
            elif unsafe_folder(p):
                out.append({"path": str(p), "ok": False, "note": unsafe_folder(p), "role": r})
            elif not p.is_dir():
                out.append({"path": str(p), "ok": False, "role": r, "note": "I cannot see this folder. If the app runs in Docker, share it (read-only is enough) with: ./ui.sh folders add --read-only <folder>, then ./ui.sh down and ./ui.sh up."})
            else:
                out.append({"path": str(p), "ok": True, "note": "", "role": r})
    return out


def short_name(proj, shown):
    """How a file is named to a model (and in what it sees): its path inside the project, or the linked folder's name and the path inside it; never the path on the author's computer."""
    if not shown.startswith("/"):
        return shown
    for f in folders(proj):
        base = f["path"].rstrip("/")
        if f["ok"] and shown.startswith(base + "/"):
            return base.rsplit("/", 1)[-1] + shown[len(base):]
    return "/".join(shown.split("/")[-2:])


def _where(proj, row):
    sp = row["section_path"]
    return short_name(proj, row["file"]) + sp[len(row["file"]):] if sp.startswith(row["file"]) else sp


def _walk(base, exts, limit_files, rel_to=None, skip_top=(), big=None):
    """Files under `base` of the wanted kinds, without hidden or build folders, secrets or links. -> (paths, stopped_early)"""
    found, seen = [], 0
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS and not (Path(dirpath) == Path(base) and d in skip_top))
        for fn in sorted(filenames):
            seen += 1
            if seen > MAX_SCAN or len(found) >= limit_files:
                return found, True
            p = Path(dirpath) / fn
            if fn.startswith(".") or fn.lower() in SKIP_NAMES or is_secret(fn) or ext_of(fn) not in exts or p.is_symlink():
                continue
            try:
                if p.stat().st_size > (big or {}).get(ext_of(fn), MAX_PROJECT_FILE):
                    found.append((p, "too big"))
                    continue
            except OSError:
                continue
            found.append((p, ""))
    return found, False


def discover(proj):
    """[(path, shown_name, kind, note, role)] of everything to read: uploads (kind source), then files in the repository and in linked folders (kind project; role is
    "project", "material" or "analysis")."""
    root = Path(proj["path"])
    out, notes = [], []
    # Author outlines live in the local, git-ignored paper state. They are
    # indexed so a draft can follow them, but are never sources or citations.
    instructions = config.paper_dir(proj) / INSTRUCTION_DIR
    if instructions.is_dir() and not instructions.is_symlink():
        for p in sorted(instructions.iterdir()):
            if p.is_file() and not p.is_symlink() and not p.name.startswith(".") and ext_of(p.name) in READ:
                out.append((p, f".overleaf-skill/{INSTRUCTION_DIR}/{p.name}", "source", "", "outline"))
    up = root / UPLOAD_DIR
    if up.is_dir() and not up.is_symlink():
        for p in sorted(up.iterdir()):
            if p.is_file() and not p.is_symlink() and not p.name.startswith(".") and ext_of(p.name) in UPLOAD_OK:
                role = "outline" if is_outline_name(p.name) else "source"  # legacy outlines uploaded before this distinction
                out.append((p, f"{UPLOAD_DIR}/{p.name}", "source", "" if p.stat().st_size <= MAX_UPLOAD else "too big", role))
    budget = MAX_FILES
    found, early = _walk(root, PROJECT_OK, budget, skip_top=(UPLOAD_DIR,))
    for p, note in found:
        out.append((p, str(p.relative_to(root)), "project", note, "project"))
    budget -= len(found)
    if early:
        notes.append("The repository has more files than I read; I stopped early.")
    for f in folders(proj):
        if f["ok"] and budget > 0:
            exts, big = (ANALYSIS_OK, ANALYSIS_BIG) if f["role"] == "analysis" else (MATERIAL_OK, MATERIAL_BIG)
            found, early = _walk(f["path"], exts, budget, big=big)
            for p, note in found:
                out.append((p, str(p), "project", note, f["role"]))
            budget -= len(found)
            if early:
                notes.append(f"The folder {f['path']} has more files than I read; I stopped early.")
    return out, notes


# ------------------------------------------------------------------------------------------------ splitting into parts

_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-Z][^\n]{2,80})$")
_NAMED = re.compile(r"^(abstract|introduction|background|related work|literature review|methods?|materials and methods|methodology|study area|data|data and methods|"
                    r"results?|results and discussion|discussion|conclusions?|conclusion and future work|limitations|acknowledge?ments?|references|bibliography|"
                    r"appendix[ a-z0-9]*|supplementary[ a-z]*)$", re.I)
_STOP_HEADS = {"references", "bibliography", "acknowledgements", "acknowledgments", "acknowledgement", "acknowledgment"}


def heading_of(par):
    """The heading a short paragraph is, or None."""
    t = par.strip()
    if not t or "\n" in t or len(t) > 90 or t[-1] in ".,;:" or len(t.split()) > 12:
        return None
    m = _NUMBERED.match(t)
    if m:
        return t
    if _NAMED.match(t):
        return t
    letters = [c for c in t if c.isalpha()]
    if 3 <= len(t) <= 60 and letters and all(c.isupper() for c in letters) and len(letters) >= 4:
        return t.title()
    return None


def _fix_par(text):
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)          # a word broken across lines
    return re.sub(r"\s*\n\s*", " ", text).strip()


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", text) if s]


def _pack(blocks):
    """blocks: [{text, lo, hi, head}] in order. -> [{text, lo, hi, head}] of about TARGET tokens, never mixing two headings, splitting a long block by sentences."""
    flat = []
    for b in blocks:
        if store.approx_tokens(b["text"]) <= MAX_TOKENS:
            flat.append(b)
            continue
        cur = ""
        for s in _sentences(b["text"]) or [b["text"]]:
            while len(s) > MAX_TOKENS * 4:                     # a block with no sentence ends at all
                flat.append({**b, "text": s[: MAX_TOKENS * 4]})
                s = s[MAX_TOKENS * 4:]
            if cur and store.approx_tokens(cur + " " + s) > MAX_TOKENS:
                flat.append({**b, "text": cur})
                cur = s
            else:
                cur = (cur + " " + s).strip()
        if cur:
            flat.append({**b, "text": cur})
    out, cur = [], None
    for b in flat:
        if cur and cur["head"] == b["head"] and store.approx_tokens(cur["text"]) < TARGET and store.approx_tokens(cur["text"] + "\n\n" + b["text"]) <= MAX_TOKENS:
            cur = {"text": cur["text"] + "\n\n" + b["text"], "lo": min(cur["lo"], b["lo"]), "hi": max(cur["hi"], b["hi"]), "head": cur["head"]}
        else:
            if cur:
                out.append(cur)
            cur = dict(b)
    if cur:
        out.append(cur)
    # a scrap that is too small to stand alone joins the part before it when it is under the same heading
    merged = []
    for c in out:
        if merged and store.approx_tokens(c["text"]) < MIN_TOKENS and merged[-1]["head"] == c["head"]:
            merged[-1] = {"text": merged[-1]["text"] + "\n\n" + c["text"], "lo": merged[-1]["lo"], "hi": max(merged[-1]["hi"], c["hi"]), "head": c["head"]}
        else:
            merged.append(c)
    return merged


def _paragraph_blocks(lines, heading=None):
    """Blank-line separated paragraphs of `lines` with their line numbers, each tagged with the heading above it."""
    blocks, head, start, buf, skipping = [], "", None, [], False

    def flush(end):
        nonlocal buf, start
        if buf:
            text = _fix_par("\n".join(buf)) if heading else "\n".join(buf).strip()
            if text and not skipping:
                blocks.append({"text": text, "lo": start, "hi": end, "head": head})
        buf, start = [], None

    for i, ln in enumerate(lines, 1):
        md = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", ln)
        h = md.group(2).strip() if md else (heading(ln) if heading else None)
        if h:
            flush(i - 1)
            head = h
            skipping = h.lower().strip(" .:0123456789") in _STOP_HEADS
            continue
        if not ln.strip():
            flush(i - 1)
        else:
            if start is None:
                start = i
            buf.append(ln)
    flush(len(lines))
    return blocks


def _tags_for(head):
    return taxonomy.classify(head) if head else []


def _mk(paper, kind, rel, n, loc, c, noun="lines"):
    head = c.get("head") or ""
    section = f"{rel} · {loc}" + (f" · {head}" if head else "")
    return {"id": f"P:{paper}:{kind}:{chunk_tex.slug(rel, 70)}#{n}", "kind": kind, "file": rel, "line_start": c["lo"], "line_end": c["hi"], "level": 2,
            "title": head or rel, "section_path": section, "part": None, "tags": _tags_for(head), "text": c["text"],
            "plain": re.sub(r"\s+", " ", c["text"]).strip(), "tokens": store.approx_tokens(c["text"]), "hash": store.sha256_text(c["text"]), "ord": n}


def _read_text(path, limit=MAX_PROJECT_FILE * 3):
    return path.read_bytes()[:limit].decode("utf-8", "replace")


def parts_pdf(path):
    pages = chunk_book.extract_pages(path, timeout=120)
    if not any(p.strip() for p in pages):
        raise FileProblem("This PDF has no text I can read (a scan?). Run it through OCR first, or paste the parts you need into the chat.")
    blocks, head, skipping = [], "", False
    for pno, page in enumerate(pages, 1):
        for par in re.split(r"\n\s*\n", page):
            par = par.strip()
            if not par:
                continue
            h = heading_of(par)
            if h:
                head = h
                skipping = h.lower().strip(" .:0123456789") in _STOP_HEADS
                continue
            text = _fix_par(par)
            if skipping or len(text) < 25 or re.fullmatch(r"[\d\s.\-–/]+", text):    # reference lists, page numbers, stray fragments
                continue
            blocks.append({"text": text, "lo": pno, "hi": pno, "head": head})
    return _pack(blocks), "pages"


def parts_docx(path):
    with zipfile.ZipFile(path) as z:
        try:
            info = z.getinfo("word/document.xml")
        except KeyError:
            raise FileProblem("This does not look like a Word (.docx) file.") from None
        if info.file_size > 40 << 20:
            raise FileProblem("This Word file is too large to read.")
        xml = z.open(info).read(41 << 20).decode("utf-8", "replace")         # never more than the size limit, whatever the file claims
    blocks, head = [], ""
    for i, m in enumerate(re.finditer(r"<w:p[ >].*?</w:p>", xml, re.S), 1):
        pxml = m.group(0)
        text = html.unescape("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", pxml, re.S))).strip()
        if not text:
            continue
        if re.search(r'<w:pStyle w:val="(?:Heading|Title)[^"]*"', pxml):
            head = text
            continue
        blocks.append({"text": text, "lo": i, "hi": i, "head": head})
    return _pack(blocks), "paragraphs"


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _fmt(x):
    return f"{x:.4g}"


def parts_table(path, delim):
    text = _read_text(path)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise FileProblem("This table has no rows.")
    head, body = [c.strip() or f"column {i + 1}" for i, c in enumerate(rows[0])], rows[1:]
    cols = []
    for j, name in enumerate(head):
        vals = [r[j].strip() for r in body if j < len(r) and r[j].strip()]
        nums = [_num(v) for v in vals]
        if vals and sum(n is not None for n in nums) >= 0.8 * len(vals):
            ns = [n for n in nums if n is not None]
            cols.append(f"{name} (numbers: min {_fmt(min(ns))}, max {_fmt(max(ns))}, mean {_fmt(statistics.fmean(ns))})")
        else:
            d = sorted(set(vals))
            cols.append(f"{name} (text: {len(d)} different" + (f", for example {', '.join(x[:20] for x in d[:5])}" if d else "") + ")")
    cut = MAX_PARTS_PER_FILE * 25
    summary = (f"Table {path.name}: {len(body)} rows and {len(head)} columns.\nColumns: " + "; ".join(cols) + "."
               + (f"\nOnly the first {cut} rows can be read." if len(body) > cut else ""))
    out = [{"text": summary, "lo": 1, "hi": 1, "head": "Summary of the table"}]
    block = 25
    for a in range(0, min(len(body), cut), block):
        chunk = body[a:a + block]
        lines = [delim.join(head)] + [delim.join(c[:80] for c in r) for r in chunk]
        out.append({"text": "\n".join(lines), "lo": a + 2, "hi": a + 1 + len(chunk), "head": f"Rows {a + 1} to {a + len(chunk)}"})
    return out, "rows"


def parts_json(path):
    try:
        data = json.loads(_read_text(path))
    except ValueError:
        raise FileProblem("This JSON file cannot be read (is it complete?).") from None
    lines = json.dumps(data, indent=1, ensure_ascii=False).splitlines()
    out = []
    for a in range(0, len(lines), 40):
        part = lines[a:a + 40]
        out.append({"text": "\n".join(part), "lo": a + 1, "hi": a + len(part), "head": ""})
    return out, "lines"


def parts_notebook(path):
    try:
        nb = json.loads(_read_text(path, MAX_PROJECT_FILE * 4))
        cells = nb["cells"]
    except (ValueError, KeyError, TypeError):
        raise FileProblem("This notebook cannot be read.") from None
    blocks = []
    for i, c in enumerate(cells, 1):
        src = "".join(c.get("source") or [])
        if c.get("cell_type") == "code":
            outs = []
            for o in c.get("outputs") or []:
                t = "".join(o.get("text") or []) or "".join((o.get("data") or {}).get("text/plain") or [])
                if t.strip():
                    outs.append(t.strip()[:1500])
            src = "[code]\n" + src.strip() + ("\n[output]\n" + "\n".join(outs) if outs else "")
        elif c.get("cell_type") == "markdown":
            src = src.strip()
        else:
            continue
        if src.strip():
            blocks.append({"text": src, "lo": i, "hi": i, "head": ""})
    return _pack(blocks), "cells"


_DEF = {"py": r"^(?:async\s+def|def|class)\s+(\w+)", "r": r"^(\w[\w.]*)\s*(?:<-|=)\s*function", "rmd": r"^#{1,4}\s+(.+)", "m": r"^function\b.*?(\w+)\s*\(",
        "jl": r"^function\s+(\w+)", "sql": r"^(?:create|select|with)\b", "sh": r"^(\w+)\s*\(\)"}


def parts_code(path, ext):
    lines = _read_text(path).splitlines()
    rx = re.compile(_DEF.get(ext, r"^$"), re.I if ext == "sql" else 0)
    blocks, head, start, buf = [], "", 1, []

    def flush(end):
        nonlocal buf
        text = "\n".join(buf).strip("\n")
        if text.strip():
            blocks.append({"text": text, "lo": start, "hi": end, "head": head})
        buf = []

    for i, ln in enumerate(lines, 1):
        m = rx.match(ln)
        if m or re.match(r"^#\s*%%", ln):
            flush(i - 1)
            start, head = i, (m.group(1) if m and m.groups() else m.group(0) if m else ln.strip("# %").strip())
        buf.append(ln)
    flush(len(lines))
    packed = []
    for b in blocks:                       # long functions: windows of whole lines
        ls = b["text"].split("\n")
        if store.approx_tokens(b["text"]) <= MAX_TOKENS:
            packed.append(b)
            continue
        w = max(10, MAX_TOKENS * 4 // max(1, int(len(b["text"]) / max(1, len(ls)))))
        for a in range(0, len(ls), w):
            packed.append({"text": "\n".join(ls[a:a + w]), "lo": b["lo"] + a, "hi": b["lo"] + min(len(ls), a + w) - 1, "head": b["head"]})
    return _pack_code(packed), "lines"


def _pack_code(blocks):
    out, cur = [], None
    for b in blocks:
        if cur and store.approx_tokens(cur["text"]) < TARGET and store.approx_tokens(cur["text"] + "\n\n" + b["text"]) <= MAX_TOKENS:
            cur = {"text": cur["text"] + "\n\n" + b["text"], "lo": cur["lo"], "hi": b["hi"], "head": cur["head"] if cur["head"] == b["head"] else (cur["head"] + " / " + b["head"]).strip(" /")}
        else:
            if cur:
                out.append(cur)
            cur = dict(b)
    if cur:
        out.append(cur)
    return out


def image_size(path):
    """(width, height) of a PNG, GIF, JPEG or WEBP, read from its first bytes, or None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
        return struct.unpack("<HH", head[6:10])
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            m = head[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return (w, h)
            if m == 0xD8 or m == 0x01 or 0xD0 <= m <= 0xD7 or m == 0xFF:
                i += 1 if m == 0xFF else 2
                continue
            i += 2 + struct.unpack(">H", head[i + 2:i + 4])[0]
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP" and len(head) >= 30:
        kind = head[12:16]
        if kind == b"VP8X":
            return (1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little"))
        if kind == b"VP8L" and head[20] == 0x2F:
            bits = int.from_bytes(head[21:25], "little")
            return (1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF))
        if kind == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
            return (int.from_bytes(head[26:28], "little") & 0x3FFF, int.from_bytes(head[28:30], "little") & 0x3FFF)
    return None


def parts_image(path):
    """A figure of the author's analysis is one part: its name and size, which is what a search can use. The picture itself goes to the model only when a request needs it."""
    size = image_size(path)
    words = re.sub(r"[_\-.]+", " ", path.stem).strip()
    text = f"Picture: {path.name}" + (f" ({size[0]} by {size[1]} pixels)" if size else "") + f". It is in the folder {path.parent.name}. What its name says: {words}."
    return [{"text": text, "lo": 1, "hi": 1, "head": ""}], "pictures"


def image_payload(path):
    """A picture ready to go out with a request: shrunk to IMAGE_SIDE pixels, as base64. -> {name, mime, b64, bytes} or None when it cannot be read or is too big to send."""
    import base64
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    ext = ext_of(path.name)
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
    try:
        from PIL import Image
    except ImportError:
        return {"name": path.name, "mime": mime, "b64": base64.b64encode(raw).decode(), "bytes": len(raw)} if len(raw) <= IMAGE_RAW_MAX else None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        if max(im.size) > IMAGE_SIDE:
            im.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
        out = io.BytesIO()
        if ext in ("jpg", "jpeg"):
            im.convert("RGB").save(out, "JPEG", quality=85)
            mime = "image/jpeg"
        else:
            im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(out, "PNG", optimize=True)
            mime = "image/png"
            if out.tell() > 3_000_000:                                  # a photograph saved as PNG: JPEG is far smaller
                out = io.BytesIO()
                im.convert("RGB").save(out, "JPEG", quality=85)
                mime = "image/jpeg"
    except Exception:  # noqa: BLE001  (a damaged or odd file: it is left out, the request goes on)
        return None
    data = out.getvalue()
    return {"name": path.name, "mime": mime, "b64": base64.b64encode(data).decode(), "bytes": len(data)}


def parts_of(path):
    """-> (chunks-without-ids [{text, lo, hi, head}], noun) for one file, or raises FileProblem."""
    ext = ext_of(path.name)
    kind = READ.get(ext)
    if ext in IMAGE_EXT:
        return parts_image(path)
    if kind == "pdf":
        return parts_pdf(path)
    if kind == "docx":
        return parts_docx(path)
    if kind == "table":
        return parts_table(path, "\t" if ext == "tsv" else ",")
    if kind == "json":
        return parts_json(path)
    if kind == "notebook":
        return parts_notebook(path)
    if kind == "code":
        return parts_code(path, ext)
    if kind == "text":
        text = _read_text(path)
        heading = heading_of if ext in ("txt", "rst", "log") else None
        parts = _pack(_paragraph_blocks(text.splitlines(), heading=heading))
        return parts, "lines"
    return [], "lines"          # pictures: kept, not read


def _location(noun, c):
    a, b = c["lo"], c["hi"]
    word = {"pages": "page", "rows": "row", "cells": "cell", "paragraphs": "paragraph", "lines": "line", "pictures": "picture"}[noun]
    return f"{word}{'s' if a != b else ''} {a}" + (f" to {b}" if a != b else "")


# ------------------------------------------------------------------------------------------------ for the index

def _cache_path(proj, path):
    return config.paper_dir(proj) / "filecache" / (hashlib.sha1(str(path).encode()).hexdigest() + ".json")


def gather(name, proj):
    """-> (chunks, files, warnings). `files` lists every file considered: {file, kind, size, parts, tokens, note}."""
    chunks, files, warnings = [], [], []
    found, notes = discover(proj)
    warnings += notes
    root = Path(proj["path"])
    for path, shown, kind, note, role in found:
        try:
            st = path.stat()
        except OSError:
            continue
        ext = ext_of(path.name)
        rec = {"file": shown, "kind": kind, "role": role, "size": st.st_size, "parts": 0, "tokens": 0, "note": note, "reads": ext in READ or (role == "analysis" and ext in IMAGE_EXT)}
        if note == "too big":
            top = (ANALYSIS_BIG if role == "analysis" else MATERIAL_BIG).get(ext, MAX_PROJECT_FILE) if kind == "project" and shown.startswith("/") else MAX_PROJECT_FILE
            rec["note"] = f"Too big to read (over {max(1, top // 1_000_000)} MB)." if kind == "project" else f"Too big to read (over {MAX_UPLOAD >> 20} MB)."
            files.append(rec)
            continue
        if not rec["reads"]:
            rec["note"] = "Kept in the project, not read (pictures are not read)."
            files.append(rec)
            continue
        sig = [LOGIC_VERSION, name, st.st_size, st.st_mtime_ns]
        cache = _cache_path(proj, path)
        got = None
        try:
            c = json.loads(cache.read_text())
            if c.get("sig") == sig:
                got = c
        except (OSError, ValueError):
            pass
        if got is None:
            try:
                parts, noun = parts_of(path)
                made = [_mk(name, kind, shown, n, _location(noun, p), p) for n, p in enumerate(parts[:MAX_PARTS_PER_FILE], 1)]
                got = {"sig": sig, "chunks": made, "note": ("Only the first %d parts are read." % MAX_PARTS_PER_FILE) if len(parts) > MAX_PARTS_PER_FILE else ""}
            except FileProblem as e:
                got = {"sig": sig, "chunks": [], "note": str(e)}
            except (OSError, zipfile.BadZipFile, RuntimeError, chunk_book.ToolMissing) as e:
                got = {"sig": sig, "chunks": [], "note": "Could not be read: " + str(e)[:160]}
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(got))
            except OSError:
                pass
        chunks += got["chunks"]
        rec.update(parts=len(got["chunks"]), tokens=sum(c["tokens"] for c in got["chunks"]), note=got.get("note") or rec["note"])
        files.append(rec)
    return chunks, files, warnings


def fingerprint_rows(proj):
    root = Path(proj["path"])
    rows = []
    found, _ = discover(proj)
    for path, shown, kind, note, role in found:
        try:
            st = path.stat()
        except OSError:
            continue
        rows.append((shown, st.st_mtime_ns, st.st_size))
    return rows + [("linked:" + f["path"], int(f["ok"]), 0) for f in folders(proj)] + [("logic", LOGIC_VERSION, 0)]


# ------------------------------------------------------------------------------------------------ for a request

WANTS_FILES = re.compile(
    r"\b(?:my|these|those|attached|uploaded|added)\s+(?:files?|data|datasets?|results|tables?|csv|spreadsheets?|code|scripts?|notebooks?|pdfs?|papers?|articles?|references?|documents?|sources?|notes)\b"
    r"|\b(?:the|this|that)\s+(?:files?|csv|spreadsheet|notebook|pdf|uploads?|uploaded\s+\w+|attached\s+\w+)\b"
    r"|\b(?:based on|according to|using|from|check (?:it )?against|compare (?:it )?(?:with|to))\s+(?:my|the|these|those|this|that|attached|uploaded)?\s*(?:files?|data|datasets?|results|tables?|csv|spreadsheets?|code|scripts?|notebooks?|pdfs?|papers?|articles?|references?|documents?|sources?|uploads?)\b"
    r"|\buse my files\b|\bin (?:my|the) (?:code|notebook|files?)\b", re.I)


GENERIC_STEMS = {"results", "result", "data", "dataset", "table", "tables", "figure", "figures", "notes", "readme", "summary", "model", "models", "paper", "code", "output",
                 "outputs", "analysis", "index", "main", "test", "tests", "config", "draft", "report", "methods", "method", "discussion", "metrics", "scores", "stats", "article"}


def mentions(files, text):
    """Files the request names: the whole file name, or the name without its ending when that is not an everyday word (a file called results.csv is named by
    "results.csv", not by every request that talks about results). Whole words only; case does not matter."""
    low = text.lower()
    hit = []
    for f in files:
        base = f["file"].rsplit("/", 1)[-1].lower()
        stem = base.rsplit(".", 1)[0]
        whole = re.search(r"(?<![\w-])" + re.escape(base) + r"(?![\w-])", low)
        short = len(stem) >= 4 and stem not in GENERIC_STEMS and re.search(r"(?<![\w-])" + re.escape(stem) + r"(?![\w-])", low)
        if whole or short:
            hit.append(f["file"])
    return hit


def listing(name, proj):
    """What the index knows about the author's files: [{file, kind, size, parts, tokens, note, reads}] (uploads first)."""
    from . import paper_index
    con = paper_index.open_index(name, proj)
    try:
        got = store.meta_get(con, "sources", [])
    finally:
        con.close()
    return sorted(got, key=lambda f: (f["kind"] != "source", f["file"].lower()))


def parts_of_file(name, proj, rel):
    """The parts of one file, in order, without their full text: [{id, where, tokens, preview}]."""
    from . import paper_index
    con = paper_index.open_index(name, proj)
    try:
        rows = con.execute("SELECT id, section_path, tokens, plain, line_start, line_end FROM chunks WHERE kind IN ('source','project') AND file=? ORDER BY ord", (rel,)).fetchall()
    finally:
        con.close()
    return [{"id": r["id"], "where": r["section_path"].split(" · ", 1)[-1], "tokens": r["tokens"], "preview": " ".join(r["plain"].split())[:220]} for r in rows]


WANTS_ANALYSIS = re.compile(
    r"\b(?:my|our|these|those)\s+(?:code|scripts?|notebooks?|analys[ie]s|outputs?|results?|findings|figures?|plots?|graphs?|charts?|images?|pictures?|metrics|logs?)\b"
    r"|\b(?:based on|according to|from|using|given|explain|interpret|describe|discuss|summari[sz]e|compare|look at|read)\s+(?:my|the|our|these|those|this|that)?\s*(?:code|scripts?|notebooks?|analys[ie]s|outputs?|results|findings|figures?|plots?|graphs?|charts?|images?|pictures?|metrics|logs?)\b"
    r"|\bwhat\s+(?:do|does)\s+(?:the|my|these|those)\s+(?:results?|figures?|plots?|graphs?|charts?|numbers|tables?|outputs?)\s+(?:show|say|mean|suggest|tell)\b", re.I)


def role_of(f):
    return f.get("role") or ("source" if f.get("kind") == "source" else "project")


def _pick_images(use, text, named, every):
    """Which pictures go with a request: the ones it names, then the ones whose file name shares words with it; when the analysis has only a few, all of them if the
    request is about the analysis. At most MAX_IMAGES."""
    from . import retrieve
    pics = [f for f in use if ext_of(f["file"]) in IMAGE_EXT and f.get("role") == "analysis"]
    chosen = [f for f in pics if f["file"] in named]
    terms = set(retrieve.fts_terms(text))
    for f in pics:
        if f not in chosen and terms & set(retrieve.fts_terms(re.sub(r"[_\-.]+", " ", f["file"].rsplit("/", 1)[-1].rsplit(".", 1)[0]))):
            chosen.append(f)
    if every and not chosen and len(pics) <= MAX_IMAGES:
        chosen = list(pics)
    return chosen[:MAX_IMAGES]


FILL_ORDER = ("json", "yaml", "yml", "toml", "csv", "tsv", "tex", "log", "txt", "md", "ipynb")           # what states a result before what produced it: numbers first, code last


def relevant(name, proj, instruction, passage="", mode="auto", budget=1800, limit=5, analysis_mode="auto", fill=False):
    """The parts of the author's files a request should see, or None. A file is used when the request names it, when it talks about the author's files or data
    (see WANTS_FILES) or, for the code and results folders, about the analysis (see WANTS_ANALYSIS), or always when the setting says so; never when it says never.
    `mode` is the setting for the notes, tables and uploads, `analysis_mode` the one for the code and results folders.
    fill: when the parts that match leave room in the budget, the rest of the analysis's tables, results files and logs fill it (used when new text is drafted from the analysis,
    where the numbers matter more than a good match).
    -> {"chunks": [...], "files": [names], "images": [{file, name}], "why": "named" | "asked" | "always"}"""
    from . import paper_index, retrieve
    info = [f for f in listing(name, proj) if f.get("parts") and role_of(f) != "outline" and (analysis_mode if role_of(f) == "analysis" else mode) != "never"]
    if not info:
        return None
    named = mentions(info, instruction)
    asked_files, asked_analysis = bool(WANTS_FILES.search(instruction)), bool(WANTS_ANALYSIS.search(instruction))

    def active(f):
        analysis = role_of(f) == "analysis"
        return (analysis_mode if analysis else mode) == "always" or f["file"] in named or (asked_analysis if analysis else asked_files)
    use = [f for f in info if active(f)]
    if not use:
        return None
    allowed = {f["file"] for f in use}
    terms = retrieve.fts_terms(instruction + " " + passage[:800])
    con = paper_index.open_index(name, proj)
    chosen, used, seen = [], 0, set()

    def take(row):
        nonlocal used
        if row is None or row["id"] in seen or row["file"] not in allowed or ext_of(row["file"]) in IMAGE_EXT or (chosen and used + row["tokens"] > budget):
            return
        seen.add(row["id"])
        used += row["tokens"]
        chosen.append({"id": row["id"], "file": row["file"], "where": _where(proj, row), "tokens": row["tokens"], "text": row["text"], "kind": row["kind"], "ord": row["ord"]})

    try:
        for rel in [n for n in named if n in allowed and ext_of(n) not in IMAGE_EXT][:3]:        # a named file: all of it when it fits, otherwise its summary and the parts that match
            rows = con.execute("SELECT * FROM chunks WHERE kind IN ('source','project') AND file=? ORDER BY ord", (rel,)).fetchall()
            if sum(r["tokens"] for r in rows) <= budget // max(1, len(named[:3])):
                for r in rows:
                    take(r)
                continue
            if rows:
                take(rows[0])
            if terms:
                ids = [r[0] for r in con.execute(
                    "SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.chunk_id WHERE chunks_fts MATCH ? AND c.file=? ORDER BY bm25(chunks_fts, 0.0, 4.0, 1.0) LIMIT ?",
                    (retrieve.fts_match(terms), rel, limit)).fetchall()]
                for i in ids:
                    take(con.execute("SELECT * FROM chunks WHERE id=?", (i,)).fetchone())
        if not named and len(chosen) < limit and terms:          # a request that names a file reads that file; others are searched only when none is named
            ids = [r[0] for r in con.execute(
                "SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.chunk_id WHERE chunks_fts MATCH ? AND c.kind IN ('source','project') "
                "ORDER BY bm25(chunks_fts, 0.0, 4.0, 1.0) LIMIT ?", (retrieve.fts_match(terms), limit * 12)).fetchall()]
            for i in ids:
                if len(chosen) >= limit:
                    break
                take(con.execute("SELECT * FROM chunks WHERE id=?", (i,)).fetchone())
        if fill:
            rank = lambda f: (FILL_ORDER.index(ext_of(f["file"])) if ext_of(f["file"]) in FILL_ORDER else len(FILL_ORDER), f["file"])  # noqa: E731
            for f in sorted((f for f in use if role_of(f) == "analysis" and ext_of(f["file"]) not in IMAGE_EXT), key=rank):
                if ext_of(f["file"]) in ("py", "r", "rmd", "m", "jl", "sql", "sh"):
                    continue
                for row in con.execute("SELECT * FROM chunks WHERE kind IN ('source','project') AND file=? ORDER BY ord", (f["file"],)).fetchall():
                    take(row)
        pics = _pick_images(use, instruction + " " + passage[:800], set(named), every=asked_analysis or analysis_mode == "always")
        for f in pics:                                              # a picture that goes out is announced in the text, so the answer can say which one it looked at
            row = con.execute("SELECT * FROM chunks WHERE kind IN ('source','project') AND file=? ORDER BY ord LIMIT 1", (f["file"],)).fetchone()
            if row:
                chosen.append({"id": row["id"], "file": row["file"], "where": _where(proj, row), "tokens": row["tokens"],
                               "text": row["text"] + " The picture itself is attached to this request.", "kind": row["kind"], "ord": row["ord"], "picture": True})
    finally:
        con.close()
    if not chosen:
        return None
    if fill:                                                         # new text is written from numbers first: results and tables, then logs, then code, then the figures' names
        order = lambda c: (1 if c.get("picture") else 0, FILL_ORDER.index(ext_of(c["file"])) if ext_of(c["file"]) in FILL_ORDER else len(FILL_ORDER), c["file"], c["ord"])  # noqa: E731
        chosen.sort(key=order)
    else:
        chosen.sort(key=lambda c: (c["file"], c["ord"]))
    return {"chunks": chosen, "files": list(dict.fromkeys(c["file"] for c in chosen)), "images": [{"file": f["file"], "name": f["file"].rsplit("/", 1)[-1]} for f in pics],
            "why": "named" if named else "asked" if (asked_files or asked_analysis) else "always"}


def block(res):
    """The parts as the text that goes into the request."""
    return "\n\n".join(f"[{c['where']}]\n{c['text']}" for c in res["chunks"])


GENERIC_OUTLINE = re.compile(r"\b(?:outline[ds]?|writing[- ]?plan|the\s+brief|my\s+brief)\b", re.I)


def _stem_tokens(f):
    stem = f.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    return {w[:5] for w in re.findall(r"[a-z]{4,}", stem) if w not in ("outline", "outlined", "prompt", "brief", "writing", "plan", "instructions", "instruction")}


def named_outlines(outlines, instruction):
    """-> (the outline files the request means, a note when it could not tell). An outline is meant when the request names its file; when it only says "the outline", the one outline
    the paper has, or the ones whose names share a word with the request ("outlined_intro.md" for "write the introduction")."""
    named = mentions(outlines, instruction)
    if named:
        return named, ""
    if not GENERIC_OUTLINE.search(instruction or ""):
        return [], ""
    if len(outlines) == 1:
        return [outlines[0]["file"]], ""
    words = {w[:5] for w in re.findall(r"[a-z]{4,}", (instruction or "").lower())}
    close = [f["file"] for f in outlines if _stem_tokens(f["file"]) & words]
    if close:
        return close, ""
    return [], ("You mention an outline and there are several (" + ", ".join(f["file"].rsplit("/", 1)[-1] for f in outlines[:6]) + "): I did not use any. Say which one by its file name.")


def outline_select(name, proj, instruction, budget=5000):
    """A named author outline, kept separate from evidence and sent whole enough to preserve its order. -> (text, shown, notes)

    An outline never becomes a hidden global instruction: the request names it ("follow Promp_Intro.md") or calls it "the outline" when there is only one that fits. When the request asks
    for some of its sections ("sections 1-4"), only those are sent, with whatever general guidance comes before the first numbered section."""
    from . import paper_index, subject
    outlines = [f for f in listing(name, proj) if role_of(f) == "outline" and f.get("parts")]
    if not outlines:
        return "", [], []
    named, note = named_outlines(outlines, instruction)
    notes = [note] if note else []
    if not named:
        return "", [], notes
    wanted = subject.requested_sections(instruction)
    con = paper_index.open_index(name, proj)
    try:
        rows = con.execute("SELECT * FROM chunks WHERE kind IN ('source','project') ORDER BY ord").fetchall()
    finally:
        con.close()
    by_file = {}
    for row in rows:
        if row["file"] in named:
            by_file.setdefault(row["file"], []).append(row)
    chosen, shown, per = [], [], max(400, budget // max(1, len(by_file)))
    for f in named:
        if f not in by_file:
            continue
        full = "\n\n".join(r["text"] for r in by_file[f])
        text, kept, present = subject.pick_sections(full, wanted)
        if wanted and present and not kept:
            notes.append(f"You asked for outline section(s) {', '.join(str(n) for n in sorted(wanted))} but {f.rsplit('/', 1)[-1]} numbers its sections {', '.join(str(n) for n in present)}: I sent all of it.")
        elif wanted and not present:
            notes.append(f"{f.rsplit('/', 1)[-1]} has no numbered sections, so I sent all of it instead of sections {', '.join(str(n) for n in sorted(wanted))}.")
        text = subject.trim_to(text, per)
        if not text.strip():
            continue
        where = (f"sections {kept[0]}-{kept[-1]} of {present[-1]}" if len(kept) > 1 and kept == list(range(kept[0], kept[-1] + 1)) else
                 f"section(s) {', '.join(str(n) for n in kept)} of {present[-1]}" if kept else "all of it")
        chosen.append(f"[{f} · {where}]\n{text}")
        shown.append({"file": f, "where": where, "tokens": store.approx_tokens(text), "sections": kept})
    return "\n\n".join(chosen), shown, notes


def outline_context(name, proj, instruction, budget=5000):
    """outline_select without the notes: -> (text, shown)"""
    text, shown, _ = outline_select(name, proj, instruction, budget)
    return text, shown
