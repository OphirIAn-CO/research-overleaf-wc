# research-overleaf-wc

A Claude Code skill + MCP server for working on any number of locally cloned
**Overleaf** papers: git sync, Mendeley reference pulling/adding, a Telegram
bot that turns a DOI into a BibTeX entry (and a Mendeley entry) in one message,
and an opt-in peer-review pass.

Stdlib-only Python 3, no dependencies.

## Install

```bash
git clone <this repo> ~/.claude/skills/research-overleaf-wc
~/.claude/skills/research-overleaf-wc/install.sh      # data dir + MCP registration
python3 ~/.claude/skills/research-overleaf-wc/scripts/common.py add /path/to/overleaf/clone --group "My Mendeley group"
```

Overleaf clones come from the project menu → Git (`git clone https://git.overleaf.com/<id>`;
use a Git auth token as the password).

## One registry, one data dir

Everything is keyed by `~/.config/overleaf-skill/projects.json` (see
`examples/`). Add a paper once; the MCP tools, the CLI and the Telegram bot all
see it. Unknown clone paths passed to any MCP tool are auto-registered.

```bash
scripts/common.py list | add <path> [--name N] [--group G] [--relink] | rm <name> | default <name> | doctor
```

Secrets (Mendeley credentials/tokens, Telegram token) live only in that data
dir, never in this repo. Override the location with `OVERLEAF_SKILL_HOME`.

## Mendeley (optional)

Create an app at dev.mendeley.com (redirect `https://localhost/callback`), put
`{"client_id": "...", "client_secret": "..."}` in
`~/.config/overleaf-skill/mendeley_credentials.json`, then
`python3 scripts/mendeley_sync.py authorize`.

## Telegram DOI bot (optional)

1. @BotFather → `/newbot`, copy the token.
2. Create `~/.config/overleaf-skill/telegram.json` (see `examples/`).
3. `python3 scripts/doi_bot.py run`, message the bot, it replies with your chat
   id; put it in `allowed_chat_ids` and restart. (`./install.sh bot-service`
   keeps it running under launchd on macOS.)

Send a DOI or doi.org link: the entry is added to the active paper's bib with
a `Author2022Word` key (deduped by DOI), pushed to Overleaf, and created in
that paper's Mendeley group. The target is the paper you last worked on in
Claude Code (shared `state.json`); if it is stale the bot asks first.
`/paper` switches, `/status` shows the target. Folders are verified against
their stored Overleaf remote before anything is written.

## Self-check

`python3 scripts/doi_bot.py demo` (needs network).

Optional companions used by `SKILL.md`: the `academic-paper` skill (peer-review
rubric) and `watermarks-remover` (prose cleanup). Both are skipped if absent.
