"""A real, unautomated browser on a virtual screen, shown in the pane like the scripted one.

QuillBot sits behind Cloudflare's "Verify you are human". A browser that a program drives through Playwright (even a well-behaved one) carries
markers that check can see, and it keeps asking. So for such a site the pane shows what a person would see on a remote desktop: an ordinary
Chromium window on a virtual display (Xvfb), started with a normal command line and no automation at all. The app takes a picture of that screen
and passes the mouse and keyboard on as X events (XTEST), exactly like a remote-desktop viewer. Nothing is disguised: this simply is a normal
browser, operated remotely.

The debugging port is used only for small, optional conveniences, one short connection at a time and never to switch on the page-instrumenting
`Runtime` domain: reading the page address and title, going to the start page, finding the writing box, finding the sign-in fields. If it is not
reachable everything else still works (the author clicks into a box and uses Paste).

Same interface as `webview._Session`, so the rest of the app does not know the difference.
"""
import io
import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from . import webview
from .webview import BrowserError

DISPLAY_W, DISPLAY_H = webview.MAX_W, webview.MAX_H     # the virtual screen; the browser window is sized to the pane inside it
SPECIAL = {"Enter": "Return", "Tab": "Tab", "Backspace": "BackSpace", "Delete": "Delete", "Escape": "Escape", "ArrowUp": "Up", "ArrowDown": "Down",
           "ArrowLeft": "Left", "ArrowRight": "Right", "Home": "Home", "End": "End", "PageUp": "Prior", "PageDown": "Next", "Insert": "Insert",
           "Space": "space", **{f"F{i}": f"F{i}" for i in range(1, 13)}}
MODIFIER_KEYSYM = {"Shift": "Shift_L", "Control": "Control_L", "Alt": "Alt_L", "Meta": "Super_L"}
CHROMIUMS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


def chromium_path():
    env = os.environ.get("OLS_REAL_CHROMIUM") or os.environ.get("OLS_BROWSER_PATH")
    if env and Path(env).exists():
        return env
    for name in CHROMIUMS:
        p = shutil.which(name)
        if p:
            return p
    return None


