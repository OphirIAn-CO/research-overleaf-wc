"""Minimal OpenAI client on urllib (no SDK). Stateless: store=false, no conversation ids, one passage per call.

openai.json (in the data dir, created by you):
  {"api_key": "sk-...", "model": "<model id>",                       required; no model id is hardcoded anywhere
   "api": "responses" | "chat",                                      default "responses"; "chat" = /chat/completions (also what
                                                                     most OpenAI-compatible servers speak)
   "base_url": "https://api.openai.com/v1", "max_output_tokens": 2500, "timeout": 120,
   "json_mode": "schema" | "object" | "none",                        default "schema" (structured outputs)
   "organization": "...", "project": "...", "temperature": 0.2,      optional
   "extra_body": {...}}                                              optional, merged into the request (e.g. reasoning effort)

The key is only ever placed in the Authorization header and is scrubbed from every error message.
"""
import json
import re
import time
import urllib.error
import urllib.request

SLEEP = time.sleep  # replaced in tests
UA = "research-overleaf-wc/writing-coach"

REVISION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["revised_text", "changes", "questions"],
    "properties": {
        "revised_text": {"type": "string"},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["before", "after", "reason", "rule"],
            "properties": {"before": {"type": "string"}, "after": {"type": "string"},
                           "reason": {"type": "string"}, "rule": {"type": "string"}}}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
}


class OpenAIError(Exception):
    """kind: auth | not_found | rate | quota | server | network | bad_request | refusal | truncated | parse"""

    def __init__(self, msg, kind="error", status=None):
        super().__init__(msg)
        self.kind, self.status = kind, status


def _scrub(msg, cfg):
    key = cfg.get("api_key") or ""
    return msg.replace(key, "[key]") if key else msg


def build_request(cfg, system, user, schema=REVISION_SCHEMA, name="revision"):
    """-> (url, body). The body is exactly what is sent (and what a dry run shows)."""
    base = cfg["base_url"].rstrip("/")
    mode = cfg.get("json_mode", "schema")
    if cfg.get("api", "responses") == "chat":
        body = {"model": cfg["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_completion_tokens": cfg["max_output_tokens"], "store": False}
        if mode == "schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}
        elif mode == "object":
            body["response_format"] = {"type": "json_object"}
        url = base + "/chat/completions"
    else:
        body = {"model": cfg["model"], "instructions": system, "input": user, "max_output_tokens": cfg["max_output_tokens"], "store": False}
        if mode == "schema":
            body["text"] = {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}}
        elif mode == "object":
            body["text"] = {"format": {"type": "json_object"}}
        url = base + "/responses"
    if cfg.get("temperature") is not None:
        body["temperature"] = cfg["temperature"]
    body.update(cfg.get("extra_body") or {})
    return url, body


def _extract(cfg, data):
    """-> (text, usage dict {input_tokens, output_tokens})"""
    if cfg.get("api", "responses") == "chat":
        ch = (data.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        if msg.get("refusal"):
            raise OpenAIError("the model refused: " + str(msg["refusal"])[:300], "refusal")
        if ch.get("finish_reason") == "length":
            raise OpenAIError("the answer was cut off (raise max_output_tokens in openai.json or shorten the passage)", "truncated")
        u = data.get("usage") or {}
        return msg.get("content") or "", {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}
    if data.get("status") == "incomplete":
        raise OpenAIError("the answer was cut off (raise max_output_tokens in openai.json or shorten the passage)", "truncated")
    text = ""
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") == "refusal":
                    raise OpenAIError("the model refused: " + str(c.get("refusal"))[:300], "refusal")
                if c.get("type") == "output_text":
                    text += c.get("text", "")
    if not text and data.get("output_text"):
        text = data["output_text"]
    u = data.get("usage") or {}
    return text, {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}


def parse_json(text):
    """Strict JSON first; then tolerate a code fence or prose around one object."""
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except json.JSONDecodeError:
            pass
    raise OpenAIError("the model did not return valid JSON", "parse")


def _http_error(cfg, e):
    try:
        err = json.loads(e.read().decode("utf-8", "replace")).get("error") or {}
    except Exception:  # noqa: BLE001
        err = {}
    msg = _scrub(str(err.get("message") or e.reason or "HTTP error"), cfg)[:400]
    code = str(err.get("code") or "")
    if e.code in (401, 403):
        return OpenAIError(f"OpenAI rejected the API key or the project's access ({e.code}): {msg}", "auth", e.code)
    if e.code == 404:
        return OpenAIError(f"not found (is the model id in openai.json right?): {msg}", "not_found", e.code)
    if e.code == 429:
        kind = "quota" if "quota" in code or "quota" in msg.lower() else "rate"
        return OpenAIError(f"{'quota exhausted' if kind == 'quota' else 'rate limited'}: {msg}", kind, e.code)
    if e.code >= 500:
        return OpenAIError(f"OpenAI server error {e.code}: {msg}", "server", e.code)
    return OpenAIError(f"request rejected ({e.code}): {msg}", "bad_request", e.code)


def call(cfg, system, user, schema=REVISION_SCHEMA, retries=2):
    """One request (with retries on rate limit / 5xx / network). -> dict(text, data, usage, latency_ms, model, tries)"""
    url, body = build_request(cfg, system, user, schema)
    payload = json.dumps(body).encode("utf-8")
    headers = {"Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json", "User-Agent": UA}
    for k, h in (("organization", "OpenAI-Organization"), ("project", "OpenAI-Project")):
        if cfg.get(k):
            headers[h] = cfg[k]
    last = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
                data = json.loads(r.read().decode("utf-8"))
            text, usage = _extract(cfg, data)
            return {"text": text, "usage": usage, "latency_ms": round((time.time() - t0) * 1000), "model": data.get("model", cfg["model"]),
                    "tries": attempt + 1}
        except urllib.error.HTTPError as e:
            last = _http_error(cfg, e)
            if last.kind not in ("rate", "server"):
                raise last from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = OpenAIError("network error talking to OpenAI: " + _scrub(str(getattr(e, "reason", e)), cfg)[:200], "network")
        except json.JSONDecodeError:
            raise OpenAIError("OpenAI returned a response that is not JSON", "parse") from None
        if attempt < retries:
            SLEEP(2 * (attempt + 1))
    raise last
