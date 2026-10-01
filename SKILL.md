---
name: research-overleaf-wc
description: Sync and edit any locally-cloned Overleaf project backed by its Overleaf Git bridge, anywhere on disk, including pulling new references from Mendeley, citing them, and running a peer-review pass on the draft. Use when the user says "overleaf", "sync the paper", "pull/push overleaf", "edit main.tex", "update the paper on overleaf", "pull references from Mendeley", "fetch new citations", "review my paper", "peer review this draft", or asks to read/edit/find/review something in an Overleaf project's LaTeX or bibliography. Also use for the writing coach: "improve / polish / tighten / rewrite my methods (abstract, introduction, ...)", "follow the book", "check this section against the writing book", "what does the book say about ...".
allowed-tools:
  - Read
  - Edit
  - Grep
  - Glob
  - Bash
  - mcp__overleaf__overleaf_pull
  - mcp__overleaf__overleaf_status
  - mcp__overleaf__overleaf_sync
  - mcp__overleaf__mendeley_fetch
  - mcp__overleaf__mendeley_list_groups
  - mcp__overleaf__mendeley_catalog_search
  - mcp__overleaf__mendeley_add_reference
  - mcp__overleaf__book_query
  - mcp__overleaf__paper_query
  - mcp__overleaf__chatgpt_revise
  - mcp__overleaf__revise_record
  - WebSearch
  - WebFetch
  - mcp__code-review-graph__build_or_update_graph_tool
  - mcp__code-review-graph__query_graph_tool
  - mcp__code-review-graph__semantic_search_nodes_tool
  - mcp__code-review-graph__list_graph_stats_tool
---

# Overleaf paper sync, edit & Mendeley citations

This skill works with **any** locally-cloned Overleaf project (Overleaf Git
bridge clone), regardless of where it lives on disk — not tied to one fixed
folder. It also finds and pulls new references from Mendeley straight into a
project's `references.bib` so they can be cited.

**Nothing here is scoped to one specific paper or one specific Mendeley
group.** Every operation — which Overleaf repo, which Mendeley group — is a
parameter resolved per call (explicit arg, or a remembered-per-repo mapping,
see below), never a hardcoded default in code or in this doc. Any mention of
a specific project/group elsewhere in this file is describing what has
already been configured for *that* project as a worked example, not the
skill's behavior in general — treat every new repo/group pairing as unknown
until established the same way.

One MCP server (`overleaf`, registered at user scope, script at
`<skill>/scripts/overleaf_mcp.py`) exposes twelve tools, nothing else:
- `overleaf_projects`: the registry of papers (name, folder, bib file,
  Mendeley group) that every tool and the Telegram bot share.
- `overleaf_pull`, `overleaf_status`, `overleaf_sync`: git sync with the
  Overleaf bridge.
- `mendeley_fetch`, `mendeley_list_groups`: pull new references already in
  a Mendeley library/group into the bib file.
- `mendeley_catalog_search`, `mendeley_add_reference`: look up a paper in
  Mendeley's canonical catalog and add it to a Mendeley group/library, then
  fetch it into the bib file.
- `paper_query`, `book_query`, `chatgpt_revise`, `revise_record`: the writing coach
  (see "Writing coach" below).
(Mendeley tools wrap `<skill>/scripts/mendeley_sync.py`; shared state lives in
`scripts/common.py`; the writing coach lives in `scripts/writing_core/` with a CLI,
`scripts/writing.py`.)

