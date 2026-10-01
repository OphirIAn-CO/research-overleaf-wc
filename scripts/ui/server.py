#!/usr/bin/env python3
"""The one place: a local web app for the Overleaf skill and its writing coach. Stdlib only, no external resources.

Meant to run in Docker on demand (`./ui.sh up`), published on 127.0.0.1 only. Safety, all enforced here:
  * refuses to bind a non-loopback address unless it is inside the container (OVERLEAF_UI_IN_DOCKER=1);
  * a per-launch access token is exchanged once for an HttpOnly SameSite=Strict cookie, then redirected away from the URL;
  * Host header allow-list (anti DNS-rebinding), Origin + X-Requested-With checks on every POST (anti cross-site requests);
  * strict CSP (no inline script/style, no external hosts), nosniff, no-store; manuscript/book/model text is only ever inserted as text;
  * request bodies capped, ids validated, no shell strings, no secrets in responses or logs, idle shutdown.
"""
import argparse
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402
import doi_bot  # noqa: E402  (DOI -> BibTeX, shared with the Telegram bot)
import overleaf_mcp  # noqa: E402  (git pull/status/sync with the build-file filter, reused as is)
from writing_core import (book_index, chunk_book, config, events, health, openai_client, paper_index, retrieve, revise,  # noqa: E402
                          stats, taxonomy, watermark)

STATIC = Path(__file__).resolve().parent / "static"
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/charts.js": ("charts.js", "text/javascript; charset=utf-8"), "/pages.js": ("pages.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
MAX_BODY = 1 << 20
PID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


class Jobs:
    """Slow operations (book import, ChatGPT calls) run in a thread; the page polls /api/jobs/<id>."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def submit(self, label, fn):
        jid = secrets.token_hex(6)
        with self.lock:
            self.items[jid] = {"id": jid, "label": label, "status": "running", "started": time.time()}
            for old in sorted(self.items, key=lambda k: self.items[k]["started"])[:-40]:
                self.items.pop(old, None)

        def run():
            try:
                res = fn()
                with self.lock:
                    self.items[jid].update(status="done", result=res, finished=time.time())
            except Exception as e:  # noqa: BLE001
                msg = friendly(e)
                if msg is None:
                    traceback.print_exc(file=sys.stderr)
                    msg = "internal error (see the container log)"
                with self.lock:
                    self.items[jid].update(status="error", error=msg, finished=time.time())

        threading.Thread(target=run, daemon=True).start()
        return jid

    def get(self, jid):
        with self.lock:
            return dict(self.items[jid]) if jid in self.items else None


def friendly(e):
    """Message for errors we expect and can explain; None for anything else (-> logged, generic 500)."""
    known = (book_index.BookError, config.ConfigError, paper_index.IsolationError, revise.ReviseError, ValueError, FileNotFoundError,
             watermark.WatermarkError)
    if isinstance(e, openai_client.OpenAIError):
        return f"OpenAI error ({e.kind}): {e}"
    if isinstance(e, known):
        return str(e)
    if isinstance(e, chunk_book.ToolMissing):
        return str(e)
    return None


class App:
    def __init__(self, token, port, public_port=None, idle_minutes=None):
        self.token = token
        self.port = port
        self.public_port = public_port or port
        self.sessions = set()
        self.jobs = Jobs()
        self.last_activity = time.time()
        self.idle_seconds = (idle_minutes if idle_minutes is not None else config.ui_cfg()["idle_minutes"]) * 60

    def host_ok(self, host):
        """Only loopback names on this app's own port: a page on another origin cannot reach us through a rebinding trick."""
        h = (host or "").strip().lower()
        if h.startswith("["):  # [::1]:8765
            name, _, rest = h.partition("]")
            name, port = name + "]", rest[1:] if rest.startswith(":") else ""
        else:
            name, _, port = h.partition(":")
        if name not in LOOPBACK:
            return False
        return port == str(self.public_port) or (port == "" and self.public_port == 80)

    def new_session(self):
        sid = secrets.token_hex(24)
        self.sessions.add(sid)
        return sid


def paper_ref(name):
    reg = common.registry()["projects"]
    if name not in reg:
        raise ApiError(404, f"unknown paper {name!r}")
    try:
        return common.resolve(name)
    except RuntimeError as e:
        raise ApiError(409, f"refusing to act: {e}") from None


