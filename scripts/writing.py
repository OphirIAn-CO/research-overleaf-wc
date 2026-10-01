#!/usr/bin/env python3
"""Writing coach CLI: book import, retrieval, evaluation. Stdlib only (book import also needs poppler's pdftotext).

  writing.py book-ingest <pdf> --id ID [--outline-only] [--rebuild] [--force]
        Import a book ONCE. Same PDF again = "skipped, 0 writes". --outline-only stops after writing outline.json so you can
        review/edit it; --rebuild re-chunks using your edited outline.json; --force starts over (re-analyzes the outline too).
  writing.py book-list | book-info [--id ID]
  writing.py book-query "<request>" [--id ID] [--part P] [--budget N] [--max N] [--no-text] [--json]
  writing.py book-chunk <chunk-id> [--id ID]
  writing.py eval [--id ID] [--file F] [-k 3] [--init]
  writing.py doctor

--id may be omitted when exactly one book is imported. Data lives in ~/.config/overleaf-skill/ (never in git).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from writing_core import book_eval, book_index, chunk_book, config, retrieve  # noqa: E402

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
    add("doctor")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.cmd](args)
    except (book_index.BookError, config.ConfigError, chunk_book.ToolMissing, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
