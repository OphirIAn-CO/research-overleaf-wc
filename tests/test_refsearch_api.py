"""Asking the chat for references: it searches the internet (stubbed here), shows what it found, adds what the author ticks to the bibliography, and a request can then cite it."""
import json
import unittest
from pathlib import Path

import doi_bot
from tests.helpers import common
from tests.test_online_use import DOI_ENTRY  # noqa: F401  (shape of a doi.org answer)
from tests.test_revise import reply
from tests.test_scholar import Net, routes
from tests.test_v2_chat_api import ASK, ChatBase
from writing_core import chat, config, scholar

FIND = "find references about armed groups attacking pipelines in Colombia"
ENTRY = ("@article{x,\n author={Rojas, Camila and Smith, John},\n title={Armed conflict and pipeline attacks in Colombia},\n year={2019},\n journal={Energy Policy},\n"
         " doi={10.1016/j.enpol.2019.05.012}\n}")
DOI = "10.1016/j.enpol.2019.05.012"


class Intent(unittest.TestCase):
    def test_what_is_a_request_for_references_and_what_is_not(self):
        yes = ["find references about how violence affects infrastructure in Colombia", "search for papers on pipeline attacks", "get me some citations for the introduction",
               "show me studies on armed conflict", "I need references about data curation", "suggest sources for the claim in the first paragraph of the introduction",
               "search the web for evidence on pipelines and armed groups"]
        no = ["improve the introduction and find references", "write the discussion and find references", "add a citation to the methods", "fix the references section",
              "what do my articles say about irrigation?", "peer review my paper", "tighten the methods",
              "Address this reviewer comment: the description of the data curation process is insufficient"]
        for t in yes:
            self.assertEqual(chat.intent(t), "refsearch", t)
        for t in no:
            self.assertNotEqual(chat.intent(t), "refsearch", t)
        self.assertEqual(chat.intent("find references in my library about irrigation"), "library")
        self.assertEqual(chat.intent("10.1016/j.enpol.2019.05.012"), "refs")

    def test_the_subject_is_what_is_left_of_the_request(self):
        self.assertEqual(chat.refs_topic("Can you find me some references about how violence affects pipelines in Colombia?"), "how violence affects pipelines in Colombia")
        self.assertEqual(chat.refs_topic("search for papers on pipeline attacks"), "pipeline attacks")


