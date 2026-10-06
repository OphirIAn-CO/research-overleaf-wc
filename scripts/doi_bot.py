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


def _clean_authors(bib):
    """A record may list an empty author ("author = { and Smith, J. and Lee, K.}"): LaTeX rejects it and it has no surname for the key. The empty names are dropped."""
    def fix(m):
        people = [a for a in re.split(r"\s+and\s+", " " + m.group(2) + " ") if a.strip(" ,{}")]
        return (m.group(1) + " and ".join(a.strip() for a in people) + m.group(3)) if people else ""          # no author at all: the field goes
    return re.sub(r"(\bauthor\s*=\s*[{\"]+)(.+?)([}\"]+,?\s*(?:\n|$))", fix, bib, count=1, flags=re.S | re.I)


def restyle(bib):
    """Rename key to Author2022FirstWord (matches the existing bib) and brace the title."""
    bib = _clean_authors(bib)
    bib = re.sub(r"([}\"\w]),\s+(\w+)\s*=", r"\1,\n    \2 =", bib)  # one field per line
    bib = re.sub(r"\s*\}\s*$", "\n}", bib)
    author, year, title = (re.search(rf"\b{f}\s*=\s*[{{\"]+(.+?)[}}\"]+,?\s*(?:\n|$)", bib, re.S | re.I)
                           for f in ("author", "year", "title"))
    first = next((a for a in author.group(1).split(" and ") if a.strip(" ,{}")), "") if author else ""
    first = first.strip() or "Anon"
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


def log_added(bib_path, doi, key, source, mendeley, pushed=None, mendeley_state=None):
    """Metadata-only `doi_added` event in the paper's own log, so the web app's Monitor and References pages see additions
    made from Telegram, the CLI or the web. Optional and silent: the bot works without the writing package."""
    try:
        from writing_core import config, events
        name = common.project_for_bib(bib_path)
        _, proj = common.resolve(name, check=False)
        events.log(config.paper_dir(proj), events.DOI_ADDED, paper=name, source=source, doi=doi, key=key, mendeley=bool(mendeley), pushed=pushed,
                   mendeley_state=mendeley_state)
    except Exception:  # noqa: BLE001
        pass


def _field(bib, name):
    m = re.search(rf"\b{name}\s*=\s*[{{\"]+(.+?)[}}\"]+,?\s*(?:\n|$)", bib, re.S | re.I)
    return re.sub(r"[{}]", "", re.sub(r"\s+", " ", m.group(1))).strip() if m else ""


def _existing_key(text, doi):
    """The cite key of the entry that already holds this DOI."""
    i = text.lower().find(doi.lower())
    if i < 0:
        return None
    keys = re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", text[:i])
    return keys[-1] if keys else None


def _paper_of(bib_path):
    """(name, proj) of the registered paper that owns this bib file, or (None, None)."""
    try:
        name = common.project_for_bib(bib_path)
        return common.resolve(name, check=False)
    except Exception:  # noqa: BLE001
        return None, None


def pending_path(proj):
    from writing_core import config
    return config.paper_dir(proj) / "pending_mendeley.json"


def pending_list(proj):
    try:
        return json.loads(pending_path(proj).read_text())
    except (FileNotFoundError, ValueError, KeyError):
        return []


def _save_pending(proj, items):
    from writing_core import config
    d = config.ensure_paper_dir(proj)  # git is told to ignore .overleaf-skill/ before anything is written there
    tmp = d / ".pending_mendeley.tmp"
    tmp.write_text(json.dumps(items, indent=1))
    tmp.replace(d / "pending_mendeley.json")


def mirror_settings(proj):
    """(group name or None, wanted?) for a paper: linked to a Mendeley group, and the 'also add to Mendeley' preference."""
    from writing_core import config
    g = ((proj or {}).get("mendeley_group") or {}).get("name")
    return g, bool(g) and config.paper_settings(proj).get("mirror_to_mendeley", True)


def retry_pending(bib_path):
    """Try again every reference that did not reach Mendeley. -> {tried, done, left}"""
    name, proj = _paper_of(bib_path)
    if proj is None:
        return {"tried": 0, "done": 0, "left": 0}
    items, left = pending_list(proj), []
    for it in items:
        if to_mendeley(it["bib"], it["doi"], bib_path, it["group"]).startswith("Mendeley: added"):
            continue
        left.append(it)
    if len(left) != len(items):
        _save_pending(proj, left)
    return {"tried": len(items), "done": len(items) - len(left), "left": len(left)}


def prepare_entry(doi, text, taken=(), fetch=None):
    """DOI -> (bibtex, key): fetched from doi.org, styled like the rest, under a key that is free in `text` (the bibliography) and in `taken` (keys planned but not written yet)."""
    bib, key = restyle((fetch or fetch_bibtex)(doi))
    base, n = key, 2
    while re.search(r"\{" + re.escape(key) + ",", text) or key in taken:  # same author+year+word collision
        key = base + chr(96 + n); n += 1
    return bib.replace("{" + base + ",", "{" + key + ",", 1), key


