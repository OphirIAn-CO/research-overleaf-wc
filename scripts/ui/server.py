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
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
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
from writing_core import (activity, book_index, chat, chunk_book, config, draft, events, files, gitmerge, health, intent, latex_mask, latexbuild, layout, library, openai_client, overleaf_auth, panes, paper_index,  # noqa: E402
                          preferences, retrieve, review, revise, rule_cards, scholar, stats, taxonomy, watermark, webview)

STATIC = Path(__file__).resolve().parent / "static"
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/charts.js": ("charts.js", "text/javascript; charset=utf-8"), "/pages.js": ("pages.js", "text/javascript; charset=utf-8"),
         "/panes.js": ("panes.js", "text/javascript; charset=utf-8"), "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
         "/settings.js": ("settings.js", "text/javascript; charset=utf-8"), "/latex.js": ("latex.js", "text/javascript; charset=utf-8"),
         "/library.js": ("library.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-src " + " ".join(panes.FRAME_HOSTS) + "; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
MAX_BODY = 1 << 20
UPLOAD_BODY = (files.MAX_UPLOAD * 4) // 3 + 8192      # a file arrives as base64 inside the JSON body
LATEX_BODY = latexbuild.MAX_FILE * 2 + 8192             # a saved .tex or .bib file arrives as JSON text (quotes and newlines are escaped, so it can grow)
BOOK_MAX = 200 << 20                                    # bytes: the writing book's PDF, uploaded from Settings (streamed to disk, never held in memory)
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

    def submit(self, label, fn, with_progress=False, with_steps=False, meta=None):
        jid = secrets.token_hex(6)
        with self.lock:
            self.items[jid] = {"id": jid, "label": label, "status": "running", "started": time.time(), "meta": meta or {}}
            for old in sorted(self.items, key=lambda k: self.items[k]["started"])[:-40]:
                self.items.pop(old, None)

        def progress(done, total):
            with self.lock:
                self.items[jid]["progress"] = [done, total]

        def set_steps(steps):
            with self.lock:
                self.items[jid]["steps"] = steps

        def run():
            try:
                res = fn(progress, set_steps) if with_steps else fn(progress) if with_progress else fn()
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

    def running(self, **meta):
        with self.lock:
            return [dict(j) for j in self.items.values() if j["status"] == "running" and all(j.get("meta", {}).get(k) == v for k, v in meta.items())]

    def get(self, jid):
        with self.lock:
            return dict(self.items[jid]) if jid in self.items else None


def friendly(e):
    """Message for errors we expect and can explain; None for anything else (-> logged, generic 500)."""
    known = (book_index.BookError, config.ConfigError, paper_index.IsolationError, revise.ReviseError, review.ReviewError, ValueError, FileNotFoundError,
             watermark.WatermarkError, webview.BrowserError, latexbuild.LatexProblem, library.LibraryError, draft.DraftError, scholar.ScholarError)
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
        self._browser, self._browser_lock = None, threading.Lock()

    def browser(self):
        """The built-in browser (QuillBot / Overleaf inside the app): the browser container in Docker, a local Chromium otherwise, or a note on why not."""
        with self._browser_lock:
            if self._browser is None:
                self._browser = webview.backend()
            return self._browser

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
                      "built_at": m.get("built_at"), "rule_cards": m.get("rule_cards", 0), "exercises": m.get("exercises", 0),
                      "outdated": (m.get("logic_version") or 1) < chunk_book.LOGIC_VERSION})
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


_AUTH_MARKERS = ("could not read password", "terminal prompts disabled", "authentication failed", "invalid username or password", "could not read username")


def git_action(name, action, message=None, join=False, push=True, paths=None):
    """pull | status | sync through the same code Claude uses. -> {output, [hint, friendly]}; the index follows a pull or sync."""
    paper_ref(name)
    args = {"repo_path": name, "_source": "ui"}
    fn = {"pull": overleaf_mcp.tool_pull, "status": overleaf_mcp.tool_status, "sync": overleaf_mcp.tool_sync, "files": overleaf_mcp.tool_send_files}[action]
    if action == "files":
        args["paths"] = list(paths or [])
        args["message"] = str(message or "Add files")[:200]
    if action == "sync":
        args["message"] = str(message or "Update via the Overleaf skill web app")[:200]
        if not push:
            args["push"] = False
    if action == "pull" and join:
        args["join"] = True
    out = fn(args)
    if any(m in out.lower() for m in _AUTH_MARKERS):
        have = (config.data_dir() / "overleaf_git.json").exists()
        if not have:
            return {"output": out, "hint": "token", "friendly": "This app is not signed in to Overleaf yet. In Settings, paste your Overleaf Git token once (Overleaf: Account Settings, Git Integration, create a token)."}
        why = _git_diagnosis(name)
        return {"output": out, "hint": "token", "friendly": ((why["message"] + " In Settings, press Test next to the Overleaf token to see what to do.") if why and not why["ok"] else
                "Overleaf did not accept the sign-in. Your Overleaf Git token is not accepted: create a new one in Overleaf (Account Settings, Git Integration) and paste it in Settings.")}
    if action in ("pull", "sync", "files"):
        try:  # keep the paper's index in step with what Overleaf just gave us
            n, p = common.resolve(name)
            paper_index.refresh(n, p, trigger="after_pull" if action == "pull" else "after_sync", source="web")
        except Exception:  # noqa: BLE001
            pass
    res = {"output": out, **plain_git(action, out)}
    if action == "sync" and res.get("sent") and res.get("level") != "bad":
        try:
            revise.mark_sent(common.resolve(name)[1])          # the chat can now say 'sent to Overleaf' for what was saved before this
        except Exception:  # noqa: BLE001
            pass
    if action in ("sync", "files") and res.get("sent"):
        try:
            res.update(_remote_has_it(common.resolve(name)[1]["path"]))
        except Exception:  # noqa: BLE001  (the check is a bonus: a push that git called ok is still reported as sent)
            pass
        if res.get("verified") is True:
            res["summary"] = f"Sent to Overleaf. Checked: Overleaf now has your change ({res['commit']})."
        elif res.get("verified") is False:
            res.update(level="warn", summary="Overleaf took your change, but its newest version is different now (was it edited there at the same moment?). Press \"Get latest from Overleaf\" and check.")
    return res


def _remote_has_it(path):
    """After a push git called ok: ask Overleaf which version it has now and compare. -> {commit, [verified]}; no 'verified' when Overleaf could not be asked."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def git(*a):
        r = subprocess.run(["git", "-C", path, *a], capture_output=True, text=True, timeout=25, env=env)
        return r.stdout.strip() if r.returncode == 0 else ""

    head, branch = git("rev-parse", "HEAD"), git("rev-parse", "--abbrev-ref", "HEAD")
    if not head or not branch:
        return {}
    out = {"commit": head[:7]}
    url = git("remote", "get-url", "--push", "origin")
    try:
        theirs = git("ls-remote", url or "origin", f"refs/heads/{branch}").split()
    except subprocess.TimeoutExpired:
        return out
    if theirs:
        out["verified"] = theirs[0] == head
    return out


REFRESH_WAIT = 1.5      # seconds between looks for the Recompile button after the page is loaded again


def refresh_overleaf(app, name, recompile=True):
    """Overleaf's editor does not follow a push by itself: a page that was open before still shows the old text, and so does its PDF. Reload the paper's Overleaf
    page in the built-in browser (whether or not its tab is on screen) and press Recompile once the editor is back. -> True when there was such a page."""
    try:
        if not config.ui_cfg().get("builtin_overleaf", True):
            return False
        n, p = common.resolve(name)
        b = app.browser()
        st = b.state("overleaf")
        pid = panes.overleaf_project_id(p)
        if not st.get("running") or not pid or f"/project/{pid}" not in (st.get("url") or ""):
            return False                                  # nothing open, or open on something else: nothing to bring up to date
    except Exception:  # noqa: BLE001
        return False

    def work():
        try:
            b.nav("overleaf", "reload")
            if not recompile:
                return
            for _ in range(24):                           # the editor needs some seconds to come back; press Recompile as soon as its button exists
                time.sleep(REFRESH_WAIT)
                if b.recompile("overleaf").get("clicked"):
                    return
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=work, daemon=True, name="overleaf-refresh").start()
    return True


_STATUS_WORDS = {"M": "edited", "A": "new", "D": "deleted", "R": "renamed", "?": "new", "U": "in conflict"}


def plain_git(action, out):
    """What git said, as a sentence an author can act on. -> {summary, level: ok|warn|bad, needs: None|'latest'|'claude'}"""
    low = out.lower()
    first = next((ln for ln in out.splitlines() if ln.strip()), "")
    if action == "pull":
        if low.startswith("pull failed"):
            if "could not join" in low:
                return {"summary": "The same lines were changed in Overleaf and here, so I could not join them by myself. Nothing was overwritten or lost. Below, choose which version to keep for each place, or ask Claude Code to join them.", "level": "warn", "needs": "choose"}
            if any(k in low for k in ("fast-forward", "diverg")):
                return {"summary": "Overleaf and this computer both have changes. Press the button to bring Overleaf's latest in and put your saved changes on top of it; if the same lines were changed on both sides it stops and tells you.", "level": "warn", "needs": "latest"}
            if any(k in low for k in ("conflict", "overwritten")):
                return {"summary": "Overleaf and this computer both have changes, and I did not mix them. Ask Claude to merge them safely; it never overwrites either side.", "level": "warn", "needs": "claude"}
            return {"summary": "I could not get the latest from Overleaf: " + first.replace("pull failed:", "").strip()[:200], "level": "bad", "needs": None}
        if "already up to date" in low:
            return {"summary": "You already have the latest from Overleaf.", "level": "ok", "needs": None}
        if low.startswith("joined:"):
            return {"summary": "Got the latest from Overleaf and put the changes saved here on top of it. Nothing was overwritten.", "level": "ok", "needs": None}
        files = re.findall(r"^\s*(\S.*?)\s+\|\s+\d+", out, re.M)
        more = "…" if len(files) > 6 else ""
        return {"summary": "Got the latest from Overleaf" + (": " + ", ".join(files[:6]) + more if files else "") + ".", "level": "ok", "needs": None}
    if action == "status":
        rows = [m.groups() for m in (re.match(r"^\s*([MADRU?]{1,2})\s+(.+)$", ln) for ln in out.splitlines()) if m]  # run_git strips the first line's leading space
        if not rows:
            return {"summary": "Nothing is waiting to be sent to Overleaf.", "level": "ok", "needs": None}
        items = [f"{f.strip()} ({_STATUS_WORDS.get(code[:1], 'changed')})" for code, f in rows[:8]]
        return {"summary": f"{len(rows)} file{'s' if len(rows) != 1 else ''} changed here and not sent yet: " + ", ".join(items) + ("…" if len(rows) > 8 else "") + ".", "level": "warn", "needs": None}
    if action in ("sync", "files"):
        if low.startswith("nothing to sync"):
            return {"summary": "Nothing to send: everything is already in Overleaf.", "level": "ok", "needs": None}
        if low.startswith("commit failed"):
            return {"summary": "I could not prepare the changes for Overleaf: " + first[:200], "level": "bad", "needs": None}
        if low.startswith("committed") and "push:" not in out:
            return {"summary": "Saved here, not sent yet.", "level": "ok", "needs": None}
        push = out.split("push:", 1)[1].strip() if "push:" in out else ""
        if push.startswith("ok"):
            return {"summary": "Sent to Overleaf." if not low.startswith("nothing new to save") else "Sent to Overleaf, including changes saved earlier that had not been sent.", "level": "ok", "needs": None, "sent": True}
        if any(k in push.lower() for k in ("rejected", "fetch first", "non-fast-forward", "behind")):
            return {"summary": "Overleaf has newer changes, so it did not accept this yet. Press \"Get latest from Overleaf\", then send again.", "level": "warn", "needs": "latest"}
        return {"summary": "Your changes are saved here but could not be sent: " + (push.splitlines() or [""])[0][:200], "level": "bad", "needs": None}
    return {"summary": first, "level": "ok", "needs": None}


def _conflict_view(name):
    """Where Overleaf's latest and the changes saved here collide, with each side's words and what changed between them. The repository is left as it was."""
    n, p = paper_ref(name)
    c = gitmerge.conflicts(p["path"])
    for f in c["files"]:
        for b in f["blocks"]:
            b["diff"] = latex_mask.word_diff(b["overleaf"], b["mine"])       # left: what Overleaf has; right: what is saved here
    return c


def api_catchup(r):
    """'Overleaf has newer changes': save what is waiting here, bring Overleaf's latest in underneath it, then send, in one go. Nothing is overwritten on either side;
    when the same lines were changed in both places it stops before sending and shows each colliding place so the author can choose."""
    name, msg = r.body.get("name", ""), r.body.get("message")
    saved = git_action(name, "sync", msg, push=False)           # first a saved change, so joining the two sides can never lose it
    if saved.get("hint") or saved.get("level") == "bad":
        return {"pull": saved, "push": None}
    pull = git_action(name, "pull", join=True)
    if pull.get("hint") or pull.get("level") != "ok":            # anything but a clean join stops here, before anything is sent
        out = {"pull": pull, "push": None}
        if "could not join" in (pull.get("output") or "").lower():
            try:
                out["conflict"] = _conflict_view(name)
            except gitmerge.MergeProblem as e:
                out["conflict_error"] = str(e)
        return out
    push = git_action(name, "sync", msg)
    if push.get("sent"):
        push["pane"] = {"reloading": refresh_overleaf(r.app, name)}
    return {"pull": pull, "push": push}


def api_resolve(r):
    """The author's choice for every colliding place -> the join is made, committed and sent."""
    name, msg = r.body.get("name", ""), r.body.get("message")
    raw = r.body.get("choices")
    if not isinstance(raw, dict) or len(raw) > 500:
        raise ApiError(400, "choices must be an object of place -> mine | overleaf | both")
    choices = {str(k)[:400]: str(v) for k, v in raw.items() if str(v) in ("mine", "overleaf", "both")}
    n, p = paper_ref(name)
    saved = git_action(name, "sync", msg, push=False)
    if saved.get("hint") or saved.get("level") == "bad":
        raise ApiError(409, saved.get("friendly") or saved.get("summary") or "I could not save what is waiting here first.")
    try:
        joined = gitmerge.resolve(p["path"], choices, "Join with Overleaf's latest (Writing assistant)")
    except gitmerge.MergeProblem as e:
        raise ApiError(409, str(e)) from None
    push = git_action(name, "sync", msg)
    if push.get("sent"):
        push["pane"] = {"reloading": refresh_overleaf(r.app, name)}
    return {"joined": joined, "push": push}


def api_conflict_handoff(r):
    """The author asks Claude Code to join the two sides. Claude Code finds the note the next time it checks the paper; this returns the sentence to say to it."""
    name = r.body.get("name", "")
    n, p = paper_ref(name)
    info = {"files": [], "places": []}
    try:
        c = gitmerge.conflicts(p["path"])
        info["files"] = [f["file"] for f in c["files"]] + [w["file"] for w in c["whole_files"]]
        info["places"] = [{"file": f["file"], "mine": b["mine"][:300], "overleaf": b["overleaf"][:300]} for f in c["files"] for b in f["blocks"]]
    except gitmerge.MergeProblem:
        pass                                                      # the note is still useful without the places
    say = gitmerge.request(p["path"], info)
    return {"ok": True, "say": say, "files": info["files"]}


