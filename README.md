# research-overleaf-wc

**Write your papers on Overleaf, let Claude Code do the plumbing, and let a
writing book coach your prose.**

A [Claude Code](https://claude.com/claude-code) skill for researchers who keep
several LaTeX papers on Overleaf. It lets Claude sync and edit them, manage
your references, and lets you add a citation from your phone by sending a DOI
to a Telegram bot. It can also improve your writing by following the rules of a
writing handbook you own, using ChatGPT as the editor, and shows everything it
does in one local web page.

No dependencies beyond Python 3 and git (Docker only if you want the web page,
`poppler` only to import a book from the command line).

## What it does

| You want to... | What happens |
|---|---|
| Edit a paper | Claude pulls from Overleaf, changes the exact passage in `main.tex`, and pushes back. It reads only what it needs, not the whole folder. |
| Work on several papers | Each paper is registered once (folder, bib file, Mendeley group). Claude always knows which one it is touching and refuses if the folder no longer matches its Overleaf project. |
| Cite a paper already in Mendeley | New Mendeley entries are pulled into `references.bib`, without duplicates. |
| Cite a new paper | Claude finds it, shows you the candidate, and after your OK adds it to Mendeley and the bib file. |
| Add a reference from your phone | Send a DOI to the Telegram bot. It writes the BibTeX entry, adds it to Mendeley, and pushes to Overleaf. |
| Get feedback | Ask for a peer review and Claude scores the draft against a rubric. It never changes the paper unless you ask. |
| Pick up where you left off | Each paper has a private notes file (`.overleaf-skill/memory.md`, never pushed) recording what was done last time. |
| Improve the writing with a book | You import a writing handbook once. When you ask to improve, say, the methods, only the few book pages about methods and only that part of your paper are used. ChatGPT proposes edits; every change is checked and shown to you as a before/after you accept or reject. |
| Keep papers apart | Each paper has its own private index. A lookup for one paper can only ever use that paper's text and the book. |
| See and manage everything in one place | A local web page (runs in Docker only when you start it): papers, readable diffs, the book, references, and charts of what the system did. |

## Setup (about 10 minutes)

You need: [Claude Code](https://claude.com/claude-code), Python 3, git, and an
Overleaf project with [Git access](https://www.overleaf.com/learn/how-to/Git_integration)
(a paid Overleaf feature).

### 1. Install the skill

```bash
git clone https://github.com/OphirIAn-CO/research-overleaf-wc ~/.claude/skills/research-overleaf-wc
~/.claude/skills/research-overleaf-wc/install.sh
```

This creates the private data folder `~/.config/overleaf-skill/` and connects
the tools to Claude Code. Restart Claude Code afterwards.

### 2. Clone your paper from Overleaf

In Overleaf: **Menu → Git**, copy the clone command, and run it wherever you
keep your papers. The password is an Overleaf **Git authentication token**
(Account Settings → Git Integration), not your normal password.

### 3. Register the paper

```bash
python3 ~/.claude/skills/research-overleaf-wc/scripts/common.py add /path/to/your/paper --group "Your Mendeley group"
```

`--group` is optional (skip it if you don't use Mendeley). Repeat for every
paper. Check everything is healthy any time with:

```bash
python3 ~/.claude/skills/research-overleaf-wc/scripts/common.py doctor
```

**That's enough to start.** Open Claude Code and say *"sync my paper"*,
*"add a citation for X to the introduction"*, or *"review my draft"*.

### 4. (Optional) Connect Mendeley

1. Create an app at [dev.mendeley.com](https://dev.mendeley.com) with redirect
   URI `https://localhost/callback`.
2. Save its credentials in `~/.config/overleaf-skill/mendeley_credentials.json`:
   ```json
   {"client_id": "...", "client_secret": "..."}
   ```
3. Run `python3 ~/.claude/skills/research-overleaf-wc/scripts/mendeley_sync.py authorize`
   and follow the prompts (the browser page that fails to load at the end is
   expected; copy its address back into the terminal).

### 5. (Optional) Add references from Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Create `~/.config/overleaf-skill/telegram.json`:
   ```json
   {"token": "<your token>", "allowed_chat_ids": [], "push": true}
   ```
3. Start the bot: `python3 ~/.claude/skills/research-overleaf-wc/scripts/doi_bot.py run`
4. Send your bot any message. It replies with your chat id. Put that number in
   `allowed_chat_ids`, then restart the bot. Until you do, it refuses everyone.
5. Keep it running after a reboot (macOS): `~/.claude/skills/research-overleaf-wc/install.sh bot-service`

**Using it:** send a DOI or a `doi.org` link. The bot adds it to the paper you
last worked on in Claude Code, and every reply says where it went:

```
[my_paper @6aa33c64 -> My Mendeley group]
Added \cite{Alves2018Brazilian}
Pushed to Overleaf.
```

If you haven't touched that paper for a while, the bot asks which paper first.
`/paper` switches papers, `/status` shows the current target.

### 6. (Optional) The writing coach: a book and ChatGPT

1. Import your writing book **once** (it must be a PDF with selectable text). Put it in
   `~/.config/overleaf-skill/inbox/` and import it from the **Book** page of the web app (step 7), or from a terminal
   (`brew install poppler` first):
   ```bash
   python3 ~/.claude/skills/research-overleaf-wc/scripts/writing.py book-ingest ~/Downloads/the-book.pdf --id handbook
   ```
   It finds the chapters from the book's table of contents, cuts the text into named, ordered chunks
   (`ch07 §7.2 "Writing the Methods" · pp.141-143`) and saves an `outline.json` you can check. Running it again on the same
   PDF does nothing.
2. Create `~/.config/overleaf-skill/openai.json` yourself (see `examples/openai.example.json`; never paste the key into a chat).
   You choose the model; none is built in.
3. Ask Claude *"improve the methods of my paper following the book"*, or use the **Write** page. You first see exactly what
   would be sent (the passage with citations, formulas and headings replaced by placeholders, plus a few book rules), and
   nothing leaves your computer until you confirm.
4. Optional, once: **Rule cards** (Book page, or `writing.py book-rules`). It distils each book chunk into a few short rules,
   so later requests carry rules instead of raw book text. It sends the book text to OpenAI one time, so it shows a cost
   estimate and waits for your confirmation.
5. Check how well the book is found with `writing.py eval --file examples/book_eval.example.json` (edit the questions for your
   book). If fewer than about 85% of them find the right chapter, tell Claude: smarter (embedding) search is the planned next step.

### 7. (Optional) The web app: everything in one place

```bash
~/.claude/skills/research-overleaf-wc/install.sh ui      # builds the Docker image once
~/.claude/skills/research-overleaf-wc/ui.sh up           # starts it and opens your browser
~/.claude/skills/research-overleaf-wc/ui.sh down         # stops it (it also stops itself when idle)
```

Pages: **Papers** (pull/push, health), **Write** (preview, send, review changes side by side, apply), **Book** (browse and
search the chunks, fix the outline, import), **Monitor** (what the system did, as charts), **Paper index** (what the system
knows about each paper, its short "paper card", what you accepted and rejected), **References** (add by DOI, Mendeley),
**Settings**. To let the app pull and push to Overleaf, create `~/.config/overleaf-skill/overleaf_git.json` yourself
(see `examples/overleaf_git.example.json`).

The web app cannot apply text by itself until the "watermark clean" service from your `watermarks-remover` skill is set in
Settings (your standing rule is never to apply unclean text). Until then use **Hand to Claude**: you tick the changes and
tell Claude "apply the proposal I handed you".

## Where things live

| What | Where |
|---|---|
| The skill (code, docs) | `~/.claude/skills/research-overleaf-wc/` (this repo) |
| Your papers list, tokens, credentials | `~/.config/overleaf-skill/` (never in git) |
| Per-paper notes | `<paper>/.overleaf-skill/` (git-ignored, never pushed to Overleaf) |
| Per-paper index, paper card, ChatGPT proposals, activity log | also inside `<paper>/.overleaf-skill/`; one paper's files are never read for another |
| The imported book, OpenAI key, Overleaf token | `~/.config/overleaf-skill/` (never in git; the book is copyrighted, keep it out of the repo) |

## Safety

- Secrets stay in `~/.config/overleaf-skill/`, outside this repo and outside your papers.
- The bot only obeys chat ids you whitelist.
- Before any action Claude checks the folder still points at the same Overleaf project, and refuses otherwise.
- LaTeX build files (`.aux`, `.log`, `.pdf`, ...) are never pushed to Overleaf by the sync tool.
- Peer review only runs when you ask. Mendeley is written to only when you ask Claude to add a reference or send a DOI to the bot.
- The ChatGPT pass only runs when you ask. It sends one passage at a time (citations, formulas and headings masked), the paper's short card and a few book rules, never the whole paper, never another paper, never your keys. By default you see the exact text and confirm first. Answers are checked before you see them (protected LaTeX intact, no new numbers, no dashes, balanced braces) and nothing is written until you accept it.
- The web app listens on `127.0.0.1` only (your computer, not the network), needs a fresh access token every time you start it, refuses requests from other websites, loads nothing from the internet, runs as you with a read-only filesystem and no extra privileges, and can see only your registered paper folders, the data folder and the inbox. It closes itself when idle.
- The activity log holds only counts, ids, tokens and timings, never your text.

## Troubleshooting

| Problem | Fix |
|---|---|
| `refusing to act: ... origin changed` | You moved or re-cloned the paper. Run `common.py add <new path> --name <name> --relink`. |
| Push rejected | Someone edited on Overleaf. Ask Claude to pull first, or tap **Pull & push** in Telegram. |
| Bot silent | Is it running? `pgrep -fl doi_bot`. Is your chat id in `allowed_chat_ids`? |
| DOI not found | The bot only accepts DOIs that resolve at doi.org. Preprints without one need Claude's web-search route instead. |
| `ui.sh up` says a port is already in use | Nothing to do: it tells you what holds the port (another container or program) and starts on the next free one, printing the right address. Pick a start port with `OVERLEAF_UI_PORT=9000 ./ui.sh up`. |
| `ui.sh up` says Docker is not running | Start Docker Desktop, wait until it says it is running, try again. |
| The web app does not list a paper | It only sees papers registered when it started: `ui.sh down`, then `ui.sh up`. |
| Pull/Sync buttons are disabled or fail in the web app | Create `~/.config/overleaf-skill/overleaf_git.json` (the Docker app cannot use your Mac keychain). |
| Book import found no chapters / odd chapters | Open **Book > Import > Review the outline**, fix titles, levels and pages, save, then "Build chunks from my outline". A scanned (image-only) PDF needs OCR first. |
| "Apply" is blocked in the web app | The watermark-clean service is not set in Settings. Use **Hand to Claude**. |
| ChatGPT pass says the model was not found / key rejected | Check `model` and `api_key` in `openai.json` (the error message does not echo the key). |
| Anything else | `common.py doctor` lists per-paper problems; `writing.py stats` prints what the system has been doing. |

Self-check for the DOI code: `python3 scripts/doi_bot.py demo` (needs internet).

## More detail

`SKILL.md` is the full instruction set Claude follows (workflow, session
memory, peer-review procedure, writing coach). Optional companion skills it can use:
`academic-paper` (review rubric) and `watermarks-remover` (prose cleanup).

Developers: `python3 -m unittest discover -s tests -t .` runs everything offline (a fake OpenAI and a small test PDF stand in
for the real ones). `tests/e2e/` has a demo server and Playwright scripts that drive the web app in a real browser.
The OpenAI client was tested against a local fake of the API, not the live service: do a first real pass on a throwaway
paragraph and read the preview before trusting it with more.

MIT licensed.
