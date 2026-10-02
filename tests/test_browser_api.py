"""The web app's side of the built-in browser: endpoints, pictures, saved sign-in details, and how the panes choose between the browser, a frame and a window."""
import http.client
import os
import stat
import threading
import time
import unittest
from unittest import mock

from tests.helpers import common, make_paper
from tests.test_ui_server import TOKEN, UiBase, ui
from tests.test_webview import Site, can_run, chromium, wait_for
from writing_core import config, webview

OVERLEAF_GIT = "https://git.overleaf.com/6aa33c64db9c6e1c1dde1aa0"
JPEG_A, JPEG_B = b"\xff\xd8AAAA\xff\xd9", b"\xff\xd8BBBBBB\xff\xd9"


class FakeBrowser:
    """Stands in for the browser (local or in its container) and records what the app asked of it."""
    kind = "fake"

    def __init__(self, filled=("email", "password"), failed=()):
        self.calls, self.filled, self.failed, self.frames, self.polls = [], list(filled), list(failed), [JPEG_A, JPEG_B], []
        self.up = {}

    def status(self):
        return {"available": True, "reason": "", "kind": self.kind, "sites": {s: {"running": s in self.up} for s in webview.SITES}}

    def open(self, site, url, w=None, h=None, force=False):
        self.calls.append(("open", site, url, w, h, force))
        self.up[site] = url
        return {"running": True, "url": url, "title": "", "w": w or 1100, "h": h or 700, "seq": 0, "notice": ""}

    def resize(self, site, w, h):
        self.calls.append(("resize", site, w, h))
        return {"w": w, "h": h}

    def input(self, site, events):
        if not isinstance(events, list):
            raise webview.BrowserError("events must be a list")
        self.calls.append(("input", site, events))
        return {"ok": True}

    def nav(self, site, action):
        if action not in ("back", "forward", "reload", "stop"):
            raise webview.BrowserError("unknown action")
        self.calls.append(("nav", site, action))
        return {"running": True, "url": "x"}

    def selection(self, site):
        return {"text": "what I selected"}

    def clipboard(self, site):
        return {"text": "what the site copied"}

    def insert(self, site, text):
        self.calls.append(("insert", site, text))
        return {"ok": True}

    def signin(self, site, email="", password=""):
        self.calls.append(("signin", site, email, password))
        return {"filled": self.filled, "failed": self.failed}

    def state(self, site):
        return {"running": site in self.up, "url": self.up.get(site, "")}

    def recompile(self, site):
        self.calls.append(("recompile", site))
        self.recompile_tries = getattr(self, "recompile_tries", 0) + 1
        return {"clicked": self.recompile_tries > getattr(self, "recompile_after", 0)}

    def close(self, site):
        self.up.pop(site, None)
        return {"ok": True}

    def next_frame(self, site, after=-1, timeout=10):
        self.polls.append(time.time())
        if after + 1 < len(self.frames):
            return after + 1, self.frames[after + 1]
        time.sleep(min(timeout, 0.15))
        return after, None


class BrowserApiBase(UiBase):
    def setUp(self):
        super().setUp()
        self.fake = FakeBrowser()
        self.srv.app._browser = self.fake
        self.c.login()
        self.root = self.tmp / "papers"
        self.paper = make_paper(self.root, "main", "x", remote=OVERLEAF_GIT)
        common.register(str(self.paper), "main")


