#!/usr/bin/env python3
"""DOI -> references.bib, optionally driven by a Telegram bot. Stdlib only.

  doi_bot.py add <doi-or-url> [project]   add one reference (CLI; project = name or path, default cwd/registry)
  doi_bot.py run                long-poll Telegram; every DOI sent is added

Config lives in ~/.config/overleaf-skill/ (see common.py), never in git:
  telegram.json  {"token": "<BotFather token>", "allowed_chat_ids": [123], "push": true,
                  "contact": "you@example.com"}   (contact goes in the User-Agent, Crossref etiquette)
  projects.json  the paper registry shared with the MCP server (`common.py add <path> --group G`)
Leave allowed_chat_ids empty on first run: the bot replies with your chat id so you can paste it in.
Without a whitelist it refuses to add anything. Telegram: /paper switches the active paper.
"""
import json, re, subprocess, sys, time, unicodedata, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import common

UA = {"User-Agent": "doi-bib-bot/1.0 (mailto:%s)" % (common.load("telegram.json", {}).get("contact") or "unset")}
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)


def http(url, accept=None, data=None, timeout=30):
    h = dict(UA, **({"Accept": accept} if accept else {}))
    with urllib.request.urlopen(urllib.request.Request(url, data, h), timeout=timeout) as r:
        return r.read().decode("utf-8")


def ascii_(s):
    return re.sub(r"\W", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode())


def fetch_bibtex(doi):
    return http("https://doi.org/" + urllib.parse.quote(doi), "application/x-bibtex").strip()


def restyle(bib):
    """Rename key to Author2022FirstWord (matches the existing bib) and brace the title."""
    bib = re.sub(r"([}\"\w]),\s+(\w+)\s*=", r"\1,\n    \2 =", bib)  # one field per line
    bib = re.sub(r"\s*\}\s*$", "\n}", bib)
    author, year, title = (re.search(rf"\b{f}\s*=\s*[{{\"]+(.+?)[}}\"]+,?\s*(?:\n|$)", bib, re.S | re.I)
                           for f in ("author", "year", "title"))
    first = author.group(1).split(" and ")[0] if author else "Anon"
    surname = first.split(",")[0] if "," in first else first.split()[-1]
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", title.group(1)) if w.lower() not in
                 {"the", "with", "from", "using", "based"}), "") if title else ""
    key = ascii_(surname) + ((re.search(r"\d{4}", year.group(1)) or [""])[0] if year else "") + ascii_(word).capitalize()
    return re.sub(r"^(@\w+)\{[^,]*,", lambda m: f"{m.group(1)}{{{key},", bib, count=1), key


def to_mendeley(bib, doi, bib_path, group):
    """Create the same paper in a Mendeley group. Non-fatal: returns a status line."""
    f = lambda k: (re.search(rf"\b{k}\s*=\s*[{{\"]+(.+?)[}}\"]+,?\s*(?:\n|$)", bib, re.S | re.I) or [0, ""])[1]
    authors = "; ".join(" ".join(reversed([p.strip() for p in a.split(",", 1)])) for a in f("author").split(" and "))
    cmd = [sys.executable, str(Path(__file__).with_name("mendeley_sync.py")), "add", "--title", f("title"),
           "--authors", authors, "--year", f("year"), "--doi", doi, "--source", f("journal") or f("booktitle"),
           "--group", group, "--bib", str(bib_path)]  # --bib: needed for group, and dedupes by DOI so no dup entry
    r = subprocess.run(cmd, capture_output=True, text=True)
    return f"Mendeley: added to {group}." if r.returncode == 0 else "Mendeley FAILED: " + (r.stdout + r.stderr)[-200:]


def add(doi, bib_path, push=False, group=None):
    """Returns a one-line status message. Never raises on user-level problems."""
    m = DOI_RE.search(urllib.parse.unquote(doi))
    if not m:
        return "No DOI found in that message."
    doi = m.group(0).rstrip(".,;)")
    bib_path = Path(bib_path)
    text = bib_path.read_text()
    if doi.lower() in text.lower():
        return f"Already in references.bib: {doi}"
    try:
        bib, key = restyle(fetch_bibtex(doi))
    except Exception as e:  # 404 = unknown DOI, else network
        return f"Could not resolve {doi}: {e}"
    n = 2
    base = key
    while re.search(r"\{" + re.escape(key) + ",", text):  # same author+year+word collision
        key = base + chr(96 + n); n += 1
    bib = bib.replace("{" + base + ",", "{" + key + ",", 1)
    bib_path.write_text(text.rstrip("\n") + "\n\n" + bib + "\n")
    title = (re.search(r"title\s*=\s*[{\"]+(.+?)[}\"]+,?\s*\n", bib, re.S | re.I) or [0, "?"])[1]
    msg = f"Added \\cite{{{key}}}\n{title}"
    if group:
        msg += "\n" + to_mendeley(bib, doi, bib_path, group)
    if push:
        repo = str(bib_path.parent)
        g = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
        g("add", bib_path.name)
        g("commit", "-m", f"Add reference {key} (doi bot)")
        g("pull", "--rebase", "--autostash")
        r = g("push")
        msg += "\nPushed to Overleaf." if r.returncode == 0 else "\nPush FAILED: " + r.stderr[-200:]
    return msg


def git_sync(bib_path, message):
    """commit (if needed) + pull --rebase + push. Returns (ok, text)."""
    repo, name = str(Path(bib_path).parent), Path(bib_path).name
    g = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    g("add", name)
    g("commit", "-m", message)
    p = g("pull", "--rebase", "--autostash")
    if p.returncode:
        g("rebase", "--abort")
        return False, "Pull FAILED (conflict?): " + p.stderr[-200:]
    r = g("push")
    return (True, "Pushed to Overleaf.") if r.returncode == 0 else (False, "Push FAILED: " + r.stderr[-200:])


