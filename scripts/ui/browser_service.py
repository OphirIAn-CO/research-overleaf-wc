#!/usr/bin/env python3
"""The built-in browser, as its own small service (its own Docker container).

QuillBot and Overleaf pages run third-party code, so Chromium never runs in the web app's container: this container has no access to the
OpenAI key, the Overleaf token, the papers or the book. It holds only the two sign-in profile folders. The web app calls it over a private
Docker network with a shared token (`X-Browser-Token`); nothing is published on the computer's network.

  POST /call/<name>       JSON arguments -> {"result": ...} or {"error": "..."}   (status, open, resize, input, nav, selection, clipboard, insert, signin, state, close)
  GET  /frame/<site>      ?after=<seq>&wait=<seconds> -> the newest picture (JPEG, X-Seq header) once it differs from <seq>, else 204
  GET  /healthz           "ok"
"""
import argparse
import hmac
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from writing_core import config, webview  # noqa: E402

MAX_BODY = 2 << 20
CALLS = ("status", "open", "resize", "input", "nav", "selection", "clipboard", "insert", "signin", "state", "close")


class Handler(BaseHTTPRequestHandler):
    server_version = "OverleafBrowser/1"
    protocol_version = "HTTP/1.0"

    def log_request(self, code="-", size="-"):
        sys.stderr.write("%s %s -> %s\n" % (self.command, urlsplit(self.path).path, code))

    def log_message(self, fmt, *args):
        sys.stderr.write((fmt % args if args else fmt).split("?")[0] + "\n")

    def _send(self, status, body=b"", ctype="application/json", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authed(self):
        ok = hmac.compare_digest(self.headers.get("X-Browser-Token", "").encode(), self.server.token.encode())
        if ok:
            self.server.last_call = time.time()
        return ok

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        if not self._authed():
            return self._send(403, {"error": "wrong token"})
        parts = u.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "frame":
            q = parse_qs(u.query)
            try:
                after, wait = int(q.get("after", ["-1"])[0]), float(q.get("wait", ["10"])[0])
                seq, jpeg = self.server.browsers.next_frame(parts[1], after, wait)
            except (ValueError, webview.BrowserError) as e:
                return self._send(409, {"error": str(e)})
            return self._send(204) if jpeg is None else self._send(200, jpeg, "image/jpeg", {"X-Seq": str(seq)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(403, {"error": "wrong token"})
        parts = urlsplit(self.path).path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "call" or parts[1] not in CALLS:
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._send(413, {"error": "request too large"})
        try:
            args = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "body must be a JSON object"})
        try:
            return self._send(200, {"result": getattr(self.server.browsers, parts[1])(**args)})
        except webview.BrowserError as e:
            return self._send(409, {"error": str(e)})
        except TypeError:
            return self._send(400, {"error": "bad arguments"})
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"error in {parts[1]}: {e!r}\n")
            return self._send(500, {"error": "internal error (see the browser container log)"})


class Service(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, token, browsers):
        self.token, self.browsers = token, browsers
        self.last_call = time.time()
        super().__init__(addr, Handler)


def make_service(bind, port, token, browsers):
    if len(token) < 24:
        raise SystemExit("the token is empty or too short")
    if bind not in ("127.0.0.1", "localhost", "::1") and os.environ.get("OVERLEAF_UI_IN_DOCKER") != "1":
        raise SystemExit(f"refusing to listen on {bind}: outside Docker the browser service only binds to loopback")
    return Service((bind, port), token, browsers)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bind", default=os.environ.get("OLS_BROWSER_BIND", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("OLS_BROWSER_PORT", 9233)))
    ap.add_argument("--token-file", default=os.environ.get("OLS_BROWSER_TOKEN_FILE"))
    ap.add_argument("--profiles", default=os.environ.get("OLS_BROWSER_PROFILES"))
    ap.add_argument("--exit-after-minutes", type=int, default=int(os.environ.get("OLS_BROWSER_EXIT_MINUTES", 240)),
                    help="stop when the web app has not called for this long (0 = never); the container is then removed")
    args = ap.parse_args(argv)
    if not args.token_file:
        raise SystemExit("--token-file (or OLS_BROWSER_TOKEN_FILE) is required")
    home = os.environ.get("HOME")
    if home:
        os.makedirs(home, exist_ok=True)
    ok, why = webview.available()
    if not ok:
        raise SystemExit(why)
    real = tuple(x for x in os.environ.get("OLS_BROWSER_REAL", "").replace(" ", "").split(",") if x in webview.SITES)
    if real:
        from writing_core import xsession
        ok_real, why_real = xsession.available()
        print(("real browser for: " + ", ".join(real)) if ok_real else f"real browser not available ({why_real}); using the scripted one", file=sys.stderr, flush=True)
    browsers = webview.Browsers(Path(args.profiles) if args.profiles else config.data_dir() / "browser", real_sites=real)
    srv = make_service(args.bind, args.port, Path(args.token_file).read_text().strip(), browsers)
    print(f"browser service listening on {args.bind}:{srv.server_address[1]}", file=sys.stderr, flush=True)

    def stop(*_):
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    if args.exit_after_minutes > 0:
        def watchdog():
            while True:
                time.sleep(30)
                if time.time() - srv.last_call > args.exit_after_minutes * 60:
                    print("nobody has used the browser for a long time: stopping", file=sys.stderr, flush=True)
                    srv.shutdown()
                    return
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        browsers.close_all()
        srv.server_close()


if __name__ == "__main__":
    main()
