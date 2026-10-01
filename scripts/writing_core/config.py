"""Paths and settings. Secrets and the book live in the data dir (never in git); per-paper state lives in
`<paper>/.overleaf-skill/`. Everything resolves through `common.HOME` at call time so tests can redirect it.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

OPENAI_DEFAULTS = {
    "base_url": "https://api.openai.com/v1",
    "max_output_tokens": 2500,
    "timeout": 120,
    "rule_budget_tokens": 2000,
    "prices": None,  # {"input_per_1m": x, "output_per_1m": y} in your currency; cost is shown only if set
}
UI_DEFAULTS = {"port": 8765, "idle_minutes": 60, "watermark_url": None,
               "guard_passages": 8,   # above this many passages in one request the chat asks before sending
               "max_passages": 25}    # a hard ceiling for one request


class ConfigError(Exception):
    pass


def data_dir():
    return Path(common.HOME)


def books_dir():
    return data_dir() / "books"


def book_dir(book_id):
    if not book_id or not all(c.isalnum() or c in "-_" for c in book_id):
        raise ConfigError(f"bad book id {book_id!r}: use letters, digits, - and _")
    return books_dir() / book_id


def inbox_dir():
    return data_dir() / "inbox"


def paper_dir(proj):
    """Per-paper state directory (already git-ignored by the skill)."""
    return Path(proj["path"]) / ".overleaf-skill"


def list_books():
    if not books_dir().is_dir():
        return []
    return sorted(p.name for p in books_dir().iterdir() if (p / "manifest.json").exists())


def openai_cfg():
    raw = common.load("openai.json")
    if not raw:
        raise ConfigError(f"missing {data_dir() / 'openai.json'}. Create it yourself (never paste the key in chat): "
                          '{"api_key": "sk-...", "model": "<model id you want>"}')
    cfg = {**OPENAI_DEFAULTS, **raw}
    for k in ("api_key", "model"):
        if not cfg.get(k):
            raise ConfigError(f"openai.json needs a non-empty {k!r}")
    return cfg


def openai_status():
    """('ok'|'missing'|'invalid', message) without raising or exposing the key."""
    try:
        c = openai_cfg()
        return "ok", f"model {c['model']}"
    except ConfigError as e:
        return ("missing" if "missing" in str(e) else "invalid"), str(e)


def ui_cfg():
    return {**UI_DEFAULTS, **(common.load("ui.json", {}) or {})}


def paper_settings(proj):
    p = paper_dir(proj) / "settings.json"
    try:
        s = json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        s = {}
    # dry_run governs Claude Code and the terminal (they preview first until you turn it off); the chat uses confirm_before_send instead
    return {"dry_run": True, "confirm_before_send": False, "send_refs_to_overleaf": True, **s}


def ensure_paper_dir(proj):
    """The paper's state folder, created only after git has been told to ignore it."""
    from . import gitguard
    gitguard.ensure_ignored(proj["path"])
    d = paper_dir(proj)
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_paper_setting(proj, key, value):
    d = ensure_paper_dir(proj)
    s = paper_settings(proj)
    s[key] = value
    (d / "settings.json").write_text(json.dumps(s, indent=2))
    return s
