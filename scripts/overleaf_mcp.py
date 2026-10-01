#!/usr/bin/env python3
"""Minimal stdio MCP server for locally-cloned Overleaf project(s).
No third-party dependencies. Each tool call takes an optional repo_path
argument: a registered project name or a path to a local git-bridge clone
(see common.py); if omitted it uses the cwd (if an Overleaf clone), else the
registry default. OVERLEAF_REPO is a legacy last-resort fallback."""
import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import common

DEFAULT_REPO = os.environ.get("OVERLEAF_REPO")  # legacy fallback only
MENDELEY_SYNC = os.path.join(SCRIPTS_DIR, "mendeley_sync.py")


def resolve_paper(args):
    """-> (name, proj, error). repo_path may be a registered project name, an absolute path (auto-registered), or omitted
    (cwd if it is an Overleaf clone, else the paper last worked on, else the registry default)."""
    ref = args.get("repo_path") or args.get("project")
    try:
        name, proj = common.resolve(ref)
    except KeyError as e:
        if ref or not DEFAULT_REPO:
            return None, None, str(e)
        name, proj = common.resolve(DEFAULT_REPO)
    except RuntimeError as e:
        return None, None, f"refusing to act: {e}"
    common.set_active(name, args.get("_source", "claude"))  # the Telegram bot follows the paper being worked on
    if not os.path.isdir(os.path.join(proj["path"], ".git")):
        return None, None, f"not a git repo: {proj['path']}"
    return name, proj, None


def resolve_repo(args):
    name, proj, err = resolve_paper(args)
    if err:
        return None, err
    args.setdefault("bib_filename", proj["bib"])
    return proj["path"], None


def tool_projects(args):
    act = args.get("action", "list")
    if act == "add":
        if not args.get("path"):
            return "add needs path"
        name = common.register(args["path"], args.get("name"), args.get("group"))
        return f"registered {name}"
    if act == "default":
        common.main(["default", args.get("name", "")])
    reg = common.registry()
    return "\n".join(f"{'*' if n == reg['default'] else ' '} {n}  group={(p.get('mendeley_group') or {}).get('name', '-')}  {p['path']}"
                     for n, p in reg["projects"].items()) or "no projects registered"


def run_git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def tool_pull(args):
    repo, err = resolve_repo(args)
    if err:
        return err
    code, out, err = run_git(repo, "pull", "--ff-only")
    if code != 0:
        return f"pull failed: {err or out}"
    return out or "already up to date"


def tool_status(args):
    repo, err = resolve_repo(args)
    if err:
        return err
    code, out, err = run_git(repo, "status", "--short")
    return out or "clean, nothing pending"


JUNK = (".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk", ".bbl", ".bcf", ".blg", ".run.xml",
        ".synctex.gz", ".lof", ".lot", ".nav", ".snm", ".vrb", ".pdf")


def tool_sync(args):
    repo, err = resolve_repo(args)
    if err:
        return err
    message = args.get("message") or "Update via MCP"
    push = args.get("push", True)
    code, out, err = run_git(repo, "status", "--short")
    if not out:
        return "nothing to sync"
    # never push LaTeX build output that is not already tracked (Overleaf compiles its own)
    _, new, _ = run_git(repo, "ls-files", "--others", "--exclude-standard")  # before staging
    junk = [f for f in new.splitlines() if f.endswith(JUNK)]
    run_git(repo, "add", "-A")
    for f in junk:
        run_git(repo, "reset", "-q", "--", f)
    skipped = f"\n(skipped {len(junk)} untracked build files)" if junk else ""
    if run_git(repo, "diff", "--cached", "--quiet")[0] == 0:
        return "nothing to sync" + skipped
    code, out, err = run_git(repo, "commit", "-m", message)
    if code != 0:
        return f"commit failed: {out or err}"
    result = f"committed: {out}{skipped}"
    if push:
        code, out2, err2 = run_git(repo, "push")
        result += f"\npush: {'ok' if code == 0 else (err2 or out2)}"
    return result


def tool_mendeley_fetch(args):
    repo, err = resolve_repo(args)
    if err:
        return err
    bib_filename = args["bib_filename"]
    bib_path = os.path.join(repo, bib_filename)
    if not os.path.isfile(bib_path):
        return f"bib file not found: {bib_path}"

    cmd = [sys.executable, MENDELEY_SYNC, "fetch", "--bib", bib_path]
    if args.get("group"):
        cmd += ["--group", args["group"]]
    if args.get("personal_library"):
        cmd += ["--personal-library"]

    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return f"mendeley fetch failed: {out}\n{(r.stderr or '').strip()}"
    return out or "no output"


