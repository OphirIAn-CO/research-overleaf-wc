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
                    "file:///etc/passwd", "https://quillbot.com.evil.example/"):
            with self.assertRaises(ValueError, msg=bad):
                panes.probe(bad)


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
        self.assertEqual(r["quillbot"], "https://quillbot.com/")
        self.assertEqual(r["overleaf"], f"https://www.overleaf.com/project/{self.pid}")
        self.assertEqual(self.c.get("/api/panes?paper=nope")[0], 404)

    def test_the_server_check_uses_the_probe(self):
        calls = []
        orig = panes.probe
        panes.probe = lambda url, timeout=8: calls.append(url) or {"url": url, "embeddable": False, "why": "stand-in"}
        try:
            st, r = self.c.post("/api/test/frames", {"paper": "alpha"})
        finally:
            panes.probe = orig
        self.assertEqual(st, 200, r)
        self.assertEqual(calls, ["https://quillbot.com/", f"https://www.overleaf.com/project/{self.pid}"])
        self.assertEqual(r["quillbot"]["why"], "stand-in")


if __name__ == "__main__":
    unittest.main()