def need(body, *keys):
    for k in keys:
        if body.get(k) in (None, ""):
            raise ApiError(400, f"missing {k}")
    return [body[k] for k in keys]


# ---------------------------------------------------------------------------- API handlers (req -> json-able)

class Req:
    def __init__(self, app, match, query, body):
        self.app, self.m, self.q, self.body = app, match, query, body

    def arg(self, key, default=None):
        v = self.q.get(key)
        return v[0] if v else default


def api_state(r):
    reg = common.registry()
    active, source, age = common.get_active()
    papers = []
    for n, p in reg["projects"].items():
        try:
            problem = common.verify(n, p)
        except Exception as e:  # noqa: BLE001
            problem = str(e)
        papers.append({"name": n, "path": p["path"], "bib": p["bib"], "group": (p.get("mendeley_group") or {}).get("name"),
                       "remote_id": common.remote_id(p), "problem": problem, "index": paper_index.peek(p),
                       "settings": config.paper_settings(p), "default": n == reg["default"]})
    books = []
    for b in config.list_books():
        m = book_index.manifest(b) or {}
        books.append({"id": b, "chunks": m.get("n_chunks"), "pages": m.get("n_pdf_pages"), "source": m.get("source_name"), "method": m.get("method"),
                      "built_at": m.get("built_at")})
    ui = config.ui_cfg()
    ostate, omsg = config.openai_status()
    token_present = (config.data_dir() / "overleaf_git.json").exists()
    return {"papers": papers, "active": active, "active_source": source, "active_age": age, "books": books, "parts": sorted(taxonomy.PARTS),
            "inbox": sorted(p.name for p in config.inbox_dir().glob("*.pdf")) if config.inbox_dir().is_dir() else [],
            "settings": {"ui": {"idle_minutes": ui["idle_minutes"], "watermark_url": ui["watermark_url"]},
                         "openai": {"state": ostate, "detail": omsg if ostate == "ok" else omsg.split(". ")[0][:120]},
                         "git_token": token_present, "in_docker": os.environ.get("OVERLEAF_UI_IN_DOCKER") == "1"},
            "idle_seconds_left": max(0, int(r.app.last_activity + r.app.idle_seconds - time.time()))}


def api_set_active(r):
    (name,) = need(r.body, "name")
    paper_ref(name)
    common.set_active(name, "ui")
    return {"active": name}


def api_git(r, action):
    (name,) = need(r.body, "name")
    paper_ref(name)
    args = {"repo_path": name, "_source": "ui"}
    fn = {"pull": overleaf_mcp.tool_pull, "status": overleaf_mcp.tool_status, "sync": overleaf_mcp.tool_sync}[action]
    if action == "sync":
        args["message"] = str(r.body.get("message") or "Update via the Overleaf skill web app")[:200]
    out = fn(args)
    if action in ("pull", "sync"):
        try:  # keep the paper's index in step with what Overleaf just gave us
            n, p = common.resolve(name)
            paper_index.refresh(n, p, trigger="after_pull" if action == "pull" else "after_sync", source="web")
        except Exception:  # noqa: BLE001
            pass
    return {"output": out}


def api_paper_outline(r):
    n, p = paper_ref(r.m.group(1))
    return paper_index.outline(n, p, source="web")


def api_paper_refresh(r):
    n, p = paper_ref(r.m.group(1))
    return paper_index.refresh(n, p, trigger="manual", force=bool(r.body.get("force")), source="web")


def api_paper_chunks(r):
    n, p = paper_ref(r.m.group(1))
    res = paper_index.query(n, p, r.arg("q", ""), part=r.arg("part") or None, kind=r.arg("kind") or None,
                            budget=int(r.arg("budget", 4000)), source="web")
    return res


def api_books(r):
    return {"books": [dict(id=b, **{k: (book_index.manifest(b) or {}).get(k) for k in ("n_chunks", "n_pdf_pages", "source_name", "method", "tokens")})
                      for b in config.list_books()]}


def book_id(r):
    b = r.m.group(1)
    config.book_dir(b)
    if b not in config.list_books():
        raise ApiError(404, f"book {b!r} is not imported")
    return b


