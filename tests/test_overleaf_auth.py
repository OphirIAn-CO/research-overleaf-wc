"""Why Overleaf would not take the sign-in: each cause gets its own message. git is replaced by a script of answers; nothing touches the network."""
import json
import unittest

from tests.helpers import common
from tests.test_paper import paper
from tests.test_ui_server import UiBase, ui
from writing_core import overleaf_auth as oa

TOKEN = "olp_SECRETSECRETSECRET1234"
URL = "https://git.overleaf.com/6650f0a1b2c3d4e5f6a7b8c9"


def scripted(fill=("password=" + TOKEN, "username=git"), ls=(0, "", "")):
    """A git that answers `credential fill` with `fill` lines and `ls-remote` with `ls`."""
    seen = []

    def git(args, stdin=None):
        seen.append(args[0])
        if args[0] == "credential":
            return (0, "protocol=https\nhost=git.overleaf.com\n" + "\n".join(fill) + "\n", "") if fill else (128, "", "fatal: could not read Password for 'https://git.overleaf.com': terminal prompts disabled")
        return ls
    git.seen = seen
    return git


class Causes(unittest.TestCase):
    def d(self, remote=URL, token=TOKEN, git=None):
        return oa.diagnose("/nowhere", remote, token, git=git or scripted())

    def test_a_password_inside_the_address_beats_any_token_and_can_be_removed(self):
        for remote in ("https://git:olp_OLDOLDOLD@git.overleaf.com/6650f0a1b2c3d4e5f6a7b8c9", "https://me%40mail.com:hunter2@git.overleaf.com/6650f0a1b2c3d4e5f6a7b8c9"):
            r = self.d(remote)
            self.assertEqual((r["ok"], r["kind"], r["fix"]), (False, "url_password", "clean_address"), remote)
            self.assertIn("never looks at the token saved in Settings", r["message"])
            self.assertNotIn("OLDOLDOLD", json.dumps(r))
            self.assertNotIn("hunter2", json.dumps(r))

    def test_a_user_name_alone_is_fine(self):
        self.assertTrue(self.d("https://git@git.overleaf.com/6650f0a1b2c3d4e5f6a7b8c9")["ok"])

    def test_cleaning_the_address_keeps_the_project_and_nothing_else(self):
        self.assertEqual(oa.clean_address("https://git:tok@git.overleaf.com/abc123"), "https://git.overleaf.com/abc123")
        self.assertEqual(oa.clean_address("https://git@git.overleaf.com/abc123"), "https://git.overleaf.com/abc123")
        self.assertIsNone(oa.clean_address("https://git.overleaf.com/abc123"))
        self.assertIsNone(oa.clean_address("https://user:pw@example.com/abc123"))
        self.assertIsNone(oa.clean_address("git@git.overleaf.com:abc123"))

    def test_other_addresses_are_named(self):
        self.assertEqual(self.d("git@github.com:me/x.git")["kind"], "not_https")
        r = self.d("https://git.overleaf.example.edu/abc123")
        self.assertEqual(r["kind"], "other_host")
        self.assertIn("git.overleaf.example.edu", r["message"])
        self.assertEqual(self.d("")["kind"], "no_remote")

    PID = "6ac15fc3b1b29fb3a1b2c3d4"

    def test_a_paper_cloned_from_github_is_named_as_such_and_given_the_real_overleaf_address(self):
        r = oa.diagnose("/x", "https://github.com/Someone/RiskPerception_MIND.git", TOKEN, overleaf_id=self.PID)
        self.assertEqual((r["ok"], r["kind"], r["fix"]), (False, "other_host", "clone_overleaf"))
        self.assertIn("cloned from GitHub", r["message"])
        joined = " ".join(r["steps"])
        self.assertIn(f"https://git.overleaf.com/{self.PID}", joined)
        self.assertNotIn("RiskPerception_MIND", joined, "the project's name in GitHub is not an Overleaf project id")
        self.assertIn("Pull GitHub changes into Overleaf", joined)
        self.assertNotIn("university", joined)

    def test_without_the_projects_web_address_it_says_where_to_find_the_id_and_offers_no_button(self):
        r = oa.diagnose("/x", "https://github.com/Someone/RiskPerception_MIND.git", TOKEN)
        self.assertEqual((r["kind"], r["fix"]), ("other_host", None))
        self.assertIn("after /project/", " ".join(r["steps"]))

    def test_another_overleaf_server_is_not_github(self):
        r = oa.diagnose("/x", "https://git.overleaf.example.edu/abc123", TOKEN, overleaf_id=self.PID)
        self.assertNotIn("GitHub", r["message"])
        self.assertIn("university", " ".join(r["steps"]))

    def test_the_project_id_from_an_address_or_on_its_own(self):
        from writing_core import panes
        for value in (f"https://www.overleaf.com/project/{self.PID}", f"https://www.overleaf.com/project/{self.PID}/", self.PID, self.PID.upper()):
            self.assertEqual(panes.overleaf_id(value), self.PID, value)
        for value in ("", "https://github.com/a/b", "https://www.overleaf.com/read/abc", "not an id", "http://www.overleaf.com/project/" + self.PID + "x"):
            self.assertIsNone(panes.overleaf_id(value), value)

    def test_no_token_saved(self):
        self.assertEqual(self.d(token="")["kind"], "no_token")

    def test_git_with_no_sign_in_to_offer_means_the_token_was_never_sent(self):
        r = self.d(git=scripted(fill=None))
        self.assertEqual(r["kind"], "no_credentials")
        self.assertIn("never sent", r["message"])
        self.assertTrue(any("ui.sh down" in s for s in r["steps"]))

    def test_another_saved_login_that_wins_is_named(self):
        r = self.d(git=scripted(fill=("password=some-older-password", "username=me")))
        self.assertEqual(r["kind"], "other_login")
        self.assertNotIn("some-older-password", json.dumps(r))

    def test_accepted(self):
        git = scripted()
        r = self.d(git=git)
        self.assertEqual((r["ok"], r["kind"]), (True, "ok"))
        self.assertEqual(git.seen, ["credential", "ls-remote"])

    def test_a_token_that_is_refused_says_what_to_check_and_how_to_tell_token_from_app(self):
        r = self.d(git=scripted(ls=(128, "", "fatal: Authentication failed for 'https://git.overleaf.com/6650f0a1b2c3d4e5f6a7b8c9/'")))
        self.assertEqual((r["ok"], r["kind"]), (False, "rejected"))
        self.assertEqual(r["message"], "Overleaf received the token and refused it.")
        joined = " ".join(r["steps"])
        self.assertIn("A token belongs to one account", joined)
        self.assertIn("shows it only once", joined)
        self.assertIn("git ls-remote https://git.overleaf.com/6650f0a1b2c3d4e5f6a7b8c9", joined)
        self.assertNotIn("does not begin with olp_", joined)
        self.assertNotIn(TOKEN, json.dumps(r))

    def test_a_token_that_is_not_an_overleaf_git_token_is_pointed_out(self):
        bad = "sk-not-an-overleaf-token-1234"
        r = oa.diagnose("/x", URL, bad, git=scripted(fill=("password=" + bad,), ls=(128, "", "fatal: Authentication failed for 'x'")))
        self.assertEqual(r["kind"], "rejected")
        self.assertIn("does not begin with olp_", r["steps"][0])
        self.assertFalse(r["facts"]["token_looks_right"])

    def test_the_account_is_fine_but_the_project_is_not_reachable(self):
        r = self.d(git=scripted(ls=(128, "", "fatal: unable to access 'https://git.overleaf.com/x/': The requested URL returned error: 403")))
        self.assertEqual(r["kind"], "forbidden")
        self.assertIn("did not let it into this project", r["message"])
        r = self.d(git=scripted(ls=(128, "", "fatal: repository 'https://git.overleaf.com/x/' not found")))
        self.assertEqual(r["kind"], "not_found")

    def test_no_connection_is_not_blamed_on_the_token(self):
        r = self.d(git=scripted(ls=(128, "", "fatal: unable to access 'https://git.overleaf.com/x/': Could not resolve host: git.overleaf.com")))
        self.assertEqual(r["kind"], "unreachable")
        self.assertIn("Could not resolve host", r["message"])

    def test_gits_words(self):
        for text, kind in (("fatal: Authentication failed for 'https://x'", "rejected"), ("remote: Invalid username or password.", "rejected"),
                           ("fatal: could not read Username for 'https://git.overleaf.com': terminal prompts disabled", "no_credentials"),
                           ("error: RPC failed; HTTP 403 curl 22 The requested URL returned error: 403", "forbidden"), ("fatal: something odd", "unknown")):
            self.assertEqual(oa.classify(text), kind, text)


