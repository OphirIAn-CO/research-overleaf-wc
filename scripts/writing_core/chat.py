"""The chat's brain: conversations stored per paper, and a resolver from plain words to the exact pieces of the manuscript.
No model is called here.

* Conversations are small JSON files inside the paper's own .overleaf-skill/chats/ folder (git is told to ignore it first), so a
  chat never mixes papers. They hold what was said and which suggestion belongs to which answer, never book text.
* understand() turns "tighten the text before Figure 1 in the methods" into exact line ranges, deterministically: part words come from
  the taxonomy, section titles from the paper itself, "before/after Figure N" from the LaTeX, "first paragraph" from the blank lines.
  When it cannot be sure it answers with a list to tick instead of guessing.
"""
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from . import chunk_tex, config, paper_index, retrieve, revise, store, taxonomy

DIRNAME = "chats"
MAX_MESSAGES = 300
MAX_TEXT = 30000                # a message can carry an outline or notes to write from; longer than this is cut, and the chat says so
CID = re.compile(r"^c[0-9a-f]{10}$")
_LOCK = threading.Lock()


# ------------------------------------------------------------------------------------------- conversations

def _dir(proj):
    return config.paper_dir(proj) / DIRNAME


def _path(proj, cid):
    if not CID.match(str(cid)):
        raise ValueError("bad chat id")
    return _dir(proj) / f"{cid}.json"


def _read(proj, cid):
    try:
        data = json.loads(_path(proj, cid).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("messages"), list) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write(proj, thread):
    d = config.ensure_paper_dir(proj) / DIRNAME  # git is told to ignore .overleaf-skill/ before anything is written in it
    d.mkdir(exist_ok=True)
    thread["messages"] = thread["messages"][-MAX_MESSAGES:]
    tmp = d / f".{thread['id']}.{os.getpid()}.{secrets.token_hex(3)}.tmp"
    tmp.write_text(json.dumps(thread, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / f"{thread['id']}.json")


def new_thread(proj, title="New chat"):
    t = {"id": "c" + secrets.token_hex(5), "title": title, "created": time.time(), "updated": time.time(), "messages": []}
    with _LOCK:
        _write(proj, t)
    return t


def list_threads(proj):
    out = []
    d = _dir(proj)
    for p in d.glob("c*.json") if d.is_dir() else []:
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": t["id"], "title": t.get("title") or "New chat", "created": t.get("created"), "updated": t.get("updated"),
                        "count": len(t.get("messages", []))})
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return sorted(out, key=lambda t: -(t.get("updated") or 0))


def get_thread(proj, cid):
    with _LOCK:
        return _read(proj, cid)


def ensure_thread(proj, cid=None):
    """The requested chat if it exists, else the most recent one, else a new one."""
    if cid and get_thread(proj, cid):
        return cid
    threads = list_threads(proj)
    return threads[0]["id"] if threads else new_thread(proj)["id"]


def append(proj, cid, role, kind, text="", **data):
    """role: 'user' | 'assistant'. kind: text | plan | ask | result | note | error | refs | review."""
    msg = {"id": "m" + secrets.token_hex(5), "ts": time.time(), "role": role, "kind": kind, "text": str(text)[:MAX_TEXT], "data": data}
    with _LOCK:
        t = _read(proj, cid)
        if t is None:
            raise ValueError("no such chat")
        t["messages"].append(msg)
        t["updated"] = msg["ts"]
        if role == "user" and t.get("title") in (None, "", "New chat"):
            t["title"] = " ".join(str(text).split())[:48] or "New chat"
        _write(proj, t)
    return msg


def update(proj, cid, mid, **fields):
    """Merge `fields` into a message's data (e.g. mark a plan as sent)."""
    with _LOCK:
        t = _read(proj, cid)
        for m in (t or {}).get("messages", []):
            if m["id"] == mid:
                m["data"] = {**m.get("data", {}), **fields}
                _write(proj, t)
                return m
    return None


def get_message(proj, cid, mid):
    t = get_thread(proj, cid)
    return next((m for m in (t or {}).get("messages", []) if m["id"] == mid), None)


def rename(proj, cid, title):
    with _LOCK:
        t = _read(proj, cid)
        if t is None:
            raise ValueError("no such chat")
        t["title"] = " ".join(str(title).split())[:80] or "New chat"
        _write(proj, t)


def clear(proj, cid):
    """Start the conversation over in the same place: no messages, so nothing is left to follow up on ("make it shorter" has no earlier place to mean)."""
    with _LOCK:
        t = _read(proj, cid)
        if t is None:
            raise ValueError("no such chat")
        t["messages"], t["title"], t["updated"] = [], "New chat", time.time()
        _write(proj, t)


def delete(proj, cid):
    with _LOCK:
        p = _path(proj, cid)
        if p.exists():
            p.unlink()