def api_book_tree(r):
    return {"book": book_id(r), "tree": book_index.tree(book_id(r)), "info": book_index.info(book_id(r))}


def api_book_chunk(r):
    return book_index.get_chunk(book_id(r), r.arg("id", ""))


def api_book_query(r):
    res = retrieve.book_query(book_id(r), r.arg("q", ""), part=r.arg("part") or None, budget=int(r.arg("budget", 2000)), source="ui")
    return res


def api_book_outline_get(r):
    o = book_index.read_outline(book_id(r))
    if not o:
        raise ApiError(404, "no outline.json")
    return o


def api_book_outline_save(r):
    b = book_id(r)
    o = r.body.get("outline")
    if not isinstance(o, dict) or not isinstance(o.get("entries"), list):
        raise ApiError(400, "outline must be an object with an entries list")
    keep = []
    for e in o["entries"]:
        try:
            keep.append({"level": int(e["level"]), "number": str(e.get("number", "")), "title": str(e["title"])[:200],
                         "pdf_page": int(e["pdf_page"]), "page": int(e.get("page", e["pdf_page"])),
                         "verified": bool(e.get("verified", True)), "skip": bool(e.get("skip"))})
        except (KeyError, TypeError, ValueError):
            raise ApiError(400, "each entry needs level, title and pdf_page") from None
    o = {**o, "entries": keep}
    book_index.write_outline(b, o)
    return {"saved": len(keep)}


def api_ingest(r):
    pdf, bid = need(r.body, "pdf", "id")
    mode = r.body.get("mode", "build")
    if mode not in ("outline", "build", "rebuild", "force"):
        raise ApiError(400, "mode must be outline, build, rebuild or force")
    config.book_dir(bid)
    name = os.path.basename(str(pdf))
    path = config.inbox_dir() / name
    if name != pdf or not name.lower().endswith(".pdf") or not path.is_file():
        raise ApiError(404, "that PDF is not in the inbox folder")

    def job():
        res = book_index.ingest(path, bid, force=mode == "force", rebuild=mode == "rebuild", outline_only=mode == "outline", source="web")
        out = {"status": res["status"], "warnings": res.get("warnings", []), "note": res.get("note")}
        if res.get("manifest"):
            m = res["manifest"]
            out.update(chunks=m["n_chunks"], chapters=m["n_chapters"], pages=m["n_pdf_pages"], method=m["method"])
        if res.get("outline"):
            out.update(entries=len(res["outline"]["entries"]), method=res["outline"]["method"], warnings=res["outline"].get("warnings", []))
        return out
    return {"job": r.app.jobs.submit(f"book import {bid} ({mode})", job)}


def api_job(r):
    j = r.app.jobs.get(r.m.group(1))
    if not j:
        raise ApiError(404, "no such job")
    return j


def _revise_args(body):
    paper, instruction = need(body, "paper", "instruction")
    n, p = paper_ref(paper)
    return n, p, dict(instruction=str(instruction)[:4000], part=body.get("part") or None, chunk_ids=body.get("chunk_ids") or None,
                      book_id=body.get("book_id") or None, use_book=body.get("use_book", True) is not False)


def api_revise_preview(r):
    n, p, kw = _revise_args(r.body)
    res = revise.run(n, p, dry_run=True, source="web", **kw)
    res["text"] = revise.format_preview(res)
    return res


def api_revise_run(r):
    n, p, kw = _revise_args(r.body)
    confirm = r.body.get("confirm")

    def job():
        res = revise.run(n, p, dry_run=False, confirm=confirm, source="web", **kw)
        if res["status"] == "preview":
            raise ValueError("the payload changed since the preview; preview it again before sending")
        return {"status": res["status"], "proposal": res["proposal"]["id"]}
    return {"job": r.app.jobs.submit(f"ChatGPT pass on {n}", job)}


def _trim(p):
    """The proposal as the page needs it (without the LaTeX placeholder maps and segment lists)."""
    q = {k: v for k, v in p.items() if k != "units"}
    q["units"] = [{k: v for k, v in u.items() if k not in ("mapping", "masked_original", "segments")} for u in p["units"]]
    return q


def _pid(r):
    pid = r.arg("id") or r.body.get("id") or ""
    if not PID_RE.match(pid):
        raise ApiError(400, "bad proposal id")
    return pid


