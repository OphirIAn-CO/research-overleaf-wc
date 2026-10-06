"""The agent: a request is planned by the model from the whole conversation, with the app's abilities as tools. The model is a script here (one JSON action per turn, interleaved with the answers of the
writing tools it calls); the paper, the library and the internet are the same stand-ins as in the other tests. What is checked: it looks before it writes, it does the work in several steps, it keeps
the context (the author's words, pending suggestions, references picked), the tools' own checks still apply, nothing is saved, and it falls back to saying so when it cannot."""
import json
import unittest

import doi_bot
from tests.test_book import needs_pdf
from tests.test_outline_citations import BIB_RISK, Net, draft_reply, openalex, routes
from tests.test_revise import reply
from tests.test_v2_chat_api import ChatBase
from writing_core import agent, config, paper_index, revise, scholar


def act(action, note="", **kw):
    base = {"note": note, "action": action, "what": "", "query": "", "queries": [], "chunks": [], "place": "", "position": "after", "instruction": "", "notes": "", "sources": [], "dois": [],
            "avoid": [], "question": "", "message": ""}
    base.update(kw)
    return base


@needs_pdf
class Base(ChatBase):
    def setUp(self):
        super().setUp()
        config.set_paper_setting(self.proj, "agent", "agent")
        paper_index.refresh(self.name, self.proj, trigger="test")
        tex = sorted((c for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["kind"] == "tex"), key=lambda c: c["ord"])
        self.keys = {f"C{i}": c for i, c in enumerate(tex, 1)}
        self.study = next(k for k, c in self.keys.items() if "Study area" in (c.get("section_path") or ""))

    def say(self, *items):
        self.oa.script = list(items)

    def texts(self):
        return [m for m in self.chat_thread()["thread"]["messages"] if m["role"] == "assistant"]

    def bodies(self):
        return [json.dumps(r["body"]) for r in self.oa.requests if r.get("body")]


