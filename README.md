# research-overleaf-wc

**A writing assistant for researchers who write in LaTeX on Overleaf.** It runs on your own computer: you pick a paper, say what you want in plain words, and it improves your text, writes new passages from
your own results, cites your own reading, keeps your bibliography in order and sends the changes to Overleaf. It follows the advice of a writing handbook that *you* provide, uses ChatGPT as the editor,
and never saves or sends anything you have not approved.

![An answer in the chat: the explanation on top, the changes side by side](docs/images/chat-answer.png)

*(The pictures in this document were taken with a made-up paper and a stand-in model.)*

## What you can do with it

- **Improve what you wrote.** *"Improve the flow of the text before Figure 1 in the methods"*, *"fix the grammar in the introduction"*, *"make the abstract shorter"*. It finds the right part of your paper, adds the
  few pages of your writing book that apply, and asks OpenAI for a better version with an explanation. Citations, formulas and numbers are swapped for tags before anything leaves your computer, and an answer that
  changes any of them is thrown away. You keep or set aside each change.
- **Write the Results and the Discussion from your analysis.** Point it to the folder with your code, tables, logs and figures. *"Write the results from my analysis"* produces paragraphs in which every number comes from your
  files and every statement says which file it rests on. ([Details](docs/guide.md#writing-new-text-from-your-sources))
- **Use your own reading.** Point it to the folder where you keep books and articles (subfolders are fine). It reads them once, on your computer, and can answer *"what do my articles say about irrigation timing?"*
  with the page, cite them in your text, and add them to your bibliography. ([Details](docs/guide.md#your-reading-the-literature-library))
- **Find references on the internet and cite them.** *"Find references about armed conflict and pipeline attacks in Colombia"* searches the web and two open scholarly indexes, shows each paper with its abstract and DOI, and adds
  the ones you tick to your bibliography; a suggestion can then be written again with them, citing them from their abstracts. ([Details](docs/guide.md#finding-references-on-the-internet-and-citing-them))
- **Write from an outline, citing your reading.** *"Write the introduction from `outlined_intro.md` sections 1-4 and my library, and cite in-text"* uses only those sections, looks up your library and the internet by the **subject**
  (the outline and abstract), never by the words about style. Every document of your library can be cited (from its `.bib` record, its DOI, Crossref or the file's own details), the library is read for each point of the outline, and where it lacks something the app searches the internet itself and cites only records that are about your subject. *"Convert my library into references"* turns the documents into bibliography entries with keys, and *"how many references are in my .bib?"* counts. The writing book is used for style rules only. ([Details](docs/guide.md#writing-from-an-outline-with-your-reading-and-your-citations))
- **It checks that it understood you.** Each request is read as a whole, in context (a pasted paragraph is material, not the request). When it is not sure it says what it understood, which text it is about and which sources it will use,
  and waits for your yes, your correction or your no; it remembers what you correct. You can also pick the kind of request yourself. ([Details](docs/guide.md#checking-that-i-understood-you))
- **Change the form of the paper.** *"Move this table to the appendix and add the reference"*, *"improve the visualization of Table 2"*, *"adjust the position of the figure and its labels"*: exact LaTeX edits, checked on your computer
  (labels and references still match, citations and the words of tables and figures unchanged, the paper still compiles), applied whole or not at all. ([Details](docs/guide.md#changing-the-form-of-the-paper-tables-figures-graphs-the-appendix))
- **Peer review.** *"Peer review my paper"* scores it against a rubric and lists the problems; **Fix this** turns a comment into an ordinary suggestion. Nothing changes by itself.
- **References.** Paste a DOI: it goes into your `.bib`, your Mendeley group and Overleaf, and you get the `\cite{...}` to use. (Also from your phone, through a Telegram bot.)
- **See your paper.** Your LaTeX source and the compiled PDF side by side, on your computer; QuillBot and Overleaf in panes next to the chat for the tone pass.
- **From Claude Code too.** The same tools are available to [Claude Code](https://claude.com/claude-code) in your terminal: sync with Overleaf, edit exact passages, manage references, search the library.

## How it works

1. **Pick a paper.** The app keeps private notes about each paper; one paper never sees another's.
2. **Say what you want**, in your own words. It works out which part of your paper you mean and finds the advice that applies. You never have to look at the book or at how it was cut up.
3. **OpenAI answers** with a better text and why, or with new text and the sources it rests on. Everything it may say is checked first: protected LaTeX intact, no number that is in none of your files, no invented citation.
4. **You decide.** Keep or set aside each change; adjust the tone in QuillBot if you like; the app checks that no citation or number was lost.
5. **Save, then send.** *Save in my files*, or *Save and send to Overleaf* in one press.

## What you need

- A Mac or Linux computer with **Docker** (Docker Desktop, or Docker Engine) for the app. Without Docker you can run it with Python 3 and git (`python3 scripts/ui/server.py`); the PDF features then need `poppler`, and the LaTeX pane needs your own TeX Live or MacTeX.
- An **OpenAI API key** (you pay OpenAI for what you ask; each request shows its size first). Any model your account can use.
- An Overleaf project with **Git integration**, which is a paid Overleaf feature, and a Git authentication token from your Overleaf account settings.
- A **writing handbook as a PDF with selectable text** that you are allowed to use (it stays on your computer and is never part of this repository). Optional but it is what makes the advice yours.
- Optional: a Mendeley account (reference manager), a Telegram bot (references from your phone), the `academic-paper` Claude skill (installed under `~/.claude/skills/`) for the peer-review rubric.

## Quick start

```bash
git clone https://github.com/OphirIAn-CO/research-overleaf-wc
cd research-overleaf-wc
./install.sh ui      # builds the Docker image once: several minutes, about 1.5 GB (it includes LaTeX and the built-in browser)
./ui.sh up           # starts the app and opens it in your browser; ./ui.sh down stops it (it also stops itself when idle)
```

Then, **in the app, under Settings** (nothing to edit by hand):

1. **OpenAI**: paste your key and the model name, press *Test*.
2. **Overleaf**: paste your Git token, press *Test*.
3. **Papers**: clone your Overleaf project (Overleaf: Menu, Git) into a folder, share that folder with the app once (`./ui.sh folders add ~/path/to/your/papers`, then `./ui.sh down && ./ui.sh up`), and press *Add* next to it.
4. **Writing book**: press *Choose the book PDF* and pick the file (a PDF with selectable text). The app copies it into its private folder and reads it; nothing to find or move by hand.
5. Optional: **Literature library** and **Code and results** (point them at your folders; in Docker share them read-only: `./ui.sh folders add --read-only ~/Documents/Literature`).

Then open the **Chat** and ask for something. The [guide](docs/guide.md) explains every part.

### Use it from Claude Code

```bash
./install.sh                                                        # creates ~/.config/overleaf-skill/ and registers the tools with Claude Code
python3 scripts/common.py add /path/to/your/paper --group "Your Mendeley group"      # --group is optional; repeat for every paper
python3 scripts/common.py doctor                                    # checks everything is healthy
```

For Claude Code to find the instructions, clone this repository into `~/.claude/skills/research-overleaf-wc` (or link it there). Then ask Claude Code to *"sync my paper"*, *"add a citation for X to the introduction"*,
*"review my draft"*, or *"what does my library say about X"*. [`SKILL.md`](SKILL.md) is the full instruction set it follows.

## Your data, and what leaves your computer

- **Nothing runs in the cloud except what you ask for.** The app listens on `127.0.0.1` only, needs a fresh access token every time you start it, refuses requests from other websites, loads nothing from the internet, runs as you
  with a read-only filesystem, sees only the folders you chose, and closes itself when idle.
- **OpenAI** gets, for a request you made: the passage (with citations, formulas and headings masked), a short card about the paper, a few pages of your book, and, only when the request uses them, the listed parts of your
  files and library and up to four shrunk pictures of your figures. The request shows what goes out ("What goes out", "Show the exact text") and you can turn on *Ask me before anything is sent*. Never another paper, never your keys.
- **Secrets** (OpenAI key, Overleaf token, saved sign-ins) are in `~/.config/overleaf-skill/`, outside this repository and your papers, readable only by you. Saved site passwords there are **not encrypted**.
- **QuillBot** is a third party: only what you paste there leaves your computer.
- **Your files, library and analysis are only read**, never changed or sent to Overleaf unless you add a file to the paper yourself.
- The activity log holds counts, ids and timings, never your text.

More in the guide: [Safety](docs/guide.md#safety), [Where things live](docs/guide.md#where-things-live).

## Working with a coauthor, or sharing one Overleaf account

Three different things get mixed up, and that is the usual reason a coauthor "cannot connect":

| | What it is | What it decides |
|---|---|---|
| **The Overleaf project** | Your paper in Overleaf | Who may edit it: the owner, anyone invited under **Share**, or anyone who has the shared login |
| **Overleaf's Git address** | `https://git.overleaf.com/<project id>`: the id is the part after `/project/` in the project's web address | **The only thing this app sends to and gets from.** Its password is an Overleaf **Git token** (starts with `olp_`) |
| **The GitHub link** | An optional Overleaf feature (Menu > Sync) that mirrors the project to a GitHub repository, tied to the GitHub account of whoever linked it | Only the *Sync* buttons inside Overleaf. It does **not** decide who may use Overleaf's Git address, and a coauthor does not have to link a GitHub account |

What to do, in order:

1. **Every computer needs its own Git token.** The "password" Git asks for when you clone from Overleaf is that token: not your Overleaf password, not a GitHub password. Make it in **Overleaf > Account settings > Git Integration**, and copy it at once (Overleaf shows it only once). It must come from an Overleaf account that can open the project: the owner, an account invited under *Share* as Editor, or the login you share. With a shared login, make one token per computer, so each can be deleted on its own. Never paste a token into a chat, an email or a screenshot.
2. **Clone from Overleaf, not from GitHub.** A folder cloned from GitHub has `github.com` as its address, so the app sends to GitHub, where an Overleaf token cannot work. **Settings > Overleaf > Test** names this case and has a **Clone it from Overleaf** button; or in Terminal: `git clone https://git@git.overleaf.com/<project id>` and paste the token at *Password*. Do not change the address of an existing GitHub folder: the two histories are unrelated. Use a fresh clone.
3. **Do not write the token inside the address** (`https://git:TOKEN@git.overleaf.com/...`). Git then ignores the token saved in Settings. The Test finds this and has a **Remove it from the address** button.
4. **Your own computer may never ask for a password.** That is only the macOS Keychain remembering your token; it is not a link between your Git account and the project.
5. **Check a token without cloning** (the token is typed hidden, and the answer is a number):
   ```bash
   read -s TOKEN      # paste the token, press Enter; nothing is shown
   curl -s -o /dev/null -w "%{http_code}\n" -u "git:$TOKEN" "https://git.overleaf.com/<project id>/info/refs?service=git-upload-pack"
   ```
   Usually: **200** the token works for this project; **401** Overleaf does not accept the token (copied wrongly, typed instead of pasted, not an Overleaf Git token, or made in another account); **403 or 404** the token is fine but that account cannot open the project (invite it under *Share* by email, as Editor); **500** Overleaf failed while looking the project up (try again later; if it repeats, ask Overleaf support). Overleaf does not document these numbers, so treat them as a guide.
   To tell *account* from *computer*: run the same test on the second computer with the **owner's** token. A 200 there means the computer is fine and the second account is the problem; the same error there means the computer or its network is (try a phone hotspot).
6. **If Overleaf's Git will not work for a coauthor,** GitHub can be the hub. They work from the GitHub copy and push with their own GitHub login (Terminal); the owner presses **Menu > Sync > Pull GitHub changes into Overleaf**, and **Push Overleaf changes to GitHub** to send changes the other way. The app itself signs in to Overleaf only, not to GitHub, so *Get latest* and *Send* do not work from a GitHub folder.

More: [the guide](docs/guide.md#working-with-a-coauthor-overleaf-github-and-the-token).

## When something goes wrong

| Problem | Fix |
|---|---|
| `ui.sh up` says Docker is not running | Start Docker Desktop, wait until it says it is running, try again. |
| "This app isn't signed in to Overleaf yet" | Paste your Git token in **Settings > Overleaf** and press *Test*. |
| The Test says the paper "was cloned from GitHub", or Git keeps asking a coauthor for a password | See [Working with a coauthor](#working-with-a-coauthor-or-sharing-one-overleaf-account). |
| The app does not list a paper | Share the folder that holds it (`./ui.sh folders add <folder>`), then `./ui.sh down && ./ui.sh up`. |
| Push rejected / "Overleaf has newer changes" | Press *Get the latest from Overleaf, then send again*; if the same lines changed on both sides the app shows each place and lets you choose. |
| The key was rejected, or the model was not found | **Settings > OpenAI > Test** says which one. |
| A folder shows "I cannot see this folder" | In Docker, share it read-only: `./ui.sh folders add --read-only <folder>`, then restart the app. |
| The book gives odd advice | **Settings > Advanced > Book library > Book check** shows which pages each part would use. A scanned (image-only) PDF needs OCR first. |

The [guide](docs/guide.md#troubleshooting) has the long list.

## Development

```bash
python3 -m unittest discover -s tests -t .     # everything, offline: a fake OpenAI and a small test PDF stand in for the real ones
python3 tests/e2e/demo_server.py --port 8799   # a demo of the app with a made-up paper, for the browser tests (tests/e2e/*.js, Playwright) and for taking the pictures in docs/
```

The code is plain Python 3 (standard library only) and vanilla JavaScript, with no build step. `scripts/writing_core/` is the engine (retrieval, checking, drafting), `scripts/ui/` the web app,
`SKILL.md` the instructions for Claude Code. The OpenAI client has been tested against a local fake of the API, not the live service: do a first real request on a throwaway paragraph and read the preview before
trusting it with more. Not affiliated with Overleaf, OpenAI, Mendeley, QuillBot or Anthropic.

## License

[MIT](LICENSE).