class Endpoints(BrowserApiBase):
    def test_open_uses_the_right_address_for_each_site(self):
        st, r = self.c.post("/api/browser/quillbot/open", {"w": 900, "h": 600})
        self.assertEqual(st, 200, r)
        st, r = self.c.post("/api/browser/overleaf/open", {"paper": "main", "w": 900, "h": 600, "force": True})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.fake.calls[0][:3], ("open", "quillbot", "https://quillbot.com/paraphrasing-tool"))
        self.assertEqual(self.fake.calls[1], ("open", "overleaf", "https://www.overleaf.com/project/6aa33c64db9c6e1c1dde1aa0", 900, 600, True))

    def test_overleaf_needs_a_known_paper_with_a_project(self):
        self.assertEqual(self.c.post("/api/browser/overleaf/open", {"paper": "nope"})[0], 404)
        other = make_paper(self.root, "plain", "x", remote="https://example.org/no-project-here")
        common.register(str(other), "plain")
        st, r = self.c.post("/api/browser/overleaf/open", {"paper": "plain"})
        self.assertEqual(st, 409)
        self.assertIn("Settings", r["error"])

    def test_home_goes_back_to_the_start_address_and_the_other_buttons_are_checked(self):
        self.c.post("/api/browser/overleaf/nav", {"action": "home", "paper": "main"})
        self.assertEqual(self.fake.calls[-1][0:2], ("open", "overleaf"))
        self.assertTrue(self.fake.calls[-1][-1], "home forces the page to load")
        self.assertEqual(self.c.post("/api/browser/quillbot/nav", {"action": "reload"})[0], 200)
        self.assertEqual(self.c.post("/api/browser/quillbot/nav", {"action": "stop"})[0], 200)
        st, r = self.c.post("/api/browser/quillbot/nav", {"action": "format-disk"})
        self.assertEqual(st, 400)

    def test_input_selection_insert_resize_close(self):
        ev = [{"t": "down", "x": 1, "y": 2}, {"t": "type", "text": "a"}]
        self.assertEqual(self.c.post("/api/browser/quillbot/input", {"events": ev})[0], 200)
        self.assertEqual(self.fake.calls[-1], ("input", "quillbot", ev))
        self.assertEqual(self.c.post("/api/browser/quillbot/input", {"events": "x"})[0], 400)
        self.assertEqual(self.c.post("/api/browser/quillbot/selection")[1], {"text": "what I selected"})
        self.assertEqual(self.c.post("/api/browser/quillbot/clipboard")[1], {"text": "what the site copied"})
        self.assertEqual(self.c.post("/api/browser/quillbot/insert", {"text": "hello \\cite{a}"})[0], 200)
        self.assertEqual(self.c.post("/api/browser/quillbot/insert", {"text": ""})[0], 400)
        self.assertEqual(self.c.post("/api/browser/quillbot/resize", {"w": 1000, "h": 500})[1], {"w": 1000, "h": 500})
        self.assertEqual(self.c.post("/api/browser/quillbot/close")[1], {"ok": True})

    def test_only_the_two_sites_exist(self):
        for path in ("/api/browser/evil/open", "/api/browser/quillbot%2f..%2fx/input"):
            self.assertEqual(self.c.post(path)[0], 404, path)
        self.assertEqual(self.c.raw("GET", "/api/browser/evil/frame", headers={"X-Requested-With": "ols"})[0], 404)

    def test_needs_the_signed_in_cookie_and_the_request_header(self):
        anon = type(self.c)(self.srv)
        self.assertEqual(anon.raw("POST", "/api/browser/quillbot/input", {"events": []}, {"X-Requested-With": "ols"})[0], 401)
        self.assertEqual(anon.raw("GET", "/api/browser/quillbot/frame")[0], 401)
        self.assertEqual(anon.raw("GET", "/api/browser/quillbot/stream")[0], 401)
        self.assertEqual(self.c.raw("POST", "/api/browser/quillbot/input", {"events": []})[0], 403)           # no X-Requested-With
        self.assertEqual(self.c.raw("POST", "/api/browser/quillbot/input", {"events": []}, {"X-Requested-With": "ols", "Origin": "http://evil.example"})[0], 403)

    def test_a_browser_that_is_not_there_is_explained(self):
        self.srv.app._browser = webview.Unavailable("It is not installed.")
        st, r = self.c.post("/api/browser/quillbot/open")
        self.assertEqual((st, r["error"]), (400, "It is not installed."))
        st, r = self.c.get("/api/browser/quillbot/frame")
        self.assertEqual((st, r["error"]), (409, "It is not installed."))
        self.assertFalse(self.c.get("/api/browser")[1]["available"])


class Pictures(BrowserApiBase):
    def test_a_single_picture_and_waiting_for_a_newer_one(self):
        st, hd, body = self.c.raw("GET", "/api/browser/quillbot/frame", headers={"X-Requested-With": "ols"})
        self.assertEqual((st, hd["content-type"], hd["x-seq"], body), (200, "image/jpeg", "0", JPEG_A))
        st, hd, body = self.c.raw("GET", "/api/browser/quillbot/frame?after=0")
        self.assertEqual((st, body, hd["x-seq"]), (200, JPEG_B, "1"))
        st, hd, body = self.c.raw("GET", "/api/browser/quillbot/frame?after=1&wait=0.2")
        self.assertEqual((st, body), (204, b""))
        self.assertEqual(self.c.raw("GET", "/api/browser/quillbot/frame?after=x")[0], 400)

    def test_head_is_refused_and_no_stream_exists(self):
        self.assertEqual(self.c.raw("HEAD", "/api/browser/quillbot/frame")[0], 405)
        self.assertEqual(self.c.raw("GET", "/api/browser/quillbot/stream")[0], 404)

    def test_asking_for_pictures_does_not_keep_the_app_awake(self):
        self.srv.app.last_activity = time.time() - 600
        before = self.srv.app.last_activity
        self.c.raw("GET", "/api/browser/quillbot/frame?after=0&wait=0.1", headers={"X-Poll": "1"})
        self.assertEqual(self.srv.app.last_activity, before, "viewing a pane is not using the app: it still closes when idle")
        self.c.post("/api/browser/quillbot/input", {"events": []})
        self.assertGreater(self.srv.app.last_activity, before, "clicking or typing in a pane is")


