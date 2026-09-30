#!/usr/bin/env python3
"""Pull new references from a Mendeley library via the Mendeley API and
append them as BibTeX entries to a references.bib file.

No third-party dependencies (urllib only). Credentials and tokens are kept
in ~/.config/overleaf-skill/ (mendeley_credentials.json, mendeley_tokens.json)
— never inside the synced Overleaf repo or this skill's git repo.

Commands:
  authorize             One-time OAuth2 authorization-code exchange.
  list-groups           List Mendeley groups (shared libraries) you belong to.
  fetch --bib PATH      Fetch library documents as BibTeX, append new
                         entries (deduped by cite key / DOI / title) to PATH.
                         Optional --group NAME_OR_ID scopes to a Mendeley
                         group; once given for a PATH it's remembered (in
                         the projects.json registry) so later fetches for
                         the same references.bib reuse it automatically.
                         The Mendeley group linked in Overleaf's own project
                         settings is NOT readable from the local git-bridge
                         clone (Overleaf doesn't expose it there), so this
                         is the closest equivalent: ask once, remember after.
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

CREDS_PATH = str(common.HOME / "mendeley_credentials.json")
TOKENS_PATH = str(common.HOME / "mendeley_tokens.json")

REDIRECT_URI = "https://localhost/callback"
AUTH_URL = "https://api.mendeley.com/oauth/authorize"
TOKEN_URL = "https://api.mendeley.com/oauth/token"
API_BASE = "https://api.mendeley.com"


def load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)


def load_creds():
    creds = load_json(CREDS_PATH)
    if not creds or "client_id" not in creds or "client_secret" not in creds:
        sys.exit(
            f"missing/incomplete credentials at {CREDS_PATH}\n"
            'expected: {"client_id": "...", "client_secret": "..."}'
        )
    return creds


def basic_auth_header(client_id, client_secret):
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def http_request(url, data=None, headers=None, method=None):
    headers = {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def cmd_authorize(args):
    creds = load_creds()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "all",
    })
    print("1. Open this URL in your browser and log in to Mendeley:\n")
    print(url)
    print(
        "\n2. After you approve, the browser will try to load "
        f"{REDIRECT_URI}?code=... and fail to connect — that's expected."
    )
    print("3. Copy the FULL url from the address bar (or just the code=... value) and paste it below.\n")
    if getattr(args, "code", None):
        pasted = args.code.strip()
    else:
        pasted = input("Paste redirect URL or code: ").strip()

    code = pasted
    if "code=" in pasted:
        parsed = urllib.parse.urlparse(pasted)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs:
            code = qs["code"][0]

    headers = {
        "Authorization": basic_auth_header(creds["client_id"], creds["client_secret"]),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    status, body, _ = http_request(TOKEN_URL, data=data, headers=headers, method="POST")
    if status != 200:
        sys.exit(f"token exchange failed ({status}): {body.decode(errors='replace')}")
    tokens = json.loads(body)
    save_json(TOKENS_PATH, tokens)
    print(f"\nSaved access + refresh token to {TOKENS_PATH}")


def refresh_access_token(creds, tokens):
    headers = {
        "Authorization": basic_auth_header(creds["client_id"], creds["client_secret"]),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }
    status, body, _ = http_request(TOKEN_URL, data=data, headers=headers, method="POST")
    if status != 200:
        sys.exit(f"token refresh failed ({status}): {body.decode(errors='replace')}\nrun 'authorize' again.")
    new_tokens = json.loads(body)
    save_json(TOKENS_PATH, new_tokens)
    return new_tokens


def get_access_token():
    creds = load_creds()
    tokens = load_json(TOKENS_PATH)
    if not tokens:
        sys.exit("no tokens found — run: python3 mendeley_sync.py authorize")
    return creds, tokens


def api_get(url, access_token, accept=None):
    headers = {"Authorization": f"Bearer {access_token}"}
    if accept:
        headers["Accept"] = accept
    return http_request(url, headers=headers, method="GET")


def api_post_json(url, access_token, body, content_type="application/vnd.mendeley-document.1+json"):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": content_type,
        "Accept": "application/vnd.mendeley-document.1+json",
    }
    return http_request(url, data=json.dumps(body).encode(), headers=headers, method="POST")


def list_groups(access_token):
    groups = []
    url = f"{API_BASE}/groups?" + urllib.parse.urlencode({"limit": "100"})
    while url:
        status, body, headers = api_get(url, access_token, accept="application/vnd.mendeley-group.1+json")
        if status == 401:
            sys.exit("access token expired/invalid (list groups)")
        if status != 200:
            sys.exit(f"list groups failed ({status}): {body.decode(errors='replace')}")
        groups.extend(json.loads(body))

        url = None
        link = headers.get("Link") or headers.get("link")
        if link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    break
    return groups


def cmd_list_groups(args):
    creds, tokens = get_access_token()
    groups = list_groups(tokens["access_token"])
    if not groups:
        print("no Mendeley groups found for this account.")
        return
    for g in groups:
        print(f"{g.get('id')}  {g.get('name')}")


def save_project_group(bib_path, group_id, group_name):
    common.register(os.path.dirname(os.path.abspath(bib_path)),
                    group={"id": group_id, "name": group_name})


def resolve_group(args, access_token):
    """Return (group_id, group_name) or (None, None) for the personal library.
    Resolution order: --group flag (name or id) > remembered mapping for this
    --bib path > None (personal library, no group filter)."""
    bib_key = os.path.abspath(args.bib)

    if args.group:
        groups = list_groups(access_token)
        match = None
        for g in groups:
            if str(g.get("id")) == args.group or g.get("name", "").lower() == args.group.lower():
                match = g
                break
        if not match:
            names = ", ".join(f"{g.get('id')} ({g.get('name')})" for g in groups) or "(none)"
            sys.exit(f"no Mendeley group matching {args.group!r}. Available: {names}")
        save_project_group(bib_key, match["id"], match.get("name"))
        return match["id"], match.get("name")

    _, proj = common.resolve(os.path.dirname(bib_key))
    remembered = proj.get("mendeley_group")
    if remembered and remembered.get("id"):
        print(f"using registered Mendeley group for this project: {remembered['name']} ({remembered['id']})")
        return remembered["id"], remembered["name"]
    if remembered:  # registered by name only (e.g. `common.py add --group NAME`): resolve the id now
        args.group = remembered["name"]
        return resolve_group(args, access_token)

    return None, None


def list_documents(access_token, folder_id=None, group_id=None):
    """Yield document dicts, following Link-header pagination."""
    params = {"view": "all", "limit": "100"}
    if group_id:
        params["group_id"] = group_id
    if folder_id:
        url = f"{API_BASE}/folders/{folder_id}/documents?" + urllib.parse.urlencode(params)
    else:
        url = f"{API_BASE}/documents?" + urllib.parse.urlencode(params)

    while url:
        status, body, headers = api_get(url, access_token, accept="application/vnd.mendeley-document.1+json")
        if status == 401:
            sys.exit("access token expired/invalid — run 'authorize' again or check refresh logic.")
        if status != 200:
            sys.exit(f"list documents failed ({status}): {body.decode(errors='replace')}")
        docs = json.loads(body)
        for d in docs:
            yield d

        url = None
        link = headers.get("Link") or headers.get("link")
        if link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    break


def fetch_bibtex_for(doc_id, access_token):
    url = f"{API_BASE}/documents/{doc_id}"
    status, body, _ = api_get(url, access_token, accept="application/x-bibtex")
    if status != 200:
        return None
    return body.decode(errors="replace").strip()


def extract_cite_key(bibtex_entry):
    m = re.match(r"@\w+\{([^,]+),", bibtex_entry)
    return m.group(1).strip() if m else None


def existing_keys_and_dois(bib_text):
    keys = set(re.findall(r"@\w+\{([^,]+),", bib_text))
    dois = set(d.lower() for d in re.findall(r"doi\s*=\s*[{\"]([^}\"]+)[}\"]", bib_text, re.IGNORECASE))
    return keys, dois


def do_fetch(bib_path, access_token, folder_id=None, group_id=None, only_doc_id=None):
    """Core fetch/dedupe/append logic. If only_doc_id is given, checks just
    that one document instead of listing the whole library/group (used right
    after `add` to pull in exactly the document just created)."""
    with open(bib_path) as f:
        bib_text = f.read()
    existing_keys, existing_dois = existing_keys_and_dois(bib_text)

    if only_doc_id:
        status, body, _ = api_get(f"{API_BASE}/documents/{only_doc_id}", access_token, accept="application/vnd.mendeley-document.1+json")
        docs = [json.loads(body)] if status == 200 else []
        checked_total = len(docs)
        doc_iter = docs
    else:
        doc_iter = list_documents(access_token, folder_id=folder_id, group_id=group_id)
        checked_total = None  # computed via counting below

    new_entries = []
    skipped = 0
    checked = 0
    for doc in doc_iter:
        checked += 1
        doi = (doc.get("identifiers") or {}).get("doi", "").lower()
        if doi and doi in existing_dois:
            skipped += 1
            continue

        bibtex = fetch_bibtex_for(doc["id"], access_token)
        if not bibtex:
            continue
        key = extract_cite_key(bibtex)
        if key and key in existing_keys:
            skipped += 1
            continue

        new_entries.append(bibtex)
        if key:
            existing_keys.add(key)
        if doi:
            existing_dois.add(doi)

    if not new_entries:
        return f"checked {checked} Mendeley documents, {skipped} already present, 0 new entries.", []

    with open(bib_path, "a") as f:
        f.write("\n")
        for entry in new_entries:
            f.write(entry + "\n\n")

    keys = [extract_cite_key(e) or "?" for e in new_entries]
    summary = f"checked {checked} Mendeley documents, {skipped} already present, {len(new_entries)} new entries appended to {bib_path}."
    summary += "".join(f"\n  + {k}" for k in keys)
    return summary, keys


def cmd_fetch(args):
    bib_path = args.bib
    if not os.path.isfile(bib_path):
        sys.exit(f"references.bib not found: {bib_path}")

    creds, tokens = get_access_token()
    access_token = tokens["access_token"]

    group_id, group_name = resolve_group(args, access_token)
    if group_id is None and args.folder is None and not args.personal_library:
        groups = list_groups(access_token)
        if groups:
            names = "\n".join(f"  {g.get('id')}  {g.get('name')}" for g in groups)
            sys.exit(
                "No Mendeley group specified and none remembered for this references.bib.\n"
                "Pass --group NAME_OR_ID (remembered for next time), or pass\n"
                "--personal-library to explicitly pull from your personal library instead.\n"
                f"Available groups:\n{names}"
            )
    else:
        print(f"pulling from: {'group ' + group_name if group_id else 'personal library'}")

    summary, _ = do_fetch(bib_path, access_token, folder_id=args.folder, group_id=group_id)
    print(summary)


def cmd_catalog_lookup(args):
    creds, tokens = get_access_token()
    access_token = tokens["access_token"]
    params = {"view": "all"}
    if args.doi:
        params["doi"] = args.doi
    elif args.isbn:
        params["isbn"] = args.isbn
    elif args.pmid:
        params["pmid"] = args.pmid
    elif args.arxiv:
        params["arxiv"] = args.arxiv
    else:
        sys.exit("catalog-lookup needs one of --doi/--isbn/--pmid/--arxiv")

    url = f"{API_BASE}/catalog?" + urllib.parse.urlencode(params)
    status, body, _ = api_get(url, access_token, accept="application/vnd.mendeley-document.1+json")
    if status != 200:
        sys.exit(f"catalog lookup failed ({status}): {body.decode(errors='replace')}")
    results = json.loads(body)
    print_catalog_results(results)


def cmd_catalog_search(args):
    creds, tokens = get_access_token()
    access_token = tokens["access_token"]
    params = {"query": args.title, "view": "all", "limit": str(args.limit)}
    url = f"{API_BASE}/search/catalog?" + urllib.parse.urlencode(params)
    status, body, _ = api_get(url, access_token, accept="application/vnd.mendeley-document.1+json")
    if status != 200:
        sys.exit(f"catalog search failed ({status}): {body.decode(errors='replace')}")
    results = json.loads(body)
    print_catalog_results(results)


def print_catalog_results(results):
    if not results:
        print("no catalog matches.")
        return
    for r in results:
        authors = ", ".join(
            f"{a.get('first_name', '')} {a.get('last_name', '')}".strip() for a in (r.get("authors") or [])
        )
        doi = (r.get("identifiers") or {}).get("doi", "")
        print(f"catalog_id={r.get('id')}  year={r.get('year')}  doi={doi}")
        print(f"  title: {r.get('title')}")
        if authors:
            print(f"  authors: {authors}")
        print()


def build_document_body(args, group_id=None):
    authors = []
    if args.authors:
        for name in args.authors.split(";"):
            name = name.strip()
            if not name:
                continue
            parts = name.rsplit(" ", 1)
            if len(parts) == 2:
                authors.append({"first_name": parts[0], "last_name": parts[1]})
            else:
                authors.append({"first_name": "", "last_name": name})

    body = {
        "title": args.title,
        "type": args.type or "journal",
    }
    if authors:
        body["authors"] = authors
    if args.year:
        body["year"] = int(args.year)
    if args.source:
        body["source"] = args.source
    identifiers = {}
    if args.doi:
        identifiers["doi"] = args.doi
    if identifiers:
        body["identifiers"] = identifiers
    if args.url:
        body["websites"] = [args.url]
    if group_id:
        body["group_id"] = group_id
    return body


def cmd_add(args):
    creds, tokens = get_access_token()
    access_token = tokens["access_token"]

    group_id, group_name = (None, None)
    if args.bib:
        group_id, group_name = resolve_group(args, access_token)

    if args.catalog_id:
        status, body, _ = api_get(
            f"{API_BASE}/catalog/{args.catalog_id}", access_token,
            accept="application/vnd.mendeley-document.1+json",
        )
        if status != 200:
            sys.exit(f"catalog lookup for {args.catalog_id} failed ({status}): {body.decode(errors='replace')}")
        catalog_doc = json.loads(body)
        doc_body = {
            k: v for k, v in catalog_doc.items()
            if k in ("title", "type", "authors", "year", "source", "identifiers", "abstract", "keywords")
        }
        doc_body.setdefault("type", "journal")
        if group_id:
            doc_body["group_id"] = group_id
        print(f"adding from catalog match: {catalog_doc.get('title')}")
    else:
        if not args.title:
            sys.exit("add needs either --catalog-id or --title (with --authors/--year/--doi/--url as available)")
        doc_body = build_document_body(args, group_id=group_id)
        print(f"adding manually-specified reference: {args.title}")

    status, body, _ = api_post_json(f"{API_BASE}/documents", access_token, doc_body)
    if status not in (200, 201):
        sys.exit(f"create document failed ({status}): {body.decode(errors='replace')}")
    new_doc = json.loads(body)
    doc_id = new_doc.get("id")
    print(f"added to Mendeley (document id {doc_id}, group={group_name or 'personal library'})")

    if args.bib:
        if not os.path.isfile(args.bib):
            sys.exit(f"references.bib not found: {args.bib}")
        summary, keys = do_fetch(args.bib, access_token, only_doc_id=doc_id)
        print(summary)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_auth = sub.add_parser("authorize", help="One-time OAuth2 login to get access/refresh tokens.")
    p_auth.add_argument("--code", default=None, help="Redirect URL or code=... value, to skip the interactive prompt (for non-interactive shells).")
    sub.add_parser("list-groups", help="List Mendeley groups (shared libraries) you belong to.")

    p_fetch = sub.add_parser("fetch", help="Fetch new references from Mendeley into a references.bib file.")
    p_fetch.add_argument("--bib", required=True, help="Path to the references.bib file to update.")
    p_fetch.add_argument("--folder", default=None, help="Optional Mendeley personal-library folder ID to scope the fetch to.")
    p_fetch.add_argument("--group", default=None, help="Mendeley group name or ID to scope the fetch to. Remembered per --bib path after first use.")
    p_fetch.add_argument("--personal-library", action="store_true", help="Explicitly pull from personal library (skip the group prompt) when no group is remembered for this --bib path.")

    p_clookup = sub.add_parser("catalog-lookup", help="Look up a paper in Mendeley's catalog by exact identifier.")
    p_clookup.add_argument("--doi", default=None)
    p_clookup.add_argument("--isbn", default=None)
    p_clookup.add_argument("--pmid", default=None)
    p_clookup.add_argument("--arxiv", default=None)

    p_csearch = sub.add_parser("catalog-search", help="Free-text search Mendeley's catalog by title.")
    p_csearch.add_argument("--title", required=True)
    p_csearch.add_argument("--limit", type=int, default=5)

    p_add = sub.add_parser("add", help="Add a reference to Mendeley (from a catalog match or manual metadata), optionally fetching it straight into a references.bib.")
    p_add.add_argument("--catalog-id", default=None, help="Mendeley catalog document ID (from catalog-lookup/catalog-search) to copy clean metadata from.")
    p_add.add_argument("--title", default=None, help="Required if --catalog-id is not given.")
    p_add.add_argument("--authors", default=None, help="Semicolon-separated 'First Last; First Last'.")
    p_add.add_argument("--year", default=None)
    p_add.add_argument("--doi", default=None)
    p_add.add_argument("--url", default=None)
    p_add.add_argument("--source", default=None, help="Journal/venue name.")
    p_add.add_argument("--type", default="journal", help="Mendeley document type, default 'journal'.")
    p_add.add_argument("--group", default=None, help="Mendeley group name or ID to add into. Remembered per --bib path (if given) after first use; personal library if omitted and none remembered.")
    p_add.add_argument("--bib", default=None, help="If given, immediately fetch the newly-added document into this references.bib as a deduped BibTeX entry.")

    args = parser.parse_args()

    def with_token_retry(fn):
        creds, tokens = get_access_token()
        try:
            fn(args)
        except SystemExit as e:
            if "expired/invalid" in str(e):
                print("refreshing token and retrying...")
                refresh_access_token(creds, tokens)
                fn(args)
            else:
                raise

    if args.cmd == "authorize":
        cmd_authorize(args)
    elif args.cmd == "list-groups":
        with_token_retry(cmd_list_groups)
    elif args.cmd == "fetch":
        with_token_retry(cmd_fetch)
    elif args.cmd == "catalog-lookup":
        with_token_retry(cmd_catalog_lookup)
    elif args.cmd == "catalog-search":
        with_token_retry(cmd_catalog_search)
    elif args.cmd == "add":
        with_token_retry(cmd_add)


if __name__ == "__main__":
    main()
