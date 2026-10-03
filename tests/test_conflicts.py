"""Overleaf and this computer changed the same lines: show each place, let the author choose, never lose either side, and let Claude Code take over on request."""
import os
import subprocess
import unittest
from pathlib import Path

import overleaf_mcp
from tests.helpers import git
from tests.test_sending import Overleaf, out
from tests.test_ui_server import UiBase
from writing_core import gitmerge

TEX = ("\\documentclass{article}\n\\begin{document}\n"
       "First line of the paper.\n"
       "Second line of the paper.\n"
       "Third line of the paper.\n"
       "Fourth line of the paper.\n"
       "Fifth line of the paper.\n"
       "Sixth line of the paper.\n"
       "Seventh line of the paper.\n"
       "Eighth line of the paper.\n"
       "\\end{document}\n")


def sh(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


class Collision(Overleaf, UiBase):
    """Two places collide: Overleaf and this computer both rewrote the second line and the eighth line."""

    def setUp(self):
        super().setUp()
        self.make("alpha", TEX)
        self.c.login()
        self.in_overleaf("Second line of the paper.", "Second line, as Overleaf has it.")
        self.in_overleaf("Eighth line of the paper.", "Eighth line, as Overleaf has it.")
        self.here("Second line of the paper.", "Second line, as written here.")
        self.here("Eighth line of the paper.", "Eighth line, as written here.")
        git(self.paper, "commit", "-q", "-am", "my edits")
        self.before = (out(self.paper, "rev-parse", "HEAD"), (self.paper / "main.tex").read_text())

    def untouched(self):
        self.assertEqual(out(self.paper, "rev-parse", "HEAD"), self.before[0])
        self.assertEqual((self.paper / "main.tex").read_text(), self.before[1])
        self.assertEqual(out(self.paper, "status", "--short"), "")
        self.assertFalse((self.paper / ".git" / "MERGE_HEAD").exists())

    def choose(self, a, b):
        return {"main.tex#0": a, "main.tex#1": b}

    def test_the_places_that_collide_are_found_with_both_versions_and_nothing_is_changed(self):
        c = gitmerge.conflicts(str(self.paper))
        self.assertEqual(c["state"], "conflict")
        blocks = c["files"][0]["blocks"]
        self.assertEqual([(b["id"], b["mine"].strip(), b["overleaf"].strip()) for b in blocks],
                         [("main.tex#0", "Second line, as written here.", "Second line, as Overleaf has it."),
                          ("main.tex#1", "Eighth line, as written here.", "Eighth line, as Overleaf has it.")])
        self.assertIn("First line of the paper.", blocks[0]["before"])
        self.assertIn("Third line of the paper.", blocks[0]["after"])
        self.untouched()
        self.assertEqual(gitmerge.conflicts(str(self.paper))["files"][0]["blocks"][0]["id"], "main.tex#0", "and it can be asked again")

    def test_each_place_takes_the_version_chosen_for_it(self):
        r = gitmerge.resolve(str(self.paper), self.choose("overleaf", "mine"), "Join")
        self.assertEqual(r["kept"], {"mine": 1, "overleaf": 1, "both": 0})
        text = (self.paper / "main.tex").read_text()
        self.assertIn("Second line, as Overleaf has it.", text)
        self.assertIn("Eighth line, as written here.", text)
        self.assertNotIn("<<<<<<<", text)
        self.assertEqual(len(out(self.paper, "rev-list", "--parents", "-n1", "HEAD").split()), 3, "a merge commit with both sides as parents")
        self.assertEqual(out(self.paper, "status", "--short"), "")

    def test_keep_both_puts_overleafs_lines_first_then_yours(self):
        gitmerge.resolve(str(self.paper), self.choose("both", "mine"), "Join")
        text = (self.paper / "main.tex").read_text()
        self.assertIn("Second line, as Overleaf has it.\nSecond line, as written here.\n", text)

    def test_a_place_without_a_choice_stops_everything_and_leaves_things_as_they_were(self):
        with self.assertRaises(gitmerge.MergeProblem) as cm:
            gitmerge.resolve(str(self.paper), {"main.tex#0": "mine"}, "Join")
        self.assertIn("every place", str(cm.exception))
        self.untouched()
        with self.assertRaises(gitmerge.MergeProblem):
            gitmerge.resolve(str(self.paper), self.choose("mine", "nonsense"), "Join")
        self.untouched()

    def test_it_refuses_while_changes_are_not_saved_yet(self):
        (self.paper / "main.tex").write_text(self.before[1] + "% unsaved\n")
        with self.assertRaises(gitmerge.MergeProblem) as cm:
            gitmerge.conflicts(str(self.paper))
        self.assertIn("not saved yet", str(cm.exception))
        self.assertEqual((self.paper / "main.tex").read_text(), self.before[1] + "% unsaved\n", "nothing of theirs was touched")

    def test_the_authors_own_merge_style_does_not_confuse_it(self):
        sh(self.paper, "config", "merge.conflictstyle", "diff3")
        c = gitmerge.conflicts(str(self.paper))
        self.assertEqual(len(c["files"][0]["blocks"]), 2)
        gitmerge.resolve(str(self.paper), self.choose("mine", "mine"), "Join")
        self.assertNotIn("|||||||", (self.paper / "main.tex").read_text())

    def test_the_app_shows_the_places_then_joins_sends_and_overleaf_has_the_result(self):
        st, r = self.c.post("/api/papers/catchup", {"name": "alpha", "message": "m"})
        self.assertEqual(st, 200, r)
        self.assertEqual((r["pull"]["level"], r["pull"]["needs"], r["push"]), ("warn", "choose", None))
        self.assertIn("choose which version to keep", r["pull"]["summary"])
        blocks = r["conflict"]["files"][0]["blocks"]
        self.assertEqual(len(blocks), 2)
        self.assertTrue(any(op in ("-", "+") for op, _ in blocks[0]["diff"]), "the words that differ are marked")
        self.untouched()
        st, done = self.c.post("/api/papers/resolve", {"name": "alpha", "choices": self.choose("mine", "overleaf"), "message": "m"})
        self.assertEqual(st, 200, done)
        self.assertEqual((done["push"]["level"], done["push"]["sent"], done["push"].get("verified")), ("ok", True, True), done)
        text = self.overleaf_text()
        self.assertIn("Second line, as written here.", text)
        self.assertIn("Eighth line, as Overleaf has it.", text)
        self.assertEqual(self.c.get("/api/overview?paper=alpha")[1]["unsent"], 0)

    def test_the_app_refuses_an_incomplete_choice_without_touching_anything(self):
        st, r = self.c.post("/api/papers/resolve", {"name": "alpha", "choices": {"main.tex#0": "mine"}})
        self.assertEqual(st, 409)
        self.assertIn("every place", r["error"])
        self.untouched()
        self.assertEqual(self.c.post("/api/papers/resolve", {"name": "alpha", "choices": "all mine"})[0], 400)
        self.assertEqual(self.c.post("/api/papers/resolve", {"name": "nobody", "choices": {}})[0], 404)

    def test_asking_claude_code_leaves_a_note_that_it_finds_and_that_a_send_settles(self):
        st, r = self.c.post("/api/papers/conflict/handoff", {"name": "alpha"})
        self.assertEqual(st, 200, r)
        self.assertIn("Resolve the Overleaf merge conflict", r["say"])
        self.assertEqual(r["files"], ["main.tex"])
        note = gitmerge.pending(str(self.paper))
        self.assertEqual(len(note["places"]), 2)
        status = overleaf_mcp.tool_status({"repo_path": "alpha"})
        self.assertIn("The author handed you a merge conflict with Overleaf in: main.tex", status)
        self.assertIn("Second line, as Overleaf has it.", status)
        self.untouched()
        # Claude Code does the join by hand and sends it
        sh(self.paper, "fetch", "origin")
        sh(self.paper, "merge", "-X", "theirs", "origin/" + out(self.paper, "rev-parse", "--abbrev-ref", "HEAD"))
        res = overleaf_mcp.tool_sync({"repo_path": "alpha", "message": "join"})
        self.assertIn("push: ok", res)
        self.assertIsNone(gitmerge.pending(str(self.paper)), "the note goes away once Overleaf has the result")
        self.assertNotIn("handed you", overleaf_mcp.tool_status({"repo_path": "alpha"}))


class OtherCollisions(Overleaf, UiBase):
    def setUp(self):
        super().setUp()
        self.make("alpha", TEX)
        self.c.login()

    def test_a_file_one_side_deleted_and_the_other_edited_is_a_whole_file_choice(self):
        (self.paper / "notes.txt").write_text("my notes\n")
        git(self.paper, "add", "-A"); git(self.paper, "commit", "-q", "-m", "notes"); git(self.paper, "push", "-q", "origin", "HEAD")
        other = self.tmp / "ed"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True)
        git(other, "config", "user.email", "o@e.org"); git(other, "config", "user.name", "o")
        git(other, "rm", "-q", "notes.txt"); git(other, "commit", "-q", "-m", "delete"); git(other, "push", "-q", "origin", "HEAD")
        (self.paper / "notes.txt").write_text("my notes, edited\n")
        git(self.paper, "commit", "-q", "-am", "edit notes")
        c = gitmerge.conflicts(str(self.paper))
        self.assertEqual([(w["file"], w["id"]) for w in c["whole_files"]], [("notes.txt", "notes.txt#file")])
        gitmerge.resolve(str(self.paper), {"notes.txt#file": "mine"}, "Join")
        self.assertEqual((self.paper / "notes.txt").read_text(), "my notes, edited\n")
        # and the other way round

    def test_a_picture_changed_on_both_sides_is_chosen_as_a_whole(self):
        (self.paper / "fig.png").write_bytes(b"\x89PNG\0one")
        git(self.paper, "add", "-A"); git(self.paper, "commit", "-q", "-m", "fig"); git(self.paper, "push", "-q", "origin", "HEAD")
        other = self.tmp / "ed2"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True)
        git(other, "config", "user.email", "o@e.org"); git(other, "config", "user.name", "o")
        (other / "fig.png").write_bytes(b"\x89PNG\0overleaf"); git(other, "commit", "-q", "-am", "fig2"); git(other, "push", "-q", "origin", "HEAD")
        (self.paper / "fig.png").write_bytes(b"\x89PNG\0mine")
        git(self.paper, "commit", "-q", "-am", "fig mine")
        c = gitmerge.conflicts(str(self.paper))
        self.assertEqual([w["id"] for w in c["whole_files"]], ["fig.png#file"])
        with self.assertRaises(gitmerge.MergeProblem):
            gitmerge.resolve(str(self.paper), {}, "Join")
        self.assertEqual((self.paper / "fig.png").read_bytes(), b"\x89PNG\0mine")
        gitmerge.resolve(str(self.paper), {"fig.png#file": "overleaf"}, "Join")
        self.assertEqual((self.paper / "fig.png").read_bytes(), b"\x89PNG\0overleaf")

    def test_windows_line_endings_survive_the_join(self):
        crlf = TEX.replace("\n", "\r\n")
        (self.paper / "main.tex").write_bytes(crlf.encode())
        git(self.paper, "commit", "-q", "-am", "crlf"); git(self.paper, "push", "-q", "origin", "HEAD")
        other = self.tmp / "ed3"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True)
        git(other, "config", "user.email", "o@e.org"); git(other, "config", "user.name", "o"); git(other, "config", "core.autocrlf", "false")
        (other / "main.tex").write_bytes(crlf.replace("Second line of the paper.", "Second, Overleaf.").encode())
        git(other, "commit", "-q", "-am", "o"); git(other, "push", "-q", "origin", "HEAD")
        (self.paper / "main.tex").write_bytes(crlf.replace("Second line of the paper.", "Second, mine.").encode())
        git(self.paper, "commit", "-q", "-am", "m")
        gitmerge.resolve(str(self.paper), {"main.tex#0": "mine"}, "Join")
        data = (self.paper / "main.tex").read_bytes()
        self.assertIn(b"Second, mine.\r\n", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "every line still ends the way it did")

    def test_nothing_to_join_is_not_an_error(self):
        self.assertEqual(gitmerge.conflicts(str(self.paper))["state"], "same")
        self.assertEqual(gitmerge.resolve(str(self.paper), {}, "Join")["state"], "same")


if __name__ == "__main__":
    unittest.main()