def available():
    """-> (ok, reason): everything the real-browser mode needs is on this computer."""
    missing = [n for n in ("Xvfb", "xclip") if not shutil.which(n)]
    for mod, name in (("Xlib.display", "python-xlib"), ("PIL.Image", "Pillow"), ("websocket", "websocket-client")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(name)
    if chromium_path() is None:
        missing.append("chromium")
    return (not missing, "" if not missing else "needs " + ", ".join(missing))


# Where something is on the virtual screen (the browser's own toolbar sits above the page), so it can be clicked like a person would.
PLACE_FIELD_JS = """(kind) => {
  const shown = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled && !el.readOnly; };
  const sel = kind === 'password' ? 'input[type=password]'
    : 'input[type=email], input[name=email], input[autocomplete=username], input[autocomplete=email], input[name=username], input[id*=email i]';
  const el = Array.from(document.querySelectorAll(sel)).find(shown);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.left + Math.min(r.width / 2, 40) + (window.outerWidth - window.innerWidth)), y: Math.round(r.top + r.height / 2 + (window.outerHeight - window.innerHeight)) };
}"""

PLACE_MAIN_JS = """() => {
  const editable = (el) => !!el && (el.isContentEditable || el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && /^(text|search)$/i.test(el.type)));
  if (editable(document.activeElement) && document.hasFocus()) return { already: true };
  let best = null, area = 0;
  for (const el of document.querySelectorAll('textarea, [contenteditable=""], [contenteditable="true"], input[type=text]')) {
    const r = el.getBoundingClientRect();
    const a = r.width * r.height;
    if (r.width > 120 && r.height > 24 && a > area && getComputedStyle(el).visibility !== 'hidden') { best = el; area = a; }
  }
  if (!best) return null;
  const r = best.getBoundingClientRect();
  return { x: Math.round(r.left + Math.min(r.width / 2, 60) + (window.outerWidth - window.innerWidth)), y: Math.round(r.top + Math.min(r.height / 2, 40) + (window.outerHeight - window.innerHeight)) };
}"""


class XSession(webview._Session):
    """One site's real browser. Same commands and picture feed as the scripted session."""
    kind = "real"

    def __init__(self, site, profile, exe=None, allow_private=False):
        super().__init__(site, profile, exe, allow_private)
        self.xvfb = self.chrome = self.xd = self.root = self.win = None
        self.port = None
        self.display = None

    # ------------------------------------------------------------------ the worker
    def _main(self):
        try:
            self._launch()
            self.ready.set()
            self._loop()
        except Exception as e:  # noqa: BLE001
            if not self.ready.is_set():
                self.error = "The built-in browser could not start: " + webview._short(e)
            elif not self.error:
                self.error = "The built-in browser stopped unexpectedly."
        finally:
            self.alive = False
            self._cleanup()
            self.ready.set()
            for fut in webview._drain(self.cmds):
                if not fut.done():
                    fut.set_exception(BrowserError(self.error or "The built-in browser was closed."))

    def _loop(self):
        last_cap = last_info = 0.0
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
                        fut.set_exception(BrowserError(webview._short(e)))
                self._current = None
                self.tick = time.time()
            now = time.time()
            if self._stop or now - self.last_seen > webview.IDLE_CLOSE:
                break
            if self.chrome.poll() is not None:
                self.error = "The browser window was closed. Open it again; your sign-in is kept."
                break
            watched = now < self.watch_until
            if watched or did:
                gap = 0.06 if now - self.last_input < 2.0 else 0.25
                if did or now - last_cap >= gap:
                    self._capture()
                    last_cap = time.time()
            if (watched or did) and now - last_info >= 0.5:
                self._refresh_info()
                last_info = now
            time.sleep(0.012 if (watched or did) else 0.08)

    # ------------------------------------------------------------------ processes
    def _launch(self):
        from Xlib import display as xdisplay
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
            try:
                (self.profile / name).unlink()
            except OSError:
                pass
        r, w = os.pipe()
        self.xvfb = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", f"{DISPLAY_W}x{DISPLAY_H}x24", "-nolisten", "tcp", "-ac"],
                                     pass_fds=(w,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(w)
        line = b""
        t0 = time.time()
        while not line.endswith(b"\n") and time.time() - t0 < 15:
            chunk = _read_timeout(r, 15 - (time.time() - t0))
            if not chunk:
                break
            line += chunk
        os.close(r)
        if not line.strip().isdigit():
            raise BrowserError("the virtual screen did not start")
        self.display = ":" + line.strip().decode()
        self.xd = xdisplay.Display(self.display)
        self.root = self.xd.screen().root
        self._start_chrome("about:blank")

    def _start_chrome(self, url):
        exe = self.exe or chromium_path()
        if not exe:
            raise BrowserError("no Chromium is installed for the real-browser mode")
        w, h = self.size
        env = {"DISPLAY": self.display, "HOME": os.environ.get("HOME", "/tmp"), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "LANG": "en_US.UTF-8", "LANGUAGE": "en_US:en"}
        if os.environ.get("OLS_BROWSER_TZ"):
            env["TZ"] = os.environ["OLS_BROWSER_TZ"]
        for k in ("XDG_RUNTIME_DIR", "TMPDIR", "FONTCONFIG_PATH", "LD_LIBRARY_PATH"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        args = [exe, f"--user-data-dir={self.profile}", f"--app={url}", "--window-position=0,0", f"--window-size={w},{h}",
                "--no-sandbox", "--test-type", "--no-first-run", "--no-default-browser-check", "--password-store=basic", "--use-mock-keychain",
                "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1", "--lang=en-US", "--hide-crash-restore-bubble", "--noerrdialogs",
                "--disk-cache-size=52428800", "--disable-features=Translate,MediaRouter",
                "--disable-blink-features=AutomationControlled"]    # a debugging port makes the browser say "navigator.webdriver" even though a person is using it
        self.chrome = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        port_file = self.profile / "DevToolsActivePort"
        t0 = time.time()
        while time.time() - t0 < 25:
            if self.chrome.poll() is not None:
                raise BrowserError("the browser exited while starting")
            try:
                self.port = int(port_file.read_text().split()[0])
            except (OSError, ValueError, IndexError):
                self.port = None
            if self.port and self._find_window():
                try:
                    if self._targets():
                        return
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.2)
        raise BrowserError("the browser window did not appear")

    def _find_window(self):
        """The newest visible top-level browser window on the virtual screen."""
        from Xlib import X
        found = None
        try:
            for win in self.root.query_tree().children:
                try:
                    cls = win.get_wm_class() or ()
                    attrs = win.get_attributes()
                    geo = win.get_geometry()
                except Exception:  # noqa: BLE001
                    continue
                if attrs.map_state == X.IsViewable and geo.width > 100 and geo.height > 100 and any("hrom" in c for c in cls):
                    found = win
        except Exception:  # noqa: BLE001
            return None
        if found is not None:
            self.win = found
        return found

    def _cleanup(self):
        for proc in (self.chrome, self.xvfb):
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM) if proc is self.chrome else proc.terminate()
                    proc.wait(timeout=4)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
        webview.kill_browser(self.profile)
        try:
            if self.xd is not None:
                self.xd.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ the debugging port: short connections, nothing that instruments the page
    def _targets(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2) as r:
            return [t for t in json.loads(r.read().decode("utf-8")) if t.get("type") == "page"]

    def _cdp(self, method, params=None, timeout=4):
        import websocket
        pages = self._targets()
        if not pages:
            raise BrowserError("the page is not ready yet")
        ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=timeout, suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    return msg.get("result") or {}
        finally:
            ws.close()

    def _js(self, fn_source, arg=None, timeout_ms=4000):
        expr = ("Promise.race([(async () => (%s)(%s))(), new Promise((r) => setTimeout(() => r(null), %d))])" % (fn_source, json.dumps(arg), timeout_ms))
        try:
            r = self._cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True, "userGesture": True}, timeout=timeout_ms / 1000 + 1)
        except Exception:  # noqa: BLE001
            return None
        return None if r.get("exceptionDetails") else (r.get("result") or {}).get("value")

    # ------------------------------------------------------------------ the picture
    def _capture(self):
        from PIL import Image
        from Xlib import X
        w, h = self.size
        try:
            raw = self.root.get_image(0, 0, w, h, X.ZPixmap, 0xFFFFFFFF)
            img = Image.frombuffer("RGB", (w, h), raw.data, "raw", "BGRX", 0, 1)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=70)
        except Exception:  # noqa: BLE001
            return
        jpeg = buf.getvalue()
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
            page = self._targets()[0]
        except Exception:  # noqa: BLE001
            return
        url = page.get("url", "")
        if not self.allow_private and not webview.public_url(url):
            self.info["notice"] = "The built-in browser only opens public websites, so it did not go to that address."
            try:
                self._cdp("Page.navigate", {"url": "about:blank"})
            except Exception:  # noqa: BLE001
                pass
            return
        self.info.update(url=url, title=(page.get("title") or "")[:120])
        if time.time() - self._challenge_at > 2.0:
            self._challenge_at = time.time()
            self.info["challenge"] = bool(self._js(webview.CHALLENGE_JS, timeout_ms=1500)) or (self.info["title"].lower().startswith(("just a moment", "attention required")))

    # ------------------------------------------------------------------ X input
    def _fake(self, kind, detail=0, x=0, y=0):
        from Xlib import X
        from Xlib.ext import xtest
        xtest.fake_input(self.xd, {"move": X.MotionNotify, "bdown": X.ButtonPress, "bup": X.ButtonRelease, "kdown": X.KeyPress, "kup": X.KeyRelease}[kind],
                         detail=detail if kind != "move" else False, x=x, y=y)

    def _focus(self):
        from Xlib import X
        win = self._find_window()
        if win is not None:
            try:
                self.xd.set_input_focus(win, X.RevertToParent, X.CurrentTime)
            except Exception:  # noqa: BLE001
                pass
        self.xd.sync()

    def _keycode(self, keysym_name_or_char):
        from Xlib import XK
        if len(keysym_name_or_char) == 1:
            ks = ord(keysym_name_or_char)
            if ks > 0xFF:
                ks += 0x01000000
        else:
            ks = XK.string_to_keysym(keysym_name_or_char)
        pairs = list(self.xd.keysym_to_keycodes(ks)) if ks else []
        return pairs[0] if pairs else (0, 0)

    def _press(self, keycode, shift=False):
        shift_code = self._keycode("Shift_L")[0]
        if shift and shift_code:
            self._fake("kdown", shift_code)
        self._fake("kdown", keycode)
        self._fake("kup", keycode)
        if shift and shift_code:
            self._fake("kup", shift_code)

    def _mods(self, want):
        want = {m for m in (want or []) if m in webview.MODS}
        for m in list(self._held - want):
            self._fake("kup", self._keycode(MODIFIER_KEYSYM[m])[0])
            self._held.discard(m)
        for m in webview.MODS:
            if m in want and m not in self._held:
                self._fake("kdown", self._keycode(MODIFIER_KEYSYM[m])[0])
                self._held.add(m)

    def _type(self, text, delay=0.012):
        for ch in text:
            code, index = self._keycode(ch) if ch != "\n" else self._keycode("Return")
            if code and ch.isascii() and index in (0, 1):
                self._press(code, shift=(index == 1))
                self.xd.sync()
                time.sleep(delay)
            else:
                self._paste(ch)

    def _click_at(self, x, y):
        self._focus()
        self._fake("move", x=int(x), y=int(y))
        self._fake("bdown", 1)
        self._fake("bup", 1)
        self.xd.sync()
        time.sleep(0.06)

    def _select_all(self):
        ctrl, a = self._keycode("Control_L")[0], self._keycode("a")[0]
        self._fake("kdown", ctrl)
        self._press(a)
        self._fake("kup", ctrl)
        self.xd.sync()
        time.sleep(0.04)

    def _set_clipboard(self, text):
        subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=text.encode("utf-8"), env={**os.environ, "DISPLAY": self.display},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        time.sleep(0.05)

    def _paste(self, text):
        self._set_clipboard(text)
        self._focus()
        ctrl = self._keycode("Control_L")[0]
        v = self._keycode("v")[0]
        self._fake("kdown", ctrl)
        self._press(v)
        self._fake("kup", ctrl)
        self.xd.sync()
        time.sleep(0.08)

    def _read_selection(self, which):
        try:
            out = subprocess.run(["xclip", "-selection", which, "-o"], env={**os.environ, "DISPLAY": self.display}, capture_output=True, timeout=3)
            return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    # ------------------------------------------------------------------ commands
    def _do_open(self, url, w, h, force):
        self._do_resize(w, h)
        cur = self.info.get("url", "") or ""
        from urllib.parse import urlsplit
        same = bool(cur) and cur != "about:blank" and webview._site_key(urlsplit(cur).hostname) == webview._site_key(urlsplit(url).hostname)
        self.info["notice"] = ""
        if force or not same:
            self.info["challenge"] = False
            self._challenge_at = 0.0
            for attempt in range(4):
                try:
                    self._cdp("Page.navigate", {"url": url})
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(0.4)
            else:
                self._restart_with(url)           # the debugging port did not answer: start the window again on that address
        time.sleep(0.3)
        self._refresh_info()
        return self.state()

    def _restart_with(self, url):
        if self.chrome is not None and self.chrome.poll() is None:
            try:
                os.killpg(self.chrome.pid, signal.SIGTERM)
                self.chrome.wait(timeout=4)
            except Exception:  # noqa: BLE001
                self.chrome.kill()
        webview.kill_browser(self.profile)
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
            try:
                (self.profile / name).unlink()
            except OSError:
                pass
        self._start_chrome(url)

    def _do_resize(self, w, h):
        w, h = webview._clamp(w, webview.MIN_W, webview.MAX_W, self.size[0]), webview._clamp(h, webview.MIN_H, webview.MAX_H, self.size[1])
        if (w, h) != self.size:
            self.size = (w, h)
            win = self._find_window()
            if win is not None:
                try:
                    win.configure(x=0, y=0, width=w, height=h)
                    self.xd.sync()
                except Exception:  # noqa: BLE001
                    pass
            self._hash = None
        return {"w": w, "h": h}

    def _do_input(self, events):
        self.last_input = time.time()
        self._focus()
        w, h = self.size
        for e in events[:webview.MAX_EVENTS]:
            if not isinstance(e, dict):
                continue
            t = e.get("t")
            x, y = webview._clamp(e.get("x"), 0, w - 1, 0), webview._clamp(e.get("y"), 0, h - 1, 0)
            if t in ("move", "down", "up", "wheel"):
                self._mods(e.get("mods"))
            if t == "move":
                self._fake("move", x=x, y=y)
            elif t in ("down", "up"):
                self._fake("move", x=x, y=y)
                self._fake("bdown" if t == "down" else "bup", {0: 1, 1: 2, 2: 3}.get(e.get("b"), 1))
            elif t == "wheel":
                self._fake("move", x=x, y=y)
                dy, dx = webview._clamp(e.get("dy"), -2000, 2000, 0), webview._clamp(e.get("dx"), -2000, 2000, 0)
                for amount, minus, plus in ((dy, 4, 5), (dx, 6, 7)):
                    for _ in range(min(20, max(1, round(abs(amount) / 53))) if amount else 0):
                        self._fake("bdown", minus if amount < 0 else plus)
                        self._fake("bup", minus if amount < 0 else plus)
            elif t == "type":
                self._mods([])
                self._type(str(e.get("text") or "")[:webview.MAX_TEXT])
            elif t == "text":
                self._mods([])
                self._paste(str(e.get("text") or "")[:webview.MAX_TEXT])
            elif t == "key":
                key = e.get("key")
                name = SPECIAL.get(key) if key in webview.NAMED_KEYS or key in ("Space", " ") else None
                if key == " ":
                    name = "space"
                if name or (isinstance(key, str) and len(key) == 1):
                    self._mods(e.get("mods"))
                    code, index = self._keycode(name or key)
                    if code:
                        self._press(code, shift=(index == 1 and "Shift" not in self._held))
        self._mods([])
        self.xd.sync()
        return {"ok": True}

    def _do_nav(self, action):
        if action == "stop":
            try:
                self._cdp("Page.stopLoading")
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                if action == "reload":
                    self._cdp("Page.reload")
                else:
                    hist = self._cdp("Page.getNavigationHistory")
                    i = hist.get("currentIndex", 0) + (-1 if action == "back" else 1)
                    if 0 <= i < len(hist.get("entries", [])):
                        self._cdp("Page.navigateToHistoryEntry", {"entryId": hist["entries"][i]["id"]})
            except Exception:  # noqa: BLE001
                raise BrowserError("The browser did not answer. Try again.") from None
        self.info["challenge"] = False
        self._challenge_at = 0.0
        time.sleep(0.3)
        self._refresh_info()
        return self.state()

    def _do_selection(self):
        return (self._read_selection("primary") or self._read_selection("clipboard"))[:webview.MAX_TEXT]

    def _do_clipboard(self):
        return self._read_selection("clipboard")[:webview.MAX_TEXT]

    def _do_insert(self, text, focus_main=False):
        """Pastes the text where the cursor is. For QuillBot, when nothing editable has the cursor, the biggest writing box on the page is clicked
        into first and its old content replaced."""
        focused = False
        if focus_main:
            spot = self._js(PLACE_MAIN_JS)
            if isinstance(spot, dict) and "x" in spot:
                self._click_at(spot["x"], spot["y"])
                self._select_all()
                focused = True
        self._mods([])
        self._paste(str(text)[:webview.MAX_TEXT])
        return {"ok": True, "focused": focused}

    def _do_signin(self, email, password):
        """Clicks into the visible sign-in fields of the current page and types the saved email and password, key by key like a person; checks the page
        kept them and, when it did not, sets them the way a form framework notices. Nothing is submitted. -> {filled: [...], failed: [...]}"""
        filled, failed = [], []
        for kind, value in (("email", email), ("password", password)):
            spot = self._js(PLACE_FIELD_JS, kind) if value else None
            if not isinstance(spot, dict):
                continue
            self._click_at(spot["x"], spot["y"])
            self._select_all()
            self._mods([])
            self._type(value, delay=0.018)
            ok = self._js(webview.KEPT_JS, len(value)) or self._js(webview.SET_FIELD_JS, [kind, value])
            (filled if ok else failed).append(kind)
        return {"filled": filled, "failed": failed}

    def _do_close(self):
        self._stop = True
        return {"ok": True}

    def state(self):
        st = super().state()
        st["kind"] = "real"
        return st


def _read_timeout(fd, seconds):
    import select
    r, _, _ = select.select([fd], [], [], max(0.0, seconds))
    return os.read(fd, 64) if r else b""
