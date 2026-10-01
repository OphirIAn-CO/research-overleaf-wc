"""Server endpoints added for the v2 workspace (secrets forms, friendly Overleaf errors, ...). Offline, no book needed."""
import json
import stat
import unittest

from tests.helpers import common
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