class Working(Base):
    def test_it_reads_the_paper_then_edits_the_place_and_tells_what_it_did(self):
        self.say(act("read_paper", "Reading the study area", query="Study area"),
                 act("edit_text", "Improving the flow of the study area", chunks=[self.study], instruction="Make the two sentences about the regions flow as one line of thought; keep every fact."),
                 reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil.")),
                 act("finish", message="I read the study area and tightened the flow of its two sentences. Nothing is saved: read the suggestion and accept it if you like it."))
        r = self.send("the study area paragraph reads badly, fix it")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text"], "no card in front of the work: the agent does it")
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        kinds = [m["kind"] for m in self.texts()]
        self.assertEqual(kinds, ["result", "text"], kinds)
        res = self.texts()[0]
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={res['data']['proposal']}")
        self.assertEqual((st, prop["status"]), (200, "proposed"))
        self.assertEqual(prop["author_request"], "the study area paragraph reads badly, fix it")
        self.assertIn("Make the two sentences about the regions flow", prop["instruction"], "the instruction is the one the agent composed, not a canned sentence")
        self.assertNotIn("Improve the clarity, flow, grammar and concision", prop["instruction"])
        final = self.texts()[-1]
        self.assertIn("I read the study area", final["text"])
        self.assertTrue(final["data"]["agent"])
        b = self.bodies()
        self.assertIn("Each region has a distinct climate and soil.", b[1], "what it read comes back to it as the result of the step")
        self.assertNotIn("\\cite{smith2020}", b[1], "citations stay behind as [ref] in what the model reads")
        self.assertIn([s["key"] for s in j["steps"]][:1], [["agent0"]])

    def test_it_searches_finds_sources_and_writes_a_passage_that_cites_them(self):
        doi = "10.5555/soil.2020.1"
        item = json.loads(openalex((doi, "Soil moisture and crop yield across regions: evidence from forty farms", 2020)))
        item["results"][0]["authorships"] = [{"author": {"display_name": "Kim Lee"}}]
        self._fetch, self._doi = scholar.fetch, doi_bot.fetch_bibtex
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", self._doi)
        scholar.fetch = Net({**routes(), "https://api.openalex.org/works": json.dumps(item), "https://api.crossref.org/works": json.dumps({"message": {"items": []}}), "https://html.duckduckgo.com/html/": "<html></html>"})
        doi_bot.fetch_bibtex = lambda d: BIB_RISK.replace("10.5555/risk.2019.007", doi).replace("Risk perception and trust across hazards", "Soil moisture and crop yield across regions") if d == doi else (_ for _ in ()).throw(RuntimeError("HTTP 404"))
        self.say(act("search_internet", "Looking for studies on soil moisture and yield", queries=["soil moisture crop yield regions farms"]),
                 act("write_new_text", "Writing a supporting passage", place=self.study, position="after", sources=["R1"],
                     instruction="Add one short paragraph after the study-area text saying that soil moisture is a known driver of crop yield across regions, citing the study found.",
                     notes="The author wants a passage that supports their idea that moisture matters."),
                 draft_reply("Earlier work links soil moisture to crop yield across regions [[LX901]]."),
                 act("finish", message="I found a study on soil moisture and yield and wrote a short paragraph that cites it. It is known only by its abstract: read it before you rely on it."))
        r = self.send("add a passage after the study area that supports my idea that soil moisture drives yield, with references")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        msgs = self.texts()
        self.assertEqual([m["kind"] for m in msgs], ["result", "text"])
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={msgs[0]['data']['proposal']}")
        self.assertEqual(prop["kind"], "draft")
        self.assertEqual([a["doi"] for a in prop["bib_adds"]], [doi], "the entry is written only when the text that cites it is saved")
        self.assertEqual(len(prop["units"][0]["added_cites"]), 1)
        self.assertIn("R1 |", self.bodies()[1], "what the search found is what the model chooses from")
        self.assertTrue(msgs[0]["data"]["draft"])

    def test_the_library_is_read_when_the_agent_names_what_to_look_up_whatever_the_wording_of_the_instruction(self):
        from pathlib import Path
        from tests.pdfmaker import make_pdf
        from tests.test_outline_citations import RISK
        from writing_core import library
        keep = doi_bot.fetch_bibtex
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", keep)
        doi_bot.fetch_bibtex = lambda d: BIB_RISK if d == "10.5555/risk.2019.007" else (_ for _ in ()).throw(RuntimeError("HTTP 404"))
        library._ENTRY_CACHE.clear()
        self.addCleanup(library._ENTRY_CACHE.clear)
        shelf = self.tmp / "Documents" / "Literature"
        shelf.mkdir(parents=True)
        make_pdf(shelf / "Smith et al. - 2019 - Risk perception and trust across hazards.pdf", RISK)
        library.set_folders([str(shelf)])
        library.update()
        self.say(act("write_new_text", "Writing", place=self.study, instruction="Write one sentence on how people perceive risk differently across hazards.", query="risk perception across hazards"),
                 draft_reply("Perceived risk differs across hazards [[LX901]]."), act("finish", message="Done."))
        r = self.send("add a sentence on how risk perception differs")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.assertIn("<library_sources>", self.bodies()[1], "the library was read although the instruction does not use a word that asks for it")
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={[m for m in self.texts() if m['kind'] == 'result'][0]['data']['proposal']}")
        self.assertEqual([a["doi"] for a in prop["bib_adds"]], ["10.5555/risk.2019.007"])

    def test_a_pending_suggestion_is_the_place_when_the_author_says_this_part(self):
        self.say(act("write_new_text", "Writing", place=self.study, instruction="Write one sentence about the sampling frame.", notes=""),
                 draft_reply("The sampling frame was listed by the regional office."),
                 act("finish", message="Done."))
        r = self.send("add a sentence about the sampling frame")
        self.wait_job(r["job"])
        old = [m for m in self.texts() if m["kind"] == "result"][0]["data"]["proposal"]
        self.say(act("write_new_text", "Writing it together", place="S1", instruction="Keep the sentence about the sampling frame and add one that says why the list was sorted by size.", notes=""),
                 draft_reply("The sampling frame was listed by the regional office. It was sorted by size before sampling."),
                 act("finish", message="I added the reason to the same suggestion."))
        r = self.send("add to this part why it was sorted by size")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.assertEqual(revise.get_proposal(self.proj, old)["status"], "superseded")
        self.assertIn("The suggestion I wrote earlier for this place", " ".join(self.bodies()[-2:]))
        self.assertIn("S1 | goes", self.bodies()[-3], "the pending suggestion is in front of the model")

    def test_a_question_to_the_author_is_one_specific_question_and_nothing_else_happens(self):
        self.say(act("ask_user", question="Do you mean the end of the introduction or the end of the methods?"))
        r = self.send("add the thing we talked about")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.assertEqual([m["kind"] for m in self.texts()], ["text"])
        self.assertIn("end of the introduction or the end of the methods", self.texts()[0]["text"])

    def test_the_same_step_twice_is_refused_and_it_gets_to_the_end(self):
        same = act("read_paper", "Reading", query="outline")
        self.say(same, same, same, act("finish", message="I read the outline."))
        r = self.send("what does my paper cover?")
        self.wait_job(r["job"])
        self.assertIn("You already did exactly this", self.bodies()[3])
        self.assertEqual(self.texts()[-1]["text"], "I read the outline.")

    def test_what_a_tool_refuses_comes_back_to_the_model_and_it_can_explain(self):
        self.say(act("edit_text", "Editing", chunks=["C9999"], instruction="Shorten it."), act("finish", message="I could not tell which text you mean: say the section."))
        r = self.send("shorten it")
        self.wait_job(r["job"])
        self.assertIn("Say which piece(s)", self.bodies()[1])
        self.assertEqual([m["kind"] for m in self.texts()], ["text"])

    def test_a_writing_tool_that_fails_its_checks_makes_no_suggestion_and_says_what_failed(self):
        bad = draft_reply("Soil is key — it matters a lot [[LX977]].")           # a dash and a tag that was not offered
        self.say(act("write_new_text", "Writing", place=self.study, instruction="Write one sentence on soil."), bad, bad,
                 act("finish", message="The writing failed its checks twice, so I made no suggestion."))
        r = self.send("write a sentence on soil")
        self.wait_job(r["job"])
        self.assertEqual([m["kind"] for m in self.texts()], ["text"], "no suggestion card for a draft that was refused")
        self.assertIn("PROBLEMS", self.bodies()[3])

    def test_without_a_working_model_it_says_so(self):
        self.oa.script = [(400, {"error": {"message": "bad request", "type": "invalid_request_error"}})]
        r = self.send("improve the study area")
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "error")
        err = [m for m in self.texts() if m["kind"] == "error"]
        self.assertTrue(err and "I could not plan this request" in err[0]["text"], err)