def last_target(thread):
    """The pieces the last request in this chat worked on, so 'make it shorter' can mean the same place."""
    for m in reversed((thread or {}).get("messages", [])):
        ids = (m.get("data") or {}).get("chunk_ids")
        if ids and m["role"] == "assistant":
            return ids
    return None


# ------------------------------------------------------------------------------------------- the comments of a peer review

HANDLED = ("saved", "sent", "rejected", "nothing", "skipped")     # the states in which a reviewer comment needs nothing more from the author
_PLAN_STATE = {"waiting": "asking", "sent": "working", "failed": "failed", "cancelled": "open"}


def issue_states(proj, thread, running=True):
    """Where every comment of every peer review in this chat stands. -> {review message id: [{state, message?, proposal?}]}, one entry per comment, in order.

    `running` is whether a request of this chat is being worked on right now; a request marked as sent that nobody is working on (the app was restarted) counts as failed.

    States: open (nothing tried), asking (waiting for a go-ahead), working (OpenAI is on it), decide (a suggestion waits for the author), saved (in the files here),
    sent (in Overleaf), rejected (the author set the suggestion aside), nothing (OpenAI would change nothing), failed (try again), skipped (the author skipped it)."""
    msgs = (thread or {}).get("messages", [])
    out = {}
    for m in msgs:
        if m["kind"] == "review":
            n = len(m["data"].get("issues") or [])
            out[m["id"]] = [{"state": "open"} for _ in range(n)]
            for i in m["data"].get("skipped") or []:
                if isinstance(i, int) and 0 <= i < n:
                    out[m["id"]][i] = {"state": "skipped"}
    for m in msgs:                                            # in the order things happened: a later try replaces an earlier one
        ref = (m.get("data") or {}).get("issue")
        if not isinstance(ref, dict) or ref.get("review") not in out or m["kind"] not in ("plan", "result"):
            continue
        slots = out[ref["review"]]
        i = ref.get("index")
        if not isinstance(i, int) or not 0 <= i < len(slots):
            continue
        if m["kind"] == "plan":
            if m["data"].get("state") == "sent" and any(x["kind"] == "result" and x["data"].get("plan") == m["id"] for x in msgs):
                continue                                      # its answer is there: the answer decides
            state = _PLAN_STATE.get(m["data"].get("state"), "working")
            slots[i] = {"state": "failed" if state == "working" and not running else state, "message": m["id"]}
            continue
        try:
            prop = revise.get_proposal(proj, m["data"].get("proposal"))
        except revise.ReviseError:
            slots[i] = {"state": "failed", "message": m["id"]}
            continue
        status = prop.get("status")
        state = {"applied": "sent" if prop.get("sent_at") else "saved", "rejected": "rejected", "no_changes": "nothing", "proposed": "decide"}.get(status, "failed")
        slots[i] = {"state": state, "message": m["id"], "proposal": prop.get("id")}
    return out


def review_counts(states):
    """-> (how many comments, how many are handled, {state: n})"""
    by = {}
    for s in states:
        by[s["state"]] = by.get(s["state"], 0) + 1
    return len(states), sum(n for k, n in by.items() if k in HANDLED), by


def close_reviews(proj, cid):
    """When the last comment of a review with several comments is handled, say so in the chat once, so the author knows the list is finished and what comes next.
    -> the new messages (usually none)"""
    thread = get_thread(proj, cid)
    states = issue_states(proj, thread)
    made = []
    for m in (thread or {}).get("messages", []):
        if m["kind"] != "review" or m["data"].get("closed") or len(m["data"].get("issues") or []) < 2:
            continue
        total, handled, by = review_counts(states.get(m["id"], []))
        if total and handled == total:
            update(proj, cid, m["id"], closed=True)
            done = by.get("saved", 0) + by.get("sent", 0)
            aside = by.get("rejected", 0) + by.get("skipped", 0) + by.get("nothing", 0)
            made.append(append(proj, cid, "assistant", "review_done", "", review=m["id"], total=total, done=done, aside=aside, unsent=by.get("saved", 0), scope=m["data"].get("scope", "")))
    return made


# ------------------------------------------------------------------------------------------- what kind of message is it

_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I)
_REVIEW = re.compile(r"\bpeer[\s-]*review|\breview\s+(?:my|the|this|our)\b|\bcritique\b|\bact\s+as\s+(?:a\s+)?(?:peer\s+)?reviewer\b|"
                     r"\bevaluate\s+(?:my|the|this|our)\s+(?:paper|draft|manuscript|article|work)\b|\breferee\s+report\b", re.I)
_HELP = re.compile(r"^\s*(?:help|how does (?:this|it) work\??|what can you do\??|\?)\s*$", re.I)


def find_dois(text):
    return [d.rstrip(".,;)") for d in _DOI.findall(text or "")]


