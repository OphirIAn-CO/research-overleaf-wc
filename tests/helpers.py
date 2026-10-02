"""Shared test helpers: put scripts/ on sys.path and give each test a private data dir and fake Overleaf papers."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


def make_paper(root, name, tex, bib="@article{a, title={T}}\n", remote=None, extra=None):
    """A fake Overleaf clone: git repo with main.tex + references.bib and an overleaf-looking origin."""
    p = Path(root) / name
    p.mkdir(parents=True)
    git(p, "init", "-q")
    git(p, "config", "user.email", "t@example.com")
    git(p, "config", "user.name", "t")
    (p / "main.tex").write_text(tex)
    (p / "references.bib").write_text(bib)
    for rel, text in (extra or {}).items():
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(text)
    git(p, "remote", "add", "origin", remote or f"https://git.overleaf.com/{name}00000000")
    return p


class IsolatedHome(unittest.TestCase):
    """Redirects common.HOME to a temp dir for the duration of each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._old_home = common.HOME
        common.HOME = self.tmp / "home"
        common.HOME.mkdir()
        os.environ.pop("OVERLEAF_SKILL_HOME", None)
        self._old_browser = os.environ.get("OLS_BROWSER")
        os.environ["OLS_BROWSER"] = "off"          # no test starts a real Chromium unless it asks for one explicitly

    def tearDown(self):
        if self._old_browser is None:
            os.environ.pop("OLS_BROWSER", None)
        else:
            os.environ["OLS_BROWSER"] = self._old_browser
        common.HOME = self._old_home
        self._tmp.cleanup()


def load_ui_server():
    """scripts/ui/server.py as a module (a file path import: a top-level `ui` name would clash with the repo's ui/ folder)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ols_ui_server", SCRIPTS / "ui" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