class Routing(Base):
    def test_the_ways_that_have_a_card_or_a_choice_of_their_own_are_not_taken_by_the_agent(self):
        self.say(reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil.")))
        config.set_paper_setting(self.proj, "confirm_before_send", True)
        r = self.send("improve the study area")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "plan"], "with 'ask me before anything is sent' on, the classic plan card is shown")
        config.set_paper_setting(self.proj, "confirm_before_send", False)
        r = self.send("improve the flow of the study area", flag="edit")
        self.assertEqual([m["kind"] for m in r["messages"]][:2], ["text", "plan"], "a kind chosen by the author is the classic way")
        self.wait_job(r["job"])
        before = len(self.oa.requests)
        keep = doi_bot.fetch_bibtex
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", keep)
        doi_bot.fetch_bibtex = lambda d: (_ for _ in ()).throw(RuntimeError("HTTP 404"))
        r = self.send("10.1016/j.enpol.2019.05.012")
        self.wait_job(r["job"])
        self.assertEqual(len(self.oa.requests), before, "a pasted DOI is added by the app, the model is not asked")
        self.assertEqual(self.last("refs")["kind"], "refs")
        config.set_paper_setting(self.proj, "agent", "classic")
        r = self.send("improve the study area")
        self.assertEqual([m["kind"] for m in r["messages"]][:2], ["text", "plan"])
        self.wait_job(r["job"])

    def test_the_setting_is_validated_and_kept(self):
        st, res = self.c.post("/api/settings", {"paper": "alpha", "agent": "sometimes"})
        self.assertEqual(st, 400)
        st, res = self.c.post("/api/settings", {"paper": "alpha", "agent": "classic"})
        self.assertEqual(st, 200, res)
        self.assertEqual(config.paper_settings(self.proj)["agent"], "classic")
        self.assertEqual(agent.MAX_ACTIONS, 12)


if __name__ == "__main__":
    unittest.main()
