#!/usr/bin/env python3
"""Writing coach CLI: book import, retrieval, evaluation. Stdlib only (book import also needs poppler's pdftotext).

  writing.py book-ingest <pdf> --id ID [--outline-only] [--rebuild] [--force]
        Import a book ONCE. Same PDF again = "skipped, 0 writes". --outline-only stops after writing outline.json so you can
        review/edit it; --rebuild re-chunks using your edited outline.json; --force starts over (re-analyzes the outline too).
  writing.py book-list | book-info [--id ID]
  writing.py book-query "<request>" [--id ID] [--part P] [--budget N] [--max N] [--no-text] [--json]
  writing.py book-chunk <chunk-id> [--id ID]
  writing.py eval [--id ID] [--file F] [-k 3] [--init]
  writing.py book-rules [--id ID] [--batch 4] [--send HASH]
        One-time: distil each book chunk into 3-6 short rules (so later requests carry rules, not raw book text). Sends the book's
        text to OpenAI, so by default it only prints the plan, cost estimate and a hash; --send <hash> does it. Resumable.
  writing.py index [--paper P] [--force]            refresh one paper's index now (it also refreshes itself on every query/pass)
  writing.py query "<request>" [--paper P] [--part P] [--kind K] [--budget N] [--no-text] [--json]
        Find the relevant chunks of ONE paper (empty request = outline). --paper: registered name or path; default = the
        current directory's paper, else the paper last worked on.
  writing.py revise "<instruction>" [--paper P] [--part X | --chunk ID ...] [--book ID] [--no-book] [--dry-run] [--send HASH] [--json]
        ChatGPT correction pass for ONE passage -> a validated proposal (never edits files). While the paper's dry-run setting is on
        (default) this only prints the exact payload and a hash; send it with --send <hash> after you have read it.
  writing.py proposals [--paper P] | proposal <id> [--paper P]
  writing.py settings [--paper P] [--dry-run on|off]
  writing.py stats [--paper P] [--days 30]       the Monitor page's numbers in a terminal
  writing.py doctor

--id may be omitted when exactly one book is imported. Data lives in ~/.config/overleaf-skill/ (never in git).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from writing_core import book_eval, book_index, chunk_book, config, openai_client, paper_index, retrieve, revise, rule_cards, stats  # noqa: E402

COMMANDS = {}


def command(name):
    def deco(fn):
        COMMANDS[name] = fn
        return fn
    return deco


def pick_book(args):
    if getattr(args, "id", None):
        return args.id
    books = config.list_books()
    if len(books) == 1:
        return books[0]
    raise SystemExit("which book? use --id (imported: %s)" % (", ".join(books) or "none yet"))


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


@command("book-ingest")
def cmd_book_ingest(args):
    r = book_index.ingest(args.pdf, args.id, force=args.force, rebuild=args.rebuild, outline_only=args.outline_only, source="cli")
    if r["status"] == "skipped":
        print(f"already ingested ({args.id}): 0 writes.")
        if r.get("note"):
            print("note:", r["note"])
    elif r["status"] == "outline":
        o = r["outline"]
        print(f"outline written ({o['method']}, {len(o['entries'])} entries) -> {book_index.paths(args.id)['outline']}")
        for w in o.get("warnings", []):
            print("warning:", w)
        print("Review/edit it, then run again with --rebuild (or without flags) to build the chunks.")
    else:
        m = r["manifest"]
        print(f"{r['status']} {args.id}: {m['n_pdf_pages']} pages, {m['n_chapters']} chapters, {m['n_chunks']} chunks "
              f"(~{m['tokens']} tokens), outline by {m['method']}")
        for w in r.get("warnings", []):
            print("warning:", w)


@command("book-list")
def cmd_book_list(args):
    for b in config.list_books():
        m = book_index.manifest(b)
        print(f"{b:20} {m['n_chunks']:5} chunks  {m['n_pdf_pages']:4} pages  {m['source_name']}")
    if not config.list_books():
        print("no books imported yet")


@command("book-info")
def cmd_book_info(args):
    emit(book_index.info(pick_book(args)))


@command("book-query")
def cmd_book_query(args):
    res = retrieve.book_query(pick_book(args), args.query, part=args.part, budget=args.budget, max_chunks=args.max)
    if args.json:
        emit(res)
    else:
        print(retrieve.format_book_result(res, with_text=not args.no_text))


@command("book-chunk")
def cmd_book_chunk(args):
    c = book_index.get_chunk(pick_book(args), args.chunk_id)
    print(f"{c['id']}  {c['label']}\nparts: {', '.join(c['parts'])}  prev: {c['prev']}  next: {c['next']}\n\n{c['text']}")


@command("book-rules")
def cmd_book_rules(args):
    book = pick_book(args)
    st = rule_cards.status(book)
    if not st["todo"]:
        print(f"every chunk of {book} already has a rule card ({st['with_cards']}/{st['chunks']}).")
        return
    pl = rule_cards.plan(book, batch=args.batch)
    if not args.send:
        print(f"DRY RUN: nothing was sent.\n{pl['chunks']} chunks in {pl['calls']} requests to {pl['model']} at {pl['endpoint']}; about {pl['est_tokens_in']} tokens in, "
              f"~{pl['est_tokens_out']} out" + (f", about {pl['est_cost']} in your price units" if pl["est_cost"] is not None else "") +
              f".\nThis sends the book's text to OpenAI once. To go ahead: writing.py book-rules --id {book} --send {pl['payload_hash']}")
        return
    def tick(i, n):
        print(f"  {i}/{n} requests done", flush=True)
    r = rule_cards.run(book, args.send, batch=args.batch, progress=tick, source="cli")
    print(f"{r['status']}: {r['with_cards']}/{r['chunks']} chunks now have rule cards" + (f" (stopped: {r['error']})" if r.get("error") else ""))


@command("eval")
def cmd_eval(args):
    book = pick_book(args)
    if args.init:
        p = book_index.paths(book)["dir"] / "eval.auto.json"
        p.write_text(json.dumps(book_eval.from_headings(book), indent=2))
        print("wrote", p, "(a self-check from the book's own headings; hand-label eval.json for a real measure)")
        return
    items = book_eval.load(book, args.file)
    out = book_eval.run(book, items, k=args.k)
    print(f"hit@{out['k']}: {out['hits']}/{out['n']} = {out['hit_at_k']:.0%}")
    for r in out["results"]:
        if not r["hit"]:
            print("  MISS:", r["query"], "->", "; ".join(r["top"]) or "(nothing)")


def resolve_paper(args):
    try:
        return common.resolve(getattr(args, "paper", None))
    except (KeyError, RuntimeError) as e:
        raise SystemExit(f"error: {e}")


@command("index")
def cmd_index(args):
    name, proj = resolve_paper(args)
    r = paper_index.refresh(name, proj, trigger="manual", force=args.force, source="cli")
    print(f"{name}: {r['added']} added, {r['changed']} changed, {r['removed']} removed, {r['unchanged']} unchanged "
          f"({r['total']} chunks, ~{r['tex_tokens']} manuscript tokens){' [no file changed]' if r['skipped_scan'] else ''}")
    for w in r.get("warnings", []):
        print("warning:", w)


@command("query")
def cmd_query(args):
    name, proj = resolve_paper(args)
    res = paper_index.query(name, proj, args.query, part=args.part, kind=args.kind, budget=args.budget, source="cli")
    if args.json:
        emit(res)
    else:
        print(paper_index.format_paper_result(res, with_text=not args.no_text))


@command("revise")
def cmd_revise(args):
    name, proj = resolve_paper(args)
    dry = True if args.dry_run else (False if args.send else None)
    res = revise.run(name, proj, args.instruction, part=args.part, chunk_ids=args.chunk or None, book_id=args.book,
                     dry_run=dry, confirm=args.send, source="cli", use_book=not args.no_book)
    if args.json:
        emit(res)
    else:
        print(revise.format_preview(res) if res["status"] == "preview" else revise.format_proposal(res["proposal"]))


@command("proposals")
def cmd_proposals(args):
    name, proj = resolve_paper(args)
    rows = revise.list_proposals(proj)
    for r in rows:
        print(f"{r['id']}  {r['status']:10} {r['hunks']:2} hunks  {', '.join(r['part'] or [])}: {r['instruction'][:60]}")
    if not rows:
        print("no proposals yet")


@command("proposal")
def cmd_proposal(args):
    name, proj = resolve_paper(args)
    print(revise.format_proposal(revise.get_proposal(proj, args.proposal_id)))


@command("settings")
def cmd_settings(args):
    name, proj = resolve_paper(args)
    if args.dry_run:
        config.set_paper_setting(proj, "dry_run", args.dry_run == "on")
    print(json.dumps({"paper": name, **config.paper_settings(proj)}, indent=2))


@command("stats")
def cmd_stats(args):
    if args.paper:
        resolve_paper(args)
    print(stats.format_text(stats.monitor(paper=args.paper, days=args.days)))


@command("doctor")
def cmd_doctor(args):
    common.main(["doctor"])


def build_parser():
    ap = argparse.ArgumentParser(prog="writing.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, *flags, **kw):
        p = sub.add_parser(name)
        for f in flags:
            p.add_argument(*f[0], **f[1])
        return p

    ID = (["--id"], {"help": "book id"})
    p = add("book-ingest", (["pdf"], {}), (["--id"], {"required": True}))
    p.add_argument("--outline-only", action="store_true")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--force", action="store_true")
    p = add("book-rules", ID)
    p.add_argument("--batch", type=int, default=4)
    p.add_argument("--send", metavar="HASH")
    add("book-list")
    add("book-info", ID)
    p = add("book-query", (["query"], {}), ID)
    p.add_argument("--part")
    p.add_argument("--budget", type=int, default=2000)
    p.add_argument("--max", type=int, default=6)
    p.add_argument("--no-text", action="store_true")
    p.add_argument("--json", action="store_true")
    add("book-chunk", (["chunk_id"], {}), ID)
    p = add("eval", ID)
    p.add_argument("--file")
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--init", action="store_true")
    p = add("index")
    p.add_argument("--paper")
    p.add_argument("--force", action="store_true")
    p = add("query", (["query"], {"nargs": "?", "default": ""}))
    p.add_argument("--paper")
    p.add_argument("--part")
    p.add_argument("--kind")
    p.add_argument("--budget", type=int, default=2500)
    p.add_argument("--no-text", action="store_true")
    p.add_argument("--json", action="store_true")
    p = add("revise", (["instruction"], {}))
    p.add_argument("--paper")
    p.add_argument("--part")
    p.add_argument("--chunk", action="append")
    p.add_argument("--book")
    p.add_argument("--no-book", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--send", metavar="HASH")
    p.add_argument("--json", action="store_true")
    p = add("proposals")
    p.add_argument("--paper")
    p = add("proposal", (["proposal_id"], {}))
    p.add_argument("--paper")
    p = add("settings")
    p.add_argument("--paper")
    p.add_argument("--dry-run", choices=["on", "off"])
    p = add("stats")
    p.add_argument("--paper")
    p.add_argument("--days", type=int, default=30)
    add("doctor")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.cmd](args)
    except openai_client.OpenAIError as e:
        print(f"OpenAI error ({e.kind}): {e}", file=sys.stderr)
        return 1
    except (book_index.BookError, config.ConfigError, chunk_book.ToolMissing, paper_index.IsolationError,
            revise.ReviseError, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