_READING = r"(?:library|literature|readings?|articles|books|sources|pdfs|reading list|references|papers)"
_SHELF = r"(?:library|literature|readings?|articles|books|sources|pdfs|reading list)"                      # "my papers" and "my references" may mean the manuscript and its bibliography
_LIBRARY_ASK = re.compile(
    rf"\b(?:search|look\s*(?:up|in|through)|find|check|scan|query|show)\b.*\b(?:my|the|our)\s+{_SHELF}\b|"
    rf"\bwhat\s+(?:do|does|did)\s+(?:my|the|our)\s+{_READING}\s+(?:say|tell|show|mention|have|cover|report)\b|"
    r"\bwhat\s+(?:does|did)\s+(?:the\s+)?literature\s+(?:say|show|tell)\b|"
    rf"\b(?:which|what)\s+(?:of\s+)?(?:my\s+)?{_READING}\b.*\b(?:about|on|mention|discuss|cover|report|show)\b|"
    r"\b(?:is|are)\s+there\s+(?:anything|something|evidence|a source|any)\b.*\b(?:in|from)\s+(?:my|the)\s+(?:library|literature|readings?|articles|books|sources)\b", re.I)
_LIBRARY_EDIT = {"add", "insert", "include", "put", "improve", "rewrite", "revise", "edit", "write", "draft", "cite", "citation", "citations", "support", "tighten", "shorten", "fix", "polish"}
_LIBRARY_NOISE = re.compile(rf"\b(?:please|can you|could you|search|look\s*(?:up|in|through)|find|check|scan|query|show|what|which|do|does|did|is|are|there|anything|something|my|the|our|of|in|from|"
                            rf"{_READING}|say|says|tell|mention|mentions|have|cover|covers|report|reports|about|on|for|me|us|any|evidence|a source)\b", re.I)


def library_topic(text):
    """'what do my articles say about well data curation?' -> 'well data curation'"""
    t = _LIBRARY_NOISE.sub(" ", text or "")
    return re.sub(r"\s+", " ", re.sub(r"[?!.:;,]+", " ", t)).strip()


_DRAFT = re.compile(r"\b(?:write|draft|compose|generate|prepare|produce|come up with)\b|\badd\s+(?:a|an|one|two|three|some|new)\s+(?:new\s+)?(?:paragraphs?|sections?|sentences?|passages?)\b", re.I)
_DRAFT_VIA = re.compile(r"\b(?:explain|interpret|discuss|describe|summari[sz]e|report|present)\b.*\b(?:in|into|for|to)\s+(?:the\s+|my\s+|our\s+)?(?:\w+\s+){0,2}?"
                        r"(?:results?|discussion|introduction|methods?|conclusions?|abstract|limitations?)\b", re.I)
_DRAFT_NOT = {"rewrite", "rephrase", "paraphrase", "improve", "fix", "polish", "tighten", "shorten", "simplify", "proofread", "revise", "edit", "smooth", "reorder", "reorganize"}
DRAFTABLE = ("abstract", "introduction", "background", "theory", "methods", "results", "discussion", "limitations", "conclusion")
_PREP_PART = re.compile(r"\b(?:in|into|for|to|inside|within|under|on)\s+(?:the\s+|my\s+|our\s+|a\s+|an\s+)?(?:\w+\s+){0,2}?(%s)\b")


ASK_WORDS = 45
_DRAFT_VERB = re.compile(r"\b(?:write|draft|compose|generate|prepare|produce|add)\b", re.I)


def ask_of(text, n=ASK_WORDS):
    """What is being asked, inside a long message. A message that carries pasted material (an outline, notes, a literature review) says what to do in its first lines or in its last ones,
    never in the middle: only those are read to tell what kind of request it is and which part of the paper it is about. -> [the whole text] when it is short, else [start, end]"""
    t = text or ""
    if len(t.split()) <= 2 * n:
        return [t]
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]

    def edge(seq):
        picked, count = [], 0
        for ln in seq:
            picked.append(ln)
            count += len(ln.split())
            if count >= 12:                                        # a greeting line alone is not yet the request
                break
        return picked
    start = " ".join(" ".join(edge(lines)).split()[:n])
    end = " ".join(" ".join(reversed(edge(list(reversed(lines))))).split()[-n:])
    return [start, end]


def _part_in(text):
    ws = taxonomy.words((text or "").lower())
    first = {}
    for part in DRAFTABLE:
        for phrase in taxonomy._IDX.get(part, []):
            n = len(phrase)
            for i in range(len(ws) - n + 1):
                if n and tuple(ws[i:i + n]) == phrase:
                    first[part] = min(first.get(part, 10 ** 6), i)
                    break
    if not first:
        return None
    start = next((i for i, w in enumerate(ws) if w in ("write", "draft", "compose", "generate", "prepare", "produce", "add")), 0)
    for part in sorted(first, key=first.get):                       # a part named after a preposition is the place ("... in the discussion")
        pos = first[part]
        if pos > 0 and ws[max(0, pos - 3):pos] and any(w in ("in", "into", "for", "to", "inside", "within", "under") for w in ws[max(0, pos - 3):pos]):
            return part
    after = [p for p in sorted(first, key=first.get) if first[p] >= start]
    return (after or sorted(first, key=first.get))[0]