For finding a paper to cite in the first place (general literature search,
not just what's already in Mendeley), use the native `WebSearch`/`WebFetch`
tools — see "Search & cite a new paper" below.

It intentionally does **not** expose read/write/list-file tools: reading and
editing LaTeX/bib text go through native tools (`Read`, `Edit`, `Grep`),
which are already loaded and cheaper per call than a custom MCP round-trip.

Every tool takes an optional `repo_path`: a **registered project name** or an
absolute path (an unknown path is auto-registered, bib file auto-detected).
Omitted, it falls back to the current directory if that is an Overleaf clone,
else **the paper last worked on**, else the registry default. Still resolve it explicitly per step 0 below and
pass it, so a call never lands on the wrong paper. Never narrate the
server's internal fallbacks to the user.

**Active paper.** Every tool call that resolves a paper records it as the
"active paper" (`state.json`), and so does starting a session inside an
Overleaf clone. The Telegram bot follows it, so DOIs land in the paper Claude
is working on without any manual switch. **Safety checks:** before acting, the
registered folder is verified (exists, is a git repo, `origin` still equals the
remote stored at registration, bib file present); a mismatch is refused with
the fix command. `python3 scripts/common.py doctor` audits every paper.
`overleaf_sync` never stages untracked LaTeX build files (`.aux`, `.log`,
`.pdf`, ...) and reports how many it skipped.

**All state is in `~/.config/overleaf-skill/` (override with
`OVERLEAF_SKILL_HOME`), never in git or in an Overleaf repo:** `projects.json`
registry, Mendeley credentials/tokens, Telegram config. Manage the registry
with `overleaf_projects` or `python3 scripts/common.py list|add|rm|default`.

## Workflow

0. **Identify the repo** — if the user names a project or path, use that. If
   only one project has been discussed in this conversation, use it.
   Otherwise ask, or `find ~ -maxdepth 6 -iname "*.git" -path "*overleaf*"`
   to locate candidates. Confirm before touching an unfamiliar path.

   Do this silently with respect to any other project this skill has been
   used on before. Never tell the user "this is different from [some other
   paper]" or otherwise name/describe a prior or example project while
   resolving *this* one — that comparison is not useful to them and leaks
   unrelated context. Just resolve this repo's path and proceed.

   **No local clone found for the project the user means?** Ask for the
   Overleaf project's Git URL (from Overleaf's project menu → Git, e.g.
   `https://git.overleaf.com/<project_id>`) and a destination directory,
   then `git clone <url> <dest>` via `Bash` (plain git, no custom
   tool/script needed). Overleaf's git bridge needs a git-specific
   token/password for HTTPS auth (not the regular Overleaf login) — if
   `git clone` prompts for credentials, tell the user and let them enter it
   themselves rather than asking them to paste it into chat. Once cloned,
   use that path as `repo_path` for the rest of the workflow.
1. **Load session memory — cheaply, not with a full `Read`** — if
   `<repo_path>/.overleaf-skill/memory.md` doesn't exist yet, this is the
   first session for this repo: create it after step 0 using the template
   below, and note that no prior context exists. If it exists, see "Session
   memory" below for how to pull just the Project snapshot and the most
   recent entries (and, only when that's not enough, how to reach for the
   code-review-graph tools instead of reading further down the file)
   instead of `Read`-ing the whole file every session.
2. **Pull first** — `overleaf_pull` with `repo_path` (git pull --ff-only)
   before editing, in case changes were made in the Overleaf web editor
   since the last sync.
3. **New references? Fetch from Mendeley first** — if the user wants to add
   citations from their Mendeley library, call `mendeley_fetch` with
   `repo_path` (and `bib_filename` if not the default `references.bib`)
   *before* editing `main.tex`. It appends only genuinely new BibTeX entries
   (deduped by cite key/DOI) — see "Mendeley fetch details" below for how
   group scoping works. Skip this step if the user is just editing existing
   content.
4. **Locate** — `Grep` under the repo for the relevant spot (section
   heading, cite key, phrase) instead of reading whole files, e.g.
   `\\section{`, `\\subsection{`, or a `\cite{...}` key. If entries were just
   fetched from Mendeley, grep the new cite keys (`mendeley_fetch`'s output
   lists them) to find where in `main.tex` they belong contextually.

   Every Overleaf project has its own structure (section names, file count,
   appendices) — always grep/glob to discover it for the repo at hand rather
   than assuming any particular layout, and never surface another project's
   structure as if it were relevant to this one.
