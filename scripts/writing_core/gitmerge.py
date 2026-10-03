"""Joining Overleaf's latest with the changes saved on this computer when the same lines were changed on both sides.

Nothing here ever overwrites either side by accident:
  * conflicts(repo)  tries the merge, reads which places collide and what each side has there, then puts everything back exactly as it was;
  * resolve(repo, choices)  does the merge again and, for each colliding place, takes the version the author chose (Overleaf's, theirs, or both), then commits the
    merge. Whatever was chosen is still in Overleaf's own history and in this computer's git history, so nothing is lost for good;
  * request(repo, ...) leaves a note for Claude Code, which finds it the next time it checks the paper.
The side called "mine" is what is saved on this computer, "overleaf" is what Overleaf has.
"""
import json
import os
import subprocess
import time
from pathlib import Path

MARK_MINE, MARK_SEP, MARK_THEIRS = "<<<<<<< ", "=======", ">>>>>>> "
CONTEXT = 2          # lines of the paper shown around a colliding place
SHOW_CHARS = 4000


class MergeProblem(Exception):
    """A reason, in plain words, why the two sides could not be joined here."""


def _git(repo, *args, timeout=90):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
    try:
        r = subprocess.run(["git", "-C", str(repo), "-c", "merge.conflictstyle=merge", "-c", "core.quotepath=off", *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, "", "git took too long"
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _merging(repo):
    return (Path(repo) / ".git" / "MERGE_HEAD").exists()


def _abort(repo):
    if _merging(repo):
        if _git(repo, "merge", "--abort")[0] != 0:
            _git(repo, "reset", "--merge")


def upstream(repo):
    code, out, _ = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code == 0 and out:
        return out
    code, branch, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return f"origin/{branch}" if code == 0 and branch else ""


def _start(repo):
    """Fetch and begin the merge without committing. -> (state, unmerged files); state is 'clean', 'conflict' or 'same'. The repository must be clean."""
    if _git(repo, "status", "--porcelain", "--untracked-files=no")[1]:
        raise MergeProblem("There are changes here that are not saved yet. Save them first (Send to Overleaf saves them), then try again.")
    up = upstream(repo)
    if not up:
        raise MergeProblem("This project has no Overleaf branch to join with.")
    code, out, err = _git(repo, "fetch", "origin")
    if code != 0:
        raise MergeProblem("I could not reach Overleaf: " + (err or out).splitlines()[-1][:200])
    code, out, err = _git(repo, "merge", "--no-commit", "--no-ff", up)
    if code == 0:
        return ("clean" if _merging(repo) else "same"), []
    files = _git(repo, "diff", "--name-only", "--diff-filter=U")[1].splitlines()
    if not files:
        _abort(repo)
        raise MergeProblem("The merge could not start: " + (err or out)[:200])
    return "conflict", files


def _parse(text):
    """-> (pieces) a list of ('text', str) and ('block', {mine, overleaf}) in file order. Lines keep their endings."""
    pieces, buf, state, mine, theirs = [], [], "text", [], []
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if state == "text" and bare.startswith(MARK_MINE):
            if buf:
                pieces.append(("text", "".join(buf)))
            buf, mine, theirs, state = [], [], [], "mine"
        elif state == "mine" and bare == MARK_SEP:
            state = "theirs"
        elif state == "theirs" and bare.startswith(MARK_THEIRS):
            pieces.append(("block", {"mine": "".join(mine), "overleaf": "".join(theirs)}))
            state = "text"
        elif state == "mine":
            mine.append(line)
        elif state == "theirs":
            theirs.append(line)
        else:
            buf.append(line)
    if state != "text":
        raise MergeProblem("A conflict in a file is not in the usual form, so I cannot split it into places.")
    if buf:
        pieces.append(("text", "".join(buf)))
    return pieces


def _read(repo, rel):
    data = (Path(repo) / rel).read_bytes()
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "surrogateescape")


def _show(s):
    return s if len(s) <= SHOW_CHARS else s[:SHOW_CHARS] + "\n[...]"


def conflicts(repo):
    """Try the join and report where it collides, then undo it completely.
    -> {state: 'clean'|'conflict'|'same', files: [{file, blocks: [{id, mine, overleaf, before, after}]}], whole_files: [{file, why}]}"""
    state, unmerged = _start(repo)
    try:
        files, whole = [], []
        for rel in unmerged:
            try:
                text = _read(repo, rel) if (Path(repo) / rel).is_file() else None
            except OSError:
                text = None
            if text is None:
                whole.append({"file": rel, "id": rel + "#file", "why": "This file cannot be compared line by line (a picture, a PDF, or one side deleted it)."})
                continue
            pieces, blocks = _parse(text), []
            if not any(k == "block" for k, _ in pieces):        # unmerged without marks: one side deleted the file, or both added it differently
                whole.append({"file": rel, "id": rel + "#file", "why": "One side deleted this file, or both created it differently."})
                continue
            for i, (kind, val) in enumerate(pieces):
                if kind != "block":
                    continue
                before = pieces[i - 1][1].splitlines()[-CONTEXT:] if i > 0 and pieces[i - 1][0] == "text" else []
                after = pieces[i + 1][1].splitlines()[:CONTEXT] if i + 1 < len(pieces) and pieces[i + 1][0] == "text" else []
                blocks.append({"id": f"{rel}#{len(blocks)}", "mine": _show(val["mine"]), "overleaf": _show(val["overleaf"]),
                               "before": "\n".join(before), "after": "\n".join(after)})
            files.append({"file": rel, "blocks": blocks})
        return {"state": state, "files": files, "whole_files": whole}
    finally:
        _abort(repo)


def _take_side(repo, rel, side):
    """The whole file from one side (stage 2 is this computer's, stage 3 is Overleaf's); when that side deleted it, the deletion."""
    stage = "2" if side == "mine" else "3"
    has = any(ln.split()[2] == stage for ln in _git(repo, "ls-files", "-u", "--", rel)[1].splitlines() if len(ln.split()) > 2)
    if has:
        _git(repo, "checkout", "--ours" if side == "mine" else "--theirs", "--", rel)
        return _git(repo, "add", "--", rel)[0] == 0
    return _git(repo, "rm", "-q", "--", rel)[0] == 0


def resolve(repo, choices, message="Join with Overleaf"):
    """Join the two sides taking, for each colliding place, the version in `choices` ({block id: 'mine'|'overleaf'|'both', file id: 'mine'|'overleaf'}) and commit
    the result. Any place without a choice stops everything and leaves the repository as it was. -> {state, kept: {'mine': n, 'overleaf': n, 'both': n}, files}"""
    state, unmerged = _start(repo)
    try:
        if state != "conflict":
            if state == "clean":                            # nothing collided after all (Overleaf changed meanwhile): the join is complete
                code, out, err = _git(repo, "commit", "-m", message)
                if code != 0:
                    raise MergeProblem("I could not finish the join: " + (err or out)[:200])
            return {"state": state, "kept": {"mine": 0, "overleaf": 0, "both": 0}, "files": []}
        kept, done = {"mine": 0, "overleaf": 0, "both": 0}, []
        for rel in unmerged:
            text = _read(repo, rel) if (Path(repo) / rel).is_file() else None
            if text is not None and not any(k == "block" for k, _ in _parse(text)):
                text = None                                      # no marks to choose between: the whole file is chosen
            if text is None:
                side = choices.get(rel + "#file")
                if side not in ("mine", "overleaf"):
                    raise MergeProblem(f"Choose which version of {rel} to keep.")
                if not _take_side(repo, rel, side):
                    raise MergeProblem(f"I could not take that version of {rel}.")
                kept[side] += 1
                done.append(rel)
                continue
            out, k = [], 0
            for kind, val in _parse(text):
                if kind == "text":
                    out.append(val)
                    continue
                pick = choices.get(f"{rel}#{k}")
                k += 1
                if pick not in ("mine", "overleaf", "both"):
                    raise MergeProblem(f"Choose Overleaf's version, yours or both for every place that collides ({rel}, place {k}).")
                kept[pick] += 1
                out.append(val["mine"] if pick == "mine" else val["overleaf"] if pick == "overleaf" else val["overleaf"] + val["mine"])
            result = "".join(out)
            if any(ln.startswith((MARK_MINE, MARK_THEIRS)) for ln in result.splitlines()):
                raise MergeProblem(f"{rel} still has conflict marks after joining, so I stopped.")
            (Path(repo) / rel).write_bytes(result.encode("utf-8", "surrogateescape"))
            if _git(repo, "add", "--", rel)[0] != 0:
                raise MergeProblem(f"I could not record {rel}.")
            done.append(rel)
        if _git(repo, "diff", "--name-only", "--diff-filter=U")[1]:
            raise MergeProblem("Some places are still unresolved, so I stopped.")
        code, out, err = _git(repo, "commit", "-m", message)
        if code != 0:
            raise MergeProblem("I could not finish the join: " + (err or out)[:200])
        return {"state": "conflict", "kept": kept, "files": done}
    except BaseException:
        _abort(repo)
        raise
    finally:
        if _merging(repo):
            _abort(repo)


# ---------------------------------------------------------------------------------------- a note for Claude Code

def _request_path(repo):
    return Path(repo) / ".overleaf-skill" / "requests" / "merge.json"


def request(repo, info):
    """The author asks Claude Code to do the join. -> the sentence to say to Claude Code."""
    from . import gitguard
    gitguard.ensure_ignored(str(repo))                   # the notes folder must never be sent to Overleaf with the paper
    p = _request_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    note = {"created": time.time(), "branch": upstream(repo), "files": info.get("files", []), "places": info.get("places", [])[:20]}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(note, indent=2))
    os.replace(tmp, p)
    return "Resolve the Overleaf merge conflict I handed you."


def pending(repo):
    try:
        return json.loads(_request_path(repo).read_text())
    except (OSError, ValueError):
        return None


def clear(repo):
    try:
        _request_path(repo).unlink()
    except OSError:
        pass


def describe(note):
    if not note:
        return ""
    files = ", ".join(note.get("files") or []) or "some files"
    lines = [f"The author handed you a merge conflict with Overleaf in: {files}. Their saved change and Overleaf's newer edit changed the same lines.",
             "Join them without losing either: git fetch, git merge " + (note.get("branch") or "origin/<branch>") + ", read both versions of each marked place, keep the "
             "meaning of both where they fit together (ask the author when they cannot), remove the marks, commit, then overleaf_sync. See SKILL.md, 'Resolving a merge conflict with Overleaf'."]
    for pl in note.get("places", [])[:6]:
        lines.append(f"- {pl.get('file')}: Overleaf has {pl.get('overleaf', '')[:160]!r}; the author has {pl.get('mine', '')[:160]!r}")
    return "\n".join(lines)