def draft_part(text):
    """The part of the paper a request to WRITE something is about ("write the discussion of these results" -> discussion; "explain the results in the discussion" ->
    discussion; "write the results from my code" -> results). None when it names none. In a long message only its start and its end are read: an outline pasted in the
    middle that says "now move from theory to methods" is material, not the request."""
    windows = ask_of(text)
    asking = [w for w in windows if _DRAFT.search(w) or _DRAFT_VIA.search(w)]            # the end of a long message may be the request, and the start only the material
    ordered = asking + [w for w in windows if w not in asking]
    for window in ordered:
        for m in _DRAFT_VERB.finditer(window):                                          # "... write the introduction of my paper": the part named right after the verb
            part = _part_in(" ".join(window[m.start():].split()[:16]))
            if part:
                return part
    for window in ordered:
        part = _part_in(window)
        if part:
            return part
    return None


def is_draft_request(text):
    """"Write the results from my analysis", "draft the discussion", "add a paragraph about the sampling to the methods": new text, not an edit of existing text."""
    windows = ask_of(text)
    t = " ... ".join(windows)
    if not (_DRAFT.search(t) or _DRAFT_VIA.search(t)):
        return False
    ws = set(taxonomy.words(t.lower())) | {w for w in re.findall(r"[a-z]+", t.lower())}
    if ws & _DRAFT_NOT:
        return False
    return bool(draft_part(text)) or bool(re.search(r"\b(?:paragraphs?|section|sentences?|passage)\b", t, re.I))


# "find references about X": look for new literature on the internet (as opposed to "what do my articles say", which searches the author's own library)
_REF_NOUN = r"(?:references?|citations?|sources?|literature|papers?|articles?|studies|study|publications?|research|evidence)"
_REF_VERB = r"(?:find|search(?:\s+for)?|look\s*(?:up|for)|get|suggest|recommend|locate|fetch|retrieve|dig\s+up|bring|give|show|list|propose|need|want)"
_REFS_ASK = re.compile(rf"\b{_REF_VERB}\b(?:\W+\w+){{0,6}}?\W+{_REF_NOUN}\b", re.I)
_REFS_EDIT = {"improve", "rewrite", "rephrase", "paraphrase", "revise", "edit", "tighten", "shorten", "polish", "fix", "proofread", "simplify", "smooth", "reorder", "reorganize"}
_REFS_NOISE = re.compile(rf"\b(?:please|can you|could you|would you|i|we|need|want|find|search(?:ing)?|for|look\s*(?:up|for)?|get|me|us|some|any|few|good|relevant|suitable|appropriate|recent|new|more|additional|"
                         rf"further|online|internet|web|google|scholar|about|regarding|concerning|related\s+to|that|which|support|supports|supporting|the|a|an|to|and|suggest|recommend|locate|fetch|"
                         rf"retrieve|propose|list|give|show|bring|add|cite|{_REF_NOUN})\b", re.I)


def is_refs_request(text):
    t = text or ""
    return bool(_REFS_ASK.search(t)) and not (set(re.findall(r"[a-z]+", t.lower())) & _REFS_EDIT) and not is_draft_request(t)


def refs_topic(text):
    """'find me some references about how violence affects pipelines in Colombia' -> 'how violence affects pipelines in Colombia'"""
    t = _REFS_NOISE.sub(" ", text or "")
    return re.sub(r"^(?:on|in|of)\s+", "", re.sub(r"\s+", " ", re.sub(r"[?!.:;,]+", " ", t)).strip())


def intent(text):
    """-> 'refs' | 'review' | 'help' | 'library' | 'refsearch' | 'draft' | 'edit'"""
    if find_dois(text):
        return "refs"
    if _HELP.match(text or ""):
        return "help"
    asked = " ... ".join(ask_of(text))                     # in a long message with pasted material, what is asked is at its start or its end
    if _REVIEW.search(asked):
        return "review"
    if _LIBRARY_ASK.search(asked) and not (set(taxonomy.words(asked.lower())) & (_INTENT_WORDS | _LIBRARY_EDIT)):
        return "library"
    if is_refs_request(asked):
        return "refsearch"
    if is_draft_request(text):
        return "draft"
    return "edit"


# ------------------------------------------------------------------------------------------- finding the place

_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3}
_FLOAT = re.compile(r"\b(before|above|preceding|prior\s+to|up\s+to|until|ahead\s+of|after|following|below|past)\s+(?:the\s+)?"
                    r"(?:(first|second|third|1st|2nd|3rd)\s+)?(?:fig\.?|figure|table)s?(?:\s*(\d+|one|two|three|four|five|six))?\b", re.I)
_PARA = re.compile(r"\b(?:the\s+)?(first|last|opening|final|second|third|(\d+)(?:st|nd|rd|th))\s+(?:(one|two|three|four|five|\d+)\s+)?paragraphs?\b|"
                   r"\bparagraphs?\s+(\d+)\b", re.I)