5. **Read only what's needed** — `Read` with `offset`/`limit` scoped to the
   lines found in step 4, not the full file.
6. **Edit** — native `Edit` tool (old_string → new_string) directly on the
   file in the repo, e.g. inserting `\cite{newkey}` at the right spot. Never
   reconstruct and rewrite the whole file for a small change. When the
   change originates from an advisor's or reviewer's comment, see
   "Implementing advisor/reviewer comments" below — it must land as an edit
   to the existing relevant passage, not a new section.

   **No em dashes.** Any prose Claude writes or rewrites for this repo
   (`main.tex`, a response letter, anything) must not use the em dash (—) or
   en dash (–) as punctuation. Use a comma, semicolon, or period instead —
   restructure the sentence if needed rather than reaching for a dash. This
   is a standing style rule for this skill, not something to ask about each
   time. Check any freshly written/edited sentence for this before moving
   on; if one slips in, fix it before the watermark pass in 6b, not after.
6b. **Strip AI provenance marks from new/rewritten prose — mandatory, not
   optional.** This is the user's own research content; Claude is only used
   to draft faster/clearer phrasing and LaTeX implementation, not to
   originate the ideas. So, for any substantial prose Claude wrote or
   rewrote in this step (not for boilerplate LaTeX/citation keys), always
   run the `watermarks-remover` skill's Layer A clean on that text before it
   goes to Overleaf — every time, no need to ask, and never skip it just
   because the change felt small. Use `/inspect` then `/clean` against the
   running service (see that skill for the API); if the service isn't
   reachable, say so and skip cleaning rather than editing the file with an
   unclean pass — but treat that as the exception that blocks the edit
   until resolved, not a routine opt-out. Also mention Layer B (paraphrase/
   humanize rewrite) is available on request for that same text, per the
   watermarks-remover workflow — don't run it silently since it changes
   wording more aggressively.
7. **Check before syncing** — `overleaf_status` with `repo_path` to see
   which files changed (git status --short) before pushing.
8. **Sync** — `overleaf_sync` with `repo_path` and an optional commit
   `message` to commit + push everything (both the updated `references.bib`
   and any `main.tex` citation edits) to Overleaf. Pass `push: false` to
   just commit locally if batching multiple edits before a final push.
9. **Update session memory** — before ending the session (or after any
   meaningful chunk of work: a sync, a batch of edits, a Mendeley fetch),
   append an entry to `<repo_path>/.overleaf-skill/memory.md` covering what
   was done this session. See "Session memory" below. Never delete or
   overwrite prior entries — this file only grows.

## Implementing advisor/reviewer comments

When the user brings a comment/correction from an advisor, thesis
committee, journal reviewer, or the "Peer review a draft" step above, and
wants it addressed in the manuscript: **the fix is a modification to the
existing passage the comment is about, not a new section, subsection, or
appended block.** Reviewer feedback like "clarify the sampling method" or
"this claim needs a citation" gets resolved by editing the sentence/
paragraph in place — the same `Grep` (locate) → `Read` (scoped) → `Edit`
(in place) flow as step 4-6 above — never by tacking on a new
`\subsection{Response to Reviewer}`-style block, a "Revisions" appendix, or
a separate summary section inside `main.tex` itself.

This applies specifically to the manuscript body. It does not forbid a
genuinely separate reviewer-response *document* if the venue requires one
(e.g. a cover letter or point-by-point response letter as its own file) —
if the user asks for that, it's a distinct deliverable, not part of
`main.tex`, and should be confirmed with them explicitly rather than
assumed. Default to "edit the manuscript in place" unless they ask for a
response letter by name.

If a comment doesn't map cleanly onto existing text (e.g. "add a
limitations paragraph" where none exists yet), that's a judgment call, not
an automatic new-section case — ask the user where it should go, or infer
placement from the paper's own structure (discovered via step 4's
grep/glob) rather than defaulting to appending at the end.