def api_git(r, action):
    (name,) = need(r.body, "name")
    res = git_action(name, action, r.body.get("message"))
    if res.get("sent"):
        res["pane"] = {"reloading": refresh_overleaf(r.app, name)}     # the open Overleaf page would otherwise keep showing the old text
    return res


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


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def api_book_upload(r):
    """The writing book's PDF, chosen in Settings: checked, given a safe name and put in the inbox, where Prepare the book reads it. (The body was already written to a temporary file.)"""
    tmp = Path(str(r.body.get("_tmp", "")))
    try:
        inbox = config.inbox_dir()
        if tmp.parent != inbox or not tmp.name.startswith(".upload-") or not tmp.is_file():
            raise ApiError(400, "no file arrived")
        with open(tmp, "rb") as fh:
            if b"%PDF-" not in fh.read(1024):
                raise ApiError(400, "That does not look like a PDF. Choose the book as a PDF file with selectable text.")
        raw = os.path.basename(str(r.arg("name", "") or "").replace("\\", "/"))
        stem = re.sub(r"\s+", " ", re.sub(r"[^\w.,()+\- ]", "_", raw.rsplit(".", 1)[0], flags=re.U)).strip(" .")[:100] or "book"
        digest = _sha_file(tmp)
        name, k = f"{stem}.pdf", 2
        while (inbox / name).exists():
            if _sha_file(inbox / name) == digest:
                tmp.unlink(missing_ok=True)                                  # the same file again: nothing to do
                return {"name": name, "size": r.body["_size"], "duplicate": True, "inbox": sorted(p.name for p in inbox.glob("*.pdf"))}
            name, k = f"{stem} ({k}).pdf", k + 1
        os.replace(tmp, inbox / name)
        os.chmod(inbox / name, 0o600)
        return {"name": name, "size": r.body["_size"], "duplicate": False, "inbox": sorted(p.name for p in inbox.glob("*.pdf"))}
    finally:
        if tmp.name.startswith(".upload-"):
            tmp.unlink(missing_ok=True)


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


BOOK_CHECK_PARTS = ("title", "abstract", "introduction", "background", "methods", "results", "discussion", "conclusion")


def api_book_check(r):
    """For each part of a paper, the book pages that would be used. Nothing is logged: this is a look, not a lookup on behalf of a paper."""
    b = book_id(r)
    rows = []
    for part in BOOK_CHECK_PARTS:
        res = retrieve.book_query(b, f"how to write the {taxonomy.label(part)}", part=part, budget=2000, max_chunks=4, style_cap=0, log=False)
        rows.append({"part": part, "label": taxonomy.label(part), "fallback": res["fallback"],
                     "chunks": [{"label": c["label"], "pages": c["pages"], "tokens": c["tokens"], "has_card": bool(c["rule_card"])} for c in res["chunks"]]})
    return {"book": b, "rows": rows, "logic_version": (book_index.manifest(b) or {}).get("logic_version") or 1, "current_version": chunk_book.LOGIC_VERSION}


def api_rules_status(r):
    b = book_id(r)
    return {**rule_cards.status(b), "model": config.openai_status()}


def api_rules_preview(r):
    b = book_id(r)
    st = rule_cards.status(b)
    if not st["todo"]:
        return {**st, "calls": 0}
    pl = rule_cards.plan(b)
    pl.pop("sample", None)
    return {**st, **pl}


def api_rules_run(r):
    b = book_id(r)
    confirm = r.body.get("confirm")

    def job(progress):
        res = rule_cards.run(b, confirm, progress=progress, source="web")
        return {**{k: res[k] for k in ("status", "error", "with_cards", "chunks", "todo") if k in res}, "cost": res.get("cost")}
    return {"job": r.app.jobs.submit(f"rule cards for {b}", job, with_progress=True)}


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
    """The suggestion as the page needs it: no placeholder maps or segment lists, but the texts the tone panel works with."""
    q = {k: v for k, v in p.items() if k != "units"}
    q["rule_labels"] = {}
    for rid in p.get("rules", [])[:12]:  # "ch05 Style, p.141" instead of an id, so the page can say where the advice comes from
        try:
            q["rule_labels"][rid] = book_index.get_chunk(rid.split(":")[1], rid)["label"]
        except Exception:  # noqa: BLE001
            pass
    q["units"] = []
    for u in p["units"]:
        v = {k: x for k, x in u.items() if k not in ("mapping", "masked_original", "segments", "model_version")}
        if u["status"] != "failed":
            v["views"] = revise.unit_views(u)
        q["units"].append(v)
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


def api_proposal_text(r):
    """The suggestion with the ticked changes only, ready to copy (real LaTeX, and the tagged version for QuillBot). Writes nothing."""
    n, p = paper_ref(r.body.get("paper", ""))
    try:
        units = revise.selected_views(p, _pid(r), r.body.get("accept", "all"))
    except revise.ReviseError as e:
        raise ApiError(409, str(e)) from None
    return {"units": units, "latex": "\n\n".join(u["latex"] for u in units), "masked": "\n\n".join(u["masked"] for u in units)}


def api_record(r):
    n, p = paper_ref(r.body.get("paper", ""))
    res = _record(n, p, r)
    cid = r.body.get("chat")
    if cid:
        try:
            chat.close_reviews(p, str(cid))             # setting aside the last open comment of a review finishes it
        except ValueError:
            pass
    return res


def _record(n, p, r):
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


def _secret(value, what, min_len=8):
    v = value.strip() if isinstance(value, str) else ""
    if len(v) < min_len or any(c.isspace() for c in v) or len(v) > 500:
        raise ApiError(400, f"{what} looks wrong: it should be one string without spaces")
    return v


def api_secret_openai(r):
    """Create or update openai.json from the Settings form. The key is written (0600) and never sent back to any page."""
    key = r.body.get("api_key")
    cur = common.load("openai.json", {}) or {}
    model = str(r.body.get("model") or cur.get("model") or "").strip()
    if not model or len(model) > 100 or any(c.isspace() for c in model):
        raise ApiError(400, "the model id is required (for example the name shown in your OpenAI account), without spaces")
    if key in (None, ""):
        if not cur.get("api_key"):
            raise ApiError(400, "paste your OpenAI API key")
        key = cur["api_key"]  # only the model changed
    else:
        key = _secret(key, "the API key")
    out = {**cur, "api_key": key, "model": model}
    common.save("openai.json", out)
    return {"ok": True}


def api_secret_git(r):
    common.save("overleaf_git.json", {"token": _secret(r.body.get("token"), "the token")})
    return {"ok": True}


_PROBES = {}   # url -> (when, result): the sites' headers hardly ever change, and asking costs a few seconds


