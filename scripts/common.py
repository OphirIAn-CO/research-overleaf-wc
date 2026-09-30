#!/usr/bin/env python3
"""Shared state for the overleaf skill: one registry, one data dir. Stdlib only.

Data dir (never in git): $OVERLEAF_SKILL_HOME or ~/.config/overleaf-skill
  projects.json          registry: every paper, its folder, bib file, Mendeley group
  mendeley_credentials.json, mendeley_tokens.json, telegram.json, telegram_state.json

CLI:
  common.py list
  common.py add <path> [--name N] [--group G]   register (or update) a paper
  common.py add <path> --name N --relink        after moving/re-cloning a paper
  common.py doctor                              check every paper (folder, remote, bib, group)
  common.py rm <name>
  common.py default <name>
"""
import json, os, subprocess, sys, time
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


def remote_url(path):
    r = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def remote_id(proj):
    """Short Overleaf project id (last URL segment), shown in bot replies to prove the target."""
    return (proj.get("remote") or "").rstrip("/").rsplit("/", 1)[-1][:8] or "no-remote"


def verify(name, proj):
    """None if the registered folder still is the Overleaf clone it was registered as, else a message.
    Backfills the remote for older registry entries."""
    p = Path(proj["path"])
    if not (p / ".git").exists():
        return f"{name}: folder missing or not a git repo ({p}). Moved? Fix: common.py add <new path> --name {name}"
    now = remote_url(p)
    if not proj.get("remote"):
        reg = registry()
        reg["projects"][name]["remote"] = proj["remote"] = now
        save("projects.json", reg)
    elif now != proj["remote"]:
        return f"{name}: origin changed ({proj['remote']} -> {now or 'none'}). Re-clone/retarget? Fix: common.py add {p} --name {name} --relink"
    if not (p / proj["bib"]).exists():
        return f"{name}: bib file {proj['bib']} not found in {p}"
    return None


def set_active(name, source):
    save("state.json", {"active": name, "source": source, "updated_at": time.time()})


def get_active():
    """(name|None, source, age_seconds): the paper last worked on, by Claude or via Telegram /paper."""
    s = load("state.json", {}) or {}
    reg = registry()
    n = s.get("active") if s.get("active") in reg["projects"] else None
    return n, s.get("source", "?"), time.time() - s.get("updated_at", 0)


def register(path, name=None, group=None):
    """Add or update a project; returns its name. Unknown folders are auto-named by folder."""
    path = str(Path(path).expanduser().resolve())
    reg = registry()
    projs = reg["projects"]
    name = name or next((n for n, p in projs.items() if p["path"] == path), None) or Path(path).name
    p = projs.setdefault(name, {"path": path, "bib": find_bib(path), "mendeley_group": None})
    p["path"] = path
    p["remote"] = remote_url(path)
    if group is not None:
        p["mendeley_group"] = group if isinstance(group, dict) else {"id": None, "name": group}
    reg["default"] = reg["default"] or name
    save("projects.json", reg)
    return name


def resolve(ref=None, check=True):
    """name | path | None -> (name, project dict). None means: cwd if it is an Overleaf clone,
    else the paper last worked on (set_active), else the registry default. A path not yet
    registered is registered on the spot. Raises KeyError (unknown) / RuntimeError (verify failed)."""
    reg = registry()
    found = None
    if ref and ref in reg["projects"]:
        found = ref
    else:
        path = ref if ref and ("/" in ref or ref.startswith("~")) else None
        cwd = git_root(os.getcwd()) if not ref else None  # implicit cwd: only trust Overleaf clones
        root = git_root(Path(path).expanduser()) if path else (cwd if cwd and is_overleaf(cwd) else None)
        if root:
            found = next((n for n, p in reg["projects"].items() if p["path"] == root), None) or register(root)
            reg = registry()
        elif ref is None:
            found = get_active()[0] or reg["default"]
    if found not in reg["projects"]:
        raise KeyError(f"unknown project {ref!r}; known: {', '.join(reg['projects']) or '(none)'}")
    proj = reg["projects"][found]
    err = verify(found, proj) if check else None
    if err:
        raise RuntimeError(err)
    return found, registry()["projects"][found]


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
        rest = [x for x in a[2:] if x != "--relink"]  # register() always refreshes the stored remote
        kw = dict(zip(rest[::2], rest[1::2]))
        print("registered", register(a[1], kw.get("--name"), kw.get("--group")))
    elif a[:1] == ["doctor"]:
        for n, p in reg["projects"].items():
            issues = [verify(n, p)] if verify(n, p) else []
            if not p.get("mendeley_group"):
                issues.append(f"{n}: no Mendeley group (DOIs will not reach Mendeley)")
            gi = Path(p["path"]) / ".gitignore"
            if not (gi.exists() and ".overleaf-skill" in gi.read_text()):
                issues.append(f"{n}: .overleaf-skill/ not in .gitignore (session memory could be pushed)")
            print(("OK   " if not issues else "WARN ") + n, *issues, sep="\n     " if issues else "")
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
