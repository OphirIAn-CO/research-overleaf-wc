"""A small TeX workspace for one paper, on this computer: the .tex files to read and edit, and the compiled PDF drawn page by page, like TeXShop.

Nothing here touches Overleaf. The paper is compiled in its own folder, but everything LaTeX writes (the PDF, .aux, .log, the page pictures) goes into
<paper>/.overleaf-skill/build/, which git ignores, so a compile never adds files to what is sent to Overleaf. Sending is the usual "Send to Overleaf".

  compile(proj)               latexmk (or pdflatex and bibtex) -> {pdf, pages, errors, warnings, undefined, seconds, ...}
  page_image(proj, n, dpi)    one page of the PDF as a PNG, drawn at twice the size it will be shown at
  forward(...) / inverse(...) SyncTeX: from a line of the source to its place in the PDF, and from a click on the PDF back to the source line
  list_files / read_file / write_file   the source files, with a check that nothing changed on disk behind the author's back
"""
import hashlib
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import chunk_tex, config

BUILD = "build"
COMPILE_TIMEOUT = int(os.environ.get("OLS_TEX_TIMEOUT", 180))
EDIT_EXT = {"tex", "bib", "sty", "cls", "bst", "txt", "md", "def", "cfg", "ins", "dtx"}
MAX_FILE = 3_000_000
MAX_FILES = 300
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv"}
_PAGES = threading.Semaphore(4)       # page pictures drawn at the same time


class LatexProblem(Exception):
    """A reason, in plain words, why something cannot be done. `.code` is a short word the pane can act on."""

    def __init__(self, msg, code="problem"):
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------------------------- what this computer can do

def tools():
    return {n: shutil.which(n) for n in ("latexmk", "pdflatex", "xelatex", "lualatex", "bibtex", "biber", "pdftoppm", "pdfinfo", "synctex")}


def available():
    """(ok, reason). The reason tells the author what to do, in words that fit where the app is running."""
    t = tools()
    docker = os.environ.get("OVERLEAF_UI_IN_DOCKER") == "1"
    if not (t["latexmk"] or t["pdflatex"]):
        if docker:
            return False, "The app's Docker image has no LaTeX in it yet. Rebuild it with LaTeX: ./ui.sh build, then ./ui.sh down and ./ui.sh up (a few minutes, and about 1 GB more disk)."
        return False, "LaTeX is not installed on this computer. Install MacTeX (macOS) or TeX Live, then start the app again."
    if not (t["pdftoppm"] and t["pdfinfo"]):
        return False, "The PDF page drawing tool (poppler) is missing. On a Mac: brew install poppler. In Docker it is part of the image."
    return True, ""


def _build_dir(proj, create=True):
    """Where everything LaTeX writes goes. Inside the paper's own notes folder (git ignores it) unless OLS_BUILD_DIR says otherwise: in Docker that is a scratch folder
    in memory, which is much faster than writing hundreds of small files through the shared folder, and nothing there needs to outlast the app."""
    base = os.environ.get("OLS_BUILD_DIR")
    if base:
        root = Path(proj["path"]).resolve()
        d = Path(base) / f"{root.name}-{hashlib.sha1(str(root).encode()).hexdigest()[:10]}"
    else:
        d = (config.ensure_paper_dir(proj) if create else config.paper_dir(proj)) / BUILD
    if create:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def main_file(proj):
    root = Path(proj["path"])
    main = proj.get("main")
    if main and (root / main).is_file():
        return main
    found = chunk_tex.find_main(root)
    if found:
        try:
            return str(Path(found).resolve().relative_to(root.resolve()))
        except ValueError:
            return Path(found).name
    raise LatexProblem("I could not tell which .tex file is the main one of this paper.", "no_main")


# --------------------------------------------------------------------------------------------- the source files

def _safe(proj, rel):
    """The absolute path of a file inside the paper, or a problem. Links that lead outside the paper are refused."""
    root = Path(proj["path"]).resolve()
    p = (root / str(rel)).resolve()
    if root not in p.parents or p.parent == root and p.name.startswith("."):
        raise LatexProblem("That file is not part of this paper.", "outside")
    if any(part.startswith(".") for part in p.relative_to(root).parts) or p.suffix.lower().lstrip(".") not in EDIT_EXT:
        raise LatexProblem("That kind of file is not shown here.", "kind")
    return p