def _probe_many(urls):
    out, threads = {}, []

    def one(u):
        hit = _PROBES.get(u)
        if hit and time.time() - hit[0] < 600:
            out[u] = hit[1]
            return
        try:
            res = panes.probe(u)
        except ValueError as e:
            res = {"url": u, "error": str(e)}
        _PROBES[u] = (time.time(), res)
        out[u] = res
    for u in urls:
        t = threading.Thread(target=one, args=(u,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=12)
    return out


def _pane_addresses(n, p):
    ui = config.ui_cfg()
    return ui, panes.quillbot_url(ui), panes.overleaf_url(p, config.paper_settings(p).get("overleaf_url"))


PANE_MODES = ("auto", "browser", "window", "inside")
SITE_TITLES = {"quillbot": "QuillBot", "overleaf": "Overleaf"}


def api_panes(r):
    """What the two side panes show and how: in the built-in browser (when it is available), inside the app as a frame (when the site allows
    it) or in a window beside the app. ?check=1 asks the sites about framing."""
    n, p = paper_ref(r.arg("paper", ""))
    ui, q, o = _pane_addresses(n, p)
    mode = ui.get("pane_mode") if ui.get("pane_mode") in PANE_MODES else "auto"
    bst = r.app.browser().status()
    have = bool(bst.get("available"))
    wanted = {k: bool(ui.get("builtin_" + k, True)) for k in ("quillbot", "overleaf")}     # the author can send one site to a window of their own browser
    use_browser = have and mode in ("auto", "browser")
    urls = [u for u in (q, o) if u]
    probes = _probe_many(urls) if r.arg("check") and mode == "auto" and not (use_browser and all(wanted.values())) else {}
    out = {"paper": n, "mode": mode, "browser": {"available": have, "reason": bst.get("reason", "")}}
    for key, url in (("quillbot", q), ("overleaf", o)):
        if not url:
            out[key] = {"url": None, "inside": False, "how": "none", "reason": "There is no address for this paper's Overleaf project yet. Add it in Settings."}
            continue
        if use_browser and not wanted[key]:
            out[key] = {"url": url, "inside": False, "how": "window", "reason": "You chose to open " + SITE_TITLES[key] + " in a window of your own browser (Settings)."}
            continue
        if use_browser:
            out[key] = {"url": url, "inside": False, "how": "browser", "reason": ""}
            continue
        if mode == "browser":   # asked for, but not available: a window is the next best thing
            out[key] = {"url": url, "inside": False, "how": "window", "reason": (bst.get("reason") or "The built-in browser is not available.") + " It opens in a window beside the app instead."}
            continue
        fmode = "window" if mode == "window" else "inside" if mode == "inside" else "auto"
        inside, reason = panes.decide_inside(fmode, probes.get(url), url)
        if fmode == "auto" and not r.arg("check"):
            inside, reason = False, ""   # not asked yet
        out[key] = {"url": url, "inside": inside, "how": "frame" if inside else "window", "reason": reason}
    return out


def api_test_frames(r):
    """Ask the two sites, from here, whether they allow being shown inside another page. Headers only; nothing about the paper is sent."""
    n, p = paper_ref(r.body.get("paper", ""))
    ui, q, o = _pane_addresses(n, p)
    out = {"quillbot": panes.probe(q)}
    out["overleaf"] = panes.probe(o) if o else {"url": None, "error": "this paper's Overleaf address has no project id"}
    for key, url in (("quillbot", q), ("overleaf", o)):
        if url:
            _PROBES[url] = (time.time(), out[key])
    out["mode"] = ui.get("pane_mode") or "auto"
    return out


# ---------------------------------------------------------------------------- the built-in browser (QuillBot and Overleaf inside the app)

def _logins():
    """The saved sign-in details of the two sites, for showing: the email, and whether a password is saved. Never the password itself."""
    raw = common.load("sites.json", {}) or {}
    return {k: {"email": (raw.get(k) or {}).get("email", ""), "password": bool((raw.get(k) or {}).get("password"))} for k in webview.SITES}


def api_secret_site(r):
    """Save (or remove) the email and password used to fill the sign-in form of QuillBot / Overleaf in the built-in browser.
    Written to sites.json (0600) on this computer; never sent back to any page and never logged."""
    site = r.body.get("site")
    if site not in webview.SITES:
        raise ApiError(400, "unknown site")
    everything = common.load("sites.json", {}) or {}
    if r.body.get("clear"):
        everything.pop(site, None)
    else:
        cur = everything.get(site) or {}
        email = str(r.body.get("email") or cur.get("email") or "").strip()
        if not email or len(email) > 200 or any(c.isspace() for c in email):
            raise ApiError(400, "enter the email address you sign in with")
        pw = r.body.get("password")
        if pw in (None, ""):
            if not cur.get("password"):
                raise ApiError(400, "enter your password")
            pw = cur["password"]
        elif not isinstance(pw, str) or len(pw) > 500 or "\n" in pw or "\r" in pw:
            raise ApiError(400, "the password looks wrong: it should be one line of text")
        everything[site] = {"email": email, "password": pw}
    common.save("sites.json", everything)
    return {"ok": True, "saved": _logins()}


def _site_url(site, paper):
    ui = config.ui_cfg()
    if site == "quillbot":
        return panes.quillbot_url(ui)
    n, p = paper_ref(paper or "")
    url = panes.overleaf_url(p, config.paper_settings(p).get("overleaf_url"))
    if not url:
        raise ApiError(409, "There is no address for this paper's Overleaf project yet. Add it in Settings.")
    return url


def api_browser_status(r):
    return {**r.app.browser().status(), "saved": _logins()}


def api_browser_open(r):
    site = r.m.group(1)
    return r.app.browser().open(site, _site_url(site, r.body.get("paper")), r.body.get("w"), r.body.get("h"), bool(r.body.get("force")))


def api_browser_resize(r):
    return r.app.browser().resize(r.m.group(1), r.body.get("w"), r.body.get("h"))


def api_browser_input(r):
    return r.app.browser().input(r.m.group(1), r.body.get("events"))


def api_browser_nav(r):
    site, action = r.m.group(1), r.body.get("action")
    if action == "home":
        return r.app.browser().open(site, _site_url(site, r.body.get("paper")), None, None, True)
    return r.app.browser().nav(site, action)


def api_browser_selection(r):
    return r.app.browser().selection(r.m.group(1))


def api_browser_clipboard(r):
    """What the page last copied with its own Copy button (QuillBot's, for example)."""
    return r.app.browser().clipboard(r.m.group(1))


def api_browser_insert(r):
    text = r.body.get("text")
    if not isinstance(text, str) or not text:
        raise ApiError(400, "nothing to paste")
    return r.app.browser().insert(r.m.group(1), text[:webview.MAX_TEXT])


def api_browser_close(r):
    return r.app.browser().close(r.m.group(1))


def api_browser_signin(r):
    """Fill the sign-in form that is on screen with the saved email and password. The author presses the site's own button."""
    site = r.m.group(1)
    cred = (common.load("sites.json", {}) or {}).get(site) or {}
    if not cred.get("email") and not cred.get("password"):
        raise ApiError(409, f"No sign-in details are saved for {SITE_TITLES[site]} yet. Add them in Settings.")
    res = r.app.browser().signin(site, cred.get("email", ""), cred.get("password", ""))
    filled, failed = res.get("filled", []), res.get("failed", [])
    what = lambda xs: " and ".join(x for x in xs)   # noqa: E731
    if failed:
        msg = (f"I could not put your {what(failed)} into the page: it does not keep typed text. Click in the box and type it yourself."
               + (f" (Your {what(filled)} was filled in.)" if filled else ""))
    elif "email" in filled and "password" in filled:
        msg = "Filled in your email and password. Press the site's own Sign in button."
    elif "email" in filled:
        msg = "Filled in your email. Press the site's Continue button, then press this again for the password."
    elif "password" in filled:
        msg = "Filled in your password. Press the site's own Sign in button."
    else:
        msg = "I could not find a sign-in form on this page. Open the site's sign-in page first, then press this again."
    return {"filled": filled, "failed": failed, "message": msg}


BROWSER_GET = re.compile(r"^/api/browser/(quillbot|overleaf)/frame$")
LATEX_GET = re.compile(r"^/latex/(page|pdf)$")
QUIET = re.compile(r"^/(api/browser/(quillbot|overleaf)/(frame|input)|api/jobs/[0-9a-f]+|latex/page|healthz)$")


# ---------------------------------------------------------------------------- chat

PLAIN_OPENAI = {"auth": "OpenAI did not accept your API key. Check it in Settings.", "not_found": "OpenAI does not know the model name in your Settings. Check the model there.",
                "rate": "OpenAI says you are sending too fast or have reached a limit. Wait a minute and ask again.",
                "quota": "OpenAI says your account has no credit or has reached its spending limit.", "network": "I could not reach OpenAI. Check your internet connection and ask again.",
                "server": "OpenAI had a problem on its side. Ask again in a moment.", "refusal": "OpenAI declined to edit that text.",
                "truncated": "OpenAI's answer was cut off. Try a smaller part of the text."}


def plain_error(e):
    if isinstance(e, openai_client.OpenAIError):
        return PLAIN_OPENAI.get(e.kind, f"OpenAI reported a problem: {e}")
    if isinstance(e, config.ConfigError):
        return "ChatGPT is not set up yet. Open Settings and add your OpenAI key and model."
    return friendly(e) or "Something went wrong on my side (details are in the container log)."


def _chat_ctx(r):
    paper = (r.body or {}).get("paper") or r.arg("paper", "")
    n, p = paper_ref(paper)
    try:
        cid = chat.ensure_thread(p, (r.body or {}).get("id") or r.arg("id"))
    except ValueError:
        raise ApiError(400, "bad chat id") from None
    return n, p, cid


def api_chat_threads(r):
    n, p = paper_ref(r.arg("paper", ""))
    chat.ensure_thread(p)
    return {"threads": chat.list_threads(p)}


def api_chat_new(r):
    n, p = paper_ref((r.body or {}).get("paper", ""))
    return {"thread": chat.new_thread(p)}


def api_chat_rename(r):
    n, p = paper_ref(r.body.get("paper", ""))
    try:
        chat.rename(p, r.body.get("id", ""), str(r.body.get("title", ""))[:80])
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {"ok": True}


def _not_while_running(r, n, cid):
    if r.app.jobs.running(paper=n, chat=cid):
        raise ApiError(409, "A request in this chat is still running. Wait for it to finish first.")


def api_chat_clear(r):
    """Start the conversation over in the same place. Suggestions already saved in the files, and what the app learned from earlier decisions, are not touched."""
    n, p = paper_ref(r.body.get("paper", ""))
    cid = str(r.body.get("id", ""))
    _not_while_running(r, n, cid)
    try:
        chat.clear(p, cid)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {"ok": True}


def api_chat_delete(r):
    n, p = paper_ref(r.body.get("paper", ""))
    cid = str(r.body.get("id", ""))
    _not_while_running(r, n, cid)
    try:
        chat.delete(p, cid)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {"ok": True}


def api_chat_get(r):
    n, p, cid = _chat_ctx(r)
    ostate, omsg = config.openai_status()
    thread = chat.get_thread(p, cid)
    running = r.app.jobs.running(paper=n, chat=cid)
    return {"id": cid, "thread": thread, "issues": chat.issue_states(p, thread, running=bool(running)), "chips": [{"label": a, "text": b} for a, b in chat.CHIPS],
            "running": [{"job": j["id"], "label": j["label"]} for j in running],
            "openai": ostate, "can_apply": bool(config.ui_cfg()["watermark_url"]), "settings": config.paper_settings(p)}


def api_chat_cancel(r):
    n, p, cid = _chat_ctx(r)
    (mid,) = need(r.body, "message")
    m = chat.get_message(p, cid, str(mid))
    if not m or m["kind"] not in ("plan", "review_plan", "draft_plan", "layout_plan") or m["data"].get("state") != "waiting":
        raise ApiError(409, "that request is not waiting")
    chat.update(p, cid, m["id"], state="cancelled")
    return {"ok": True}


def api_chat_count(r):
    n, p = paper_ref(r.body.get("paper", ""))
    ids = [str(i) for i in (r.body.get("chunk_ids") or [])][:200]
    return chat.count(n, p, ids)


def _start_pass(app, n, p, cid, plan_mid):
    """Send a prepared request to OpenAI in the background; the answer lands in the chat when it is done."""
    msg = chat.get_message(p, cid, plan_mid)
    if not msg or msg["kind"] != "plan":
        raise ApiError(404, "that request is not in this chat")
    if msg["data"].get("state") == "sent":
        raise ApiError(409, "that request was already sent")
    plan = msg["data"]["plan"]
    chat.update(p, cid, plan_mid, state="sent")

    def work(progress, set_steps):
        try:
            again = chat.plan(n, p, plan["instruction"], plan["chunk_ids"], book_id=plan.get("book"), facts=plan.get("facts", ""), online=plan.get("online") or None)
            if again["payload_hash"] != plan["payload_hash"]:
                raise ValueError("Your paper changed after I prepared this request (for example after getting the latest from Overleaf). Please ask again.")
            res = revise.run(n, p, plan["instruction"], chunk_ids=plan["chunk_ids"], book_id=plan.get("book"), dry_run=False,
                             confirm=plan["payload_hash"], source="web", progress=set_steps, facts=plan.get("facts", ""), online=plan.get("online") or None)
            if res["status"] == "preview":
                raise ValueError("Your paper changed after I prepared this request. Please ask again.")
        except Exception as e:  # noqa: BLE001
            chat.update(p, cid, plan_mid, state="failed")
            chat.append(p, cid, "assistant", "error", plain_error(e))
            raise
        prop = res["proposal"]
        api_kind = next((u.get("error_kind") for u in prop["units"] if u.get("error_kind") in PLAIN_OPENAI), None)
        if prop["status"] == "failed" and api_kind:  # the key, the account or the connection is the problem, not the text
            chat.update(p, cid, plan_mid, state="failed")
            chat.append(p, cid, "assistant", "error", PLAIN_OPENAI[api_kind])
            return {"proposal": prop["id"], "failed": api_kind}
        old = msg["data"].get("revises")
        replaced = bool(old) and prop["status"] == "proposed" and revise.supersede(p, old, prop["id"])      # the suggestion this one revises can no longer be saved by mistake
        chat.append(p, cid, "assistant", "result", "", proposal=prop["id"], chunk_ids=plan["chunk_ids"], plan=plan_mid, status=prop["status"],
                    usage=prop["usage"], steps=prop.get("steps", []), **({"issue": msg["data"]["issue"]} if msg["data"].get("issue") else {}),
                    **({"revises": old, "replaced": replaced} if old else {}))
        chat.close_reviews(p, cid)                      # a comment OpenAI would change nothing for can be the last one open
        return {"proposal": prop["id"]}
    return app.jobs.submit(f"Improving {n}", work, with_steps=True, meta={"paper": n, "chat": cid})


def _start_layout(app, n, p, cid, plan_mid):
    """Ask for the layout change in the background; the suggestion lands in the chat when it is ready."""
    msg = chat.get_message(p, cid, plan_mid)
    if not msg or msg["kind"] != "layout_plan":
        raise ApiError(404, "that request is not in this chat")
    if msg["data"].get("state") == "sent":
        raise ApiError(409, "that request was already sent")
    plan = msg["data"]["plan"]
    chat.update(p, cid, plan_mid, state="sent")

    def work(progress, set_steps):
        try:
            res = layout.resolve(n, p, plan["instruction"], msg["data"].get("chunk_ids") or None)
            out = layout.run(n, p, plan["instruction"], res, confirm=plan["payload_hash"], source="web", progress=set_steps)
            if out["status"] == "preview":
                raise ValueError("Your paper changed after I prepared this request. Please ask again.")
        except Exception as e:  # noqa: BLE001
            chat.update(p, cid, plan_mid, state="failed")
            chat.append(p, cid, "assistant", "error", plain_error(e))
            raise
        prop = out["proposal"]
        if prop["status"] == "failed" and prop.get("error_kind") in PLAIN_OPENAI:
            chat.update(p, cid, plan_mid, state="failed")
            chat.append(p, cid, "assistant", "error", PLAIN_OPENAI[prop["error_kind"]])
            return {"proposal": prop["id"], "failed": prop["error_kind"]}
        chat.append(p, cid, "assistant", "result", "", proposal=prop["id"], chunk_ids=[], plan=plan_mid, status=prop["status"], usage=prop["usage"], steps=prop.get("steps", []), layout=True)
        return {"proposal": prop["id"]}
    return app.jobs.submit(f"Changing the layout of {n}", work, with_steps=True, meta={"paper": n, "chat": cid})


def _start_draft(app, n, p, cid, plan_mid):
    """Write new text in the background; the suggestion lands in the chat when it is ready."""
    msg = chat.get_message(p, cid, plan_mid)
    if not msg or msg["kind"] != "draft_plan":
        raise ApiError(404, "that request is not in this chat")
    if msg["data"].get("state") == "sent":
        raise ApiError(409, "that request was already sent")
    plan = msg["data"]["plan"]
    chat.update(p, cid, plan_mid, state="sent")

    def work(progress, set_steps):
        try:
            res = draft.run(n, p, plan["instruction"], plan["anchor"], plan["part"], facts=plan.get("facts", ""), confirm=plan["payload_hash"], source="web", progress=set_steps, book_id=plan.get("book"),
                            online=plan.get("online") or None)
            if res["status"] == "preview":
                raise ValueError("Your paper or your files changed after I prepared this request. Please ask again.")
        except Exception as e:  # noqa: BLE001
            chat.update(p, cid, plan_mid, state="failed")
            chat.append(p, cid, "assistant", "error", plain_error(e))
            raise
        prop = res["proposal"]
        api_kind = next((u.get("error_kind") for u in prop["units"] if u.get("error_kind") in PLAIN_OPENAI), None)
        if prop["status"] == "failed" and api_kind:
            chat.update(p, cid, plan_mid, state="failed")
            chat.append(p, cid, "assistant", "error", PLAIN_OPENAI[api_kind])
            return {"proposal": prop["id"], "failed": api_kind}
        old = msg["data"].get("revises")
        replaced = bool(old) and prop["status"] == "proposed" and revise.supersede(p, old, prop["id"])
        chat.append(p, cid, "assistant", "result", "", proposal=prop["id"], chunk_ids=[], plan=plan_mid, status=prop["status"], usage=prop["usage"], steps=prop.get("steps", []), draft=True,
                    **({"revises": old, "replaced": replaced} if old else {}))
        return {"proposal": prop["id"]}
    return app.jobs.submit(f"Writing new text for {n}", work, with_steps=True, meta={"paper": n, "chat": cid})


HELP_TEXT = ("Tell me what to do with your paper, in your own words. For example:\n"
             "- improve the flow of the text before Figure 1 in the methods\n"
             "- fix the grammar in the introduction\n"
             "- make the abstract shorter\n"
             "- write the results from my analysis (or the discussion, or a paragraph on the sampling from my notes)\n"
             "- write a new introduction paragraph after the selected text, using my literature library and citing sources\n"
             "- what do my articles say about irrigation?\n"
             "- find references about how armed conflict affects energy infrastructure (I search the internet, you choose which to add)\n"
             "- move this table to the appendix and add the reference (or improve a table or a graph, adjust where a figure sits)\n"
             "- peer review my paper\n"
             "- or paste a DOI to add the reference.\n"
             "I read each request as a whole and, when I am not sure what you mean, I say what I understood and wait for your yes, your correction or your no. "
             "You can also choose the kind of request under the message box.\n"
             "I read the right part of your paper, add the advice from your writing book, and ask OpenAI for a better version with an explanation. "
             "You decide what to keep; nothing is saved until you approve it.")


def _issue_ref(p, cid, ref):
    """'Fix this' on comment `index` of a peer review: remember which comment the request is for, so its progress can be followed. -> {review, index} or None"""
    if not isinstance(ref, dict):
        return None
    review_mid, i = str(ref.get("review", "")), ref.get("index")
    m = chat.get_message(p, cid, review_mid)
    if not m or m["kind"] != "review" or not isinstance(i, int) or not 0 <= i < len(m["data"].get("issues") or []):
        return None
    skipped = [x for x in (m["data"].get("skipped") or []) if x != i]
    if skipped != (m["data"].get("skipped") or []):
        chat.update(p, cid, review_mid, skipped=skipped)                     # trying a comment again takes it off the skipped list
    return {"review": review_mid, "index": i}


def api_chat_issue(r):
    """Skip a comment of a peer review (it needs no change), or bring it back."""
    n, p, cid = _chat_ctx(r)
    (mid,) = need(r.body, "message")
    action = r.body.get("action")
    i = r.body.get("index")
    m = chat.get_message(p, cid, str(mid))
    if not m or m["kind"] != "review" or action not in ("skip", "reopen") or not isinstance(i, int) or not 0 <= i < len(m["data"].get("issues") or []):
        raise ApiError(400, "that comment is not in this chat")
    skipped = [x for x in (m["data"].get("skipped") or []) if x != i]
    if action == "skip":
        skipped.append(i)
    chat.update(p, cid, m["id"], skipped=sorted(skipped), **({"closed": False} if action == "reopen" else {}))
    closed = chat.close_reviews(p, cid) if action == "skip" else []
    return {"ok": True, "closed": bool(closed)}


def api_chat_send(r):
    n, p, cid = _chat_ctx(r)
    raw = str(r.body.get("text", "")).strip()
    text, cut = raw[:chat.MAX_TEXT], len(raw) > chat.MAX_TEXT
    ticked = [str(i) for i in (r.body.get("chunk_ids") or [])][:200] or None
    if not text and not ticked:
        raise ApiError(400, "say what you want")
    text = text or "Improve this."
    issue = _issue_ref(p, cid, r.body.get("issue"))
    redraft = str(r.body.get("redraft") or "")
    facts = str(r.body.get("facts") or "").strip()[:3000] if (ticked or redraft) else ""          # the author's answers to the model's questions; only for a revision of a chosen place
    revises = str(r.body.get("revises") or "")
    find_refs = bool(r.body.get("find_refs"))
    words = str(r.body.get("words") or "").strip()[:200]
    online = [d for d in (r.body.get("online") or []) if isinstance(d, str)][:6]
    if not online and not find_refs and chat.mentions_selected_references(text) and chat.intent(text) in ("edit", "draft"):
        online = _recent_picks(p, cid)                                  # "...based on the ones selected": the references picked earlier in this chat
    online = [c["doi"] for c in scholar.recall(p, "", force=online)] if online else []              # only references that were added to this paper's bibliography
    if not ((facts or online or find_refs) and PID_RE.match(revises)):
        revises = ""
    if not ((facts or online or find_refs) and PID_RE.match(redraft)):
        redraft = ""
    out = [chat.append(p, cid, "user", "text", text, **({"chunk_ids": ticked} if ticked else {}), **({"issue": issue} if issue else {}), **({"facts": facts} if facts else {}))]
    if cut:                                                  # never silently: a long outline pasted into the message is read up to a limit
        out.append(chat.append(p, cid, "assistant", "text", f"Your message is longer than {chat.MAX_TEXT:,} characters, so I read only the first {chat.MAX_TEXT:,}. "
                                                              "For a longer text, put it in a file (Add a file) or in your literature library, and I can draw on all of it."))
    # A selection normally means "revise these lines", but it cannot erase an
    # explicit request to write new prose.  Drafting then inserts after the
    # selected passage rather than replacing it.
    asked = chat.strip_pasted(n, p, text) if not ticked else text      # what is asked, without the paragraphs of the paper that the message carries
    inferred = chat.intent(asked)
    kind = "refsearch" if find_refs else "draft" if redraft else (inferred if inferred in ("draft", "refsearch", "layout") else "edit") if ticked else inferred
    req = {"text": text, "asked": asked, "ticked": ticked, "facts": facts, "online": online, "issue": issue, "redraft": redraft, "revises": revises, "find_refs": find_refs, "words": words}
    uses = None
    flag = str(r.body.get("flag") or "")
    if flag in intent.ALL:                                                # the author said what kind of request this is: no reading needed
        kind = flag
    elif not (find_refs or redraft or revises or online or words) and kind not in ("refs", "help"):
        card, reading, usage = _understand_gate(n, p, cid, req, kind, out[0])
        if card:                                                          # not clearly right: say what was understood and wait for the author's yes, correction or no
            out.append(card)
            return {"messages": out, "job": None}
        if reading:
            kind, uses = reading["intent"], reading["uses"]
            chat.update(p, cid, out[0]["id"], understood={"intent": reading["intent"], "summary": reading["summary"], "target": reading["target"], "confidence": reading["confidence"]})
            out[0] = chat.get_message(p, cid, out[0]["id"])
    return _dispatch(r.app, n, p, cid, kind, req, out, out[0]["id"], uses)


UNDERSTAND_MODES = ("always", "unsure", "never")


def _capabilities(n, p):
    """What the reader may be told is available, a line each (it decides which sources a request can use)."""
    caps = {}
    try:
        st = library.status()
        caps["the author's literature library"] = f"{st['docs']} documents" if st.get("docs") else "empty or not chosen"
    except Exception:  # noqa: BLE001
        pass
    try:
        outlines = [f["file"].rsplit("/", 1)[-1] for f in files.listing(n, p) if files.role_of(f) == "outline"]
        caps["outlines the author wrote"] = ", ".join(outlines[:6]) or "none"
    except Exception:  # noqa: BLE001
        pass
    caps["searching the internet for references"] = "switched off" if config.paper_settings(p).get("use_online") == "never" else "allowed"
    return caps


def _understand_gate(n, p, cid, req, rules_kind, user_msg):
    """Read the request as a whole and decide whether to show the reading first. -> (card message or None, reading or None, usage). With no model, or with reading switched off,
    (None, None, None): the word-based guess is used, as before."""
    mode = config.paper_settings(p).get("understand", "unsure")
    if mode not in UNDERSTAND_MODES or mode == "never" or config.openai_status()[0] != "ok":
        return None, None, None
    reading, usage = _read_request(n, p, cid, req, rules_kind, user_msg["id"])
    if not reading:
        return None, None, None
    if intent.needs_card(mode, reading, rules_kind):
        card = chat.append(p, cid, "assistant", "understanding", reading["summary"], reading=reading, rules_kind=rules_kind, source=user_msg["id"], state="waiting", usage=usage or {})
        return card, reading, usage
    return None, reading, usage


def _read_request(n, p, cid, req, rules_kind, user_mid, correction=""):
    try:
        tex = [c for c in paper_index.list_chunks(n, p)["chunks"] if c["kind"] == "tex"]
        sections = [s["label"] for s in chat._sections(tex)]
    except Exception:  # noqa: BLE001
        sections = []
    text = req["text"] + (f"\n\n[The author's correction of an earlier reading: {correction}]" if correction else "")
    material = intent.build(text, asked=req.get("asked"), selected=req.get("ticked"), sections=sections, capabilities=_capabilities(n, p),
                            thread=chat.get_thread(p, cid)["messages"], current_id=user_mid, guess=rules_kind, examples=intent.examples(p, req["text"]))
    return intent.interpret(config.openai_cfg(), material)


def _request_of(n, p, cid, user_mid):
    """The parameters of a request already in the chat, rebuilt from its message."""
    m = chat.get_message(p, cid, user_mid)
    if not m or m["role"] != "user":
        raise ApiError(404, "that request is not in this chat")
    d = m.get("data") or {}
    ticked = d.get("chunk_ids") or None
    return {"text": m["text"], "asked": chat.strip_pasted(n, p, m["text"]) if not ticked else m["text"], "ticked": ticked, "facts": d.get("facts", ""), "online": [], "issue": d.get("issue"),
            "redraft": "", "revises": "", "find_refs": False, "words": ""}


def api_chat_understood(r):
    """The author's answer to "is this what you meant?": go ahead (perhaps as another kind of request, with other sources, or with a note), read it again with a correction, or set it aside."""
    n, p, cid = _chat_ctx(r)
    mid = str(r.body.get("message") or "")
    action = str(r.body.get("action") or "")
    card = chat.get_message(p, cid, mid)
    if action == "reconsider":                                               # after the fact: "that is not what I meant" under a request that went ahead
        req = _request_of(n, p, cid, mid)
        user = chat.get_message(p, cid, mid)
        old = (user.get("data") or {}).get("understood") or {}
        reading = {"intent": old.get("intent") if old.get("intent") in intent.INTENTS else "edit", "confidence": 0.5, "summary": old.get("summary") or "", "target": old.get("target") or "",
                   "uses": {"library": False, "online": False, "outline": False}, "question": "Tell me what you meant, or pick the kind of request.", "alternatives": []}
        card = chat.append(p, cid, "assistant", "understanding", reading["summary"], reading=reading, rules_kind=reading["intent"], source=mid, state="waiting", usage={})
        return {"messages": [card], "job": None}
    if not card or card["kind"] != "understanding":
        raise ApiError(404, "that question is not in this chat")
    data = card["data"]
    if data.get("state") != "waiting":
        raise ApiError(409, "that was already answered")
    req = _request_of(n, p, cid, data["source"])
    reading, guess = data["reading"], data["reading"]["intent"]
    note = str(r.body.get("note") or "").strip()[:600]
    if action == "cancel":
        chat.update(p, cid, mid, state="cancelled", note=note)
        intent.remember(p, req["text"], guess, guess, "rejected", note)
        msg = chat.append(p, cid, "assistant", "text", "Okay, I did nothing. Tell me again what you meant, in your own words, or pick the kind of request next to the message box.")
        return {"messages": [chat.get_message(p, cid, mid), msg], "job": None}
    if action == "change":                                                   # read it again with the author's correction
        if not note:
            raise ApiError(400, "write what to correct")
        new, usage = _read_request(n, p, cid, req, data.get("rules_kind", ""), data["source"], correction=note)
        if not new:
            raise ApiError(409, "I could not read it again just now. Pick the kind of request yourself and press Go ahead.")
        intent.remember(p, req["text"], guess, new["intent"], "corrected" if new["intent"] != guess else "confirmed", note)
        chat.update(p, cid, mid, reading=new, usage=usage or {}, correction=note)
        return {"messages": [chat.get_message(p, cid, mid)], "job": None}
    if action != "go":
        raise ApiError(400, "action must be go, change or cancel")
    chosen = str(r.body.get("intent") or guess)
    if chosen not in intent.INTENTS:
        raise ApiError(400, "that kind of request is not one I know")
    uses = r.body.get("uses") if isinstance(r.body.get("uses"), dict) else reading["uses"]
    uses = {k: bool(uses.get(k)) for k in ("library", "online", "outline")}
    if note:
        req = dict(req, facts=(req["facts"] + "\nThe author adds: " + note).strip()[:3000])
    intent.remember(p, req["text"], guess, chosen, "corrected" if chosen != guess else "confirmed", note)
    chat.update(p, cid, mid, state="confirmed", chosen=chosen, uses=uses, note=note)
    out = [chat.get_message(p, cid, mid)]
    return _dispatch(r.app, n, p, cid, chosen, req, out, data["source"], uses, explicit=True)


def _dispatch(app, n, p, cid, kind, req, out, user_mid, uses=None, explicit=False):
    """Carry out a request of a known kind: help, DOIs, library, references, writing, citing, review, editing. `uses` (from a reading the author confirmed) names the sources to add."""
    text, asked, ticked, facts, online = req["text"], req["asked"], req["ticked"], req["facts"], req["online"]
    issue, redraft, revises, find_refs, words = req["issue"], req["redraft"], req["revises"], req["find_refs"], req["words"]
    uses = uses or {}
    online_off = config.paper_settings(p).get("use_online") == "never" or (explicit and kind != "refsearch" and uses.get("online") is False)         # unticked on the card: no search of the internet for this request
    cite_nudge = False
    if kind == "cite":
        if uses.get("online") and not uses.get("library"):
            kind = "refsearch"                                            # references found on the internet for this text, then the ones the author ticks are cited
        else:
            kind, cite_nudge = "edit", True
    nudges = []
    if uses.get("outline") and not files.GENERIC_OUTLINE.search(text) and kind in ("edit", "draft"):
        nudges.append("Follow the outline.")
    if uses.get("library") and not library.wants(text) and kind in ("edit", "draft"):
        nudges.append("Use my literature library.")
    if cite_nudge and not library.requires_citations(text):
        nudges.append("Add in-text citations (\\cite) from the sources provided where they support the statements.")
    if nudges:
        text, asked = text + "\n\n" + " ".join(nudges), asked + "\n\n" + " ".join(nudges)
    job = None
    if kind == "help":
        out.append(chat.append(p, cid, "assistant", "text", HELP_TEXT))
    elif kind == "refs":
        job = _start_refs(app, n, p, cid, chat.find_dois(text)[:20])
    elif kind == "library":
        out.append(_library_answer(n, p, cid, text))
    elif kind == "refsearch":
        if online_off:
            out.append(chat.append(p, cid, "assistant", "text", "Searching the internet for references is switched off for this paper. Turn it on in Settings (Literature library), then ask again."))
        elif not words and not find_refs and not ticked and not chat.refs_topic(text) and not chat.refs_place(text, ""):
            out.append(chat.append(p, cid, "assistant", "text", "What should I look for? For example: find references on how armed conflict affects energy infrastructure in Colombia."))
        else:
            prior = [m for m in chat.get_thread(p, cid)["messages"] if m["id"] != user_mid]
            job = _start_refsearch(app, n, p, cid, text, ticked, facts, revises or redraft, issue, words, chat.last_target({"messages": prior}))
    elif kind == "draft":
        if config.openai_status()[0] != "ok":
            out.append(chat.append(p, cid, "assistant", "error", "ChatGPT is not set up yet. Open Settings and add your OpenAI key and model, then ask again."))
            return {"messages": out}
        try:
            if redraft:                                                    # the author answered the model's questions: write it again for the same place, with the answers
                old_prop = revise.get_proposal(p, redraft)
                where = old_prop.get("draft") or {}
                loc = {"status": "ready", "anchor": where["anchor"], "part": where.get("part", ""), "where": "the same place as before", "notes": [], "label": taxonomy.label(where.get("part", "")).capitalize()}
            else:
                loc = draft.locate(n, p, asked, chunk_ids=ticked)
            if loc["status"] != "ready":
                tail = (" Sections I can see: " + ", ".join(loc["sections"]) + ".") if loc.get("sections") else ""
                out.append(chat.append(p, cid, "assistant", "text", loc["why"] + tail))
                return {"messages": out}
            pl = draft.plan(n, p, text, loc["anchor"], loc["part"], facts=facts, online=online or None)
        except (draft.DraftError, revise.ReviseError, config.ConfigError, openai_client.OpenAIError, ValueError, FileNotFoundError, KeyError, paper_index.IsolationError) as e:
            out.append(chat.append(p, cid, "assistant", "error", plain_error(e)))
            return {"messages": out}
        if pl.get("require_citations") and not any(x.get("tag") for x in pl.get("library", [])):
            if online_off:
                out.append(chat.append(p, cid, "assistant", "error", "Your request requires in-text citations, but the local library has no citable bibliography records and online reference search is switched off in Settings."))
                return {"messages": out}
            why = "; ".join(f"{u['label']}: {u['why']}" for u in (pl.get("uncitable") or [])[:4])
            out.append(chat.append(p, cid, "assistant", "text", ("I could not cite anything from your literature library" + (f" ({why})" if why else " (nothing in it matches this subject)") +
                                                              ". I am looking for verified online reference records on the subject of your outline, so you can choose which ones to add and cite.")))
            job = _start_refsearch(app, n, p, cid, text, ticked, facts, "", issue, "", chat.last_target({"messages": chat.get_thread(p, cid)["messages"]}), force_draft=True)
            return {"messages": out, "job": job}
        ask = config.paper_settings(p)["confirm_before_send"] or pl.get("require_citations", False)
        m = chat.append(p, cid, "assistant", "draft_plan", "", plan=pl, where=loc["where"], notes=loc["notes"], state="waiting", confirm="citations" if pl.get("require_citations") else "setting" if ask else None,
                        **({"revises": redraft} if redraft else {}))
        out.append(m)
        if not ask:
            job = _start_draft(app, n, p, cid, m["id"])
            out[-1] = chat.get_message(p, cid, m["id"])
    elif kind == "layout":
        if config.openai_status()[0] != "ok":
            out.append(chat.append(p, cid, "assistant", "error", "ChatGPT is not set up yet. Open Settings and add your OpenAI key and model, then ask again."))
            return {"messages": out}
        try:
            res = layout.resolve(n, p, asked, ticked)
            if res["ask"]:
                out.append(chat.append(p, cid, "assistant", "text", res["ask"]))
                return {"messages": out}
            pl = layout.plan(n, p, text, res)
        except (layout.LayoutError, revise.ReviseError, config.ConfigError, ValueError, OSError, KeyError, paper_index.IsolationError) as e:
            out.append(chat.append(p, cid, "assistant", "error", plain_error(e)))
            return {"messages": out}
        ask = config.paper_settings(p)["confirm_before_send"]
        m = chat.append(p, cid, "assistant", "layout_plan", "", plan=pl, state="waiting", confirm="setting" if ask else None, chunk_ids=ticked or [])
        out.append(m)
        if not ask:
            job = _start_layout(app, n, p, cid, m["id"])
            out[-1] = chat.get_message(p, cid, m["id"])
    elif kind == "review":
        if config.openai_status()[0] != "ok":
            out.append(chat.append(p, cid, "assistant", "error", "ChatGPT is not set up yet. Open Settings and add your OpenAI key and model, then ask again."))
            return {"messages": out}
        try:
            ids, scope = chat.review_scope(n, p, text)
            pl = review.plan(n, p, ids, scope)
            out.append(chat.append(p, cid, "assistant", "review_plan", "", plan=pl, state="waiting"))  # a review always waits for a yes
        except (review.ReviewError, config.ConfigError, ValueError) as e:
            out.append(chat.append(p, cid, "assistant", "error", plain_error(e)))
    else:
        if config.openai_status()[0] != "ok":
            out.append(chat.append(p, cid, "assistant", "error", "ChatGPT is not set up yet. Open Settings and add your OpenAI key and model, then ask again."))
            return {"messages": out}
        thread = chat.get_thread(p, cid)
        prior = [m for m in thread["messages"] if m["id"] != user_mid]
        try:
            u = chat.understand(n, p, text, chunk_ids=ticked, last_ids=chat.last_target({"messages": prior}))
        except (ValueError, FileNotFoundError, paper_index.IsolationError) as e:
            out.append(chat.append(p, cid, "assistant", "error", plain_error(e)))
            return {"messages": out}
        if u["status"] == "empty":
            out.append(chat.append(p, cid, "assistant", "error", u["why"]))
        elif u["status"] == "choose":
            out.append(chat.append(p, cid, "assistant", "ask", u["why"], original=text, options=u["options"], preselect=u["preselect"], sections=u["sections"],
                                   in_part=u["in_part"], limit=u["limit"], notes=u["notes"]))
        else:
            try:
                pl = chat.plan(n, p, chat.instruction_for(u.get("request") or text), u["chunk_ids"], facts=facts, online=online or None)
            except (revise.ReviseError, config.ConfigError, openai_client.OpenAIError) as e:
                out.append(chat.append(p, cid, "assistant", "error", plain_error(e)))
                return {"messages": out}
            if pl.get("require_citations") and not any(x.get("tag") for x in pl.get("library", [])):       # asked to cite, and nothing in the library can be cited
                if online_off:
                    out.append(chat.append(p, cid, "assistant", "error", "Your request requires in-text citations, but nothing in your literature library can be cited and searching the internet for references is switched off in Settings."))
                    return {"messages": out}
                why = "; ".join(f"{x['label']}: {x['why']}" for x in (pl.get("uncitable") or [])[:4])
                out.append(chat.append(p, cid, "assistant", "text", ("You asked for in-text citations, but I could not cite anything from your literature library" + (f" ({why})" if why else "") +
                                                                  ". I am looking for verified references on the subject of this text, so you can choose which ones to add and cite.")))
                job = _start_refsearch(app, n, p, cid, text, u["chunk_ids"], facts, "", issue, "", [], auto_cite=True)
                return {"messages": out, "job": job}
            guard = config.ui_cfg()["guard_passages"]
            ask = config.paper_settings(p)["confirm_before_send"] or pl["units"] > guard
            reason = ("big" if pl["units"] > guard else "setting") if ask else None
            m = chat.append(p, cid, "assistant", "plan", "", plan=pl, title=u["title"], notes=u["notes"], chunk_ids=u["chunk_ids"], state="waiting", confirm=reason,
                            **({"issue": issue} if issue else {}), **({"revises": revises} if revises else {}))
            out.append(m)
            if not ask:
                job = _start_pass(app, n, p, cid, m["id"])
                out[-1] = chat.get_message(p, cid, m["id"])
    return {"messages": out, "job": job}




def _library_answer(n, p, cid, text):
    """"What do my articles say about X?": the best parts of the library, with where they are from and how to cite them. Local and free: nothing goes to OpenAI."""
    topic = chat.library_topic(text)
    st = library.status()
    if not topic:
        return chat.append(p, cid, "assistant", "text", "What should I look for in your library? For example: what do my articles say about well data curation?")
    if not any(f["ok"] for f in st["folders"]) or not st["docs"]:
        why = "Your literature library is empty." if any(f["ok"] for f in st["folders"]) else "You have not chosen a folder for your literature library yet."
        return chat.append(p, cid, "assistant", "text", why + " Open Settings, add the folder where you keep your articles and books, and press \"Read the folders\". Then ask again.")
    res = library.query(topic, k=6, budget=3000, per_doc=2)
    hits = library.annotate(res["chunks"], p)
    cmd = library.cite_command(p)
    slim = [{"id": c["id"], "doc": c["doc"], "label": c["label"], "title": c["title"], "authors": c["authors"], "year": c["year"], "doi": c["doi"], "where": c["where"], "pages": c["pages"],
             "text": c["text"][:700] + ("..." if len(c["text"]) > 700 else ""), "in_bib": c["in_bib"], "cite_key": c["cite_key"], "can_add": c["can_add"],
             "has_entry": bool(library.get_doc(c["doc"])["bibtex"])} for c in hits]
    return chat.append(p, cid, "assistant", "library", "", topic=topic, hits=slim, command=cmd, docs=res["docs"], total=st["docs"])


def _recent_picks(p, cid, limit=6):
    """The DOIs of the references the author ticked and added in this chat, the latest first."""
    out = []
    for m in reversed(chat.get_thread(p, cid)["messages"]):
        if m["kind"] == "refsearch":
            for doi in (m["data"].get("added") or {}):
                if doi not in out:
                    out.append(doi)
    return out[:limit]


CITE_REQUEST = "Support the claims of this text with citations from the sources provided: cite a source only where its abstract says what the text says, and leave everything else as it is."
_CITE_WORDS = re.compile(r"\b(?:citation|citations|cite|reference|references|source|sources|literature|evidence|studies|study|publication|publications|peer-reviewed)\b", re.I)


def _start_refsearch(app, n, p, cid, text, ticked, facts, revises, issue, words, last_ids, force_draft=False, auto_cite=False):
    """Look for references on the internet in the background; the candidates land in the chat as a card. The search words are chosen from what the author wrote, the passage it is
    about and the author's answers; nothing but those few words leaves this computer, and nothing is added to the bibliography until the author ticks it."""
    def work(progress, set_steps):
        steps = revise.Steps(set_steps)
        try:
            steps.start("words", "Working out what to look for")
            ctx = chat.refs_context(n, p, text, chunk_ids=ticked, last_ids=last_ids)
            instruction, questions, is_draft, rev = (text if (force_draft or auto_cite) else ""), [], bool(force_draft), revises or ""
            if PID_RE.match(rev):
                try:
                    prop = revise.get_proposal(p, rev)
                    instruction, is_draft = prop.get("instruction", ""), prop.get("kind") == "draft"
                    questions = [q for u in prop["units"] for q in (u.get("questions") or []) if _CITE_WORDS.search(q)]
                except revise.ReviseError:
                    rev = ""
            about = ""
            if is_draft:                                  # writing from an outline: the subject is the outline's sections that were asked for and the abstract, not the request's own words
                try:
                    o_text, _, _ = files.outline_select(n, p, instruction or text)
                    about = draft.subject_of(n, p, instruction or text, o_text, facts)
                except (OSError, ValueError, KeyError, paper_index.IsolationError):
                    about = ""
            topic = words or (" ".join(scholar.focus_terms(about, max_terms=6)) if about else ctx["topic"])
            if not ctx["passage"] and not facts and not about and len(scholar.terms(topic)) < 2:        # nothing to go on: no subject, and no text it could be about
                steps.finish("words", "nothing to search for", state="failed")
                chat.append(p, cid, "assistant", "text", "What should the references be about? Say it in a few words, for example: find references on how armed conflict affects energy infrastructure in Colombia. "
                            "Or name the place in your paper they are for, for example: suggest sources for the first paragraph of the introduction.")
                return {"found": 0}
            cfg = config.openai_cfg() if config.openai_status()[0] == "ok" else None
            queries, usage, how = scholar.choose_queries(topic, request=instruction or text, facts=facts, passage=ctx["passage"], question=" ".join(questions), cfg=cfg, given=words, subject_text=about)
            if not queries:
                raise scholar.ScholarError("I could not tell what to look for. Say it in a few words, for example: find references on how armed conflict affects energy infrastructure in Colombia.")
            steps.finish("words", " · ".join(queries), label="Chose the search words" + (" with ChatGPT" if how == "model" else ""))
            res = scholar.search(queries, steps=steps)
        except (scholar.ScholarError, config.ConfigError, ValueError, FileNotFoundError, paper_index.IsolationError) as e:
            if any(i["key"] == "words" and i["state"] == "running" for i in steps.items):
                steps.finish("words", state="failed")
            chat.append(p, cid, "assistant", "error", plain_error(e) if not isinstance(e, scholar.ScholarError) else str(e))
            raise
        bib = library.paper_bib(p)
        cands = []
        for c in res["candidates"]:
            if scholar.bad_query(c["title"], f"{about} {topic} {' '.join(queries)}"):         # a guide to writing is not a reference for a paper about something else
                continue
            have = library.cite_for({"doi": c["doi"], "title": c["title"]}, bib)
            cands.append({"id": c["doi"], "doi": c["doi"], "title": c["title"], "authors": (c.get("authors") or [])[:10], "year": c.get("year"), "venue": c.get("venue", ""), "label": c["label"],
                          "abstract": (c.get("abstract") or "")[:2500], "url": c.get("url", ""), "cited_by": c.get("cited_by") or 0, "sources": c.get("sources", []),
                          "in_bib": bool(have), "cite_key": have["key"] if have else ""})
        ctx_out = {"chunk_ids": ctx["chunk_ids"], "where": ctx["where"], "request": instruction or (CITE_REQUEST if ctx["chunk_ids"] else ""), "facts": facts, "revises": rev, "issue": issue or "", "draft": is_draft}
        chat.append(p, cid, "assistant", "refsearch", "", topic=topic, queries=res["queries"], how=how, candidates=cands, others=res["others"], tried=res["tried"], ctx=ctx_out, steps=steps.items,
                    usage=usage or {}, command=library.cite_command(p), added={})
        return {"found": len(cands)}
    return app.jobs.submit(f"Looking for references for {n}", work, with_steps=True, meta={"paper": n, "chat": cid})


def _start_refs_pick(app, n, p, cid, mid, dois):
    """Put the references the author ticked in the bibliography (and Mendeley, and Overleaf) and remember their abstracts, so a request can cite them."""
    msg = chat.get_message(p, cid, mid)
    if not msg or msg["kind"] != "refsearch":
        raise ApiError(404, "those references are not in this chat")
    cands = {c["doi"]: c for c in msg["data"]["candidates"]}
    picks = list(dict.fromkeys(d for d in dois if d in cands))[:12]
    if not picks:
        raise ApiError(400, "tick at least one reference")
    cmd = msg["data"].get("command") or "\\cite"

    def work(progress, set_steps):
        common.set_active(n, "ui")
        todo = [d for d in picks if not cands[d]["cite_key"]]
        res = add_references(n, p, todo, mirror="auto", progress=set_steps) if todo else {"results": [], "push": None, "steps": []}
        added = dict(chat.get_message(p, cid, mid)["data"].get("added") or {})
        for d in picks:
            if cands[d]["cite_key"]:
                added[d] = {"key": cands[d]["cite_key"], "status": "had"}
        for d, x in zip(todo, res["results"]):
            if x["status"] in ("added", "duplicate") and x.get("key"):
                added[d] = {"key": x["key"], "status": x["status"]}
        for v in added.values():
            v["cite"] = f"{cmd}{{{v['key']}}}"
        scholar.remember(p, [dict(cands[d], key=added[d]["key"]) for d in picks if d in added])
        chat.update(p, cid, mid, added=added)
        if res["results"]:
            chat.append(p, cid, "assistant", "refs", "", results=res["results"], push=res["push"], steps=res["steps"], pending=len(doi_bot.pending_list(p)))
        return {"added": sum(1 for x in res["results"] if x["status"] == "added"), "usable": len(added)}
    return app.jobs.submit(f"Adding references to {n}", work, with_steps=True, meta={"paper": n, "chat": cid})


def api_chat_refs_pick(r):
    n, p, cid = _chat_ctx(r)
    (mid,) = need(r.body, "message")
    dois = [str(d) for d in (r.body.get("dois") or []) if isinstance(d, str)]
    return {"job": _start_refs_pick(r.app, n, p, cid, str(mid), dois)}


def _start_refs(app, n, p, cid, dois):
    """Pasted DOIs: add them to the bibliography (and Mendeley, and Overleaf) in the background; the cards land in the chat."""
    def work(progress, set_steps):
        common.set_active(n, "ui")
        res = add_references(n, p, dois, mirror="auto", progress=set_steps)
        chat.append(p, cid, "assistant", "refs", "", results=res["results"], push=res["push"], steps=res["steps"], pending=len(doi_bot.pending_list(p)))
        return {"added": sum(1 for x in res["results"] if x["status"] == "added")}
    return app.jobs.submit(f"Adding references to {n}", work, with_steps=True, meta={"paper": n, "chat": cid})


def _start_review(app, n, p, cid, mid):
    msg = chat.get_message(p, cid, mid)
    if not msg or msg["kind"] != "review_plan":
        raise ApiError(404, "that review is not in this chat")
    if msg["data"].get("state") != "waiting":
        raise ApiError(409, "that review was already started")
    plan = msg["data"]["plan"]
    chat.update(p, cid, mid, state="sent")

    def work(progress, set_steps):
        try:
            res = review.run(n, p, plan.get("chunk_ids"), plan["scope"], confirm=plan["hash"], source="web", progress=set_steps)
        except Exception as e:  # noqa: BLE001
            chat.update(p, cid, mid, state="failed")
            chat.append(p, cid, "assistant", "error", plain_error(e))
            raise
        chat.append(p, cid, "assistant", "review", "", scope=plan["scope"], **res)
        return {"issues": len(res["issues"])}
    return app.jobs.submit(f"Reviewing {n}", work, with_steps=True, meta={"paper": n, "chat": cid})


def api_chat_run(r):
    n, p, cid = _chat_ctx(r)
    (mid,) = need(r.body, "message")
    m = chat.get_message(p, cid, str(mid))
    if m and m["kind"] == "review_plan":
        return {"job": _start_review(r.app, n, p, cid, str(mid))}
    if m and m["kind"] == "draft_plan":
        return {"job": _start_draft(r.app, n, p, cid, str(mid))}
    if m and m["kind"] == "layout_plan":
        return {"job": _start_layout(r.app, n, p, cid, str(mid))}
    return {"job": _start_pass(r.app, n, p, cid, str(mid))}


def api_proposal_check(r):
    n, p = paper_ref(r.body.get("paper", ""))
    texts = r.body.get("texts")
    if not isinstance(texts, dict):
        raise ApiError(400, "texts must be an object of passage ids to text")
    return {"checks": revise.check_adopted(p, _pid(r), {str(k): str(v)[:60000] for k, v in texts.items()})}


def api_proposal_approve(r):
    """Save the suggestion (or the author's own version of it) into the files, and optionally send it to Overleaf in the same click."""
    n, p = paper_ref(r.body.get("paper", ""))
    pid = _pid(r)
    texts, accept, cid = r.body.get("texts"), r.body.get("accept", "all"), r.body.get("chat")
    is_layout = revise.get_proposal(p, pid).get("kind") == "layout"
    if is_layout and texts is not None:
        raise ApiError(400, "a layout change is applied as it is; it has no text to edit")
    if texts is not None:
        if not isinstance(texts, dict):
            raise ApiError(400, "texts must be an object of passage ids to text")
        ad = revise.adopt(n, p, pid, {str(k): str(v)[:60000] for k, v in texts.items()}, acknowledge=bool(r.body.get("acknowledge")), source="web")
        if not ad["ok"]:
            return {"ok": False, "needs_ack": ad["needs_ack"], "blocked": ad["blocked"], "checks": ad["checks"]}
        accept = "all"
    if r.body.get("mode") == "handoff":
        try:
            h = revise.handoff(n, p, pid, accept=accept, source="web")
        except revise.ReviseError as e:
            raise ApiError(409, str(e)) from None
        return {"ok": True, "handoff": h}
    url = config.ui_cfg()["watermark_url"]
    cleaner = watermark.make_cleaner(url) if url and not is_layout else None             # the text cleaner is for prose; a layout change is LaTeX
    unclean = bool(r.body.get("unclean_ok")) and not url      # the author saves as written because no text-cleaning service is set up; with one set up it always runs
    try:
        if is_layout:
            out = layout.apply(n, p, pid, source="web")
        else:
            out = revise.apply(n, p, pid, accept=accept, cleaner=cleaner, unclean_ok=unclean, source="web",
                               note=("edited by you" if texts is not None else "") + (" (saved without the text-cleaning step)" if unclean else ""))
    except revise.ReviseError as e:
        raise ApiError(409, str(e), handoff_available=not is_layout) from None
    files = sorted({w["file"] for w in out["written"]})
    res = {"ok": True, "applied": out, "files": files, "cleaned": bool(cleaner)}
    added_refs = [a["key"] for a in (out.get("bibliography") or {}).get("added", [])]
    if cid:
        try:
            chat.append(p, cid, "assistant", "note", f"Saved in {', '.join(files)}." + (" This is your own version." if texts is not None else "")
                        + ((" Added to your bibliography: " + ", ".join(added_refs) + ".") if added_refs else "")
                        + ("" if cleaner or is_layout else " Saved exactly as written: no text-cleaning service is set up."), proposal=pid)
        except ValueError:
            pass
    if cid:
        try:
            chat.close_reviews(p, cid)                  # saving the last open comment of a review finishes it
        except ValueError:
            pass
    if r.body.get("push"):
        prop = revise.get_proposal(p, pid)
        what = ", ".join(taxonomy.label(x) for x in prop.get("part") or []) or "text"
        res["push"] = git_action(n, "sync", "Writing assistant: change the layout" if is_layout else f"Writing assistant: improve {what}")
        if res["push"].get("sent"):
            res["push"]["pane"] = {"reloading": refresh_overleaf(r.app, n)}
        if cid and "hint" not in res["push"] and res["push"].get("level") == "ok":      # only when Overleaf really took it
            try:
                chat.append(p, cid, "assistant", "note", "Sent to Overleaf.", proposal=pid)
            except ValueError:
                pass
    return res


def api_sections(r):
    n, p = paper_ref(r.m.group(1))
    paper_index.refresh(n, p, trigger="outline", source="web")
    return {"sections": chat._sections([c for c in paper_index.list_chunks(n, p)["chunks"] if c["kind"] == "tex"])}


def api_paper_text(r):
    """The paper's own lines for the 'Paper text' pane: the pieces a request used, or a whole section, read fresh from the files."""
    n, p = paper_ref(r.m.group(1))
    ids = [i for i in (r.arg("ids", "") or "").split(",") if i][:80]
    out, total = [], 0
    for rid in ids:
        cid, a, b = revise.split_range_id(rid)
        got = paper_index.get_chunks(n, p, [cid])
        if not got or got[0]["kind"] != "tex":
            continue
        c = got[0]
        a, b = (max(a, c["line_start"]), min(b, c["line_end"])) if a else (c["line_start"], c["line_end"])
        lines, _ = revise._read_lines(Path(p["path"]) / c["file"])
        text = "\n".join(lines[a - 1:b])
        total += len(text)
        if total > 300000:
            break
        out.append({"id": rid, "file": c["file"], "lines": [a, b], "section": c["section_path"], "text": text})
    return {"pieces": out}


# ---------------------------------------------------------------------------- overview, connections, tests, adding papers

JUNK_END = overleaf_mcp.JUNK


def pending_files(proj):
    r = subprocess.run(["git", "-C", proj["path"], "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True, timeout=30)
    rows = []
    for ln in r.stdout.splitlines():  # not run_git: it strips the first line's leading space, which is part of the status code
        shown = ln[3:].strip().strip('"')
        if len(ln) > 3 and (shown.startswith(overleaf_mcp.KEEP_DIRS) or not shown.endswith(JUNK_END)) and not shown.startswith(".overleaf-skill"):
            rows.append({"file": ln[3:].strip().strip('"'), "what": _STATUS_WORDS.get(ln[:2].strip()[:1], "changed")})
    return rows


# ---------------------------------------------------------------------------- the author's files: uploads, code and results

_GROUPS = {"py": "code", "r": "code", "rmd": "code", "m": "code", "jl": "code", "sql": "code", "sh": "code", "ipynb": "notebook", "csv": "table", "tsv": "table", "json": "results",
           "yaml": "results", "yml": "results", "toml": "results", "log": "log", "txt": "text", "md": "text", "markdown": "text", "rst": "text", "tex": "table",
           "png": "figure", "jpg": "figure", "jpeg": "figure", "gif": "figure", "webp": "figure", "pdf": "document", "docx": "document"}
GROUP_NAMES = {"code": "Code", "notebook": "Notebooks", "table": "Tables", "results": "Results files", "log": "Logs", "text": "Notes", "figure": "Figures", "document": "Articles and reports"}


def _file_view(f):
    ext = files.ext_of(f["file"])
    return {"file": f["file"], "name": f["file"].rsplit("/", 1)[-1], "kind": f["kind"], "role": files.role_of(f), "group": "instruction" if files.role_of(f) == "outline" else _GROUPS.get(ext, "text"), "upload": f["kind"] == "source", "size": f["size"],
            "parts": f["parts"], "words": int(f["tokens"] * 0.75), "note": f.get("note", ""), "reads": f.get("reads", True)}


def _shared_folders():
    """The folders shared with the app to read (ui.sh folders add --read-only): where a library or a folder of material can be taken from. In Docker the app sees nothing else."""
    return list(dict.fromkeys(common.data_roots()))


def _folder_views(proj, listed, role="material"):
    """The author's linked folders of one kind with what was read from each: [{path, ok, note, files, parts, groups: {code: n, figure: n, ...}}]"""
    out = []
    for f in files.folders(proj, role):
        mine = [x for x in listed if x["file"].startswith(f["path"].rstrip("/") + "/")]
        groups = {}
        for x in mine:
            if x.get("parts"):
                g = _GROUPS.get(files.ext_of(x["file"]), "text")
                groups[g] = groups.get(g, 0) + 1
        out.append({**f, "files": len(mine), "parts": sum(x.get("parts", 0) for x in mine), "groups": groups})
    return out


def api_files(r):
    n, p = paper_ref(r.arg("paper", ""))
    paper_index.refresh(n, p, trigger="files", source="web")
    st = config.paper_settings(p)
    listed = files.listing(n, p)
    return {"files": [_file_view(f) for f in listed], "folders": _folder_views(p, listed), "analysis_folders": _folder_views(p, listed, "analysis"), "use_files": st["use_files"],
            "use_analysis": st["use_analysis"], "send_uploads": st["send_uploads"], "max_mb": files.MAX_UPLOAD >> 20, "folder": files.UPLOAD_DIR, "shared": _shared_folders(),
            "group_names": GROUP_NAMES}


def api_files_parts(r):
    n, p = paper_ref(r.arg("paper", ""))
    rel = r.arg("file", "")
    if rel not in {f["file"] for f in files.listing(n, p)}:
        raise ApiError(404, "that file is not one of this paper's files")
    return {"file": rel, "parts": files.parts_of_file(n, p, rel)}


def api_files_upload(r):
    """Save evidence in references, but keep an author prompt/outline local as writing instructions."""
    n, p = paper_ref(r.body.get("paper", ""))
    name, b64, purpose = r.body.get("name"), r.body.get("data"), r.body.get("purpose")
    if not isinstance(name, str) or not isinstance(b64, str):
        raise ApiError(400, "send the file's name and its content")
    try:
        data = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error):
        raise ApiError(400, "the file did not arrive intact; try again") from None
    try:
        saved = files.save_upload(p, name, data, purpose=purpose)
    except files.FileProblem as e:
        raise ApiError(400, str(e)) from None
    paper_index.refresh(n, p, trigger="upload", source="web")
    info = next((f for f in files.listing(n, p) if f["file"] == saved["rel"]), None)
    res = {"ok": True, "saved": saved, "file": _file_view(info) if info else None}
    send = False if saved.get("outline") else r.body.get("send")
    if send is None:
        send = config.paper_settings(p)["send_uploads"]
    if send:
        res["push"] = git_action(n, "files", f"Add {saved['name']} to the references", paths=[saved["rel"]])
        if res["push"].get("sent"):
            res["push"]["pane"] = {"reloading": refresh_overleaf(r.app, n, recompile=False)}      # the new file shows in Overleaf's file list after a reload
    cid = r.body.get("chat")
    if cid:
        if saved.get("outline"):
            bits = [f"{saved['name']} is an author outline. I will follow it when drafting, but it is not evidence, is never cited, and is not sent to Overleaf."]
        else:
            bits = [f"{saved['name']} was already in the {files.UPLOAD_DIR} folder." if saved["duplicate"] else f"Added {saved['name']} to the {files.UPLOAD_DIR} folder."]
        if info and info.get("parts"):
            bits.append(f"I split it into {info['parts']} part{'s' if info['parts'] != 1 else ''} (about {int(info['tokens'] * 0.75):,} words) that a request can read one at a time.")
        elif info and info.get("note"):
            bits.append(info["note"])
        push = res.get("push") or {}
        if push.get("sent"):
            bits.append("Sent to Overleaf.")
        elif send:
            bits.append("It is saved here but not in Overleaf yet: " + (push.get("summary") or push.get("friendly") or "see the Send button."))
        elif not saved.get("outline"):
            bits.append("It is saved here; it goes to Overleaf with your next send.")
        try:
            chat.append(p, cid, "assistant", "note", " ".join(bits))
        except ValueError:
            pass
    return res


def api_files_remove(r):
    n, p = paper_ref(r.body.get("paper", ""))
    try:
        files.remove_upload(p, str(r.body.get("file", "")))
    except files.FileProblem as e:
        raise ApiError(400, str(e)) from None
    paper_index.refresh(n, p, trigger="upload", source="web")
    return {"ok": True, "note": "Removed here. It leaves Overleaf too the next time you send."}


def api_files_folders(r):
    """The folders (outside the Overleaf project) whose code and results this paper may read."""
    n, p = paper_ref(r.body.get("paper", ""))
    raw = r.body.get("folders")
    role = r.body.get("role") or "material"
    if role not in files.ROLES:
        raise ApiError(400, "role must be material or analysis")
    if not isinstance(raw, list) or len(raw) > 8:
        raise ApiError(400, "folders must be a list of at most 8 paths")
    try:
        files.set_folders(p, raw, role)
    except files.FileProblem as e:
        raise ApiError(400, str(e)) from None
    paper_index.refresh(n, p, trigger="files", source="web")
    listed = files.listing(n, p)
    return {"folders": _folder_views(p, listed, role), "files": [_file_view(f) for f in listed]}


# ---------------------------------------------------------------------------- the literature library (books, articles and PDFs the author keeps in folders)

def _library_status():
    st = library.status()
    st["shared"] = _shared_folders()
    st["in_docker"] = os.environ.get("OVERLEAF_UI_IN_DOCKER") == "1"
    return st


def api_library(r):
    q = r.arg("q", "")
    docs = library.docs(q)
    return {"status": _library_status(), "docs": docs[:300], "matching": len(docs)}


def api_library_folders(r):
    raw = r.body.get("folders")
    if not isinstance(raw, list):
        raise ApiError(400, "folders must be a list of paths")
    library.set_folders(raw)
    return {"status": _library_status()}


def api_library_index(r):
    if library.running():
        raise ApiError(409, "The library is being read already; wait for it to finish.")
    if not any(f["ok"] for f in library.folders()):
        raise ApiError(400, "None of the library's folders can be read yet. Add a folder first.")
    rebuild = bool(r.body.get("rebuild"))

    def job(progress):
        if rebuild:
            library.clear()
        return library.update(progress)
    return {"job": r.app.jobs.submit("Reading the library", job, with_progress=True, meta={"library": True})}


def api_library_parts(r):
    doc = r.arg("doc", "")
    d = library.get_doc(doc)
    if not d:
        raise ApiError(404, "that document is not in the library")
    return {"doc": {k: d[k] for k in ("id", "rel", "title", "authors", "year", "doi", "label", "pages", "parts", "note")}, "parts": library.parts(doc)}


def api_library_cite(r):
    """Make a document of the library citable in a paper: its entry from a .bib in the library is copied into the paper's bibliography, or its DOI is added the usual way."""
    n, p = paper_ref(r.body.get("paper", ""))
    (doc_id,) = need(r.body, "doc")
    d = library.get_doc(str(doc_id))
    if not d:
        raise ApiError(404, "that document is not in the library")
    cmd = library.cite_command(p)
    have = library.cite_for(d, library.paper_bib(p))
    if have:
        return {"status": "in_bib", "key": have["key"], "cite": f"{cmd}{{{have['key']}}}", "message": f"It is already in your bibliography as {have['key']}."}
    bib = common.bib_path(p)
    if d["bibtex"]:
        key = library.add_entry_from_record(d["id"], str(bib))
        paper_index.refresh(n, p, trigger="after_edit", source="web")
        return {"status": "added", "key": key, "cite": f"{cmd}{{{key}}}", "message": f"Added {key} to {Path(bib).name}. It goes to Overleaf with your next send."}
    if d["doi"]:
        common.set_active(n, "ui")
        out = add_references(n, p, [d["doi"]], mirror="auto")
        res = out["results"][0]
        if res["status"] not in ("added", "duplicate"):
            raise ApiError(400, res["message"])
        return {"status": res["status"], "key": res.get("key", ""), "cite": res.get("cite", ""), "message": res["message"], "push": out["push"], "result": res}
    raise ApiError(400, "This document has no DOI and no entry in a .bib file, so I cannot add it to your bibliography. Paste its DOI in the chat instead.")


def api_library_search(r):
    q = r.arg("q", "").strip()
    if not q:
        raise ApiError(400, "type what to look for")
    res = library.query(q, k=8, budget=4000, per_doc=2)
    paper = r.arg("paper", "")
    if paper:
        _, p = paper_ref(paper)
        library.annotate(res["chunks"], p)
    return res


def api_overview(r):
    """Cheap, local facts for the chat header: what is waiting to be sent, and when Overleaf was last asked."""
    n, p = paper_ref(r.arg("paper", ""))
    rows = pending_files(p)
    unsent = overleaf_mcp.unsent_commits(p["path"])       # saved here, but Overleaf does not have them (a rejected send leaves them behind)
    fh = Path(p["path"]) / ".git" / "FETCH_HEAD"
    last = fh.stat().st_mtime if fh.exists() else None
    bits = []
    if rows:
        bits.append(f"{len(rows)} file{'s' if len(rows) != 1 else ''} changed here, not sent yet")
    if unsent:
        bits.append(f"{unsent} saved change{'s' if unsent != 1 else ''} not in Overleaf yet")
    sentence = ", ".join(bits) if bits else "Nothing waiting to be sent to Overleaf"
    return {"paper": n, "pending": rows[:30], "pending_count": len(rows), "unsent": unsent, "sentence": sentence, "last_checked": last, "index": paper_index.peek(p)}


def _panes_settings(r):
    ui = config.ui_cfg()
    out = {"builtin": {k: bool(ui.get("builtin_" + k, True)) for k in ("quillbot", "overleaf")},
           "mode": ui.get("pane_mode") if ui.get("pane_mode") in PANE_MODES else "auto", "quillbot_url": panes.quillbot_url(ui), "quillbot_default": panes.QUILLBOT_URL,
           "overleaf_url": None, "overleaf_derived": False, "overleaf_override": ""}
    paper = r.arg("paper")
    if paper:
        n, p = paper_ref(paper)
        override = config.paper_settings(p).get("overleaf_url") or ""
        out.update(overleaf_url=panes.overleaf_url(p, override), overleaf_override=override, overleaf_derived=bool(not (override and panes.valid_overleaf(override)) and panes.overleaf_url(p)))
    return out


def api_connections(r):
    d = config.data_dir()
    tg = common.load("telegram.json", {}) or {}
    mend_creds, mend_tokens = (d / "mendeley_credentials.json").exists(), (d / "mendeley_tokens.json").exists()
    here = os.environ.get("OLS_HOST_DIR") or str(SCRIPTS.parent)
    ostate, omsg = config.openai_status()
    model = None
    if ostate == "ok":
        model = config.openai_cfg()["model"]
    wstate, wdetail = watermark.check(config.ui_cfg()["watermark_url"]) if config.ui_cfg()["watermark_url"] else ("missing", "no address set")
    return {"openai": {"state": ostate, "model": model, "detail": omsg if ostate != "ok" else ""},
            "overleaf": {"token": (d / "overleaf_git.json").exists()},
            "cleaner": {"state": wstate, "detail": wdetail, "url": config.ui_cfg()["watermark_url"]},
            "mendeley": {"state": "connected" if mend_creds and mend_tokens else "needs_sign_in" if mend_creds else "not_set_up",
                         "command": f'python3 "{here}/scripts/mendeley_sync.py" authorize'},
            "telegram": {"state": "set_up" if tg.get("token") else "not_set_up", "allowed": len(tg.get("allowed_chat_ids") or [])},
            "limits": {k: config.ui_cfg()[k] for k in ("guard_passages", "max_passages", "idle_minutes")},
            "panes": _panes_settings(r), "in_docker": os.environ.get("OVERLEAF_UI_IN_DOCKER") == "1",
            "sites": _logins(), "browser": {k: v for k, v in r.app.browser().status().items() if k in ("available", "reason", "kind")}}


def api_test_openai(r):
    try:
        res = openai_client.check(config.openai_cfg())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": plain_error(e)}
    return {"ok": True, "message": f"OpenAI accepted your key and knows the model {res['model']}."}


def _git_diagnosis(name):
    """Why Overleaf does not take the sign-in for this paper (see overleaf_auth), or None when that cannot be found out."""
    try:
        n, p = common.resolve(name)
        token = str((common.load("overleaf_git.json", {}) or {}).get("token") or "")
        return overleaf_auth.diagnose(p["path"], common.remote_url(p["path"]), token, overleaf_id=panes.overleaf_id(config.paper_settings(p).get("overleaf_url")))
    except Exception:  # noqa: BLE001
        return None


def api_test_git(r):
    n, p = paper_ref(r.body.get("paper", ""))
    d = _git_diagnosis(n)
    if d is None:
        return {"ok": False, "message": "I could not test the Overleaf sign-in for this paper.", "steps": [], "fix": None}
    return {"ok": d["ok"], "message": d["message"], "steps": d["steps"], "fix": d["fix"], "kind": d["kind"]}


def _clone_overleaf(target, project_id):
    """git clone of an Overleaf project into `target`. -> (code, error text)"""
    import shutil
    r = subprocess.run(["git", "clone", "--quiet", overleaf_auth.git_address(project_id), str(target)], capture_output=True, text=True, timeout=900,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "LANG": "C"})
    if r.returncode != 0 and Path(target).exists():
        shutil.rmtree(target, ignore_errors=True)                  # no half-made folder is left behind
    return r.returncode, (r.stderr or r.stdout).strip()


def api_clone_overleaf(r):
    """Make a copy of the paper's Overleaf project that comes straight from Overleaf (a paper cloned from GitHub cannot send to Overleaf), in a folder shared with the app, and add it as a paper."""
    n, p = paper_ref(r.body.get("paper", ""))
    pid = panes.overleaf_id(r.body.get("overleaf") or config.paper_settings(p).get("overleaf_url") or "")
    if not pid:
        raise ApiError(400, "I need the Overleaf project's web address (https://www.overleaf.com/project/...). Save it for this paper in Settings, further down on the page, and try again.")
    roots = common.workspace_roots()
    if not roots:
        raise ApiError(409, "No folder is shared with the app for new copies yet. In Terminal run   ./ui.sh folders add ~/Documents/overleaf   (any folder you like), then   ./ui.sh down   and   ./ui.sh up , and press the button again.")
    if not (config.data_dir() / "overleaf_git.json").exists():
        raise ApiError(409, "Paste your Overleaf Git token above and press Save first.")
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", str(r.body.get("name") or f"{n}-overleaf"))[:60] or "paper-overleaf"
    name, k = base, 2
    reg = common.registry()["projects"]
    while name in reg or (Path(roots[0]) / name).exists():
        name, k = f"{base}-{k}", k + 1
    target = Path(roots[0]) / name
    group = p.get("mendeley_group")

    def work(progress, set_steps):
        steps = revise.Steps(set_steps)
        steps.start("clone", "Copying the project from Overleaf")
        code, err = _clone_overleaf(target, pid)
        if code != 0:
            kind = overleaf_auth.classify(err)
            why = {"rejected": "Overleaf refused the token.", "no_credentials": "The app had no sign-in to give Overleaf (restart it with ./ui.sh down and ./ui.sh up).",
                   "forbidden": "Overleaf accepted the token but did not let it into this project: it must be made in an account that can open the project.",
                   "not_found": "Overleaf does not know a project with that address for this token.", "unreachable": "I could not reach Overleaf: " + (err.splitlines() or [""])[-1][:150]}.get(kind, (err.splitlines() or ["no answer"])[-1][:200])
            steps.finish("clone", why, state="failed")
            raise ValueError(why)
        steps.finish("clone", "done")
        steps.add("register", "Adding it as a paper", detail=name)
        new = common.register(str(target), name, group)
        return {"name": new}
    return {"job": r.app.jobs.submit(f"Copying {n} from Overleaf", work, with_steps=True, meta={"paper": n}), "name": name, "folder": str(target)}


def api_clean_address(r):
    """Take a user name and password out of the paper's Overleaf address (git would use them instead of the token saved in Settings)."""
    n, p = paper_ref(r.body.get("paper", ""))
    new = overleaf_auth.clean_address(common.remote_url(p["path"]))
    if not new:
        raise ApiError(409, "This paper's address has no user name or password in it.")
    code, out, err = overleaf_mcp.run_git(p["path"], "remote", "set-url", "origin", new)
    if code != 0:
        raise ApiError(500, "I could not change the address: " + (err or out)[-150:])
    reg = common.registry()
    if n in reg["projects"]:
        reg["projects"][n]["remote"] = new
        common.save("projects.json", reg)
    return {"ok": True, "message": f"The address is now {new}. Press Test again.", "address": new}


def api_test_cleaner(r):
    url = config.ui_cfg()["watermark_url"]
    state, detail = watermark.check(url) if url else ("missing", "no address saved yet")
    return {"ok": state == "ok", "message": "The text-cleaning service answered." if state == "ok" else f"{detail or state}"}


def _overleaf_clone(path):
    return (Path(path) / ".git").exists() and "overleaf.com" in (common.remote_url(path) or "")


def api_discover(r):
    roots = common.workspace_roots()
    have = {str(Path(p["path"]).resolve()) for p in common.registry()["projects"].values()}
    found = []
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        level1 = [base] + [d for d in sorted(base.iterdir())[:300] if d.is_dir() and not d.name.startswith(".")]
        level2 = [g for d in level1[1:] for g in sorted(d.iterdir())[:100] if g.is_dir() and not g.name.startswith(".")] if len(level1) < 300 else []
        for d in level1 + level2:
            if _overleaf_clone(d):
                found.append({"path": str(d), "name": d.name, "remote_id": common.remote_id({"remote": common.remote_url(d)}), "registered": str(d.resolve()) in have})
    return {"roots": roots, "found": found}


def api_add_paper(r):
    (path,) = need(r.body, "path")
    target = Path(str(path)).resolve()
    if not any(common.inside(target, root) for root in common.workspace_roots()):
        raise ApiError(403, "that folder is not one of the folders shared with the app (ui.sh folders add <folder>)")
    if not _overleaf_clone(target):
        raise ApiError(400, "that folder is not an Overleaf project clone (its Git address is not Overleaf's)")
    reg = common.registry()["projects"]
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(r.body.get("name") or target.name))[:60] or "paper"
    base, k = name, 2
    while name in reg and str(Path(reg[name]["path"]).resolve()) != str(target):
        name, k = f"{base}-{k}", k + 1
    group = str(r.body.get("group") or "").strip()[:100] or None
    name = common.register(str(target), name, group)
    return {"name": name}


