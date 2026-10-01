"""Server endpoints added for the v2 workspace (secrets forms, friendly Overleaf errors, ...). Offline, no book needed."""
import json
import stat
import unittest
from pathlib import Path

from tests.helpers import IsolatedHome, common, make_paper
from tests.test_ui_server import UiBase, ui
from tests.test_paper import paper
from writing_core import config


class Secrets(UiBase):
    def setUp(self):
        super().setUp()
        self.c.login()

    def mode(self, name):
        return stat.S_IMODE((common.HOME / name).stat().st_mode)

    def test_openai_form_writes_a_private_file_and_never_echoes_the_key(self):
        st, r = self.c.post("/api/secrets/openai", {"api_key": "sk-test-1234567890", "model": "some-model"})
        self.assertEqual((st, r), (200, {"ok": True}))
        self.assertEqual(self.mode("openai.json"), 0o600)
        saved = json.loads((common.HOME / "openai.json").read_text())
        self.assertEqual((saved["api_key"], saved["model"]), ("sk-test-1234567890", "some-model"))
        st, state = self.c.get("/api/state")
        self.assertEqual(state["settings"]["openai"]["state"], "ok")
        self.assertNotIn("sk-test-1234567890", json.dumps(state))

    def test_changing_only_the_model_keeps_the_stored_key(self):
        self.c.post("/api/secrets/openai", {"api_key": "sk-test-1234567890", "model": "m1"})
        self.assertEqual(self.c.post("/api/secrets/openai", {"model": "m2"})[0], 200)
        saved = json.loads((common.HOME / "openai.json").read_text())
        self.assertEqual((saved["api_key"], saved["model"]), ("sk-test-1234567890", "m2"))

    def test_bad_input_is_refused_and_nothing_is_written(self):
        for body in ({"api_key": "short", "model": "m"}, {"api_key": "has a space 123456", "model": "m"},
                     {"api_key": "sk-test-1234567890", "model": ""}, {"api_key": "sk-test-1234567890", "model": "a b"}, {"model": "m"}):
            self.assertEqual(self.c.post("/api/secrets/openai", body)[0], 400, body)
        self.assertFalse((common.HOME / "openai.json").exists())

    def test_overleaf_token_form(self):
        self.assertEqual(self.c.post("/api/secrets/git", {"token": "olp_abcdefgh1234"})[0], 200)
        self.assertEqual(self.mode("overleaf_git.json"), 0o600)
        self.assertEqual(json.loads((common.HOME / "overleaf_git.json").read_text()), {"token": "olp_abcdefgh1234"})
        self.assertEqual(self.c.post("/api/secrets/git", {"token": "x y"})[0], 400)
        st, state = self.c.get("/api/state")
        self.assertTrue(state["settings"]["git_token"])
        self.assertNotIn("olp_abcdefgh1234", json.dumps(state))


class FriendlySignIn(UiBase):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)
        self.c.login()
        self._pull = ui.overleaf_mcp.tool_pull
        ui.overleaf_mcp.tool_pull = lambda args: "pull failed: fatal: could not read Password for 'https://git@git.overleaf.com': terminal prompts disabled"

    def tearDown(self):
        ui.overleaf_mcp.tool_pull = self._pull
        super().tearDown()

    def test_missing_token_is_explained_in_plain_words(self):
        st, r = self.c.post("/api/papers/pull", {"name": "alpha"})
        self.assertEqual((st, r["hint"]), (200, "token"))
        self.assertIn("not signed in", r["friendly"])
        self.assertIn("could not read Password", r["output"])

    def test_a_refused_token_says_so(self):
        common.save("overleaf_git.json", {"token": "olp_abcdefgh1234"})
        st, r = self.c.post("/api/papers/pull", {"name": "alpha"})
        self.assertIn("not accepted", r["friendly"])


if __name__ == "__main__":
    unittest.main()


