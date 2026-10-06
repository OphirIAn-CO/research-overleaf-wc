"""When the words of a request do not say where new text goes, the model chooses the place from what the author wrote, how the request was read and what is in the chat (including a suggestion
that is not saved yet), and the author is not asked the same question twice. Also: "do not use references from X" is kept, and a ticked reference whose record cannot be read is still added."""
import json
import unittest

import doi_bot
from tests.test_book import needs_pdf
from tests.test_outline_citations import Net, draft_reply, openalex, routes
from tests.test_refsearch_api import ENTRY
from tests.test_understanding import reading
from tests.test_v2_chat_api import ChatBase
from writing_core import config, draft, paper_index, revise, scholar

PLACE_ASK = "add a passage that supports this part"


def place(choice, position="after", reason="it is the paragraph you mean", question=""):
    return {"place": choice, "position": position, "reason": reason, "question": question}


class Words(unittest.TestCase):
    def test_a_name_is_avoided_when_every_word_of_it_is_in_an_authors_name(self):
        self.assertTrue(scholar.avoided(["Rafael Amaya-Gómez", "John Smith"], ["Amaya Gomez", "Cabrales"]), "accents, hyphens and order do not matter")
        self.assertTrue(scholar.avoided("John Smith; Ana Cabrales", ["Cabrales"]))
        self.assertFalse(scholar.avoided(["Ana Gomez"], ["Amaya Gomez"]), "one common surname is not the author who was named")
        self.assertFalse(scholar.avoided(["Ana Gomez"], []))

    def test_an_empty_author_in_a_record_does_not_break_the_entry(self):
        raw = "@article{x,\n title={Multivariate Regression Model to Predict Failure of Pipelines},\n author={ and Hariharan, Narain and Cobanoglu, Murat},\n year={2017},\n journal={IJET},\n doi={10.7763/ijet.2017.v9.1006}\n}"
        bib, key = doi_bot.restyle(raw)
        self.assertEqual(key, "Hariharan2017Multivariate")
        self.assertNotIn("{ and", bib)
        self.assertEqual(doi_bot.restyle(raw.replace("author={ and Hariharan, Narain and Cobanoglu, Murat},", "author={ and },"))[1][:4], "Anon", "a record with no author at all gets a key too")


