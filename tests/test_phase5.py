import json
import subprocess
import unittest
from pathlib import Path
from urllib.parse import quote

import doi_bot
import overleaf_mcp as mcp
from tests.fake_openai import FakeOpenAI
from tests.helpers import common
from tests.test_book import FIXTURE, needs_pdf
from tests.test_paper import METHODS, paper
from tests.test_revise import reply
from tests.test_ui_server import FakeWatermark, UiBase
from writing_core import book_index, config, events, openai_client, paper_index, revise, stats

BIB_ENTRY = "@article{x,\n author={Lee, K.},\n title={Soil Water Dynamics},\n year={2021}\n}"


def porcelain(root):
    return subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()


@needs_pdf
class Phase5(UiBase):
    def setUp(self):
        super().setUp()
        openai_client.SLEEP = lambda s: None
        self.oa = FakeOpenAI("responses")
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url})
        book_index.ingest(FIXTURE, "mini")
        self.name, self.proj = paper(self)
        self.root = Path(self.proj["path"])
        self.c.login()
        self._orig = doi_bot.fetch_bibtex
        doi_bot.fetch_bibtex = lambda d: BIB_ENTRY if "bad" not in d else (_ for _ in ()).throw(RuntimeError("HTTP 404"))

    def tearDown(self):
        doi_bot.fetch_bibtex = self._orig
        self.oa.close()
        super().tearDown()

    # ---- paper card and index page
    def test_paper_card_roundtrip_is_private_and_travels_with_requests(self):
        st, c = self.c.get("/api/paper/alpha/card")
        self.assertEqual((c["exists"], c["text"]), (False, ""))
        self.assertIn("## Key terms", c["template"])
        self.assertEqual(self.c.post("/api/paper/alpha/card", {"text": "say 'yield', never 'harvest'"})[0], 200)
        self.assertEqual(self.c.get("/api/paper/alpha/card")[1]["text"], "say 'yield', never 'harvest'")
        self.assertEqual(self.c.post("/api/paper/alpha/card", {"text": "x" * (ui_card_max() + 1)})[0], 400)
        self.assertEqual(self.c.post("/api/paper/alpha/card", {"text": 5})[0], 400)
        self.assertEqual(self.c.post("/api/paper/zzz/card", {"text": "x"})[0], 404)
        self.assertEqual(porcelain(self.root), "")
        st, pv = self.c.post("/api/revise/preview", {"paper": "alpha", "instruction": "tighten my methods", "part": "methods"})
        self.assertIn("never 'harvest'", json.dumps(pv["payloads"]))

    def test_index_page_data_marks_what_the_last_refresh_rewrote(self):
        st, r = self.c.get("/api/paper/alpha/index")
        self.assertEqual(len(r["chunks"]), 11)
        self.assertTrue(all("text" not in c for c in r["chunks"]))
        (self.root / "sections" / "methods.tex").write_text(METHODS.replace("40 farms", "42 farms"))
        st, r = self.c.get("/api/paper/alpha/index")
        recent = [c["section_path"] for c in r["chunks"] if c["recent"]]
        self.assertEqual(recent, ["Materials and Methods > Participants and sampling"])
        self.assertEqual(r["last_change_trigger"], "view")
        cid = next(c["id"] for c in r["chunks"] if c["recent"])
        st, ch = self.c.get("/api/paper/alpha/chunk?id=" + quote(cid, safe=""))
        self.assertIn("42 farms", ch["text"])
        self.assertEqual(self.c.get("/api/paper/alpha/chunk?id=P:alpha:nope")[0], 404)

    # ---- references
    def test_doi_preview_flags_new_duplicate_and_unresolvable(self):
        text = "see 10.1234/abc, and https://doi.org/10.9999/bad. also 10.1234/abc again"
        st, r = self.c.post("/api/refs/preview", {"paper": "alpha", "text": text})
        self.assertEqual([(x["doi"], x["status"]) for x in r["refs"]], [("10.1234/abc", "new"), ("10.9999/bad", "error")])
        self.assertEqual(r["refs"][0]["key"], "Lee2021Soil")
        self.assertIn("Soil Water Dynamics", r["refs"][0]["title"])
        self.assertEqual(self.c.post("/api/refs/preview", {"paper": "alpha", "text": "no doi here"})[0], 400)
        (self.root / "references.bib").write_text((self.root / "references.bib").read_text() + "\n% 10.1234/abc is already cited\n")
        st, r = self.c.post("/api/refs/preview", {"paper": "alpha", "text": "10.1234/abc"})
        self.assertEqual(r["refs"][0]["status"], "duplicate")

    def test_adding_a_doi_writes_the_bib_logs_the_source_and_shows_in_recent(self):
        st, a = self.c.post("/api/refs/add", {"paper": "alpha", "dois": ["10.1234/abc"], "push": False})
        self.assertIn("Added \\cite{Lee2021Soil}", a["messages"][0])
        self.assertIn("Lee2021Soil", (self.root / "references.bib").read_text())
        self.assertEqual(porcelain(self.root), "M references.bib")
        st, rec = self.c.get("/api/refs/recent?paper=alpha")
        self.assertEqual([(e["source"], e["key"]) for e in rec["recent"]], [("web", "Lee2021Soil")])
        self.assertEqual(rec["bib_entries"], 3)  # the index followed the edit: 2 original entries + the new one
        st, state = self.c.get("/api/state")
        self.assertEqual(common.get_active()[:2], ("alpha", "ui"))
        self.assertEqual(self.c.post("/api/refs/add", {"paper": "alpha", "dois": []})[0], 400)
        # a push that cannot work (the fake remote) is reported, not raised
        st, a = self.c.post("/api/refs/add", {"paper": "alpha", "dois": ["10.1234/other"], "push": True})
        self.assertFalse(a["push"]["ok"])

    def test_phone_and_web_additions_share_one_list(self):
        doi_bot.log_added(common.bib_path(self.proj), "10.1/phone", "Phone2020Key", "telegram", None)
        st, rec = self.c.get("/api/refs/recent?paper=alpha")
        self.assertEqual(rec["recent"][0]["source"], "telegram")
        m = stats.monitor()
        self.assertEqual(m["overview"]["doi_added"], 1)

    # ---- hand to Claude
    def make_proposal(self):
        config.set_paper_setting(self.proj, "dry_run", False)
        self.oa.script = [reply(("We sampled 40 farms by stratified random sampling.", "We drew a stratified random sample of 40 farms."),
                                ("Informed consent was obtained from all farmers.", "All farmers gave informed consent."))]
        return revise.run(self.name, self.proj, "tighten my methods", part="methods.data")["proposal"]

    def test_the_whole_handoff_round_trip(self):
        p = self.make_proposal()
        uids = [h["uid"] for h in p["units"][0]["hunks"]]
        self.assertEqual(mcp.tool_chatgpt_revise({"repo_path": "alpha"}), "No proposals are waiting for Claude.")
        st, r = self.c.post("/api/proposal/handoff", {"paper": "alpha", "id": p["id"], "accept": [uids[0]]})
        self.assertEqual((st, r["accepted"]), (200, [uids[0]]))
        self.assertEqual(self.methods_text(), METHODS)  # handing over writes nothing
        listing = mcp.tool_chatgpt_revise({"repo_path": "alpha"})
        self.assertIn(p["id"], listing)
        self.assertIn(uids[0], listing)
        detail = mcp.tool_chatgpt_revise({"repo_path": "alpha", "proposal_id": p["id"]})
        self.assertIn("THE AUTHOR CHOSE THESE CHANGES: " + uids[0], detail)
        self.assertIn("step 6b", detail)
        st, lst = self.c.get("/api/proposals?paper=alpha")
        self.assertTrue(lst["proposals"][0]["handoff"])
        evs = events.read(config.paper_dir(self.proj), types={"handoff"})
        self.assertEqual((evs[-1]["proposal"], evs[-1]["hunks"]), (p["id"], 1))
        # Claude applies the chosen change with Edit and records it
        rec = mcp.tool_revise_record({"repo_path": "alpha", "proposal_id": p["id"], "accepted": [uids[0]], "rejected": "all", "applied": True})
        self.assertIn("1 accepted", rec)
        self.assertEqual(mcp.tool_chatgpt_revise({"repo_path": "alpha"}), "No proposals are waiting for Claude.")
        self.assertEqual(self.c.post("/api/proposal/handoff", {"paper": "alpha", "id": p["id"], "accept": [uids[0]]})[0], 400)  # already decided

    def methods_text(self):
        return (self.root / "sections" / "methods.tex").read_text()

    def test_handoff_validation(self):
        p = self.make_proposal()
        self.assertEqual(self.c.post("/api/proposal/handoff", {"paper": "alpha", "id": p["id"], "accept": []})[0], 400)
        self.assertEqual(self.c.post("/api/proposal/handoff", {"paper": "alpha", "id": p["id"], "accept": ["u9.h9"]})[0], 400)
        self.assertEqual(self.c.post("/api/proposal/handoff", {"paper": "alpha", "id": "../x"})[0], 400)
        self.assertEqual(self.c.post("/api/proposal/handoff", {"paper": "nope", "id": p["id"]})[0], 404)

    def test_decisions_are_listed_newest_first_after_apply(self):
        p = self.make_proposal()
        wm = FakeWatermark()
        try:
            self.c.post("/api/settings", {"ui": {"watermark_url": wm.url}})
            uids = [h["uid"] for h in p["units"][0]["hunks"]]
            self.c.post("/api/proposal/apply", {"paper": "alpha", "id": p["id"], "accept": [uids[0]]})
        finally:
            wm.close()
        st, d = self.c.get("/api/paper/alpha/decisions")
        self.assertEqual([x["status"] for x in d["decisions"]].count("accepted"), 1)
        self.assertEqual([x["status"] for x in d["decisions"]].count("rejected"), 1)
        self.assertTrue(all(x["proposal"] == p["id"] for x in d["decisions"]))
        self.assertEqual(self.c.get("/api/paper/zzz/decisions")[0], 404)

    def test_handoff_counts_in_the_monitor_activity(self):
        p = self.make_proposal()
        self.c.post("/api/proposal/handoff", {"paper": "alpha", "id": p["id"], "accept": "all"})
        tl = stats.timeline(stats.gather(), days=3)
        self.assertEqual(tl["series"]["handoff"][-1], 1)


def ui_card_max():
    from tests.helpers import load_ui_server
    return load_ui_server().CARD_MAX


if __name__ == "__main__":
    unittest.main()
