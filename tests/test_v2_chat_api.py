"""The chat through the web app: a request goes in, a plan or a question comes back, the answer lands in the thread."""
import json
import threading
import time
import unittest
from pathlib import Path

from tests.fake_openai import FakeOpenAI
from tests.helpers import common
from tests.test_book import FIXTURE, needs_pdf
from tests.test_paper import paper
from tests.test_revise import reply
from tests.test_ui_server import FakeWatermark, UiBase
from tests.test_v2_chat import MAIN
from writing_core import book_index, chat, config, openai_client, paper_index

ASK = "improve the flow of the text before figure 1 in methods"


@needs_pdf
class ChatBase(UiBase):
    def setUp(self):
        super().setUp()
        openai_client.SLEEP = lambda s: None
        self.oa = FakeOpenAI("responses")
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url})
        book_index.ingest(FIXTURE, "mini")
        self.name, self.proj = paper(self, main=MAIN)
        self.main = Path(self.proj["path"]) / "main.tex"
        self.c.login()
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."),
                                approach="The two sentences repeated the same idea; now they flow.")] * 5

    def tearDown(self):
        t0 = time.time()
        while self.srv.app.jobs.running() and time.time() - t0 < 15:  # a pass still writing into the paper folder would race its removal
            time.sleep(0.05)
        self.oa.close()
        super().tearDown()

    def send(self, text=ASK, **kw):
        st, r = self.c.post("/api/chat/send", {"paper": "alpha", "text": text, **kw})
        self.assertEqual(st, 200, r)
        return r

    def chat_thread(self, cid=None):
        st, t = self.c.get("/api/chat?paper=alpha" + (f"&id={cid}" if cid else ""))
        self.assertEqual(st, 200, t)
        return t

    def last(self, kind=None):
        msgs = self.chat_thread()["thread"]["messages"]
        return [m for m in msgs if kind in (None, m["kind"])][-1]


