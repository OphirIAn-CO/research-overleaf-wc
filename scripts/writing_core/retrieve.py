"""Retrieval. Book side here (paper side is added in paper_index.py and shares `fts_terms` / `pack`).

book_query(): route the request to paper part(s) -> BM25 inside those parts' chunks (heading weighted up) -> a small capped set
of `style` chunks -> pack into a token budget. Returns ids and page references so every suggestion can cite the book.
"""
import re
import time

from . import book_index, events, store, taxonomy

STOP = set("""a an the and or of to in on for with without my our your me i we you it its this that these those from by as at
is are was were be been being can could should would will may might do does did how what when where which who why whom
please help improve improving improved write writing wrote rewrite rewriting polish polishing check checking make making
better good best paper papers section sections part parts text draft manuscript article about using use used into over
more most some any each very really just also than then so if not no yes""".split())

HEAD_W, TEXT_W, CARD_W = 6.0, 1.0, 3.0  # bm25 column weights: heading_path, text, rule_card
SUBPART_BOOST = 1.35
REQUEST_RATIO = 0.3  # a part named in a short request counts even next to a stronger one ("tense in my methods")


def fts_terms(query, max_terms=14):
    seen, out = set(), []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", query.lower()):
        if w in STOP or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:max_terms]


def fts_match(terms):
    return " OR ".join('"%s"' % t for t in terms)


def check_parts(part):
    """str | list | None -> list of validated tags."""
    if not part:
        return []
    tags = [part] if isinstance(part, str) else list(part)
    valid = set(taxonomy.PARTS) | set(taxonomy.SUBPARTS)
    bad = [t for t in tags if t not in valid]
    if bad:
        raise ValueError(f"unknown part {bad[0]!r}. Valid parts: {', '.join(sorted(taxonomy.PARTS))}")
    return tags


def short_tokens(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def book_query(book_id, query, part=None, budget=2000, max_chunks=6, style_cap=2, paper=None, paper_scope=None, source="cli"):
    """-> dict(chunks=[...], parts, terms, tokens, budget, candidates, fallback, trace, latency_ms).

    paper/paper_scope: when the lookup is made on behalf of a paper, the event is also written to that paper's own log
    (with the chunk ids it used) so the Monitor's isolation audit can check them; the book's log never names a paper."""
    t0 = time.time()
    explicit = check_parts(part)
    routed = explicit or taxonomy.classify(query, min_ratio=REQUEST_RATIO)
    parents = list(dict.fromkeys(t.split(".")[0] for t in routed))
    subs = [t for t in routed if "." in t]
    terms = fts_terms(query)
    con = book_index.open_book(book_id)
    try:
        ranked, fallback = _rank(con, terms, parents, subs, limit=max(max_chunks * 4, 12))
        if not ranked and parents:  # the request named a part but no chunk text matches: reading order within the part
            ranked = _reading_order(con, parents, limit=max_chunks)
            fallback = "reading-order"
        if not ranked and terms:  # nothing is tagged with that part (or no part named): search the whole book
            ranked, _ = _rank(con, terms, [], [], limit=max_chunks * 2)
            fallback = "whole-book"
        style = []
        if "style" not in parents and style_cap and terms:
            style, _ = _rank(con, terms, ["style"], [], limit=style_cap)
        chosen, used = [], 0
        seen = set()
        for cand, is_style in [(c, False) for c in ranked] + [(c, True) for c in style]:
            if cand["id"] in seen:
                continue
            cap = max_chunks if not is_style else max_chunks + style_cap
            room = budget - used
            if len(chosen) >= cap or (cand["tokens"] > room and chosen):
                continue
            if is_style and used + cand["tokens"] > budget * 1.25:
                continue
            seen.add(cand["id"])
            cand["via"] = "style" if is_style else "routed"
            chosen.append(cand)
            used += cand["tokens"]
        for c in chosen:
            c["parts"] = [r[0] for r in con.execute("SELECT part FROM chunk_parts WHERE chunk_id=?", (c["id"],))]
        candidates = len(ranked)
    finally:
        con.close()
    res = {"book": book_id, "query": query, "parts": routed, "terms": terms, "chunks": chosen, "tokens": used,
           "budget": budget, "candidates": candidates, "fallback": fallback,
           "latency_ms": round((time.time() - t0) * 1000, 1)}
    res["trace"] = (f"[book {book_id}: {len(chosen)} chunks, {short_tokens(used)} tok"
                    f"{' (' + ', '.join(parents) + ')' if parents else ''}{', ' + fallback if fallback else ''}]")
    ev = dict(kind="book", book=book_id, query=query, parts=routed, explicit_part=bool(explicit), terms=terms,
              candidates=candidates, ids=[c["id"] for c in chosen], scores=[round(c["score"], 3) for c in chosen],
              tokens=used, budget=budget, fallback=fallback, latency_ms=res["latency_ms"], source=source)
    events.log(book_index.paths(book_id)["dir"], events.RETRIEVE, **ev)
    if paper_scope:
        events.log(paper_scope, events.RETRIEVE, paper=paper, **ev)
    return res


def _row(r, score, via=None):
    return {"id": r["id"], "label": r["label"], "pages": _pages(r["page_start"], r["page_end"]),
            "pdf_pages": _pages(r["pdf_start"], r["pdf_end"]), "score": float(score), "tokens": r["tokens"], "text": r["text"],
            "heading_path": r["heading_path"], "rule_card": r["rule_card"] or ""}


def _pages(a, b):
    return f"{a}" if a == b else f"{a}-{b}"


def _rank(con, terms, parents, subs, limit):
    if not terms:
        return [], None
    sql = ("SELECT c.*, bm25(chunks_fts, 0.0, ?, ?, ?) AS s FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.chunk_id "
           "WHERE chunks_fts MATCH ?")
    args = [HEAD_W, TEXT_W, CARD_W, fts_match(terms)]
    if parents:
        sql += " AND c.id IN (SELECT chunk_id FROM chunk_parts WHERE part IN (%s))" % ",".join("?" * len(parents))
        args += parents
    sql += " ORDER BY s LIMIT ?"
    args.append(limit * 3)
    rows = con.execute(sql, args).fetchall()
    out = []
    for r in rows:
        score = -r["s"]
        if subs:
            tags = {x[0] for x in con.execute("SELECT part FROM chunk_parts WHERE chunk_id=?", (r["id"],))}
            if tags & set(subs):
                score *= SUBPART_BOOST
        out.append(_row(r, score))
    out.sort(key=lambda c: -c["score"])
    return out[:limit], None


def _reading_order(con, parents, limit):
    rows = con.execute("SELECT c.* FROM chunks c WHERE c.id IN (SELECT chunk_id FROM chunk_parts WHERE part IN (%s)) "
                       "ORDER BY c.seq LIMIT ?" % ",".join("?" * len(parents)), [*parents, limit]).fetchall()
    return [_row(r, 0.0) for r in rows]


def format_book_result(res, with_text=True):
    """Plain text for the CLI and the MCP tool. Page numbers are the book's printed ones."""
    lines = [res["trace"]]
    if res["parts"]:
        lines.append("routed to: " + ", ".join(res["parts"]))
    for c in res["chunks"]:
        lines.append(f"\n--- {c['id']} | {c['label']} | score {c['score']:.2f} | ~{c['tokens']} tok | {c['via']}")
        if with_text:
            lines.append(c["rule_card"] + "\n" + c["text"] if c["rule_card"] else c["text"])
    return "\n".join(lines)
