"""Optional NVIDIA embeddings for the literature library: hybrid search = BM25 (local) + vectors, fused by rank.

Key: env NVIDIA_API_KEY, or nvidia.json in the data dir: {"api_key": "nvapi-...", "model": "...", "dimensions": 512}.
No key -> everything stays BM25-only, exactly as before. Only library part text goes to NVIDIA (once per part; vectors are
cached in the library index), never the paper, never credentials.
"""
import json
import os
import urllib.request
from array import array

from . import config

URL = "https://integrate.api.nvidia.com/v1/embeddings"
DEFAULTS = {"model": "nvidia/llama-3.2-nv-embedqa-1b-v2", "dimensions": 512, "batch": 32, "timeout": 60, "url": URL}


def settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.loads((config.data_dir() / "nvidia.json").read_text()))
    except (OSError, ValueError):
        pass
    s["api_key"] = os.environ.get("NVIDIA_API_KEY") or s.get("api_key")
    return s


def available(s=None):
    return bool((s or settings()).get("api_key"))


def tag(s):
    """What the cached vectors were made with; a change means they must be made again."""
    return f"{s['model']}@{s.get('dimensions') or 'full'}"


def embed(texts, kind="passage", s=None):
    """texts -> list of float vectors. kind: 'passage' for library parts, 'query' for a search."""
    s = s or settings()
    body = {"input": texts, "model": s["model"], "input_type": kind, "encoding_format": "float", "truncate": "END"}
    if s.get("dimensions"):
        body["dimensions"] = s["dimensions"]
    req = urllib.request.Request(s["url"], data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + s["api_key"], "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=s["timeout"]) as r:
        data = json.loads(r.read())["data"]
    return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]


def pack(v):
    return array("f", v).tobytes()