def list_files(proj):
    """The files of the paper that can be read and edited here: [{file, size, main}], the main file first."""
    root = Path(proj["path"])
    try:
        main = main_file(proj)
    except LatexProblem:
        main = None
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if fn.startswith(".") or p.suffix.lower().lstrip(".") not in EDIT_EXT or p.is_symlink():
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            if stat.st_size <= MAX_FILE:
                rel = str(p.relative_to(root))
                out.append({"file": rel, "size": stat.st_size, "mtime": stat.st_mtime, "main": rel == main})
            if len(out) >= MAX_FILES:
                break
    order = {"tex": 0, "bib": 1}
    out.sort(key=lambda f: (not f["main"], order.get(f["file"].rsplit(".", 1)[-1], 2), f["file"].lower()))
    return out


def _hash(data):
    return hashlib.sha1(data).hexdigest()[:16]


def read_file(proj, rel):
    p = _safe(proj, rel)
    try:
        data = p.read_bytes()
    except FileNotFoundError:
        raise LatexProblem("That file no longer exists.", "gone") from None
    if len(data) > MAX_FILE:
        raise LatexProblem("That file is too large to show here.", "big")
    if b"\0" in data[:4096]:
        raise LatexProblem("That does not look like a text file.", "binary")
    text = data.decode("utf-8", "replace")
    return {"file": str(Path(rel)), "text": text, "hash": _hash(data), "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1)}


def file_hash(proj, rel):
    try:
        return _hash(_safe(proj, rel).read_bytes())
    except (OSError, LatexProblem):
        return None


def write_file(proj, rel, text, base):
    """Saves an edit. `base` is the hash of the version the author opened: if the file is different now (Overleaf's latest was fetched, or the assistant saved a
    suggestion), nothing is written and the author is told, so one side never overwrites the other without knowing."""
    p = _safe(proj, rel)
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_FILE:
        raise LatexProblem("That text is too large to save here.", "big")
    try:
        cur = p.read_bytes()
    except FileNotFoundError:
        raise LatexProblem("That file no longer exists.", "gone") from None
    if not base or _hash(cur) != base:
        raise LatexProblem("This file changed on disk since you opened it (the assistant saved a suggestion, or the latest from Overleaf arrived). Reload it, then make your edit again.", "stale")
    data = text.encode("utf-8")
    tmp = p.with_name("." + p.name + ".part")
    tmp.write_bytes(data)
    try:
        shutil.copymode(p, tmp)
    except OSError:
        pass
    os.replace(tmp, p)
    return {"hash": _hash(data)}


def changed(proj):
    """The lines changed here since the last commit, i.e. what is not in Overleaf yet. -> {file: [[first line, last line], ...]} (line numbers as the file is now);
    a file that is new counts as all of its lines. Only files this pane shows."""
    root = Path(proj["path"])
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    out = {}
    try:
        diff = subprocess.run(["git", "-C", str(root), "diff", "--unified=0", "--no-color", "--no-ext-diff", "HEAD", "--"], capture_output=True, text=True, timeout=30, env=env).stdout
        new = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, timeout=30, env=env).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return out
    cur = None
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            cur = ln[6:] if ln.startswith("+++ b/") else None
            continue
        m = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", ln)
        if m and cur:
            first, count = int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1
            if count:
                out.setdefault(cur, []).append([first, first + count - 1])
    for rel in new:
        try:
            n = len(_safe(proj, rel).read_text(encoding="utf-8", errors="replace").splitlines())
        except (LatexProblem, OSError):
            continue
        if n:
            out[rel] = [[1, n]]
    shown = {}
    for rel, ranges in out.items():
        try:
            _safe(proj, rel)
        except LatexProblem:
            continue
        shown[rel] = ranges
    return shown


# --------------------------------------------------------------------------------------------- compiling

_ERR = re.compile(r"^(?P<file>[^\s:][^:\n]*?):(?P<line>\d+): (?P<msg>.+)$")
_BANG = re.compile(r"^! (?P<msg>.+)$")
_LINE_AT = re.compile(r"^l\.(\d+) ")
_CITE = re.compile(r"Citation `([^']+)' on page \d+ undefined")
_REF = re.compile(r"Reference `([^']+)' on page \d+ undefined")
_MISSING = re.compile(r"File `([^']+)' not found")
_MAGIC = re.compile(r"^\s*%\s*!T[eE]X\s+(?:TS-)?program\s*=\s*(\w+)", re.M)


