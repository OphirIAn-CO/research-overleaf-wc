import subprocess
import unittest
from pathlib import Path

import doi_bot
from tests.helpers import IsolatedHome, common, git, make_paper
from writing_core import config, events, gitguard

BIB = "@article{smith2020, author={Smith, J.}, title={Water}, year={2020}}\n"


def porcelain(root):
    return subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()


class Guard(IsolatedHome):
    def paper(self):
        root = make_paper(self.tmp, "p", "x", bib=BIB)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "i")
        common.register(str(root), "p")
        return root, common.resolve("p")[1]

    def test_the_first_event_in_a_fresh_paper_cannot_leak_into_git(self):
        root, proj = self.paper()
        self.assertFalse((root / ".overleaf-skill").exists())
        events.log(config.paper_dir(proj), "error", message="first writer")
        self.assertTrue((root / ".overleaf-skill").exists())
        self.assertEqual(porcelain(root), "")
        self.assertIn(".overleaf-skill/", (root / ".git" / "info" / "exclude").read_text())

    def test_settings_and_other_first_writers_are_covered_too(self):
        root, proj = self.paper()
        config.set_paper_setting(proj, "dry_run", False)
        self.assertEqual(porcelain(root), "")
        root2 = make_paper(self.tmp, "q", "x", bib=BIB)
        git(root2, "add", "-A")
        git(root2, "commit", "-q", "-m", "i")
        config.ensure_paper_dir({"path": str(root2)})
        (root2 / ".overleaf-skill" / "memory.md").write_text("m")
        self.assertEqual(porcelain(root2), "")

    def test_a_doi_added_from_telegram_to_a_never_indexed_paper_is_safe(self):
        root, proj = self.paper()
        doi_bot.log_added(common.bib_path(proj), "10.1/x", "Key2020", "telegram", None)
        evs = events.read(config.paper_dir(proj), types={"doi_added"})
        self.assertEqual([(e["source"], e["doi"], e["key"], e["paper"]) for e in evs], [("telegram", "10.1/x", "Key2020", "p")])
        self.assertEqual(porcelain(root), "")

    def test_add_logs_who_added_it(self):
        root, proj = self.paper()
        orig = (doi_bot.fetch_bibtex, doi_bot.restyle)
        doi_bot.fetch_bibtex = lambda d: "@article{x,\n author={Lee, K.},\n title={Soil},\n year={2021}\n}"
        try:
            msg = doi_bot.add("10.5555/abc", common.bib_path(proj), source="web")
        finally:
            doi_bot.fetch_bibtex, doi_bot.restyle = orig
        self.assertIn("Added \\cite{Lee2021Soil}", msg)
        ev = events.read(config.paper_dir(proj), types={"doi_added"})[-1]
        self.assertEqual((ev["source"], ev["key"], ev["doi"]), ("web", "Lee2021Soil", "10.5555/abc"))
        self.assertIn("Lee2021Soil", (root / "references.bib").read_text())
        self.assertEqual(porcelain(root), "M references.bib")  # the bib is the only change: no state files leaked

    def test_guard_ignores_folders_that_are_not_a_papers_state_dir(self):
        d = self.tmp / "other"
        d.mkdir()
        gitguard.guard_scope(d)          # not named .overleaf-skill: nothing to do
        gitguard.guard_scope(self.tmp / "nowhere" / ".overleaf-skill")   # no repo around it: nothing to do


if __name__ == "__main__":
    unittest.main()
