"""Answering a suggestion's questions: all of them are answered first, which of them are to be answered by looking for references on the internet is a tick, and one button sends everything
together. Nothing is asked of OpenAI or the internet by a question's own button."""
import json
import unittest
from pathlib import Path

from tests.test_book import needs_pdf
import doi_bot
from tests.test_refsearch_api import DOI, ENTRY
from tests.test_outline_citations import Net, routes
from tests.test_revise import reply
from tests.test_v2_chat_api import ASK, ChatBase
from writing_core import config, revise, scholar

Q1 = "Which citations would support the claim about security and violence?"
Q2 = "What was the response rate of the farmers?"
STATIC = Path(__file__).resolve().parents[1] / "scripts" / "ui" / "static"


@needs_pdf
class Together(ChatBase):
    def setUp(self):
        super().setUp()
        self._fetch = scholar.fetch
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self.net = scholar.fetch = Net(routes())
        self._doi, self._mend, self._push = doi_bot.fetch_bibtex, doi_bot.to_mendeley, doi_bot.git_sync
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", self._doi)
        self.addCleanup(setattr, doi_bot, "to_mendeley", self._mend)
        self.addCleanup(setattr, doi_bot, "git_sync", self._push)
        doi_bot.fetch_bibtex = lambda d: ENTRY if d == DOI else (_ for _ in ()).throw(RuntimeError("HTTP 404"))
        doi_bot.to_mendeley = lambda *a: "Mendeley: added to G."
        doi_bot.git_sync = lambda bib, msg: (True, "Pushed to Overleaf.")
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."), questions=[Q1, Q2])]
        r = self.send(ASK)
        self.wait_job(r["job"])
        self.pid = self.last("result")["data"]["proposal"]
        self.ids = self.chat_thread()["thread"]["messages"][1]["data"]["chunk_ids"]

    ANSWER = "Question: " + Q1 + "\nAnswer: add a citation of how armed groups attacking pipelines affects the infrastructure in Colombia\nQuestion: " + Q2 + "\nAnswer: 62 percent responded"

    def test_the_questions_about_sources_are_not_asked_the_others_are(self):
        prop = self.c.get(f"/api/proposal?paper=alpha&id={self.pid}")[1]
        self.assertEqual(prop["units"][0]["questions"], [Q2], "choosing sources is the app's job, not a question for the author")
        self.assertTrue(any("found nothing that supports" in w for w in prop["warnings"]), prop["warnings"])
        self.assertEqual(self.net.asked, [])

    def test_ticking_look_for_references_while_answering_searches_picks_and_cites_without_asking_again(self):
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has a distinct climate and soil [[LX901]]."))]
        r = self.send("Find references for these questions: " + Q1, find_refs=True, revises=self.pid, chunk_ids=self.ids, facts=self.ANSWER, ask=[Q1])
        self.assertEqual(r["messages"][1]["data"]["plan"]["web"], {"ask": [Q1]})
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        self.assertIn("web", [s["key"] for s in j["steps"]])
        asked = " ".join(u for u in self.net.asked if "openalex" in u).lower()
        self.assertIn("security", asked, "the ticked question says what the references are for")
        sent = json.dumps(self.oa.requests[-1]["body"]["input"])
        self.assertIn("62 percent responded", sent, "every answer goes along")
        self.assertIn("Rojas and Smith 2019", sent)
        self.assertFalse([m for m in self.chat_thread()["thread"]["messages"] if m["kind"] == "refsearch"], "no card to choose from, and no new question")
        new = self.c.get(f"/api/proposal?paper=alpha&id={self.last('result')['data']['proposal']}")[1]
        self.assertEqual(new["units"][0]["added_cites"], ["\\cite{Rojas2019Armed}"])
        self.assertEqual(revise.get_proposal(self.proj, self.pid)["status"], "superseded", "the first suggestion is replaced only when the new one is ready")
        self.assertNotIn("Find references for these questions", sent, "the button's own words are not the instruction")
        self.assertNotIn("Improve the clarity, flow, grammar and concision", sent, "no stock polish is added to a request that was about something else")
        self.assertIn(ASK, sent, "what the author asked in the first place is what the model is asked, with the answers")

    def test_a_search_the_author_asked_for_with_the_kind_flag_still_shows_the_candidates_to_choose_from(self):
        self.oa.script = [{"queries": ["armed groups and the security of pipelines in Colombia", "violence and energy infrastructure"]}]
        r = self.send("Find references for these questions: " + Q1, flag="refsearch", revises=self.pid, chunk_ids=self.ids, facts=self.ANSWER)
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        d = self.last("refsearch")["data"]
        planner = self.oa.requests[-1]["body"]["input"]
        self.assertIn("62 percent responded", planner)
        self.assertEqual((d["ctx"]["facts"], d["ctx"]["revises"]), (self.ANSWER, self.pid))
        self.assertEqual(d["candidates"][0]["doi"], DOI)
        st, r = self.c.post("/api/chat/refs/pick", {"paper": "alpha", "message": self.last("refsearch")["id"], "dois": [DOI]})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.wait_job(r["job"])["status"], "done")

    def test_the_questions_of_the_card_have_a_tick_and_one_button_not_a_button_each(self):
        js = (STATIC / "chat.js").read_text()
        self.assertNotIn("'Find references for this'", js)
        self.assertNotIn("findRefs(", js)
        for needed in ("Send my responses and re-edit", "I do not have the references: look for them on the internet", "Add instructions or change the request", "Send and make a new suggestion", "ask: qs.length ? qs : undefined"):
            self.assertIn(needed, js)


if __name__ == "__main__":
    unittest.main()