def _engine(root, main):
    try:
        head = (Path(root) / main).read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return "pdflatex"
    m = _MAGIC.search(head)
    name = (m.group(1).lower() if m else "pdflatex")
    return name if name in ("pdflatex", "xelatex", "lualatex") else "pdflatex"


def _env(root, build):
    texmf = build / ".texmf"
    for sub in ("var", "config", "home"):
        (texmf / sub).mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(texmf), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "TEXMFVAR": str(texmf / "var"), "TEXMFCONFIG": str(texmf / "config"), "TEXMFHOME": str(texmf / "home"),
           "TEXINPUTS": f".:{root}:", "BIBINPUTS": f".:{root}:", "BSTINPUTS": f".:{root}:",
           "openout_any": "p", "openin_any": "p", "shell_escape": "f", "max_print_line": "1000", "error_line": "254", "half_error_line": "238"}
    for k in ("TZ", "SOURCE_DATE_EPOCH"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def _limits():
    os.setsid()
    resource.setrlimit(resource.RLIMIT_FSIZE, (400 << 20, 400 << 20))      # a runaway loop cannot fill the disk
    resource.setrlimit(resource.RLIMIT_CPU, (COMPILE_TIMEOUT + 60, COMPILE_TIMEOUT + 60))


def _run(cmd, cwd, env, timeout):
    """Runs one command in its own process group; kills the whole group on timeout. -> (returncode or None, combined output)"""
    p = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, preexec_fn=_limits)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        out, _ = p.communicate()
        return None, out.decode("utf-8", "replace")


def _tex_cmd(engine):
    flags = "-no-shell-escape -interaction=nonstopmode -file-line-error -synctex=1"
    return f"{engine} {flags} %O %S"


def _norm(file, root):
    f = file[2:] if file.startswith("./") else file
    try:
        return str(Path(f).resolve().relative_to(Path(root).resolve())) if os.path.isabs(f) else f
    except ValueError:
        return f


def parse_log(text, root):
    """-> {errors: [{file, line, message, context}], warnings: [str], undefined: {citations, references}, missing: [file names TeX could not find]}"""
    lines = text.splitlines()
    errors, warnings, missing = [], [], []
    for i, ln in enumerate(lines):
        m = _ERR.match(ln)
        if m and not m.group("msg").startswith(("Warning", "warning")) and not m.group("file").startswith(("(", "/usr", "/var")):
            msg, ctx = m.group("msg").strip(), []
            for nxt in lines[i + 1:i + 6]:
                if nxt.strip() and not _ERR.match(nxt) and not nxt.startswith("!"):
                    ctx.append(nxt.rstrip())
                else:
                    break
            errors.append({"file": _norm(m.group("file"), root), "line": int(m.group("line")), "message": msg, "context": "\n".join(ctx[:3])})
        elif _BANG.match(ln) and not any(e["message"] == _BANG.match(ln).group("msg") for e in errors):
            # an error TeX reported without a file and line (a fatal one, or a missing file at the very start)
            ctx = [x.rstrip() for x in lines[i + 1:i + 4] if x.strip()]
            line_no = next((int(_LINE_AT.match(x).group(1)) for x in lines[i + 1:i + 8] if _LINE_AT.match(x)), 0)
            errors.append({"file": "", "line": line_no, "message": _BANG.match(ln).group("msg").strip(), "context": "\n".join(ctx[:2])})
        mm = _MISSING.search(ln)
        if mm and mm.group(1) not in missing:
            missing.append(mm.group(1))
        if "Warning" in ln and len(warnings) < 40 and ("LaTeX Warning" in ln or "Package" in ln):
            warnings.append(ln.strip()[:240])
    joined = "\n".join(lines)
    return {"errors": errors[:40], "warnings": warnings, "missing": missing,
            "undefined": {"citations": sorted(set(_CITE.findall(joined)))[:40], "references": sorted(set(_REF.findall(joined)))[:40]}}


def _pdf_id(pdf):
    st = pdf.stat()
    return f"{st.st_mtime_ns:x}{st.st_size:x}"


def pdf_path(proj, main=None):
    main = main or main_file(proj)
    return _build_dir(proj, create=False) / (Path(main).stem + ".pdf")