@needs_pdf
class Placing(ChatBase):
    def setUp(self):
        super().setUp()
        paper_index.refresh(self.name, self.proj, trigger="test")
        tex = [c for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["kind"] == "tex"]
        order = sorted(tex, key=lambda c: c["ord"])
        self.pieces = {f"C{i}": c for i, c in enumerate(order, 1)}
        self.study = next(k for k, c in self.pieces.items() if "Study area" in (c.get("section_path") or ""))

    def go(self, text=PLACE_ASK, **kw):
        return self.send(text, flag="draft", **kw)

    def test_when_the_words_do_not_say_where_the_model_chooses_and_the_author_is_not_asked(self):
        c = self.pieces[self.study]
        self.oa.script = [place(self.study, "after", "the sentence you pasted is in the study area"), draft_reply("The regions differ in rainfall, which shapes how farms were sampled.")]
        r = self.go()
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "draft_plan"], r["messages"])
        plan = r["messages"][1]["data"]
        self.assertEqual(plan["plan"]["anchor"]["after_line"], c["line_end"])
        self.assertIn("I chose this place from what you wrote", " ".join(plan["notes"]))
        self.assertIn("Study area", plan["plan"]["where"])
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        sent = json.dumps(self.oa.requests[0]["body"])
        self.assertIn("<pieces>", sent)
        self.assertIn(c["file"], sent)

    def test_a_question_from_the_model_is_asked_once_and_names_what_it_needs(self):
        self.oa.script = [place("", question="Do you mean the end of the introduction or the end of the methods?")]
        r = self.go()
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "text"])
        self.assertIn("Do you mean the end of the introduction or the end of the methods?", r["messages"][1]["text"])
        self.assertNotIn("Which part of the paper should the new text go in?", r["messages"][1]["text"])

    def test_words_that_do_say_where_need_no_model_for_it(self):
        self.oa.script = [draft_reply("The new paragraph is about the sampling.")]
        r = self.go("add a paragraph on the sampling to the methods")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "draft_plan"])
        self.wait_job(r["job"])
        self.assertNotIn("<pieces>", json.dumps(self.oa.requests[0]["body"]), "the place was found from the words, so no request was made for it")

    def test_a_passage_that_is_in_a_suggestion_not_saved_yet_is_written_again_together_with_the_new_one(self):
        self.oa.script = [draft_reply("Related studies support count models of failures.")]
        r = self.go("add a paragraph on the sampling to the methods")
        self.wait_job(r["job"])
        old = self.last("result")["data"]["proposal"]
        self.assertEqual(revise.get_proposal(self.proj, old)["status"], "proposed")
        self.oa.script = [place("S1", "after", "that passage is in the suggestion above"), draft_reply("Related studies support count models of failures. A second sentence now backs it with the reference.")]
        r = self.go("it would be to support this part\n\nRelated studies support count models of failures.")
        plan = r["messages"][1]
        self.assertEqual((plan["kind"], plan["data"].get("revises")), ("draft_plan", old), "it is the pending suggestion that is meant")
        self.assertIn("replaces that suggestion", " ".join(plan["data"]["notes"]))
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        sent = json.dumps(self.oa.requests[-1]["body"])
        self.assertIn("The suggestion I wrote earlier for this place", sent)
        self.assertIn("Related studies support count models of failures", sent)
        self.assertEqual(revise.get_proposal(self.proj, old)["status"], "superseded", "the old one is replaced when the new one is ready")

    def test_when_the_model_gives_no_place_the_old_question_is_still_asked(self):
        keep = draft.find_place
        self.addCleanup(setattr, draft, "find_place", keep)
        draft.find_place = lambda *a, **k: None
        r = self.go()
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "text"])
        self.assertIn("Which part of the paper should the new text go in?", r["messages"][1]["text"])


@needs_pdf
class Excluding(ChatBase):
    def setUp(self):
        super().setUp()
        config.set_paper_setting(self.proj, "understand", "always")
        self._fetch, self._doi = scholar.fetch, doi_bot.fetch_bibtex
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", self._doi)
        hits = json.loads(openalex(("10.1016/a.1", "Soil moisture and crop yield by Amaya", 2019), ("10.1016/b.2", "Soil moisture and crop yield by Lee", 2020)))
        hits["results"][0]["authorships"] = [{"author": {"display_name": "Rafael Amaya-Gómez"}}]
        hits["results"][1]["authorships"] = [{"author": {"display_name": "Kim Lee"}}]
        self.net = scholar.fetch = Net({**routes(), "https://api.openalex.org/works": json.dumps(hits), "https://api.crossref.org/works": json.dumps({"message": {"items": []}}),
                                       "https://html.duckduckgo.com/html/": "<html></html>"})

    def test_do_not_use_references_from_a_named_author_is_kept(self):
        self.oa.script = [reading("refsearch", 0.95, "You want references on soil moisture and yield, not by Amaya Gomez or Cabrales.", "the paper", online=True, avoid=["Amaya Gomez", "Cabrales"])] + self.oa.script
        r = self.send("find references on soil moisture and crop yield, but dont use references from Amaya Gomez or Cabrales")
        job = r.get("job")
        if job is None:                                   # the reading was shown first: go ahead
            card = r["messages"][-1]
            st, res = self.c.post("/api/chat/understood", {"paper": "alpha", "message": card["id"], "action": "go"})
            self.assertEqual(st, 200, res)
            job = res["job"]
        self.assertEqual(self.wait_job(job)["status"], "done")
        d = self.last("refsearch")["data"]
        titles = [c["title"] for c in d["candidates"]]
        self.assertTrue(any("by Lee" in t for t in titles), titles)
        self.assertFalse(any("by Amaya" in t for t in titles), titles)
        self.assertEqual((d["excluded"], d["avoid"]), (1, ["Amaya Gomez", "Cabrales"]))


if __name__ == "__main__":
    unittest.main()
