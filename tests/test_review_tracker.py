"""The comments of a peer review: each one shows where it stands, the open ones stay in view, and the chat says so when the last one is handled."""
import json
import time
import unittest
from pathlib import Path

from tests.fake_openai import FakeOpenAI
from tests.helpers import common
from tests.test_paper import paper
from tests.test_revise import reply
from tests.test_ui_server import UiBase
from tests.test_v2_chat import MAIN
from tests.test_v2_review import REPORT, ReviewEnv
from writing_core import chat, config, openai_client, revise

STUDY = reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."), approach="Shorter.")
RESULTS = reply(("Yield rose with moisture in all three regions", "Yield rose with moisture in each of the three regions"), approach="Clearer.")
ONE = REPORT.replace(REPORT[REPORT.index('  {"severity": "minor"'):REPORT.index('  {"severity": "huh"')], "").replace('  {"severity": "huh", "where": "", "quote": "", "problem": "", "suggestion": ""}\n', "").replace('"suggestion": "Give rainfall and soil type for each region."},', '"suggestion": "Give rainfall and soil type for each region."}')


class Tracker(UiBase, ReviewEnv):
    def setUp(self):
        super().setUp()
        self.env_up()
        self.name, self.proj = paper(self, main=MAIN)
        self.main = Path(self.proj["path"]) / "main.tex"
        config.set_paper_setting(self.proj, "understand", "never")
        self.c.login()

    def tearDown(self):
        t0 = time.time()
        while self.srv.app.jobs.running() and time.time() - t0 < 15:
            time.sleep(0.05)
        self.env_down()
        super().tearDown()

    def send(self, text, **kw):
        st, r = self.c.post("/api/chat/send", {"paper": "alpha", "text": text, **kw})
        self.assertEqual(st, 200, r)
        return r

    def chat_get(self):
        st, t = self.c.get("/api/chat?paper=alpha")
        self.assertEqual(st, 200, t)
        return t

    def review(self, report=REPORT):
        plan = self.send("peer review my paper")["messages"][1]
        self.oa.script = [report]
        st, run = self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})
        self.assertEqual(self.wait_job(run["job"])["status"], "done")
        t = self.chat_get()
        rv = [m for m in t["thread"]["messages"] if m["kind"] == "review"][-1]
        return rv

    def states(self, rv):
        return [x["state"] for x in self.chat_get()["issues"][rv["id"]]]

    def fix(self, rv, i, step):
        it = rv["data"]["issues"][i]
        self.oa.script = [step]
        r = self.send(f"Address this reviewer comment: {it['problem']}", chunk_ids=it["chunk_ids"], issue={"review": rv["id"], "index": i})
        self.wait_job(r["job"])
        return [m for m in self.chat_get()["thread"]["messages"] if m["kind"] == "result"][-1]

    def test_every_comment_starts_open_and_a_fix_follows_it_through_to_saved_and_sent(self):
        rv = self.review()
        self.assertEqual(self.states(rv), ["open", "open", "open"])
        res = self.fix(rv, 0, STUDY)
        self.assertEqual(res["data"]["issue"], {"review": rv["id"], "index": 0})
        self.assertEqual(self.states(rv), ["decide", "open", "open"])           # a suggestion waits for the author
        slot = self.chat_get()["issues"][rv["id"]][0]
        self.assertEqual((slot["message"], slot["proposal"]), (res["id"], res["data"]["proposal"]))
        st, r = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": res["data"]["proposal"], "chat": self.chat_get()["id"], "unclean_ok": True})
        self.assertEqual((st, r["ok"]), (200, True), r)
        self.assertEqual(self.states(rv), ["saved", "open", "open"])             # in the files here, not in Overleaf yet
        self.assertEqual(revise.mark_sent(self.proj), 1)                        # a send marks what was saved
        self.assertEqual(self.states(rv), ["sent", "open", "open"])
        self.assertEqual(revise.mark_sent(self.proj), 0)                        # and only once

    def test_when_the_last_comment_is_handled_the_chat_says_so_once_and_asks_what_next(self):
        rv = self.review()
        res = self.fix(rv, 0, STUDY)
        self.c.post("/api/proposal/approve", {"paper": "alpha", "id": res["data"]["proposal"], "chat": self.chat_get()["id"], "unclean_ok": True})
        self.assertEqual([m["kind"] for m in self.chat_get()["thread"]["messages"] if m["kind"] == "review_done"], [])      # not yet: two are still open
        st, r = self.c.post("/api/chat/issue", {"paper": "alpha", "message": rv["id"], "index": 2, "action": "skip"})
        self.assertEqual((st, r["closed"]), (200, False), r)
        self.assertEqual(self.states(rv), ["saved", "open", "skipped"])
        res = self.fix(rv, 1, RESULTS)
        st, r = self.c.post("/api/proposal/record", {"paper": "alpha", "id": res["data"]["proposal"], "accepted": [], "rejected": "all", "chat": self.chat_get()["id"]})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.states(rv), ["saved", "rejected", "skipped"])
        done = [m for m in self.chat_get()["thread"]["messages"] if m["kind"] == "review_done"]
        self.assertEqual(len(done), 1)
        self.assertEqual({k: done[0]["data"][k] for k in ("review", "total", "done", "aside", "unsent")}, {"review": rv["id"], "total": 3, "done": 1, "aside": 2, "unsent": 1})
        # saving or recording again does not repeat it
        self.c.post("/api/chat/issue", {"paper": "alpha", "message": rv["id"], "index": 2, "action": "skip"})
        self.assertEqual(len([m for m in self.chat_get()["thread"]["messages"] if m["kind"] == "review_done"]), 1)

    def test_skipping_can_be_undone_and_trying_a_skipped_comment_again_takes_it_off_the_list(self):
        rv = self.review()
        self.c.post("/api/chat/issue", {"paper": "alpha", "message": rv["id"], "index": 0, "action": "skip"})
        self.assertEqual(self.states(rv), ["skipped", "open", "open"])
        self.c.post("/api/chat/issue", {"paper": "alpha", "message": rv["id"], "index": 0, "action": "reopen"})
        self.assertEqual(self.states(rv), ["open", "open", "open"])
        self.c.post("/api/chat/issue", {"paper": "alpha", "message": rv["id"], "index": 0, "action": "skip"})
        self.fix(rv, 0, STUDY)                                                    # a new try replaces the skip
        self.assertEqual(self.states(rv), ["decide", "open", "open"])
        for bad in ({"index": 9, "action": "skip"}, {"index": "0", "action": "skip"}, {"index": 0, "action": "delete"}, {"index": 0, "action": "skip", "message": "mnope"}):
            body = {"paper": "alpha", "message": rv["id"], **bad}
            self.assertEqual(self.c.post("/api/chat/issue", body)[0], 400, bad)

    def test_a_fix_that_changes_nothing_counts_as_handled_and_a_failed_one_can_be_tried_again(self):
        rv = self.review()
        self.fix(rv, 0, lambda b: {"approach": "Fine as it is.", "revised_text": __import__("tests.fake_openai", fromlist=["passage_of"]).passage_of(b), "changes": [], "questions": []})
        self.assertEqual(self.states(rv), ["nothing", "open", "open"])
        main = self.main.read_text()
        self.main.write_text(main.replace("The second paragraph", "The next paragraph"))        # the paper changes under a waiting suggestion
        it = rv["data"]["issues"][1]
        self.oa.script = [RESULTS]
        r = self.send("Address this reviewer comment: x", chunk_ids=it["chunk_ids"], issue={"review": rv["id"], "index": 1})
        self.wait_job(r["job"])
        self.assertIn(self.states(rv)[1], ("decide", "failed"))

    def test_a_request_marked_as_sent_that_nobody_is_working_on_counts_as_failed(self):
        p = self.proj
        cid = chat.ensure_thread(p)
        rv = chat.append(p, cid, "assistant", "review", "", scope="x", issues=[{"severity": "major", "problem": "p", "chunk_ids": ["a"]}, {"severity": "minor", "problem": "q", "chunk_ids": ["b"]}])
        chat.append(p, cid, "assistant", "plan", "", plan={}, state="sent", issue={"review": rv["id"], "index": 0})
        thread = chat.get_thread(p, cid)
        self.assertEqual(chat.issue_states(p, thread, running=True)[rv["id"]][0]["state"], "working")
        self.assertEqual(chat.issue_states(p, thread, running=False)[rv["id"]][0]["state"], "failed")

    def test_a_single_comment_is_followed_but_needs_no_closing_message(self):
        rv = self.review(ONE)
        self.assertEqual(len(rv["data"]["issues"]), 1)
        res = self.fix(rv, 0, STUDY)
        self.c.post("/api/proposal/approve", {"paper": "alpha", "id": res["data"]["proposal"], "chat": self.chat_get()["id"], "unclean_ok": True})
        self.assertEqual(self.states(rv), ["saved"])
        self.assertEqual([m for m in self.chat_get()["thread"]["messages"] if m["kind"] == "review_done"], [])

    def test_a_made_up_comment_reference_is_ignored(self):
        rv = self.review()
        it = rv["data"]["issues"][0]
        self.oa.script = [STUDY]
        r = self.send("Address this: x", chunk_ids=it["chunk_ids"], issue={"review": rv["id"], "index": 7})
        self.wait_job(r["job"])
        self.assertEqual(self.states(rv), ["open", "open", "open"])
        self.assertNotIn("issue", json.dumps([m["data"] for m in self.chat_get()["thread"]["messages"] if m["kind"] in ("plan", "result")]))


if __name__ == "__main__":
    unittest.main()
