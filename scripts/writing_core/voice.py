"""How new text reads: natural, not a list of facts, but never more than the author's material supports.

A draft that only states what the sources state reads like a table in sentences. A draft that is allowed to say anything reads well and invents. The way between is the thing a good
author does: connect the facts (what follows from them, what they mean for the question the section asks, why the next step comes) and say it in new words, without adding a fact.
The model is asked to write that way (draft.SYSTEM_PROMPT) and to mark the sentences that only connect (basis source "commentary"). This module checks, on this computer and without a
model, that the result stays on the right side of the line:

  too literal       a long run of words copied from a library source (error: it is someone else's text) or from the author's own outline and notes (warning)
  more than the     a sweeping claim ("for the first time", "proves", "unprecedented", "statistically significant") that nothing the author gave says (error)
  material says
  a comment that    a connecting sentence that brings in several content words the author's material does not have (warning: look at it before you keep it)
  brings in a topic
  padding           stock phrases that add length and no meaning ("it is important to note", "plays a crucial role"), and a draft well over the length asked for (warning)
  too much comment  more of the draft is connecting commentary than facts (warning)
"""
import re

from . import retrieve

RUN_LIBRARY_ERROR = 12                   # words in a row copied from a source in the author's library
RUN_LIBRARY_WARN = 8
RUN_AUTHOR_WARN = 14                     # words in a row copied from the author's own outline or notes
COMMENT_SHARE_WARN = 0.35                # share of the words that is connecting commentary
NEW_WORDS_WARN = 3                       # content words in one commentary sentence that the material does not have
OVER_TARGET = 1.25

# claims that go beyond what a source says, unless a source says them
OVERCLAIM = re.compile(
    r"\b(?:for the first time|first (?:ever|study|to)|unprecedented|ground-?breaking|revolutionary|breakthrough|pioneering|never before|"
    r"prove[sd]?|proven|proving|undoubtedly|unquestionabl[ey]|conclusive(?:ly)?|definitive(?:ly)?|beyond (?:any )?doubt|without (?:a )?doubt|"
    r"it is (?:well|widely|generally|universally) (?:known|established|documented|accepted|recognis?zed)|widely (?:accepted|recogni[sz]ed|known|believed)|universally|"
    r"statistically significant(?:ly)?|significantly (?:higher|lower|more|less|better|worse|improv\w+|reduc\w+|increas\w+|decreas\w+|outperform\w*)|"
    r"clearly (?:shows?|demonstrates?|proves?)|novel (?:approach|method|framework|contribution)|state[- ]of[- ]the[- ]art)\b", re.I)

# stock phrases: length without meaning
PADDING = re.compile(
    r"\b(?:it is (?:important|worth|interesting|crucial|essential) (?:to )?(?:note|noting|mention|mentioning|emphasi[sz]e|highlight|stress)|it should be (?:noted|emphasi[sz]ed|stressed)|"
    r"plays? an? (?:crucial|vital|key|pivotal|significant|important|critical) role|in today'?s (?:world|society|era|age)|in (?:the )?(?:modern|current|present) (?:era|age|day)|"
    r"a testament to|delve[sd]? (?:into|deeper)|rich tapestry|shed(?:s|ding)? (?:new )?light on|needless to say|as (?:previously )?mentioned (?:above|earlier|before)|"
    r"in the realm of|at the end of the day|when it comes to|the fact that|it goes without saying|in an increasingly)\b", re.I)
SEQUENCERS = re.compile(r"(?:^|(?<=[.!?]\s))(?:Moreover|Furthermore|Additionally|In addition),", re.M)

# words that connect and hedge: a commentary sentence made of these and of the author's own words brings in nothing new
CONNECTIVE = set("""therefore however thus hence moreover whereas although while though despite since because given together overall taken suggests suggest suggesting suggested consistent
consistently implies imply implying indicates indicate indicating points pointing evidence reading interpretation interpreted difference differences differ differs similar similarly contrast
likely unlikely possible possibly perhaps could would might should these those their there where about above below across between within against after before during other another also
both each every often rather mainly largely broadly simply question questions section paper study studies analysis approach reason reasons explains explain explanation matters matter means
meaning implication implications context contexts first second third next finally later earlier already still even only most more less much many several various certain particular
general specific specifically especially notably importantly relevant relevance fits fit follows follow following leads lead leading helps help helping shows show showing shown reflect
reflects reflecting reflected capture captures captured picture whole basis based result results finding findings pattern patterns trend trends direction scale extent degree range
narrow wider broader stronger weaker larger smaller higher lower clear clearer unclear open remains remain remaining leave leaves leaving point step steps stage stages reader readers
author authors aim aims goal goals purpose focus focuses focused""".split())


def _words(text):
    t = re.sub(r"\[\[LX\d+\]\]", " ", text or "")
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", t.lower())


def _stem(w):
    for suf in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "es", "ed", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w


