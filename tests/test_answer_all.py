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

    def search(self, **body):
        self.oa.script = [{"queries": ["armed groups and the security of pipelines in Colombia", "violence and energy infrastructure"]}]
        r = self.send("Find references for these questions: " + Q1, find_refs=True, revises=self.pid, chunk_ids=self.ids, **body)
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        return r

    def test_the_suggestion_has_the_questions_and_nothing_has_been_asked_of_the_internet_yet(self):
        prop = self.c.get(f"/api/proposal?paper=alpha&id={self.pid}")[1]
        self.assertEqual(prop["units"][0]["questions"], [Q1, Q2])
        self.assertEqual(self.net.asked, [])

    def test_the_ticked_question_and_every_answer_go_into_one_search_and_the_unticked_one_does_not_set_the_words(self):
        facts = f"Question: {Q2}\nAnswer: 62 percent responded"
        self.search(facts=facts, ask=[Q1])
        d = self.last("refsearch")["data"]
        planner = self.oa.requests[-1]["body"]["input"]
        self.assertIn(f"<question>\n{Q1}\n</question>", planner, "only the ticked question says what the references are for")
        self.assertNotIn(f"<question>\n{Q1} {Q2}", planner)
        self.assertIn("62 percent responded", planner, "but every answer goes along")
        self.assertEqual((d["ctx"]["facts"], d["ctx"]["revises"]), (facts, self.pid), "and is kept for the re-edit that follows the choice of references")
        self.assertEqual(d["candidates"][0]["doi"], DOI)

    def test_choosing_references_then_rewrites_with_all_the_answers_and_the_references(self):
        facts = f"Question: {Q2}\nAnswer: 62 percent responded"
        self.search(facts=facts, ask=[Q1])
        d = self.last("refsearch")["data"]
        st, r = self.c.post("/api/chat/refs/pick", {"paper": "alpha", "message": self.last("refsearch")["id"], "dois": [DOI]})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."))]
        again = self.send(d["ctx"]["request"] or "x", online=[DOI], facts=d["ctx"]["facts"], revises=d["ctx"]["revises"], chunk_ids=d["ctx"]["chunk_ids"])
        self.wait_job(again["job"])
        sent = json.dumps(self.oa.requests[-1]["body"]["input"])
        self.assertIn("62 percent responded", sent)
        self.assertIn("Rojas and Smith 2019", sent)
        self.assertEqual(revise.get_proposal(self.proj, self.pid)["status"], "superseded", "the first suggestion is replaced only when the new one is ready")

    def test_without_ticks_the_search_still_uses_the_citation_questions_as_before(self):
        self.search()
        self.assertIn(Q1, self.oa.requests[-1]["body"]["input"])

    def test_the_questions_of_the_card_have_a_tick_and_one_button_not_a_button_each(self):
        js = (STATIC / "chat.js").read_text()
        self.assertNotIn("'Find references for this'", js)
        self.assertNotIn("findRefs(", js)
        for needed in ("Send my responses and re-edit", "I do not have the references: look for some on the internet", "Add instructions or change the request", "Send and make a new suggestion", "ask: qs.length ? qs : undefined"):
            self.assertIn(needed, js)


if __name__ == "__main__":
    unittest.main()
