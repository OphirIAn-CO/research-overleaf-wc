# research-overleaf-wc

**Write your papers on Overleaf, let Claude Code do the plumbing.**

A [Claude Code](https://claude.com/claude-code) skill for researchers who keep
several LaTeX papers on Overleaf. It lets Claude sync and edit them, manage
your references, and lets you add a citation from your phone by sending a DOI
to a Telegram bot.

No dependencies beyond Python 3 and git.

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

## Where things live

| What | Where |
|---|---|
| The skill (code, docs) | `~/.claude/skills/research-overleaf-wc/` (this repo) |
| Your papers list, tokens, credentials | `~/.config/overleaf-skill/` (never in git) |
| Per-paper notes | `<paper>/.overleaf-skill/` (git-ignored, never pushed to Overleaf) |

## Safety

- Secrets stay in `~/.config/overleaf-skill/`, outside this repo and outside your papers.
- The bot only obeys chat ids you whitelist.
- Before any action Claude checks the folder still points at the same Overleaf project, and refuses otherwise.
- LaTeX build files (`.aux`, `.log`, `.pdf`, ...) are never pushed to Overleaf by the sync tool.
- Peer review only runs when you ask. Mendeley is written to only when you ask Claude to add a reference or send a DOI to the bot.

## Troubleshooting

| Problem | Fix |
|---|---|
| `refusing to act: ... origin changed` | You moved or re-cloned the paper. Run `common.py add <new path> --name <name> --relink`. |
| Push rejected | Someone edited on Overleaf. Ask Claude to pull first, or tap **Pull & push** in Telegram. |
| Bot silent | Is it running? `pgrep -fl doi_bot`. Is your chat id in `allowed_chat_ids`? |
| DOI not found | The bot only accepts DOIs that resolve at doi.org. Preprints without one need Claude's web-search route instead. |
| Anything else | `common.py doctor` lists per-paper problems. |

Self-check for the DOI code: `python3 scripts/doi_bot.py demo` (needs internet).

## More detail

`SKILL.md` is the full instruction set Claude follows (workflow, session
memory, peer-review procedure). Optional companion skills it can use:
`academic-paper` (review rubric) and `watermarks-remover` (prose cleanup).

MIT licensed.
