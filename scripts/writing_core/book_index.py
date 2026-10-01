"""Book persistence. One folder per book under <data dir>/books/<id>/:

  outline.json    chapters/sections found in the PDF. A CHECKPOINT you can review and edit before chunks are final
  chunks.jsonl    canonical chunks, in reading order (human-readable, diffable)
  book.sqlite     derived search index (FTS5/BM25 + part tags); rebuilt from chunks.jsonl if missing or damaged
  manifest.json   written last: PDF sha256, outline sha256, counts. Its presence means "import complete"
  events-*.jsonl  metadata-only event log for this book

Import happens once. The same PDF again is a no-op ("skipped, 0 writes"). Rebuilding is deliberate (--force / --rebuild).
"""
import json
import os
import time
from pathlib import Path

from . import chunk_book, config, events, store, taxonomy

SCHEMA = 2


class BookError(Exception):
    pass


def paths(book_id):
    d = config.book_dir(book_id)
    return {"dir": d, "outline": d / "outline.json", "chunks": d / "chunks.jsonl",
            "db": d / "book.sqlite", "manifest": d / "manifest.json"}


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def manifest(book_id):
    return _read_json(paths(book_id)["manifest"])


def read_outline(book_id):
    return _read_json(paths(book_id)["outline"])


def write_outline(book_id, outline):
    """Saves an edited outline (used by the Book page). Does not rebuild chunks; run ingest --rebuild for that."""
    _atomic_write(paths(book_id)["outline"], json.dumps(outline, indent=2, ensure_ascii=False))


