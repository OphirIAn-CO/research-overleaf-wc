"""Aggregations over the metadata-only event logs. Feeds the Monitor page and `writing.py stats`. Read-only.

Per-paper events live in each paper's own folder; book events in the book's folder. The cross-paper view exists only here,
as metadata (counts, ids, tokens); nothing here is ever sent to a model or used in retrieval.
"""
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import common

from . import book_index, config, events


def gather(paper=None, days=None):
    since = time.time() - days * 86400 if days else None
    reg = common.registry()["projects"]
    out = {"papers": {}, "books": {}, "registry": reg}
    for n, p in reg.items():
        if paper and n != paper:
            continue
        d = config.paper_dir(p)
        out["papers"][n] = events.read(d, since=since) if d.exists() else []
    for b in config.list_books():
        out["books"][b] = events.read(config.book_dir(b), since=since)
    return out


def _all(g):
    return [e for evs in g["papers"].values() for e in evs] + [e for evs in g["books"].values() for e in evs]


def _day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _sent(e):
    """A real (non-preview) ChatGPT call."""
    return e["type"] == "revise" and not e.get("dry_run") and e.get("status") not in (None, "preview")


def overview(g):
    allev = _all(g)
    revs = [e for evs in g["papers"].values() for e in evs if _sent(e)]
    ok = [e for e in revs if e.get("status") in ("proposed", "no_changes")]
    decs = [e for evs in g["papers"].values() for e in evs if e["type"] == "decision"]
    costs = [e["cost"] for e in revs if e.get("cost") is not None]
    refresh = [e["ts"] for e in allev if e["type"] == "index_refresh"]
    book_chunks = 0
    for b in g["books"]:
        m = book_index.manifest(b)
        book_chunks += (m or {}).get("n_chunks", 0)
    indexed = sum(1 for n, p in g["registry"].items() if (config.paper_dir(p) / "index.sqlite").exists() and n in g["papers"])
    acc, rej = sum(e.get("accepted", 0) for e in decs), sum(e.get("rejected", 0) for e in decs)
    return {
        "papers": len(g["papers"]), "papers_indexed": indexed, "books": len(g["books"]), "book_chunks": book_chunks,
        "last_refresh": max(refresh) if refresh else None,
        "passes": len(revs), "previews": sum(1 for evs in g["papers"].values() for e in evs if e["type"] == "revise" and e.get("dry_run")),
        "pass_rate": round(len(ok) / len(revs), 3) if revs else None,
        "tokens_in": sum(e.get("tokens_in", 0) for e in revs), "tokens_out": sum(e.get("tokens_out", 0) for e in revs),
        "cost": round(sum(costs), 6) if costs else None,
        "accepted": acc, "rejected": rej, "accept_rate": round(acc / (acc + rej), 3) if acc + rej else None,
        "doi_added": sum(1 for e in allev if e["type"] == "doi_added"), "errors": sum(1 for e in allev if e["type"] == "error"),
    }


def timeline(g, days=30):
    today = datetime.now(timezone.utc).date()
    labels = [(today.fromordinal(today.toordinal() - i)).isoformat() for i in range(days - 1, -1, -1)]
    series = defaultdict(lambda: [0] * days)
    index = {d: i for i, d in enumerate(labels)}
    for e in _all(g):
        i = index.get(_day(e["ts"]))
        if i is not None:
            series[e["type"]][i] += 1
    return {"days": labels, "series": {k: v for k, v in sorted(series.items())}}


def index_health(g, limit=200):
    rows = []
    for n, evs in g["papers"].items():
        for e in evs:
            if e["type"] == "index_refresh":
                rows.append({"ts": e["ts"], "paper": n, "trigger": e.get("trigger"), "added": e.get("added", 0), "changed": e.get("changed", 0),
                             "unchanged": e.get("unchanged", 0), "removed": e.get("removed", 0), "total": e.get("total", 0),
                             "skipped_scan": bool(e.get("skipped_scan")), "duration_s": e.get("duration_s")})
    return sorted(rows, key=lambda r: r["ts"])[-limit:]