def run():
    cfg = common.load("telegram.json")
    api = f"https://api.telegram.org/bot{cfg['token']}/"
    allowed, offset = cfg.get("allowed_chat_ids", []), 0
    stale = cfg.get("stale_hours", 6) * 3600  # older "active paper" => ask before adding
    pending = {}  # short key -> DOIs waiting for a paper choice / confirmation

    def tg(method, **kw):
        return http(api + method, data=urllib.parse.urlencode(kw).encode())

    def say(chat, text, buttons=None):
        kb = {"reply_markup": json.dumps({"inline_keyboard": [[b] for b in buttons]})} if buttons else {}
        tg("sendMessage", chat_id=chat, text=text, **kb)

    def paper_buttons(prefix):
        return [{"text": n, "callback_data": prefix + n} for n in common.registry()["projects"]]

    def target(name):  # -> (proj, group, header) or raises RuntimeError with a user-facing message
        _, proj = common.resolve(name)
        g = (proj.get("mendeley_group") or {}).get("name")
        return proj, g, f"[{name} @{common.remote_id(proj)} -> {g or 'no Mendeley'}]"

    def process(chat, name, dois):
        try:
            proj, g, head = target(name)
        except (KeyError, RuntimeError) as e:
            return say(chat, f"Refusing: {e}")
        common.set_active(name, "telegram")
        out = "\n\n".join(add(d, common.bib_path(proj), False, g) for d in dois)
        btn = None
        if cfg.get("push", True):  # one push per message, not per DOI
            ok, t = git_sync(common.bib_path(proj), "Add references (doi bot)")
            out += "\n\n" + t
            btn = None if ok else [{"text": "Pull & push", "callback_data": "sync:" + name}]
        say(chat, head + "\n" + out, btn)

    print("bot running, Ctrl-C to stop")
    while True:
        try:
            ups = json.loads(http(api + "getUpdates?timeout=50&offset=%d" % offset, timeout=70))["result"]
        except Exception as e:
            print("poll error:", e); time.sleep(5); continue
        for u in ups:
            offset = u["update_id"] + 1
            cb = u.get("callback_query")
            if cb:
                chat, data = cb["message"]["chat"]["id"], cb["data"]
                tg("answerCallbackQuery", callback_query_id=cb["id"])
                if chat not in allowed:
                    continue
                kind, _, rest = data.partition(":")
                if kind == "paper":  # /paper switch
                    common.set_active(rest, "telegram")
                    say(chat, f"Active paper: {rest}")
                elif kind == "go":  # confirm/choose for a pending DOI message: go:<key>:<paper>
                    key, _, name = rest.partition(":")
                    dois = pending.pop(key, None)
                    say(chat, "Expired, send the DOI again.") if dois is None else process(chat, name, dois)
                elif kind == "sync":
                    try:
                        proj, _, head = target(rest)
                        ok, t = git_sync(common.bib_path(proj), "Add references (doi bot)")
                        say(chat, f"{head}\n{t}", None if ok else [{"text": "Pull & push", "callback_data": "sync:" + rest}])
                    except (KeyError, RuntimeError) as e:
                        say(chat, f"Refusing: {e}")
                continue
            msg = u.get("message") or {}
            chat, text = msg.get("chat", {}).get("id"), msg.get("text", "")
            if chat is None:
                continue
            if chat not in allowed:
                say(chat, f"Not authorised. Add {chat} to allowed_chat_ids.")
                continue
            name, source, age = common.get_active()
            name = name or common.registry()["default"]
            when = "never set" if source == "?" else f"set by {source}, {int(age // 60)} min ago"
            if text.startswith("/paper"):
                say(chat, f"Active: {name} ({when}). Switch to:", paper_buttons("paper:"))
                continue
            if text.startswith("/status"):
                try: say(chat, target(name)[2] + f"\n{when}")
                except (KeyError, RuntimeError) as e: say(chat, f"Problem: {e}")
                continue
            dois = list(dict.fromkeys(DOI_RE.findall(text)))
            if not dois:
                say(chat, "Send a DOI or doi.org link. /paper switches paper, /status shows the target.")
                continue
            if age > stale or source == "?":  # nobody touched this paper recently: confirm the target
                key = str(u["update_id"])
                pending[key] = dois
                say(chat, f"Active paper is {name} ({when}). Add {len(dois)} DOI(s) to:",
                    [{"text": ("✔ " if n == name else "") + n, "callback_data": f"go:{key}:{n}"} for n in common.registry()["projects"]])
            else:
                process(chat, name, dois)


def demo():
    import tempfile
    p = Path(tempfile.mkdtemp()) / "t.bib"
    p.write_text("")
    assert "Added" in add("https://doi.org/10.3390/pr10020400", p), "add"
    assert "Already" in add("10.3390/pr10020400", p), "dedupe"
    assert "Elshaboury2022" in p.read_text(), "key style"
    assert "No DOI" in add("hello", p)
    print("ok")


if __name__ == "__main__":
    c = sys.argv[1:2]
    if c == ["add"]:
        name, p = common.resolve(sys.argv[3] if len(sys.argv) > 3 else None)
        g = (p.get("mendeley_group") or {}).get("name")
        print(f"[{name}]", add(sys.argv[2], common.bib_path(p), False, g))
    elif c == ["run"]:
        run()
    elif c == ["demo"]:
        demo()
    else:
        print(__doc__)