## Writing coach (book rules + per-paper index + ChatGPT pass)

**Opt-in only, like peer review.** Start it when the user asks to improve, polish,
tighten, rewrite, or "check against the book" a part of a paper (or asks what the book
says about something). Never as a side effect of syncing, editing, or citing.

What it is: a book (imported once) and each paper (indexed separately, refreshed on
every call) are searched by *retrieval*, so you never read the whole book or whole
manuscript. ChatGPT is only the editor: one passage per call, validated before you see
it. Each paper's index is private to it: never use, quote or mention another paper's
content while working on this one (same rule as step 0).

Tools: `paper_query` (this paper's relevant chunks with file and line ranges; empty
query = outline; "my methods" returns that whole part), `book_query` (the few book
chunks that matter, with page references to cite), `chatgpt_revise` (one passage ->
validated proposal; never edits files), `revise_record` (logs accept/reject).

1. Resolve the paper (step 0) and `overleaf_pull`.
2. Which part? If unclear, `paper_query` with no query shows the outline; confirm with
   the user. For "what does the book say about X" just use `book_query` and cite pages.
3. `chatgpt_revise` with `instruction` and `part` (or `chunk_ids`). While the paper's
   dry-run setting is on (the default) it returns a **preview**: the exact payload
   (masked passage, book rules, model) and a `payload_hash`. Show it to the user and
   ask. Only after they agree call again with `dry_run: false` and
   `confirm: <payload_hash>`. Never send without that agreement. (Once the user has
   turned the preview off for a paper, `writing.py settings --dry-run off`, it sends
   directly.)
4. Present the proposal: each hunk (`u1.h2`) with old/new text, the reason, and the
   book page it follows. Questions the model raised go to the user; never answer them by
   guessing. If validation failed, say so; nothing is offered, and do not hand-edit
   around it.
5. Apply only the hunks the user accepts, with native `Edit` in place (old text -> new
   text, as in "Implementing advisor/reviewer comments"). Steps 6 (no dashes) and 6b
   (watermark clean) apply to the new text exactly as for any prose you write; clean
   before inserting. If a hunk's old text no longer matches the file, stop and re-run.
6. `revise_record` with the accepted/rejected hunk ids (`applied: true`), then
   `overleaf_status` / `overleaf_sync` as usual and step 9 (session memory).

The same flow is available to the user in the local web app (`<skill>/ui.sh up`, Docker, loopback
only): a readable side-by-side diff, the book browser, and a Monitor page of everything the
system did. When the user asks to "open the UI / dashboard / monitor", run `ui.sh up` and give
them the printed URL (it carries a one-time access token; do not repeat it anywhere else).

What leaves the machine: only the selected passage (LaTeX masked), the paper card, and a
few capped book rule snippets; never the whole manuscript, credentials, or another paper.
Setup (the user does these themselves): `python3 <skill>/scripts/writing.py book-ingest
<book.pdf> --id <slug>` once, and `~/.config/overleaf-skill/openai.json`
(`{"api_key": "...", "model": "..."}`, never pasted in chat). `writing.py doctor` reports
what is missing.

## Session memory

Every repo this skill touches gets its own local, git-ignored notes file:
`<repo_path>/.overleaf-skill/memory.md`. It exists so each new session
starts with the context the last one ended with — project structure, the
remembered Mendeley group, what was done recently, and any open threads —
instead of re-deriving all of it from scratch every time.

**Local-only, never pushed.** This directory must never reach Overleaf:
`overleaf_sync` runs `git add -A`, so on first creation for a repo, ensure
`.overleaf-skill/` is listed in that repo's `.gitignore` (append a
`.overleaf-skill/` line if the file doesn't already exclude it; create
`.gitignore` if the repo has none). Verify this *before* the first
`overleaf_sync` call of the session touches that repo — check with
`git check-ignore .overleaf-skill/memory.md` if unsure.

