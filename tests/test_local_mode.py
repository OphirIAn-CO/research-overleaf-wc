"""Local-first mode: plain paper folders (no Overleaf, no git), local reference checks, hybrid-search fusion."""
import os

from tests.helpers import IsolatedHome, common, git
import refs
from writing_core import library


class Local(IsolatedHome):
    def paper(self, where):
        where.mkdir(parents=True)
        (where / "main.tex").write_text("% \\cite{commented}\nAs shown \\citep[p.~2]{a, b} and \\textcite{c}.\n")
        (where / "references.bib").write_text("@article{a, title={A}}\n@book{b, title={B}}\n@misc{unused, title={U}}\n@string{j = {J}}\n")
        return where

    def test_a_plain_folder_is_a_paper_and_cwd_finds_it(self):
        p = self.paper(self.tmp / "thesis")
        name, proj = common.resolve(str(p))
        self.assertEqual(proj["path"], str(p.resolve()))
        old = os.getcwd()
        try:
            os.chdir(p)
            self.assertEqual(common.resolve()[0], name)
        finally:
            os.chdir(old)

    def test_a_registered_folder_inside_a_bigger_repo_is_not_swallowed_by_it(self):
        repo = self.tmp / "project"
        p = self.paper(repo / "paper")
        git(repo, "init", "-q")
        common.register(str(p))
        self.assertEqual(common.resolve(str(p))[1]["path"], str(p.resolve()))

    def test_check_finds_missing_and_unused_keys(self):
        p = self.paper(self.tmp / "t")
        r = refs.check(common.resolve(str(p))[1])
        self.assertEqual(set(r["missing"]), {"c"})
        self.assertEqual(r["unused"], ["unused"])

    def test_rank_fusion_rewards_agreement(self):
        rows = [{"id": i, "score": 0} for i in "abc"]
        self.assertEqual([r["id"] for r in library._fuse(None, rows, ["c", "a"])], ["a", "c", "b"])
