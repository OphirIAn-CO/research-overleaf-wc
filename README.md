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
4. **You decide.** Keep or set aside each change. If you want to adjust the tone, put the text in QuillBot (it opens inside the
   app, next to the chat), take the result back, and the app checks that no citation or number was lost.
5. **Save, then send.** **Save in my files** puts the changes you ticked into your files, right from the answer.
   **Save and send to Overleaf** does that and sends them in one press. If Overleaf has newer changes, the note says so and one
   button gets the latest and sends again. The Activity page tells you in sentences what happened and what it cost.

Every answer also has **Copy: the new text / for QuillBot / everything** buttons: one press copies the improved text with your
citations and formulas back in place (only the changes you ticked), the same text as tags for QuillBot, or the whole answer (the reason,
each change and the new text). Nothing needs selecting by hand.

Also in the chat: **"peer review my paper"** (it asks first, because it reads the whole paper) and **pasting a DOI**, which adds
the reference to your bibliography, to your Mendeley group and to Overleaf, and gives you the `\cite{...}` to use.

**Going through a peer review.** The review lists its comments; **Fix this** on one turns it into an ordinary suggestion. While a review with
two or more comments still has open ones, a **Reviewer comments** panel stays above the message box with all of them, so after every decision you
see what is left without scrolling back. Each comment shows where it stands, with a colour and a mark: *Working on it*, *Suggestion ready: your
decision*, *Completed - saved here, not in Overleaf yet*, *Completed - sent to Overleaf*, *Set aside* (you pressed Not now), *OpenAI would change
nothing here*, *Skipped* (you pressed Skip: this comment needs no change; **Bring it back** undoes it) or *Could not finish* (**Try again**).
**Fix the next one** takes the next open comment. When the last comment is handled the panel goes away and the chat says so ("All 7 reviewer
comments are handled ... Let's continue. What would you like to work on next?"), with a button to send what was saved but not sent yet. The
review itself stays in the conversation with the final status of each comment, folded away.

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
~/.claude/skills/research-overleaf-wc/install.sh ui      # builds the Docker image once (several minutes, about 1.5 GB: it includes LaTeX and the built-in browser)
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
The chat uses the width of your window. In an answer the explanation, questions and warnings sit on top (side by side when there is room), and the before/after comparison below them uses the full width of the card, **Now** and **Suggested** side by side; only a card narrower than about 520 pixels stacks them. The comparison's text is sized to the card, not the window, so it stays readable with a pane open beside the chat.

**The LaTeX pane: your paper's source and its PDF, on this computer (like TeXShop).** Press **LaTeX** above the chat. On the left is the
paper's source (`.tex` and `.bib`, a file menu at the top), coloured, with line numbers; on the right is the PDF made from it, drawn page
by page and sharp at any zoom. Nothing is sent anywhere to make it: the paper is compiled by `latexmk` on your computer (in the Docker
image, or with your own MacTeX/TeX Live when you run the app without Docker), and the PDF and the other files LaTeX writes stay out of
what is sent to Overleaf.
- **Edit and compile.** Type in the source. A moment after you stop, it is saved and compiled (**Auto** switches that off; **Recompile**
  or Cmd/Ctrl + Enter does it at once, Cmd/Ctrl + S saves). Errors are listed in words with their file and line; click one to go there
  (the line is marked red). Undefined citations and references, and missing packages, are listed too. The bibliography runs by itself.
- **The assistant's changes show up in the source.** When it saves a suggestion (or you get the latest from Overleaf), the file that
  changed is shown, the changed lines are highlighted for a few seconds, and the PDF is made again. A green mark in the margin shows
  every line changed here since the last send, i.e. what **Send to Overleaf** will put into Overleaf. Sending itself is unchanged.
- **Source and PDF lead to each other (SyncTeX).** **Show in PDF**, or Cmd/Ctrl + click a line, finds the line in the PDF; double-click
  the PDF to go to its source line. **Source / Both / PDF** chooses what to show; the pane stacks the two when it is narrow. **-**, **+**,
  **Fit**, the page arrows and **Download** work on the PDF.
- **Safe by design.** A file you are editing is never overwritten behind your back: if the assistant saved a suggestion to the same file
  meanwhile, you are asked whether to load the new version or keep yours. Only the paper's own text files can be opened. The compiler runs
  without shell access (`-no-shell-escape`), may not write outside the paper, and is stopped after three minutes
  (`OLS_TEX_TIMEOUT` seconds) with everything it started.
- **No LaTeX on the computer?** The source still shows and can be edited; the pane says what to install (MacTeX, or rebuild the Docker
  image with `./ui.sh build`; `OLS_WITH_LATEX=0 ./ui.sh build` leaves LaTeX out to save about 1 GB). The Overleaf pane stays available.

**QuillBot and Overleaf inside the app (the built-in browser).** Press **QuillBot**, **Overleaf** or **Paper text** above the chat and the
page opens in the side area. QuillBot and Overleaf refuse to be shown inside another page, so the app starts a real browser (Chromium) in
its own Docker container and shows you what it sees; your mouse and keyboard are passed to it, so it works like a browser tab inside the app.
You sign in once and it stays signed in (the sign-in is kept in `~/.config/overleaf-skill/browser/`; delete that folder to sign out).

- **Sign in:** type your email and password in the page as usual, or save them once in **Settings > Sign-in for QuillBot and Overleaf**
  and press **Fill my sign-in** in the pane. The app types them into the form, checks the page kept them (and tries two other ways if
  it did not), and tells you what happened; you press the site's own Sign in button, so a code or a "not a robot" check stays with you.
  Saved details are in `~/.config/overleaf-skill/sites.json` (`{"quillbot": {"email": "...", "password": "..."}, "overleaf": {...}}`),
  readable only by you and **not encrypted**; the app never shows a saved password again. Google, Apple or university sign-in cannot be
  filled (and Google refuses embedded browsers): for these two sites use an email and password (Overleaf: Account settings, set a
  password; QuillBot: "Forgot password" lets you set one).
- **"Verify you are human" checks.** QuillBot (Cloudflare) and Overleaf (reCAPTCHA) check visitors, and a browser that a program drives
  carries marks those checks can see. So the built-in browser comes in two kinds. **Overleaf** uses a scripted one (it passes). **QuillBot**
  uses an *ordinary* Chromium window on a virtual screen, started with a normal command line and no automation at all: the app takes a
  picture of that screen and passes your mouse and keyboard on as keyboard and mouse events, the way a remote-desktop viewer does. You will
  see the browser's own tab strip and address bar inside the pane. Its address bar works, but it can only open public websites. It is
  Linux Chromium on your own internet connection, not your Mac's Chrome, so a site can still decide not to trust it; nothing here tries to
  solve or get around a check. When a pane shows one, a note appears above it: click the checkbox in the picture, and if it keeps coming
  back press **Open QuillBot in a window instead**. That sends the site to a window of your own browser, which passes these checks, and
  remembers it (**Settings > QuillBot and Overleaf inside the app**, "Use the built-in browser for ..."; you can switch it back there).
- **Layout:** one pane uses the whole side area; **Both at once** (strip at the top of the area) stacks QuillBot above Overleaf; **Wider**
  gives the area more room; drag its left edge to resize. The page is drawn at least 1100 pixels wide and shrunk to fit, so the sites keep
  their desktop layout; **Aa** in the pane draws it narrower for bigger text. Back, forward, reload, stop and home are in the pane's toolbar.
- **Copy and paste:** Cmd/Ctrl+V pastes your clipboard into the page, Cmd/Ctrl+C copies the text selected there to your clipboard, and
  **Copy what I selected** does the same with a button. Tab and Esc go to the page; click outside it to leave.
- **The tone pass (QuillBot) step by step.** Under an answer, press **Copy: for QuillBot** (it copies the text with your citations and formulas as
  tags) or **Polish in QuillBot or by hand**. Either one opens a panel right above the message box, so you never scroll back up to the suggestion,
  and QuillBot opens beside the chat. The panel shows four steps and marks the one you are on: (1) **Copy the text** (**Put it in QuillBot** types
  it into QuillBot's writing box); (2) adjust the tone in QuillBot, or edit **Your final version** by hand; (3) **Bring the result back**:
  **Take QuillBot's result** brings back what QuillBot copied with its own Copy button, **Take the text I selected** brings back the text you
  selected there, **Paste what I copied** takes your clipboard; (4) the notes under the box confirm every `[[LX001]]` tag (your citations and
  formulas) is still there, then **Save and send to Overleaf** (or **Save only**). Saving closes the panel and the conversation says what was saved
  and sent. **Hide** folds the panel away without losing what you typed; **Close** drops it. **Copy my version as LaTeX** copies what is in the box
  with the citations back in place. Sending to Overleaf reloads the Overleaf pane so you see the update. (The QuillBot buttons are best effort on
  the real site; if one does not land, click in QuillBot's box and paste with Cmd/Ctrl+V.)
- **"Hand to Claude Code instead"** is only for when you work in Claude Code: it leaves the ticked changes for Claude Code to apply (after your
  text-cleaning step). You never need it to save from the app.
- **No text-cleaning service?** Saving then writes the text exactly as it was approved, and the card says so. With a service set up in Settings
  it always runs first and a failing service blocks saving.
- **Settings > QuillBot and Overleaf inside the app** shows whether the built-in browser is ready, the QuillBot address, each paper's
  Overleaf project address (normally built from its Git address), and how to show them: automatic, always the built-in browser, always a
  separate window beside the app, or inside the page as a frame (usually blank).
- **Speed, and what to do when it is slow.** The pane is a live picture of a real browser running inside Docker, so every scroll has to travel
  there, be drawn there without a graphics card, and travel back. Overleaf's editor and PDF preview are heavy pages, so how smooth it is
  depends on the CPUs Docker gives that container. What the app does about it: the browser pushes its own pictures as the page changes (instead of
  being photographed again and again, which roughly doubled the frame rate in tests), scroll notches that queue up are sent as one scroll,
  and animated scrolling is off so one notch is one picture. If it is still slow, the pane notices (a scroll or a key taking over 0.7 seconds to
  show, five times) and says so with a button, **Open Overleaf in a window beside the app**; the **↗** button in the pane header does the same any time.
  That window is your own browser: instantly smooth, already signed in, and the pane is replaced by a button that brings the window forward. You can
  switch back in **Settings > QuillBot and Overleaf inside the app**. To make the built-in one faster: give Docker more CPUs (Docker Desktop >
  Settings > Resources, 4 or more), close other heavy tabs, and keep the pane at one site at a time.
- **Safety:** the browser runs in a second container (`overleaf-browser`) on a private Docker network with nothing published to your
  network. It has no access to your OpenAI key, Overleaf token, papers or book, only its sign-in folder. It refuses to open local or
  private addresses, refuses downloads, and the app can only start it on the QuillBot and Overleaf project addresses. Chromium runs there
  without its own sandbox (a container cannot give it one), so the container itself is the sandbox: read-only, no capabilities, no new
  privileges, not root. It stops itself after hours of not being used, and `ui.sh down` stops it.
- **If it is not available** (an old image, or Docker could not start the container) the app says so in Settings and falls back to the
  old way: a frame when the site allows it, otherwise **Open QuillBot beside the app**, a window on the free half of your screen.
  After updating the app run `./ui.sh down`, then `./ui.sh up`; the first start rebuilds the image (a few minutes).

QuillBot has no public API, so the paste stays yours. **Copy for QuillBot** copies the suggestion with your citations and formulas as tags
(`[[LX001]]`) so QuillBot cannot damage them.

**What the chat remembers, and starting again.** Very little. A request goes to OpenAI on its own: the earlier messages of a chat are *not* sent. Inside a chat the
only memory is the place of your last request, so "make it shorter" means the same passage. For the paper, in every chat, two things carry over: the notes about it
(key terms, tense, notation; **Settings > Advanced > Paper index**) and a short style note built from which suggestions you kept or set aside. To start again:
**Start over** (header, or in the **?** panel) empties the current chat and keeps it in the same place; **+ New chat** (sidebar) opens a second, empty one and keeps
the old; **Delete this chat** and **Rename** are in the **?** panel; **Forget my past decisions** (also there, only when something was learned) erases the style note.
None of these touch your paper or the suggestions already saved in your files. **Which chat belongs to which paper:** each chat is a small file inside its own paper's folder (`<paper>/.overleaf-skill/chats/`, git-ignored, never sent to Overleaf), so the sidebar lists exactly the chats of the paper you selected, a chat id from another paper opens that paper's own chat and never this one, and moving or re-cloning a paper folder takes its chats along only if you copy `.overleaf-skill/` with it.

**Your files: articles, tables, notes and code.** The app does not only read your LaTeX. Press **Add a file** next to the message box (or
drop files on the chat, or use **References > Your files**) to add an article you cite (PDF or Word), a table of results (CSV or TSV),
notes (text, Markdown), JSON, a notebook, or code (Python, R, MATLAB, Julia, SQL, shell); pictures are kept but not read. Excel files: save as CSV first.
- **Saved and sent.** The file is saved in the `references/` folder of your paper, committed on its own (whatever else is waiting stays
  waiting) and sent to Overleaf, where it shows in the file list. Switch the sending off on the **Your files** card to keep files here until
  your next send. A second file with the same name never replaces the first (it becomes `name (2).pdf`); passwords and keys are refused;
  25 MB per file (Overleaf projects have a size limit too).
- **Split into parts.** Each file is cut into small labelled parts of about 350 tokens: a PDF by page and heading (the reference list is left
  out), a table into a summary (rows, columns, min/max/mean of each numeric column) plus blocks of 25 rows, code at its functions, Markdown
  and Word by heading, a notebook by cell (code and text output; pictures are skipped). **See the parts** lists them with where each came from
  ("pages 3 to 4 · Methods", "rows 26 to 50"). Scanned PDFs have no text: the app says so.
- **Code and results around the paper.** Without adding anything, the app also reads data and code files inside your paper's folder
  (tables, JSON, notebooks, scripts, notes; never `.env`, keys, hidden or build folders, or links). If your analysis lives elsewhere, list
  its folders under **Your files > Code and results in other folders**. The app runs in Docker, which sees only what you share with it, so
  share the folder first, **read-only** (the app can read the code and results, never change them):
  `./ui.sh folders add --read-only ~/code/analysis`, then `./ui.sh down` and `./ui.sh up`.
- **When a request uses them.** Never silently. By default ("when I mention them") a request uses your files when it names one
  (`rewrite the results using results.csv`), or talks about them ("based on my data", "check it against my files", the **Use my files** chip).
  Everyday words do not count: a file called `results.csv` is not named by "improve the results". The request card says how many parts go
  with it and **What goes out** lists them; the answer says which parts it used. You can set it to *every request* or *never* per paper.
  Only the few best parts go (about 1,800 tokens, fewer when a request has many passages).
- **Numbers from your files.** The check that stops a rewrite from inventing numbers accepts a number that stands in the parts that went
  with the request, and points it out in the answer ("Numbers that came from your files: 0.82. Check them against the source"). A number
  found nowhere is still refused. The model is told to copy numbers exactly, never to invent a value, and not to copy long stretches of an article.
- **Claude Code (in your terminal) does not need Docker or sharing.** It runs on your computer and can read any folder you point it at, with its own
  file tools, and through the same parts: `paper_query` with `kind: "source"` (added files) or `kind: "project"` (everything else). To make a
  folder part of the paper's index for good: `python3 scripts/writing.py settings --paper NAME --link-folder ~/code/analysis`; see what is
  readable with `writing.py files` (and `--parts FILE`). The web app and Claude Code share the same list.
- **Which one writes a new Discussion?** The web app rewrites and improves text that already exists (a Discussion with a few notes in it, a
  Results paragraph) using your files; it does not yet draft a section from nothing. Drafting a section from your results is something to ask
  Claude Code (SKILL.md, "Writing a section from the author's own results"): it reads the results, the paper and the cited references, writes
  the text into `main.tex` for you to approve, and sends it with the usual **Send to Overleaf**.

**Sending to Overleaf, and why it sometimes says "Overleaf has newer changes".** If you (or a coauthor) edited the project in Overleaf since you last
got the latest, Overleaf refuses a send. Your change is then a *saved change* that never arrived. The header counts these ("1 saved change not
in Overleaf yet"), another **Send** tries again, and **Get the latest from Overleaf, then send again** saves what is waiting, puts Overleaf's
latest underneath your changes, and sends. Changes in different places are joined automatically.

**When the same lines were changed on both sides** the app cannot know which words you want, so it stops before sending, undoes its attempt (your
files are exactly as they were) and shows **every colliding place**: what Overleaf has beside what you saved here, the words that differ marked,
and a choice for each: *Use Overleaf's*, *Use mine*, or *Keep both*. **Keep mine everywhere** / **Keep Overleaf's everywhere** choose for all at once.
Nothing happens until every place has a choice; then **Join with my choices and send** makes the merge, commits it, sends it, checks it, and reloads
the Overleaf pane. This replaces only the colliding lines and only in this copy and in Overleaf's newest version: the text you did not choose stays in
Overleaf's History (Overleaf: Menu, History) and in this computer's git history, so a wrong choice can be taken back. Pictures or files that one side
deleted are chosen as a whole file. **Ask Claude Code to join them** leaves a note and gives you one sentence to say in your Claude Code terminal
("Resolve the Overleaf merge conflict I handed you."): Claude Code runs on your computer, not inside the app, so it cannot be called from the page.
It finds the note itself (`overleaf_status` ends with it), joins the versions keeping what fits together, asks you when they contradict, sends, and
the note goes away once Overleaf has the result. After a send the app asks Overleaf what it has and says so ("Checked: Overleaf now has your
change"), reloads the project's Overleaf page in the built-in browser even when its tab is not showing, and presses Recompile there so the PDF follows.
(Overleaf's editor does not follow a send by itself: a page that was open before keeps showing the old text until it is loaded again.)

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
- LaTeX build files (`.aux`, `.log`, `.pdf`, ...) are never pushed to Overleaf by the sync tool (files you add to `references/` are not build files and are sent).
- Peer review only runs when you ask. Mendeley is written to only when you ask Claude to add a reference or send a DOI to the bot.
- The ChatGPT pass only runs when you ask. Each passage is sent with citations, formulas and headings masked, together with the paper's short card and a few book rules; never another paper, never your keys. Asking in the chat is the consent, and "What goes out" shows the exact text; turn on "Ask me before anything is sent" in Settings to confirm every time. Requests above a size you set, and every peer review (which reads the whole paper), always ask first. Answers are checked before you see them (protected LaTeX intact, no new numbers, no dashes, balanced braces, and every LaTeX command written exactly as in your paper: a model that doubles a backslash, `\\S` for `\S`, is corrected and told so, and one that adds or changes other LaTeX is refused) and nothing is written until you approve it. Parts of your own files go out only when a request uses them, and the request lists them. Linked folders cannot be system folders, hidden folders or this app's own settings folder.
- QuillBot is a third party: only what you paste or put there leaves your computer. Your own version is checked against the same rules before it can be saved.
- The built-in browser (QuillBot and Overleaf inside the app) lives in its own container with none of your keys or papers (see above). Your saved QuillBot/Overleaf email and password are in `~/.config/overleaf-skill/sites.json`, readable only by you, not encrypted.
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
| The pane says the built-in browser is not available | The image is older than the app: run `./ui.sh down`, then `./ui.sh up` (the first start rebuilds the image, a few minutes). **Settings > QuillBot and Overleaf inside the app** says why if it still fails. Meanwhile the sites open in a window beside the app. |
| The pane stays on "Starting the browser" or says the page did not answer | The site could not be reached from the container (internet, a VPN, or the site is down). Press **Reload**, or **Home**. Chromium keeps your sign-in, so nothing is lost. |
| Scrolling in the Overleaf pane lags | Press the **↗** button in the pane header (or the button in the "is slow here" note) to use a window of your own browser, which is always smooth. To keep the built-in one: Docker Desktop > Settings > Resources > more CPUs, and close other heavy tabs. |
| The page keeps asking "Verify you are human", or the sign-in is refused | Click the checkbox in the picture. If it comes back, use **Open QuillBot in a window instead** in the note above the pane (or switch the built-in browser off for that site in **Settings**); your own browser passes these checks. A Google/Apple/university sign-in does not work in an embedded browser: use an email and password. If Overleaf says the email or password is wrong, check them in Settings; the app never sees what you type in the page itself. |
| The picture is small or blurry | The page is drawn wide and shrunk to fit: press **Wider**, or **Aa** for bigger text. |
| "Fill my sign-in" finds no form | Open the site's sign-in page first. Some sites ask for the email, then the password: press the button again after **Continue**. |
| Typing does nothing | Click inside the page first (a blue outline shows it has the keyboard). |
| A reference is "waiting for Mendeley" | The bibliography already has it. Mendeley was unreachable or not signed in; the entry is retried after the next success, or press **Try again now** on the References page. |
| The LaTeX pane says LaTeX is not installed | In Docker the image needs LaTeX: run `./ui.sh down`, then `./ui.sh up` (the first start rebuilds the image with LaTeX, several minutes and about 1 GB more), or `./ui.sh build`. Without Docker, install MacTeX or TeX Live and restart the app. |
| A package is "not found" when compiling | The Docker image has the usual LaTeX packages (publisher classes, maths, figures, biblatex), not every package ever written. Add the `.sty` file to the paper folder, or ask for the package to be added to `ui/Dockerfile`. |
| The browser or LaTeX is slow, even though the app is idle | `./ui.sh up` and `./ui.sh status` say when Docker was given fewer than 4 CPUs or 6 GB of memory: Docker Desktop > Settings > Resources, raise both, Apply. On an Apple-silicon Mac Docker must run the arm64 image, not x86 under emulation (`./ui.sh up` tells you). |
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