class SavedSignIn(BrowserApiBase):
    def file(self):
        return config.data_dir() / "sites.json"

    def test_saved_with_private_permissions_and_never_sent_back(self):
        st, r = self.c.post("/api/secrets/site", {"site": "quillbot", "email": "me@example.org", "password": "my secret pass"})
        self.assertEqual(st, 200, r)
        self.assertEqual(stat.S_IMODE(self.file().stat().st_mode), 0o600)
        self.assertEqual(common.load("sites.json"), {"quillbot": {"email": "me@example.org", "password": "my secret pass"}})
        for st, body in (self.c.post("/api/secrets/site", {"site": "quillbot", "email": "me@example.org"}), self.c.get("/api/connections"), self.c.get("/api/browser")):
            self.assertNotIn("my secret pass", str(body))
        conn = self.c.get("/api/connections")[1]
        self.assertEqual(conn["sites"]["quillbot"], {"email": "me@example.org", "password": True})
        self.assertEqual(conn["sites"]["overleaf"], {"email": "", "password": False})

    def test_changing_only_the_email_keeps_the_password_and_clearing_removes_it(self):
        self.c.post("/api/secrets/site", {"site": "overleaf", "email": "a@b.org", "password": "p1"})
        self.c.post("/api/secrets/site", {"site": "overleaf", "email": "c@d.org"})
        self.assertEqual(common.load("sites.json")["overleaf"], {"email": "c@d.org", "password": "p1"})
        self.c.post("/api/secrets/site", {"site": "overleaf", "clear": True})
        self.assertNotIn("overleaf", common.load("sites.json"))

    def test_bad_input_is_refused(self):
        post = lambda b: self.c.post("/api/secrets/site", b)[0]  # noqa: E731
        self.assertEqual(post({"site": "gmail", "email": "a@b.org", "password": "x"}), 400)
        self.assertEqual(post({"site": "quillbot", "email": "", "password": "x"}), 400)
        self.assertEqual(post({"site": "quillbot", "email": "has space@b.org", "password": "x"}), 400)
        self.assertEqual(post({"site": "quillbot", "email": "a@b.org"}), 400)                      # no password yet, none saved
        self.assertEqual(post({"site": "quillbot", "email": "a@b.org", "password": "two\nlines"}), 400)
        self.assertEqual(post({"site": "quillbot", "email": "a@b.org", "password": "x" * 501}), 400)
        self.assertFalse(self.file().exists())

    def test_signing_in_fills_the_form_with_what_is_saved(self):
        st, r = self.c.post("/api/browser/quillbot/signin")
        self.assertEqual(st, 409)
        self.assertIn("Settings", r["error"])
        self.c.post("/api/secrets/site", {"site": "quillbot", "email": "me@example.org", "password": "pw pw"})
        st, r = self.c.post("/api/browser/quillbot/signin")
        self.assertEqual((st, r["filled"]), (200, ["email", "password"]))
        self.assertEqual(self.fake.calls[-1], ("signin", "quillbot", "me@example.org", "pw pw"))
        self.assertIn("Press the site's own Sign in button", r["message"])
        self.assertNotIn("pw pw", str(r))
        self.fake.filled = ["email"]
        self.assertIn("Continue", self.c.post("/api/browser/quillbot/signin")[1]["message"])
        self.fake.filled = []
        self.assertIn("could not find a sign-in form", self.c.post("/api/browser/quillbot/signin")[1]["message"])
        self.fake.filled, self.fake.failed = ["email"], ["password"]
        r = self.c.post("/api/browser/quillbot/signin")[1]
        self.assertIn("could not put your password into the page", r["message"])
        self.assertIn("Your email was filled in", r["message"])
        self.assertEqual(r["failed"], ["password"])
        self.assertNotIn("pw pw", str(r))

    def test_the_password_is_not_in_the_container_log_line_of_any_call(self):
        self.c.post("/api/secrets/site", {"site": "quillbot", "email": "me@example.org", "password": "TOPSECRET-123"})
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.c.post("/api/browser/quillbot/signin")
            self.c.get("/api/connections")
        self.assertNotIn("TOPSECRET-123", buf.getvalue())