def book_usage(g):
    out = []
    for b, evs in g["books"].items():
        try:
            tree = book_index.tree(b)
        except book_index.BookError:
            continue
        counts = Counter(i for e in evs if e["type"] == "retrieve" for i in e.get("ids", []))
        labels, chapters, never = {}, [], []
        for c in tree:
            secs = []
            for s in c["sections"]:
                chunks = [{"id": k["id"], "count": counts.get(k["id"], 0), "label": k["label"]} for k in s["chunks"]]
                for k in chunks:
                    labels[k["id"]] = k["label"]
                    if not k["count"]:
                        never.append(k["id"])
                secs.append({"section": s["section"], "title": s["title"], "count": sum(k["count"] for k in chunks), "chunks": chunks})
            chapters.append({"chapter": c["chapter"], "title": c["title"], "count": sum(s["count"] for s in secs), "sections": secs})
        out.append({"book": b, "retrievals": sum(1 for e in evs if e["type"] == "retrieve"), "chapters": chapters,
                    "top": [{"id": i, "label": labels.get(i, i), "count": n} for i, n in counts.most_common(10)],
                    "never_used": len(never), "total_chunks": len(labels)})
    return out


def retrievals(g, limit=60):
    rows, seen = [], set()
    for n, evs in g["papers"].items():
        for e in evs:
            if e["type"] == "retrieve":
                rows.append({"ts": e["ts"], "paper": n, "kind": e.get("kind"), "query": e.get("query"), "parts": e.get("parts"),
                             "mode": e.get("mode") or e.get("fallback"), "ids": e.get("ids", []), "scores": e.get("scores"),
                             "tokens": e.get("tokens"), "budget": e.get("budget"), "latency_ms": e.get("latency_ms"), "source": e.get("source")})
                seen.add((e.get("query"), tuple(e.get("ids", []))))
    for b, evs in g["books"].items():
        for e in evs:
            if e["type"] == "retrieve" and (e.get("query"), tuple(e.get("ids", []))) not in seen:  # book lookups made without a paper
                rows.append({"ts": e["ts"], "paper": None, "kind": "book", "query": e.get("query"), "parts": e.get("parts"),
                             "mode": e.get("fallback"), "ids": e.get("ids", []), "scores": e.get("scores"), "tokens": e.get("tokens"),
                             "budget": e.get("budget"), "latency_ms": e.get("latency_ms"), "source": e.get("source")})
    return sorted(rows, key=lambda r: r["ts"])[-limit:][::-1]


def efficiency(g, limit=60):
    book_tokens = sum((book_index.manifest(b) or {}).get("tokens", 0) for b in g["books"])
    rows = []
    for n, evs in g["papers"].items():
        for e in evs:
            if _sent(e):
                rows.append({"ts": e["ts"], "paper": n, "what": "ChatGPT pass", "sent": e.get("tokens_in", 0),
                             "manuscript": e.get("manuscript_tokens", 0), "book": book_tokens})
            elif e["type"] == "retrieve" and e.get("kind") == "paper":
                rows.append({"ts": e["ts"], "paper": n, "what": "paper query", "sent": e.get("tokens", 0),
                             "manuscript": e.get("manuscript_tokens", 0), "book": book_tokens})
    rows = sorted(rows, key=lambda r: r["ts"])[-limit:]
    return {"rows": rows, "book_tokens": book_tokens}