_BEGIN, _END = re.compile(r"\\begin\{(figure|table)\*?\}"), re.compile(r"\\end\{(figure|table)\*?\}")
_SCAFFOLD = re.compile(r"^\s*(\\(label|centering|caption|includegraphics|begin|end)\b.*)?$")
_STOP = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with", "section", "part", "text", "paragraph", "my", "our", "this", "that",
         "these", "those", "is", "are", "it", "please", "before", "after", "figure", "table", "first", "last", "improve", "fix", "make", "all"}
_INTENT_WORDS = {"improve", "fix", "tighten", "shorten", "shorter", "clarify", "clearer", "clear", "flow", "grammar", "grammatical", "tone", "concise",
                 "rewrite", "polish", "simplify", "expand", "formal", "academic", "error", "errors", "passive", "active", "style", "readability",
                 "wordy", "revise", "edit", "proofread", "cut", "reduce", "strengthen", "smooth", "better", "tense", "spelling", "typo", "typos",
                 "rephrase", "paraphrase", "transition", "transitions", "coherent", "coherence", "logic", "structure", "reorganize", "reorder",
                 "longer", "detail", "direct", "readable", "natural", "fluent", "fluency", "repetition", "repetitive", "vague", "informal"}


def _float_request(text):
    """-> (direction 'before'|'after', kind 'figure'|'table', n) or None."""
    m = _FLOAT.search(text)
    if not m:
        return None
    word = m.group(1).lower()
    kind = "table" if "table" in m.group(0).lower() else "figure"
    n = m.group(3) or m.group(2) or "1"
    n = int(n) if n.isdigit() else _NUM.get(n.lower(), 1)
    return ("after" if word in ("after", "following", "below", "past") else "before"), kind, max(1, n)


def _para_request(text):
    """-> ('first'|'last'|'nth', n) or None.  'first two paragraphs' -> ('first', 2); 'the second paragraph' -> ('nth', 2)."""
    m = _PARA.search(text)
    if not m:
        return None
    word = (m.group(1) or "").lower()
    count = m.group(3)
    n = int(count) if count and count.isdigit() else _NUM.get((count or "").lower(), 1)
    if m.group(4):
        return "nth", int(m.group(4))
    if word in ("first", "opening"):
        return "first", n
    if word in ("last", "final"):
        return "last", n
    if m.group(2):
        return "nth", int(m.group(2))
    return "nth", _NUM.get(word, 1)


def _lines(proj, rel):
    return revise._read_lines(Path(proj["path"]) / rel)[0]


def _has_text(lines, a, b):
    """Is there prose between 1-indexed lines a..b (comments, blank lines and float scaffolding do not count)?"""
    for i in range(max(1, a), min(len(lines), b) + 1):
        raw = chunk_tex.strip_comment(lines[i - 1])
        if raw.strip() and not _SCAFFOLD.match(raw):
            return True
    return False


def _trim(lines, a, b):
    """Shrink [a, b] so it starts and ends on a line with something in it."""
    while a <= b and not chunk_tex.strip_comment(lines[a - 1]).strip():
        a += 1
    while b >= a and not chunk_tex.strip_comment(lines[b - 1]).strip():
        b -= 1
    return (a, b) if a <= b else None


def _floats(proj, tex_chunks):
    """Every figure/table environment in reading order, numbered per kind like LaTeX numbers them."""
    found, counts, cache = [], {"figure": 0, "table": 0}, {}
    for c in sorted(tex_chunks, key=lambda c: c["ord"]):
        lines = cache.setdefault(c["file"], _lines(proj, c["file"]))
        i = c["line_start"]
        while i <= c["line_end"]:
            m = _BEGIN.search(chunk_tex.strip_comment(lines[i - 1]))
            if m:
                kind, j = m.group(1), i
                while j < c["line_end"] and not _END.search(chunk_tex.strip_comment(lines[j - 1])):
                    j += 1
                counts[kind] += 1
                found.append({"kind": kind, "n": counts[kind], "ord": c["ord"], "begin": i, "end": j, "file": c["file"]})
                i = j
            i += 1
    return found


def _rid(c, a=None, b=None):
    return c["id"] if a is None else f"{c['id']}@{a}-{b}"


