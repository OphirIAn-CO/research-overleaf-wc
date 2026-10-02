"""Sending to Overleaf with real git: a send that Overleaf refused must not be forgotten, joining the two sides must not lose either, and the open Overleaf page must follow.

"Overleaf" here is a bare repository on disk; "the Overleaf editor" is a second clone that pushes edits to it.
"""
import subprocess
import time
import unittest
from pathlib import Path

import overleaf_mcp
from tests.helpers import common, git, make_paper
from tests.test_book import needs_pdf
from tests.test_browser_api import FakeBrowser
from tests.test_revise import reply
from tests.test_ui_server import UiBase, ui
from tests.test_v2_chat_api import ChatBase
from tests.test_webview import wait_for

TEX = ("\\documentclass{article}\n\\begin{document}\n"
       "First line of the paper.\n"
       "Second line of the paper.\n"
       "Third line of the paper.\n"
       "\\end{document}\n")
PID = "6aa33c64db9c6e1c1dde1aa0"


def out(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True).stdout.strip()


class Overleaf:
    """A paper whose origin is a bare repository, and a way to edit it 'in the Overleaf editor'."""

    def make(self, name="alpha", tex=TEX):
        self.bare = self.tmp / f"{name}-overleaf.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        self.paper = make_paper(self.tmp / "papers", name, tex, remote=str(self.bare))
        git(self.paper, "add", "-A")
        git(self.paper, "commit", "-q", "-m", "init")
        git(self.paper, "push", "-q", "-u", "origin", "HEAD")
        common.register(str(self.paper), name)
        return self.paper

    def in_overleaf(self, old, new, message="edit in the Overleaf editor"):
        other = self.tmp / f"editor-{time.time_ns()}"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True)
        git(other, "config", "user.email", "o@example.com")
        git(other, "config", "user.name", "o")
        (other / "main.tex").write_text((other / "main.tex").read_text().replace(old, new))
        git(other, "commit", "-q", "-am", message)
        git(other, "push", "-q", "origin", "HEAD")

    def overleaf_text(self):
        return out(self.bare, "show", "HEAD:main.tex")

    def here(self, old, new):
        f = self.paper / "main.tex"
        f.write_text(f.read_text().replace(old, new))