def api_settings(r):
    body = r.body
    if body.get("paper") is not None:
        n, p = paper_ref(body["paper"])
        for key in ("dry_run", "confirm_before_send", "send_refs_to_overleaf", "mirror_to_mendeley", "send_uploads"):
            if key in body:
                config.set_paper_setting(p, key, bool(body[key]))
        if "use_files" in body:
            if body["use_files"] not in ("auto", "always", "never"):
                raise ApiError(400, "use_files must be auto, always or never")
            config.set_paper_setting(p, "use_files", body["use_files"])
        if "use_analysis" in body:
            if body["use_analysis"] not in ("auto", "always", "never"):
                raise ApiError(400, "use_analysis must be auto, always or never")
            config.set_paper_setting(p, "use_analysis", body["use_analysis"])
        if "use_library" in body:
            if body["use_library"] not in ("auto", "always", "never"):
                raise ApiError(400, "use_library must be auto, always or never")
            config.set_paper_setting(p, "use_library", body["use_library"])
        if "understand" in body:
            if body["understand"] not in UNDERSTAND_MODES:
                raise ApiError(400, "understand must be always, unsure or never")
            config.set_paper_setting(p, "understand", body["understand"])
        if "forget_understanding" in body and body["forget_understanding"]:
            intent.forget(p)
        if "use_online" in body:
            if body["use_online"] not in ("auto", "never"):
                raise ApiError(400, "use_online must be auto or never")
            config.set_paper_setting(p, "use_online", body["use_online"])
        if "overleaf_url" in body:
            v = str(body["overleaf_url"] or "").strip()
            if v and not panes.valid_overleaf(v):
                raise ApiError(400, "the Overleaf address must look like https://www.overleaf.com/project/<project id>")
            config.set_paper_setting(p, "overleaf_url", v)
    ui = body.get("ui")
    if isinstance(ui, dict):
        cur = common.load("ui.json", {}) or {}
        if "idle_minutes" in ui:
            try:
                cur["idle_minutes"] = max(5, min(int(ui["idle_minutes"]), 24 * 60))
            except (TypeError, ValueError):
                raise ApiError(400, "idle_minutes must be a number") from None
        if "quillbot_url" in ui:
            v = str(ui["quillbot_url"] or "").strip()
            if v and not panes.valid_quillbot(v):
                raise ApiError(400, "the QuillBot address must be an https address on quillbot.com")
            cur["quillbot_url"] = v or None
        for key in ("builtin_quillbot", "builtin_overleaf"):
            if key in ui:
                cur[key] = bool(ui[key])
        if "pane_mode" in ui:
            if ui["pane_mode"] not in PANE_MODES:
                raise ApiError(400, "pane_mode must be auto, browser, window or inside")
            cur["pane_mode"] = ui["pane_mode"]
        for key, lo, hi in (("guard_passages", 1, 100), ("max_passages", 1, 100)):
            if key in ui:
                try:
                    cur[key] = max(lo, min(int(ui[key]), hi))
                except (TypeError, ValueError):
                    raise ApiError(400, f"{key} must be a number") from None
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


