"""What this paper's author has accepted and rejected, as a short digest the next ChatGPT request carries.

Built only from <paper>/.overleaf-skill/decisions.jsonl (this paper's own history; never another paper's), shown on the Paper index
page exactly as the model sees it, and erasable with one click. It nudges, it never forces: the model is told these are the author's
observed preferences, to be weighed with the book rules.
"""
import json
import os
import time
from collections import Counter

from . import config, store

MAX_CHARS = 1400
MIN_REJECTIONS = 2


def read(proj, limit=300):
    path = config.paper_dir(proj) / "decisions.jsonl"
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except FileNotFoundError:
        pass
    return rows


def _ex(row, n=150):
    one = lambda s: " ".join((s or "").split())[:n]  # noqa: E731
    return f'"{one(row.get("old"))}" -> "{one(row.get("new"))}"'


def digest(proj, max_chars=MAX_CHARS):
    rows = read(proj)
    if not rows:
        return ""
    acc, rej = Counter(), Counter()
    for r in rows:
        for rule in r.get("rules") or []:
            (acc if r["status"] == "accepted" else rej)[rule] += 1
    lines = []
    for rule, n in rej.most_common(5):
        if n >= MIN_REJECTIONS and n > acc[rule]:
            lines.append(f"- The author rejected changes based on book rule {rule} {n} times (accepted {acc[rule]}): use it only where clearly needed.")
    rejected = [r for r in rows if r["status"] == "rejected"][-4:]
    accepted = [r for r in rows if r["status"] == "accepted"][-3:]
    if rejected:
        lines.append("- Recently rejected (do not repeat this kind of change):")
        lines += [f"  * {_ex(r)}" + (f" [{'; '.join(r.get('reasons') or [])[:100]}]" if r.get("reasons") else "") for r in rejected]
    if accepted:
        lines.append("- Recently accepted (the style the author likes):")
        lines += [f"  * {_ex(r)}" for r in accepted]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def stats(proj):
    rows = read(proj)
    return {"accepted": sum(1 for r in rows if r["status"] == "accepted"), "rejected": sum(1 for r in rows if r["status"] == "rejected")}


def forget(proj):
    """Erase this paper's decision history (the log itself, not the manuscript). Returns how many entries were removed."""
    path = config.paper_dir(proj) / "decisions.jsonl"
    n = len(read(proj, limit=10 ** 9))
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return n
