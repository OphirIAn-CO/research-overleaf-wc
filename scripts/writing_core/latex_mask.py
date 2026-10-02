"""Keep LaTeX safe while ChatGPT edits prose. Stdlib only.

mask()/unmask()   protected spans (citations, refs, labels, math, floats, headings, comments, URLs) become [[LX001]] placeholders
validate()        checks on the model's output before anyone sees it (placeholders, numbers, dashes, braces, commands, length ...)
hunks()           the change between original and revised text, as reviewable sentence-level hunks that can be accepted one by one
"""
import difflib
import re
from collections import Counter

from .chunk_tex import _group, strip_comment

PH = re.compile(r"\[\[LX(\d{3,5})\]\]")

PROTECT_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "displaymath", "flalign", "alignat", "split", "math",
                "figure", "table", "tabular", "tabularx", "longtable", "lstlisting", "verbatim", "minted", "tikzpicture",
                "algorithm", "algorithmic", "thebibliography", "comment", "wrapfigure", "sidewaystable", "sidewaysfigure")
_ENV = re.compile(r"\\begin\{(%s)(\*?)\}.*?\\end\{\1\2\}" % "|".join(PROTECT_ENVS), re.S)
_DISPLAY = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)", re.S)
_INLINE = re.compile(r"(?<!\\)\$(?:\\.|[^$\\])+?\$", re.S)
_HEAD = re.compile(r"\\(?:part|chapter|(?:sub){0,2}section|(?:sub)?paragraph)\*?(?:\s*\[[^\]]*\])?\s*\{")
_CMDS = ("cite|citep|citet|citealp|citeauthor|citeyear|parencite|textcite|autocite|footcite|nocite|ref|eqref|autoref|cref|Cref|"
         "pageref|nameref|vref|label|url|href|input|include|includegraphics|bibliography|bibliographystyle|usepackage|todo|"
         "newcommand|renewcommand|DeclareMathOperator")
_CMD = re.compile(r"\\(?:%s)\*?(?:\s*\[[^\]]*\])*\s*\{" % _CMDS)
_EXISTING = re.compile(r"\[\[LX\d+\]\]")


def _brace_spans(text, pattern, groups_after=1):
    spans = []
    for m in pattern.finditer(text):
        end = m.end() - 1
        g = _group(text, end)
        if not g:
            continue
        stop = g[1]
        if m.group(0).startswith("\\href"):  # \href{url}{text}: the visible text is prose, but we keep the command whole
            g2 = _group(text, stop) if stop < len(text) and text[stop] == "{" else None
            stop = g2[1] if g2 else stop
        spans.append((m.start(), stop))
    return spans


def _comment_spans(text):
    spans, pos = [], 0
    for line in text.split("\n"):
        kept = strip_comment(line)
        if len(kept) < len(line):
            spans.append((pos + len(kept), pos + len(line)))
        pos += len(line) + 1
    return spans


def protected_spans(text):
    spans = _comment_spans(text)
    spans += [(m.start(), m.end()) for m in _ENV.finditer(text)]
    spans += [(m.start(), m.end()) for m in _DISPLAY.finditer(text)]
    spans += [(m.start(), m.end()) for m in _INLINE.finditer(text)]
    spans += [(m.start(), m.end()) for m in _EXISTING.finditer(text)]
    spans += _brace_spans(text, _HEAD)
    spans += _brace_spans(text, _CMD)
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out, last = [], -1
    for a, b in spans:
        if a >= last:  # leftmost, longest wins; anything inside an already protected span is covered
            out.append((a, b))
            last = b
    return out


def mask(text):
    """-> (masked_text, mapping {'LX001': original_span})."""
    spans = protected_spans(text)
    mapping, pieces, pos = {}, [], 0
    for n, (a, b) in enumerate(spans, 1):
        key = f"LX{n:03d}"
        mapping[key] = text[a:b]
        pieces += [text[pos:a], f"[[{key}]]"]
        pos = b
    pieces.append(text[pos:])
    return "".join(pieces), mapping


