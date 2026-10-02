"""The real-browser mode: an ordinary Chromium on a virtual screen, operated through X events, against the same stand-in site as test_webview.

Skipped where Xvfb, xclip, python-xlib, Pillow, websocket-client or a Chromium are missing (OLS_REAL_CHROMIUM can point at a Chromium binary).
"""
import io
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import tests.helpers  # noqa: F401  (puts scripts/ on sys.path)
from tests.test_webview import Site, wait_for, webview_processes
from writing_core import webview

os.environ.setdefault("OLS_REAL_CHROMIUM", "/opt/pw-browsers/chromium")


def can_run():
    from writing_core import xsession
    return xsession.available()[0]


@unittest.skipUnless(can_run(), "Xvfb, xclip, python-xlib, Pillow, websocket-client and a Chromium are needed")
class RealBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/"
        cls.tmp = Path(tempfile.mkdtemp())
        cls.b = webview.Browsers(cls.tmp, allow_private=True, real_sites=("quillbot", "overleaf"))

    @classmethod
    def tearDownClass(cls):
        cls.b.close_all()
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        Site.log.clear()
        Site.posts.clear()

    # the browser has its own toolbar above the page, so a point of the page is lower on the screen by the height of that toolbar
    def top(self, site="quillbot"):
        return int(self.b.sessions[site]._js("() => window.outerHeight - window.innerHeight") or 0)

    def click(self, x, y, site="quillbot"):
        y += self.top(site)
        self.b.input(site, [{"t": "down", "x": x, "y": y}, {"t": "up", "x": x, "y": y}])

    def open(self, path="", site="quillbot"):
        return self.b.open(site, self.url + path, 1100, 700, force=True)

    def test_it_is_an_ordinary_browser_not_a_scripted_one(self):
        st = self.open()
        self.assertEqual((st["kind"], st["title"]), ("real", "Stand-in site"))
        s = self.b.sessions["quillbot"]
        self.assertIs(s._js("() => navigator.webdriver"), False)
        cmd = open(f"/proc/{s.chrome.pid}/cmdline", "rb").read().decode().replace("\0", " ")
        for marker in ("--enable-automation", "--remote-debugging-pipe", "--headless", "playwright"):
            self.assertNotIn(marker, cmd, marker)
        self.assertIn("--remote-debugging-port=0", cmd, "the debugging port is used only for small conveniences, one short connection at a time")
        self.assertTrue(s.display.startswith(":"))

    def test_clicks_typing_and_paste_reach_the_page(self):
        self.open()
        self.click(60, 95)
        self.b.input("quillbot", [{"t": "type", "text": "Hello, World 42! (Ok)"}])
        self.click(60, 180)
        self.b.input("quillbot", [{"t": "text", "text": "pasted \\cite{x}\nline two é"}])
        self.click(60, 280)                                                      # Go
        got = wait_for(lambda: Site.log[-1] if Site.log else None)
        self.assertEqual(got, {"a": "Hello, World 42! (Ok)", "b": "pasted \\cite{x}\nline two é"})

    def test_named_keys_and_shortcuts(self):
        self.open()
        self.click(60, 180)
        self.b.input("quillbot", [{"t": "type", "text": "abc"}, {"t": "key", "key": "Enter"}, {"t": "type", "text": "def"}, {"t": "key", "key": "ArrowLeft"},
                                  {"t": "key", "key": "Backspace"}, {"t": "key", "key": "a", "mods": ["Control"]}])
        self.assertEqual(self.b.selection("quillbot")["text"], "abc\ndf")

    def test_what_is_selected_and_what_the_page_copied_can_be_read(self):
        self.open()
        self.click(60, 180)
        self.b.input("quillbot", [{"t": "type", "text": "copy me"}, {"t": "key", "key": "a", "mods": ["Control"]}])
        self.assertEqual(self.b.selection("quillbot")["text"], "copy me")
        self.click(380, 280)                                                      # the page's own Copy button
        self.assertEqual(wait_for(lambda: self.b.clipboard("quillbot")["text"]), "copy me")

    def test_a_suggestion_goes_into_the_biggest_writing_box_and_replaces_what_was_there(self):
        self.open()
        self.b.insert("quillbot", "first suggestion")
        self.b.insert("quillbot", "second suggestion")                          # the cursor is in the box now: goes in at the cursor
        self.click(60, 280)
        self.assertEqual(wait_for(lambda: Site.log[-1] if Site.log else None), {"b": "first suggestionsecond suggestion"})
        self.open()
        self.b.insert("quillbot", "replaced")
        Site.log.clear()
        self.click(60, 280)
        self.assertEqual(wait_for(lambda: Site.log[-1] if Site.log else None), {"b": "replaced"})

    def test_the_sign_in_is_typed_in_and_not_submitted(self):
        self.open("login", site="overleaf")
        r = self.b.signin("overleaf", "me@example.org", "s3cret Pass!")
        self.assertEqual((r["filled"], r["failed"]), (["email", "password"], []))
        time.sleep(0.4)
        self.assertEqual(Site.posts, [])
        self.b.input("overleaf", [{"t": "key", "key": "Enter"}])
        self.assertEqual(wait_for(lambda: Site.posts[-1] if Site.posts else None), {"email": "me@example.org", "password": "s3cret Pass!"})

    def test_a_page_that_wipes_typed_text_still_gets_the_sign_in(self):
        self.open("picky", site="overleaf")
        r = self.b.signin("overleaf", "me@example.org", "pw one")
        self.assertEqual((r["filled"], r["failed"]), (["email", "password"], []))
        self.b.input("overleaf", [{"t": "key", "key": "Enter"}])
        self.assertEqual(wait_for(lambda: Site.posts[-1] if Site.posts else None), {"email": "me@example.org", "password": "pw one"})

    def test_back_forward_reload_stop_and_home(self):
        self.open()
        self.b.next_frame("quillbot", -1, 2)                                      # somebody is looking, so the address is kept up to date
        self.click(210, 330)                                                      # the link to /challenge
        self.assertTrue(wait_for(lambda: self.b.state("quillbot")["url"].endswith("/challenge")))
        self.assertFalse(self.b.nav("quillbot", "back")["url"].endswith("/challenge"))
        self.assertTrue(wait_for(lambda: self.b.nav("quillbot", "forward")["url"].endswith("/challenge")))
        self.assertTrue(self.b.nav("quillbot", "reload")["running"])
        self.assertTrue(self.b.nav("quillbot", "stop")["running"])
        self.assertEqual(self.open()["title"], "Stand-in site")

    def test_the_picture_follows_the_size_of_the_pane(self):
        from PIL import Image
        self.open()
        seq, jpeg = self.b.next_frame("quillbot", -1, 8)
        self.assertEqual(Image.open(io.BytesIO(jpeg)).size, (1100, 700))
        self.b.resize("quillbot", 1300, 900)
        def latest_size():
            _, j = self.b.next_frame("quillbot", -1, 2)
            return Image.open(io.BytesIO(j)).size
        self.assertTrue(wait_for(lambda: latest_size() == (1300, 900), 10), "the picture is the new size")
        self.assertEqual(self.top() > 0 and self.b.sessions["quillbot"]._js("() => window.innerWidth"), 1300, "and so is the browser window")
        self.b.resize("quillbot", 1100, 700)

    def test_a_human_check_on_the_page_is_noticed(self):
        self.open("challenge")
        self.b.next_frame("quillbot", -1, 3)
        self.assertTrue(wait_for(lambda: self.b.state("quillbot")["challenge"], 8))
        self.open()
        self.b.next_frame("quillbot", -1, 3)
        self.assertTrue(wait_for(lambda: not self.b.state("quillbot")["challenge"], 8))

    def test_the_sign_in_survives_closing_the_browser(self):
        self.open(site="overleaf")
        self.b.close("overleaf")
        self.assertFalse(self.b.state("overleaf")["running"])
        self.assertFalse(webview_processes(str(self.tmp / "overleaf")), "the browser and its screen are gone")
        Site.cookies.clear()
        self.open(site="overleaf")
        self.assertTrue(wait_for(lambda: any("session=abc123" in c for c in Site.cookies)), "the cookie was kept in the profile folder")

    def test_a_browser_that_stops_answering_is_replaced(self):
        from writing_core import xsession
        old_stuck, old_clip = webview.STUCK_AFTER, xsession.XSession._do_clipboard
        webview.STUCK_AFTER = 3
        xsession.XSession._do_clipboard = lambda self: time.sleep(40)
        try:
            b = webview.Browsers(self.tmp / "stuck", allow_private=True, real_sites=("quillbot",))
            b.open("quillbot", self.url, 1100, 700, force=True)
            with self.assertRaises(webview.BrowserError) as cm:
                b.clipboard("quillbot")
            self.assertIn("stopped responding", str(cm.exception))
            self.assertTrue(wait_for(lambda: not webview_processes(str(self.tmp / "stuck" / "quillbot")), 8))
            xsession.XSession._do_clipboard = old_clip
            self.assertTrue(b.open("quillbot", self.url, 1100, 700, force=True)["running"])
            b.close_all()
        finally:
            webview.STUCK_AFTER, xsession.XSession._do_clipboard = old_stuck, old_clip

    def test_a_closed_browser_window_is_noticed_and_started_again(self):
        b = webview.Browsers(self.tmp / "closed", allow_private=True, real_sites=("quillbot",))
        b.open("quillbot", self.url, 1100, 700, force=True)
        b.sessions["quillbot"].chrome.kill()
        self.assertTrue(wait_for(lambda: not b.state("quillbot")["running"], 8))
        self.assertTrue(b.open("quillbot", self.url, 1100, 700, force=True)["running"])
        b.close_all()

    def test_only_the_sites_asked_for_use_it(self):
        scripted = webview.Browsers(self.tmp / "mixed", allow_private=True, exe="/opt/pw-browsers/chromium", real_sites=("overleaf",))
        self.assertEqual(scripted.open("quillbot", self.url, 1100, 700, force=True)["kind"], "scripted")
        self.assertEqual(scripted.open("overleaf", self.url, 1100, 700, force=True)["kind"], "real")
        scripted.close_all()

    def test_without_the_tools_the_scripted_browser_is_used(self):
        from unittest import mock
        from writing_core import xsession
        with mock.patch.object(xsession, "available", return_value=(False, "needs Xvfb")):
            b = webview.Browsers(self.tmp / "fallback", allow_private=True, exe="/opt/pw-browsers/chromium", real_sites=("quillbot",))
            try:
                self.assertEqual(b.open("quillbot", self.url, 1100, 700, force=True)["kind"], "scripted")
            finally:
                b.close_all()


if __name__ == "__main__":
    unittest.main()
