"""Keep <paper>/.overleaf-skill/ out of git, whoever writes there first (index, events, proposals, settings, memory).

`overleaf_sync` runs `git add -A`, so an unprotected folder with indexes, event logs or ChatGPT proposals in it could be pushed to
Overleaf. The clone-local .git/info/exclude is used: nothing is added to the repository that Overleaf would receive.
"""
import subprocess
from pathlib import Path

_CHECKED = set()
DIRNAME = ".overleaf-skill"


def ensure_ignored(root):
    """root = the paper's folder. Returns a short status; raises RuntimeError if it cannot protect the folder."""
    root = Path(root)
    if str(root) in _CHECKED:
        return "checked"
    r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", f"{DIRNAME}/index.sqlite"], capture_output=True)
    if r.returncode == 0:
        _CHECKED.add(str(root))
        return "already ignored"
    exclude = root / ".git" / "info" / "exclude"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        needs_nl = exclude.exists() and exclude.stat().st_size and not exclude.read_text().endswith("\n")
        with open(exclude, "a", encoding="utf-8") as f:
            f.write(("\n" if needs_nl else "") + f"{DIRNAME}/\n")
        _CHECKED.add(str(root))
        return "added to .git/info/exclude"
    except OSError as e:
        raise RuntimeError(f"cannot protect {DIRNAME}/ from git ({e}); refusing to write there") from e


def guard_scope(scope_dir):
    """For any directory about to receive state: if it is a paper's .overleaf-skill/, make sure git ignores it first."""
    p = Path(scope_dir)
    if p.name == DIRNAME and (p.parent / ".git").exists():
        ensure_ignored(p.parent)
