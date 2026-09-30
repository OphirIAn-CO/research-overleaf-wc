#!/usr/bin/env python3
"""Shared state for the overleaf skill: one registry, one data dir. Stdlib only.

Data dir (never in git): $OVERLEAF_SKILL_HOME or ~/.config/overleaf-skill
  projects.json          registry: every paper, its folder, bib file, Mendeley group
  mendeley_credentials.json, mendeley_tokens.json, telegram.json, telegram_state.json

CLI:
  common.py list
  common.py add <path> [--name N] [--group G]   register (or update) a paper
  common.py rm <name>
  common.py default <name>
"""
import json, os, subprocess, sys
from pathlib import Path

HOME = Path(os.environ.get("OVERLEAF_SKILL_HOME", "~/.config/overleaf-skill")).expanduser()
REG = HOME / "projects.json"


def load(name, default=None):
    try:
        return json.loads((HOME / name).read_text())
    except FileNotFoundError:
        return default


def save(name, data):
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = HOME / name
    p.write_text(json.dumps(data, indent=2))
    p.chmod(0o600)


def registry():
    return load("projects.json", {"default": None, "projects": {}})


def find_bib(path):
    path = Path(path)
    if (path / "references.bib").exists():
        return "references.bib"
    bibs = sorted(p.name for p in path.glob("*.bib"))
    return bibs[0] if bibs else "references.bib"


def git_root(path):
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def is_overleaf(path):
    r = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True)
    return "overleaf.com" in r.stdout


def register(path, name=None, group=None):
    """Add or update a project; returns its name. Unknown folders are auto-named by folder."""
    path = str(Path(path).expanduser().resolve())
    reg = registry()
    projs = reg["projects"]
    name = name or next((n for n, p in projs.items() if p["path"] == path), None) or Path(path).name
    p = projs.setdefault(name, {"path": path, "bib": find_bib(path), "mendeley_group": None})
    p["path"] = path
    if group is not None:
        p["mendeley_group"] = group if isinstance(group, dict) else {"id": None, "name": group}
    reg["default"] = reg["default"] or name
    save("projects.json", reg)
    return name


def resolve(ref=None):
    """name | path | None (-> cwd's git root, then registry default) -> (name, project dict).
    A path not yet registered is registered on the spot, so nothing needs manual setup."""
    reg = registry()
    if ref and ref in reg["projects"]:
        return ref, reg["projects"][ref]
    path = ref if ref and ("/" in ref or ref.startswith("~")) else None
    cwd = git_root(os.getcwd()) if not ref else None  # implicit cwd: only trust Overleaf clones
    root = git_root(Path(path).expanduser()) if path else (cwd if cwd and is_overleaf(cwd) else None)
    if root:
        by_path = next((n for n, p in reg["projects"].items() if p["path"] == root), None)
        name = by_path or register(root)
        return name, registry()["projects"][name]
    d = reg["default"]
    if ref is None and d in reg["projects"]:
        return d, reg["projects"][d]
    raise KeyError(f"unknown project {ref!r}; known: {', '.join(reg['projects']) or '(none)'}")


def bib_path(proj):
    return str(Path(proj["path"]) / proj["bib"])


def project_for_bib(bib):
    """Project owning this bib file (registering it if new)."""
    bib = str(Path(bib).resolve())
    for n, p in registry()["projects"].items():
        if bib_path(p) == bib:
            return n
    return register(os.path.dirname(bib))


def main(a):
    reg = registry()
    if a[:1] == ["list"]:
        for n, p in reg["projects"].items():
            g = (p.get("mendeley_group") or {}).get("name", "-")
            print(f"{'*' if n == reg['default'] else ' '} {n:20} {g:18} {p['path']}")
    elif a[:1] == ["add"] and len(a) > 1:
        kw = dict(zip(a[2::2], a[3::2]))
        print("registered", register(a[1], kw.get("--name"), kw.get("--group")))
    elif a[:1] == ["rm"] and len(a) > 1:
        reg["projects"].pop(a[1], None)
        reg["default"] = reg["default"] if reg["default"] in reg["projects"] else next(iter(reg["projects"]), None)
        save("projects.json", reg)
    elif a[:1] == ["default"] and len(a) > 1 and a[1] in reg["projects"]:
        reg["default"] = a[1]
        save("projects.json", reg)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