class ChatApi(ChatBase):
    def test_a_request_is_understood_sent_and_answered_in_the_thread(self):
        r = self.send()
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "plan"])
        plan = r["messages"][1]
        self.assertEqual(plan["data"]["state"], "sent")                    # sent straight away: asking is the consent
        self.assertIn("before figure 1", plan["data"]["title"].lower())
        self.assertEqual(plan["data"]["plan"]["units"], 1)
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        self.assertEqual([s["key"] for s in j["steps"]], ["read", "advice", "ask1", "check1", "finish"])
        res = self.last("result")
        self.assertEqual(res["data"]["status"], "proposed")
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={res['data']['proposal']}")
        u = prop["units"][0]
        self.assertEqual(u["approach"], "The two sentences repeated the same idea; now they flow.")
        self.assertIn("Each region has its own climate and soil.", u["views"]["suggested_masked"])
        self.assertNotIn("mapping", u)
        self.assertNotIn("After the map", self.oa.requests[0]["body"]["input"])       # only the part before the figure went out

    def test_confirm_before_sending_is_a_setting_and_a_request_is_sent_once(self):
        config.set_paper_setting(self.proj, "confirm_before_send", True)
        r = self.send()
        plan = r["messages"][1]
        self.assertEqual((plan["data"]["state"], plan["data"]["confirm"], r["job"]), ("waiting", "setting", None))
        self.assertEqual(self.oa.requests, [])
        st, run = self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})
        self.assertEqual(st, 200, run)
        self.assertEqual(self.wait_job(run["job"])["status"], "done")
        self.assertEqual(self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})[0], 409)
        self.assertEqual(len(self.oa.requests), 1)

    def test_a_big_request_waits_for_a_yes_even_without_the_setting(self):
        common.save("ui.json", {"guard_passages": 0})
        plan = self.send("improve the methods")["messages"][1]
        self.assertEqual((plan["data"]["state"], plan["data"]["confirm"]), ("waiting", "big"))
        self.assertEqual(self.oa.requests, [])

    def test_no_place_gives_a_question_with_sections_and_ticking_pieces_makes_a_plan(self):
        r = self.send("make it better")
        ask = r["messages"][1]
        self.assertEqual((ask["kind"], r["job"]), ("ask", None))
        self.assertIn("Materials and Methods", [s["label"] for s in ask["data"]["sections"]])
        ticked = [s for s in ask["data"]["sections"] if s["label"] == "Introduction"][0]["chunk_ids"]
        r2 = self.send("make it better", chunk_ids=ticked)
        self.assertEqual(r2["messages"][1]["kind"], "plan")
        self.assertTrue(r2["job"])

    def test_a_follow_up_means_the_same_place(self):
        self.wait_job(self.send()["job"])
        r = self.send("make it shorter")
        self.assertEqual(r["messages"][1]["data"]["chunk_ids"], self.chat_thread()["thread"]["messages"][1]["data"]["chunk_ids"])
        self.wait_job(r["job"])

    def test_help_does_not_call_openai(self):
        self.assertIn("Tell me what to do", self.send("help")["messages"][1]["text"])
        self.assertEqual(self.oa.requests, [])

    def test_a_pasted_doi_is_added_in_the_background_and_answered_with_a_card(self):
        import doi_bot
        orig = doi_bot.fetch_bibtex, doi_bot.git_sync
        doi_bot.fetch_bibtex = lambda d: "@article{x,\n author={Lee, K.},\n title={Soil},\n year={2021},\n doi={%s}\n}" % d
        doi_bot.git_sync = lambda bib, msg: (True, "Pushed to Overleaf.")
        try:
            r = self.send("please add 10.1016/j.cageo.2020.104524 to my paper")
            self.assertTrue(r["job"])
            j = self.wait_job(r["job"])
            self.assertEqual((j["status"], j["result"]), ("done", {"added": 1}), j)
            m = self.last("refs")
            self.assertEqual(m["data"]["results"][0]["cite"], "\\cite{Lee2021Soil}")
            self.assertTrue(m["data"]["push"]["ok"])
            self.assertIn("Lee2021Soil", (Path(self.proj["path"]) / "references.bib").read_text())
        finally:
            doi_bot.fetch_bibtex, doi_bot.git_sync = orig
        self.assertEqual(self.oa.requests, [])

    def test_missing_key_and_openai_errors_are_explained(self):
        (common.HOME / "openai.json").unlink()
        r = self.send()
        self.assertEqual(r["messages"][1]["kind"], "error")
        self.assertIn("Settings", r["messages"][1]["text"])
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url})
        self.oa.script = [(401, {"error": {"message": "bad key"}})]
        r = self.send()
        j = self.wait_job(r["job"])
        self.assertEqual((j["status"], j["result"]["failed"]), ("done", "auth"))
        err = self.last()
        self.assertEqual(err["kind"], "error")
        self.assertIn("did not accept your API key", err["text"])
        self.assertEqual(self.last("plan")["data"]["state"], "failed")

    def test_a_paper_that_changed_after_planning_is_not_sent(self):
        config.set_paper_setting(self.proj, "confirm_before_send", True)
        plan = self.send()["messages"][1]
        self.main.write_text(self.main.read_text().replace("Each region has a distinct climate", "Every region has a distinct climate"))
        st, run = self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})
        j = self.wait_job(run["job"])
        self.assertEqual(j["status"], "error")
        self.assertIn("changed", self.last()["text"])
        self.assertEqual(self.oa.requests, [])

    def test_threads_list_new_rename_delete_and_isolation(self):
        self.send()
        st, t = self.c.get("/api/chat/threads?paper=alpha")
        self.assertEqual(len(t["threads"]), 1)
        st, new = self.c.post("/api/chat/new", {"paper": "alpha"})
        self.assertEqual(self.c.post("/api/chat/rename", {"paper": "alpha", "id": new["thread"]["id"], "title": "Second"})[0], 200)
        titles = {x["id"]: x["title"] for x in self.c.get("/api/chat/threads?paper=alpha")[1]["threads"]}
        self.assertEqual(titles[new["thread"]["id"]], "Second")
        self.assertEqual(self.c.post("/api/chat/delete", {"paper": "alpha", "id": new["thread"]["id"]})[0], 200)
        self.assertEqual(self.c.post("/api/chat/delete", {"paper": "alpha", "id": "../../etc"})[0], 400)
        n2, p2 = paper(self, name="beta", main=MAIN)
        mine = self.c.get("/api/chat?paper=alpha")[1]["id"]
        theirs = self.c.get(f"/api/chat?paper=beta&id={mine}")[1]
        self.assertNotEqual(theirs["id"], mine)                     # another paper's chat id opens that paper's own chat, never this one
        self.assertEqual(theirs["thread"]["messages"], [])

    def test_count_and_the_paper_text_pane(self):
        r = self.send()
        ids = r["messages"][1]["data"]["chunk_ids"]
        self.wait_job(r["job"])
        st, c = self.c.post("/api/chat/count", {"paper": "alpha", "chunk_ids": ids})
        self.assertEqual((c["units"], c["ok"]), (1, True))
        from urllib.parse import quote
        st, t = self.c.get(f"/api/paper/alpha/text?ids={quote(','.join(ids))}")
        text = "\n".join(x["text"] for x in t["pieces"])
        self.assertIn("The study was conducted", text)
        self.assertNotIn("\\begin{figure}", text)
        st, s = self.c.get("/api/paper/alpha/sections")
        self.assertIn("Results", [x["label"] for x in s["sections"]])