**Reading it back — never a full `Read`.** `memory.md` only grows (newest
entry on top, entries never deleted), so after enough sessions on one paper
a plain `Read` of the whole file burns tokens on months of old log entries
you don't need. Instead:

1. `Grep -n` for `^## Project snapshot`, `^## Session log`, and `^### ` in
   the file. This is a handful of matches with line numbers, at a fraction
   of the file's token cost, and tells you exactly where each section and
   each dated entry starts.
2. `Read` with `offset`/`limit` for just the **Project snapshot** section
   (between the two `##` matches) and the **top 1-2 dated entries** (the
   newest — the log is newest-first). That's normally everything a session
   needs: current group/structure + what just happened.
3. **Only if that's not enough** — the user asks about something from
   further back ("why did we pick this Mendeley group," "when did we last
   touch the Methods section") and the relevant date isn't obvious from the
   `### ` headers alone — first try `paper_query` with `kind: "memory"` (the
   memory file is indexed per paper, one chunk per dated entry). Only if that
   is unavailable, reach for the code-review-graph tools instead of
   reading down the rest of the file blind:
   - `mcp__code-review-graph__build_or_update_graph_tool` with
     `repo_root: <repo_path>` first, if `list_graph_stats_tool` shows no
     graph yet or it's stale. This is a small repo (one paper + one bib
     file), so the build/incremental-update is cheap even though the graph
     is normally built for source code, not prose.
   - `mcp__code-review-graph__query_graph_tool` with
     `pattern: "file_summary"`, `target: ".overleaf-skill/memory.md"` to
     see how the file is indexed before pulling any more of it in.
   - `mcp__code-review-graph__semantic_search_nodes_tool` with a `query`
     describing what's being looked for, to jump straight to the matching
     line range instead of scanning the log top-to-bottom. Requires
     `embed_graph_tool` to have been run once for real semantic (not just
     keyword) matches — falls back to FTS/keyword matching otherwise, which
     is still cheaper than a full `Read`.
   Then `Read` only the specific `offset`/`limit` the query pointed at.

   Treat this as an escalation path, not a default step — for a normal
   session, step 1-2 above is enough and step 3 never fires.

**First time for a repo** (no `.overleaf-skill/memory.md` yet): create it
with this template, filled in from what step 0/1 of the workflow just
discovered:

```markdown
# Overleaf skill memory — <project name / repo folder name>

Local-only notes for the research-overleaf-wc skill on this repo. Never
committed or pushed to Overleaf (.overleaf-skill/ is git-ignored).

## Project snapshot
- Repo path: <absolute path>
- Mendeley group: <name + id, or "personal library", or "not yet resolved">
- Key files: <e.g. main.tex (~N lines), references.bib (~N lines)>
- Structure (as of <date>): <top-level sections/outline, or "see main.tex"
  if it changes often>

## Session log
(newest entry on top)

### <date>
- What was done: <fetched N Mendeley refs / edited section X / synced>
- Files touched: <paths>
- Open items / follow-ups: <anything left for next time, or "none">
```

**Every later session**: read this file in step 1, then in step 9 prepend a
new dated entry to the Session log (newest on top) summarizing this
session's work — what was fetched/edited/synced, files touched, and any
follow-ups. If the project snapshot changed (structure reorganized, group
changed, files renamed), update that section in place; otherwise leave it.
Keep entries concise — a few lines each — this is working memory for the
skill, not a changelog for humans. Never delete the file or wipe prior
entries; it should only accumulate and occasionally get its snapshot
refreshed.

## Mendeley fetch details

`mendeley_fetch` pulls documents from the user's Mendeley library via the
Mendeley API and appends new ones as BibTeX entries to `references.bib`. No
manual field mapping — it uses Mendeley's per-document `application/x-bibtex`
content negotiation directly.

