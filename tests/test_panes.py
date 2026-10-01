import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.helpers import common, make_paper
from tests.test_ui_server import UiBase
from writing_core import panes


class Site:
    """A page that answers with the given headers, standing in for QuillBot / Overleaf."""

    def __init__(self, headers, status=200):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Decide(unittest.TestCase):
    def test_header_combinations(self):
        self.assertEqual(panes.decide(None, None)[0], True)
        self.assertEqual(panes.decide("DENY", None)[0], False)
        self.assertEqual(panes.decide("SAMEORIGIN", None)[0], False)
        self.assertIsNone(panes.decide("ALLOW-FROM https://x.example", None)[0])
        self.assertEqual(panes.decide(None, ["'none'"])[0], False)
        self.assertEqual(panes.decide(None, ["'self'", "https://quillbot.com"])[0], False)
        self.assertEqual(panes.decide(None, ["*"])[0], True)
        self.assertEqual(panes.decide(None, ["http://127.0.0.1:*"])[0], True)
        # frame-ancestors wins over X-Frame-Options, as in current browsers
        self.assertEqual(panes.decide("DENY", ["*"])[0], True)
        self.assertEqual(panes.decide("SAMEORIGIN", ["'none'"])[0], False)

    def test_frame_ancestors_is_found_among_other_directives_and_policies(self):
        self.assertEqual(panes.frame_ancestors(["default-src 'self'; frame-ancestors 'self' https://a.example; img-src *"]), ["'self'", "https://a.example"])
        self.assertEqual(panes.frame_ancestors(["script-src 'self'", "frame-ancestors 'none'"]), ["'none'"])
        self.assertIsNone(panes.frame_ancestors(["default-src 'self'"]))
        self.assertIsNone(panes.frame_ancestors([]))


class Probe(unittest.TestCase):
    def test_reads_the_headers_of_a_page(self):
        s = Site([("X-Frame-Options", "SAMEORIGIN")])
        try:
            r = panes.probe_any(s.url)
        finally:
            s.close()
        self.assertEqual((r["status"], r["xfo"], r["embeddable"]), (200, "SAMEORIGIN", False))
        self.assertIn("X-Frame-Options", r["why"])

    def test_a_page_without_restrictions_is_embeddable_and_an_error_page_still_counts(self):
        s = Site([], status=404)
        try:
            r = panes.probe_any(s.url)
        finally:
            s.close()
        self.assertEqual((r["status"], r["embeddable"]), (404, True))

    def test_an_unreachable_site_is_reported_not_raised(self):
        r = panes.probe_any("http://127.0.0.1:1/", timeout=2)
        self.assertIn("could not reach", r["error"])

    def test_only_the_two_fixed_addresses_can_be_probed(self):
        for bad in ("http://127.0.0.1:8000/", "https://evil.example/", "https://www.overleaf.com/project/../../x", "https://www.overleaf.com/login",
                    "file:///etc/passwd", "https://quillbot.com.evil.example/", "https://127.0.0.1/project/6aa33c640000000000000000"):
            with self.assertRaises(ValueError, msg=bad):
                panes.probe(bad)


