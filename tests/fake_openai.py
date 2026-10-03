"""A local stand-in for the OpenAI API: records every request and answers from a script. No network."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def passage_of(body):
    """The masked passage inside the user message of a recorded request body (either API style)."""
    from writing_core import openai_client
    user = openai_client.user_text(body)
    m = re.search(r"<passage[^>]*>\n(.*?)\n</passage>", user, re.S)
    return m.group(1) if m else ""


class FakeOpenAI:
    def __init__(self, api="responses"):
        self.api = api
        self.requests = []   # dicts: path, headers, body
        self.models_reply = (200, {"id": "fake-model", "object": "model"})   # what GET /models/<id> answers
        self.script = []     # callables(body) -> (status, payload_dict) OR str (the model's text) OR dict (revision JSON)
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": None, "method": "GET"})
                status, payload = outer.models_reply
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                step = outer.script.pop(0) if outer.script else (lambda b: {"revised_text": passage_of(b), "changes": [], "questions": []})
                out = step(body) if callable(step) else step
                status, payload = (out if isinstance(out, tuple) else (200, out))
                if isinstance(payload, (dict, list)) and not (isinstance(payload, dict) and ("output" in payload or "choices" in payload or "error" in payload)):
                    payload = outer.wrap(json.dumps(payload))
                elif isinstance(payload, str):
                    payload = outer.wrap(payload)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def wrap(self, text, in_tok=120, out_tok=60):
        if self.api == "chat":
            return {"model": "fake-model", "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok}}
        return {"model": "fake-model", "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                "usage": {"input_tokens": in_tok, "output_tokens": out_tok}}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
