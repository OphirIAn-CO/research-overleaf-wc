"""Remove the entries of the .bib file that no .tex file cites. Stdlib only."""
import re
import shutil
from pathlib import Path

from .chunk_tex import _group, strip_comment

_CITE = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}")
_ENTRY = re.compile(r"^[ \t]*@(\w+)\s*\{", re.M)
_KEEP = {"string", "preamble", "comment"}


def _cited(root):
    keys = set()
    for p in root.rglob("*.tex"):
        if any(x in (".git", ".overleaf-skill") for x in p.relative_to(root).parts):
            continue
        text = "\n".join(strip_comment(ln) for ln in p.read_text(encoding="utf-8", errors="replace").split("\n"))
        for m in _CITE.finditer(text):
            keys.update(k.strip() for k in m.group(1).split(","))
    return keys


def prune(proj, apply=True):
    """-> {removed: [key], kept: n, skipped: reason or ''}. Cuts each uncited entry (and the blank lines after it); a copy of the old file goes to .overleaf-skill/."""
    root = Path(proj["path"])
    bib = root / proj.get("bib", "references.bib")
    cited = _cited(root)
    if "*" in cited:
        return {"removed": [], "kept": 0, "skipped": "the paper has \\nocite{*}, which cites every entry"}
    text = bib.read_text(encoding="utf-8", errors="replace")
    removed, cuts = [], []
    for m in _ENTRY.finditer(text):
        if m.group(1).lower() in _KEEP:
            continue
        g = _group(text, m.end() - 1)
        key = g[0].split(",", 1)[0].strip() if g else ""
        if g and key and key not in cited:
            end = g[1]
            while end < len(text) and text[end] in " \t\r\n":
                end += 1
            cuts.append((m.start(), end))
            removed.append(key)
    if apply and cuts:
        (root / ".overleaf-skill").mkdir(exist_ok=True)
        shutil.copy2(bib, root / ".overleaf-skill" / (bib.name + ".bak"))
        for a, b in reversed(cuts):
            text = text[:a] + text[b:]
        bib.write_text(text, encoding="utf-8")
    return {"removed": removed, "kept": len(_ENTRY.findall(text)) - (0 if apply else len(removed)), "skipped": ""}