@needs_pdf
class ApproveFlow(ChatBase):
    def setUp(self):
        super().setUp()
        r = self.send()
        self.wait_job(r["job"])
        self.pid = self.last("result")["data"]["proposal"]
        st, self.prop = self.c.get(f"/api/proposal?paper=alpha&id={self.pid}")
        self.view = self.prop["units"][0]["views"]

    def test_live_checks_in_sentences(self):
        st, r = self.c.post("/api/proposal/check", {"paper": "alpha", "id": self.pid, "texts": {"u1": self.view["suggested_masked"]}})
        c = r["checks"]["u1"]
        self.assertTrue(c["ok"])
        self.assertIn("diff_original", c)
        st, r = self.c.post("/api/proposal/check", {"paper": "alpha", "id": self.pid, "texts": {"u1": self.view["suggested_masked"].replace("three regions", "four regions")}})
        self.assertFalse(r["checks"]["u1"]["ok"])
        self.assertIn("Numbers not in the original: 4", json.dumps(r["checks"]["u1"]["messages"]))
        self.assertEqual(self.c.post("/api/proposal/check", {"paper": "alpha", "id": self.pid, "texts": "no"})[0], 400)

    def test_approving_the_authors_own_version_writes_it(self):
        wm = FakeWatermark()
        try:
            self.c.post("/api/settings", {"ui": {"watermark_url": wm.url}})
            mine = self.view["suggested_masked"].replace("its own climate and soil", "a climate and soil of its own")
            st, r = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": self.pid, "texts": {"u1": mine}, "chat": self.chat_thread()["id"]})
            self.assertEqual((st, r["ok"]), (200, True), r)
            written = self.main.read_text()
            self.assertIn("a climate and soil of its own", written)
            self.assertNotIn("its own climate and soil", written)
            self.assertIn("\\begin{figure}", written)
            self.assertEqual(self.last()["kind"], "note")
            self.assertIn("Saved in main.tex", self.last()["text"])
            self.assertEqual(self.c.get(f"/api/proposal?paper=alpha&id={self.pid}")[1]["status"], "applied")
        finally:
            wm.close()

    def test_a_blocked_version_is_returned_with_reasons_and_nothing_is_written(self):
        wm = FakeWatermark()
        try:
            self.c.post("/api/settings", {"ui": {"watermark_url": wm.url}})
            bad = self.view["suggested_masked"].replace("three regions", "four regions")
            before = self.main.read_text()
            st, r = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": self.pid, "texts": {"u1": bad}})
            self.assertEqual((st, r["ok"], r["blocked"]), (200, False, ["u1"]))
            self.assertEqual(self.main.read_text(), before)
        finally:
            wm.close()

    def test_saving_in_the_app_without_a_cleaner_needs_the_authors_explicit_choice_and_says_so(self):
        before = self.main.read_text()
        st, r = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": self.pid, "accept": "all"})
        self.assertEqual((st, r["handoff_available"]), (409, True), "without the choice nothing is written")
        self.assertEqual(self.main.read_text(), before)
        st, r = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": self.pid, "accept": "all", "unclean_ok": True, "chat": self.chat_thread()["id"]})
        self.assertEqual((st, r["ok"], r["cleaned"]), (200, True, False), r)
        self.assertNotEqual(self.main.read_text(), before)
        self.assertIn("no text-cleaning service is set up", self.last()["text"])
        self.assertEqual(self.c.get(f"/api/proposal?paper=alpha&id={self.pid}")[1]["status"], "applied")

    def test_with_a_cleaner_set_up_the_choice_cannot_skip_it(self):
        wm = FakeWatermark()
        try:
            self.c.post("/api/settings", {"ui": {"watermark_url": wm.url}})
            st, r = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": self.pid, "accept": "all", "unclean_ok": True})
            self.assertEqual((st, r["cleaned"]), (200, True))
            self.assertEqual([c[0] for c in wm.calls], ["/inspect", "/clean"], "the cleaner ran although the flag was sent")
        finally:
            wm.close()

    def test_the_ticked_changes_can_be_copied_as_text_without_writing_anything(self):
        before = self.main.read_text()
        uids = [h["uid"] for u in self.prop["units"] for h in u["hunks"]]
        self.assertTrue(uids)
        st, r = self.c.post("/api/proposal/text", {"paper": "alpha", "id": self.pid, "accept": "all"})
        self.assertEqual(st, 200)
        self.assertEqual(r["latex"], self.view["suggested_latex"])
        self.assertEqual(r["masked"], self.view["suggested_masked"])
        self.assertIn("[[LX", r["masked"])
        self.assertNotIn("[[LX", r["latex"])
        st, none = self.c.post("/api/proposal/text", {"paper": "alpha", "id": self.pid, "accept": []})
        self.assertEqual(none["latex"].strip(), self.prop["units"][0]["original_text"].strip(), "no change ticked: the original text")
        self.assertEqual(self.c.post("/api/proposal/text", {"paper": "alpha", "id": "nope"})[0], 400)
        self.assertEqual(self.main.read_text(), before)

    def test_without_the_cleaner_saving_is_refused_and_handing_to_claude_is_offered(self):
        st, r = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": self.pid, "accept": "all"})
        self.assertEqual(st, 409)
        self.assertTrue(r["handoff_available"])
        st, r = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": self.pid, "accept": "all", "mode": "handoff"})
        self.assertEqual((st, r["ok"]), (200, True))
        self.assertEqual(self.c.get(f"/api/proposal?paper=alpha&id={self.pid}")[1]["handoff"]["accepted"], ["u1.h1"])


