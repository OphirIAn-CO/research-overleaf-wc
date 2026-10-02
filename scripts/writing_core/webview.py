"""The built-in browser: QuillBot and Overleaf shown inside the app.

Both sites refuse to be framed by another page (X-Frame-Options / frame-ancestors) and Safari blocks the cookies of framed pages anyway, so
the app cannot simply embed them. Instead a real Chromium runs next to the app, one profile per site (so a sign-in lasts), and the app shows
what it sees as a live picture and forwards the mouse and keyboard. To the author it is a browser tab inside the app.

Two ways to run it, behind the same small interface (`status`, `open`, `resize`, `input`, `nav`, `selection`, `insert`, `signin`,
`next_frame`, `close`):
  * `Browsers`       Chromium started here, through Playwright (outside Docker, and inside the separate browser container).
  * `RemoteBackend`  talks to `ui/browser_service.py` in that separate container over a private network. The web app itself never runs
                     third-party pages: a browser bug cannot reach the OpenAI key, the Overleaf token or the papers.

Rules kept here: only the QuillBot / Overleaf start addresses can be opened by the app; a page cannot take the browser to a private or
local address (top-level navigations are checked); downloads are refused; the saved sign-in details are used only when asked, are filled into
the form and are never logged or returned.
"""
import http.client
import ipaddress
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.parse
from concurrent.futures import Future, TimeoutError as FutureTimeout
from pathlib import Path

from . import panes

SITES = ("quillbot", "overleaf")
DESKTOP_W = 1100            # sites switch to their cramped phone layout below this width, so the page is drawn at least this wide
MIN_W, MAX_W, MIN_H, MAX_H = 640, 1920, 360, 1400
IDLE_CLOSE = int(os.environ.get("OLS_BROWSER_IDLE", 30 * 60))   # seconds without anyone looking before Chromium is closed (the sign-in stays on disk)
NAV_TIMEOUT_MS = 20000      # how long opening a page may take before the pane says so (the page keeps loading unless stopped)
STUCK_AFTER = int(os.environ.get("OLS_BROWSER_STUCK", 75))   # seconds without the worker finishing a round before the browser is declared stuck and killed
MAX_EVENTS, MAX_TEXT = 200, 100000
BUTTONS = {0: "left", 1: "middle", 2: "right"}
MODS = ("Shift", "Control", "Alt", "Meta")
NAMED_KEYS = {"Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Insert",
              *[f"F{i}" for i in range(1, 13)]}
NAV_SCHEMES = ("http", "https", "about", "blob", "data", "chrome-error", "chrome", "chrome-untrusted", "chrome-search")   # the last three are the browser's own pages


class BrowserError(Exception):
    """A problem the author can be told about in plain words."""


def available():
    """-> (ok, reason). Whether Playwright is installed here."""
    if os.environ.get("OLS_BROWSER", "").lower() == "off":
        return False, "The built-in browser is turned off (OLS_BROWSER=off)."
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False, "The built-in browser is not installed in this copy of the app. Run ./ui.sh build to add it."
    return True, ""