def api_preferences(r):
    n, p = paper_ref(r.m.group(1))
    return {"digest": preferences.digest(p), **preferences.stats(p)}


def api_forget(r):
    n, p = paper_ref(r.m.group(1))
    return {"forgotten": preferences.forget(p)}


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


def _bib_push(n, p, message):
    """Commit and push only the bibliography (like the phone bot does). -> {ok, text, friendly?}"""
    ok, text = doi_bot.git_sync(common.bib_path(p), message)
    out = {"ok": ok, "text": text}
    if not ok and any(m in text.lower() for m in _AUTH_MARKERS):
        have = (config.data_dir() / "overleaf_git.json").exists()
        out["friendly"] = "Overleaf did not accept the sign-in. Check the token in Settings." if have else "This app is not signed in to Overleaf yet. In Settings, paste your Overleaf Git token once."
    elif not ok and "rejected" in text.lower():
        out["friendly"] = "Overleaf has newer changes. Press \"Get latest from Overleaf\", then send the bibliography again."
    return out


def add_references(n, p, dois, source="web", mirror="auto", push=None, progress=None):
    """Every DOI through the one shared function, then one bibliography-only push. -> {results, messages, push}"""
    steps = revise.Steps(progress)
    bib = common.bib_path(p)
    results, msgs = [], []
    for i, d in enumerate(dois, 1):
        steps.start(f"doi{i}", f"Adding {d}")
        res = doi_bot.add_reference(d, bib, source=source, mirror=mirror)
        results.append(res)
        msgs.append(res["message"])
        mend = res.get("mendeley") or {}
        detail = {"added": "added" if res["status"] == "added" else res["status"], "duplicate": "already in your bibliography", "error": "could not be found", "invalid": "not a DOI"}.get(res["status"], "")
        if res["status"] == "added":
            detail = f"{res['cite']}; " + {"added": f"Mendeley group “{mend.get('group')}” has it", "failed": "Mendeley will be tried again", "not_linked": "not linked to Mendeley", "off": "Mendeley is switched off"}.get(mend.get("state"), "")
        steps.finish(f"doi{i}", detail, state="done" if res["status"] in ("added", "duplicate") else "failed")
    added = [r for r in results if r["status"] == "added"]
    out_push = None
    if added:
        paper_index.refresh(n, p, trigger="after_edit", source=source)
        if push is None:
            push = config.paper_settings(p).get("send_refs_to_overleaf", True)
        if push:
            steps.start("push", "Sending the bibliography to Overleaf")
            out_push = _bib_push(n, p, "Add references (web app)")
            steps.finish("push", state="done" if out_push["ok"] else "failed", detail=out_push.get("friendly") or ("sent" if out_push["ok"] else out_push["text"][:120]))
    return {"results": results, "messages": msgs, "push": out_push, "steps": steps.items}