def _apply_float(proj, cands, all_tex, req):
    """Keep only what comes before/after the Nth figure or table. -> (ids, note). A paragraph run that straddles the figure is cut at it."""
    direction, kind, n = req
    label = f"{kind.capitalize()} {n}"
    fl = next((f for f in _floats(proj, all_tex) if f["kind"] == kind and f["n"] == n), None)
    if not fl:
        return [_rid(c) for c in cands], f"I could not find {label} in your files, so I did not narrow anything by it."
    lines = _lines(proj, fl["file"])
    ids = []
    for c in sorted(cands, key=lambda c: c["ord"]):
        if direction == "before":
            if c["ord"] < fl["ord"]:
                ids.append(_rid(c))
            elif c["ord"] == fl["ord"] and _has_text(lines, c["line_start"], fl["begin"] - 1):
                span = _trim(lines, c["line_start"], fl["begin"] - 1)
                if span:
                    ids.append(_rid(c, *span))
        else:
            if c["ord"] > fl["ord"]:
                ids.append(_rid(c))
            elif c["ord"] == fl["ord"] and _has_text(lines, fl["end"] + 1, c["line_end"]):
                span = _trim(lines, fl["end"] + 1, c["line_end"])
                if span:
                    ids.append(_rid(c, *span))
    return ids, f"{'Before' if direction == 'before' else 'After'} {label}."


def _paragraphs(proj, c):
    """[(a, b)] 1-indexed paragraph line ranges inside a chunk (never splitting an environment)."""
    lines = _lines(proj, c["file"])
    tuples = [(c["file"], i + 1, lines[i], chunk_tex.strip_comment(lines[i])) for i in range(len(lines))]
    return [(a + 1, b + 1) for a, b in chunk_tex.paragraphs(tuples, c["line_start"] - 1, c["line_end"])]


def _apply_para(proj, cands, req):
    which, n = req
    paras = [(c, a, b) for c in sorted(cands, key=lambda c: c["ord"]) for a, b in _paragraphs(proj, c)]
    if not paras:
        return [_rid(c) for c in cands], ""
    pick = paras[:n] if which == "first" else paras[-n:] if which == "last" else paras[n - 1:n]
    if not pick:
        return [_rid(c) for c in cands], f"There is no paragraph {n} there, so I used everything."
    many = f"{n} paragraphs" if n != 1 else "paragraph"
    name = {"first": f"first {many}", "last": f"last {many}", "nth": f"paragraph {n}"}[which]
    return [_rid(c, a, b) for c, a, b in pick], f"Only the {name}."


def _norm_title(t):
    return tuple(w for w in taxonomy.words(t) if len(w) > 2 and w not in _STOP)


def _title_match(text, tex):
    """Chunks under a section whose title the author named ("the Study Area section"). Longest title wins."""
    words = set(taxonomy.words(text))
    best, best_len = [], 0
    seen = {}
    for c in tex:
        for comp in c["section_path"].split(" > "):
            tw = _norm_title(comp)
            if tw and set(tw) <= words:
                seen.setdefault(tw, set()).add(comp)
    for tw, comps in seen.items():
        if len(tw) > best_len:
            best, best_len = comps, len(tw)
    if not best:
        return [], ""
    return [c for c in tex if best & set(c["section_path"].split(" > "))], sorted(best)[0]


def _option(c):
    return {"id": c["id"], "section": c["section_path"], "part": c["part"], "file": c["file"], "lines": [c["line_start"], c["line_end"]],
            "tokens": c["tokens"], "preview": c.get("preview", ""), "ord": c["ord"]}


def _sections(tex):
    """The top-level sections of the paper as buttons: label, how many pieces, the piece ids."""
    out, order = {}, []
    for c in sorted(tex, key=lambda c: c["ord"]):
        top = c["section_path"].split(" > ")[0]
        if top not in out:
            out[top] = {"label": top, "part": c["part"], "chunk_ids": []}
            order.append(top)
        out[top]["chunk_ids"].append(c["id"])
    return [out[t] for t in order][:14]


def _choose(why, tex, preselect=None, in_part=None, notes=None, limit=None):
    return {"status": "choose", "why": why, "options": [_option(c) for c in tex], "preselect": preselect or [], "in_part": in_part or [],
            "sections": _sections(tex), "notes": notes or [], "limit": limit}


def _units_of(listing, ids):
    """How many separate passages a selection (ids may carry line ranges) will be."""
    by = {c["id"]: c for c in listing}
    chosen = []
    for i in ids:
        cid, a, b = revise.split_range_id(i)
        c = by.get(cid)
        if c:
            chosen.append({**c, "line_start": a or c["line_start"], "line_end": b or c["line_end"]} if a else c)
    return revise.group_chunks(chosen)