def sentences_of(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n\s*\n", re.sub(r"\[\[LX\d+\]\]", "", text or "").strip()) if s.strip()]


# ------------------------------------------------------------------------------------------------ too literal

def longest_run(draft, source, floor=6):
    """The longest stretch of words that the draft has in common with a source, in order. -> (number of words, the words) ; (0, '') when it is shorter than `floor`."""
    d, s = _words(draft), _words(source)
    if len(d) < floor or len(s) < floor:
        return 0, ""
    grams = {}
    for i in range(len(s) - floor + 1):
        grams.setdefault(tuple(s[i:i + floor]), []).append(i)
    best, at, i = 0, 0, 0
    while i <= len(d) - floor:
        hits = grams.get(tuple(d[i:i + floor]))
        if not hits:
            i += 1
            continue
        top = 0
        for j in hits:
            n = floor
            while i + n < len(d) and j + n < len(s) and d[i + n] == s[j + n]:
                n += 1
            top = max(top, n)
        if top > best:
            best, at = top, i
        i += max(1, top - floor + 1)
    return best, " ".join(d[at:at + min(best, 14)]) + (" ..." if best > 14 else "")


# ------------------------------------------------------------------------------------------------ connecting commentary

def commentary_of(draft, basis):
    """The sentences of the draft the model marked as connecting commentary (basis source 'commentary'). A basis line gives the first words of a sentence."""
    marks = []
    for b in basis or []:
        if isinstance(b, dict) and str(b.get("source", "")).strip().lower() == "commentary":
            w = _words(str(b.get("statement", "")))[:6]
            if len(w) >= 2:
                marks.append(w)
    out = []
    for s in sentences_of(draft):
        sw = _words(s)
        if any(sw[:min(len(m), 4)] == m[:min(len(m), 4)] for m in marks):
            out.append(s)
    return out


def _known(context):
    return {_stem(w) for w in _words(context)}


def new_words(sentence, known):
    """Content words of a sentence that neither the author's material nor the vocabulary of connecting prose has."""
    out = []
    for w in _words(sentence):
        if len(w) < 5 or w in retrieve.STOP or w in CONNECTIVE or _stem(w) in known or _stem(w) in {_stem(c) for c in CONNECTIVE}:
            continue
        if w not in out:
            out.append(w)
    return out


# ------------------------------------------------------------------------------------------------ the checks

def check(draft, basis=None, *, material="", library_text="", target_words=0):
    """Validator results for new text, in the shape latex_mask.validate gives. `material` is everything the author gave (outline, notes, sources, library, answers, the text around
    the place, the abstract); `library_text` the excerpts of other people's work among it."""
    res = []

    def add(name, ok, detail="", severity="error"):
        res.append({"name": name, "ok": bool(ok), "severity": severity, "detail": detail if not ok else ""})

    # too literal
    n_lib, run_lib = longest_run(draft, library_text, RUN_LIBRARY_WARN) if library_text else (0, "")
    add("copies_a_source", n_lib < RUN_LIBRARY_ERROR,
        f"{n_lib} words in a row are copied from an article in the library (\"{run_lib}\"); say it in your own words", "error")
    if RUN_LIBRARY_WARN <= n_lib < RUN_LIBRARY_ERROR:
        add("close_to_a_source", False, f"{n_lib} words in a row follow an article in the library (\"{run_lib}\"); put it more in your own words", "warn")
    own = material
    n_own, run_own = longest_run(draft, own, RUN_AUTHOR_WARN) if own else (0, "")
    add("stays_close_to_your_wording", n_own < RUN_AUTHOR_WARN, f"{n_own} words in a row repeat your outline, your notes or the text around it (\"{run_own}\"); it reads more naturally put in new words", "warn")

    # more than the material says
    said = {m.group(0).lower() for m in OVERCLAIM.finditer(material or "")}
    claims = sorted({m.group(0).lower() for m in OVERCLAIM.finditer(draft)} - said)
    add("claims_beyond_the_material", not claims,
        "wording that claims more than anything you gave says: " + ", ".join(f"\"{c}\"" for c in claims) + "; remove it, or say only what the sources say", "error")

    # connecting commentary
    comments = commentary_of(draft, basis)
    if comments:
        words_all = len(_words(draft))
        words_comment = sum(len(_words(s)) for s in comments)
        share = words_comment / words_all if words_all else 0
        add("commentary_proportion", share <= COMMENT_SHARE_WARN,
            f"about {share:.0%} of the draft is connecting commentary rather than facts from your material; keep it to a sentence now and then", "warn")
        known = _known(material)
        drifting = []
        for s in comments:
            nw = new_words(s, known)
            if len(nw) >= NEW_WORDS_WARN:
                drifting.append(f"\"{' '.join(s.split()[:8])}...\" brings in {', '.join(nw[:5])}")
        add("commentary_stays_on_topic", not drifting,
            "a connecting comment that brings in words your material does not have, check it before you keep it: " + "; ".join(drifting[:3]), "warn")

    # padding and length
    pad = sorted({m.group(0).lower() for m in PADDING.finditer(draft)})
    seq = len(SEQUENCERS.findall(draft))
    add("no_padding", not pad and seq < 2,
        ("stock phrases that add length and no meaning: " + ", ".join(f"\"{p}\"" for p in pad) if pad else "") + ("; " if pad and seq >= 2 else "") +
        ("\"Moreover/Furthermore/Additionally\" starts several sentences; let the sentences follow from each other instead" if seq >= 2 else ""), "warn")
    words = len(_words(draft))
    if target_words:
        add("close_to_the_length_asked", words <= max(target_words * OVER_TARGET, target_words + 30),
            f"the draft is {words} words and about {target_words} were asked for; shorter reads better", "warn")
    return res
