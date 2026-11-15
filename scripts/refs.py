#!/usr/bin/env python3
"""Local references: search, add by DOI, check. No Overleaf, no Mendeley, no git push; only the paper's own .bib changes.

  refs.py search "topic words" [-n 8] [--no-web] [--json]   OpenAlex + Crossref (+ web): title, authors, year, DOI, abstract
  refs.py add DOI [DOI ...] [--paper P]                    doi.org BibTeX -> the paper's .bib (deduped by DOI, key Author2022Word)
  refs.py check [--paper P]                                cite keys used in the .tex files but missing from the .bib, and unused entries

--paper: registered name or folder; default = the folder Claude was started in (see ../research), else the paper last worked on.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import doi_bot  # noqa: E402

CITE_RE = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}")
KEY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,")


def paper(ref):
    if not ref and any(Path.cwd().glob("*.tex")):  # started inside a paper folder: that folder, registered on the spot
        ref = "."
    try:
        name, proj = common.resolve(ref)
    except (KeyError, RuntimeError) as e:
        raise SystemExit(f"{e}\nRun inside the paper's folder, or pass --paper NAME|PATH.")
    common.set_active(name, "refs")
    return proj


def used_keys(root):
    keys = {}
    for tex in Path(root).rglob("*.tex"):
        if ".overleaf-skill" in tex.parts:
            continue
        for n, line in enumerate(tex.read_text(errors="replace").splitlines(), 1):
            if line.lstrip().startswith("%"):
                continue
            for m in CITE_RE.finditer(line):
                for k in m.group(1).split(","):
                    if k.strip():
                        keys.setdefault(k.strip(), f"{tex.relative_to(root)}:{n}")
    return keys


def bib_keys(text):
    return {k for kind, k in KEY_RE.findall(text) if kind.lower() not in ("string", "comment", "preamble")}


def check(proj):
    """-> {missing: {key: first file:line}, unused: [keys]}"""
    used = used_keys(proj["path"])
    have = bib_keys(Path(common.bib_path(proj)).read_text(errors="replace"))
    return {"missing": {k: v for k, v in used.items() if k not in have}, "unused": sorted(have - set(used))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=8)
    s.add_argument("--no-web", action="store_true", help="only the OpenAlex and Crossref indexes")
    s.add_argument("--json", action="store_true")
    a = sub.add_parser("add")
    a.add_argument("dois", nargs="+")
    a.add_argument("--paper")
    c = sub.add_parser("check")
    c.add_argument("--paper")
    args = ap.parse_args(argv)

    if args.cmd == "search":
        from writing_core import scholar
        try:
            res = scholar.search([args.query], limit=args.n, use_web=not args.no_web)
        except scholar.ScholarError as e:
            raise SystemExit(str(e))
        if args.json:
            print(json.dumps(res["candidates"], ensure_ascii=False, indent=1))
            return
        for i, x in enumerate(res["candidates"], 1):
            au = "; ".join(x["authors"][:3]) + (" et al." if len(x["authors"]) > 3 else "")
            print(f"[{i}] {x['title']} ({x.get('year') or '?'})\n    {au} | {x.get('venue') or ''} | doi:{x['doi']} | cited {x.get('cited_by', 0)}")
            if x.get("abstract"):
                print("    " + " ".join(x["abstract"].split())[:300])
        if not res["candidates"]:
            print("nothing found; try other words")
    elif args.cmd == "add":
        bib = common.bib_path(paper(args.paper))
        for d in args.dois:
            r = doi_bot.add_reference(d, bib, source="refs", mirror=None, push=False)
            print(f"{r['status']:9} {r.get('cite') or ''}  {r.get('title') or r['message']}")
    else:
        r = check(paper(args.paper))
        for k, where in r["missing"].items():
            print(f"MISSING  {k}  (cited at {where})")
        print(f"{len(r['missing'])} cited keys missing from the .bib; {len(r['unused'])} entries never cited" + (": " + ", ".join(r["unused"][:30]) if r["unused"] else ""))


if __name__ == "__main__":
    main()