class PaneChoice(BrowserApiBase):
    def panes(self, **ui_settings):
        if ui_settings:
            self.c.post("/api/settings", {"ui": ui_settings})
        return self.c.get("/api/panes?paper=main&check=1")[1]

    def test_the_built_in_browser_is_used_when_it_is_there(self):
        r = self.panes()
        self.assertTrue(r["browser"]["available"])
        for k in ("quillbot", "overleaf"):
            self.assertEqual((r[k]["how"], r[k]["inside"]), ("browser", False))
        self.assertEqual(r["overleaf"]["url"], "https://www.overleaf.com/project/6aa33c64db9c6e1c1dde1aa0")

    def test_the_author_can_still_choose_a_window_or_a_frame(self):
        self.assertEqual(self.panes(pane_mode="window")["quillbot"]["how"], "window")
        with mock.patch.object(ui, "_probe_many", return_value={}):
            self.assertEqual(self.panes(pane_mode="inside")["quillbot"]["how"], "frame")
        self.assertEqual(self.panes(pane_mode="browser")["quillbot"]["how"], "browser")
        self.assertEqual(self.c.post("/api/settings", {"ui": {"pane_mode": "teleport"}})[0], 400)

    def test_one_site_can_be_sent_to_a_window_of_the_authors_own_browser(self):
        self.assertEqual(self.c.get("/api/connections?paper=main")[1]["panes"]["builtin"], {"quillbot": True, "overleaf": True})
        self.assertEqual(self.c.post("/api/settings", {"ui": {"builtin_quillbot": False}})[0], 200)
        r = self.panes()
        self.assertEqual((r["quillbot"]["how"], r["overleaf"]["how"]), ("window", "browser"))
        self.assertIn("window of your own browser", r["quillbot"]["reason"])
        self.assertEqual(r["quillbot"]["url"], "https://quillbot.com/paraphrasing-tool", "the window opens the same page")
        self.assertEqual(self.c.get("/api/connections?paper=main")[1]["panes"]["builtin"], {"quillbot": False, "overleaf": True})
        self.c.post("/api/settings", {"ui": {"builtin_quillbot": True}})
        self.assertEqual(self.panes()["quillbot"]["how"], "browser")

    def test_without_the_browser_the_old_behaviour_remains(self):
        self.srv.app._browser = webview.Unavailable("It is not installed.")
        with mock.patch.object(ui, "_probe_many", return_value={}):
            r = self.panes(pane_mode="auto")
        self.assertFalse(r["browser"]["available"])
        self.assertEqual(r["quillbot"]["how"], "window")
        r = self.panes(pane_mode="browser")
        self.assertEqual(r["quillbot"]["how"], "window")
        self.assertIn("It is not installed.", r["quillbot"]["reason"])


@unittest.skipUnless(can_run(), "Playwright and a Chromium are not installed here")
class WithARealBrowser(UiBase):
    def setUp(self):
        super().setUp()
        self.site = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=self.site.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.site.server_address[1]}/"
        self.browsers = webview.Browsers(self.tmp / "profiles", exe=chromium(), allow_private=True)
        self.srv.app._browser = self.browsers
        self.patch = mock.patch("writing_core.panes.quillbot_url", lambda _ui: self.url)
        self.patch.start()
        self.c.login()
        Site.log.clear()

    def tearDown(self):
        self.patch.stop()
        self.browsers.close_all()
        self.site.shutdown()
        super().tearDown()

    def test_a_page_that_refuses_framing_is_usable_through_the_app(self):
        st, r = self.c.post("/api/browser/quillbot/open", {"w": 1100, "h": 700})
        self.assertEqual((st, r["title"]), (200, "Stand-in site"), r)
        st, hd, body = self.c.raw("GET", "/api/browser/quillbot/frame?wait=8")
        self.assertEqual((st, body[:2]), (200, b"\xff\xd8"))
        self.c.post("/api/browser/quillbot/input", {"events": [{"t": "down", "x": 60, "y": 95}, {"t": "up", "x": 60, "y": 95}, {"t": "type", "text": "from the app"},
                                                              {"t": "down", "x": 60, "y": 280}, {"t": "up", "x": 60, "y": 280}]})
        self.assertEqual(wait_for(lambda: Site.log[-1] if Site.log else None), {"a": "from the app"})
        self.c.post("/api/browser/quillbot/input", {"events": [{"t": "down", "x": 60, "y": 95}, {"t": "up", "x": 60, "y": 95}, {"t": "key", "key": "a", "mods": ["Control"]}]})
        self.assertEqual(self.c.post("/api/browser/quillbot/selection")[1]["text"], "from the app")
        self.assertTrue(self.c.get("/api/browser")[1]["sites"]["quillbot"]["running"])


if __name__ == "__main__":
    unittest.main()
