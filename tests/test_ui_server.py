import http.client
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.fake_openai import FakeOpenAI
from tests.helpers import IsolatedHome, common, git, load_ui_server
from tests.test_book import FIXTURE, needs_pdf
from tests.test_paper import METHODS, paper
from tests.test_revise import reply
from writing_core import book_index, config, openai_client

ui = load_ui_server()
TOKEN = "t" * 40


class Client:
    def __init__(self, srv):
        self.port = srv.server_address[1]
        self.cookie = None

    def raw(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        if self.cookie and "Cookie" not in h:
            h["Cookie"] = self.cookie
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        if method == "POST":
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        c.close()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, payload

    def login(self):
        st, hd, _ = self.raw("GET", f"/?token={TOKEN}")
        assert st == 302, st
        self.cookie = hd["set-cookie"].split(";")[0]

    def api(self, method, path, body=None, headers=None):
        h = {"X-Requested-With": "ols", **(headers or {})}
        st, hd, payload = self.raw(method, path, body if method == "POST" else None, h)
        try:
            return st, json.loads(payload.decode() or "null")
        except ValueError:
            return st, payload

    def get(self, path):
        return self.api("GET", path)

    def post(self, path, body=None):
        return self.api("POST", path, body or {})


class UiBase(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.srv = ui.make_server("127.0.0.1", 0, TOKEN, idle_minutes=60)
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.c = Client(self.srv)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def wait_job(self, jid, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st, j = self.c.get(f"/api/jobs/{jid}")
            if j["status"] != "running":
                return j
            time.sleep(0.05)
        self.fail("job did not finish")


class Security(UiBase):
    def test_healthz_is_open_and_says_nothing(self):
        st, hd, body = self.c.raw("GET", "/healthz")
        self.assertEqual((st, body), (200, b"ok"))

    def test_nothing_else_is_reachable_without_signing_in(self):
        for path in ("/", "/app.js", "/style.css", "/api/state", "/api/monitor"):
            st, hd, _ = self.c.raw("GET", path)
            self.assertEqual(st, 401, path)
        st, hd, _ = self.c.raw("POST", "/api/settings", {}, {"X-Requested-With": "ols"})
        self.assertEqual(st, 401)

    def test_token_exchange_sets_a_strict_httponly_cookie_and_leaves_the_url(self):
        st, hd, _ = self.c.raw("GET", "/?token=wrong")
        self.assertEqual(st, 403)
        self.assertNotIn("set-cookie", hd)
        st, hd, _ = self.c.raw("GET", f"/?token={TOKEN}")
        self.assertEqual(st, 302)
        self.assertEqual(hd["location"], "/")  # the token does not stay in the address bar
        cookie = hd["set-cookie"]
        for part in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, cookie)
        self.assertNotIn(TOKEN, cookie)
        self.c.cookie = cookie.split(";")[0]
        self.assertEqual(self.c.raw("GET", "/")[0], 200)

    def test_every_response_carries_the_hardening_headers(self):
        self.c.login()
        for path in ("/", "/app.js", "/api/state", "/nope"):
            st, hd, _ = self.c.raw("GET", path)
            csp = hd["content-security-policy"]
            self.assertIn("default-src 'none'", csp, path)
            self.assertIn("script-src 'self'", csp)
            self.assertNotIn("unsafe-inline", csp)
            for directive in csp.split(";"):  # no external origin may be allowed, except to FRAME the two side-pane sites
                name, _, rest = directive.strip().partition(" ")
                if name == "frame-src":
                    self.assertEqual(set(rest.split()), {"https://quillbot.com", "https://*.quillbot.com", "https://www.overleaf.com"})
                else:
                    self.assertNotIn("http", directive)
            self.assertEqual(hd["x-content-type-options"], "nosniff")
            self.assertEqual(hd["cache-control"], "no-store")
            self.assertEqual(hd["x-frame-options"], "DENY")
            self.assertEqual(hd["referrer-policy"], "no-referrer")

    def test_a_forged_host_header_is_refused_even_when_signed_in(self):  # DNS rebinding
        self.c.login()
        for host in ("evil.example.com", f"evil.example.com:{self.c.port}", "127.0.0.1:1", f"localhost.evil.com:{self.c.port}"):
            self.assertEqual(self.c.raw("GET", "/api/state", host=host)[0], 403, host)
        self.assertEqual(self.c.raw("GET", "/api/state", host=f"localhost:{self.c.port}")[0], 200)

    def test_posts_need_the_custom_header_and_a_same_origin_origin(self):
        self.c.login()
        origin = f"http://127.0.0.1:{self.c.port}"
        self.assertEqual(self.c.raw("POST", "/api/settings", {})[0], 403)  # no X-Requested-With
        self.assertEqual(self.c.raw("POST", "/api/settings", {}, {"X-Requested-With": "ols", "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.c.raw("POST", "/api/settings", {}, {"X-Requested-With": "ols", "Origin": "null"})[0], 403)
        self.assertEqual(self.c.raw("POST", "/api/settings", {}, {"X-Requested-With": "ols", "Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.c.raw("POST", "/api/settings", {}, {"X-Requested-With": "ols", "Origin": origin})[0], 200)
        self.assertEqual(self.c.raw("POST", "/api/settings", {}, {"X-Requested-With": "ols", "Sec-Fetch-Site": "same-origin"})[0], 200)

    def test_body_limits_and_malformed_bodies(self):
        self.c.login()
        h = {"X-Requested-With": "ols"}
        try:  # the server refuses without reading the body, so the client may see 413 or a closed connection: both mean "refused"
            self.assertEqual(self.c.raw("POST", "/api/settings", b"x" * (ui.MAX_BODY + 1), h)[0], 413)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.assertEqual(self.c.raw("POST", "/api/settings", b"{not json", h)[0], 400)
        self.assertEqual(self.c.raw("POST", "/api/settings", b"[1, 2]", h)[0], 400)

    def test_other_methods_and_paths_are_refused(self):
        self.c.login()
        for m in ("PUT", "DELETE", "PATCH"):
            self.assertEqual(self.c.raw(m, "/api/state", None, {"X-Requested-With": "ols"})[0], 405, m)
        for path in ("/server.py", "/static/app.js", "/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/api/../server.py", "/index.html"):
            self.assertEqual(self.c.raw("GET", path)[0], 404, path)

    def test_outside_docker_it_refuses_to_listen_on_the_network(self):
        old = os.environ.pop("OVERLEAF_UI_IN_DOCKER", None)
        try:
            for bind in ("0.0.0.0", "192.168.1.5", ""):
                with self.assertRaises(SystemExit):
                    ui.make_server(bind, 0, TOKEN)
            os.environ["OVERLEAF_UI_IN_DOCKER"] = "1"
            s = ui.make_server("0.0.0.0", 0, TOKEN)
            s.server_close()
        finally:
            os.environ.pop("OVERLEAF_UI_IN_DOCKER", None)
            if old is not None:
                os.environ["OVERLEAF_UI_IN_DOCKER"] = old

    def test_the_access_token_never_reaches_the_log(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.c.login()
            self.c.raw("GET", "/?token=nope")
            self.c.get("/api/state")
        log = err.getvalue()
        self.assertNotIn(TOKEN, log)
        self.assertNotIn("token=", log)
        self.assertIn("GET /", log)

    def test_unexpected_errors_do_not_leak_details(self):
        self.c.login()
        orig = ui.api_health

        def boom(r):
            raise RuntimeError("secret internals sk-123 /home/user")
        ui.ROUTES[:] = [(m, rx, boom if fn is orig else fn) for m, rx, fn in ui.ROUTES]
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                st, body = self.c.get("/api/health")
            self.assertEqual(st, 500)
            self.assertNotIn("secret internals", json.dumps(body))
            self.assertIn("secret internals", err.getvalue())  # but the operator can see it in the container log
        finally:
            ui.ROUTES[:] = [(m, rx, orig if fn is boom else fn) for m, rx, fn in ui.ROUTES]

    def test_idle_shutdown_fires_and_polling_does_not_keep_it_alive(self):
        srv = ui.make_server("127.0.0.1", 0, TOKEN, idle_minutes=0.02)  # 1.2 s
        ui.IDLE_POLL = 0.1
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        t.start()
        threading.Thread(target=ui.idle_watch, args=(srv,), daemon=True).start()
        c = Client(srv)
        try:
            st, hd, _ = c.raw("GET", f"/?token={TOKEN}")
            c.cookie = hd["set-cookie"].split(";")[0]
            t0 = time.time()
            while t.is_alive() and time.time() - t0 < 8:
                c.raw("GET", "/api/state", None, {"X-Poll": "1"})  # polling must not count as activity
                time.sleep(0.1)
            self.assertFalse(t.is_alive(), "server should have shut itself down")
        finally:
            ui.IDLE_POLL = 15
            srv.server_close()

    def test_secrets_never_appear_in_api_answers(self):
        common.save("openai.json", {"api_key": "sk-SECRET-KEY-VALUE", "model": "m"})
        common.save("overleaf_git.json", {"token": "olp_SECRET_TOKEN"})
        self.c.login()
        blob = json.dumps([self.c.get(p)[1] for p in ("/api/state", "/api/health", "/api/monitor", "/api/events")])
        for secret in ("sk-SECRET-KEY-VALUE", "olp_SECRET_TOKEN", TOKEN):
            self.assertNotIn(secret, blob)
        st, state = self.c.get("/api/state")
        self.assertEqual((state["settings"]["openai"]["state"], state["settings"]["git_token"]), ("ok", True))


class FakeWatermark:
    """Stands in for the step-6b service: /inspect answers, /clean returns the text with a marker removed."""

    def __init__(self, broken=False):
        outer = self
        self.calls = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append((self.path, body))
                out = {"ok": True} if self.path == "/inspect" else {"text": body["text"].replace("ZWSP", "")}
                data = b"garbage" if broken else json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@needs_pdf
class Api(UiBase):
    def setUp(self):
        super().setUp()
        openai_client.SLEEP = lambda s: None
        self.oa = FakeOpenAI("responses")
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url})
        book_index.ingest(FIXTURE, "mini")
        self.name, self.proj = paper(self)
        self.methods = Path(self.proj["path"]) / "sections" / "methods.tex"
        self.c.login()

    def tearDown(self):
        self.oa.close()
        super().tearDown()

    def test_state_lists_papers_books_and_settings(self):
        st, s = self.c.get("/api/state")
        self.assertEqual(st, 200)
        self.assertEqual([p["name"] for p in s["papers"]], ["alpha"])
        self.assertIsNone(s["papers"][0]["problem"])
        self.assertTrue(s["papers"][0]["settings"]["dry_run"])
        self.assertEqual(s["books"][0]["id"], "mini")
        self.assertEqual(s["books"][0]["chunks"], 20)
        self.assertIsNone(s["papers"][0]["index"])  # listing never creates an index

    def test_choosing_a_paper_sets_the_shared_active_paper(self):
        self.assertEqual(self.c.post("/api/papers/active", {"name": "alpha"})[0], 200)
        self.assertEqual(common.get_active()[:2], ("alpha", "ui"))
        self.assertEqual(self.c.post("/api/papers/active", {"name": "nope"})[0], 404)
        self.assertEqual(self.c.post("/api/papers/active", {})[0], 400)

    def test_git_status_through_the_app_and_index_follows_a_pull(self):
        self.methods.write_text(METHODS + "\nMore words.\n")
        st, r = self.c.post("/api/papers/status", {"name": "alpha"})
        self.assertIn("methods.tex", r["output"])
        self.assertEqual(self.c.post("/api/papers/status", {"name": "nope"})[0], 404)

    def test_outline_refresh_and_chunks(self):
        st, o = self.c.get("/api/paper/alpha/outline")
        self.assertIn("Materials and Methods", [x["title"] for x in o["outline"]])
        st, r = self.c.post("/api/paper/alpha/refresh", {})
        self.assertEqual(r["unchanged"], r["total"])
        st, q = self.c.get("/api/paper/alpha/chunks?q=improve+my+methods")
        self.assertEqual((q["mode"], len(q["chunks"])), ("part", 2))
        self.assertEqual(self.c.get("/api/paper/zzz/outline")[0], 404)
        self.assertEqual(self.c.get("/api/paper/alpha/chunks?part=introdution")[0], 400)

    def test_book_pages_tree_chunk_query_outline(self):
        st, t = self.c.get("/api/book/mini/tree")
        self.assertEqual(len(t["tree"]), 6)
        self.assertEqual(t["info"]["n_chunks"], 20)
        st, ch = self.c.get("/api/book/mini/chunk?id=B:mini:c02.s03.k01")
        self.assertIn("statistical test", ch["text"])
        self.assertEqual(self.c.get("/api/book/mini/chunk?id=B:mini:c99.s00.k01")[0], 400)
        st, q = self.c.get("/api/book/mini/query?q=statistical+analysis+in+methods")
        self.assertEqual(q["chunks"][0]["id"], "B:mini:c02.s03.k01")
        self.assertEqual(self.c.get("/api/book/nope/tree")[0], 404)
        self.assertEqual(self.c.get("/api/book/..%2f..%2fx/tree")[0], 404)
        st, o = self.c.get("/api/book/mini/outline")
        o["entries"][0]["skip"] = True
        self.assertEqual(self.c.post("/api/book/mini/outline", {"outline": o})[1], {"saved": 21})
        self.assertTrue(book_index.read_outline("mini")["entries"][0]["skip"])
        self.assertEqual(self.c.post("/api/book/mini/outline", {"outline": {"entries": [{"title": "x"}]}})[0], 400)
        self.assertEqual(self.c.post("/api/book/mini/outline", {"outline": "nope"})[0], 400)

    def test_book_import_from_the_inbox_runs_as_a_job_and_is_idempotent(self):
        config.inbox_dir().mkdir(parents=True)
        shutil.copy(FIXTURE, config.inbox_dir() / "handbook.pdf")
        self.assertEqual(self.c.get("/api/state")[1]["inbox"], ["handbook.pdf"])
        for bad in ("../x.pdf", "nope.pdf", "handbook.txt", "/etc/passwd"):
            self.assertEqual(self.c.post("/api/book/ingest", {"pdf": bad, "id": "hb"})[0], 404, bad)
        self.assertEqual(self.c.post("/api/book/ingest", {"pdf": "handbook.pdf", "id": "../hb"})[0], 400)
        self.assertEqual(self.c.post("/api/book/ingest", {"pdf": "handbook.pdf", "id": "hb", "mode": "weird"})[0], 400)
        st, r = self.c.post("/api/book/ingest", {"pdf": "handbook.pdf", "id": "hb", "mode": "build"})
        j = self.wait_job(r["job"])
        self.assertEqual((j["status"], j["result"]["status"], j["result"]["chunks"]), ("done", "ingested", 20))
        st, r = self.c.post("/api/book/ingest", {"pdf": "handbook.pdf", "id": "hb"})
        j = self.wait_job(r["job"])
        self.assertEqual(j["result"]["status"], "skipped")  # once means once
        self.assertEqual(self.c.get("/api/jobs/aaaaaaaaaaaa")[0], 404)

    def test_the_whole_write_flow_preview_run_review_apply(self):
        wm = FakeWatermark()
        try:
            body = {"paper": "alpha", "instruction": "tighten my methods and follow the book", "part": "methods.data"}
            st, pv = self.c.post("/api/revise/preview", body)
            self.assertEqual((st, pv["status"]), (200, "preview"))
            self.assertIn("DRY RUN", pv["text"])
            self.assertEqual(self.oa.requests, [])
            self.oa.script = [reply(("We sampled 40 farms by stratified random sampling.", "We drew a stratified random sample of 40 farms."),
                                    ("Informed consent was obtained from all farmers.", "All farmers gave informed consent."))]
            st, r = self.c.post("/api/revise/run", {**body, "confirm": pv["payload_hash"]})
            j = self.wait_job(r["job"])
            self.assertEqual((j["status"], j["result"]["status"]), ("done", "proposed"))
            pid = j["result"]["proposal"]
            st, lst = self.c.get("/api/proposals?paper=alpha")
            self.assertEqual(lst["proposals"][0]["id"], pid)
            st, p = self.c.get(f"/api/proposal?paper=alpha&id={pid}")
            self.assertEqual(len(p["units"][0]["hunks"]), 2)
            self.assertNotIn("mapping", json.dumps(p))
            self.assertNotIn("segments", json.dumps(p))
            # without a configured cleaner nothing is written and the page is told it can hand over to Claude
            st, e = self.c.post("/api/proposal/apply", {"paper": "alpha", "id": pid, "accept": "all"})
            self.assertEqual((st, e["handoff_available"]), (409, True))
            self.assertIn("6b", e["error"])
            self.assertEqual(self.methods.read_text(), METHODS)
            # a service that does not answer in the expected shape also blocks (fail closed)
            bad = FakeWatermark(broken=True)
            try:
                self.assertEqual(self.c.post("/api/settings", {"ui": {"watermark_url": bad.url}})[0], 200)
                st, e = self.c.post("/api/proposal/apply", {"paper": "alpha", "id": pid, "accept": "all"})
                self.assertEqual((st, e["handoff_available"]), (409, True))
                self.assertIn("cleaner failed", e["error"])
                self.assertEqual(self.methods.read_text(), METHODS)
            finally:
                bad.close()
            self.assertEqual(self.c.post("/api/settings", {"ui": {"watermark_url": wm.url}})[0], 200)
            uids = [h["uid"] for h in p["units"][0]["hunks"]]
            st, a = self.c.post("/api/proposal/apply", {"paper": "alpha", "id": pid, "accept": [uids[0]]})
            self.assertEqual(st, 200, a)
            self.assertEqual(a["accepted"], [uids[0]])
            after = self.methods.read_text()
            self.assertIn("We drew a stratified random sample", after)
            self.assertIn("Informed consent was obtained from all farmers.", after)   # unchecked hunk left alone
            self.assertEqual([c[0] for c in wm.calls], ["/inspect", "/clean"])        # step 6b ran on the new text
            self.assertEqual(self.c.get(f"/api/proposal?paper=alpha&id={pid}")[1]["status"], "applied")
            st, h = self.c.get("/api/health")
            self.assertEqual(h["watermark"]["state"], "ok")
        finally:
            wm.close()

    def test_rejecting_and_bad_ids(self):
        self.assertEqual(self.c.get("/api/proposal?paper=alpha&id=../../x")[0], 400)
        self.assertEqual(self.c.get("/api/proposal?paper=alpha&id=20260101-000000-abcd")[0], 400)  # valid shape, no such file
        self.assertEqual(self.c.get("/api/proposals?paper=nope")[0], 404)

    def test_revise_run_with_a_stale_confirmation_is_refused(self):
        body = {"paper": "alpha", "instruction": "tighten my methods", "part": "methods"}
        st, r = self.c.post("/api/revise/run", {**body, "confirm": "deadbeef"})
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "error")
        self.assertIn("preview it again", j["error"])
        self.assertEqual(self.oa.requests, [])

    def test_monitor_events_health_and_settings(self):
        self.c.get("/api/paper/alpha/chunks?q=improve+my+methods")
        st, m = self.c.get("/api/monitor?days=30")
        self.assertEqual(st, 200)
        self.assertTrue(m["isolation"]["ok"])
        self.assertEqual(m["overview"]["papers_indexed"], 1)
        self.assertGreaterEqual(len(m["index_health"]), 1)
        st, m2 = self.c.get("/api/monitor?paper=alpha&days=7")
        self.assertEqual(m2["paper"], "alpha")
        self.assertEqual(self.c.get("/api/monitor?paper=nope")[0], 404)
        st, ev = self.c.get("/api/events")
        self.assertTrue(any(e["type"] == "retrieve" for e in ev["events"]))
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "dry_run": False})[0], 200)
        self.assertFalse(config.paper_settings(self.proj)["dry_run"])
        self.assertEqual(self.c.post("/api/settings", {"ui": {"idle_minutes": 90}})[0], 200)
        self.assertEqual(config.ui_cfg()["idle_minutes"], 90)
        self.assertEqual(self.c.post("/api/settings", {"ui": {"idle_minutes": "abc"}})[0], 400)
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "http://a b"):
            self.assertEqual(self.c.post("/api/settings", {"ui": {"watermark_url": bad}})[0], 400, bad)
        st, h = self.c.get("/api/health")
        self.assertTrue(any("overleaf_git.json" in i["text"] for i in h["issues"]))


if __name__ == "__main__":
    unittest.main()
