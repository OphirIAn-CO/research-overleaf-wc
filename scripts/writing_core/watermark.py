"""Client for the step-6b "watermarks-remover" service. FAILS CLOSED: any surprise raises WatermarkError and the caller blocks the edit.

SKILL.md says to run that skill's Layer A clean on new prose before it goes to Overleaf, via its /inspect and /clean endpoints on a
running service. That skill is not part of this repo and its exact API has not been verified here, so this client assumes the most
plain shape and refuses to proceed on anything else:
    POST {url}/inspect  {"text": ...}              -> any JSON object (proves the service speaks this protocol)
    POST {url}/clean    {"text": ..., "layer": "A"} -> JSON object with the cleaned string under "text" (or "cleaned" / "clean_text")
If your service differs, adjust only `_CLEAN_KEYS` / `_post` below. Until it is confirmed, Apply in the web app stays blocked and
"Hand to Claude" is the path (Claude applies the hunks and runs the skill itself).
"""
import json
import urllib.error
import urllib.request

_CLEAN_KEYS = ("text", "cleaned", "clean_text")
TIMEOUT = 30


class WatermarkError(Exception):
    pass


def _post(url, path, payload):
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        raise WatermarkError(f"the watermark service at {url} did not answer {path} as expected ({getattr(e, 'reason', e)})") from None
    if not isinstance(data, dict):
        raise WatermarkError(f"unexpected answer from {path}")
    return data


def make_cleaner(url):
    """-> callable(text) -> cleaned text. Never returns the input unchanged on failure: it raises."""
    if not url:
        raise WatermarkError("no watermark service URL is configured (Settings)")

    def clean(text):
        _post(url, "/inspect", {"text": text})
        data = _post(url, "/clean", {"text": text, "layer": "A"})
        for k in _CLEAN_KEYS:
            if isinstance(data.get(k), str) and data[k].strip():
                return data[k]
        raise WatermarkError("the service's /clean answer has no cleaned text in a known field")

    return clean


def check(url):
    """('ok'|'not configured'|'unreachable', detail) for the Settings page."""
    if not url:
        return "not configured", "Set the service URL in Settings (the service from the watermarks-remover skill)."
    try:
        make_cleaner(url)("Probe sentence for the service check.")
        return "ok", "reachable and answering in the expected shape"
    except WatermarkError as e:
        return "unreachable", str(e)