def unmask(masked, mapping):
    return PH.sub(lambda m: mapping.get("LX" + m.group(1), m.group(0)), masked)


def placeholders(text):
    return ["LX" + n for n in PH.findall(text)]


# ------------------------------------------------------------------------------------------- validators

_WORD_NUM = {w: str(n) for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_WORD_NUM.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
                  "hundred": "100", "thousand": "1000"})
_WORD_NUM.pop("one")  # "one of the participants" is a pronoun-like use, not a quantity
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_UNITS = {w: n for n, w in enumerate("one two three four five six seven eight nine".split(), 1)}  # "one" only inside compounds
_NUM_RE = re.compile(r"(?<![\w.\\])\d[\d,]*(?:\.\d+)?")
_WORDNUM_RE = re.compile(r"\b(?:(%s)[- ](%s)|(%s))\b" % ("|".join(_TENS), "|".join(_UNITS), "|".join(_WORD_NUM)), re.I)
WHITELIST_CAPS = {"table", "figure", "fig", "section", "equation", "eq", "appendix", "chapter", "supplementary", "algorithm", "theorem",
                  "lemma", "proposition", "definition", "corollary", "i", "we", "our"}


def number_tokens(text):
    t = PH.sub(" ", text)
    out = {n.replace(",", "") for n in _NUM_RE.findall(t)}
    for m in _WORDNUM_RE.finditer(t):
        if m.group(1):
            out.add(str(_TENS[m.group(1).lower()] + _UNITS[m.group(2).lower()]))
        else:
            out.add(_WORD_NUM[m.group(3).lower()])
    return out


def _dash_counts(text):
    t = PH.sub(" ", text)
    return {"em": t.count("\u2014") + len(re.findall(r"---", t)), "en": t.count("\u2013") + len(re.findall(r"(?<!-)--(?!-)", t))}


def _special_count(text):
    t = PH.sub(" ", text)
    return len(re.findall(r"(?<!\\)[%&#_^]", t))


def _balance(text):
    t = PH.sub(" ", text)
    t = re.sub(r"\\[{}]", "", t)
    envs = Counter()
    for m in re.finditer(r"\\(begin|end)\{([^}]*)\}", t):
        envs[m.group(2)] += 1 if m.group(1) == "begin" else -1
    return t.count("{") - t.count("}"), {k: v for k, v in envs.items() if v}


def _commands(text):
    return set(re.findall(r"\\([A-Za-z]+)", PH.sub(" ", text)))


_TOKEN = re.compile(r"\\+(?:[A-Za-z]+|[^A-Za-z\s])")


def command_tokens(text):
    """Every backslash command or escape exactly as written, backslashes included: \\S and \\\\S are different tokens, which the set of command names cannot tell apart."""
    return Counter(_TOKEN.findall(PH.sub(" ", text)))


_DOUBLED = re.compile(r"(?<!\\)\\\\([A-Za-z]+)")


def collapse_doubled(original, revised):
    """A model that answers in JSON sometimes writes a command with its backslash doubled (\\\\S for \\S), which LaTeX reads as a line break followed by the letter S.
    Where the original has the command written once and never doubled, put the single backslash back. -> (text, [commands fixed])"""
    fixed = []

    def fix(m):
        name = m.group(1)
        if ("\\\\" + name) in original or not re.search(r"(?<!\\)\\" + re.escape(name) + r"(?![A-Za-z])", original):
            return m.group(0)
        fixed.append("\\" + name)
        return "\\" + name
    return _DOUBLED.sub(fix, revised), fixed


def _caps(text):
    t = PH.sub(" ", text)
    t = re.sub(r"\\[A-Za-z]+", " ", t)
    acr = set(re.findall(r"\b[A-Z][A-Z0-9]{1,}\b", t))
    mid = set(re.findall(r"(?<=[a-z,;:)] )[A-Z][a-z]{2,}\b", t))
    return acr, mid