def commit_entry(bib_path, bib, key, doi, source="cli", mirror="auto", push=False, push_message=None):
    """Write one prepared entry to the bibliography, mirror it to Mendeley, log it. -> the dict add_reference returns (status 'added')."""
    bib_path = Path(bib_path)
    text = bib_path.read_text()
    bib_path.write_text(text.rstrip("\n") + "\n\n" + bib + "\n")
    title, authors, year, journal = _field(bib, "title"), _field(bib, "author"), _field(bib, "year"), _field(bib, "journal") or _field(bib, "booktitle")
    out = {"status": "added", "doi": doi, "key": key, "cite": f"\\cite{{{key}}}", "title": title or "?", "authors": authors.replace(" and ", "; "), "year": year,
           "journal": journal, "bibtex": bib, "mendeley": {"state": "off", "group": None, "detail": ""}, "push": None}
    msg = f"Added \\cite{{{key}}}\n{title or '?'}"
    name, proj = _paper_of(bib_path)
    if mirror == "auto":
        group, wanted = mirror_settings(proj) if proj else (None, False)
        idle_state = "off" if group else "not_linked"
    else:  # a group named by the caller, or None/False for "not this time"
        group, wanted, idle_state = (mirror or None), bool(mirror), "off"
    if group and wanted:
        res = to_mendeley(bib, doi, bib_path, group)
        if res.startswith("Mendeley: added"):
            out["mendeley"] = {"state": "added", "group": group, "detail": res}
            msg += "\n" + res
            if proj and pending_list(proj):  # the network clearly works now: catch up on anything that was waiting
                retry_pending(bib_path)
        else:
            out["mendeley"] = {"state": "failed", "group": group, "detail": res}
            if proj:
                items = [i for i in pending_list(proj) if i["doi"].lower() != doi.lower()]
                items.append({"doi": doi, "key": key, "group": group, "bib": bib, "title": title, "error": res[-200:], "ts": time.time()})
                _save_pending(proj, items)
            msg += "\n" + res + " It is kept in the waiting list and will be tried again."
    else:
        out["mendeley"] = {"state": idle_state, "group": group, "detail": "This paper is not linked to a Mendeley group." if idle_state == "not_linked" else
                           "Adding to Mendeley is switched off."}
    log_added(bib_path, doi, key, source, group if out["mendeley"]["state"] == "added" else None, None, mendeley_state=out["mendeley"]["state"])
    if push:
        ok, t = git_sync(bib_path, push_message or f"Add reference {key} (doi bot)")
        out["push"] = {"ok": ok, "text": t}
        msg += "\n" + t
    out["message"] = msg
    return out


def add_reference(doi, bib_path, source="cli", mirror="auto", push=False, push_message=None, fallback=None):
    """DOI -> references.bib, and on to Mendeley when the paper is linked to a group. Never raises on user-level problems.
    mirror: 'auto' = the paper's linked Mendeley group (if the author wants it), a group name, or None/False = not this time.
    -> dict: status added|duplicate|error|invalid, message (one readable block), and for added/duplicate: doi, key, cite, title, authors, year,
       journal, bibtex, mendeley {state: added|failed|not_linked|off, group, detail}, push {ok, text} | None"""
    m = DOI_RE.search(urllib.parse.unquote(doi))
    if not m:
        return {"status": "invalid", "message": "No DOI found in that message."}
    doi = m.group(0).rstrip(".,;)")
    bib_path = Path(bib_path)
    text = bib_path.read_text()
    if doi.lower() in text.lower():
        key = _existing_key(text, doi)
        return {"status": "duplicate", "doi": doi, "key": key, "cite": f"\\cite{{{key}}}" if key else None, "message": f"Already in references.bib: {doi}"}
    try:
        bib, key = prepare_entry(doi, text)
    except Exception as e:  # 404 = unknown DOI, else network, else a record that cannot be read
        made = None
        if fallback:                                    # the caller knows the paper from a search (title, authors, year): an entry made from that is better than none
            try:
                made = fallback(doi, text)
            except Exception:  # noqa: BLE001
                made = None
        if not made:
            return {"status": "error", "doi": doi, "message": f"Could not resolve {doi}: {e}"}
        bib, key = made
    return commit_entry(bib_path, bib, key, doi, source=source, mirror=mirror, push=push, push_message=push_message)


def add(doi, bib_path, push=False, group=None, source="cli"):
    """The one-line status for the bot, the CLI and older callers. A group name mirrors to that group; otherwise the paper's own
    linked Mendeley group is used, so a reference never reaches the bibliography without reaching Mendeley."""
    return add_reference(doi, bib_path, source=source, mirror=group or "auto", push=push)["message"]


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
        out = "\n\n".join(add(d, common.bib_path(proj), False, None, source="telegram") for d in dois)
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