class Search(ChatBase):
    def setUp(self):
        super().setUp()
        self._fetch, self._doi = scholar.fetch, doi_bot.fetch_bibtex
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", self._doi)
        self.net = Net(routes())
        scholar.fetch = self.net
        doi_bot.fetch_bibtex = lambda d: ENTRY if d == DOI else (_ for _ in ()).throw(RuntimeError("HTTP 404"))
        doi_bot.to_mendeley = lambda *a: "Mendeley: added to G."
        self._push = doi_bot.git_sync
        self.addCleanup(setattr, doi_bot, "git_sync", self._push)
        doi_bot.git_sync = lambda bib, msg: (True, "Pushed to Overleaf.")
        self.bib = Path(self.proj["path"]) / self.proj["bib"]

    def search(self, text=FIND, **kw):
        r = self.send(text, **kw)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text"], r)
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        return j, self.last("refsearch")

    def test_it_searches_the_internet_and_shows_what_it_found_without_adding_anything(self):
        before = self.bib.read_text()
        j, m = self.search()
        d = m["data"]
        self.assertEqual([s["key"] for s in j["steps"]], ["words", "web", "index", "pages"])
        self.assertEqual((d["how"], d["queries"]), ("topic", ["armed groups attacking pipelines in Colombia"]))
        self.assertEqual(len(self.oa.requests), 0, "a plain subject needs no model call")
        first = d["candidates"][0]
        self.assertEqual((first["doi"], first["label"], first["in_bib"]), (DOI, "Rojas and Smith 2019", False))
        self.assertIn("412 attacks", first["abstract"])
        self.assertEqual(sorted(first["sources"]), ["crossref", "openalex", "web"])
        self.assertEqual(self.bib.read_text(), before, "nothing is added until the author ticks it")
        self.assertEqual(d["ctx"]["chunk_ids"], [])
        self.assertTrue(all(t["ok"] for t in d["tried"]))

    def test_a_reference_already_in_the_bibliography_says_so(self):
        self.bib.write_text(self.bib.read_text() + "@article{rojas2019armed, title={Armed conflict and pipeline attacks in Colombia}, year={2019}, doi={10.1016/j.enpol.2019.05.012}}\n")
        j, m = self.search()
        first = m["data"]["candidates"][0]
        self.assertEqual((first["in_bib"], first["cite_key"]), (True, "rojas2019armed"))

    def test_switched_off_for_the_paper_it_does_not_touch_the_internet(self):
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "use_online": "never"})[0], 200)
        r = self.send(FIND)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "text"])
        self.assertIn("switched off", r["messages"][1]["text"])
        self.assertEqual(self.net.asked, [])
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "use_online": "sometimes"})[0], 400)

    def test_with_no_connection_it_says_so_and_nothing_else_breaks(self):
        scholar.fetch = Net({})
        r = self.send(FIND)
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "error")
        err = self.last("error")
        self.assertIn("could not reach the internet", err["text"])

    def test_no_subject_asks_what_to_look_for(self):
        r = self.send("show the references")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "text"])
        self.assertIn("What should I look for?", r["messages"][1]["text"])
        self.assertEqual(self.net.asked, [])

    def test_a_place_that_cannot_be_found_and_no_subject_asks_instead_of_searching_for_nothing(self):
        r = self.send("find a source for this")
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done")
        self.assertIn("What should the references be about?", self.last()["text"])
        self.assertEqual(self.net.asked, [])

    def test_a_request_that_points_at_a_place_uses_that_text_to_choose_the_words(self):
        self.oa.script = [{"queries": ["effect of regional climate and soil on crop yield", "stratified sampling of farms"]}]
        j, m = self.search("suggest sources for the text before figure 1 in methods")
        d = m["data"]
        self.assertEqual((d["how"], d["queries"][0]), ("model", "effect of regional climate and soil on crop yield"))
        sent = json.dumps(self.oa.requests[0]["body"])
        self.assertIn("distinct climate and soil", sent, "the passage went to the model that writes the search words")
        self.assertTrue(d["ctx"]["chunk_ids"], "and the place is kept so the references can be used there")
        self.assertIn("before figure 1", d["ctx"]["where"].lower())

    def test_ticked_references_go_to_the_bibliography_and_are_remembered(self):
        j, m = self.search()
        st, r = self.c.post("/api/chat/refs/pick", {"paper": "alpha", "message": m["id"], "dois": [DOI, "10.9999/not-in-the-list"]})
        self.assertEqual(st, 200, r)
        done = self.wait_job(r["job"])
        self.assertEqual(done["status"], "done", done)
        self.assertIn("@article{Rojas2019Armed,", self.bib.read_text())
        added = self.last("refsearch")["data"]["added"]
        self.assertEqual(added[DOI]["key"], "Rojas2019Armed")
        self.assertEqual(added[DOI]["cite"], "\\cite{Rojas2019Armed}")
        self.assertEqual(self.last("refs")["data"]["results"][0]["status"], "added")
        remembered = json.loads((Path(self.proj["path"]) / ".overleaf-skill" / "found_refs.json").read_text())
        self.assertEqual((remembered[0]["key"], "412 attacks" in remembered[0]["abstract"]), ("Rojas2019Armed", True))
        st, r = self.c.post("/api/chat/refs/pick", {"paper": "alpha", "message": m["id"], "dois": ["10.9999/nothing"]})
        self.assertEqual(st, 400)

    def test_a_reference_already_in_the_bibliography_can_be_used_without_adding_it_again(self):
        self.bib.write_text(self.bib.read_text() + "@article{rojas2019armed, title={Armed conflict and pipeline attacks in Colombia}, year={2019}, doi={10.1016/j.enpol.2019.05.012}}\n")
        j, m = self.search()
        r = self.c.post("/api/chat/refs/pick", {"paper": "alpha", "message": m["id"], "dois": [DOI]})[1]
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.assertEqual(self.bib.read_text().count("Armed conflict and pipeline attacks"), 1)
        self.assertEqual(self.last("refsearch")["data"]["added"][DOI]["key"], "rojas2019armed")

    def test_a_request_can_then_cite_what_was_picked(self):
        j, m = self.search()
        pick = self.c.post("/api/chat/refs/pick", {"paper": "alpha", "message": m["id"], "dois": [DOI]})[1]
        self.assertEqual(self.wait_job(pick["job"])["status"], "done")
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has a distinct climate and soil [[LX901]]."))]
        r = self.send(ASK, online=[DOI, "10.1/never-added"])
        plan = r["messages"][1]["data"]["plan"]
        self.assertEqual(plan["online"], [DOI], "only references that are in this paper's bibliography go along")
        done = self.wait_job(r["job"])
        self.assertEqual(done["status"], "done", done)
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={self.last('result')['data']['proposal']}")
        u = prop["units"][0]
        self.assertEqual(u["added_cites"], ["\\cite{Rojas2019Armed}"], u.get("error"))
        self.assertTrue(prop["library"][0]["online"])
        sent = self.oa.requests[-1]["body"]
        self.assertIn("Rojas and Smith 2019, abstract (found online)", json.dumps(sent))

    def test_from_a_suggestions_question_the_search_knows_the_comment_the_answers_and_the_place(self):
        # a suggestion that asks which citations would help
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."), questions=["Which additional citations would help justify the points about security?"])]
        r = self.send(ASK)
        self.wait_job(r["job"])
        res = self.last("result")
        pid, ids = res["data"]["proposal"], res["data"]["chunk_ids"]
        facts = "Question: Which additional citations would help justify the points about security?\nAnswer: add a citation of how armed groups attacking pipelines affects the infrastructure in Colombia"
        self.oa.script = [{"queries": ["armed groups attacks on pipelines Colombia infrastructure", "violence effects on energy infrastructure"]}]
        j, m = self.search("Find references for the question about security", find_refs=True, chunk_ids=ids, facts=facts, revises=pid)
        d = m["data"]
        self.assertEqual(d["how"], "model")
        sent = json.dumps(self.oa.requests[-1]["body"])
        self.assertIn("armed groups attacking pipelines", sent)
        self.assertIn("Which additional citations would help", sent, "the question the model asked is part of what the search words are made from")
        self.assertEqual((d["ctx"]["revises"], d["ctx"]["chunk_ids"], d["ctx"]["facts"]), (pid, ids, facts))
        self.assertEqual(d["ctx"]["request"], ASK, "what the suggestion was asked to do is kept, to ask for it again with the references")
        self.assertFalse(d["ctx"]["draft"])
        # the author types other words
        j, m2 = self.search("Find references about pipelines", find_refs=True, chunk_ids=ids, facts=facts, revises=pid, words="oil pipeline sabotage Colombia")
        self.assertEqual((m2["data"]["how"], m2["data"]["queries"]), ("given", ["oil pipeline sabotage Colombia"]))


if __name__ == "__main__":
    unittest.main()