def api_refs_add(r):
    n, p = paper_ref(r.body.get("paper", ""))
    dois = [d for d in (r.body.get("dois") or []) if isinstance(d, str)][:20]
    if not dois:
        raise ApiError(400, "no DOIs to add")
    common.set_active(n, "ui")
    mirror = None if r.body.get("mendeley") is False else "auto"
    out = add_references(n, p, dois, mirror=mirror, push=r.body.get("push"))
    out.pop("steps", None)
    out["pending"] = len(doi_bot.pending_list(p))
    return out


def api_refs_pending(r):
    n, p = paper_ref(r.arg("paper", ""))
    items = doi_bot.pending_list(p)
    return {"pending": [{"doi": i["doi"], "key": i.get("key"), "title": i.get("title"), "group": i.get("group"), "since": i.get("ts")} for i in items]}


def api_refs_retry(r):
    n, p = paper_ref(r.body.get("paper", ""))
    return doi_bot.retry_pending(common.bib_path(p))


def api_refs_groups(r):
    """The Mendeley groups of the signed-in account, so a paper can be linked to one."""
    out = overleaf_mcp.tool_mendeley_list_groups({})
    groups = []
    for ln in out.splitlines():
        m = re.match(r"^([0-9a-f-]{8,})\s+(.+)$", ln.strip())
        if m:
            groups.append({"id": m.group(1), "name": m.group(2).strip()})
    if not groups:
        raise ApiError(409, "I could not list your Mendeley groups. " + ("Connect Mendeley first (Settings)." if "credentials" in out.lower() or "tokens" in out.lower() or "authorize" in out.lower() else out[:200]))
    return {"groups": groups}