def chatgpt(g, limit=60):
    revs = [dict(e, paper=n) for n, evs in g["papers"].items() for e in evs if _sent(e)]
    validators = defaultdict(lambda: {"pass": 0, "fail": 0})
    for e in revs:
        for k, v in (e.get("validators") or {}).items():
            validators[k]["pass" if v else "fail"] += 1
    decs = [e for evs in g["papers"].values() for e in evs if e["type"] == "decision"]
    acc, rej = Counter(), Counter()
    for e in decs:
        acc.update(e.get("rules_accepted") or [])
        rej.update(e.get("rules_rejected") or [])
    rows = [{"ts": e["ts"], "paper": e["paper"], "model": e.get("model"), "tokens_in": e.get("tokens_in", 0), "tokens_out": e.get("tokens_out", 0),
             "latency_ms": e.get("latency_ms", 0), "retries": e.get("retries", 0), "hunks": e.get("hunks", 0), "status": e.get("status"),
             "cost": e.get("cost")} for e in sorted(revs, key=lambda e: e["ts"])[-limit:]]
    return {"passes": rows, "validators": {k: v for k, v in sorted(validators.items())},
            "accepted": sum(e.get("accepted", 0) for e in decs), "rejected": sum(e.get("rejected", 0) for e in decs),
            "rules_rejected": [list(x) for x in rej.most_common(8)],
            "rules_accepted": [list(x) for x in acc.most_common(8)]}


def isolation(g):
    """Every id a paper's events used must belong to that paper (or be a book chunk); book logs must not name a paper."""
    checked, bad = 0, []
    for n, evs in g["papers"].items():
        for e in evs:
            ids = list(e.get("ids") or []) + list(e.get("rule_ids") or [])
            if not ids and not e.get("paper"):
                continue
            checked += 1
            if e.get("paper") not in (None, n):
                bad.append({"ts": e["ts"], "scope": n, "problem": f"event names paper {e.get('paper')!r} in {n!r}'s log"})
            for i in ids:
                if i.startswith("P:") and not i.startswith(f"P:{n}:"):
                    bad.append({"ts": e["ts"], "scope": n, "problem": f"used {i}", "id": i})
                elif not (i.startswith("P:") or i.startswith("B:")):
                    bad.append({"ts": e["ts"], "scope": n, "problem": f"unrecognized id {i!r}", "id": i})
    for b, evs in g["books"].items():
        for e in evs:
            if e["type"] == "retrieve":
                checked += 1
                if "paper" in e:
                    bad.append({"ts": e["ts"], "scope": f"book {b}", "problem": "book log names a paper"})
    return {"checked": checked, "violations": bad, "ok": not bad}


def monitor(paper=None, days=30):
    g = gather(paper=paper, days=days)
    return {"paper": paper, "days": days, "overview": overview(g), "timeline": timeline(g, min(days, 90)), "index_health": index_health(g),
            "book_usage": book_usage(g), "retrievals": retrievals(g), "efficiency": efficiency(g), "chatgpt": chatgpt(g), "isolation": isolation(g)}


def format_text(m):
    o = m["overview"]
    lines = [f"papers {o['papers_indexed']}/{o['papers']} indexed | books {o['books']} ({o['book_chunks']} chunks) | "
             f"ChatGPT passes {o['passes']} (+{o['previews']} previews) | tokens {o['tokens_in']} in / {o['tokens_out']} out"
             + (f" | cost {o['cost']}" if o["cost"] is not None else ""),
             f"validator pass rate {o['pass_rate'] if o['pass_rate'] is not None else '-'} | decisions {o['accepted']} accepted / {o['rejected']} rejected | "
             f"last index refresh {events.iso(o['last_refresh']) if o['last_refresh'] else '-'}"]
    ih = m["index_health"][-5:]
    if ih:
        lines.append("recent index refreshes: " + "; ".join(f"{r['paper']} +{r['added']} ~{r['changed']} ={r['unchanged']} -{r['removed']}"
                                                            f"{' (skip)' if r['skipped_scan'] else ''}" for r in ih))
    for bu in m["book_usage"]:
        lines.append(f"book {bu['book']}: {bu['retrievals']} retrievals, {bu['never_used']}/{bu['total_chunks']} chunks never used")
    iso = m["isolation"]
    lines.append(f"isolation audit: {iso['checked']} events checked, {len(iso['violations'])} violations" + ("" if iso["ok"] else "  <-- LOOK AT THIS"))
    for v in iso["violations"][:5]:
        lines.append(f"  {v['scope']}: {v['problem']}")
    return "\n".join(lines)
