"""Tell the author a long request is ready, so they do not have to watch the chat.

Two ways, both optional and both set in Settings:
  - the browser: a short chime and a notification when the tab is out of sight (done by the page, see app.js);
  - Telegram: a message through the bot that already adds DOIs from the phone. It uses the same telegram.json (the bot's token and the
    chats allowed to talk to it), so there is nothing new to set up. Only the paper's name and one plain sentence are sent, never any text
    of the paper, the conversation or an answer.
Nothing here may ever break the work itself: a message that cannot be sent is simply not sent.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

import common

from . import config

DEFAULTS = {"notify_telegram": True, "notify_sound": True, "notify_browser": True, "notify_min_seconds": 20}


def settings():
    ui = config.ui_cfg()
    return {k: ui.get(k, v) for k, v in DEFAULTS.items()}


def telegram_state():
    """('set_up', n chats) when the bot has a token and at least one chat that may talk to it, else ('not_set_up', 0)."""
    cfg = common.load("telegram.json", {}) or {}
    chats = [c for c in (cfg.get("allowed_chat_ids") or []) if isinstance(c, (int, str))]
    return ("set_up", len(chats)) if cfg.get("token") and chats else ("not_set_up", 0)


def send_telegram(text):
    """Send one message to every chat the bot is allowed to talk to. Returns (how many got it, a plain problem or None); never raises."""
    cfg = common.load("telegram.json", {}) or {}
    chats = [c for c in (cfg.get("allowed_chat_ids") or []) if isinstance(c, (int, str))]
    if not cfg.get("token") or not chats:
        return 0, "The Telegram bot is not set up yet (no token or no allowed chat)."
    sent, problem = 0, None
    for chat in chats:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text[:600], "disable_web_page_preview": "true"}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{cfg['token']}/sendMessage", data), timeout=10) as r:
                ok = json.loads(r.read().decode("utf-8")).get("ok")
            sent += 1 if ok else 0
            problem = problem or (None if ok else "Telegram did not accept the message.")
        except urllib.error.HTTPError as e:   # the address holds the bot's token, so it is never put in the message
            problem = "Telegram refused it (the bot token or the chat number is wrong)." if e.code in (400, 401, 403, 404) else f"Telegram answered with an error ({e.code})."
        except (urllib.error.URLError, OSError, ValueError):
            problem = "This computer could not reach Telegram just now."
    return sent, problem


def job_text(job, cfg=None):
    """The one sentence for a finished job, or None when it should not be announced (not a chat request, too quick, or switched off)."""
    cfg = cfg or settings()
    meta = job.get("meta") or {}
    if not cfg["notify_telegram"] or not meta.get("chat") or job.get("status") not in ("done", "error"):
        return None
    if (job.get("finished") or 0) - (job.get("started") or 0) < max(0, int(cfg["notify_min_seconds"])):
        return None
    paper = meta.get("paper") or "your paper"
    if job["status"] == "error":
        return f"Stopped: your request in {paper} could not be completed. Open the chat to see why."
    res = job.get("result") if isinstance(job.get("result"), dict) else {}
    made = res.get("made")
    if isinstance(made, int) and made > 0:
        return f"Ready: {made} suggestion{'s' if made != 1 else ''} to review in {paper}. Nothing is saved until you accept it."
    return f"Ready: your request in {paper} is done. Open the chat to see the answer."


def job_finished(job):
    """Called by the app when any background job ends; sends the Telegram message when it applies."""
    text = job_text(job)
    if text:
        send_telegram(text)


def test_message():
    """What the Test button sends. Returns (ok, plain sentence)."""
    state, n = telegram_state()
    if state != "set_up":
        return False, "The Telegram bot is not set up yet, so there is nowhere to send it. See the Telegram card above."
    sent, problem = send_telegram("This is a test from the Overleaf assistant: when a long request is ready, you will get a message like this.")
    if sent:
        return True, f"Sent to {sent} chat{'s' if sent != 1 else ''}. Look at Telegram on your phone." + (f" (One did not get it: {problem})" if problem and sent < n else "")
    return False, problem or "Nothing was sent."