class PlainGit(unittest.TestCase):
    def test_pull_sentences(self):
        pg = ui.plain_git
        self.assertEqual(pg("pull", "Already up to date.")["summary"], "You already have the latest from Overleaf.")
        r = pg("pull", "Updating 1a2b3c4..5d6e7f8\nFast-forward\n main.tex       | 4 ++--\n refs/new.bib   | 10 ++++++++++\n 2 files changed, 12 insertions(+), 2 deletions(-)")
        self.assertEqual((r["level"], r["summary"]), ("ok", "Got the latest from Overleaf: main.tex, refs/new.bib."))
        r = pg("pull", "pull failed: fatal: Not possible to fast-forward, aborting.")
        self.assertEqual((r["level"], r["needs"]), ("warn", "claude"))
        self.assertNotIn("fatal", r["summary"])
        self.assertEqual(pg("pull", "pull failed: fatal: unable to access 'https://git.overleaf.com/x/': Could not resolve host")["level"], "bad")

    def test_status_and_sync_sentences(self):
        pg = ui.plain_git
        self.assertEqual(pg("status", "clean, nothing pending")["level"], "ok")
        r = pg("status", "M main.tex\n?? figs/new.png")          # as run_git returns it: the first line's leading space is gone
        self.assertEqual(r["level"], "warn")
        self.assertIn("2 files changed here and not sent yet", r["summary"])
        self.assertIn("main.tex (edited)", r["summary"])
        self.assertIn("figs/new.png (new)", r["summary"])
        self.assertIn("already in Overleaf", pg("sync", "nothing to sync")["summary"])
        self.assertEqual(pg("sync", "committed: [master abc] x\npush: ok")["summary"], "Sent to Overleaf.")
        r = pg("sync", "committed: [master abc] x\npush: ! [rejected] master -> master (fetch first)")
        self.assertEqual((r["level"], r["needs"]), ("warn", "latest"))
        self.assertIn("Get latest from Overleaf", r["summary"])
        self.assertIn("could not be sent", pg("sync", "committed: x\npush: fatal: unable to access")["summary"])


class Overview(UiBase):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)
        self.c.login()

    def test_pending_files_and_last_asked(self):
        st, o = self.c.get("/api/overview?paper=alpha")
        self.assertEqual((o["pending_count"], o["sentence"]), (0, "Nothing waiting to be sent to Overleaf"))
        (Path(self.proj["path"]) / "main.tex").write_text("changed\n")
        (Path(self.proj["path"]) / "main.aux").write_text("build output\n")
        (Path(self.proj["path"]) / "figs").mkdir()
        (Path(self.proj["path"]) / "figs" / "new.png").write_bytes(b"png")
        st, o = self.c.get("/api/overview?paper=alpha")
        self.assertEqual(sorted(x["file"] for x in o["pending"]), ["figs/new.png", "main.tex"])     # build files and our own folder never count
        self.assertEqual(o["sentence"], "2 files changed here, not sent yet")
        self.assertIsNone(o["last_checked"])
        self.assertEqual(self.c.get("/api/overview?paper=nope")[0], 404)

    def test_settings_preferences(self):
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "confirm_before_send": True, "send_refs_to_overleaf": False})[0], 200)
        s = config.paper_settings(self.proj)
        self.assertEqual((s["confirm_before_send"], s["send_refs_to_overleaf"], s["dry_run"]), (True, False, True))
        self.assertEqual(self.c.post("/api/settings", {"ui": {"guard_passages": 3, "max_passages": 500}})[0], 200)
        self.assertEqual((config.ui_cfg()["guard_passages"], config.ui_cfg()["max_passages"]), (3, 100))   # clamped
        self.assertEqual(self.c.post("/api/settings", {"ui": {"guard_passages": "many"}})[0], 400)


class Connections(UiBase):
    def setUp(self):
        super().setUp()
        self.c.login()

    def test_states_without_secrets(self):
        common.save("openai.json", {"api_key": "sk-SECRET-VALUE-123", "model": "my-model"})
        common.save("telegram.json", {"token": "123:SECRET-BOT", "allowed_chat_ids": [1]})
        (common.HOME / "mendeley_credentials.json").write_text("{}")
        st, c = self.c.get("/api/connections")
        self.assertEqual((c["openai"]["state"], c["openai"]["model"]), ("ok", "my-model"))
        self.assertEqual((c["mendeley"]["state"], c["telegram"]["state"], c["telegram"]["allowed"]), ("needs_sign_in", "set_up", 1))
        self.assertIn("mendeley_sync.py", c["mendeley"]["command"])
        blob = json.dumps(c)
        self.assertNotIn("SECRET", blob)
        (common.HOME / "mendeley_tokens.json").write_text("{}")
        self.assertEqual(self.c.get("/api/connections")[1]["mendeley"]["state"], "connected")

    def test_testing_the_openai_key_uses_a_read_only_call(self):
        from tests.fake_openai import FakeOpenAI
        oa = FakeOpenAI("responses")
        try:
            common.save("openai.json", {"api_key": "sk-test-12345678", "model": "m", "base_url": oa.url})
            st, r = self.c.post("/api/test/openai", {})
            self.assertTrue(r["ok"], r)
            self.assertEqual([x["body"] for x in oa.requests], [None])                      # one GET, nothing was generated
            oa.models_reply = (401, {"error": {"message": "bad key sk-test-12345678"}})
            st, r = self.c.post("/api/test/openai", {})
            self.assertFalse(r["ok"])
            self.assertIn("did not accept your API key", r["message"])
            self.assertNotIn("sk-test-12345678", json.dumps(r))
            oa.models_reply = (404, {"error": {"message": "no such model"}})
            self.assertIn("model name", self.c.post("/api/test/openai", {})[1]["message"])
        finally:
            oa.close()
        (common.HOME / "openai.json").unlink()
        self.assertIn("not set up", self.c.post("/api/test/openai", {})[1]["message"])