def info(proj):
    """The compiled PDF now: {pdf, id, pages, width, height} (points), or {pdf: False}."""
    try:
        pdf = pdf_path(proj)
    except LatexProblem:
        return {"pdf": False}
    if not pdf.is_file():
        return {"pdf": False}
    try:
        r = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return {"pdf": False}
    pages = re.search(r"^Pages:\s+(\d+)", r.stdout, re.M)
    size = re.search(r"^Page size:\s+([\d.]+) x ([\d.]+) pts", r.stdout, re.M)
    if not pages:
        return {"pdf": False}
    return {"pdf": True, "id": _pdf_id(pdf), "pages": int(pages.group(1)), "width": float(size.group(1)) if size else 595.0, "height": float(size.group(2)) if size else 842.0}


def compile(proj, timeout=None):
    """Compile the paper's main file into the build folder. A PDF is kept even when there are errors, if LaTeX could still make one."""
    ok, why = available()
    if not ok:
        raise LatexProblem(why, "unavailable")
    root = Path(proj["path"])
    main = main_file(proj)
    build = _build_dir(proj)
    engine = _engine(root, main)
    env = _env(str(root.resolve()), build)
    timeout = timeout or COMPILE_TIMEOUT
    t0 = time.time()
    t = tools()
    old_pdf = pdf_path(proj, main)
    before = old_pdf.stat().st_mtime_ns if old_pdf.exists() else 0
    if t["latexmk"]:
        # -norc: a latexmkrc in the paper (Overleaf projects often have one, and collaborators can add one) is a program; it is never run here
        cmd = [t["latexmk"], "-norc", f"-{ 'pdf' if engine == 'pdflatex' else 'pdfxe' if engine == 'xelatex' else 'pdflua' }", "-f", "-bibtex",
               "-e", f"${engine} = q{{{_tex_cmd(engine)}}}", f"-outdir={build}", "-interaction=nonstopmode", main]
        code, out = _run(cmd, root, env, timeout)
    else:                         # no latexmk: the classic sequence, enough for a bibliography and cross references
        outs, code = [], 0
        exe = t.get(engine) or t["pdflatex"]
        tex = [exe, "-no-shell-escape", "-interaction=nonstopmode", "-file-line-error", "-synctex=1", f"-output-directory={build}", main]
        for step in (tex, None, tex, tex):
            if step is None:
                if not t["bibtex"] or not list(root.rglob("*.bib")) and "\\bibliography" not in (root / main).read_text(errors="replace"):
                    continue
                step = [t["bibtex"], str(build / Path(main).stem)]
            code, o = _run(step, root, env, timeout)
            outs.append(o)
            if code is None:
                break
        out = "\n".join(outs)
    seconds = round(time.time() - t0, 1)
    timed_out = code is None
    log_file = build / (Path(main).stem + ".log")
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else out
    parsed = parse_log(log, root)
    pdf = pdf_path(proj, main)
    made = pdf.is_file() and pdf.stat().st_mtime_ns > before
    res = {"ok": bool(made and not parsed["errors"]), "pdf": pdf.is_file(), "fresh": bool(made), "engine": engine, "main": main, "seconds": seconds, "started": t0, "at": time.time(),
           "timed_out": timed_out, **parsed, "log_tail": out[-4000:] if (parsed["errors"] or not made or timed_out) else ""}
    if timed_out:
        res["errors"] = [{"file": "", "line": 0, "message": f"Compiling took longer than {timeout} seconds, so I stopped it. A loop in the document, or a very heavy picture, can cause that.", "context": ""}] + res["errors"]
    if res["pdf"]:
        res.update({k: v for k, v in info(proj).items() if k != "pdf"})
        _clean_pages(proj, res.get("id"))
    try:
        (build / "last.json").write_text(json.dumps(res))
    except OSError:
        pass
    return res


def last(proj):
    try:
        res = json.loads((_build_dir(proj, create=False) / "last.json").read_text())
    except (OSError, ValueError):
        return None
    cur = info(proj)
    if cur.get("pdf"):
        res.update({k: v for k, v in cur.items() if k != "pdf"})
        res["pdf"] = True
    return res


# --------------------------------------------------------------------------------------------- pictures of the pages

