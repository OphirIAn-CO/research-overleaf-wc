"""Uploading, listing and removing the author's files through the web app, with real git behind it (a bare repository stands in for Overleaf)."""
import base64
import subprocess
import unittest
from pathlib import Path

from tests.helpers import common
from tests.test_book import needs_pdf
from tests.test_files import CSV
from tests.test_sending import Overleaf, out
from tests.test_ui_server import UiBase, ui
from tests.test_v2_chat_api import ASK, ChatBase
from tests.test_revise import reply
from writing_core import files, paper_index

TEX = ("\\documentclass{article}\n\\begin{document}\n\\section{Results}\nWe report the results here.\n\\end{document}\n")


def b64(b):
    return base64.b64encode(b).decode()


class Upload(Overleaf, UiBase):
    def setUp(self):
        super().setUp()
        self.make("alpha", TEX)
        self.c.login()

    def upload(self, name, data, **kw):
        return self.c.post("/api/files/upload", {"paper": "alpha", "name": name, "data": b64(data), **kw})

    def test_a_file_is_saved_read_into_parts_and_sent_alone(self):
        (self.paper / "main.tex").write_text(TEX.replace("here", "here, edited and not sent"))          # something else is waiting
        st, r = self.upload("results.csv", CSV.encode())
        self.assertEqual(st, 200, r)
        self.assertEqual((r["saved"]["rel"], r["file"]["parts"], r["file"]["upload"]), ("references/results.csv", 4, True))
        self.assertEqual((r["push"]["level"], r["push"]["sent"], r["push"]["verified"]), ("ok", True, True), r["push"])
        self.assertEqual(out(self.bare, "show", "HEAD:references/results.csv"), CSV.strip())
        self.assertNotIn("edited and not sent", out(self.bare, "show", "HEAD:main.tex"), "only the file was sent")
        self.assertIn("main.tex", out(self.paper, "status", "--short"), "and the other change is still waiting")
        self.assertEqual(out(self.paper, "log", "-1", "--format=%s"), "Add results.csv to the references")

    def test_a_pdf_is_not_dropped_as_a_build_file(self):
        from tests.test_book import FIXTURE
        if not FIXTURE.exists():
            self.skipTest("no fixture")
        st, r = self.upload("Smith 2020.pdf", FIXTURE.read_bytes())
        self.assertTrue(r["push"]["sent"], r)
        self.assertTrue(out(self.bare, "ls-tree", "-r", "--name-only", "HEAD").count("references/Smith 2020.pdf"))
        # and a plain Send later does not lose it either
        (self.paper / "references" / "second.pdf").write_bytes(FIXTURE.read_bytes())
        st, s = self.c.post("/api/papers/sync", {"name": "alpha"})
        self.assertTrue(s["sent"], s)
        self.assertIn("references/second.pdf", out(self.bare, "ls-tree", "-r", "--name-only", "HEAD"))

    def test_the_author_can_keep_it_here_and_send_later(self):
        st, r = self.upload("notes.md", b"# Notes\n\nThe survey ran in 2021.\n", send=False)
        self.assertNotIn("push", r)
        self.assertNotIn("references/notes.md", out(self.bare, "ls-tree", "-r", "--name-only", "HEAD"))
        st, s = self.c.post("/api/papers/sync", {"name": "alpha"})
        self.assertTrue(s["sent"])
        self.assertIn("references/notes.md", out(self.bare, "ls-tree", "-r", "--name-only", "HEAD"))
        st, ov = self.c.get("/api/files?paper=alpha")
        self.c.post("/api/settings", {"paper": "alpha", "send_uploads": False})
        st, r = self.upload("more.md", b"# More\n\nText here.\n")
        self.assertNotIn("push", r, "the paper's setting is respected")

    def test_when_overleaf_has_newer_edits_the_file_waits_for_the_catch_up(self):
        self.in_overleaf("here.", "here, edited in Overleaf.")
        st, r = self.upload("results.csv", CSV.encode())
        self.assertEqual((r["push"]["level"], r["push"]["needs"]), ("warn", "latest"))
        self.assertEqual(self.c.get("/api/overview?paper=alpha")[1]["unsent"], 1)
        st, c = self.c.post("/api/papers/catchup", {"name": "alpha", "message": "m"})
        self.assertEqual((c["pull"]["level"], c["push"]["level"]), ("ok", "ok"), c)
        self.assertIn("references/results.csv", out(self.bare, "ls-tree", "-r", "--name-only", "HEAD"))
        self.assertIn("edited in Overleaf", self.overleaf_text())

    def test_what_cannot_be_added_is_refused_in_plain_words(self):
        for name, data, word in (("virus.exe", b"MZ", "cannot add .exe"), ("fake.pdf", b"nope", "real .pdf"), ("../../x.txt", b"", "empty")):
            st, r = self.upload(name, data)
            self.assertEqual(st, 400, name)
            self.assertIn(word, r["error"])
        st, r = self.c.post("/api/files/upload", {"paper": "alpha", "name": "a.txt", "data": "***not base64***"})
        self.assertEqual((st, "intact" in r["error"]), (400, True))
        self.assertEqual(self.c.post("/api/files/upload", {"paper": "nobody", "name": "a.txt", "data": b64(b"x")})[0], 404)
        self.assertFalse((self.paper / "references").exists())

    def test_a_file_over_the_limit_is_refused_before_it_is_read(self):
        old = ui.UPLOAD_BODY
        ui.UPLOAD_BODY = 3000
        try:
            st, r = self.upload("big.txt", b"x" * 5000)
        finally:
            ui.UPLOAD_BODY = old
        self.assertEqual(st, 413)
        self.assertIn("MB limit", r["error"])

    def test_the_list_the_parts_and_removing(self):
        self.upload("results.csv", CSV.encode())
        (self.paper / "notes").mkdir()
        (self.paper / "notes" / "log.md").write_text("# Log\n\nWe ran the survey in 2021 across forty farms.\n")
        st, r = self.c.get("/api/files?paper=alpha")
        self.assertEqual([(f["file"], f["kind"], f["upload"]) for f in r["files"]], [("references/results.csv", "source", True), ("notes/log.md", "project", False)])
        self.assertEqual((r["use_files"], r["send_uploads"], r["folder"]), ("auto", True, "references"))
        st, parts = self.c.get("/api/files/parts?paper=alpha&file=references/results.csv")
        self.assertEqual(len(parts["parts"]), 4)
        self.assertIn("Summary of the table", parts["parts"][0]["where"])
        self.assertEqual(self.c.get("/api/files/parts?paper=alpha&file=main.tex")[0], 404)
        self.assertEqual(self.c.post("/api/files/remove", {"paper": "alpha", "file": "notes/log.md"})[0], 400, "only uploads can be removed")
        st, rm = self.c.post("/api/files/remove", {"paper": "alpha", "file": "references/results.csv"})
        self.assertEqual(st, 200, rm)
        self.assertEqual([f["file"] for f in self.c.get("/api/files?paper=alpha")[1]["files"]], ["notes/log.md"])
        st, s = self.c.post("/api/papers/sync", {"name": "alpha"})
        self.assertNotIn("references/results.csv", out(self.bare, "ls-tree", "-r", "--name-only", "HEAD"), "the removal goes to Overleaf with the next send")

    def test_linked_folders_are_saved_checked_and_read(self):
        other = self.tmp / "analysis"
        other.mkdir()
        (other / "fit.py").write_text("def fit(x):\n    return x\n")
        st, r = self.c.post("/api/files/folders", {"paper": "alpha", "folders": [str(other), str(self.tmp / "missing"), str(other)]})
        self.assertEqual(st, 200, r)
        self.assertEqual([f["ok"] for f in r["folders"]], [True, False])
        self.assertEqual([f["file"] for f in r["files"]], [str(other / "fit.py")])
        self.assertEqual(self.c.post("/api/files/folders", {"paper": "alpha", "folders": "nope"})[0], 400)
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "use_files": "sometimes"})[0], 400)
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "use_files": "never"})[0], 200)
        self.assertEqual(self.c.get("/api/files?paper=alpha")[1]["use_files"], "never")