def understand(name, proj, text, chunk_ids=None, last_ids=None, max_units=None):
    """-> {"status": "ready", chunk_ids, parts, title, notes, passages, units}
       | {"status": "choose", why, options, preselect, sections, ...}   (the author is asked to tick or pick)
       | {"status": "empty", why}"""
    text = (text or "").strip()
    max_units = max_units or config.ui_cfg()["max_passages"]
    paper_index.refresh(name, proj, trigger="pass_start", source="web")
    listing = paper_index.list_chunks(name, proj)["chunks"]
    tex = [c for c in listing if c["kind"] == "tex"]
    if not tex:
        return {"status": "empty", "why": "I cannot find any manuscript text in this paper yet. Press \"Get latest from Overleaf\" first, then ask again."}
    by_id = {c["id"]: c for c in tex}
    notes, parts, title, ids = [], [], "", []

    if chunk_ids:  # the author ticked pieces: take them as they are
        ids = [i for i in chunk_ids if revise.split_range_id(i)[0] in by_id]
        if not ids:
            return {"status": "empty", "why": "Those pieces do not exist any more. The paper may have changed; ask again."}
        parts = sorted({by_id[revise.split_range_id(i)[0]]["part"] for i in ids if by_id[revise.split_range_id(i)[0]]["part"]})
        title = "your selection"
    else:
        tags = taxonomy.classify(text, min_ratio=retrieve.REQUEST_RATIO)
        parents = list(dict.fromkeys(t.split(".")[0] for t in tags))
        subs = [t for t in tags if "." in t]
        freq, para = _float_request(text), _para_request(text)
        if freq and parents == ["figures_tables"]:
            parents = []  # "before the figure": the figure is a location here, not the thing to rewrite
        cands, label = [], ""
        if parents:
            got = paper_index.part_chunks(name, proj, parents, refresh_first=False)
            narrowed = [c for c in got if set(c["tags"]) & set(subs)] if subs else []
            cands = [{**by_id[c["id"]], "tags": c["tags"]} for c in (narrowed or got) if c["id"] in by_id]
            label = taxonomy.label(parents[0])
            parts = parents
        if not cands:
            cands, label = _title_match(text, tex)
            parts = sorted({c["part"] for c in cands if c["part"]})
        if not cands and not freq and not para and last_ids:
            ids = [i for i in last_ids if revise.split_range_id(i)[0] in by_id]
            if ids:
                return {"status": "ready", "chunk_ids": ids, "parts": [], "title": "the same place as before", "notes": ["Same place as your last request."],
                        "passages": [], "units": len(_units_of(tex, ids))}
        if not cands:
            if (freq or para) and last_ids:  # "the first paragraph" after talking about a section
                cands = [by_id[revise.split_range_id(i)[0]] for i in last_ids if revise.split_range_id(i)[0] in by_id]
            if not cands:
                return _choose("Which part of the paper do you mean? Pick a section below, or tick the pieces you want improved.", tex, limit=max_units)
        if freq:
            ids, note = _apply_float(proj, cands, tex, freq)
            notes.append(note)
            cands = [by_id[revise.split_range_id(i)[0]] for i in ids if revise.split_range_id(i)[0] in by_id]
            if not ids:
                return _choose(f"{note} There is no {label.lower() or 'text'} there. Pick what you mean.", tex, notes=notes, limit=max_units)
        else:
            ids = [_rid(c) for c in sorted(cands, key=lambda c: c["ord"])]
        if para:
            by_cid = {c["id"]: c for c in cands}
            base = [by_cid[revise.split_range_id(i)[0]] for i in ids if revise.split_range_id(i)[0] in by_cid]
            ids, note = _apply_para(proj, base, para)
            if note:
                notes.append(note)
        title = label.lower() + (", " + notes[0][:1].lower() + notes[0][1:].rstrip(".") if notes else "") if label else "your text"

    groups = _units_of(tex, ids)
    if len(groups) > max_units:
        keep = []
        for i in ids:
            if len(_units_of(tex, keep + [i])) > max_units:
                break
            keep.append(i)
        return _choose(f"That is {len(groups)} separate passages and one request handles at most {max_units}. I ticked the first {len(_units_of(tex, keep))}; "
                       "change the ticks if you like, and ask again for the rest.", tex, preselect=keep, in_part=ids, notes=notes, limit=max_units)
    chosen = [by_id[revise.split_range_id(i)[0]] for i in ids]
    return {"status": "ready", "chunk_ids": ids, "parts": parts, "title": title, "notes": notes, "units": len(groups),
            "passages": [_option(c) for c in chosen]}


def count(name, proj, chunk_ids):
    """How many separate passages a ticked selection makes (the same grouping the real run uses)."""
    tex = [c for c in paper_index.list_chunks(name, proj)["chunks"] if c["kind"] == "tex"]
    groups = _units_of(tex, chunk_ids)
    limit = config.ui_cfg()["max_passages"]
    by = {c["id"]: c for c in tex}
    tokens = sum(by[revise.split_range_id(i)[0]]["tokens"] for i in chunk_ids if revise.split_range_id(i)[0] in by)
    return {"pieces": len(chunk_ids), "units": len(groups), "tokens": tokens, "limit": limit, "ok": 0 < len(groups) <= limit}


def instruction_for(text):
    """What to tell the model. If the message only names a place ("methods", "the text before Figure 1"), ask for a general polish."""
    cleaned = _PARA.sub(" ", _FLOAT.sub(" ", text))
    words = set(taxonomy.words(cleaned.lower()))
    if words & _INTENT_WORDS:
        return text.strip()
    return text.strip() + "\n\nImprove the clarity, flow, grammar and concision of this passage, following the writing advice."