def api_proposals(r):
    n, p = paper_ref(r.arg("paper", ""))
    return {"proposals": revise.list_proposals(p)}


def api_proposal(r):
    n, p = paper_ref(r.arg("paper", ""))
    return _trim(revise.get_proposal(p, _pid(r)))


def api_apply(r):
    n, p = paper_ref(r.body.get("paper", ""))
    pid = _pid(r)
    url = config.ui_cfg()["watermark_url"]
    cleaner = watermark.make_cleaner(url) if url else None
    accept = r.body.get("accept", "all")
    try:
        return revise.apply(n, p, pid, accept=accept, cleaner=cleaner, source="web")
    except revise.ReviseError as e:
        raise ApiError(409, str(e), handoff_available=True) from None


def api_record(r):
    n, p = paper_ref(r.body.get("paper", ""))
    return revise.record(n, p, _pid(r), accepted=r.body.get("accepted"), rejected=r.body.get("rejected"),
                         applied=bool(r.body.get("applied")), synced=bool(r.body.get("synced")), source="web", note=str(r.body.get("note", ""))[:300])


def api_monitor(r):
    paper = r.arg("paper") or None
    if paper:
        paper_ref(paper)
    return stats.monitor(paper=paper, days=max(1, min(int(r.arg("days", 30)), 365)))


def api_events(r):
    since = float(r.arg("since", 0) or 0)
    rows = []
    for n, p in common.registry()["projects"].items():
        d = config.paper_dir(p)
        if d.exists():
            rows += [dict(e, scope=n) for e in events.read(d, since=since or None)]
    for b in config.list_books():
        rows += [dict(e, scope=f"book {b}") for e in events.read(config.book_dir(b), since=since or None)]
    rows.sort(key=lambda e: e["ts"])
    return {"events": rows[-150:], "now": time.time()}


def api_health(r):
    issues = [{"scope": "global", "text": t} for t in health.env_issues()]
    for n, p in common.registry()["projects"].items():
        v = common.verify(n, p)
        if v:
            issues.append({"scope": n, "text": v})
        issues += [{"scope": n, "text": t} for t in health.paper_issues(n, p)]
    wm = config.ui_cfg()["watermark_url"]
    wstate, wdetail = watermark.check(wm)
    if wstate != "ok":
        issues.append({"scope": "global", "text": f"watermark service (step 6b): {wstate}. Apply stays blocked; use Hand to Claude. {wdetail}"})
    if not (config.data_dir() / "overleaf_git.json").exists():
        issues.append({"scope": "global", "text": "no overleaf_git.json: Pull and Sync from this app need your Overleaf Git token (see README)"})
    return {"issues": issues, "watermark": {"state": wstate, "detail": wdetail}}


def api_settings(r):
    body = r.body
    if body.get("paper") is not None and "dry_run" in body:
        n, p = paper_ref(body["paper"])
        config.set_paper_setting(p, "dry_run", bool(body["dry_run"]))
    ui = body.get("ui")
    if isinstance(ui, dict):
        cur = common.load("ui.json", {}) or {}
        if "idle_minutes" in ui:
            try:
                cur["idle_minutes"] = max(5, min(int(ui["idle_minutes"]), 24 * 60))
            except (TypeError, ValueError):
                raise ApiError(400, "idle_minutes must be a number") from None
        if "watermark_url" in ui:
            u = str(ui["watermark_url"] or "").strip()
            if u and not re.match(r"^https?://[A-Za-z0-9_.:\-\[\]]+(/[^\s]*)?$", u):
                raise ApiError(400, "watermark_url must be an http(s) URL")
            cur["watermark_url"] = u or None
        common.save("ui.json", cur)
    return {"ok": True}


CARD_TEMPLATE = """# Paper card

Short facts that every ChatGPT request for this paper carries, so terminology and style stay consistent.
Keep it under about 40 lines. Only this paper's own facts.

## Research question
-

## Contribution
-

## Key terms (one canonical spelling each)
- say "...", never "..."

## Style
- Tense: past for what we did and found, present for established knowledge
- Voice:

## Notation and units
-

## Target venue
-

## Decisions
-
"""
CARD_MAX = 20000