class Sending(Overleaf, UiBase):
    def setUp(self):
        super().setUp()
        self.make()
        self.c.login()

    def sync(self):
        st, r = self.c.post("/api/papers/sync", {"name": "alpha", "message": "Writing assistant: update"})
        self.assertEqual(st, 200, r)
        return r

    def catch_up(self):
        st, r = self.c.post("/api/papers/catchup", {"name": "alpha", "message": "Writing assistant: update"})
        self.assertEqual(st, 200, r)
        return r

    def overview(self):
        return self.c.get("/api/overview?paper=alpha")[1]

    def test_a_normal_send_reaches_overleaf_and_is_checked(self):
        self.here("Second line", "Second, better line")
        r = self.sync()
        self.assertEqual((r["level"], r["sent"], r["verified"]), ("ok", True, True))
        self.assertIn("Checked", r["summary"])
        self.assertIn("Second, better line", self.overleaf_text())
        self.assertEqual(self.sync()["summary"], "Nothing to send: everything is already in Overleaf.")

    def test_a_send_overleaf_refused_is_not_forgotten(self):
        self.in_overleaf("First line", "First line, edited in Overleaf")
        self.here("Third line", "Third, better line")
        r = self.sync()
        self.assertEqual((r["level"], r["needs"]), ("warn", "latest"))
        self.assertNotIn("sent", r)
        self.assertNotIn("Third, better line", self.overleaf_text())
        # the change is a saved commit now, so the file looks clean; the app must still know it never arrived
        again = self.sync()
        self.assertEqual((again["level"], again["needs"]), ("warn", "latest"), again)
        self.assertNotIn("Nothing to send", again["summary"])
        ov = self.overview()
        self.assertEqual(ov["unsent"], 1)
        self.assertIn("1 saved change not in Overleaf yet", ov["sentence"])

    def test_the_catch_up_joins_both_sides_and_sends(self):
        self.in_overleaf("First line", "First line, edited in Overleaf")
        self.here("Third line", "Third, better line")
        self.sync()
        r = self.catch_up()
        self.assertEqual(r["pull"]["level"], "ok")
        self.assertIn("Nothing was overwritten", r["pull"]["summary"])
        self.assertEqual((r["push"]["level"], r["push"]["sent"], r["push"]["verified"]), ("ok", True, True), r)
        text = self.overleaf_text()
        self.assertIn("First line, edited in Overleaf", text)
        self.assertIn("Third, better line", text)
        self.assertEqual(self.overview()["unsent"], 0)
        self.assertEqual(out(self.paper, "status", "--short"), "")

    def test_the_catch_up_also_works_when_the_change_was_never_committed(self):
        self.in_overleaf("First line", "First line, edited in Overleaf")
        self.here("Third line", "Third, better line")                 # saved in the file only
        r = self.catch_up()
        self.assertEqual((r["pull"]["level"], r["push"]["level"]), ("ok", "ok"), r)
        text = self.overleaf_text()
        self.assertIn("First line, edited in Overleaf", text)
        self.assertIn("Third, better line", text)

    def test_the_catch_up_works_when_both_sides_changed_the_same_file_and_the_pull_alone_refuses(self):
        self.in_overleaf("Second line", "Second line, edited in Overleaf")
        self.here("Second line", "Second line, saved here")
        st, plain = self.c.post("/api/papers/pull", {"name": "alpha"})
        self.assertEqual(plain["level"], "warn", "a plain pull never overwrites the unsaved change")
        r = self.catch_up()
        self.assertEqual(r["pull"]["needs"], "claude", "the same line changed on both sides is not joined silently")

    def test_when_the_same_lines_changed_nothing_is_lost_or_left_half_done(self):
        self.in_overleaf("Second line of the paper.", "Second line, as Overleaf has it.")
        self.here("Second line of the paper.", "Second line, as written here.")
        self.sync()
        before = out(self.paper, "rev-parse", "HEAD")
        r = self.catch_up()
        self.assertEqual((r["pull"]["level"], r["pull"]["needs"], r["push"]), ("warn", "claude", None))
        self.assertIn("Nothing was overwritten", r["pull"]["summary"])
        self.assertEqual(out(self.paper, "rev-parse", "HEAD"), before, "the saved change is exactly where it was")
        self.assertIn("as written here", (self.paper / "main.tex").read_text())
        self.assertEqual(out(self.paper, "status", "--short"), "", "no conflict markers, no unfinished rebase")
        self.assertFalse((self.paper / ".git" / "rebase-merge").exists() or (self.paper / ".git" / "rebase-apply").exists())
        self.assertNotIn("as written here", self.overleaf_text())
        self.assertIn("as Overleaf has it", self.overleaf_text())
        self.assertEqual(self.overview()["unsent"], 1, "and the header still says it was not sent")

    def test_a_plain_pull_when_both_sides_changed_offers_the_catch_up(self):
        self.in_overleaf("First line", "First line, edited in Overleaf")
        self.here("Third line", "Third, better line")
        self.sync()
        st, r = self.c.post("/api/papers/pull", {"name": "alpha"})
        self.assertEqual((r["level"], r["needs"]), ("warn", "latest"))
        self.assertIn("Third, better line", (self.paper / "main.tex").read_text(), "a plain pull changes nothing")

    def test_saved_changes_that_never_left_are_sent_even_when_the_files_look_clean(self):
        self.here("Third line", "Third, better line")
        git(self.paper, "commit", "-q", "-am", "saved but not sent")
        self.assertEqual(overleaf_mcp.unsent_commits(str(self.paper)), 1)
        res = overleaf_mcp.tool_sync({"repo_path": "alpha", "message": "x"})
        self.assertIn("push: ok", res)
        self.assertIn("Third, better line", self.overleaf_text())
        self.assertEqual(overleaf_mcp.tool_sync({"repo_path": "alpha"}), "nothing to sync")

    def test_saving_without_sending_is_not_an_error(self):
        self.here("Third line", "Third, better line")
        res = overleaf_mcp.tool_sync({"repo_path": "alpha", "message": "x", "push": False})
        self.assertTrue(res.startswith("committed"))
        self.assertEqual(ui.plain_git("sync", res)["level"], "ok")
        self.assertEqual(self.overview()["unsent"], 1)