def tool_mendeley_list_groups(args):
    r = subprocess.run(
        [sys.executable, MENDELEY_SYNC, "list-groups"], capture_output=True, text=True
    )
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return f"list-groups failed: {out}\n{(r.stderr or '').strip()}"
    return out or "no groups found"


def tool_mendeley_catalog_search(args):
    doi = args.get("doi")
    title = args.get("title")
    if doi:
        cmd = [sys.executable, MENDELEY_SYNC, "catalog-lookup", "--doi", doi]
    elif title:
        cmd = [sys.executable, MENDELEY_SYNC, "catalog-search", "--title", title]
        if args.get("limit"):
            cmd += ["--limit", str(args["limit"])]
    else:
        return "provide either doi or title"
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return f"catalog search failed: {out}\n{(r.stderr or '').strip()}"
    return out or "no matches"


def tool_mendeley_add_reference(args):
    repo, err = resolve_repo(args)
    if err:
        return err
    bib_filename = args["bib_filename"]
    bib_path = os.path.join(repo, bib_filename)
    if not os.path.isfile(bib_path):
        return f"bib file not found: {bib_path}"

    cmd = [sys.executable, MENDELEY_SYNC, "add", "--bib", bib_path]

    if args.get("catalog_id"):
        cmd += ["--catalog-id", args["catalog_id"]]
    elif args.get("title"):
        cmd += ["--title", args["title"]]
        for key, flag in (
            ("authors", "--authors"), ("year", "--year"), ("doi", "--doi"),
            ("url", "--url"), ("source", "--source"), ("type", "--type"),
        ):
            if args.get(key):
                cmd += [flag, str(args[key])]
    else:
        return "provide either catalog_id (from mendeley_catalog_search) or title (+ authors/year/doi/url as available)"

    if args.get("group"):
        cmd += ["--group", args["group"]]

    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return f"add reference failed: {out}\n{(r.stderr or '').strip()}"
    return out or "no output"


