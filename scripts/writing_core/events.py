"""Append-only, metadata-only event log. Feeds the Monitor page, `writing.py stats` and the trace lines.

One JSONL file per month inside a scope directory: a paper's `<paper>/.overleaf-skill/` or a book folder.
Metadata only: ids, counts, scores, tokens, timings, validator results. Strings are clipped so passage text cannot end
up here by accident. Logging never raises: monitoring must not break the feature being monitored.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_STR = 240
MAX_ITEMS = 200

BOOK_INGEST, INDEX_REFRESH, RETRIEVE, REVISE, DECISION, DOI_ADDED, ERROR, HANDOFF, REVIEW = (
    "book_ingest", "index_refresh", "retrieve", "revise", "decision", "doi_added", "error", "handoff", "review")


def _clip(v, depth=0):
    if isinstance(v, str):
        return v if len(v) <= MAX_STR else v[:MAX_STR - 1] + "…"
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, dict) and depth < 3:
        return {str(k)[:60]: _clip(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)) and depth < 3:
        return [_clip(x, depth + 1) for x in list(v)[:MAX_ITEMS]]
    return str(v)[:MAX_STR]


def month_file(scope_dir, ts=None):
    return Path(scope_dir) / ("events-%s.jsonl" % time.strftime("%Y-%m", time.gmtime(ts or time.time())))


def log(scope_dir, type_, **fields):
    """Append one event; returns the event dict (or None if it could not be written)."""
    ev = {"v": 1, "ts": round(time.time(), 3), "type": type_}
    ev.update({k: _clip(v) for k, v in fields.items()})
    try:
        from . import gitguard  # imported here: gitguard has no dependencies, this just avoids load-order surprises
        gitguard.guard_scope(scope_dir)  # never write into a paper's .overleaf-skill/ before git is told to ignore it
        p = month_file(scope_dir, ev["ts"])
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return ev
    except (OSError, RuntimeError):
        return None


def read(scope_dir, since=None, types=None):
    """All events in a scope directory, oldest first. Bad lines are skipped."""
    out = []
    for p in sorted(Path(scope_dir).glob("events-*.jsonl")):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since and ev.get("ts", 0) < since:
                continue
            if types and ev.get("type") not in types:
                continue
            out.append(ev)
    return out


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
