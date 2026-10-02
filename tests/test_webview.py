"""The built-in browser engine, against a local stand-in site that refuses to be framed (X-Frame-Options: DENY) like QuillBot and Overleaf do.

Needs Playwright and a Chromium; skipped where they are not installed (set OLS_BROWSER_PATH to a Chromium binary to point at one).
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import tests.helpers  # noqa: F401  (puts scripts/ on sys.path)
from tests.helpers import SCRIPTS
from writing_core import webview


def load_service():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ols_browser_service", SCRIPTS / "ui" / "browser_service.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

PAGE = b"""<!doctype html><title>Stand-in site</title><body style="margin:0;font:16px sans-serif">
<h1 style="margin:10px 20px">Stand-in editor</h1>
<input id=a style="position:absolute;left:20px;top:80px;width:300px;height:30px">
<textarea id=b style="position:absolute;left:20px;top:140px;width:300px;height:100px"></textarea>
<button id=go style="position:absolute;left:20px;top:260px;width:100px;height:40px"
  onclick="fetch('/log?'+new URLSearchParams({a:document.getElementById('a').value,b:document.getElementById('b').value}))">Go</button>
<a id=lnk href="/login" style="position:absolute;left:200px;top:270px">sign in</a>
<button id=copy style="position:absolute;left:330px;top:260px;width:100px;height:40px"
  onclick="navigator.clipboard.writeText(document.getElementById('b').value)">Copy</button>