class OverleafPage(Overleaf, UiBase):
    """What happens to the Overleaf page that is open in the built-in browser."""

    def setUp(self):
        super().setUp()
        ui.REFRESH_WAIT = 0.02
        self.make()
        self.fake = FakeBrowser()
        self.srv.app._browser = self.fake
        self.c.login()
        self.url = f"https://www.overleaf.com/project/{PID}"

    def tearDown(self):
        ui.REFRESH_WAIT = 1.5
        super().tearDown()

    def send(self):
        f = self.paper / "main.tex"
        lines = f.read_text().splitlines()
        lines[4] = f"Third line, version {time.time_ns()}"
        f.write_text("\n".join(lines) + "\n")
        st, r = self.c.post("/api/papers/sync", {"name": "alpha", "message": "m"})
        self.assertEqual(st, 200, r)
        return r

    def with_project(self):
        from writing_core import panes
        self._old = panes.overleaf_project_id
        panes.overleaf_project_id = lambda proj: PID
        self.addCleanup(lambda: setattr(panes, "overleaf_project_id", self._old))

    def test_the_open_overleaf_page_is_reloaded_and_recompiled_after_a_send(self):
        self.with_project()
        self.fake.up["overleaf"] = self.url + "/something"
        r = self.send()
        self.assertEqual(r["pane"], {"reloading": True})
        self.assertTrue(wait_for(lambda: ("recompile", "overleaf") in self.fake.calls))
        self.assertIn(("nav", "overleaf", "reload"), self.fake.calls)
        self.assertLess(self.fake.calls.index(("nav", "overleaf", "reload")), self.fake.calls.index(("recompile", "overleaf")))

    def test_it_keeps_looking_for_the_recompile_button_while_the_editor_loads(self):
        self.with_project()
        self.fake.up["overleaf"] = self.url
        self.fake.recompile_after = 3
        self.send()
        self.assertTrue(wait_for(lambda: self.fake.calls.count(("recompile", "overleaf")) == 4))
        time.sleep(0.2)
        self.assertEqual(self.fake.calls.count(("recompile", "overleaf")), 4, "and stops once it was pressed")

    def test_nothing_is_touched_when_the_page_is_not_open_or_is_another_project(self):
        self.with_project()
        self.assertEqual(self.send()["pane"], {"reloading": False})
        self.fake.up["overleaf"] = "https://www.overleaf.com/project/0123456789abcdef01234567"
        self.assertEqual(self.send()["pane"], {"reloading": False})
        self.fake.up["overleaf"] = "https://www.overleaf.com/project"
        self.assertEqual(self.send()["pane"], {"reloading": False})
        time.sleep(0.2)
        self.assertEqual([c for c in self.fake.calls if c[0] in ("nav", "recompile")], [])

    def test_a_site_the_author_opens_in_a_window_is_left_alone(self):
        self.with_project()
        common.save("ui.json", {"builtin_overleaf": False})
        self.fake.up["overleaf"] = self.url
        self.assertEqual(self.send()["pane"], {"reloading": False})

    def test_a_send_that_did_not_arrive_does_not_reload_anything(self):
        self.with_project()
        self.fake.up["overleaf"] = self.url
        self.in_overleaf("First line", "First line, edited in Overleaf")
        r = self.send()
        self.assertEqual(r["level"], "warn")
        self.assertNotIn("pane", r)
        time.sleep(0.2)
        self.assertEqual([c for c in self.fake.calls if c[0] in ("nav", "recompile")], [])

    def test_the_catch_up_reloads_it_too(self):
        self.with_project()
        self.fake.up["overleaf"] = self.url
        self.in_overleaf("First line", "First line, edited in Overleaf")
        self.send()
        st, r = self.c.post("/api/papers/catchup", {"name": "alpha", "message": "m"})
        self.assertEqual(r["push"]["pane"], {"reloading": True}, r)
        self.assertTrue(wait_for(lambda: ("recompile", "overleaf") in self.fake.calls))


@needs_pdf
class ApprovingWhileOverleafMovedOn(Overleaf, ChatBase):
    """The whole path from a suggestion in the chat: 'Save and send' when Overleaf has newer edits, then the catch-up."""

    def setUp(self):
        super().setUp()
        self.paper = Path(self.proj["path"])
        self.bare = self.tmp / "ol.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        git(self.paper, "remote", "set-url", "origin", str(self.bare))
        git(self.paper, "push", "-q", "-u", "origin", "HEAD")
        common.register(str(self.paper), "alpha")

    def test_saved_then_refused_then_caught_up(self):
        self.in_overleaf("After the map we describe", "After the map, edited in Overleaf, we describe")
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."))] * 5
        r = self.send()
        self.wait_job(r["job"])
        pid = self.last("result")["data"]["proposal"]
        st, res = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": pid, "accept": "all", "push": True, "unclean_ok": True})
        self.assertEqual(st, 200, res)
        self.assertEqual((res["push"]["level"], res["push"]["needs"]), ("warn", "latest"))
        self.assertNotIn("its own climate", self.overleaf_text())
        st, r2 = self.c.post("/api/papers/catchup", {"name": "alpha", "message": "m"})
        self.assertEqual((r2["pull"]["level"], r2["push"]["level"]), ("ok", "ok"), r2)
        text = self.overleaf_text()
        self.assertIn("its own climate and soil", text)
        self.assertIn("edited in Overleaf", text)


if __name__ == "__main__":
    unittest.main()