def api_refs_link(r):
    n, p = paper_ref(r.body.get("paper", ""))
    gid, name = str(r.body.get("group_id", ""))[:80], str(r.body.get("group_name", ""))[:120]
    if not gid or not name:
        raise ApiError(400, "choose a group")
    common.register(p["path"], n, group={"id": gid, "name": name})
    return {"ok": True, "group": name}


def api_activity(r):
    paper = r.arg("paper") or None
    days = max(1, min(int(r.arg("days", 30)), 365))
    waiting = None
    if paper:
        n, p = paper_ref(paper)
        waiting = activity.waiting(n, p)
    return {"paper": paper, "days": days, "summary": activity.summary(paper=paper, days=days), "items": activity.timeline(paper=paper, days=days), "waiting": waiting}


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


# ---------------------------------------------------------------------------- the LaTeX workspace: the paper's source files and the PDF made from them, on this computer

_LATEX_LOCK = threading.Lock()


def _tex_paper(r):
    return paper_ref(r.arg("paper", "") or (r.body or {}).get("paper", ""))


def _latex(fn, *a, **k):
    try:
        return fn(*a, **k)
    except latexbuild.LatexProblem as e:
        raise ApiError(409 if e.code in ("stale", "unavailable") else 400, str(e), code=e.code) from None


