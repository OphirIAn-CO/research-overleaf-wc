# research-overleaf-wc

**Write your papers on Overleaf, let Claude Code do the plumbing, and let a
writing book coach your prose.**

A [Claude Code](https://claude.com/claude-code) skill for researchers who keep
several LaTeX papers on Overleaf. It lets Claude sync and edit them and manage
your references. It also comes with a **chat app that runs on your own computer**
(like Claude Desktop or ChatGPT Desktop): pick a paper, say what you want ("improve
the flow of the text before Figure 1 in the methods", "fix the grammar in the
introduction", "peer review my paper", or paste a DOI), and it does it, following
the rules of a writing handbook you own, with ChatGPT as the editor. You approve
every change; nothing is saved or sent without you.

No dependencies beyond Python 3 and git (Docker only if you want the web page,
`poppler` only to import a book from the command line).

## How it works, in plain words

1. **Pick a paper.** The app keeps private notes about each paper. One paper never sees another's.
2. **Say what you want**, in your own words. The app works out which part of your paper you mean ("the methods", "the
   text before Figure 1", "the first paragraph", a section by its title) and finds the advice in your writing book that
   applies to it. You never have to look at the book or at how it was cut up.
3. **OpenAI rewrites that part** following the advice, and tells you what was weak and how it fixed it. Citations,
   formulas, numbers and figures are replaced by tags before anything leaves your computer, and an answer that changes any
   of them is thrown away.
4. **You decide.** Keep or set aside each change. If you want to adjust the tone, copy the text to QuillBot (it can sit next
   to the chat), paste your final version back, and the app checks that no citation or number was lost.
5. **Approve.** The text is saved in your files, and one click sends it to Overleaf. The Activity page tells you in
   sentences what happened and what it cost.

Also in the chat: **"peer review my paper"** (it asks first, because it reads the whole paper) and **pasting a DOI**, which adds
the reference to your bibliography, to your Mendeley group and to Overleaf, and gives you the `\cite{...}` to use.

## What it does

| You want to... | What happens |
|---|---|
| Edit a paper | Claude pulls from Overleaf, changes the exact passage in `main.tex`, and pushes back. It reads only what it needs, not the whole folder. |
| Work on several papers | Each paper is registered once (folder, bib file, Mendeley group). Claude always knows which one it is touching and refuses if the folder no longer matches its Overleaf project. |
| Cite a paper already in Mendeley | New Mendeley entries are pulled into `references.bib`, without duplicates. |
| Cite a new paper | Claude finds it, shows you the candidate, and after your OK adds it to Mendeley and the bib file. |
| Add a reference from your phone | Send a DOI to the Telegram bot. It writes the BibTeX entry, adds it to Mendeley, and pushes to Overleaf. |
| Get feedback | Ask for a peer review, in Claude Code or in the chat app. The draft is scored against the `academic-paper` skill's rubric, with a list of issues; "Fix this" turns an issue into an ordinary suggestion. It never changes the paper by itself. |
| Pick up where you left off | Each paper has a private notes file (`.overleaf-skill/memory.md`, never pushed) recording what was done last time. |
| Improve the writing with a book | You prepare a writing handbook once. When you ask to improve, say, the methods, only the few book pages about methods and only that part of your paper are used. ChatGPT proposes edits with an explanation; every change is checked and shown to you as a before/after you keep or set aside. |
| Keep papers apart | Each paper has its own private index. A lookup for one paper can only ever use that paper's text and the book. |
| Add a reference | Paste a DOI in the chat (or send it to the Telegram bot). It lands in `references.bib`, in the paper's Mendeley group, and in Overleaf, and you get the `\cite{...}` to copy. If Mendeley is unreachable the entry waits and is retried; the bibliography is never held back. |
| See and manage everything in one place | A local chat app (runs in Docker only when you start it): your papers, the conversation, readable before/after changes, References, Activity, Settings, and QuillBot, Overleaf and the paper text beside the chat. |

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

### 6. (Recommended) The chat app

```bash
~/.claude/skills/research-overleaf-wc/install.sh ui      # builds the Docker image once
~/.claude/skills/research-overleaf-wc/ui.sh up           # starts it and opens your browser
~/.claude/skills/research-overleaf-wc/ui.sh down         # stops it (it also stops itself when idle)
```

Everything else is set up **inside the app**, in **Settings** (nothing to edit by hand):

1. **OpenAI**: paste your API key and the model name you want to use; press **Test** (it is a read-only check, no cost).
2. **Overleaf**: paste your Overleaf Git token (Overleaf: Account Settings, Git Integration) so the app can get the latest and
   send your changes; press **Test**.
3. **Writing book**: put the PDF (selectable text) in `~/.config/overleaf-skill/inbox/` and press **Prepare the book**. It is read
   once. Optionally press **See the cost**, then **Make the summaries once**: a one-time step that makes every later request
   cheaper and sharper (it sends the book text to OpenAI once, shows the estimate first, and can resume).
4. **Text-cleaning service** (your `watermarks-remover` step): put its address here so **Approve and save** works from the app.
   Until then the button is **Let Claude apply it**: you tick the changes and tell Claude "apply the suggestion I handed you".
5. **Papers**: your registered papers are in the sidebar. To add more from the app, share the folder that holds your Overleaf
   clones once (`ui.sh folders add ~/path/to/folder`, then `ui.sh down` and `ui.sh up`); the app lists the clones in it with an
   **Add** button. `common.py add <path> --group "Mendeley group"` still works.
6. **Mendeley**: signing in needs a browser, so run `python3 scripts/mendeley_sync.py authorize` once (Settings shows the exact
   command). Link a paper to its group on the References page.

Then just talk to it. The pages are **Chat**, **References**, **Activity** and **Settings**; the papers and your chats are in the sidebar.

**QuillBot and Overleaf beside the chat.** QuillBot has no public API, so the paste stays yours, but you can switch on
**QuillBot**, **Overleaf** and **Paper text** above the chat to see them side by side. Some sites refuse to be shown inside another
page, and Safari can block their sign-in; each pane has **Open beside the app**, which opens the site in a window on the free half of
your screen. **Settings, Advanced, Test the side panes** tells you what works in your browser. **Copy for QuillBot** copies the
suggestion with your citations and formulas as tags (`[[LX001]]`) so QuillBot cannot damage them; paste QuillBot's result into
"Your final version" and the app checks every tag is still there before you can approve.

**Peer review** needs the `academic-paper` skill installed under `~/.claude/skills/` (`ui.sh` shares its rubric folder read-only).

Terminal alternatives (for Claude Code or scripts): `writing.py book-ingest`, `book-rules`, `revise`, `eval`, `stats`, `doctor`; see
`SKILL.md`. Check how well the book is found with `writing.py eval --file examples/book_eval.example.json` (edit the questions for
your book), or use **Book check** in the book library (Settings, Advanced).

## Where things live

| What | Where |
|---|---|
| The skill (code, docs) | `~/.claude/skills/research-overleaf-wc/` (this repo) |
| Your papers list, tokens, credentials | `~/.config/overleaf-skill/` (never in git) |
| Per-paper notes | `<paper>/.overleaf-skill/` (git-ignored, never pushed to Overleaf) |
| Per-paper notes index, paper card, your chats, ChatGPT suggestions, peer reviews, activity log, references waiting for Mendeley | also inside `<paper>/.overleaf-skill/`; one paper's files are never read for another |
| The imported book, OpenAI key, Overleaf token | `~/.config/overleaf-skill/` (never in git; the book is copyrighted, keep it out of the repo) |

## Safety

- Secrets stay in `~/.config/overleaf-skill/`, outside this repo and outside your papers.
- The bot only obeys chat ids you whitelist.
- Before any action Claude checks the folder still points at the same Overleaf project, and refuses otherwise.
- LaTeX build files (`.aux`, `.log`, `.pdf`, ...) are never pushed to Overleaf by the sync tool.
- Peer review only runs when you ask. Mendeley is written to only when you ask Claude to add a reference or send a DOI to the bot.
- The ChatGPT pass only runs when you ask. Each passage is sent with citations, formulas and headings masked, together with the paper's short card and a few book rules; never another paper, never your keys. Asking in the chat is the consent, and "What goes out" shows the exact text; turn on "Ask me before anything is sent" in Settings to confirm every time. Requests above a size you set, and every peer review (which reads the whole paper), always ask first. Answers are checked before you see them (protected LaTeX intact, no new numbers, no dashes, balanced braces) and nothing is written until you approve it.
- QuillBot is a third party: only what you paste there leaves your computer. Your own version is checked against the same rules before it can be saved.
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
| "This app isn't signed in to Overleaf yet" | Paste your Overleaf Git token in **Settings > Overleaf** (the Docker app cannot use your Mac keychain), then press **Test**. |
| The book gives odd advice | **Settings > Advanced > Book library > Book check** shows, for each part of a paper, which pages would be used. If the wrong chapter shows up, tell Claude which part and what you expected. A scanned (image-only) PDF needs OCR first. |
| "Approve and save" is missing | The text-cleaning service is not set in Settings, so the button is **Let Claude apply it**. |
| The model was not found / the key was rejected | Use **Test** in **Settings > OpenAI**. It says which one it is, and never shows the key. |
| QuillBot or Overleaf stays blank next to the chat | The site refuses to be framed (or Safari blocked its sign-in). Use **Open beside the app**; **Settings > Advanced > Test the side panes** explains what your browser allows. |
| A reference is "waiting for Mendeley" | The bibliography already has it. Mendeley was unreachable or not signed in; the entry is retried after the next success, or press **Try again now** on the References page. |
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