class AddressRules(unittest.TestCase):
    def test_quillbot_addresses(self):
        for ok in ("https://quillbot.com/paraphrasing-tool", "https://quillbot.com/", "https://app.quillbot.com/x"):
            self.assertTrue(panes.valid_quillbot(ok), ok)
        for bad in ("http://quillbot.com/", "https://quillbot.com.evil.example/", "https://evil.example/quillbot.com", "https://user:pw@quillbot.com/", "javascript:alert(1)",
                    "https://notquillbot.com/", "", None, "https://quillbot.com/" + "a" * 400):
            self.assertFalse(panes.valid_quillbot(bad), bad)

    def test_overleaf_project_addresses(self):
        for ok in ("https://www.overleaf.com/project/6aa33c640000000000000000", "https://latex.university.edu/project/6aa33c64000000000000/"):
            self.assertTrue(panes.valid_overleaf(ok), ok)
        for bad in ("http://www.overleaf.com/project/6aa33c640000000000000000", "https://www.overleaf.com/login", "https://www.overleaf.com/project/../x",
                    "https://127.0.0.1/project/6aa33c640000000000000000", "https://localhost/project/6aa33c640000000000000000", "https://intranet.local/project/6aa33c640000000000000000",
                    "https://[::1]/project/6aa33c640000000000000000", "https://www.overleaf.com/project/6aa33c640000000000000000?x=1", "https://nodot/project/6aa33c640000000000000000"):
            self.assertFalse(panes.valid_overleaf(bad), bad)

    def test_the_authors_own_address_wins_over_the_one_built_from_the_git_address(self):
        pid = "6aa33c64" + "0" * 16
        proj = {"remote": f"https://git.overleaf.com/{pid}"}
        self.assertEqual(panes.overleaf_url(proj), f"https://www.overleaf.com/project/{pid}")
        mine = "https://latex.university.edu/project/abcdef0123456789"
        self.assertEqual(panes.overleaf_url(proj, mine), mine)
        self.assertEqual(panes.overleaf_url(proj, "https://evil.example/steal"), f"https://www.overleaf.com/project/{pid}")     # a bad override is ignored
        self.assertEqual(panes.quillbot_url({"quillbot_url": "https://evil.example/"}), panes.QUILLBOT_URL)

    def test_only_these_hosts_may_appear_inside_a_frame(self):
        self.assertTrue(panes.frameable_host("https://quillbot.com/paraphrasing-tool"))
        self.assertTrue(panes.frameable_host("https://www.overleaf.com/project/6aa33c640000000000000000"))
        self.assertFalse(panes.frameable_host("https://latex.university.edu/project/6aa33c640000000000000000"))     # not on the page's allow-list

    def test_the_decision_inside_or_window(self):
        ok = {"embeddable": True}
        no = {"embeddable": False, "why": "the site sends X-Frame-Options: DENY"}
        q = "https://quillbot.com/paraphrasing-tool"
        self.assertEqual(panes.decide_inside("auto", ok, q), (True, ""))
        inside, why = panes.decide_inside("auto", no, q)
        self.assertFalse(inside)
        self.assertIn("X-Frame-Options", why)
        self.assertIn("window beside the app", why)
        self.assertFalse(panes.decide_inside("auto", {"error": "could not reach it from here: boom"}, q)[0])
        self.assertFalse(panes.decide_inside("auto", None, q)[0])
        self.assertFalse(panes.decide_inside("window", ok, q)[0])
        self.assertTrue(panes.decide_inside("inside", no, q)[0])                                    # the author asked to try
        self.assertFalse(panes.decide_inside("inside", ok, "https://latex.university.edu/project/6aa33c640000000000000000")[0])


class Addresses(unittest.TestCase):
    def test_overleaf_project_url_comes_from_the_git_address(self):
        pid = "6aa33c64" + "0" * 16
        self.assertEqual(panes.overleaf_url({"remote": f"https://git.overleaf.com/{pid}"}), f"https://www.overleaf.com/project/{pid}")
        self.assertEqual(panes.overleaf_url({"remote": f"https://git.overleaf.com/{pid}/"}), f"https://www.overleaf.com/project/{pid}")
        self.assertIsNone(panes.overleaf_url({"remote": "https://git.overleaf.com/not-an-id"}))
        self.assertIsNone(panes.overleaf_url({}))