**Group scoping is per-project, stored in the registry.** The Mendeley
**group** linked inside Overleaf's own project settings is *not* readable
from the local git-bridge clone, so it can't be auto-detected. Instead it is
stored once per project in `projects.json` (`mendeley_group`): set it with
`python3 scripts/common.py add <path> --group "Group name"`, or by passing
`group` to the first `mendeley_fetch`/`mendeley_add_reference` call for that
project (remembered after). Ask the user once per project, never again.
Use `mendeley_list_groups` to find id/name pairs. Pass
`personal_library: true` to use the personal library instead.

**One-time setup** (already done for this account, only needed again if
adding a new Mendeley account or the tokens are revoked):
1. Register an app at dev.mendeley.com — standard "Mendeley login"
   authorization-code flow (not the Elsevier SSO variant), redirect URI
   `https://localhost/callback`.
2. User drops `client_id`/`client_secret` into
   `~/.config/overleaf-skill/mendeley_credentials.json` themselves
   (never via pasted chat text — sensitive; have them run the `cat > ... <<
   EOF` command directly).
3. Run `python3 <skill>/scripts/mendeley_sync.py
   authorize` — prints an authorize URL, user logs in in-browser, browser
   redirect to `https://localhost/callback?code=...` fails to load
   (expected — no local server), user copies the URL/code from the address
   bar and pastes it back at the prompt. Saves tokens to
   `mendeley_tokens.json` in the data dir.
4. Requests use a browser-like User-Agent (Cloudflare in front of the
   Mendeley API blocks default Python/urllib User-Agents with a `1010`
   error otherwise).

Credentials and tokens live in the data dir above, **outside** every
Overleaf repo and this skill's git repo, so `overleaf_sync`'s `git add -A`
and `git push` of the skill can never pick them up.

`mendeley_fetch` auto-refreshes the access token on expiry via the stored
refresh token; if that also fails, re-run `authorize`.

## Search & cite a new paper (not yet in Mendeley)

When the user wants a paper cited that isn't already in their Mendeley
library:

1. **Web search** — use `WebSearch` (and `WebFetch` on a specific landing
   page if needed, e.g. to confirm a DOI) to find the paper. General search,
   not limited to Mendeley's catalog — covers preprints/arXiv/journal sites
   Mendeley's catalog may not have indexed.
2. **Confirm with the user** — show the candidate's title, authors, and
   URL/DOI *before* adding anything. Never add silently; ambiguous searches
   can surface the wrong paper or a low-quality match.
3. **Check Mendeley's catalog** — `mendeley_catalog_search` with the DOI
   (exact) or title (fuzzy) found in step 1. A catalog match gives cleaner,
   canonical metadata than manual entry, so prefer it when available.
4. **Add it** — `mendeley_add_reference`:
   - If a catalog match exists: pass its `catalog_id` (copies canonical
     metadata).
   - Otherwise: pass manual fields (`title`, `authors`, `year`, `doi`,
     `url`, `source`) from what was found in step 1.
   - Always pass `repo_path` (and `group` if this repo/bib has no group
     remembered yet — see above) so it lands in the right project's
     `references.bib`, not just the personal library.
   This creates the Mendeley document *and* fetches it into `references.bib`
   as a deduped BibTeX entry in one call.
5. **Cite it** — same as step 4–6 of the main workflow: grep `main.tex` for
   where it belongs contextually, `Edit` in `\cite{newkey}`.
6. **Sync** — `overleaf_status` / `overleaf_sync` as usual.
7. **Update session memory** — same as step 9 of the main workflow.

## Add references by DOI (Telegram bot / CLI)

`scripts/doi_bot.py` (stdlib only, no Mendeley needed). Resolves a DOI via
doi.org BibTeX content negotiation, renames the key to `Author2022Word`,
dedupes by DOI, appends to `references.bib`, then commit + push to Overleaf.

- `python3 scripts/doi_bot.py add <doi-or-url> [project]`: one-off add (project = name or path, default cwd/registry).
- `python3 scripts/doi_bot.py run`: Telegram bot; send it a DOI or doi.org
  link (several per message are fine) and it replies with the `\cite{key}`.
- `python3 scripts/doi_bot.py demo`: self-check (needs network).