def _clean_pages(proj, keep_id):
    d = _build_dir(proj, create=False) / "pages"
    if d.is_dir():
        for f in list(d.glob("*.png")) + list(d.glob("*.jpg")):
            if keep_id and f".{keep_id}." not in f.name:
                try:
                    f.unlink()
                except OSError:
                    pass


def page_image(proj, n, dpi=110):
    """A PNG of page n (1 based) drawn at `dpi`; kept until the PDF changes. -> path
    PNG, not JPEG: JPEG blurs the edges of small black text and smears coloured text, which is exactly what a page of a paper is made of."""
    cur = info(proj)
    if not cur.get("pdf"):
        raise LatexProblem("There is no PDF yet. Compile first.", "nopdf")
    n = int(n)
    if not 1 <= n <= cur["pages"]:
        raise LatexProblem("There is no such page.", "nopage")
    dpi = max(36, min(int(round(int(dpi) / 6.0) * 6), 300))
    d = _build_dir(proj) / "pages"
    d.mkdir(exist_ok=True)
    out = d / f"p{n}-{dpi}.{cur['id']}.png"
    if out.is_file():
        return out
    with _PAGES:
        if out.is_file():
            return out
        prefix = d / f".tmp-{os.getpid()}-{threading.get_ident()}-{n}"
        r = subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(n), "-l", str(n), "-singlefile", "-aa", "yes", "-aaVector", "yes", "-freetype", "yes",
                            str(pdf_path(proj)), str(prefix)], capture_output=True, text=True, timeout=90)
        tmp = Path(str(prefix) + ".png")
        if r.returncode != 0 or not tmp.is_file():
            raise LatexProblem("I could not draw that page: " + (r.stderr or "")[:160], "draw")
        os.replace(tmp, out)
    return out


# --------------------------------------------------------------------------------------------- SyncTeX

def _synctex(args, proj):
    if not shutil.which("synctex"):
        raise LatexProblem("SyncTeX is not available.", "nosynctex")
    r = subprocess.run(["synctex", *args], capture_output=True, text=True, cwd=str(proj["path"]), timeout=30,
                       env={"PATH": os.environ.get("PATH", ""), "HOME": str(_build_dir(proj) / ".texmf")})
    return r.stdout


def forward(proj, rel, line):
    """The place in the PDF where a line of the source ends up. -> {page, x, y, w, h} in points from the top left of the page"""
    pdf = pdf_path(proj)
    if not pdf.is_file():
        raise LatexProblem("There is no PDF yet. Compile first.", "nopdf")
    out = _synctex(["view", "-i", f"{int(line)}:0:{Path(proj['path']).resolve() / rel}", "-o", str(pdf)], proj)
    first = {}
    for ln in out.splitlines():
        k, _, v = ln.partition(":")
        if k in ("Page", "x", "y", "h", "v", "W", "H") and k not in first:
            first[k] = v.strip()
    if "Page" not in first:
        raise LatexProblem("This line does not appear in the PDF (a comment, a definition, or text on a page that is not drawn).", "nowhere")
    return {"page": int(first["Page"]), "x": float(first.get("h", 0)), "y": float(first.get("v", 0)), "w": float(first.get("W", 0)), "h": float(first.get("H", 0))}


def inverse(proj, page, x, y):
    """The source line behind a point of the PDF (points from the top left of the page). -> {file, line}"""
    pdf = pdf_path(proj)
    if not pdf.is_file():
        raise LatexProblem("There is no PDF yet. Compile first.", "nopdf")
    out = _synctex(["edit", "-o", f"{int(page)}:{float(x):.2f}:{float(y):.2f}:{pdf}"], proj)
    pairs, cur = [], None
    for ln in out.splitlines():                       # several candidates, the closest first; generated files (the bibliography BibTeX made) are skipped
        k, _, v = ln.partition(":")
        if k == "Input":
            cur = v.strip()
        elif k == "Line" and cur is not None:
            pairs.append((cur, v.strip()))
            cur = None
    root = Path(proj["path"]).resolve()
    for name, line in pairs:
        f = Path(name)
        f = f if f.is_absolute() else root / f
        try:
            rel = str(f.resolve().relative_to(root))
            _safe(proj, rel)
        except (ValueError, LatexProblem):
            continue
        return {"file": rel, "line": max(1, int(line) if line.lstrip("-").isdigit() else 1)}
    raise LatexProblem("Nothing in the source belongs to that place.", "nowhere")