def public_url(url):
    """True for an address the built-in browser may open: http(s)/about/blob/data, and a public host (never a local or private one)."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if u.scheme not in NAV_SCHEMES:
        return False
    if u.scheme in ("about", "blob", "data", "chrome-error", "chrome", "chrome-untrusted", "chrome-search"):
        return True
    host = (u.hostname or "").lower()
    if not host or "." not in host and ":" not in host:     # single-label names are internal (docker service names, "localhost")
        return False
    if host.endswith((".local", ".internal", ".localhost", ".lan", ".home.arpa")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return ip.is_global


def _chrome_ua(exe):
    """A normal desktop Chrome user agent for the engine that is installed (headless builds announce themselves as 'HeadlessChrome', which many sites refuse)."""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
        major = re.search(r"(\d+)\.\d+\.\d+\.\d+", out).group(1)
    except Exception:  # noqa: BLE001
        return None
    return f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(int(v), hi))
    except (TypeError, ValueError):
        return default


def _site_key(host):
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 and not parts[-1].isdigit() else (host or "").lower()


class _Session:
    """One site's browser: a worker thread owns every Playwright object (the sync API must be used from one thread)."""

    def __init__(self, site, profile, exe=None, allow_private=False):
        self.site, self.profile, self.exe, self.allow_private = site, Path(profile), exe, allow_private
        self.cmds = queue.Queue()
        self.ready = threading.Event()
        self.error = None
        self.cond = threading.Condition()
        self.seq, self.jpeg, self._hash = 0, b"", None
        self.info = {"url": "", "title": "", "notice": "", "challenge": False}
        self._challenge_at = 0.0
        self.size = (DESKTOP_W, 720)
        self.alive = False
        self.last_seen = time.time()
        self.watch_until = 0.0
        self.last_input = 0.0
        self.thread = None
        self._stop = False
        self._violation = None
        self._held = set()
        self.page = None
        self.pages = []
        self.cdp = {}
        self.tick = time.time()
        self._current = None

    # ------------------------------------------------------------------ used by other threads
    def start(self):
        self._stop, self.error, self.alive = False, None, True
        self.ready.clear()
        self.last_seen = time.time()
        self.tick = time.time()
        self.thread = threading.Thread(target=self._main, name=f"browser-{self.site}", daemon=True)
        self.thread.start()
        threading.Thread(target=self._watchdog, name=f"browser-watch-{self.site}", daemon=True).start()

    def _watchdog(self):
        """If the worker has not come round for STUCK_AFTER seconds the browser is not answering: kill it, so the pane can say so and start again."""
        thread = self.thread
        while self.alive and self.thread is thread:
            time.sleep(1)
            if self.alive and time.time() - self.tick > STUCK_AFTER:
                self.error = "The built-in browser stopped responding and was closed. Open it again; your sign-in is kept."
                self.alive = False
                kill_browser(self.profile)
                for fut in [self._current] + _drain(self.cmds):
                    if fut is not None and not fut.done():
                        fut.set_exception(BrowserError(self.error))
                with self.cond:
                    self.cond.notify_all()
                return

    def running(self):
        return self.alive and self.thread is not None and self.thread.is_alive()

    def touch(self):
        self.last_seen = time.time()

    def watch(self):
        """Somebody is looking: keep taking pictures for a few more seconds."""
        self.last_seen = time.time()
        self.watch_until = self.last_seen + 6

    def call(self, name, *args, timeout=30):
        if not self.running():
            raise BrowserError(self.error or "The built-in browser is not running.")
        self.touch()
        fut = Future()
        self.cmds.put((name, args, fut))
        try:
            return fut.result(timeout)
        except FutureTimeout:
            raise BrowserError("The built-in browser did not answer in time. Try again.") from None

    def frame_after(self, after, timeout):
        self.watch()
        with self.cond:
            if self.seq == after or not self.jpeg:
                self.cond.wait(timeout)
            self.watch()
            if self.jpeg and self.seq != after:
                return self.seq, self.jpeg
            return after, None

    # ------------------------------------------------------------------ the worker
    def _main(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.error, self.alive = available()[1], False
            self.ready.set()
            return
        try:
            with sync_playwright() as pw:
                self._run(pw)
        except Exception as e:  # noqa: BLE001
            self.error = _launch_message(e) if not self.ready.is_set() else "The built-in browser stopped unexpectedly."
            self.alive = False
        finally:
            self.alive = False
            self.ready.set()
            # anything still waiting on a command gets an answer
            while True:
                try:
                    _, _, fut = self.cmds.get_nowait()
                except queue.Empty:
                    break
                fut.set_exception(BrowserError("The built-in browser was closed."))

    def _run(self, pw):
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):   # left behind when a container was stopped; this app is the only user of the profile
            try:
                (self.profile / name).unlink()
            except OSError:
                pass
        w, h = self.size
        exe = self.exe or os.environ.get("OLS_BROWSER_PATH") or None
        # The person at the keyboard is the one using this browser; Playwright is only the cable between the picture and the page. Left alone the browser
        # would tell every site "navigator.webdriver = true" (an automated robot), which sign-in pages and human checks react to, so that one flag is off.
        kw = dict(user_data_dir=str(self.profile), headless=True, viewport={"width": w, "height": h}, locale="en-US", accept_downloads=False,
                  permissions=["clipboard-read", "clipboard-write"],   # so a site's own "Copy" button can be read back (see clipboard())
                  args=["--disable-gpu", "--disk-cache-size=52428800", "--no-first-run", "--no-default-browser-check", "--disable-features=Translate,MediaRouter",
                        "--disable-blink-features=AutomationControlled"])
        tz = os.environ.get("OLS_BROWSER_TZ", "")
        if re.fullmatch(r"[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}", tz):
            kw["timezone_id"] = tz        # the time zone of the computer, so the browser does not claim to be elsewhere than the connection it uses
        if exe:
            kw["executable_path"] = exe
        else:
            kw["channel"] = "chromium"      # the full Chromium in its newer headless mode, not the stripped-down headless shell
        try:
            ua = _chrome_ua(exe or pw.chromium.executable_path)
        except Exception:  # noqa: BLE001
            ua = None
        if ua:
            kw["user_agent"] = ua
        try:
            self.ctx = pw.chromium.launch_persistent_context(**kw)
        except Exception as e:  # noqa: BLE001
            if "timezone_id" not in kw or "imezone" not in str(e):
                raise
            kw.pop("timezone_id")         # an unknown time zone name must not stop the browser from starting
            self.ctx = pw.chromium.launch_persistent_context(**kw)
        self.ctx.set_default_timeout(15000)
        self.ctx.on("page", self._adopt)
        for p in list(self.ctx.pages):
            self._adopt(p)
        if not self.page:
            self._adopt(self.ctx.new_page())
        self.ready.set()
        last_cap = last_info = 0.0
        try:
            while not self._stop and self.alive:
                did = False
                self.tick = time.time()
                for _ in range(40):
                    try:
                        name, args, fut = self.cmds.get_nowait()
                    except queue.Empty:
                        break
                    did = True
                    self._current = fut
                    try:
                        result = getattr(self, "_do_" + name)(*args)
                        if not fut.done():
                            fut.set_result(result)
                    except BrowserError as e:
                        if not fut.done():
                            fut.set_exception(e)
                    except Exception as e:  # noqa: BLE001
                        if not fut.done():
                            fut.set_exception(BrowserError(_short(e)))
                    self._current = None
                    self.tick = time.time()
                now = time.time()
                if self._stop or now - self.last_seen > IDLE_CLOSE:
                    break
                if self._violation:
                    self._leave(*self._violation)
                    self._violation = None
                watched = now < self.watch_until
                if watched or did:
                    gap = 0.06 if now - self.last_input < 2.0 else 0.25
                    if did or now - last_cap >= gap:
                        self._capture()
                        last_cap = time.time()
                if (watched or did) and now - last_info >= 0.5:
                    self._refresh_info()
                    last_info = now
                self._pump(12 if (watched or did) else 80)
        finally:
            try:
                self.ctx.close()
            except Exception:  # noqa: BLE001
                pass

    def _pump(self, ms):
        """Lets Playwright deliver events (new pages, dialogs, navigations) for `ms` milliseconds."""
        for _ in range(2):
            try:
                self.page.wait_for_timeout(ms)
                return
            except Exception:  # noqa: BLE001
                self._pick_page()
        time.sleep(ms / 1000)

    def _adopt(self, page):
        if page in self.pages:
            return
        self.pages.append(page)
        self.page = page
        try:
            page.set_viewport_size({"width": self.size[0], "height": self.size[1]})
            self.cdp[page] = self.ctx.new_cdp_session(page)
        except Exception:  # noqa: BLE001
            pass
        page.on("dialog", lambda d: d.accept() if d.type in ("alert", "beforeunload") else d.dismiss())
        page.on("close", lambda _p=page: self._closed(_p))
        page.on("framenavigated", lambda f, _p=page: self._navigated(_p, f))

    def _closed(self, page):
        self.cdp.pop(page, None)
        if page in self.pages:
            self.pages.remove(page)
        if self.page is page:
            self._pick_page()

    def _pick_page(self):
        live = [p for p in self.pages if not p.is_closed()]
        self.pages = live
        if live:
            self.page = live[-1]
        elif not self._stop:
            try:
                self._adopt(self.ctx.new_page())
            except Exception:  # noqa: BLE001
                self._stop = True

    def _navigated(self, page, frame):
        try:
            if frame != page.main_frame:
                return
            url = frame.url
        except Exception:  # noqa: BLE001
            return
        if not self.allow_private and not public_url(url):
            self._violation = (page, url)

    def _leave(self, page, url):
        self.info["notice"] = "The built-in browser only opens public websites, so it did not go to " + (urllib.parse.urlsplit(url).hostname or "that address") + "."
        try:
            if page is self.page and len(self.pages) <= 1:
                page.goto("about:blank")
            else:
                page.close()
        except Exception:  # noqa: BLE001
            pass

    def _capture(self):
        page = self.page
        try:
            jpeg = page.screenshot(type="jpeg", quality=70, caret="initial", timeout=4000)
        except Exception:  # noqa: BLE001
            return
        digest = hash(jpeg)
        if digest == self._hash:
            return
        self._hash = digest
        with self.cond:
            self.seq += 1
            self.jpeg = jpeg
            self.cond.notify_all()

    def _refresh_info(self):
        try:
            self.info.update(url=self.page.url, title=self.page.title()[:120])
        except Exception:  # noqa: BLE001
            return
        if time.time() - self._challenge_at > 2.0:       # is the site asking "verify you are human"? asked every couple of seconds, not every picture
            self._challenge_at = time.time()
            try:
                self.info["challenge"] = bool(self._js(CHALLENGE_JS, timeout_ms=1500))
            except Exception:  # noqa: BLE001
                pass

    def _send(self, method, params=None):
        c = self.cdp.get(self.page)
        if c is None:
            raise BrowserError("The page is not ready yet. Try again in a moment.")
        return c.send(method, params or {})

    def _js(self, fn_source, arg=None, timeout_ms=4000):
        """Runs `fn_source` (a JavaScript function) on the page through the browser's debugging channel and returns its value. Unlike page.evaluate this
        answers (with None) even when the page is stuck loading, so one dead site cannot freeze the pane."""
        expr = ("Promise.race([(async () => (%s)(%s))(), new Promise((r) => setTimeout(() => r(null), %d))])" % (fn_source, json.dumps(arg), timeout_ms))
        try:
            r = self._send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True, "userGesture": True, "timeout": timeout_ms})
        except BrowserError:
            raise
        except Exception:  # noqa: BLE001
            return None
        return None if r.get("exceptionDetails") else (r.get("result") or {}).get("value")

    def _stop_loading(self):
        try:
            self._send("Page.stopLoading")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ commands (run on the worker thread)
    def _do_open(self, url, w, h, force):
        self._do_resize(w, h)
        cur = self.page.url or ""
        same = bool(cur) and cur != "about:blank" and _site_key(urllib.parse.urlsplit(cur).hostname) == _site_key(urllib.parse.urlsplit(url).hostname)
        self.info["notice"] = ""
        if force or not same:
            self.info["challenge"] = False        # a new page: what the old one asked no longer holds
            self._challenge_at = 0.0
            try:
                self.page.goto(url, wait_until="commit", timeout=NAV_TIMEOUT_MS)
            except Exception as e:  # noqa: BLE001
                self.info["notice"] = "That page did not answer in time. Check your internet connection, then press Reload."
                self._stop_loading()      # a load that never starts would otherwise block everything that needs the page
        self._refresh_info()
        return self.state()

    def _do_resize(self, w, h):
        w, h = _clamp(w, MIN_W, MAX_W, self.size[0]), _clamp(h, MIN_H, MAX_H, self.size[1])
        if (w, h) != self.size:
            self.size = (w, h)
            for p in self.pages:
                try:
                    p.set_viewport_size({"width": w, "height": h})
                except Exception:  # noqa: BLE001
                    pass
            self._hash = None
        return {"w": w, "h": h}

    def _release(self):
        for m in list(self._held):
            self.page.keyboard.up(m)
        self._held.clear()

    def _mods(self, want):
        want = {m for m in (want or []) if m in MODS}
        for m in list(self._held - want):
            self.page.keyboard.up(m)
            self._held.discard(m)
        for m in MODS:
            if m in want and m not in self._held:
                self.page.keyboard.down(m)
                self._held.add(m)

    def _do_input(self, events):
        page = self.page
        w, h = self.size
        self.last_input = time.time()
        for e in events[:MAX_EVENTS]:
            if not isinstance(e, dict):
                continue
            t = e.get("t")
            x, y = _clamp(e.get("x"), 0, w, 0), _clamp(e.get("y"), 0, h, 0)
            if t in ("move", "down", "up", "wheel"):
                self._mods(e.get("mods"))
            if t == "move":
                page.mouse.move(x, y)
            elif t == "down":
                page.mouse.move(x, y)
                page.mouse.down(button=BUTTONS.get(e.get("b"), "left"), click_count=_clamp(e.get("n"), 1, 3, 1))
            elif t == "up":
                page.mouse.move(x, y)
                page.mouse.up(button=BUTTONS.get(e.get("b"), "left"), click_count=_clamp(e.get("n"), 1, 3, 1))
            elif t == "wheel":
                page.mouse.move(x, y)
                page.mouse.wheel(_clamp(e.get("dx"), -2000, 2000, 0), _clamp(e.get("dy"), -2000, 2000, 0))
            elif t == "type":                                 # printable characters, typed one by one so the page sees key events
                self._mods([])
                text = str(e.get("text") or "")[:MAX_TEXT]
                if len(text) <= 8:
                    page.keyboard.type(text)
                else:
                    page.keyboard.insert_text(text)
            elif t == "text":                                 # a paste: inserted as one piece
                self._mods([])
                page.keyboard.insert_text(str(e.get("text") or "")[:MAX_TEXT])
            elif t == "key":
                key = e.get("key")
                if key == " ":
                    key = "Space"
                if key in NAMED_KEYS or key == "Space" or (isinstance(key, str) and len(key) == 1):
                    self._mods(e.get("mods"))
                    page.keyboard.press(key)
        self._release()
        return {"ok": True}

    def _do_nav(self, action):
        page = self.page
        if action == "stop":
            self._stop_loading()
            self.info["notice"] = ""
            self._refresh_info()
            return self.state()
        self.info["challenge"] = False
        self._challenge_at = 0.0
        try:
            {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[action](timeout=15000)
        except KeyError:
            raise BrowserError("unknown action") from None
        except Exception as e:  # noqa: BLE001
            self.info["notice"] = "That page did not answer in time. Check your internet connection, then press Reload."
            self._stop_loading()
        self._refresh_info()
        return self.state()

    def _do_selection(self):
        return str(self._js(SELECTION_JS) or "")[:MAX_TEXT]

    def _do_insert(self, text, focus_main=False):
        self._mods([])
        focused = False
        if focus_main:   # QuillBot: when nothing editable has the cursor, use the biggest writing box on the page and replace what is in it
            focused = bool(self._js(FOCUS_MAIN_JS))
        self.page.keyboard.insert_text(str(text)[:MAX_TEXT])
        return {"ok": True, "focused": focused}

    def _do_clipboard(self):
        return str(self._js("() => navigator.clipboard.readText()") or "")[:MAX_TEXT]

    def _do_signin(self, email, password):
        """Types the saved email and password into the visible sign-in fields of the current page, key by key like a person, checks the page kept them and,
        when it did not, sets them the way a form framework notices. Nothing is submitted: the author presses the site's own button.
        -> {filled: [...], failed: [...]}"""
        filled, failed = [], []
        for kind, value in (("email", email), ("password", password)):
            if not value or not self._js(FOCUS_FIELD_JS, kind):             # focused with its content selected, so typing replaces it
                continue
            self.page.keyboard.type(value, delay=18)
            ok = self._js(KEPT_JS, len(value)) or self._js(SET_FIELD_JS, [kind, value])
            (filled if ok else failed).append(kind)
        return {"filled": filled, "failed": failed}

    def _do_recompile(self):
        """Presses the page's own Recompile button when it is on screen. -> {clicked: bool}"""
        return {"clicked": bool(self._js(RECOMPILE_JS))}

    def _do_close(self):
        self._stop = True
        return {"ok": True}

    def state(self):
        return {"running": self.running(), "url": self.info.get("url", ""), "title": self.info.get("title", ""), "notice": self.info.get("notice", ""),
                "challenge": bool(self.info.get("challenge")), "kind": "scripted", "w": self.size[0], "h": self.size[1], "seq": self.seq}


SELECTION_JS = """() => {
  const a = document.activeElement;
  if (a && (a.tagName === 'TEXTAREA' || (a.tagName === 'INPUT' && /^(text|search|url|tel|email)$/i.test(a.type)))) {
    const s = a.selectionStart, e = a.selectionEnd;
    if (s != null && e != null && e > s) return a.value.slice(s, e);
  }
  return String(window.getSelection() || '');
}"""


FOCUS_MAIN_JS = """() => {
  const editable = (el) => !!el && (el.isContentEditable || el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && /^(text|search)$/i.test(el.type)));
  if (editable(document.activeElement)) return false;
  let best = null, area = 0;
  for (const el of document.querySelectorAll('textarea, [contenteditable=""], [contenteditable="true"], input[type=text]')) {
    const r = el.getBoundingClientRect();
    const a = r.width * r.height;
    if (r.width > 120 && r.height > 24 && a > area && getComputedStyle(el).visibility !== 'hidden') { best = el; area = a; }
  }
  if (!best) return false;
  best.focus();
  if (best.select) best.select();
  else { const range = document.createRange(); range.selectNodeContents(best); const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range); }
  return true;
}"""


FOCUS_FIELD_JS = """(kind) => {
  const shown = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled && !el.readOnly; };
  const sel = kind === 'password' ? 'input[type=password]'
    : 'input[type=email], input[name=email], input[autocomplete=username], input[autocomplete=email], input[name=username], input[id*=email i]';
  const el = Array.from(document.querySelectorAll(sel)).find(shown);
  if (!el) return false;
  el.focus();
  if (el.select) el.select();
  return true;
}"""


RECOMPILE_JS = """() => {
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  for (const el of document.querySelectorAll('button, [role="button"]')) {
    const t = String(el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    if ((t === 'recompile' || t === 'compile') && shown(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true') { el.click(); return true; }
  }
  return false;
}"""

KEPT_JS = """(n) => { const el = document.activeElement; return !!el && el.tagName === 'INPUT' && el.value.length === n; }"""

SET_FIELD_JS = """([kind, value]) => {
  const shown = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled && !el.readOnly; };
  const sel = kind === 'password' ? 'input[type=password]'
    : 'input[type=email], input[name=email], input[autocomplete=username], input[autocomplete=email], input[name=username], input[id*=email i]';
  const el = Array.from(document.querySelectorAll(sel)).find(shown);
  if (!el) return false;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return el.value.length === value.length;
}"""

CHALLENGE_JS = """() => !!document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="recaptcha"][src*="bframe"], iframe[src*="hcaptcha.com"][src*="challenge"], .cf-turnstile, #challenge-form, #cf-challenge-running')
  || /^(just a moment|attention required|one more step)/i.test(document.title)"""


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait()[2])
        except queue.Empty:
            return out