def _words(text):
    return len(re.findall(r"[A-Za-z]{2,}", PH.sub(" ", re.sub(r"\\[A-Za-z]+", " ", text))))


def validate(original, revised, min_ratio=0.5, max_ratio=1.6, context=""):
    """Compare masked texts. -> list of {name, ok, severity ('error'|'warn'), detail}. A passage is acceptable iff no error fails.
    `context` is text the revision may legitimately draw on (parts of the author's own files): numbers, acronyms and names that stand there are not new."""
    res = []

    def add(name, ok, detail="", severity="error"):
        res.append({"name": name, "ok": bool(ok), "severity": severity, "detail": detail if not ok else ""})

    po, pr = Counter(placeholders(original)), Counter(placeholders(revised))
    missing, extra = sorted((po - pr).elements()), sorted((pr - po).elements())
    add("placeholders_intact", not missing and not extra,
        "; ".join(filter(None, [f"missing or dropped: {', '.join(missing)}" if missing else "", f"duplicated or invented: {', '.join(extra)}" if extra else ""])))
    add("placeholder_order", placeholders(original) == placeholders(revised) or po != pr,
        "citations/formulas/refs changed their relative order; check they still sit next to the claim they support", "warn")
    new_nums = sorted(number_tokens(revised) - number_tokens(original) - (number_tokens(context) if context else set()))
    add("no_new_numbers", not new_nums, f"numbers not in the original: {', '.join(new_nums)}")
    do, dr = _dash_counts(original), _dash_counts(revised)
    add("no_new_dashes", dr["em"] <= do["em"] and dr["en"] <= do["en"],
        "em/en dashes (—, –, ---, --) are not allowed as punctuation; use commas, semicolons or periods")
    bo, br = _balance(original), _balance(revised)
    add("braces_and_environments_balanced", bo == br, f"brace/environment balance changed: {bo} -> {br}")
    new_cmds = sorted(_commands(revised) - _commands(original))
    cmd_list = ", ".join("\\" + c for c in new_cmds)
    add("no_new_commands", not new_cmds, f"LaTeX commands not in the original: {cmd_list}")
    to, tr = command_tokens(original), command_tokens(revised)
    changed = sorted(t for t in (tr - to).elements() if t.lstrip("\\") not in new_cmds)
    add("commands_unchanged", not changed, "LaTeX that is not in the original or is written differently (a doubled backslash, for example): " + ", ".join(dict.fromkeys(changed)))
    gone = sorted((to - tr).elements())
    add("commands_kept", not gone, "LaTeX in the original that is missing now: " + ", ".join(dict.fromkeys(gone)), "warn")
    add("no_new_special_characters", _special_count(revised) <= _special_count(original),
        "unescaped % & # _ ^ would break compilation (write \\% \\& \\# \\_)")
    wo, wr = _words(original), _words(revised)
    lo, hi = (min_ratio, max_ratio) if wo >= 25 else (0.3, 2.5)
    ratio = wr / wo if wo else 1.0
    add("length_within_bounds", wo == 0 or lo <= ratio <= hi, f"length changed to {ratio:.0%} of the original (allowed {lo:.0%} to {hi:.0%})")
    ao, mo = _caps(original)
    ar, mr = _caps(revised)
    if context:
        eo, em = _caps(context)
        ao, mo = ao | eo, mo | em
    low_original = (original + " " + context).lower() if context else original.lower()
    new_acr = sorted(a for a in ar - ao if a.lower() not in low_original.split() and a not in {"LX"})
    add("no_new_acronyms", not new_acr, f"acronyms not in the original: {', '.join(new_acr)}")
    new_names = sorted(n for n in mr - mo if n.lower() not in low_original and n.lower() not in WHITELIST_CAPS)
    add("no_new_named_entities", not new_names, f"capitalized names not in the original: {', '.join(new_names)}", "warn")
    return res