@needs_pdf
class InTheChat(Overleaf, ChatBase):
    """An uploaded file is announced in the chat, and a request that names it shows which parts go out."""

    def setUp(self):
        super().setUp()
        self.paper = Path(self.proj["path"])
        self.bare = self.tmp / "ol.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        subprocess.run(["git", "-C", str(self.paper), "remote", "set-url", "origin", str(self.bare)], check=True)
        subprocess.run(["git", "-C", str(self.paper), "push", "-q", "-u", "origin", "HEAD"], check=True)
        common.register(str(self.paper), "alpha")

    def test_the_upload_is_noted_in_the_conversation_and_a_request_names_it(self):
        cid = self.chat_thread()["id"]
        st, r = self.c.post("/api/files/upload", {"paper": "alpha", "chat": cid, "name": "results.csv", "data": b64(CSV.encode())})
        self.assertEqual(st, 200, r)
        note = self.last("note")["text"]
        self.assertIn("Added results.csv to the references folder", note)
        self.assertIn("split it into 4 parts", note)
        self.assertIn("Sent to Overleaf", note)
        self.oa.script = [reply(("Yield rose with moisture", "Yield rose clearly with moisture"))] * 3
        plan = self.send("rewrite the results using results.csv")["messages"][1]
        self.assertEqual({s["file"] for s in plan["data"]["plan"]["sources"]}, {"references/results.csv"})
        self.assertEqual(self.oa.requests, [], "listed in the plan, and sent only as part of the request itself")


if __name__ == "__main__":
    unittest.main()