def kill_browser(profile):
    """Stops the Chromium processes that use this profile folder (Linux: the browser container). Used when a browser stops answering."""
    needle = "--user-data-dir=" + str(profile)
    for d in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            if needle in cmd and int(d.name) != os.getpid():
                os.kill(int(d.name), 9)
        except (OSError, ValueError):
            continue


def _short(e):
    return " ".join(str(e).split("\n")[0].split())[:200] or e.__class__.__name__


def _launch_message(e):
    text = _short(e)
    if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
        return "The browser engine is missing from this copy of the app. Run ./ui.sh build to add it."
    return "The built-in browser could not start: " + text


class Browsers:
    """Chromium started here. One profile folder per site under `profile_root`, so signing in once lasts."""
    kind = "local"

    def __init__(self, profile_root, exe=None, allow_private=False, real_sites=()):
        """real_sites: sites shown in an ordinary, unautomated browser on a virtual screen (see xsession.py) instead of a scripted one; used for a site
        whose human check does not let a scripted browser through."""
        self.root = Path(profile_root)
        self.exe, self.allow_private = exe, allow_private
        self.real_sites = tuple(real_sites)
        self.lock = threading.Lock()
        self.sessions = {}

    def _site(self, site):
        if site not in SITES:
            raise BrowserError("unknown site")
        return site

    def _session(self, site):
        """The running browser for a site; a new one when there is none or the old one stopped (a stopped one is never reused)."""
        self._site(site)
        with self.lock:
            s = self.sessions.get(site)
            if s is None or not s.running():
                cls = _Session
                if site in self.real_sites:
                    from . import xsession
                    if xsession.available()[0]:
                        cls = xsession.XSession
                s = self.sessions[site] = cls(site, self.root / site, self.exe, self.allow_private)
                s.start()
            return s

    def _ready(self, s, timeout=60):
        if not s.ready.wait(timeout):
            raise BrowserError("The built-in browser is taking too long to start. Try again in a moment.")
        if not s.running():
            raise BrowserError(s.error or "The built-in browser could not start.")
        return s

    def status(self):
        ok, why = available()
        out = {"available": ok, "reason": why, "kind": self.kind, "sites": {}}
        for site in SITES:
            s = self.sessions.get(site)
            out["sites"][site] = s.state() if s else {"running": False}
            if s and s.error and not s.running():
                out["sites"][site]["error"] = s.error
        return out

    def open(self, site, url, w=None, h=None, force=False):
        if not (self.allow_private or (panes.valid_quillbot(url) if site == "quillbot" else panes.valid_overleaf(url))):
            raise BrowserError("That address is not one the built-in browser may open.")
        s = self._ready(self._session(site))
        return s.call("open", url, w, h, bool(force), timeout=40)

    def resize(self, site, w, h):
        return self._ready(self._session(site)).call("resize", w, h)

    def input(self, site, events):
        if not isinstance(events, list):
            raise BrowserError("events must be a list")
        return self._live(site).call("input", events)

    def nav(self, site, action):
        if action not in ("back", "forward", "reload", "stop"):
            raise BrowserError("unknown action")
        return self._live(site).call("nav", action, timeout=40)

    def selection(self, site):
        return {"text": self._live(site).call("selection")}

    def insert(self, site, text):
        return self._live(site).call("insert", str(text), site == "quillbot")

    def clipboard(self, site):
        return {"text": self._live(site).call("clipboard")}

    def signin(self, site, email="", password=""):
        return self._live(site).call("signin", str(email or ""), str(password or ""))

    def recompile(self, site):
        return self._live(site).call("recompile")

    def state(self, site):
        s = self.sessions.get(self._site(site))
        return s.state() if s else {"running": False}

    def next_frame(self, site, after=-1, timeout=10):
        s = self._live(site)
        return s.frame_after(int(after), max(0.1, min(float(timeout), 30.0)))

    def close(self, site):
        s = self.sessions.get(self._site(site))
        if s and s.running():
            try:
                s.call("close", timeout=10)
            except BrowserError:
                pass
            if s.thread:
                s.thread.join(timeout=10)
        return {"ok": True}

    def close_all(self):
        for site in list(self.sessions):
            self.close(site)

    def _live(self, site):
        s = self.sessions.get(self._site(site))
        if not s or not s.running():
            raise BrowserError("The built-in browser is not open for this site yet.")
        return s