def api_latex_status(r):
    n, p = _tex_paper(r)
    ok, why = latexbuild.available()
    out = {"available": ok, "why": why, "tools": {k: bool(v) for k, v in latexbuild.tools().items()}, "main": None, "files": latexbuild.list_files(p), "changed": latexbuild.changed(p),
           "last": latexbuild.last(p) if ok else None}
    run = r.app.jobs.running(latex=n)
    out["job"] = run[0]["id"] if run else None
    try:
        out["main"] = latexbuild.main_file(p)
    except latexbuild.LatexProblem as e:
        out["no_main"] = str(e)
    return out


def api_latex_file(r):
    n, p = _tex_paper(r)
    rel = r.arg("file", "")
    out = _latex(latexbuild.read_file, p, rel)
    out["ranges"] = latexbuild.changed(p).get(out["file"], [])
    return out


def api_latex_save(r):
    """The author's own edit of a source file. Refused (and nothing written) when the file changed on disk since it was opened."""
    n, p = _tex_paper(r)
    rel, text, base = str(r.body.get("file", "")), r.body.get("text"), str(r.body.get("base") or "")
    out = _latex(latexbuild.write_file, p, rel, text, base)
    try:
        paper_index.refresh(n, p, trigger="after_edit", source="web")          # the chat finds the new text; cheap, only what changed is read again
    except Exception:  # noqa: BLE001  (a bonus: the file is already saved)
        pass
    out["ranges"] = latexbuild.changed(p).get(rel, [])
    return out


def api_latex_compile(r):
    """Compile in the background; asking again while it runs returns the same job, so a burst of saves makes one compile at a time."""
    n, p = _tex_paper(r)
    with _LATEX_LOCK:
        run = r.app.jobs.running(latex=n)
        if run:
            return {"job": run[0]["id"], "already": True}
        ok, why = latexbuild.available()
        if not ok:
            raise ApiError(409, why, code="unavailable")
        return {"job": r.app.jobs.submit(f"Compiling {n}", lambda: latexbuild.compile(p), meta={"latex": n})}


def api_latex_forward(r):
    n, p = _tex_paper(r)
    line = r.body.get("line")
    if not isinstance(line, int) or line < 1:
        raise ApiError(400, "line must be a number")
    return _latex(latexbuild.forward, p, str(r.body.get("file", "")), line)


def api_latex_inverse(r):
    n, p = _tex_paper(r)
    try:
        page, x, y = int(r.body.get("page")), float(r.body.get("x")), float(r.body.get("y"))
    except (TypeError, ValueError):
        raise ApiError(400, "page, x and y must be numbers") from None
    return _latex(latexbuild.inverse, p, page, x, y)


ROUTES = [
    ("GET", r"^/api/latex/status$", api_latex_status), ("GET", r"^/api/latex/file$", api_latex_file), ("POST", r"^/api/latex/save$", api_latex_save),
    ("POST", r"^/api/latex/compile$", api_latex_compile), ("POST", r"^/api/latex/forward$", api_latex_forward), ("POST", r"^/api/latex/inverse$", api_latex_inverse),
    ("GET", r"^/api/state$", api_state), ("POST", r"^/api/papers/active$", api_set_active),
    ("POST", r"^/api/papers/(pull|status|sync)$", None), ("POST", r"^/api/papers/resolve$", api_resolve), ("POST", r"^/api/papers/conflict/handoff$", api_conflict_handoff),
    ("GET", r"^/api/paper/([^/]+)/outline$", api_paper_outline), ("POST", r"^/api/paper/([^/]+)/refresh$", api_paper_refresh),
    ("GET", r"^/api/paper/([^/]+)/chunks$", api_paper_chunks),
    ("GET", r"^/api/books$", api_books), ("GET", r"^/api/book/([A-Za-z0-9_-]+)/tree$", api_book_tree),
    ("GET", r"^/api/book/([A-Za-z0-9_-]+)/chunk$", api_book_chunk), ("GET", r"^/api/book/([A-Za-z0-9_-]+)/query$", api_book_query),
    ("GET", r"^/api/book/([A-Za-z0-9_-]+)/outline$", api_book_outline_get), ("POST", r"^/api/book/([A-Za-z0-9_-]+)/outline$", api_book_outline_save),
    ("GET", r"^/api/book/([A-Za-z0-9_-]+)/check$", api_book_check), ("GET", r"^/api/book/([A-Za-z0-9_-]+)/rules$", api_rules_status), ("POST", r"^/api/book/([A-Za-z0-9_-]+)/rules/preview$", api_rules_preview),
    ("POST", r"^/api/book/([A-Za-z0-9_-]+)/rules/run$", api_rules_run), ("POST", r"^/api/book/ingest$", api_ingest), ("GET", r"^/api/jobs/([0-9a-f]{12})$", api_job),
    ("POST", r"^/api/revise/preview$", api_revise_preview), ("POST", r"^/api/revise/run$", api_revise_run),
    ("GET", r"^/api/proposals$", api_proposals), ("GET", r"^/api/proposal$", api_proposal),
    ("POST", r"^/api/proposal/apply$", api_apply), ("POST", r"^/api/proposal/record$", api_record),
    ("GET", r"^/api/monitor$", api_monitor), ("GET", r"^/api/events$", api_events),
    ("GET", r"^/api/files$", api_files), ("GET", r"^/api/files/parts$", api_files_parts), ("POST", r"^/api/files/upload$", api_files_upload), ("POST", r"^/api/book/upload$", api_book_upload),
    ("POST", r"^/api/files/remove$", api_files_remove), ("POST", r"^/api/files/folders$", api_files_folders),
    ("GET", r"^/api/health$", api_health), ("POST", r"^/api/settings$", api_settings),
    ("GET", r"^/api/paper/([^/]+)/card$", api_card_get), ("POST", r"^/api/paper/([^/]+)/card$", api_card_save),
    ("GET", r"^/api/paper/([^/]+)/decisions$", api_decisions), ("GET", r"^/api/paper/([^/]+)/index$", api_index_list),
    ("GET", r"^/api/paper/([^/]+)/chunk$", api_index_chunk), ("GET", r"^/api/paper/([^/]+)/preferences$", api_preferences),
    ("POST", r"^/api/paper/([^/]+)/forget$", api_forget),
    ("POST", r"^/api/refs/preview$", api_refs_preview), ("POST", r"^/api/refs/add$", api_refs_add), ("GET", r"^/api/refs/recent$", api_refs_recent),
    ("POST", r"^/api/refs/mendeley$", api_refs_mendeley), ("POST", r"^/api/proposal/handoff$", api_handoff),
    ("GET", r"^/api/refs/pending$", api_refs_pending), ("POST", r"^/api/refs/retry$", api_refs_retry), ("GET", r"^/api/refs/groups$", api_refs_groups),
    ("POST", r"^/api/refs/link$", api_refs_link), ("GET", r"^/api/activity$", api_activity),
    ("POST", r"^/api/secrets/openai$", api_secret_openai), ("POST", r"^/api/secrets/git$", api_secret_git), ("POST", r"^/api/secrets/site$", api_secret_site),
    ("GET", r"^/api/browser$", api_browser_status), ("POST", r"^/api/browser/(quillbot|overleaf)/open$", api_browser_open),
    ("POST", r"^/api/browser/(quillbot|overleaf)/resize$", api_browser_resize), ("POST", r"^/api/browser/(quillbot|overleaf)/input$", api_browser_input),
    ("POST", r"^/api/browser/(quillbot|overleaf)/nav$", api_browser_nav), ("POST", r"^/api/browser/(quillbot|overleaf)/selection$", api_browser_selection),
    ("POST", r"^/api/browser/(quillbot|overleaf)/clipboard$", api_browser_clipboard),
    ("POST", r"^/api/browser/(quillbot|overleaf)/insert$", api_browser_insert), ("POST", r"^/api/browser/(quillbot|overleaf)/signin$", api_browser_signin),
    ("POST", r"^/api/browser/(quillbot|overleaf)/close$", api_browser_close),
    ("GET", r"^/api/panes$", api_panes), ("POST", r"^/api/test/frames$", api_test_frames),
    ("GET", r"^/api/library$", api_library), ("POST", r"^/api/library/folders$", api_library_folders), ("POST", r"^/api/library/index$", api_library_index),
    ("GET", r"^/api/library/parts$", api_library_parts), ("GET", r"^/api/library/search$", api_library_search), ("POST", r"^/api/library/cite$", api_library_cite),
    ("GET", r"^/api/overview$", api_overview), ("GET", r"^/api/connections$", api_connections), ("POST", r"^/api/test/openai$", api_test_openai),
    ("POST", r"^/api/test/git$", api_test_git), ("POST", r"^/api/papers/clean-address$", api_clean_address), ("POST", r"^/api/papers/clone$", api_clone_overleaf), ("POST", r"^/api/test/cleaner$", api_test_cleaner),
    ("GET", r"^/api/papers/discover$", api_discover), ("POST", r"^/api/papers/add$", api_add_paper),
    ("GET", r"^/api/chat/threads$", api_chat_threads), ("POST", r"^/api/chat/new$", api_chat_new), ("POST", r"^/api/chat/rename$", api_chat_rename),
    ("POST", r"^/api/chat/delete$", api_chat_delete), ("POST", r"^/api/chat/clear$", api_chat_clear), ("POST", r"^/api/chat/issue$", api_chat_issue), ("GET", r"^/api/chat$", api_chat_get), ("POST", r"^/api/chat/send$", api_chat_send), ("POST", r"^/api/chat/understood$", api_chat_understood),
    ("POST", r"^/api/chat/run$", api_chat_run), ("POST", r"^/api/chat/refs/pick$", api_chat_refs_pick), ("POST", r"^/api/chat/count$", api_chat_count), ("POST", r"^/api/chat/cancel$", api_chat_cancel),
    ("POST", r"^/api/proposal/check$", api_proposal_check), ("POST", r"^/api/proposal/approve$", api_proposal_approve),
    ("POST", r"^/api/proposal/text$", api_proposal_text), ("POST", r"^/api/papers/catchup$", api_catchup),
    ("GET", r"^/api/paper/([^/]+)/sections$", api_sections), ("GET", r"^/api/paper/([^/]+)/text$", api_paper_text),
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
        path = urlparse(self.path).path
        if str(code) in ("200", "204") and (self.headers.get("X-Poll") or QUIET.match(path)):
            return                                   # pictures of the built-in browser and polling answer many times a second: a line each would only fill the log
        sys.stderr.write("%s %s %s -> %s\n" % (time.strftime("%H:%M:%S"), self.command, path, code))

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), (fmt % args if args else fmt).split("?")[0]))

    def _send(self, status, body=b"", ctype="application/json", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (headers or {}):
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
            if path == "/api/book/upload":                                   # a raw PDF, not JSON: written to disk as it arrives
                if n <= 0:
                    return self._err(400, "The file is empty.")
                if n > BOOK_MAX:
                    return self._err(413, f"That PDF is over the {BOOK_MAX >> 20} MB limit.")
                inbox = config.inbox_dir()
                inbox.mkdir(parents=True, exist_ok=True)
                tmp = inbox / f".upload-{secrets.token_hex(6)}.part"
                left = n
                try:
                    with open(tmp, "wb") as fh:
                        while left:
                            chunk = self.rfile.read(min(1 << 20, left))
                            if not chunk:
                                break
                            fh.write(chunk)
                            left -= len(chunk)
                except OSError as e:
                    tmp.unlink(missing_ok=True)
                    return self._err(500, f"I could not save the file ({e.__class__.__name__}).")
                if left:
                    tmp.unlink(missing_ok=True)
                    return self._err(400, "The upload was cut off. Try again.")
                body = {"_tmp": str(tmp), "_size": n}
                n = 0
            if n > (UPLOAD_BODY if path == "/api/files/upload" else LATEX_BODY if path == "/api/latex/save" else MAX_BODY):
                return self._err(413, "request too large" if path != "/api/files/upload" else f"That file is over the {files.MAX_UPLOAD >> 20} MB limit.")
            if path != "/api/book/upload":
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                    if not isinstance(body, dict):
                        raise ValueError
                except ValueError:
                    return self._err(400, "body must be a JSON object")
        bm = BROWSER_GET.match(path) if method == "GET" else None
        if bm:
            return self._browser_picture(bm.group(1), query)
        lm = LATEX_GET.match(path) if method == "GET" else None
        if lm:
            return self._latex_file(lm.group(1), query)
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

    def _browser_picture(self, site, query):
        """The newest picture of a built-in browser as a JPEG. ?after=<number from the last picture>&wait=<seconds> waits (up to that long) for a
        different picture and answers 204 when nothing changed, so the page can keep asking and always show what the browser shows."""
        if self.command == "HEAD":
            return self._err(405, "method not allowed")
        try:
            after = int(query.get("after", ["-1"])[0])
            wait = max(0.1, min(float(query.get("wait", ["0.1"])[0]), 20.0))
        except ValueError:
            return self._err(400, "bad number")
        try:
            seq, jpeg = self.app.browser().next_frame(site, after, wait)
        except webview.BrowserError as e:
            return self._err(409, str(e))
        return self._send(204, b"", "text/plain") if jpeg is None else self._send(200, jpeg, "image/jpeg", {"X-Seq": str(seq)})

    def _latex_file(self, kind, query):
        """A page of the compiled PDF as a picture, or the PDF itself to keep. The page address carries the PDF's id, so a picture is never mixed up with a newer compile
        and the browser may keep it."""
        if self.command == "HEAD":
            return self._err(405, "method not allowed")
        try:
            n, p = paper_ref(query.get("paper", [""])[0])
            if kind == "pdf":
                if not latexbuild.info(p).get("pdf"):
                    return self._err(404, "There is no PDF yet. Compile first.")
                name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(latexbuild.main_file(p)).stem) + ".pdf"
                return self._send(200, latexbuild.pdf_path(p).read_bytes(), "application/pdf", {"Content-Disposition": f'attachment; filename="{name}"'})
            want = query.get("id", [""])[0]
            if want and latexbuild.info(p).get("id") != want:
                return self._err(409, "That version of the PDF is out of date.")
            img = latexbuild.page_image(p, int(query.get("n", ["1"])[0]), int(query.get("dpi", ["110"])[0]))
            return self._send(200, img.read_bytes(), "image/png", {"Cache-Control": "private, max-age=86400, immutable"})
        except ApiError as e:
            return self._err(e.status, str(e))
        except latexbuild.LatexProblem as e:
            return self._err(400, str(e), code=e.code)
        except (ValueError, OSError) as e:
            return self._err(400, f"I could not draw that page ({e.__class__.__name__}).")

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