class InTheApp(UiBase):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)
        self.c.login()
        self._git = oa._git
        self.addCleanup(setattr, oa, "_git", self._git)

    def script(self, **kw):
        g = scripted(**kw)
        oa._git = lambda path, *args, stdin=None, timeout=40: g(list(args), stdin)

    def test_the_test_button_names_the_cause_and_never_returns_the_token(self):
        common.save("overleaf_git.json", {"token": TOKEN})
        self.script(ls=(128, "", "fatal: Authentication failed for 'https://git.overleaf.com/x/'"))
        st, r = self.c.post("/api/test/git", {"paper": "alpha"})
        self.assertEqual((st, r["ok"], r["kind"]), (200, False, "rejected"))
        self.assertTrue(r["steps"])
        self.assertNotIn(TOKEN, json.dumps(r))
        self.script()
        st, r = self.c.post("/api/test/git", {"paper": "alpha"})
        self.assertEqual((r["ok"], r["message"]), (True, "Overleaf accepted the sign-in for this paper."))

    def test_a_password_in_the_address_is_found_and_removed_without_breaking_the_paper(self):
        common.save("overleaf_git.json", {"token": TOKEN})
        path = self.proj["path"]
        dirty = "https://git:olp_OLDOLDOLD@git.overleaf.com/alpha00000000"
        import subprocess
        subprocess.run(["git", "-C", path, "remote", "set-url", "origin", dirty], check=True)
        reg = common.registry()
        reg["projects"]["alpha"]["remote"] = dirty
        common.save("projects.json", reg)
        st, r = self.c.post("/api/test/git", {"paper": "alpha"})
        self.assertEqual((r["kind"], r["fix"]), ("url_password", "clean_address"))
        self.assertNotIn("OLDOLDOLD", json.dumps(r))
        st, r = self.c.post("/api/papers/clean-address", {"paper": "alpha"})
        self.assertEqual((st, r["address"]), (200, "https://git.overleaf.com/alpha00000000"))
        self.assertNotIn("OLDOLDOLD", json.dumps(r))
        self.assertEqual(common.remote_url(path), "https://git.overleaf.com/alpha00000000")
        name, proj = common.resolve("alpha")
        self.assertIsNone(common.verify(name, proj), "the registry follows the new address, so Pull and Send still find the paper")
        self.assertNotIn("OLDOLDOLD", (common.HOME / "projects.json").read_text())
        st, r = self.c.post("/api/papers/clean-address", {"paper": "alpha"})
        self.assertEqual(st, 409)

    def github_paper(self):
        """alpha now looks like a clone made from GitHub, with the Overleaf project's web address saved for it."""
        import subprocess
        from writing_core import config
        subprocess.run(["git", "-C", self.proj["path"], "remote", "set-url", "origin", "https://github.com/Someone/RiskPerception_MIND.git"], check=True)
        reg = common.registry()
        reg["projects"]["alpha"]["remote"] = "https://github.com/Someone/RiskPerception_MIND.git"
        reg["projects"]["alpha"]["mendeley_group"] = {"id": "g1", "name": "PhD Paper 1"}
        common.save("projects.json", reg)
        config.set_paper_setting(self.proj, "overleaf_url", f"https://www.overleaf.com/project/{Causes.PID}")

    def overleaf_remote(self, base, files=None):
        """A bare repository standing in for the Overleaf project, found where the app looks for git.overleaf.com."""
        import subprocess
        import tempfile
        from pathlib import Path
        base = Path(base)
        bare = base / Causes.PID
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        work = Path(tempfile.mkdtemp(dir=base.parent))
        subprocess.run(["git", "clone", "-q", str(bare), str(work / "w")], check=True, capture_output=True)
        w = work / "w"
        (w / "main.tex").write_text("\\documentclass{article}\\begin{document}Hello\\end{document}\n")
        (w / "references.bib").write_text("@article{a, title={T}}\n")
        for cmd in (["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "init"], ["push", "-q", "origin", "HEAD:master"]):
            subprocess.run(["git", "-C", str(w), *cmd], check=True, capture_output=True)
        return bare

    def share_folder(self):
        from pathlib import Path
        root = Path(self.tmp) / "clones"
        root.mkdir()
        common.save("ui.json", {"workspace_roots": [str(root)]})
        return root

    def test_the_test_names_github_and_offers_to_clone_the_project_from_overleaf(self):
        self.github_paper()
        common.save("overleaf_git.json", {"token": TOKEN})
        st, r = self.c.post("/api/test/git", {"paper": "alpha"})
        self.assertEqual((r["kind"], r["fix"]), ("other_host", "clone_overleaf"))
        self.assertIn(f"https://git.overleaf.com/{Causes.PID}", " ".join(r["steps"]))

    def test_cloning_from_overleaf_makes_a_new_paper_that_keeps_the_mendeley_group(self):
        from pathlib import Path
        self.github_paper()
        common.save("overleaf_git.json", {"token": TOKEN})
        root = self.share_folder()
        base = Path(self.tmp) / "overleaf-server"
        base.mkdir()
        self.overleaf_remote(base)
        old = oa.GIT_BASE
        oa.GIT_BASE = str(base) + "/"
        self.addCleanup(setattr, oa, "GIT_BASE", old)
        st, r = self.c.post("/api/papers/clone", {"paper": "alpha"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["name"], "alpha-overleaf")
        done = self.wait_job(r["job"])
        self.assertEqual(done["status"], "done", done)
        self.assertEqual(done["result"], {"name": "alpha-overleaf"})
        self.assertTrue((root / "alpha-overleaf" / "main.tex").exists())
        reg = common.registry()["projects"]
        self.assertEqual(Path(reg["alpha-overleaf"]["path"]), (root / "alpha-overleaf").resolve())
        self.assertEqual(reg["alpha-overleaf"]["mendeley_group"], {"id": "g1", "name": "PhD Paper 1"})
        self.assertEqual(reg["alpha-overleaf"]["bib"], "references.bib")
        self.assertIn("alpha", reg, "the GitHub copy stays as it is")
        st, again = self.c.post("/api/papers/clone", {"paper": "alpha"})
        self.assertEqual(again["name"], "alpha-overleaf-2", "a second copy gets a new name instead of overwriting")

    def test_a_failed_clone_says_why_and_leaves_no_folder(self):
        self.github_paper()
        common.save("overleaf_git.json", {"token": TOKEN})
        root = self.share_folder()
        old = oa.GIT_BASE
        oa.GIT_BASE = str(self.tmp) + "/nothing-here/"
        self.addCleanup(setattr, oa, "GIT_BASE", old)
        st, r = self.c.post("/api/papers/clone", {"paper": "alpha"})
        done = self.wait_job(r["job"])
        self.assertEqual(done["status"], "error")
        self.assertIn("does not know a project", done["error"])
        self.assertEqual(list(root.iterdir()), [])

    def test_the_clone_needs_the_address_a_shared_folder_and_the_token(self):
        self.github_paper()
        st, r = self.c.post("/api/papers/clone", {"paper": "alpha"})
        self.assertEqual(st, 409)
        self.assertIn("ui.sh folders add", r["error"])
        self.share_folder()
        st, r = self.c.post("/api/papers/clone", {"paper": "alpha"})
        self.assertEqual(st, 409)
        self.assertIn("token", r["error"])
        common.save("overleaf_git.json", {"token": TOKEN})
        from writing_core import config
        config.set_paper_setting(self.proj, "overleaf_url", "")
        st, r = self.c.post("/api/papers/clone", {"paper": "alpha"})
        self.assertEqual(st, 400)
        self.assertIn("web address", r["error"])

    def test_a_refused_pull_says_why_and_points_to_the_test(self):
        common.save("overleaf_git.json", {"token": TOKEN})
        self.script(ls=(128, "", "fatal: Authentication failed for 'x'"))
        old = ui.overleaf_mcp.tool_pull
        ui.overleaf_mcp.tool_pull = lambda args: "pull failed: fatal: Authentication failed for 'https://git.overleaf.com/alpha00000000/'"
        self.addCleanup(setattr, ui.overleaf_mcp, "tool_pull", old)
        st, r = self.c.post("/api/papers/pull", {"name": "alpha"})
        self.assertEqual(r["hint"], "token")
        self.assertIn("Overleaf received the token and refused it.", r["friendly"])
        self.assertIn("press Test", r["friendly"])


if __name__ == "__main__":
    unittest.main()