</body>"""
LOGIN = b"""<!doctype html><title>Sign in</title><body style="margin:0">
<form method=post action="/login" style="margin:30px">
<input type=email name=email style="display:block;width:300px;height:30px;margin:10px">
<input type=password name=password style="display:block;width:300px;height:30px;margin:10px">
<button id=sub style="width:100px;height:40px;margin:10px">Sign in</button></form></body>"""


def chromium():
    p = os.environ.get("OLS_BROWSER_PATH") or "/opt/pw-browsers/chromium"
    return p if Path(p).exists() else None


def can_run():
    ok, _ = webview.available()
    return ok and (chromium() is not None or "playwright" in str(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")))


class Site(BaseHTTPRequestHandler):
    log, posts, cookies = [], [], []

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html", extra=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/log":
            Site.log.append(dict(urllib.parse.parse_qsl(u.query)))
            return self._send(b"ok", "text/plain")
        if u.path == "/login":
            return self._send(LOGIN)
        if u.path == "/seen":   # for the browser demo: what the page has reported so far
            return self._send(json.dumps({"log": Site.log, "posts": Site.posts}).encode(), "application/json")
        Site.cookies.append(self.headers.get("Cookie") or "")
        self._send(PAGE, extra={"Set-Cookie": "session=abc123; Max-Age=86400; Path=/"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Site.posts.append(dict(urllib.parse.parse_qsl(self.rfile.read(n).decode())))
        self._send(b"<!doctype html><title>Signed in</title><p>done")


def webview_processes(profile):
    """Chromium processes (Linux) that were started with this profile folder."""
    found = []
    for d in Path("/proc").glob("[0-9]*"):
        try:
            if ("--user-data-dir=" + profile) in (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace"):
                found.append(d.name)
        except OSError:
            pass
    return found


def wait_for(fn, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return fn()


@unittest.skipUnless(can_run(), "Playwright and a Chromium are not installed here")
class BuiltInBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/"
        cls.tmp = Path(tempfile.mkdtemp())
        cls.b = webview.Browsers(cls.tmp, exe=chromium(), allow_private=True)

    @classmethod
    def tearDownClass(cls):
        cls.b.close_all()
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        Site.log.clear()
        Site.posts.clear()

    def test_shows_a_page_that_refuses_framing_and_takes_typing_and_clicks(self):
        st = self.b.open("quillbot", self.url, 1100, 700, force=True)
        self.assertTrue(st["running"])
        self.assertEqual(st["title"], "Stand-in site")
        seq, jpeg = self.b.next_frame("quillbot", -1, 8)
        self.assertTrue(jpeg.startswith(b"\xff\xd8"), "a JPEG picture of the page")
        # click into the input, type, click into the textarea, paste, press Go
        self.b.input("quillbot", [{"t": "down", "x": 60, "y": 95, "b": 0, "n": 1}, {"t": "up", "x": 60, "y": 95, "b": 0, "n": 1},
                                  {"t": "type", "text": "héllo"},
                                  {"t": "down", "x": 60, "y": 180}, {"t": "up", "x": 60, "y": 180},
                                  {"t": "text", "text": "pasted\nlines \\cite{a}"},
                                  {"t": "down", "x": 60, "y": 280}, {"t": "up", "x": 60, "y": 280}])
        got = wait_for(lambda: Site.log[-1] if Site.log else None)
        self.assertEqual(got, {"a": "héllo", "b": "pasted\nlines \\cite{a}"})

    def test_a_new_picture_arrives_only_when_something_changed(self):
        self.b.open("quillbot", self.url, 1100, 700, force=True)
        seq, _ = self.b.next_frame("quillbot", -1, 8)
        _, none = self.b.next_frame("quillbot", seq, 1.0)
        self.assertIsNone(none, "nothing moved, so no new picture")
        self.b.input("quillbot", [{"t": "down", "x": 60, "y": 180}, {"t": "up", "x": 60, "y": 180}, {"t": "type", "text": "x"}])
        seq2, jpeg = self.b.next_frame("quillbot", seq, 5.0)
        self.assertNotEqual(seq2, seq)
        self.assertTrue(jpeg)

    def test_copy_out_returns_the_selected_text_and_keyboard_shortcuts_work(self):
        self.b.open("quillbot", self.url, 1100, 700, force=True)
        self.b.input("quillbot", [{"t": "down", "x": 60, "y": 180}, {"t": "up", "x": 60, "y": 180}, {"t": "type", "text": "select me"},
                                  {"t": "key", "key": "a", "mods": ["Control"]}])
        self.assertEqual(self.b.selection("quillbot")["text"], "select me")
        self.b.input("quillbot", [{"t": "key", "key": "Backspace"}])
        self.assertEqual(self.b.selection("quillbot")["text"], "")
        self.b.insert("quillbot", "inserted at the cursor")
        self.b.input("quillbot", [{"t": "key", "key": "a", "mods": ["Control"]}])
        self.assertEqual(self.b.selection("quillbot")["text"], "inserted at the cursor")

    def test_a_suggestion_is_put_into_the_biggest_writing_box_when_nothing_has_the_cursor(self):
        self.b.open("quillbot", self.url, 1100, 700, force=True)
        self.b.insert("quillbot", "first suggestion")
        self.b.insert("quillbot", "second suggestion")        # the cursor is in the box now, so this goes in at the cursor
        self.b.input("quillbot", [{"t": "down", "x": 60, "y": 280}, {"t": "up", "x": 60, "y": 280}])
        self.assertEqual(wait_for(lambda: Site.log[-1] if Site.log else None), {"b": "first suggestionsecond suggestion"})
        self.b.open("quillbot", self.url, 1100, 700, force=True)   # a fresh page: nothing focused, the box is chosen and its old text replaced
        self.b.insert("quillbot", "replaced")
        Site.log.clear()
        self.b.input("quillbot", [{"t": "down", "x": 60, "y": 280}, {"t": "up", "x": 60, "y": 280}])
        self.assertEqual(wait_for(lambda: Site.log[-1] if Site.log else None), {"b": "replaced"})

    def test_what_the_page_copied_with_its_own_copy_button_can_be_read_back(self):
        self.b.open("quillbot", self.url, 1100, 700, force=True)
        self.b.input("quillbot", [{"t": "down", "x": 60, "y": 180}, {"t": "up", "x": 60, "y": 180}, {"t": "type", "text": "copy me"},
                                  {"t": "key", "key": "a", "mods": ["Control"]}, {"t": "key", "key": "c", "mods": ["Control"]}])
        self.assertEqual(self.b.clipboard("quillbot")["text"], "copy me")

    def test_fills_the_sign_in_form_but_does_not_submit_it(self):
        self.b.open("overleaf", self.url + "login", 1100, 700, force=True)
        r = self.b.signin("overleaf", "me@example.org", "s3cret pass")
        self.assertEqual(r["filled"], ["email", "password"])
        time.sleep(0.4)
        self.assertEqual(Site.posts, [], "nothing is sent until the author presses the site's own button")
        self.b.input("overleaf", [{"t": "key", "key": "Enter"}])
        got = wait_for(lambda: Site.posts[-1] if Site.posts else None)
        self.assertEqual(got, {"email": "me@example.org", "password": "s3cret pass"})

    def test_the_sign_in_survives_closing_the_browser(self):
        self.b.open("overleaf", self.url, 1100, 700, force=True)
        self.b.close("overleaf")
        self.assertFalse(self.b.state("overleaf")["running"])
        Site.cookies.clear()
        self.b.open("overleaf", self.url, 1100, 700, force=True)
        self.assertTrue(wait_for(lambda: any("session=abc123" in c for c in Site.cookies)), "the cookie was kept in the profile folder")
        self.assertTrue((self.tmp / "overleaf").is_dir())
        self.assertFalse((self.tmp / "quillbot").samefile(self.tmp / "overleaf"), "one profile per site")

    def test_each_site_has_its_own_browser(self):
        self.b.open("quillbot", self.url, 1100, 700, force=True)
        self.b.open("overleaf", self.url + "login", 1100, 700, force=True)
        self.assertIn("/login", self.b.state("overleaf")["url"])
        self.assertNotIn("/login", self.b.state("quillbot")["url"])

    def test_resize_changes_what_the_page_is_drawn_at(self):
        self.b.open("quillbot", self.url, 1100, 700, force=True)
        st = self.b.resize("quillbot", 1400, 800)
        self.assertEqual((st["w"], st["h"]), (1400, 800))
        self.assertEqual(self.b.resize("quillbot", 10, 10)["w"], webview.MIN_W)

    def test_back_forward_reload(self):
        self.b.open("quillbot", self.url, 1100, 700, force=True)
        self.b.input("quillbot", [{"t": "down", "x": 205, "y": 278}, {"t": "up", "x": 205, "y": 278}])
        self.assertTrue(wait_for(lambda: "/login" in (self.b.nav("quillbot", "reload") or {}).get("url", "")))
        self.assertNotIn("/login", self.b.nav("quillbot", "back")["url"])

    def test_the_app_can_only_open_the_addresses_it_knows(self):
        strict = webview.Browsers(self.tmp / "strict", exe=chromium())
        for site, url in (("quillbot", "https://evil.example/"), ("quillbot", "http://quillbot.com/"), ("overleaf", "https://www.overleaf.com/login"),
                          ("overleaf", "https://127.0.0.1/project/" + "a" * 24)):
            with self.assertRaises(webview.BrowserError, msg=url):
                strict.open(site, url)
        with self.assertRaises(webview.BrowserError):
            strict.open("somewhere", "https://quillbot.com/")

    def test_input_that_is_not_understood_is_ignored(self):
        self.b.open("quillbot", self.url, 1100, 700, force=True)
        self.b.input("quillbot", [None, "x", {"t": "nope"}, {"t": "key", "key": "Hyper"}, {"t": "key", "key": "F99"}, {"t": "down", "x": "a", "y": None}])
        with self.assertRaises(webview.BrowserError):
            self.b.input("quillbot", "not a list")

    def test_a_page_that_never_answers_does_not_freeze_the_pane(self):
        import socket
        hole = socket.socket()
        hole.bind(("127.0.0.1", 0))
        hole.listen(50)                      # accepts connections and never says a word, like an unreachable site
        old = webview.NAV_TIMEOUT_MS
        webview.NAV_TIMEOUT_MS = 2000
        try:
            b = webview.Browsers(self.tmp / "hole", exe=chromium(), allow_private=True)
            st = b.open("quillbot", f"http://127.0.0.1:{hole.getsockname()[1]}/", 1100, 700, force=True)
            self.assertIn("did not answer in time", st["notice"])
            seq, jpeg = b.next_frame("quillbot", -1, 8)
            self.assertTrue(jpeg and jpeg.startswith(b"\xff\xd8"), "there is still a picture to show")
            t0 = time.time()
            self.assertEqual(b.selection("quillbot")["text"], "")
            self.assertEqual(b.clipboard("quillbot")["text"], "")
            self.assertEqual(b.signin("quillbot", "a@b.org", "pw")["filled"], [])
            self.assertLess(time.time() - t0, 10, "nothing waits for the page that never answers")
            b.nav("quillbot", "stop")
            b.open("quillbot", self.url, 1100, 700, force=True)       # and a working page opens afterwards in the same browser
            self.assertEqual(b.state("quillbot")["title"], "Stand-in site")
            b.close_all()
        finally:
            webview.NAV_TIMEOUT_MS = old
            hole.close()

    def test_a_browser_that_stops_answering_is_killed_and_replaced(self):
        old_stuck, old_clip = webview.STUCK_AFTER, webview._Session._do_clipboard
        webview.STUCK_AFTER = 3
        webview._Session._do_clipboard = lambda self: time.sleep(40)     # a call that never comes back
        try:
            b = webview.Browsers(self.tmp / "stuck", exe=chromium(), allow_private=True)
            b.open("quillbot", self.url, 1100, 700, force=True)
            profile = str(self.tmp / "stuck" / "quillbot")
            self.assertTrue(webview_processes(profile), "Chromium is running")
            t0 = time.time()
            with self.assertRaises(webview.BrowserError) as cm:
                b.clipboard("quillbot")
            self.assertIn("stopped responding", str(cm.exception))
            self.assertLess(time.time() - t0, 15)
            self.assertTrue(wait_for(lambda: not webview_processes(profile), 8), "the stuck Chromium was stopped")
            self.assertFalse(b.state("quillbot")["running"])
            webview._Session._do_clipboard = old_clip
            st = b.open("quillbot", self.url, 1100, 700, force=True)   # asking again starts a fresh browser with the same profile
            self.assertTrue(st["running"])
            b.close_all()
        finally:
            webview.STUCK_AFTER, webview._Session._do_clipboard = old_stuck, old_clip

    def test_closed_by_itself_when_nobody_is_looking(self):
        old = webview.IDLE_CLOSE
        webview.IDLE_CLOSE = 2
        try:
            b = webview.Browsers(self.tmp / "idle", exe=chromium(), allow_private=True)
            b.open("quillbot", self.url, 1100, 700, force=True)
            self.assertTrue(wait_for(lambda: not b.state("quillbot")["running"], 12))
            b.open("quillbot", self.url, 1100, 700)           # and it starts again when asked
            self.assertTrue(b.state("quillbot")["running"])
            b.close_all()
        finally:
            webview.IDLE_CLOSE = old


@unittest.skipUnless(can_run(), "Playwright and a Chromium are not installed here")
class BrowserContainer(unittest.TestCase):
    """The web app talks to the browser through the service of the separate container (here in this process, on a loopback port)."""

    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.site.server_address[1]}/"
        cls.tmp = Path(tempfile.mkdtemp())
        cls.token = "k" * 40
        cls.browsers = webview.Browsers(cls.tmp, exe=chromium(), allow_private=True)
        cls.svc = load_service().make_service("127.0.0.1", 0, cls.token, cls.browsers)
        threading.Thread(target=cls.svc.serve_forever, daemon=True).start()
        cls.remote = webview.RemoteBackend(f"http://127.0.0.1:{cls.svc.server_address[1]}", cls.token)

    @classmethod
    def tearDownClass(cls):
        cls.browsers.close_all()
        cls.svc.shutdown()
        cls.site.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        Site.log.clear()
        Site.posts.clear()

    def test_everything_works_through_the_service(self):
        self.assertTrue(self.remote.status()["available"])
        st = self.remote.open("quillbot", self.url, 1100, 700, force=True)
        self.assertEqual(st["title"], "Stand-in site")
        seq, jpeg = self.remote.next_frame("quillbot", -1, 8)
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertEqual(self.remote.next_frame("quillbot", seq, 0.5), (seq, None))
        self.remote.input("quillbot", [{"t": "down", "x": 60, "y": 95}, {"t": "up", "x": 60, "y": 95}, {"t": "type", "text": "via service"}])
        self.assertTrue(wait_for(lambda: self.remote.selection("quillbot") is not None))
        self.remote.input("quillbot", [{"t": "key", "key": "a", "mods": ["Control"]}])
        self.assertEqual(self.remote.selection("quillbot")["text"], "via service")
        self.remote.open("overleaf", self.url + "login", 1100, 700, force=True)
        self.assertEqual(self.remote.signin("overleaf", "a@b.org", "pw pw pw")["filled"], ["email", "password"])
        self.assertEqual(self.remote.nav("overleaf", "reload")["running"], True)
        self.assertIn("running", self.remote.status()["sites"]["overleaf"])
        self.remote.close("overleaf")
        self.assertFalse(self.remote.state("overleaf")["running"])

    def test_wrong_token_and_unknown_calls_are_refused(self):
        bad = webview.RemoteBackend(f"http://127.0.0.1:{self.svc.server_address[1]}", "x" * 40)
        self.assertFalse(bad.status()["available"])
        with self.assertRaises(webview.BrowserError):
            bad.open("quillbot", self.url)
        with self.assertRaises(webview.BrowserError):
            self.remote._call("rm_rf")
        with self.assertRaises(webview.BrowserError):
            self.remote.input("quillbot", "nope")

    def test_a_browser_container_that_is_gone_is_explained(self):
        gone = webview.RemoteBackend("http://127.0.0.1:9", self.token)
        st = gone.status()
        self.assertFalse(st["available"])
        self.assertIn("ui.sh up", st["reason"])
        with self.assertRaises(webview.BrowserError):
            gone.next_frame("quillbot", -1, 0.1)


class AddressRules(unittest.TestCase):
    def test_only_public_websites_can_be_visited(self):
        for ok in ("https://www.overleaf.com/project/abc", "https://quillbot.com/", "http://example.org/", "about:blank", "data:text/html,hi", "chrome-error://chromewebdata/"):
            self.assertTrue(webview.public_url(ok), ok)
        for bad in ("http://127.0.0.1:8765/", "http://localhost/", "http://[::1]/", "http://10.1.2.3/", "http://192.168.0.1/", "http://169.254.169.254/latest/",
                    "http://overleaf-ui:8765/", "http://overleaf-browser:9233/", "http://host.docker.internal/", "http://printer.local/", "http://intranet.internal/",
                    "file:///etc/passwd", "ftp://example.org/", "javascript:alert(1)", "http://x.localhost/"):
            self.assertFalse(webview.public_url(bad), bad)

    def test_unavailable_browser_says_why_and_everything_refuses(self):
        u = webview.Unavailable("Not installed.")
        self.assertFalse(u.status()["available"])
        for call in (lambda: u.open("quillbot", "x"), lambda: u.input("quillbot", []), lambda: u.next_frame("quillbot"), lambda: u.signin("quillbot")):
            with self.assertRaises(webview.BrowserError):
                call()

    def test_in_docker_the_web_app_never_runs_a_browser_itself(self):
        env = {"OVERLEAF_UI_IN_DOCKER": "1"}
        old = {k: os.environ.pop(k, None) for k in ("OLS_BROWSER_URL", "OLS_BROWSER", "OVERLEAF_UI_IN_DOCKER")}
        try:
            os.environ.update(env)
            self.assertEqual(webview.backend().kind, "none")
            os.environ["OLS_BROWSER_URL"] = "http://overleaf-browser:9233"
            os.environ["OLS_BROWSER_TOKEN"] = "t" * 30
            self.assertEqual(webview.backend().kind, "remote")
            os.environ["OLS_BROWSER"] = "off"
            self.assertEqual(webview.backend().kind, "none")
        finally:
            for k in ("OLS_BROWSER_URL", "OLS_BROWSER", "OVERLEAF_UI_IN_DOCKER", "OLS_BROWSER_TOKEN"):
                os.environ.pop(k, None)
            os.environ.update({k: v for k, v in old.items() if v is not None})


if __name__ == "__main__":
    unittest.main()