def _writing_call(fn):
    """Run a writing-coach tool; known failures become a plain message instead of a stack trace. Imports are lazy so a
    problem in the writing package can never break the sync tools above."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        from writing_core import book_index, config, openai_client, paper_index, revise
        known = (book_index.BookError, config.ConfigError, paper_index.IsolationError, revise.ReviseError, ValueError)
        if isinstance(e, known):
            return f"error: {e}"
        if isinstance(e, openai_client.OpenAIError):
            return f"OpenAI error ({e.kind}): {e}"
        raise


def _book_for(args):
    from writing_core import config
    if args.get("book_id"):
        return args["book_id"]
    books = config.list_books()
    if len(books) == 1:
        return books[0]
    raise ValueError("which book? pass book_id (imported: %s)" % (", ".join(books) or "none; run writing.py book-ingest"))


def tool_book_query(args):
    def go():
        from writing_core import config, retrieve
        if not args.get("query"):
            return "provide query (what you want the book's advice on)"
        name, proj, _ = resolve_paper(dict(args))  # a paper in context lets the event be audited; not required
        res = retrieve.book_query(_book_for(args), args["query"], part=args.get("part"), budget=int(args.get("budget") or 2000),
                                  paper=name, paper_scope=config.paper_dir(proj) if proj else None, source="claude")
        return retrieve.format_book_result(res)
    return _writing_call(go)


def tool_paper_query(args):
    def go():
        from writing_core import paper_index
        name, proj, err = resolve_paper(args)
        if err:
            return err
        res = paper_index.query(name, proj, args.get("query") or "", part=args.get("part"), kind=args.get("kind"),
                                budget=int(args.get("budget") or 2500), source="claude")
        return paper_index.format_paper_result(res)
    return _writing_call(go)


def tool_chatgpt_revise(args):
    def go():
        from writing_core import revise
        name, proj, err = resolve_paper(args)
        if err:
            return err
        if args.get("proposal_id"):  # an existing proposal, e.g. one the author handed over from the web app
            return revise.format_proposal(revise.get_proposal(proj, args["proposal_id"]))
        if not args.get("instruction"):  # nothing to do yet: what is waiting for Claude?
            return revise.format_handoffs(revise.waiting_handoffs(proj))
        res = revise.run(name, proj, args.get("instruction", ""), part=args.get("part"), chunk_ids=args.get("chunk_ids"),
                         book_id=args.get("book_id"), dry_run=args.get("dry_run"), confirm=args.get("confirm"), source="claude")
        return revise.format_preview(res) if res["status"] == "preview" else revise.format_proposal(res["proposal"])
    return _writing_call(go)


def tool_revise_record(args):
    def go():
        from writing_core import revise
        name, proj, err = resolve_paper(args)
        if err:
            return err
        r = revise.record(name, proj, args.get("proposal_id", ""), accepted=args.get("accepted"), rejected=args.get("rejected"),
                          applied=bool(args.get("applied")), synced=bool(args.get("synced")), source="claude", note=args.get("note", ""))
        return f"recorded for proposal {r['proposal']}: {r['accepted']} accepted, {r['rejected']} rejected, {r['pending']} pending (status {r['status']})"
    return _writing_call(go)


REPO_PATH_PROP = {
    "repo_path": {
        "type": "string",
        "description": "Registered project name OR absolute path of the local Overleaf clone (new paths are auto-registered). Optional: defaults to the current directory if it is an Overleaf clone, else the registry default project.",
    }
}

TOOLS = {
    "overleaf_projects": {
        "description": "List/add/default the registry of Overleaf papers (name, folder, bib file, Mendeley group) shared by every tool and the Telegram DOI bot. Args: action (list|add|default, default list); add takes path, name (optional), group (Mendeley group name, optional); default takes name.",
        "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}, "path": {"type": "string"}, "name": {"type": "string"}, "group": {"type": "string"}}},
        "fn": tool_projects,
    },
    "book_query": {
        "description": (
            "Writing coach: fetch the few book chunks relevant to a request (never the whole book), with page references to cite. "
            "Args: query (required, e.g. 'how to describe sampling in methods'), part (optional: introduction, methods, results, "
            "discussion, abstract, style, ... or a sub-part like methods.analysis; otherwise routed from the query), book_id (optional "
            "if one book is imported), budget (tokens, default 2000), repo_path (optional paper)."),
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "part": {"type": "string"}, "book_id": {"type": "string"},
                                                          "budget": {"type": "integer"}, **{k: v for k, v in REPO_PATH_PROP.items()}}},
        "fn": tool_book_query,
    },
    "paper_query": {
        "description": (
            "Writing coach: find the relevant chunks of ONE paper (never reads the whole manuscript). Empty query = outline. "
            "'my methods' returns that whole part in document order with file and line ranges (use them with Read/Edit); other "
            "requests do a full-text search. Args: query, part, kind (tex|memory|review|bib), budget, repo_path. The paper's index "
            "refreshes itself on every call; only changed chunks are rewritten."),
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "part": {"type": "string"}, "kind": {"type": "string"},
                                                          "budget": {"type": "integer"}, **REPO_PATH_PROP}},
        "fn": tool_paper_query,
    },
    "chatgpt_revise": {
        "description": (
            "Writing coach, OPT-IN ONLY (run it only when the user asks to improve/polish/rewrite/check a part against the book). "
            "Sends ONE passage plus the matching book rules to ChatGPT and returns a validated PROPOSAL of reviewable hunks; it "
            "never edits files. While the paper's dry-run setting is on (default) it returns the exact payload and a payload_hash "
            "instead: show it to the author, and only after they agree call again with dry_run=false and confirm=<payload_hash>. "
            "Args: instruction (required), part (e.g. methods, abstract) or chunk_ids, book_id, dry_run, confirm, repo_path. With NO instruction it "
            "lists proposals the author handed to you from the web app; with proposal_id it shows that proposal (apply exactly the changes the "
            "author chose)."),
        "inputSchema": {"type": "object", "properties": {"instruction": {"type": "string"}, "proposal_id": {"type": "string"}, "part": {"type": "string"},
                                                          "chunk_ids": {"type": "array", "items": {"type": "string"}}, "book_id": {"type": "string"},
                                                          "dry_run": {"type": "boolean"}, "confirm": {"type": "string"}, **REPO_PATH_PROP}},
        "fn": tool_chatgpt_revise,
    },
    "revise_record": {
        "description": (
            "Writing coach: log which hunks of a proposal the author accepted/rejected after you applied them with Edit (so the "
            "Monitor and the paper's decision log stay true). Args: proposal_id, accepted (list of hunk ids like u1.h2, or 'all'), "
            "rejected (list or 'all'), applied (bool: the accepted text is now in the file), synced (bool), note, repo_path."),
        "inputSchema": {"type": "object", "properties": {"proposal_id": {"type": "string"}, "accepted": {}, "rejected": {},
                                                          "applied": {"type": "boolean"}, "synced": {"type": "boolean"}, "note": {"type": "string"},
                                                          **REPO_PATH_PROP}},
        "fn": tool_revise_record,
    },
    "overleaf_pull": {
        "description": "Pull latest changes from Overleaf (git pull --ff-only) for the given repo_path. Run before editing to avoid conflicts with edits made in the browser.",
        "inputSchema": {"type": "object", "properties": {**REPO_PATH_PROP}},
        "fn": tool_pull,
    },
    "overleaf_status": {
        "description": "Show which tracked files have uncommitted local changes (git status --short) for the given repo_path. Use to check what will be synced before calling overleaf_sync.",
        "inputSchema": {"type": "object", "properties": {**REPO_PATH_PROP}},
        "fn": tool_status,
    },
    "overleaf_sync": {
        "description": "Commit and push all local changes to Overleaf (git add -A, commit, push) for the given repo_path. Args: message (optional commit message), push (optional bool, default true). Edit files directly on disk with normal file tools first, then call this to sync.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **REPO_PATH_PROP,
                "message": {"type": "string"},
                "push": {"type": "boolean"},
            },
        },
        "fn": tool_sync,
    },
    "mendeley_fetch": {
        "description": (
            "Fetch new references from a Mendeley library into <repo_path>/<bib_filename> "
            "(default 'references.bib'), deduped by cite key/DOI, appending only new BibTeX "
            "entries. Args: repo_path (optional, see above), bib_filename (optional), "
            "group (optional Mendeley group name or ID to scope to — once given for this "
            "repo's bib file it's remembered for future calls, so the group is effectively "
            "per-repository), personal_library (optional bool to explicitly use the personal "
            "library instead of a group). Requires prior one-time 'authorize' CLI setup — see "
            "SKILL.md."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **REPO_PATH_PROP,
                "bib_filename": {"type": "string"},
                "group": {"type": "string"},
                "personal_library": {"type": "boolean"},
            },
        },
        "fn": tool_mendeley_fetch,
    },
    "mendeley_list_groups": {
        "description": "List the Mendeley groups (shared libraries) the authorized account belongs to, as 'id  name' pairs. Use to find the right value for mendeley_fetch's group argument.",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": tool_mendeley_list_groups,
    },
    "mendeley_catalog_search": {
        "description": (
            "Look up a paper in Mendeley's own catalog (canonical bibliographic database), "
            "either by exact 'doi' or fuzzy 'title' text search. Use this AFTER finding a "
            "candidate paper via general web search, to check whether Mendeley already has "
            "clean canonical metadata for it (returns catalog_id to pass to "
            "mendeley_add_reference for higher-quality metadata than manual entry). Args: "
            "doi (optional, exact match), title (optional, fuzzy match, used if doi not "
            "given), limit (optional, max results for title search, default 5)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
                "title": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
        "fn": tool_mendeley_catalog_search,
    },
    "mendeley_add_reference": {
        "description": (
            "Add a new reference to Mendeley (into a group or the personal library) and "
            "immediately fetch it into <repo_path>/<bib_filename> as a BibTeX entry, ready "
            "to \\cite{}. ALWAYS show the candidate title/authors/URL to the user for "
            "confirmation before calling this — it writes to the user's live Mendeley "
            "library. Two ways to specify the reference: (1) catalog_id, from a prior "
            "mendeley_catalog_search match — preferred, gives clean canonical metadata; "
            "(2) manual fields — title (required), authors ('First Last; First Last'), "
            "year, doi, url, source (journal/venue name), type (default 'journal') — use "
            "when no catalog match was found, sourced from general web search results. "
            "Also takes repo_path/bib_filename (as in mendeley_fetch) and group (Mendeley "
            "group name or ID; falls back to whatever is already remembered for this repo's "
            "bib file, same as mendeley_fetch, so it stays scoped per-repository)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **REPO_PATH_PROP,
                "bib_filename": {"type": "string"},
                "group": {"type": "string"},
                "catalog_id": {"type": "string"},
                "title": {"type": "string"},
                "authors": {"type": "string"},
                "year": {"type": "string"},
                "doi": {"type": "string"},
                "url": {"type": "string"},
                "source": {"type": "string"},
                "type": {"type": "string"},
            },
        },
        "fn": tool_mendeley_add_reference,
    },
}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    try:  # a session opened inside an Overleaf clone makes that paper the bot's target right away
        name, _ = common.resolve(None)
        if common.git_root(os.getcwd()) and common.is_overleaf(common.git_root(os.getcwd())):
            common.set_active(name, "claude")
    except (KeyError, RuntimeError):
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {}) or {}

        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "overleaf-mcp", "version": "0.2.0"},
                },
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {"name": name, "description": t["description"], "inputSchema": t["inputSchema"]}
                        for name, t in TOOLS.items()
                    ]
                },
            })
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            tool = TOOLS.get(name)
            if not tool:
                send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown tool {name}"}})
                continue
            try:
                result = tool["fn"](args)
                send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": str(result)}], "isError": False},
                })
            except Exception as e:
                send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True},
                })
        elif req_id is not None:
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
