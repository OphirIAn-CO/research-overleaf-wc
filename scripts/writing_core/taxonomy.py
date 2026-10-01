"""Paper parts and their synonyms. One vocabulary for both sides: the book's chapter/section headings and the
manuscript's `\\section{...}` titles are mapped to the same parts, so "rules for methods" and "my methods text"
pair automatically, and a request like "improve my methodology" routes to `methods`.
"""
import re

# part -> synonym phrases (matched on word boundaries, plural-insensitive)
PARTS = {
    "title": ["title", "titles", "naming the paper", "paper title", "headline"],
    "abstract": ["abstract", "structured abstract", "executive summary"],
    "keywords": ["keyword", "index terms"],
    "introduction": ["introduction", "intro", "opening", "motivation", "research gap", "problem statement",
                     "statement of the problem", "background and motivation", "setting the scene"],
    "background": ["literature review", "background", "related work", "previous work", "prior work",
                   "review of the literature", "state of the art", "theoretical background", "survey of the literature"],
    "theory": ["theory", "hypothesis", "conceptual framework", "theoretical framework", "research question",
               "proposition", "model development", "framework"],
    "methods": ["method", "methodology", "materials and methods", "experimental setup", "experimental design",
                "research design", "study design", "participant", "subject", "sample", "sampling",
                "data collection", "instrument", "measure", "procedure", "data analysis", "statistical analysis",
                "analysis plan", "protocol", "setup", "apparatus", "materials"],
    "results": ["result", "finding", "outcome", "observation", "presenting data", "reporting results"],
    "discussion": ["discussion", "interpretation", "implication", "interpreting results", "explaining results"],
    "limitations": ["limitation", "threat to validity", "weakness", "caveat"],
    "conclusion": ["conclusion", "concluding", "closing remark", "future work", "summary and conclusion"],
    "references": ["reference", "citation", "cite", "bibliography", "citing", "quoting", "paraphrasing",
                   "attribution", "plagiarism", "reference list", "acknowledgment"],
    "figures_tables": ["figure", "table", "graph", "chart", "visual", "caption", "illustration", "display item"],
    "notation": ["equation", "notation", "symbol", "mathematical", "formula", "variable", "unit", "numeral",
                 "reporting statistics", "numbers"],
    "style": ["style", "clarity", "concision", "concise", "wordiness", "tense", "voice", "passive", "active voice",
              "word choice", "grammar", "punctuation", "paragraph", "sentence", "transition", "coherence", "flow",
              "readability", "plain language", "jargon", "hedging", "revision", "editing", "proofreading",
              "abbreviation", "acronym", "words", "writing process", "organization", "structure"],
    "cover_letter": ["cover letter", "letter to the editor", "submission letter"],
    "response_to_reviewers": ["response to reviewers", "reviewer comment", "rebuttal", "revision letter",
                              "responding to reviewers", "peer review", "referee", "reviewer"],
}

# sub-parts of the methods section, used to sharpen routing (they always imply the parent part)
SUBPARTS = {
    "methods.design": ["study design", "research design", "experimental design", "design"],
    "methods.data": ["participant", "subject", "sample", "sampling", "data collection", "dataset", "data source"],
    "methods.measures": ["measure", "instrument", "questionnaire", "scale", "operationalization"],
    "methods.procedure": ["procedure", "protocol", "apparatus", "experimental setup", "implementation"],
    "methods.analysis": ["data analysis", "statistical analysis", "analysis plan", "model estimation", "statistical test"],
}

# parts every request also gets a few chunks of (kept small by the retriever)
ALWAYS_ON = ("style",)

_WORD = re.compile(r"[a-z0-9]+")


def stem(w):
    """Very light plural/ing stripping, applied identically to synonyms and text."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "is", "us")):
        return w[:-1]
    return w


def words(text):
    return [stem(w) for w in _WORD.findall(text.lower())]


def _phrase_index():
    idx = {}
    for part, syns in list(PARTS.items()) + list(SUBPARTS.items()):
        idx[part] = [tuple(words(s)) for s in syns]
    return idx


_IDX = _phrase_index()


def _contains(ws, phrase):
    n = len(phrase)
    return n > 0 and any(tuple(ws[i:i + n]) == phrase for i in range(len(ws) - n + 1))


def scores(text):
    """{part_or_subpart: score}. A match scores the phrase length (longer phrases are more specific)."""
    ws = words(text)
    out = {}
    for part, phrases in _IDX.items():
        s = sum(len(p) for p in phrases if _contains(ws, p))
        if s:
            out[part] = s
    return out


def classify(text, min_ratio=0.5):
    """Parts a heading or a request is about, best first. Sub-parts are returned too (e.g. methods.analysis) and
    always bring their parent part along."""
    sc = scores(text)
    parents = {}
    for k, s in sc.items():
        parent = k.split(".")[0]
        parents[parent] = max(parents.get(parent, 0), s)
    if not parents:
        return []
    top = max(parents.values())
    keep = [p for p, s in sorted(parents.items(), key=lambda kv: -kv[1]) if s >= max(1, top * min_ratio)]
    subs = [k for k in sc if "." in k and k.split(".")[0] in keep]
    return keep + sorted(subs, key=lambda k: -sc[k])


def parents(tags):
    return [t for t in tags if "." not in t]


def label(part):
    return part.replace("_", " ").replace(".", " / ")
