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


def resolve_repo(args):
    """repo_path may be a registered project name, an absolute path (auto-registered),
    or omitted (cwd if it is an Overleaf clone, else the registry default)."""
    ref = args.get("repo_path") or args.get("project")
    try:
        _, proj = common.resolve(ref)
    except KeyError as e:
        if ref or not DEFAULT_REPO:
            return None, str(e)
        _, proj = common.resolve(DEFAULT_REPO)
    repo = proj["path"]
    if not os.path.isdir(os.path.join(repo, ".git")):
        return None, f"not a git repo: {repo}"
    args.setdefault("bib_filename", proj["bib"])
    return repo, None


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


def tool_sync(args):
    repo, err = resolve_repo(args)
    if err:
        return err
    message = args.get("message") or "Update via MCP"
    push = args.get("push", True)
    code, out, err = run_git(repo, "status", "--short")
    if not out:
        return "nothing to sync"
    run_git(repo, "add", "-A")
    code, out, err = run_git(repo, "commit", "-m", message)
    if code != 0:
        return f"commit failed: {out or err}"
    result = f"committed: {out}"
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
                    "serverInfo": {"name": "overleaf-mcp", "version": "0.1.0"},
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