def api_card_get(r):
    n, p = paper_ref(r.m.group(1))
    path = config.paper_dir(p) / "paper_card.md"
    try:
        return {"exists": True, "text": path.read_text(encoding="utf-8"), "template": CARD_TEMPLATE}
    except FileNotFoundError:
        return {"exists": False, "text": "", "template": CARD_TEMPLATE}


def api_card_save(r):
    n, p = paper_ref(r.m.group(1))
    text = r.body.get("text")
    if not isinstance(text, str) or len(text) > CARD_MAX:
        raise ApiError(400, f"text must be a string of at most {CARD_MAX} characters")
    d = config.ensure_paper_dir(p)
    tmp = d / f".paper_card.{os.getpid()}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, d / "paper_card.md")
    return {"saved": len(text)}


def api_decisions(r):
    n, p = paper_ref(r.m.group(1))
    path = config.paper_dir(p) / "decisions.jsonl"
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-100:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except FileNotFoundError:
        pass
    return {"decisions": rows[::-1]}


def api_index_list(r):
    n, p = paper_ref(r.m.group(1))
    paper_index.refresh(n, p, trigger="view", source="web")
    return paper_index.list_chunks(n, p)


def api_index_chunk(r):
    n, p = paper_ref(r.m.group(1))
    got = paper_index.get_chunks(n, p, [r.arg("id", "")])
    if not got:
        raise ApiError(404, "no such chunk")
    return got[0]


def _clean_dois(text):
    return list(dict.fromkeys(d.rstrip(".,;)") for d in doi_bot.DOI_RE.findall(urllib.parse.unquote(str(text)))))[:20]


def api_refs_preview(r):
    n, p = paper_ref(r.body.get("paper", ""))
    dois = _clean_dois(r.body.get("text", ""))
    if not dois:
        raise ApiError(400, "no DOI found in that text")
    existing = Path(common.bib_path(p)).read_text(encoding="utf-8", errors="replace").lower()
    out = []
    for d in dois:
        if d.lower() in existing:
            out.append({"doi": d, "status": "duplicate"})
            continue
        try:
            bib, key = doi_bot.restyle(doi_bot.fetch_bibtex(d))
            title = (re.search(r"title\s*=\s*[{\"]+(.+?)[}\"]+,?\s*\n", bib, re.S | re.I) or [0, ""])[1]
            out.append({"doi": d, "status": "new", "key": key, "bibtex": bib, "title": re.sub(r"\s+", " ", title)})
        except Exception as e:  # noqa: BLE001  (404 = unknown DOI, otherwise network)
            out.append({"doi": d, "status": "error", "error": str(e)[:200]})
    return {"paper": n, "group": (p.get("mendeley_group") or {}).get("name"), "refs": out}


def api_refs_add(r):
    n, p = paper_ref(r.body.get("paper", ""))
    dois = [d for d in (r.body.get("dois") or []) if isinstance(d, str)][:20]
    if not dois:
        raise ApiError(400, "no DOIs to add")
    group = (p.get("mendeley_group") or {}).get("name") if r.body.get("mendeley") else None
    common.set_active(n, "ui")
    msgs = [doi_bot.add(d, common.bib_path(p), False, group, source="web") for d in dois]
    out = {"messages": msgs}
    if r.body.get("push"):
        ok, text = doi_bot.git_sync(common.bib_path(p), "Add references (web app)")
        out["push"] = {"ok": ok, "text": text}
    paper_index.refresh(n, p, trigger="after_edit", source="web")
    return out


def api_refs_recent(r):
    n, p = paper_ref(r.arg("paper", ""))
    evs = [e for e in events.read(config.paper_dir(p), types={"doi_added"})][-30:][::-1]
    idx = paper_index.peek(p) or {"counts": {}}
    return {"recent": [{k: e.get(k) for k in ("ts", "source", "doi", "key", "mendeley")} for e in evs], "bib_entries": idx["counts"].get("bib", 0)}


def api_refs_mendeley(r):
    n, p = paper_ref(r.body.get("paper", ""))
    out = overleaf_mcp.tool_mendeley_fetch({"repo_path": n, "_source": "ui"})
    paper_index.refresh(n, p, trigger="after_edit", source="web")
    return {"output": out}


def api_handoff(r):
    n, p = paper_ref(r.body.get("paper", ""))
    return revise.handoff(n, p, _pid(r), accept=r.body.get("accept", "all"), source="web")