class Unavailable:
    kind = "none"

    def __init__(self, reason):
        self.reason = reason

    def status(self):
        return {"available": False, "reason": self.reason, "kind": self.kind, "sites": {s: {"running": False} for s in SITES}}

    def _no(self, *a, **k):
        raise BrowserError(self.reason)
    open = resize = input = nav = selection = clipboard = insert = signin = recompile = next_frame = close = state = _no


class RemoteBackend:
    """The browser container, reached over the private network with a shared token (see ui/browser_service.py)."""
    kind = "remote"

    def __init__(self, base, token):
        u = urllib.parse.urlsplit(base)
        self.host, self.port, self.token = u.hostname, u.port or 80, token

    def _http(self, method, path, body=None, timeout=45):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            hdr = {"X-Browser-Token": self.token}
            data = None
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                hdr["Content-Type"] = "application/json"
            conn.request(method, path, data, hdr)
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        except (OSError, http.client.HTTPException) as e:
            raise BrowserError("The built-in browser container is not answering. Start the app again with ./ui.sh up. (" + _short(e) + ")") from None
        finally:
            conn.close()

    def _call(self, name, timeout=45, **args):
        status, _, raw = self._http("POST", "/call/" + name, args, timeout)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise BrowserError("The built-in browser container sent an answer I could not read.") from None
        if status != 200 or "error" in data:
            raise BrowserError(str(data.get("error") or "The built-in browser container refused that."))
        return data.get("result")

    def status(self):
        try:
            return self._call("status", timeout=8)
        except BrowserError as e:
            return {"available": False, "reason": str(e), "kind": self.kind, "sites": {s: {"running": False} for s in SITES}}

    def open(self, site, url, w=None, h=None, force=False):
        return self._call("open", site=site, url=url, w=w, h=h, force=force, timeout=70)

    def resize(self, site, w, h):
        return self._call("resize", site=site, w=w, h=h)

    def input(self, site, events):
        return self._call("input", site=site, events=events)

    def nav(self, site, action):
        return self._call("nav", site=site, action=action)

    def selection(self, site):
        return self._call("selection", site=site)

    def insert(self, site, text):
        return self._call("insert", site=site, text=text)

    def clipboard(self, site):
        return self._call("clipboard", site=site)

    def signin(self, site, email="", password=""):
        return self._call("signin", site=site, email=email, password=password)

    def recompile(self, site):
        return self._call("recompile", site=site)

    def state(self, site):
        return self._call("state", site=site)

    def close(self, site):
        return self._call("close", site=site)

    def next_frame(self, site, after=-1, timeout=10):
        status, hdr, raw = self._http("GET", f"/frame/{site}?after={int(after)}&wait={float(timeout):.1f}", None, timeout + 15)
        if status == 204:
            return int(after), None
        if status != 200:
            try:
                msg = json.loads(raw.decode("utf-8")).get("error")
            except ValueError:
                msg = None
            raise BrowserError(str(msg or "The built-in browser is not open for this site yet."))
        seq = next((int(v) for k, v in hdr.items() if k.lower() == "x-seq"), int(after) + 1)
        return seq, raw


def backend():
    """The browser the web app uses. In Docker only the separate browser container is ever used, never one inside the web app's own container."""
    if os.environ.get("OLS_BROWSER", "").lower() == "off":
        return Unavailable("The built-in browser is turned off (OLS_BROWSER=off).")
    url = os.environ.get("OLS_BROWSER_URL")
    if url:
        tf = os.environ.get("OLS_BROWSER_TOKEN_FILE")
        try:
            token = Path(tf).read_text().strip() if tf else os.environ.get("OLS_BROWSER_TOKEN", "")
        except OSError:
            token = ""
        return RemoteBackend(url, token)
    if os.environ.get("OVERLEAF_UI_IN_DOCKER") == "1":
        return Unavailable("The built-in browser is not running. Start the app again with ./ui.sh up (after ./ui.sh build the first time).")
    ok, why = available()
    if not ok:
        return Unavailable(why)
    from . import config
    return Browsers(config.data_dir() / "browser")