CHIPS = [("Make it clearer", "Make this clearer and easier to follow."), ("More concise", "Make this more concise without losing any content."),
         ("Fix grammar and tense", "Fix grammar, tense and word choice."), ("Improve the flow", "Improve the flow between sentences and paragraphs."),
         ("Use my files", "Use my files.")]


# ------------------------------------------------------------------------------------------- the request, ready to send

def plan(name, proj, instruction, chunk_ids, book_id=None, facts="", online=None):
    """Prepare the request without sending anything. -> what the chat shows ("2 passages, about 900 tokens") plus the hash that proves
    exactly this payload is what gets sent. online: DOIs of references found online that the author picked for this request."""
    res = revise.run(name, proj, instruction, chunk_ids=chunk_ids, book_id=book_id, dry_run=True, source="web", facts=facts, online=online)
    return {"instruction": instruction, "facts": (facts or "").strip()[:3000], "chunk_ids": chunk_ids, "online": list(online or []), "units": res["units"], "passages": res["passages"], "est_tokens_in": res["est_tokens_in"],
            "est_cost": res["est_cost"], "manuscript_tokens": res["manuscript_tokens"], "model": res["model"], "tips": len(res["rules"]),
            "payload_hash": res["payload_hash"], "warnings": res["warnings"], "book": res["book"], "parts": res["parts"], "sources": res.get("sources", []),
            "library": res.get("library", []), "pictures": res.get("pictures", [])}


# ------------------------------------------------------------------------------------------- finding references: about which text?

def refs_place(text, topic):
    """Does a request for references point at a place in the paper ("for the introduction", "the claim in the first paragraph") instead of naming a subject?"""
    from . import scholar
    return bool(_float_request(text) or _para_request(text) or (topic.strip() and len(scholar.terms(topic)) <= 2))


def passage_text(name, proj, chunk_ids, limit=2500):
    """The plain text of the chosen pieces of the manuscript (LaTeX commands left in, they are only used to choose search words), shortest first piece by piece up to `limit` characters."""
    tex = {c["id"]: c for c in paper_index.list_chunks(name, proj)["chunks"] if c["kind"] == "tex"}
    out, total = [], 0
    for i in chunk_ids:
        cid, a, b = revise.split_range_id(i)
        c = tex.get(cid)
        if not c:
            continue
        lines = _lines(proj, c["file"])
        text = "\n".join(lines[(a or c["line_start"]) - 1:(b or c["line_end"])])
        out.append(text)
        total += len(text)
        if total >= limit:
            break
    return "\n\n".join(out)[:limit]


def refs_context(name, proj, text, chunk_ids=None, last_ids=None):
    """What a request for references is about. -> {topic, passage, chunk_ids, where}
    The subject the author wrote, and, when the request points at a place in the paper (or the author ticked some text), that text and where it is, so the references can later be
    used there."""
    topic = refs_topic(text)
    if not chunk_ids and not refs_place(text, topic):
        return {"topic": topic, "passage": "", "chunk_ids": [], "where": ""}
    try:
        u = understand(name, proj, text, chunk_ids=chunk_ids, last_ids=last_ids)
    except (ValueError, FileNotFoundError):
        u = {"status": "empty"}
    if u["status"] != "ready":
        return {"topic": topic, "passage": "", "chunk_ids": [], "where": ""}
    return {"topic": topic, "passage": passage_text(name, proj, u["chunk_ids"]), "chunk_ids": u["chunk_ids"], "where": u["title"]}


# ------------------------------------------------------------------------------------------- peer review: which part?

_REVIEW_NOISE = re.compile(r"\b(peer[\s-]*reviews?|reviewers?|reviews?|critique|referee|evaluate|act as|report|my|our|the|this|a|an|paper|draft|manuscript|article|work|"
                           r"please|section|part|of|for|on|in|and|only|just)\b", re.I)


def review_scope(name, proj, text):
    """'peer review my paper' -> (None, 'the whole paper'); 'review the methods' -> (chunk ids of the methods, 'the methods')."""
    cleaned = _REVIEW_NOISE.sub(" ", text or "")
    paper_index.refresh(name, proj, trigger="pass_start", source="web")
    tex = [c for c in paper_index.list_chunks(name, proj)["chunks"] if c["kind"] == "tex"]
    tags = taxonomy.classify(cleaned, min_ratio=retrieve.REQUEST_RATIO) if cleaned.strip() else []
    parents = list(dict.fromkeys(t.split(".")[0] for t in tags))
    if parents:
        got = paper_index.part_chunks(name, proj, parents, refresh_first=False)
        if got:
            return [c["id"] for c in got], "the " + ", ".join(taxonomy.label(x) for x in parents)
    cands, label = _title_match(cleaned, tex) if cleaned.strip() else ([], "")
    if cands:
        return [c["id"] for c in cands], f"the section “{label}”"
    return None, "the whole paper"