def read_chunks(book_id):
    out = []
    try:
        with open(paths(book_id)["chunks"], encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
    except FileNotFoundError:
        raise BookError(f"book {book_id!r} has no chunks yet") from None
    return out


# ------------------------------------------------------------------------------ sqlite (derived)

def build_sqlite(chunks, db_path, meta=None):
    """Create the search index from chunks, atomically (temp file then rename)."""
    db_path = Path(db_path)
    tmp = db_path.with_name(db_path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = store.connect(tmp)
    con.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE chunks(id TEXT PRIMARY KEY, seq INTEGER, chapter INTEGER, section INTEGER, k INTEGER,
            chapter_number TEXT, section_number TEXT, chapter_title TEXT, section_title TEXT, heading_path TEXT,
            pdf_start INTEGER, pdf_end INTEGER, page_start INTEGER, page_end INTEGER, label TEXT,
            text TEXT, tokens INTEGER, rule_card TEXT, kind TEXT);
        CREATE TABLE chunk_parts(chunk_id TEXT, part TEXT);
        CREATE INDEX idx_parts ON chunk_parts(part);
        CREATE INDEX idx_seq ON chunks(seq);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, heading_path, text, rule_card,
            tokenize='porter unicode61');
    """)
    cols = ["id", "seq", "chapter", "section", "k", "chapter_number", "section_number", "chapter_title",
            "section_title", "heading_path", "pdf_start", "pdf_end", "page_start", "page_end", "label", "text", "tokens", "rule_card", "kind"]
    for c in chunks:
        con.execute(f"INSERT INTO chunks({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [c.get(k, "") for k in cols])
        con.executemany("INSERT INTO chunk_parts VALUES(?, ?)", [(c["id"], p) for p in c.get("parts", [])])
        con.execute("INSERT INTO chunks_fts(chunk_id, heading_path, text, rule_card) VALUES(?,?,?,?)",
                    (c["id"], c["heading_path"], c["text"], c.get("rule_card", "")))
    store.meta_set(con, "schema", SCHEMA)
    for k, v in (meta or {}).items():
        store.meta_set(con, k, v)
    con.commit()
    con.close()
    os.replace(tmp, db_path)


def open_book(book_id):
    """Search connection; rebuilds the derived index from chunks.jsonl if it is missing or damaged."""
    p = paths(book_id)
    if not p["manifest"].exists():
        raise BookError(f"book {book_id!r} is not imported. Run: writing.py book-ingest <pdf> --id {book_id}")
    rebuild = lambda: build_sqlite(read_chunks(book_id), p["db"], {"book_id": book_id})  # noqa: E731
    con = store.open_checked(p["db"], rebuild)
    if store.meta_get(con, "schema") != SCHEMA:  # an index built by an older version of this code: it is only a cache, so make it again
        con.close()
        rebuild()
        con = store.connect(p["db"])
    return con


# ------------------------------------------------------------------------------ import

def analyze(pdf, book_id, window=4):
    """Step 1: read the PDF's structure and write outline.json (the checkpoint). Builds no chunks."""
    outline = chunk_book.analyze_outline(pdf, window=window)
    outline["book_id"] = book_id
    write_outline(book_id, outline)
    return outline


def build(pdf, book_id, target=chunk_book.TARGET_TOKENS, maximum=chunk_book.MAX_TOKENS, minimum=chunk_book.MIN_TOKENS):
    """Step 2: outline.json + PDF -> chunks.jsonl -> book.sqlite -> manifest.json (last)."""
    p = paths(book_id)
    outline = read_outline(book_id)
    if not outline:
        raise BookError("no outline.json yet: run the analyze step first")
    pages = chunk_book.clean_pages(chunk_book.extract_pages(pdf))
    chunks, stats = chunk_book.build_chunks(pages, outline, book_id, target, maximum, minimum)
    if not chunks:
        raise BookError("no text chunks were produced. Is the PDF scanned (images only)? It needs selectable text.")
    try:  # advice summaries cost money to make: keep the ones whose passage did not change
        old = {c["id"]: c for c in read_chunks(book_id)}
    except BookError:
        old = {}
    for c in chunks:
        prev = old.get(c["id"])
        if prev and prev.get("rule_card") and prev.get("text") == c["text"]:
            c["rule_card"] = prev["rule_card"]
    _atomic_write(p["chunks"], "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks))
    build_sqlite(chunks, p["db"], {"book_id": book_id})
    man = {"book_id": book_id, "schema": SCHEMA, "source_name": Path(pdf).name, "source_path": str(Path(pdf).resolve()),
           "source_sha256": store.sha256_file(pdf), "outline_sha256": store.sha256_file(p["outline"]),
           "method": outline.get("method"), "n_pdf_pages": len(pages), "n_chunks": len(chunks),
           "n_chapters": len({c["chapter"] for c in chunks}), "tokens": sum(c["tokens"] for c in chunks),
           "built_at": time.time(), "stats": stats, "logic_version": chunk_book.LOGIC_VERSION,
           "rule_cards": sum(1 for c in chunks if c.get("rule_card")), "exercises": sum(1 for c in chunks if c.get("kind") == "exercise")}
    _atomic_write(p["manifest"], json.dumps(man, indent=2))
    return man


def ingest(pdf, book_id, force=False, rebuild=False, outline_only=False, source="cli", window=4):
    """Idempotent import. Returns {status: 'ingested'|'rebuilt'|'outline'|'skipped', ...}."""
    t0 = time.time()
    pdf = Path(pdf)
    if not pdf.is_file():
        raise BookError(f"PDF not found: {pdf}")
    p = paths(book_id)
    p["dir"].mkdir(parents=True, exist_ok=True)
    sha = store.sha256_file(pdf)
    man = manifest(book_id)
    scope = p["dir"]

    if man and not force and not rebuild:
        if man["source_sha256"] != sha:
            raise BookError(f"book id {book_id!r} already holds a different PDF ({man['source_name']}). "
                            "Use another --id, or --force to replace it.")
        note = None
        if p["outline"].exists() and store.sha256_file(p["outline"]) != man["outline_sha256"]:
            note = "outline.json changed since the last build: run again with --rebuild to apply your edits"
        events.log(scope, events.BOOK_INGEST, book=book_id, skipped=True, writes=0, source=source, note=note)
        return {"status": "skipped", "writes": 0, "note": note, "manifest": man}

    method, warnings = None, []
    # The outline is analyzed only when forced or absent: an existing outline.json may hold your edits, and
    # --rebuild / a plain re-run after `--outline-only` must keep them.
    if force or not p["outline"].exists():
        outline = analyze(pdf, book_id, window=window)
        method, warnings = outline.get("method"), outline.get("warnings", [])
        if outline_only:
            events.log(scope, events.BOOK_INGEST, book=book_id, stage="outline", method=method, warnings=warnings,
                       entries=len(outline["entries"]), duration_s=round(time.time() - t0, 2), source=source)
            return {"status": "outline", "outline": outline}
    elif outline_only:
        return {"status": "outline", "outline": read_outline(book_id)}
    if force and man:
        for k in ("chunks", "db", "manifest"):
            p[k].unlink(missing_ok=True)
    built = build(pdf, book_id)
    events.log(scope, events.BOOK_INGEST, book=book_id, skipped=False, stage="build", method=built["method"] or method,
               pages=built["n_pdf_pages"], chapters=built["n_chapters"], chunks=built["n_chunks"], tokens=built["tokens"],
               warnings=warnings, duration_s=round(time.time() - t0, 2), source=source,
               **{k: v for k, v in built["stats"].items() if k != "chunks"})
    return {"status": "rebuilt" if (man or rebuild) else "ingested", "manifest": built, "warnings": warnings}


def update_rule_cards(book_id, cards):
    """Store distilled rules ({chunk_id: 'text'}) in chunks.jsonl (canonical) and rebuild the derived search index. No re-chunking."""
    p = paths(book_id)
    chunks = read_chunks(book_id)
    known = {c["id"] for c in chunks}
    for cid, text in cards.items():
        if cid not in known:
            raise BookError(f"unknown chunk {cid!r}")
    for c in chunks:
        if c["id"] in cards:
            c["rule_card"] = cards[c["id"]]
    _atomic_write(p["chunks"], "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks))
    build_sqlite(chunks, p["db"], {"book_id": book_id})
    man = manifest(book_id) or {}
    man["rule_cards"] = sum(1 for c in chunks if c.get("rule_card"))
    man["rule_cards_at"] = time.time()
    _atomic_write(p["manifest"], json.dumps(man, indent=2))
    return man["rule_cards"]


# ------------------------------------------------------------------------------ reading

def info(book_id):
    man = manifest(book_id)
    if not man:
        raise BookError(f"book {book_id!r} is not imported")
    con = open_book(book_id)
    parts = {r[0]: r[1] for r in con.execute("SELECT part, COUNT(*) FROM chunk_parts GROUP BY part ORDER BY 2 DESC")}
    con.close()
    return {**man, "parts": parts}


def tree(book_id):
    """Chapters -> sections -> chunk summaries, in reading order (for the Book page)."""
    con = open_book(book_id)
    rows = con.execute("SELECT id, seq, chapter, section, k, chapter_title, section_title, label, tokens, page_start, page_end "
                       "FROM chunks ORDER BY seq").fetchall()
    parts = {}
    for r in con.execute("SELECT chunk_id, part FROM chunk_parts"):
        parts.setdefault(r[0], []).append(r[1])
    con.close()
    chapters = {}
    for r in rows:
        ch = chapters.setdefault(r["chapter"], {"chapter": r["chapter"], "title": r["chapter_title"], "sections": {}})
        sec = ch["sections"].setdefault(r["section"], {"section": r["section"], "title": r["section_title"] or "(chapter opening)", "chunks": []})
        sec["chunks"].append({"id": r["id"], "seq": r["seq"], "label": r["label"], "tokens": r["tokens"],
                              "pages": [r["page_start"], r["page_end"]], "parts": parts.get(r["id"], [])})
    return [{**c, "sections": list(c["sections"].values())} for c in chapters.values()]


def get_chunk(book_id, chunk_id):
    con = open_book(book_id)
    r = con.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    if not r:
        con.close()
        raise BookError(f"no chunk {chunk_id!r} in book {book_id!r}")
    d = dict(r)
    d["parts"] = [x[0] for x in con.execute("SELECT part FROM chunk_parts WHERE chunk_id=?", (chunk_id,))]
    for key, op in (("prev", "<"), ("next", ">")):
        n = con.execute(f"SELECT id FROM chunks WHERE seq{op}? ORDER BY seq {'DESC' if op == '<' else 'ASC'} LIMIT 1", (d["seq"],)).fetchone()
        d[key] = n[0] if n else None
    con.close()
    return d
