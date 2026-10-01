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
MAX_TEXT = 4000
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


# ------------------------------------------------------------------------------------------- what kind of message is it

_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I)
_REVIEW = re.compile(r"\bpeer[\s-]*review|\breview\s+(?:my|the|this|our)\b|\bcritique\b|\bact\s+as\s+(?:a\s+)?(?:peer\s+)?reviewer\b|"
                     r"\bevaluate\s+(?:my|the|this|our)\s+(?:paper|draft|manuscript|article|work)\b|\breferee\s+report\b", re.I)
_HELP = re.compile(r"^\s*(?:help|how does (?:this|it) work\??|what can you do\??|\?)\s*$", re.I)


def find_dois(text):
    return [d.rstrip(".,;)") for d in _DOI.findall(text or "")]


def intent(text):
    """-> 'refs' | 'review' | 'help' | 'edit'"""
    if find_dois(text):
        return "refs"
    if _HELP.match(text or ""):
        return "help"
    if _REVIEW.search(text or ""):
        return "review"
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
         ("Fix grammar and tense", "Fix grammar, tense and word choice."), ("Improve the flow", "Improve the flow between sentences and paragraphs.")]


# ------------------------------------------------------------------------------------------- the request, ready to send

def plan(name, proj, instruction, chunk_ids, book_id=None):
    """Prepare the request without sending anything. -> what the chat shows ("2 passages, about 900 tokens") plus the hash that proves
    exactly this payload is what gets sent."""
    res = revise.run(name, proj, instruction, chunk_ids=chunk_ids, book_id=book_id, dry_run=True, source="web")
    return {"instruction": instruction, "chunk_ids": chunk_ids, "units": res["units"], "passages": res["passages"], "est_tokens_in": res["est_tokens_in"],
            "est_cost": res["est_cost"], "manuscript_tokens": res["manuscript_tokens"], "model": res["model"], "tips": len(res["rules"]),
            "payload_hash": res["payload_hash"], "warnings": res["warnings"], "book": res["book"], "parts": res["parts"]}


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