def errors(results):
    return [r for r in results if not r["ok"] and r["severity"] == "error"]


def summarize(results):
    bad = [r for r in results if not r["ok"]]
    return "; ".join(f"{r['name']}: {r['detail']}" for r in bad)


# ------------------------------------------------------------------------------------------- hunks

def sentences(text):
    """Pieces that join back to exactly `text` (whitespace stays attached to the start of the next piece)."""
    return [p for p in re.split(r"(?<=[.!?])(?=\s)|(?=\n\n)", text) if p != ""]


def word_diff(old, new):
    tok = lambda s: re.findall(r"\s+|\w+|[^\w\s]", s)  # noqa: E731
    a, b = tok(old), tok(new)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(["=", "".join(a[i1:i2])])
        else:
            if i2 > i1:
                out.append(["-", "".join(a[i1:i2])])
            if j2 > j1:
                out.append(["+", "".join(b[j1:j2])])
    return out


def segments(original, revised):
    """-> list of segments: {'type': 'eq', 'text'} or {'type': 'hunk', 'id', 'old', 'new'} covering the whole text.
    Hunks whose placeholders move between them are merged, so any subset of hunks is a consistent text."""
    a, b = sentences(original), sentences(revised)
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    # group into runs: each non-equal op is a hunk candidate; merge neighbours linked by a moving placeholder
    items = []
    for t, i1, i2, j1, j2 in ops:
        if t == "replace" and i2 - i1 == j2 - j1 > 1:  # sentence-for-sentence rewrite: one hunk per sentence
            items += [dict(tag="replace", i1=i1 + d, i2=i1 + d + 1, j1=j1 + d, j2=j1 + d + 1) for d in range(i2 - i1)]
        else:
            items.append(dict(tag=t, i1=i1, i2=i2, j1=j1, j2=j2))
    changed = [k for k, it in enumerate(items) if it["tag"] != "equal"]
    parent = {k: k for k in changed}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    old_ph = {k: set(placeholders("".join(a[items[k]["i1"]:items[k]["i2"]]))) for k in changed}
    new_ph = {k: set(placeholders("".join(b[items[k]["j1"]:items[k]["j2"]]))) for k in changed}
    for x in changed:
        for y in changed:
            if x < y and ((old_ph[x] & new_ph[y]) or (old_ph[y] & new_ph[x]) or (old_ph[x] - new_ph[x]) & (new_ph[y] - old_ph[y])):
                parent[find(y)] = find(x)
    groups = {}
    for k in changed:
        groups.setdefault(find(k), []).append(k)
    # a merged group spans from its first to its last op (equal ops in between are carried inside it)
    spans = sorted((min(g), max(g)) for g in groups.values())
    merged, out_ops, cur = [], [], 0
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    segs, n = [], 0
    pos = 0
    for lo, hi in merged:
        for k in range(pos, lo):
            it = items[k]
            segs.append({"type": "eq", "text": "".join(a[it["i1"]:it["i2"]])})
        first, last = items[lo], items[hi]
        n += 1
        segs.append({"type": "hunk", "id": f"h{n}", "old": "".join(a[first["i1"]:last["i2"]]), "new": "".join(b[first["j1"]:last["j2"]])})
        pos = hi + 1
    for k in range(pos, len(items)):
        it = items[k]
        segs.append({"type": "eq", "text": "".join(a[it["i1"]:it["i2"]])})
    # coalesce neighbouring equal segments
    out = []
    for s in segs:
        if out and s["type"] == "eq" and out[-1]["type"] == "eq":
            out[-1]["text"] += s["text"]
        else:
            out.append(s)
    return out


def assemble(segs, accepted):
    """Final text with only the accepted hunk ids applied."""
    return "".join(s["text"] if s["type"] == "eq" else (s["new"] if s["id"] in accepted else s["old"]) for s in segs)


