"""Checks for `common.py doctor` and the Settings page. Return plain strings; empty list = healthy."""
import shutil

from . import config, store


def env_issues():
    out = []
    if not store.fts5_available():
        out.append("SQLite FTS5 is missing in this Python (needed for search). Use python.org or Homebrew Python.")
    state, msg = config.openai_status()
    if state != "ok":
        out.append(f"ChatGPT pass not configured: {msg}")
    if not config.list_books():
        out.append("no book imported yet (writing.py book-ingest <pdf> --id <slug>)")
    if not shutil.which("docker"):
        out.append("Docker not found: the web app (./ui.sh up) needs it; everything else works without")
    if not shutil.which("pdftotext"):
        out.append("pdftotext (poppler) not found: needed once to import a book from the command line (brew install poppler); the web app has its own")
    return out


def paper_issues(name, proj):
    out = []
    d = config.paper_dir(proj)
    if not d.exists():
        return out  # never used the writing coach on this paper yet: nothing to check
    idx = d / "index.sqlite"
    if idx.exists():
        try:
            con = store.connect(idx, readonly=True)
            ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            con.close()
            if not ok:
                out.append(f"{name}: index.sqlite damaged (it rebuilds itself on next use)")
        except Exception as e:  # noqa: BLE001
            out.append(f"{name}: index.sqlite unreadable ({e}); it rebuilds itself on next use")
    if not (d / "paper_card.md").exists() and idx.exists():
        out.append(f"{name}: no paper card yet (key terms/decisions); create it from the UI or ask Claude to draft one")
    return out
