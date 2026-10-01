"""Per-paper index: <paper>/.overleaf-skill/index.sqlite (a derived cache; the .tex files are the truth).

Isolation is physical: one file per paper, opened only through `open_index`, which refuses a file whose stored remote id is
not this paper's (a copied folder) and rebuilds on rename. Chunk ids carry the paper name (P:<paper>:...), which the Monitor's
isolation audit checks against every logged event.

Refresh is incremental and lazy: a cheap fingerprint of the source files decides whether to parse at all; then per-chunk content
hashes decide what to rewrite. Added / changed / unchanged / removed counts are logged on every pass (event `index_refresh`).
"""
import json
import subprocess
import time
from pathlib import Path

import common

from . import chunk_tex, config, events, retrieve, store, taxonomy

SCHEMA = 1
KINDS_DEFAULT = ("tex", "memory", "review")


class IsolationError(Exception):
    pass


def index_path(proj):
    return config.paper_dir(proj) / "index.sqlite"


def _identity(name, proj):
    return {"paper_name": name, "remote_id": common.remote_id(proj)}


_CHECKED = set()


def ensure_ignored(proj):
    """Make sure .overleaf-skill/ can never be committed (overleaf_sync runs `git add -A`). Prefers the clone-local
    .git/info/exclude: nothing is added to the repo that Overleaf would receive."""
    root = Path(proj["path"])
    if str(root) in _CHECKED:
        return "checked"
    probe = ".overleaf-skill/index.sqlite"
    r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", probe], capture_output=True)
    if r.returncode == 0:
        _CHECKED.add(str(root))
        return "already ignored"
    exclude = root / ".git" / "info" / "exclude"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude, "a", encoding="utf-8") as f:
            f.write(("\n" if exclude.exists() and exclude.stat().st_size and not exclude.read_text().endswith("\n") else "")
                    + ".overleaf-skill/\n")
        _CHECKED.add(str(root))
        return "added to .git/info/exclude"
    except OSError as e:
        raise RuntimeError(f"cannot protect .overleaf-skill/ from git ({e}); refusing to write index files") from e


SCHEMA_SQL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE chunks(id TEXT PRIMARY KEY, kind TEXT, file TEXT, line_start INTEGER, line_end INTEGER, level INTEGER,
    title TEXT, section_path TEXT, part TEXT, tags TEXT, text TEXT, plain TEXT, hash TEXT, tokens INTEGER, ord INTEGER);
