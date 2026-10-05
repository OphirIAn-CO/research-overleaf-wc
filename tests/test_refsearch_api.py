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
        j, m = self.search("Find references for the question about security", flag="refsearch", chunk_ids=ids, facts=facts, revises=pid)
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

    def test_ticking_look_for_references_while_answering_searches_picks_and_cites_without_asking(self):
        """The author answers the model's question and ticks "look for them on the internet": the app searches, keeps what is about the subject, rewrites with it and cites it."""
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."), questions=["Which additional citations would help justify the points about security?"])]
        r = self.send(ASK)
        self.wait_job(r["job"])
        res = self.last("result")
        pid, ids = res["data"]["proposal"], res["data"]["chunk_ids"]
        facts = "Question: Which additional citations would help justify the points about security?\nAnswer: add a citation of how armed groups attacking pipelines affects the infrastructure in Colombia"
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has a distinct climate and soil [[LX901]]."))]
        before = self.bib.read_text()
        r = self.send("Revise the suggestion with my answers", find_refs=True, chunk_ids=ids, facts=facts, revises=pid)
        plan = r["messages"][1]["data"]["plan"]
        self.assertEqual(plan["web"], {"ask": []}, "the app looks for the sources itself")
        done = self.wait_job(r["job"])
        self.assertEqual(done["status"], "done", done)
        self.assertEqual([m["kind"] for m in self.chat_thread()["thread"]["messages"] if m["kind"] == "refsearch"], [], "no card of candidates to choose from")
        keys = [s["key"] for s in done["steps"]]
        self.assertIn("web", keys)
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={self.last('result')['data']['proposal']}")
        u = prop["units"][0]
        self.assertEqual(u["added_cites"], ["\\cite{Rojas2019Armed}"], u.get("error"))
        self.assertTrue(any(x["online"] and x["cite_key"] == "Rojas2019Armed" for x in prop["library"]))
        self.assertEqual([a["key"] for a in prop["bib_adds"]], ["Rojas2019Armed"])
        self.assertEqual(self.bib.read_text(), before, "the entry is written when the text that cites it is saved, not before")
        sent = json.dumps(self.oa.requests[-1]["body"])
        self.assertIn("found online", sent)

    def test_a_request_about_what_is_written_in_the_introduction_searches_the_papers_subject_and_hides_the_off_topic_records(self):
        """The real case: "from what is written in te introduction ... strengthen the ideas being made" matched papers on mathematics, law and teaching because of the words "ideas" and
        "introduction". The place is the introduction (the typo does not matter), the words are the paper's subject, and a record that is not about it is not shown."""
        from tests.test_outline_citations import openalex
        hits = openalex(("10.1016/j.hm.2016.07.002", "On Khinchin's paper Ideas of intuitionism and the struggle for a subject matter in contemporary mathematics", 2016),
                        ("10.22215/etd/2012-09451", "Translating travelling ideas: the introduction of unemployment insurance and labour law reforms", 2012),
                        ("10.5555/soil.2020.1", "Soil moisture and crop yield across regions: evidence from forty farms", 2020))
        self.net.pages["https://api.openalex.org/works"] = hits
        self.net.pages["https://api.crossref.org/works"] = json.dumps({"message": {"items": []}})
        self.net.pages["https://html.duckduckgo.com/html/"] = "<html></html>"
        self.oa.script = [{"queries": ["soil moisture and crop yield on farms", "irrigation guidance for farmers"]}]
        j, m = self.search("from what is written in te introduction it is necessary to find references to strengthen the ideas being made")
        d = m["data"]
        self.assertTrue(d["ctx"]["chunk_ids"], "the introduction is the place, whatever the typo")
        self.assertEqual([c["doi"] for c in d["candidates"]], ["10.5555/soil.2020.1"])
        self.assertEqual(d["hidden"], 2, "the two records that share only the words of the request are left out")
        asked = " ".join(self.net.asked).lower()
        self.assertIn("moisture", asked)
        self.assertNotIn("ideas", asked, "the words of the request are never the search")
        planner = json.dumps(self.oa.requests[-1]["body"]["input"])
        self.assertIn("soil moisture shapes crop yield", planner, "the paper's own abstract is what the words are made from")

    def test_records_that_share_only_the_words_of_the_request_are_not_shown(self):
        """The search matched "ideas" and "introduction" in papers about mathematics and law: nothing about the paper's subject, so nothing is shown."""
        junk = [{"title": "On Khinchin's paper Ideas of intuitionism and the struggle for a subject matter in contemporary mathematics"}]
        self.assertTrue(scholar.on_subject({"title": "Armed conflict and pipeline attacks in Colombia", "abstract": ""}, "armed groups attacking pipelines in Colombia"))
        self.assertFalse(scholar.on_subject(dict(title=junk[0]["title"], abstract=""), "perceived risk survey hazard heuristics population heterogeneity"))
        self.assertTrue(scholar.on_subject({"title": "Risk perception and hazard heuristics", "abstract": ""}, "perceived risk survey hazard heuristics population heterogeneity"))
        self.assertTrue(scholar.on_subject({"title": "anything"}, "x"), "with no subject to judge by, nothing is held back")


if __name__ == "__main__":
    unittest.main()