def attach_reasons(hunks_, changes):
    """Match the model's own change notes (before/after/reason/rule) to our computed hunks by word overlap."""
    wset = lambda s: set(re.findall(r"[A-Za-z0-9]{3,}", PH.sub(" ", s.lower())))  # noqa: E731
    for h in hunks_:
        h.setdefault("reasons", [])
        h.setdefault("rules", [])
    for ch in changes or []:
        cw = wset(str(ch.get("before", "")) + " " + str(ch.get("after", "")))
        best, score = None, 0.0
        for h in hunks_:
            hw = wset(h["old"] + " " + h["new"])
            j = len(cw & hw) / len(cw | hw) if cw | hw else 0
            if j > score:
                best, score = h, j
        if best is not None and score >= 0.25:
            if ch.get("reason") and ch["reason"] not in best["reasons"]:
                best["reasons"].append(str(ch["reason"]))
            if ch.get("rule") and ch["rule"] not in best["rules"]:
                best["rules"].append(str(ch["rule"]))
    return hunks_


# ------------------------------------------------------------------------------------------- text edited by the author

_LOOSE_TAG = re.compile(r"(?:\[\s*){1,2}LX\s*(\d{3,5})(?:\s*\]){1,2}|(?<![\w\[])LX(\d{3,5})(?![\w\]])")


def normalize_tags(text):
    """Tags that went through another tool come back slightly changed ([LX001], [[ LX001 ]], a bare LX001). Put them back in the canonical
    spelling so they are matched like the originals."""
    return _LOOSE_TAG.sub(lambda m: f"[[LX{m.group(1) or m.group(2)}]]", text)


def restore_literals(text, mapping, original):
    """If a protected piece is missing as a tag but its LaTeX appears in the text as it is (the author pasted the 'as LaTeX' version),
    turn that occurrence back into its tag. Only tags the original had, and only pieces that are really missing."""
    have = Counter(placeholders(text))
    want = Counter(placeholders(original))
    for key in sorted(want):
        if have[key] >= want[key]:
            continue
        literal = mapping.get(key)
        if literal and literal in text:
            text = text.replace(literal, f"[[{key}]]", 1)
            have[key] += 1
    return text


def missing_details(original, text, mapping, context_words=6):
    """For every protected piece the text lost: what it was and which words it sat between, so the author can put it back."""
    have = Counter(placeholders(text))
    out = []
    for m in PH.finditer(original):
        key = "LX" + m.group(1)
        if have[key] > 0:
            have[key] -= 1
            continue
        before = re.sub(r"\s+", " ", PH.sub("", original[:m.start()])).split()[-context_words:]
        after = re.sub(r"\s+", " ", PH.sub("", original[m.end():])).split()[:context_words]
        out.append({"tag": key, "latex": mapping.get(key, ""), "words_before": " ".join(before), "words_after": " ".join(after)})
    return out


def check_author_text(original, text, mapping):
    """The author's own version of a masked passage. -> (normalized text, validator results, missing-piece details).
    Same validators as for the model's answer, run on the text after forgiving tag spellings."""
    fixed = restore_literals(normalize_tags(text), mapping, original)
    fixed, doubled = collapse_doubled(original, fixed)
    results = validate(original, fixed)
    if doubled:
        results.append({"name": "doubled_backslashes_fixed", "ok": False, "severity": "warn", "detail": f"I turned {', '.join(dict.fromkeys(doubled))} back into a single backslash, as in your paper (a doubled one would break the LaTeX)"})
    return fixed, results, missing_details(original, fixed, mapping)


# ------------------------------------------------------------------------------------------- headings the model does not need to see

_TAG_LINE = re.compile(r"^(?:\[\[LX\d{3,5}\]\]\s*)+$")