Config: `~/.config/overleaf-skill/telegram.json`
(`{"token": "...", "allowed_chat_ids": [...], "push": true, "contact": "you@example.com"}`).
Token comes from @BotFather; the user creates the file themselves, never via
chat. With `allowed_chat_ids` empty the bot refuses and replies with the
sender's chat id. Papers come from the shared registry (re-read on every
message, so a newly registered paper appears without restarting). The target
paper is the shared **active paper** (see above), so it follows Claude's work.
If nothing touched it for `stale_hours` (telegram.json, default 6) the bot
asks which paper first, with one button per paper. `/paper` switches, `/status`
shows the target and what set it. Every reply is prefixed
`[project @overleaf-id -> Mendeley group]`; the folder is verified first and
a mismatch is refused. A "Pull & push" button appears when a push fails.
Always-on: `./install.sh bot-service` (launchd, macOS).

## Peer review a draft

**Opt-in only — never run this on your own initiative.** This is a separate,
explicit request, not a step that happens automatically while syncing,
editing, or citing. Only start it when the user's message itself asks to
review, critique, evaluate, or "peer review" the paper (or a section of it)
in an Overleaf repo. A sync, an edit, or a citation fetch — even a large one
— is never itself a trigger to run a review; if the user wants one, they'll
say so.

Once actually asked:

1. **Resolve the repo** — same as step 0/2 of the main workflow (identify
   `repo_path`, `overleaf_pull` first so the review runs against the latest
   content).
2. **Read the whole draft** — unlike normal editing, a real review needs the
   full `main.tex` (and any `\input`/`\include`d files), not a scoped
   `offset`/`limit` read. Read `references.bib` too if checking citation
   density/quality is in scope.
3. **Borrow the rubric, don't reinvent it** — read
   `~/.claude/skills/academic-paper/agents/peer_reviewer_agent.md` (part of
   the `academic-paper` skill's 12-agent pipeline) and follow its five-
   dimension scoring rubric, review process, verdict mapping, and output
   format directly against this paper's content. This skill always points at
   that file rather than duplicating the rubric here, so an update there is
   picked up automatically.

   Run it **standalone** — this skill has no `draft_writer_agent`,
   `structure_architect_agent`, `citation_compliance_agent`, or
   `argument_builder_agent` to hand off to, so skip anything in that file
   that assumes those exist (the revision-loop handoffs, the multi-agent
   input/output tables). Derive missing optional inputs (Paper Outline,
   Citation Audit Report, Argument Blueprint) from `main.tex`/`references.bib`
   themselves when not otherwise available, per that file's "Incomplete
   Input" handling. Infer paper type (theoretical, case study, IMRaD, etc.)
   from the draft's own structure — never assume the type or discipline of
   any other project this skill has reviewed before.
4. **Present the report to the user as chat output** — never silently apply
   any of the suggested revisions. Flag issues; let the user decide what to
   act on. If they then ask for specific fixes, apply those with `Edit` per
   the main workflow (steps 4-8), and re-run just the relevant part of the
   rubric if they want a re-check.
5. **Optionally save the report** — if the user wants a persistent copy,
   write it to `<repo_path>/.overleaf-skill/reviews/<date>-review.md`. This
   directory needs the same `.gitignore` treatment as `.overleaf-skill/`
   generally (see "Session memory" above) — never let a review report reach
   Overleaf via `overleaf_sync`.
6. **Update session memory** — same as step 9 of the main workflow: note
   that a review ran, the verdict, and any follow-ups the user chose.

## Notes

- `overleaf_sync` runs `git add -A`, so it will pick up any new files added
  under the repo, not just tracked-file edits.
- If `overleaf_pull` fails (non-fast-forward), there's a conflicting change
  made on Overleaf's web editor and in this local clone at the same time —
  surface the conflict to the user rather than force-pushing over it.
- Each Overleaf project clone has its own `.git` — independent of any git
  repo the rest of its containing research project directory may or may not
  have.