ROUTES = [
    ("GET", r"^/api/state$", api_state), ("POST", r"^/api/papers/active$", api_set_active),
    ("POST", r"^/api/papers/(pull|status|sync)$", None),
    ("GET", r"^/api/paper/([^/]+)/outline$", api_paper_outline), ("POST", r"^/api/paper/([^/]+)/refresh$", api_paper_refresh),
    ("GET", r"^/api/paper/([^/]+)/chunks$", api_paper_chunks),
    ("GET", r"^/api/books$", api_books), ("GET", r"^/api/book/([A-Za-z0-9_-]+)/tree$", api_book_tree),
    ("GET", r"^/api/book/([A-Za-z0-9_-]+)/chunk$", api_book_chunk), ("GET", r"^/api/book/([A-Za-z0-9_-]+)/query$", api_book_query),
    ("GET", r"^/api/book/([A-Za-z0-9_-]+)/outline$", api_book_outline_get), ("POST", r"^/api/book/([A-Za-z0-9_-]+)/outline$", api_book_outline_save),
    ("POST", r"^/api/book/ingest$", api_ingest), ("GET", r"^/api/jobs/([0-9a-f]{12})$", api_job),
    ("POST", r"^/api/revise/preview$", api_revise_preview), ("POST", r"^/api/revise/run$", api_revise_run),
    ("GET", r"^/api/proposals$", api_proposals), ("GET", r"^/api/proposal$", api_proposal),
    ("POST", r"^/api/proposal/apply$", api_apply), ("POST", r"^/api/proposal/record$", api_record),
    ("GET", r"^/api/monitor$", api_monitor), ("GET", r"^/api/events$", api_events),
    ("GET", r"^/api/health$", api_health), ("POST", r"^/api/settings$", api_settings),
    ("GET", r"^/api/paper/([^/]+)/card$", api_card_get), ("POST", r"^/api/paper/([^/]+)/card$", api_card_save),
    ("GET", r"^/api/paper/([^/]+)/decisions$", api_decisions), ("GET", r"^/api/paper/([^/]+)/index$", api_index_list),
    ("GET", r"^/api/paper/([^/]+)/chunk$", api_index_chunk),
    ("POST", r"^/api/refs/preview$", api_refs_preview), ("POST", r"^/api/refs/add$", api_refs_add), ("GET", r"^/api/refs/recent$", api_refs_recent),
    ("POST", r"^/api/refs/mendeley$", api_refs_mendeley), ("POST", r"^/api/proposal/handoff$", api_handoff),
]
ROUTES = [(m, re.compile(p), fn) for m, p, fn in ROUTES]
EXTENSIONS = []  # (method, compiled regex, fn): later phases register their pages here


def register(method, pattern, fn):
    EXTENSIONS.append((method, re.compile(pattern), fn))


# ---------------------------------------------------------------------------- HTTP plumbing