class Api(UiBase):
    def setUp(self):
        super().setUp()
        self.pid = "6aa33c64" + "0" * 16
        p = make_paper(self.tmp / "papers", "alpha", "\\documentclass{article}\\begin{document}Hi\\end{document}\n", remote=f"https://git.overleaf.com/{self.pid}")
        common.register(str(p), "alpha")
        self.c.login()

    def test_panes_endpoint_and_csp(self):
        st, r = self.c.get("/api/panes?paper=alpha")
        self.assertEqual(st, 200, r)
        self.assertEqual(r["mode"], "auto")
        self.assertEqual(r["quillbot"]["url"], "https://quillbot.com/paraphrasing-tool")
        self.assertEqual(r["overleaf"]["url"], f"https://www.overleaf.com/project/{self.pid}")
        self.assertFalse(r["quillbot"]["inside"])                                  # nobody has asked the sites yet: nothing is framed on a guess
        self.assertEqual(self.c.get("/api/panes?paper=nope")[0], 404)

    def stub_probe(self, results):
        calls = []
        orig = panes.probe
        panes.probe = lambda url, timeout=8: calls.append(url) or results(url)
        self.addCleanup(setattr, panes, "probe", orig)
        ui_mod = __import__("tests.test_ui_server", fromlist=["ui"]).ui
        ui_mod._PROBES.clear()
        return calls

    def test_checking_decides_per_site_and_remembers_the_answer(self):
        calls = self.stub_probe(lambda u: {"url": u, "embeddable": "quillbot" in u, "why": "the site sends X-Frame-Options: SAMEORIGIN"})
        st, r = self.c.get("/api/panes?paper=alpha&check=1")
        self.assertEqual((r["quillbot"]["inside"], r["overleaf"]["inside"]), (True, False))
        self.assertIn("SAMEORIGIN", r["overleaf"]["reason"])
        self.assertEqual(len(calls), 2)
        self.c.get("/api/panes?paper=alpha&check=1")
        self.assertEqual(len(calls), 2)                                            # cached for a few minutes: no new request to the sites

    def test_the_modes_override_the_check(self):
        calls = self.stub_probe(lambda u: {"url": u, "embeddable": False, "why": "no"})
        self.assertEqual(self.c.post("/api/settings", {"ui": {"pane_mode": "window"}})[0], 200)
        r = self.c.get("/api/panes?paper=alpha&check=1")[1]
        self.assertEqual((r["mode"], r["quillbot"]["inside"], calls), ("window", False, []))        # in window mode the sites are not even asked
        self.c.post("/api/settings", {"ui": {"pane_mode": "inside"}})
        r = self.c.get("/api/panes?paper=alpha&check=1")[1]
        self.assertEqual((r["quillbot"]["inside"], r["overleaf"]["inside"]), (True, True))
        self.assertEqual(self.c.post("/api/settings", {"ui": {"pane_mode": "sideways"}})[0], 400)

    def test_the_addresses_can_be_set_in_settings_and_bad_ones_are_refused(self):
        self.assertEqual(self.c.post("/api/settings", {"ui": {"quillbot_url": "https://quillbot.com/grammar-check"}})[0], 200)
        mine = "https://latex.university.edu/project/abcdef0123456789"
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "overleaf_url": mine})[0], 200)
        r = self.c.get("/api/panes?paper=alpha")[1]
        self.assertEqual((r["quillbot"]["url"], r["overleaf"]["url"]), ("https://quillbot.com/grammar-check", mine))
        c = self.c.get("/api/connections?paper=alpha")[1]["panes"]
        self.assertEqual((c["overleaf_override"], c["overleaf_derived"], c["quillbot_default"]), (mine, False, panes.QUILLBOT_URL))
        for body in ({"ui": {"quillbot_url": "https://evil.example/"}}, {"ui": {"quillbot_url": "http://quillbot.com/"}},
                     {"paper": "alpha", "overleaf_url": "https://www.overleaf.com/login"}, {"paper": "alpha", "overleaf_url": "http://127.0.0.1/project/abcdef0123456789"}):
            self.assertEqual(self.c.post("/api/settings", body)[0], 400, body)
        self.c.post("/api/settings", {"paper": "alpha", "overleaf_url": ""})                # empty = back to the address built from the Git address
        c = self.c.get("/api/connections?paper=alpha")[1]["panes"]
        self.assertEqual((c["overleaf_override"], c["overleaf_derived"], c["overleaf_url"]), ("", True, f"https://www.overleaf.com/project/{self.pid}"))

    def test_the_server_check_uses_the_probe(self):
        calls = []
        orig = panes.probe
        panes.probe = lambda url, timeout=8: calls.append(url) or {"url": url, "embeddable": False, "why": "stand-in"}
        try:
            st, r = self.c.post("/api/test/frames", {"paper": "alpha"})
        finally:
            panes.probe = orig
        self.assertEqual(st, 200, r)
        self.assertEqual(calls, ["https://quillbot.com/paraphrasing-tool", f"https://www.overleaf.com/project/{self.pid}"])
        self.assertEqual(r["quillbot"]["why"], "stand-in")


if __name__ == "__main__":
    unittest.main()