if __name__ == "__main__":
    unittest.main()


class StartingOver(ChatBase):
    """What a chat remembers, and the ways to forget it."""

    def test_a_follow_up_means_the_same_place_until_the_chat_is_started_over(self):
        cid = self.chat_thread()["id"]
        self.wait_job(self.send()["job"])
        follow = self.send("make it shorter")
        self.assertEqual(follow["messages"][1]["kind"], "plan", "it knows where the last request worked")
        self.wait_job(follow["job"])
        st, r = self.c.post("/api/chat/clear", {"paper": "alpha", "id": cid})
        self.assertEqual((st, r), (200, {"ok": True}))
        t = self.chat_thread(cid)
        self.assertEqual((t["thread"]["messages"], t["thread"]["title"], t["id"]), ([], "New chat", cid), "the same chat, empty")
        self.assertEqual([x["id"] for x in self.c.get("/api/chat/threads?paper=alpha")[1]["threads"]], [cid])
        again = self.send("make it shorter")
        self.assertEqual(again["messages"][1]["kind"], "ask", "with nothing before it, it has to ask which part")
        self.assertEqual(len(self.oa.requests), 2, "and nothing more was sent to OpenAI for the question")

    def test_clearing_and_deleting_are_refused_while_a_request_is_running_and_for_unknown_chats(self):
        cid = self.chat_thread()["id"]
        gate = threading.Event()
        orig = self.oa.script
        self.oa.script = [lambda body: (gate.wait(5) and None) or orig[0](body)]
        r = self.send()
        try:
            self.assertEqual(self.c.post("/api/chat/clear", {"paper": "alpha", "id": cid})[0], 409)
            self.assertEqual(self.c.post("/api/chat/delete", {"paper": "alpha", "id": cid})[0], 409)
        finally:
            gate.set()
        self.wait_job(r["job"])
        self.assertEqual(self.c.post("/api/chat/clear", {"paper": "alpha", "id": "c" + "0" * 10})[0], 400)
        self.assertEqual(self.c.post("/api/chat/clear", {"paper": "nobody", "id": cid})[0], 404)
        self.assertEqual(self.c.post("/api/chat/delete", {"paper": "alpha", "id": cid})[0], 200)
        self.assertTrue(self.c.get("/api/chat/threads?paper=alpha")[1]["threads"], "a chat is always there to write in")

    def test_what_was_learned_from_the_decisions_can_be_forgotten_and_a_new_chat_keeps_it_until_then(self):
        from writing_core import preferences
        pid = self.last_proposal_id()
        self.c.post("/api/proposal/record", {"paper": "alpha", "id": pid, "accepted": "all", "rejected": []})
        st, pref = self.c.get("/api/paper/alpha/preferences")
        self.assertGreater(pref["accepted"] + pref["rejected"], 0)
        self.assertTrue(preferences.digest(self.proj))
        self.c.post("/api/chat/new", {"paper": "alpha"})
        self.assertTrue(preferences.digest(self.proj), "a new chat does not forget what the paper has taught")
        st, f = self.c.post("/api/paper/alpha/forget", {})
        self.assertEqual(st, 200)
        self.assertGreater(f["forgotten"], 0)
        self.assertEqual(preferences.digest(self.proj), "")
        self.assertEqual(self.c.get("/api/paper/alpha/preferences")[1]["accepted"], 0)

    def last_proposal_id(self):
        self.wait_job(self.send()["job"])
        return self.last("result")["data"]["proposal"]