class AddPaper(UiBase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "shared"
        make_paper(self.root, "thesis", "\\documentclass{article}\\begin{document}Hi\\end{document}\n", remote="https://git.overleaf.com/" + "ab12cd34" * 3)
        make_paper(self.root / "nested", "deep", "\\documentclass{article}\\begin{document}Hi\\end{document}\n", remote="https://git.overleaf.com/" + "ef56ab78" * 3)
        make_paper(self.tmp / "elsewhere", "other", "\\documentclass{article}\\begin{document}Hi\\end{document}\n", remote="https://git.overleaf.com/" + "99999999" * 3)
        (self.root / "notes").mkdir()
        self.c.login()

    def test_nothing_is_listed_until_a_folder_is_shared(self):
        st, d = self.c.get("/api/papers/discover")
        self.assertEqual((d["roots"], d["found"]), ([], []))
        st, r = self.c.post("/api/papers/add", {"path": str(self.root / "thesis")})
        self.assertEqual(st, 403)

    def test_shared_folders_list_clones_and_add_registers_one(self):
        common.main(["folders", "add", str(self.root)])
        st, d = self.c.get("/api/papers/discover")
        self.assertEqual(sorted(x["name"] for x in d["found"]), ["deep", "thesis"])            # two levels down; "notes" and the other folder are not clones / not shared
        st, r = self.c.post("/api/papers/add", {"path": str(self.root / "thesis"), "group": "My group"})
        self.assertEqual((st, r["name"]), (200, "thesis"))
        reg = common.registry()["projects"]["thesis"]
        self.assertEqual((reg["path"], reg["mendeley_group"]["name"]), (str((self.root / "thesis").resolve()), "My group"))
        st, d = self.c.get("/api/papers/discover")
        self.assertEqual([x["registered"] for x in d["found"] if x["name"] == "thesis"], [True])

    def test_only_overleaf_clones_inside_shared_folders_can_be_added(self):
        common.main(["folders", "add", str(self.root)])
        self.assertEqual(self.c.post("/api/papers/add", {"path": str(self.tmp / "elsewhere" / "other")})[0], 403)
        self.assertEqual(self.c.post("/api/papers/add", {"path": str(self.root / "notes")})[0], 400)
        self.assertEqual(self.c.post("/api/papers/add", {"path": str(self.root / ".." / "elsewhere" / "other")})[0], 403)      # no walking out with ..
        (self.root / "link").symlink_to(self.tmp / "elsewhere" / "other")
        self.assertEqual(self.c.post("/api/papers/add", {"path": str(self.root / "link")})[0], 403)                             # nor with a symlink

    def test_same_folder_name_in_two_places_gets_its_own_entry(self):
        make_paper(self.root / "x", "thesis", "\\documentclass{article}\\begin{document}Hi\\end{document}\n", remote="https://git.overleaf.com/" + "77777777" * 3)
        common.main(["folders", "add", str(self.root)])
        self.assertEqual(self.c.post("/api/papers/add", {"path": str(self.root / "thesis")})[1]["name"], "thesis")
        self.assertEqual(self.c.post("/api/papers/add", {"path": str(self.root / "x" / "thesis")})[1]["name"], "thesis-2")


class Folders(IsolatedHome):
    def test_folders_command_and_what_the_container_must_see(self):
        import io
        from contextlib import redirect_stdout
        a, b = self.tmp / "a", self.tmp / "b"
        a.mkdir(); b.mkdir()
        inside = make_paper(a, "p1", "x\n")
        outside = make_paper(b, "p2", "x\n")
        common.register(str(inside), "p1"); common.register(str(outside), "p2")
        buf = io.StringIO()
        with redirect_stdout(buf):
            common.main(["folders", "add", str(a)])
            common.main(["paths"])
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[1:], [str(a.resolve()), str(outside.resolve())])        # the shared folder, then only papers outside it
        buf = io.StringIO()
        with redirect_stdout(buf):
            common.main(["folders", "rm", str(a)])
            common.main(["folders"])
        self.assertIn("no folder is shared", buf.getvalue())
        with self.assertRaises(SystemExit):
            common.main(["folders", "add", str(self.tmp / "missing")])
