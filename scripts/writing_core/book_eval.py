"""Retrieval quality check for a book index: a list of questions with what a good answer must contain, scored as hit@k.

eval.json:  [{"query": "how do I describe my sample", "part": null,
              "expect": {"title_contains": ["sampl", "participant"]}}]      # any chunk heading containing any substring
            or       {"expect": {"pages": [141, 150]}}                      # any chunk whose printed pages overlap this range
Edit it for your book; `eval --init` writes a self-check from the book's own headings, and
examples/book_eval.example.json is a 25-question generic starting point.
"""
import json
from pathlib import Path

from . import book_index, retrieve


def _hit(chunk, expect):
    hp = chunk["heading_path"].lower()
    if any(s.lower() in hp for s in expect.get("title_contains", [])):
        return True
    if "pages" in expect:
        a, b = expect["pages"]
        lo, hi = (int(x) for x in (chunk["pages"].split("-") + [chunk["pages"]])[:2])
        return lo <= b and hi >= a
    return False


def run(book_id, items, k=3, budget=2000):
    results, hits = [], 0
    for it in items:
        res = retrieve.book_query(book_id, it["query"], part=it.get("part"), budget=budget, max_chunks=k, style_cap=0, source="eval")
        top = res["chunks"][:k]
        ok = any(_hit(c, it.get("expect", {})) for c in top)
        hits += ok
        results.append({"query": it["query"], "hit": ok, "top": [c["id"].split(":")[-1] + " " + c["heading_path"] for c in top]})
    n = len(items) or 1
    return {"k": k, "n": len(items), "hits": hits, "hit_at_k": round(hits / n, 3), "results": results}


def from_headings(book_id, n=25):
    """Self-check: ask for each (sampled) section by its own heading words; its own section should come back."""
    tree = book_index.tree(book_id)
    secs = [(c["title"], s["title"]) for c in tree for s in c["sections"] if s["title"] != "(chapter opening)"]
    step = max(1, len(secs) // n)
    items = []
    for chap, sec in secs[::step][:n]:
        items.append({"query": f"how to write {sec.lower()}", "part": None, "expect": {"title_contains": [sec]}})
    return items


def load(book_id, path=None):
    p = Path(path) if path else book_index.paths(book_id)["dir"] / "eval.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} not found. Create it (see examples/book_eval.example.json) or run `eval --init`.")
    return json.loads(p.read_text())