def split_frame(masked):
    """Lines that hold nothing but protected pieces at the very start or end of a passage (the section headings, a label, a float) are
    not sent to the model at all: it has nothing to edit there, and small models tend to drop them. -> (head, body, tail) with
    head + body + tail == masked. Tag-only lines in the middle stay where they are."""
    lines = masked.split("\n")
    skip = lambda ln: not ln.strip() or bool(_TAG_LINE.match(ln.strip()))  # noqa: E731
    i, j = 0, len(lines)
    while i < j and skip(lines[i]):
        i += 1
    while j > i and skip(lines[j - 1]):
        j -= 1
    if i >= j:  # nothing but tags: leave it alone
        return "", masked, ""
    head = "\n".join(lines[:i]) + "\n" if i else ""
    tail = "\n" + "\n".join(lines[j:]) if j < len(lines) else ""
    return head, "\n".join(lines[i:j]), tail


def _words_around(text, n=4):
    return re.findall(r"\S+", PH.sub(" ", text))[-n:] if n > 0 else []


def _find_seq(hay, words, from_end=False):
    """Position of the word sequence in hay (case-insensitive, whitespace-tolerant) -> (start, end) or None."""
    if not words:
        return None
    pat = re.compile(r"\s+".join(re.escape(w) for w in words), re.I)
    ms = list(pat.finditer(hay))
    return (ms[-1] if from_end else ms[0]).span() if ms else None


def repair(original, revised):
    """Put back protected pieces the model dropped, where they sat in the original: a heading or a float on its own line goes before the
    paragraph that followed it, an inline citation or formula right after the words that preceded it. Only restores; never invents.
    -> (text, [{tag, own_line}])  A tag that cannot be placed with confidence is left missing, so the usual check still rejects the answer."""
    need = Counter(placeholders(original)) - Counter(placeholders(revised))
    restored = []
    if not need:
        return revised, restored
    seen = Counter()
    for m in PH.finditer(original):
        key = "LX" + m.group(1)
        seen[key] += 1
        if need.get(key, 0) <= 0:
            continue
        before, after = original[:m.start()], original[m.end():]
        eol = after.find("\n")
        own_line = not before[before.rfind("\n") + 1:].strip() and not (after if eol < 0 else after[:eol]).strip()
        tag = f"[[{key}]]"
        b_words, a_words = _words_around(before), (re.findall(r"\S+", PH.sub(" ", after))[:4])
        placed = False
        if own_line:  # a heading or float on its own line: before the paragraph that followed it, else after the paragraph that preceded it
            for ws in (a_words[:4], a_words[:2]):
                hit = _find_seq(revised, ws)
                if hit:
                    revised = revised[:hit[0]] + tag + "\n" + revised[hit[0]:]
                    placed = True
                    break
            if not placed and not a_words:  # nothing follows it: it closed the passage
                revised = revised.rstrip("\n") + "\n" + tag
                placed = True
            if not placed:
                for ws in (b_words, b_words[-2:]):
                    hit = _find_seq(revised, ws, from_end=True)
                    if hit:
                        nl = revised.find("\n", hit[1])
                        pos = nl if nl >= 0 else len(revised)
                        revised = revised[:pos] + "\n" + tag + revised[pos:]
                        placed = True
                        break
        else:  # inline: right after the words that came before it, else right before the words that came after it
            for ws in (b_words, b_words[-2:]):
                hit = _find_seq(revised, ws, from_end=True)
                if hit:
                    revised = revised[:hit[1]] + (" " if before[-1:].isspace() else "") + tag + revised[hit[1]:]
                    placed = True
                    break
            if not placed:
                for ws in (a_words, a_words[:2]):
                    hit = _find_seq(revised, ws)
                    if hit:
                        revised = revised[:hit[0]] + tag + (" " if after[:1].isspace() else "") + revised[hit[0]:]
                        placed = True
                        break
        if placed:
            need[key] -= 1
            restored.append({"tag": key, "own_line": own_line})
    return revised, restored