class Handler(BaseHTTPRequestHandler):
    server_version = "OverleafUI/1"
    protocol_version = "HTTP/1.0"  # one request per connection: an early error reply can never desync a body we did not read

    @property
    def app(self):
        return self.server.app

    def log_request(self, code="-", size="-"):  # the default line carries the full request target, i.e. ?token=...: log the path only
        sys.stderr.write("%s %s %s -> %s\n" % (time.strftime("%H:%M:%S"), self.command, urlparse(self.path).path, code))

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), (fmt % args if args else fmt).split("?")[0]))

    def _send(self, status, body=b"", ctype="application/json", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, status, msg, **extra):
        self._send(status, {"error": msg, **extra})

    def _cookie_ok(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "ols_session" and v in self.app.sessions:
                return True
        return False

    def _handle(self, method):
        u = urlparse(self.path)
        path, query = u.path, parse_qs(u.query)
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        if not self.app.host_ok(self.headers.get("Host")):
            return self._err(403, "bad Host header")
        if not self.headers.get("X-Poll"):
            self.app.last_activity = time.time()
        if path == "/" and "token" in query:
            if hmac.compare_digest(query["token"][0], self.app.token):
                sid = self.app.new_session()
                return self._send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": f"ols_session={sid}; HttpOnly; SameSite=Strict; Path=/"})
            time.sleep(0.5)
            return self._err(403, "wrong token")
        if not self._cookie_ok():
            if path.startswith("/api/"):
                return self._err(401, "not signed in: open the URL printed by ui.sh (it carries the access token)")
            return self._send(401, b"<!doctype html><meta charset=utf-8><title>Sign in</title><p>Open the URL printed by <code>ui.sh up</code> "
                                   b"(it contains the access token).", "text/html; charset=utf-8")
        body = {}
        if method == "POST":
            origin = self.headers.get("Origin")
            if origin is not None and not self.app.host_ok(urlparse(origin).netloc):
                return self._err(403, "bad Origin")
            if origin is None and self.headers.get("Sec-Fetch-Site") not in ("same-origin", None):
                return self._err(403, "cross-site request refused")
            if self.headers.get("X-Requested-With") != "ols":
                return self._err(403, "missing X-Requested-With")
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._err(413, "request too large")
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._err(400, "body must be a JSON object")
        if method == "GET" and path in FILES:
            name, ctype = FILES[path]
            return self._send(200, (STATIC / name).read_bytes(), ctype)
        for m, rx, fn in ROUTES + EXTENSIONS:
            mt = rx.match(path)
            if m == method and mt:
                try:
                    req = Req(self.app, mt, query, body)
                    if fn is None:
                        out = api_git(req, mt.group(1))
                    else:
                        out = fn(req)
                    return self._send(200, out)
                except ApiError as e:
                    return self._err(e.status, str(e), **e.extra)
                except Exception as e:  # noqa: BLE001
                    msg = friendly(e)
                    if msg is not None:
                        return self._err(400, msg)
                    traceback.print_exc(file=sys.stderr)
                    return self._err(500, "internal error (see the container log)")
        return self._err(404, "not found")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_HEAD(self):
        self._handle("GET")

    def do_PUT(self):
        self._err(405, "method not allowed")

    do_DELETE = do_PATCH = do_PUT


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, app):
        self.app = app
        super().__init__(addr, Handler)


def is_loopback(host):
    return host in ("127.0.0.1", "localhost", "::1")


def make_server(bind, port, token, public_port=None, idle_minutes=None):
    if not is_loopback(bind) and os.environ.get("OVERLEAF_UI_IN_DOCKER") != "1":
        raise SystemExit(f"refusing to listen on {bind}: outside the container the app only binds to loopback")
    app = App(token, port, public_port, idle_minutes)
    srv = Server((bind, port), app)
    app.port = srv.server_address[1]
    if public_port is None:
        app.public_port = app.port
    return srv


IDLE_POLL = 15


def idle_watch(srv):
    while True:
        time.sleep(IDLE_POLL)
        app = srv.app
        if time.time() - app.last_activity > app.idle_seconds:
            sys.stderr.write("idle timeout reached: shutting down\n")
            threading.Thread(target=srv.shutdown, daemon=True).start()
            return


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bind", default=os.environ.get("OVERLEAF_UI_BIND", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("OVERLEAF_UI_PORT", config.ui_cfg()["port"])))
    ap.add_argument("--public-port", type=int, default=int(os.environ.get("OVERLEAF_UI_PUBLIC_PORT", 0)) or None)
    ap.add_argument("--token-file", default=os.environ.get("OVERLEAF_UI_TOKEN_FILE"))
    args = ap.parse_args(argv)
    home = os.environ.get("HOME")
    if home:
        os.makedirs(home, exist_ok=True)
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    os.environ.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1000")
    os.environ.setdefault("GIT_HTTP_LOW_SPEED_TIME", "30")
    if args.token_file:
        token = Path(args.token_file).read_text().strip()
        if len(token) < 24:
            raise SystemExit("token file is empty or too short")
        shown = False
    else:
        token, shown = secrets.token_urlsafe(32), True
    srv = make_server(args.bind, args.port, token, args.public_port)
    if shown:
        print(f"Open: http://127.0.0.1:{srv.app.public_port}/?token={token}", flush=True)
    print(f"listening on {args.bind}:{srv.app.port}; idle shutdown after {srv.app.idle_seconds // 60} min", file=sys.stderr, flush=True)
    threading.Thread(target=idle_watch, args=(srv,), daemon=True).start()
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    srv.server_close()


if __name__ == "__main__":
    main()