CREATE INDEX idx_kind_ord ON chunks(kind, ord);
CREATE TABLE chunk_parts(chunk_id TEXT, part TEXT);
CREATE INDEX idx_pparts ON chunk_parts(part);
CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, section_path, plain, tokenize='porter unicode61');
"""


def _create(path, ident):
    path = Path(path)
    con = store.connect(path)
    con.executescript(SCHEMA_SQL)
    store.meta_set(con, "schema", SCHEMA)
    for k, v in ident.items():
        store.meta_set(con, k, v)
    con.commit()
    con.close()


def open_index(name, proj):
    """Connection to this paper's index (created on first use). Raises IsolationError for another paper's file."""
    ensure_ignored(proj)
    path = index_path(proj)
    ident = _identity(name, proj)
    con = store.open_checked(path, lambda: _create(path, ident))
    other = store.meta_get(con, "remote_id")
    if other != ident["remote_id"]:
        con.close()
        raise IsolationError(f"{path} belongs to another Overleaf project (remote id {other}); refusing to use it for "
                             f"{name!r} (remote id {ident['remote_id']}). Delete it to rebuild from this paper's own files.")
    if store.meta_get(con, "paper_name") != name:  # same project registered under a new name: ids change, rebuild the cache
        con.close()
        path.unlink()
        _create(path, ident)
        con = store.connect(path)
    return con


# ------------------------------------------------------------------------------ sources and refresh

def _sources(proj):
    root = Path(proj["path"])
    files = [p for p in root.rglob("*.tex") if ".git" not in p.parts and ".overleaf-skill" not in p.parts]
    files += [root / proj["bib"]] if (root / proj["bib"]).exists() else []
    d = config.paper_dir(proj)
    files += [d / "memory.md"] if (d / "memory.md").exists() else []
    files += sorted((d / "reviews").glob("*.md")) if (d / "reviews").is_dir() else []
    return sorted(set(files))


def fingerprint(proj):
    root = Path(proj["path"])
    rows = []
    for p in _sources(proj):
        st = p.stat()
        rows.append((str(p.relative_to(root)), st.st_mtime_ns, st.st_size))
    return store.sha256_text(json.dumps([SCHEMA, proj.get("main"), rows]))


def gather(name, proj):
    root = Path(proj["path"])
    chunks, warnings, outline, files, main = [], [], [], [], None
    try:
        parsed = chunk_tex.parse_paper(root, name, root / proj["main"] if proj.get("main") else None)
        chunks += parsed["chunks"]
        outline, files, warnings, main = parsed["outline"], parsed["files"], parsed["warnings"], parsed["main"]
    except FileNotFoundError as e:
        warnings.append(str(e))
    d = config.paper_dir(proj)
    mem = d / "memory.md"
    if mem.exists():
        chunks += chunk_tex.parse_markdown(mem.read_text(encoding="utf-8", errors="replace"), name, ".overleaf-skill/memory.md", "memory")
    for rv in sorted((d / "reviews").glob("*.md")) if (d / "reviews").is_dir() else []:
        chunks += chunk_tex.parse_markdown(rv.read_text(encoding="utf-8", errors="replace"), name, f".overleaf-skill/reviews/{rv.name}", "review")
    bib = root / proj["bib"]
    if bib.exists():
        chunks += chunk_tex.parse_bib(bib.read_text(encoding="utf-8", errors="replace"), name, proj["bib"])
    return chunks, {"outline": outline, "files": files, "warnings": warnings, "main": main}


def _tex_tokens(con):
    return con.execute("SELECT COALESCE(SUM(tokens), 0) FROM chunks WHERE kind='tex'").fetchone()[0]


def refresh(name, proj, trigger="query", force=False, source="cli"):
    t0 = time.time()
    scope = config.paper_dir(proj)
    con = open_index(name, proj)
    try:
        fp = fingerprint(proj)
        total = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if not force and total and store.meta_get(con, "fingerprint") == fp:
            res = {"added": 0, "changed": 0, "removed": 0, "unchanged": total, "total": total, "skipped_scan": True,
                   "tex_tokens": _tex_tokens(con), "warnings": store.meta_get(con, "warnings", [])}
        else:
            chunks, info = gather(name, proj)
            con.close()
            with store.write_lease(index_path(proj)):
                con = store.connect(index_path(proj))
                res = _apply(con, chunks, info, fp)
            res["skipped_scan"] = False
    finally:
        con.close()
    res["duration_s"] = round(time.time() - t0, 3)
    events.log(scope, events.INDEX_REFRESH, paper=name, trigger=trigger, source=source, **{k: v for k, v in res.items() if k != "warnings"},
               warnings=res.get("warnings", []))
    return res


def _apply(con, chunks, info, fp):
    existing = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, hash, ord FROM chunks")}
    fresh = {c["id"]: c for c in chunks}
    added = [i for i in fresh if i not in existing]
    changed = [i for i in fresh if i in existing and existing[i][0] != fresh[i]["hash"]]
    removed = [i for i in existing if i not in fresh]
    unchanged = len(fresh) - len(added) - len(changed)
    with con:
        for cid in removed + changed:
            con.execute("DELETE FROM chunks WHERE id=?", (cid,))
            con.execute("DELETE FROM chunk_parts WHERE chunk_id=?", (cid,))
            con.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (cid,))
        for cid in added + changed:
            c = fresh[cid]
            con.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, c["kind"], c["file"], c["line_start"], c["line_end"], c["level"], c["title"], c["section_path"],
                         c["part"], json.dumps(c["tags"]), c["text"], c["plain"], c["hash"], c["tokens"], c["ord"]))
            con.executemany("INSERT INTO chunk_parts VALUES(?,?)", [(cid, t) for t in c["tags"]])
            con.execute("INSERT INTO chunks_fts(chunk_id, section_path, plain) VALUES(?,?,?)", (cid, c["section_path"], c["plain"]))
        # keep document order fresh even for chunks whose text did not change (an earlier section grew or shrank)
        con.executemany("UPDATE chunks SET ord=? WHERE id=?",
                        [(c["ord"], cid) for cid, c in fresh.items() if cid in existing and existing[cid][1] != c["ord"]])
        store.meta_set(con, "fingerprint", fp)
        store.meta_set(con, "indexed_at", time.time())
        store.meta_set(con, "outline", info["outline"])
        store.meta_set(con, "files", info["files"])
        store.meta_set(con, "main", info["main"])
        store.meta_set(con, "warnings", info["warnings"])
        store.meta_set(con, "manuscript_tokens", sum(c["tokens"] for c in chunks if c["kind"] == "tex"))
    return {"added": len(added), "changed": len(changed), "removed": len(removed), "unchanged": unchanged,
            "total": len(fresh), "tex_tokens": sum(c["tokens"] for c in chunks if c["kind"] == "tex"), "warnings": info["warnings"]}


# ------------------------------------------------------------------------------ reading

def _chunk(r, with_text=True):
    d = {"id": r["id"], "kind": r["kind"], "file": r["file"], "line_start": r["line_start"], "line_end": r["line_end"],
         "section_path": r["section_path"], "part": r["part"], "tags": json.loads(r["tags"] or "[]"), "tokens": r["tokens"], "ord": r["ord"],
         "level": r["level"], "title": r["title"], "hash": r["hash"]}
    if with_text:
        d["text"] = r["text"]
    return d


def outline(name, proj, refresh_first=True, source="cli"):
    if refresh_first:
        refresh(name, proj, trigger="outline", source=source)
    con = open_index(name, proj)
    try:
        return {"paper": name, "main": store.meta_get(con, "main"), "files": store.meta_get(con, "files", []),
                "manuscript_tokens": store.meta_get(con, "manuscript_tokens", 0), "outline": store.meta_get(con, "outline", []),
                "counts": {r[0]: r[1] for r in con.execute("SELECT kind, COUNT(*) FROM chunks GROUP BY kind")},
                "warnings": store.meta_get(con, "warnings", []), "indexed_at": store.meta_get(con, "indexed_at")}
    finally:
        con.close()


def get_chunks(name, proj, ids):
    con = open_index(name, proj)
    try:
        out = []
        for i in ids:
            r = con.execute("SELECT * FROM chunks WHERE id=?", (i,)).fetchone()
            if r:
                out.append(_chunk(r))
        return out
    finally:
        con.close()


def part_chunks(name, proj, part, refresh_first=True, trigger="pass_start", source="cli"):
    """Every tex chunk of a part (or sub-part) in document order, e.g. the whole methods section (index refreshed first)."""
    parts = retrieve.check_parts(part)
    if refresh_first:
        refresh(name, proj, trigger=trigger, source=source)
    con = open_index(name, proj)
    try:
        marks = ",".join("?" * len(parts))
        rows = con.execute(f"SELECT * FROM chunks WHERE kind='tex' AND id IN (SELECT chunk_id FROM chunk_parts WHERE part IN ({marks})) "
                           "ORDER BY ord", parts).fetchall()
        return [_chunk(r) for r in rows]
    finally:
        con.close()


def query(name, proj, query="", part=None, kind=None, budget=2500, limit=8, trigger="query", source="cli", refresh_first=True):
    """Find the relevant chunks of THIS paper. Part-first: when the request names a part ("my methods"), that whole part comes
    back in document order if it fits the budget; otherwise the best-matching chunks inside it; otherwise a full-text search."""
    t0 = time.time()
    rf = refresh(name, proj, trigger=trigger, source=source) if refresh_first else None
    explicit = retrieve.check_parts(part)
    routed = explicit or (taxonomy.classify(query, min_ratio=retrieve.REQUEST_RATIO) if query else [])
    parents = list(dict.fromkeys(t.split(".")[0] for t in routed))
    subs = [t for t in routed if "." in t]
    terms = retrieve.fts_terms(query) if query else []
    kinds = [kind] if isinstance(kind, str) else list(kind or KINDS_DEFAULT)
    con = open_index(name, proj)
    mode, truncated, chosen, used = "none", 0, [], 0
    try:
        manuscript = store.meta_get(con, "manuscript_tokens", 0)
        if not query and not explicit:
            return {"paper": name, "mode": "outline", **outline(name, proj, refresh_first=False)}
        rows = []
        if parents and "tex" in kinds:
            marks = ",".join("?" * len(parents))
            rows = con.execute(f"SELECT * FROM chunks WHERE kind='tex' AND id IN (SELECT chunk_id FROM chunk_parts WHERE part IN ({marks})) "
                               "ORDER BY ord", parents).fetchall()
        if rows:
            cands = [_chunk(r) for r in rows]
            narrowed = [c for c in cands if set(c["tags"]) & set(subs)] if subs else []
            cands = narrowed or cands  # "my statistical analysis" -> just that subsection when the paper has one
            if sum(c["tokens"] for c in cands) <= budget:
                chosen, mode = cands, "part"
            else:
                ranked = _rank(con, terms, kinds, [c["id"] for c in cands], limit=len(cands)) if terms else []
                order = ranked or [c["id"] for c in cands]
                by_id = {c["id"]: c for c in cands}
                for cid in order:
                    c = by_id[cid]
                    if used + c["tokens"] > budget and chosen:
                        continue
                    chosen.append(c)
                    used += c["tokens"]
                chosen.sort(key=lambda c: c["ord"])
                mode = "part-ranked" if ranked else "part-truncated"
                truncated = len(cands) - len(chosen)
        elif terms:
            ids = _rank(con, terms, kinds, None, limit=limit * 3)
            mode = "search" if ids else "none"
            for cid in ids:
                r = con.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
                if len(chosen) >= limit or (used + r["tokens"] > budget and chosen):
                    continue
                chosen.append(_chunk(r))
                used += r["tokens"]
        used = sum(c["tokens"] for c in chosen)
    finally:
        con.close()
    res = {"paper": name, "mode": mode, "parts": routed, "terms": terms, "chunks": chosen, "tokens": used, "budget": budget,
           "manuscript_tokens": manuscript, "truncated": truncated, "latency_ms": round((time.time() - t0) * 1000, 1), "refresh": rf}
    chg = f" | index: {rf['changed'] + rf['added']} changed, {rf['unchanged']} unchanged" if rf else ""
    res["trace"] = (f"[paper {name}: {len(chosen)} chunks, {retrieve.short_tokens(used)} of {retrieve.short_tokens(manuscript)} tok"
                    f"{' (' + ', '.join(parents) + ')' if parents else ''}, {mode}{chg}]")
    events.log(config.paper_dir(proj), events.RETRIEVE, paper=name, kind="paper", query=query, parts=routed, terms=terms, mode=mode,
               ids=[c["id"] for c in chosen], tokens=used, budget=budget, manuscript_tokens=manuscript, truncated=truncated,
               latency_ms=res["latency_ms"], source=source)
    return res


def _rank(con, terms, kinds, only_ids, limit):
    marks = ",".join("?" * len(kinds))
    sql = (f"SELECT c.id, bm25(chunks_fts, 0.0, 4.0, 1.0) AS s FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.chunk_id "
           f"WHERE chunks_fts MATCH ? AND c.kind IN ({marks}) ORDER BY s LIMIT ?")
    rows = con.execute(sql, [retrieve.fts_match(terms), *kinds, 1000 if only_ids else limit]).fetchall()
    ids = [r[0] for r in rows]
    if only_ids:
        keep = set(only_ids)
        return [i for i in ids if i in keep][:limit]
    return ids


def format_paper_result(res, with_text=True):
    if res.get("mode") == "outline":
        lines = [f"paper {res['paper']}: main {res['main']}, ~{res['manuscript_tokens']} tokens, chunks {res['counts']}"]
        for o in res["outline"]:
            lines.append(f"{'  ' * max(o['level'], 0)}{o['title']}  [{o.get('part') or '-'}]  {o.get('file', '')}:{o.get('line', '')}")
        return "\n".join(lines + [f"warning: {w}" for w in res["warnings"]])
    lines = [res["trace"]]
    for c in res["chunks"]:
        lines.append(f"\n--- {c['id']} | {c['file']}:{c['line_start']}-{c['line_end']} | {c['section_path']} | ~{c['tokens']} tok")
        if with_text:
            lines.append(c["text"])
    if res.get("truncated"):
        lines.append(f"\n({res['truncated']} more chunks of this part not shown; raise the budget)")
    return "\n".join(lines)
