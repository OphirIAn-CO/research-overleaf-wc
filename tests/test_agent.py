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
    base = {"note": note, "action": action, "what": "", "query": "", "queries": [], "chunks": [], "place": "", "position": "after", "instruction": "", "notes": "", "sources": [], "files": [], "dois": [],
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


class Reading(Base):
    """How it reads the paper: it always sees the whole paper as a map, and reads the text of the parts it asks for, by id, range, kind of section or title."""

    def ctx(self):
        return agent.Ctx(self.name, self.proj, "t", "x")

    def keys_of(self, hits):
        return [k for k, _, _ in hits]

    def test_a_kind_of_section_is_found_whatever_its_title(self):
        c = self.ctx()
        methods = [k for k, p in c.pieces.items() if "Materials and Methods" in (p.get("section_path") or "")]
        self.assertTrue(methods)
        self.assertEqual(self.keys_of(agent._find_pieces(c, "methodology")), methods, "'methodology' is the section called Materials and Methods")
        both = self.keys_of(agent._find_pieces(c, "abstract and introduction"))
        self.assertTrue(any("Introduction" in c.pieces[k]["section_path"] for k in both))

    def test_ids_ranges_the_whole_paper_and_a_quoted_sentence_are_understood(self):
        c = self.ctx()
        keys = list(c.pieces)
        self.assertEqual(self.keys_of(agent._find_pieces(c, f"{keys[0]}-{keys[2]}")), keys[:3])
        self.assertEqual(self.keys_of(agent._find_pieces(c, f"{keys[1]} {keys[3]}")), [keys[1], keys[3]])
        self.assertEqual(self.keys_of(agent._find_pieces(c, "full manuscript")), keys)
        quoted = agent._find_pieces(c, "The second paragraph describes soils, slopes and the access roads that were used to reach every farm in the sample")
        self.assertTrue(quoted and "access roads" in agent._plain_lines(c.proj, c.pieces[quoted[0][0]], quoted[0][1], quoted[0][2]), "a quoted sentence finds the part that holds it")

    def test_reading_says_what_was_read_and_what_was_left_for_length(self):
        c = self.ctx()
        text, _ = agent.t_read_paper(c, {"query": "full manuscript"})
        self.assertTrue(text.startswith("Read: "), text[:80])
        self.assertIn("citations and formulas show as [ref]", text)
        self.assertNotIn("\\cite{smith2020}", text, "citations stay behind when the model reads")
        none, _ = agent.t_read_paper(c, {"query": "zebra migration patterns"})
        self.assertIn("could not match", none)
        self.assertIn("Materials and Methods", none, "and the map of the paper comes back, so it can choose")

    def test_a_long_paper_is_still_shown_whole_in_the_map(self):
        from pathlib import Path
        long = "\\documentclass{article}\n\\begin{document}\n" + "".join(f"\\section{{Topic {i}}}\nSentence number {i} about the topic and its details for the reader.\n\n" for i in range(1, 160)) + "\\end{document}\n"
        (Path(self.proj["path"]) / "main.tex").write_text(long)
        c = agent.Ctx(self.name, self.proj, "t", "x")
        m = c.sections_map()
        self.assertIn("Topic 1 ", m + " ")
        self.assertIn("Topic 159", m, "the last section of a long paper is in the map")
        self.assertIn("<paper_map", c.context())
        self.assertLess(len(m), 14001)

    def test_a_brief_that_says_keep_the_citations_is_not_a_demand_for_a_new_one(self):
        self.say(act("edit_text", "Shortening the study area", chunks=[self.study], instruction="Shorten this passage while keeping every citation and every number exactly as they are."),
                 reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil.")), act("finish", message="Done."))
        r = self.send("shorten the study area")
        self.wait_job(r["job"])
        res = [m for m in self.texts() if m["kind"] == "result"]
        self.assertTrue(res, "the suggestion was made: the word 'citation' in the brief did not make a new citation required")
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={res[0]['data']['proposal']}")
        self.assertEqual(prop["status"], "proposed")
        self.assertIn("Suggestion ready", self.bodies()[-1], "and the model is told so in one line")


class Broad(Base):
    """A request about the whole paper: it works section by section within its steps, can read a page the author linked, and when the steps run out it says what is done and what is left."""

    def long_paper(self, n=12):
        from pathlib import Path
        text = "\\documentclass{article}\n\\begin{document}\n" + "".join(
            f"\\section{{Part {i}}}\nIn this part we describe, in a rather long and roundabout way, the thing number {i} that we studied.\n\nIt should be said that part {i} was long.\n\n" for i in range(1, n + 1)) + "\\end{document}\n"
        (Path(self.proj["path"]) / "main.tex").write_text(text)
        paper_index.refresh(self.name, self.proj, trigger="test")
        return agent.Ctx(self.name, self.proj, "t", "x")

    def test_more_passages_than_the_old_soft_limit_go_in_one_call_and_the_map_names_them(self):
        c = self.long_paper(14)
        ids = list(c.pieces)[:12]
        from tests.fake_openai import passage_of
        calls = {"agent": 0}

        def model(body):                     # the agent's turns, and the editing model's answers, from one script
            if body["text"]["format"]["name"] == "agent":
                calls["agent"] += 1
                return act("edit_text", "Tightening the first parts", chunks=[f"{ids[0]}-{ids[-1]}"], instruction="Cut the roundabout wording; keep every fact.") if calls["agent"] == 1 else act("finish", message="Done.")
            return {"approach": "Cut.", "revised_text": passage_of(body).replace("in a rather long and roundabout way, ", ""), "questions": [],
                    "changes": [{"before": "in a rather long and roundabout way, ", "after": "", "reason": "cut", "rule": ""}]}
        self.oa.script = [model] * 12
        r = self.send("shorten the whole paper, it is too verbose")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.assertNotIn("more than one call can take", " ".join(self.bodies()))
        self.assertTrue([m for m in self.texts() if m["kind"] == "result"], "a suggestion for the sections together")
        self.assertIn("Part 14", self.bodies()[0], "the map has every section")

    def test_a_page_the_author_linked_can_be_read_and_no_other(self):
        keep = scholar.fetch
        self.addCleanup(setattr, scholar, "fetch", keep)
        scholar.fetch = lambda url, *a, **k: ("<html><body><h1>Aims and scope</h1><p>" + "Reliability engineering and system safety of engineered systems. " * 12 + "</p></body></html>", url)
        link = "https://www.example.org/journal/ress"
        self.say(act("read_webpage", "Reading the journal page", query=link), act("read_webpage", "Reading another page", query="https://evil.example.com/x"),
                 act("finish", message="I read the journal's page."))
        r = self.send("improve the paper so that it fits this journal: " + link)
        self.wait_job(r["job"])
        self.assertIn("Aims and scope", self.bodies()[1])
        self.assertIn("I only read web addresses the author gave", self.bodies()[2])

    def test_when_the_steps_run_out_the_model_says_what_is_done_and_what_is_left(self):
        keep = agent.MAX_ACTIONS
        self.addCleanup(setattr, agent, "MAX_ACTIONS", keep)
        agent.MAX_ACTIONS = 3
        self.say(act("read_paper", "a", query="outline"), act("read_paper", "b", query="Study area"), act("read_paper", "c", query="results"),
                 act("finish", message="I read three parts and made no suggestion yet. Left: the whole paper. Say continue."))
        r = self.send("a complete review of the paper")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.assertIn("Say continue", self.texts()[-1]["text"])
        self.assertIn("<actions_left>0", self.bodies()[-1])
        self.assertIn("LAST action", self.bodies()[2])


class Memory(Base):
    """What the model has read stays in front of it for the whole request: a section read in several turns is not forgotten three turns later and read again (the cause of a request that
    only ever read, in the author's screenshot), and what is already in front of it is not sent twice."""

    def big_paper(self, n=4, size=3200):
        from pathlib import Path
        text = "\\documentclass{article}\n\\begin{document}\n" + "".join(
            f"\\section{{Method {i}}}\n" + " ".join(f"Step {i} of the procedure number {j} is carried out as described." for j in range(1, size // 55)) + "\n\n" for i in range(1, n + 1)) + "\\end{document}\n"
        (Path(self.proj["path"]) / "main.tex").write_text(text)
        paper_index.refresh(self.name, self.proj, trigger="test")
        return agent.Ctx(self.name, self.proj, "t", "x")

    def test_a_section_read_over_several_turns_is_still_in_front_of_the_model_at_the_end(self):
        c = self.big_paper(6, 1500)
        ids = list(c.pieces)
        self.say(*[act("read_paper", f"Reading part {i}", query=ids[i]) for i in range(6)], act("finish", message="Read it all."))
        r = self.send("revise the style of the methods, but also look for missing information that would make the methodology more robust")
        self.wait_job(r["job"])
        b = self.bodies()
        self.assertEqual(len(b), 7)
        for i in range(6):
            self.assertIn(f"[{ids[i]}] ", b[6], f"piece {ids[i]} was read at step {i + 1} and is still there at step 7")
        self.assertIn("Step 1 of the procedure number 26 is carried", b[6], "not only its first line: the last sentence of what was read at step 1 is still there")
        self.assertIn("The text is in <paper_read_so_far>", b[6], "the list of steps only says it was read: the text is not repeated in it")
        self.assertEqual(b[6].count(f"[{ids[0]}] "), 1, "and it is there once")

    def test_reading_what_is_in_front_of_it_again_returns_only_the_new_parts(self):
        c = self.big_paper(4, 800)
        ids = list(c.pieces)
        first, _ = agent.t_read_paper(c, {"query": f"{ids[0]}-{ids[1]}"})
        self.assertIn(f"[{ids[0]}] ", first)
        again, _ = agent.t_read_paper(c, {"query": f"{ids[0]}-{ids[3]}"})
        self.assertIn(f"Already in front of you: {ids[0]}-{ids[1]}", again)
        self.assertNotIn(f"[{ids[0]}] ", again, "what it has is not sent again")
        self.assertIn(f"[{ids[2]}] ", again)
        self.assertIn(f"[{ids[3]}] ", again)
        none, _ = agent.t_read_paper(c, {"query": f"{ids[0]}-{ids[3]}"})
        self.assertTrue(none.startswith("You already have"), none)
        self.assertNotIn("[C", none)

    def test_a_long_section_is_read_in_pages_and_the_next_page_starts_where_the_last_stopped(self):
        c = self.big_paper(4, 3200)
        ids = list(c.pieces)
        page1, _ = agent.t_read_paper(c, {"query": "Method"})
        self.assertIn("Not read yet (too long for one read)", page1.split("\n\n")[0])
        got1 = [k for k in ids if f"[{k}] " in page1]
        self.assertTrue(0 < len(got1) < len(ids), got1)
        page2, _ = agent.t_read_paper(c, {"query": "Method"})
        got2 = [k for k in ids if f"[{k}] " in page2]
        self.assertFalse(set(got1) & set(got2), "the second page is the next part, not the first again")
        self.assertIn("Already in front of you", page2.split("\n\n")[0])
        while len(set(got1) | set(got2)) < len(ids):
            more, _ = agent.t_read_paper(c, {"query": "Method"})
            new = [k for k in ids if f"[{k}] " in more]
            self.assertTrue(new, more[:200])
            got2 += new

    def test_when_there_is_too_much_the_oldest_goes_first_and_can_be_read_again(self):
        keep = agent.KEEP_READ
        self.addCleanup(setattr, agent, "KEEP_READ", keep)
        agent.KEEP_READ = 5000
        c = self.big_paper(3, 2500)
        ids = list(c.pieces)
        for k in ids:
            agent.t_read_paper(c, {"query": k})
        self.assertFalse(c.has_read(ids[0]), "the oldest was let go")
        self.assertTrue(c.has_read(ids[-1]))
        again, _ = agent.t_read_paper(c, {"query": ids[0]})
        self.assertIn(f"[{ids[0]}] ", again)

    def test_a_request_that_only_reads_is_told_to_do_the_work(self):
        c = self.big_paper(8, 600)
        ids = list(c.pieces)
        self.say(*[act("read_paper", f"Reading {i}", query=ids[i]) for i in range(7)], act("finish", message="Done."))
        r = self.send("revise the style of the methods, but also look for missing information that would make the methodology more robust")
        self.wait_job(r["job"])
        b = self.bodies()
        self.assertNotIn("have not written anything yet", b[4], "five steps of reading are not too many")
        self.assertIn("you have spent 6 steps reading and have not written anything yet", b[6])
        self.assertIn("<paper_read_so_far>", b[6])


class Files(Base):
    """The author's own code, results and documents: listed to the agent, searched and read (read-only, part by part, kept in front of it), and named to the writing tools so that what
    they write rests on them. Nothing outside the paper's project and the folders the author linked, nothing the author switched off, no path of the author's computer."""

    def setUp(self):
        super().setUp()
        from pathlib import Path
        from tests.test_analysis import make_analysis
        self.dir = make_analysis(Path(self.tmp) / "work" / "analysis")
        from writing_core import files
        files.set_folders(self.proj, [str(self.dir)], "analysis")
        paper_index.refresh(self.name, self.proj, trigger="files")
        self.ctx = agent.Ctx(self.name, self.proj, "t", "x")

    def test_the_agent_is_shown_which_files_it_may_read_by_short_names(self):
        ctx = self.ctx
        labels = [f["label"] for f in ctx.file_list()]
        self.assertIn("analysis/train.py", labels)
        self.assertIn("analysis/results/metrics.csv", labels)
        self.assertFalse([l for l in labels if "secret" in l or "config.py" in l], "secrets and hidden folders are not there")
        block = ctx.context()
        self.assertIn("<files", block)
        self.assertIn("analysis/results/summary.json | code and results", block)
        self.assertNotIn(str(self.dir), block, "the folder of the author's computer never goes out: only its name")
        self.assertNotIn(str(self.tmp), block)

    def test_a_term_is_found_in_the_files_with_its_file_and_part(self):
        out, _ = agent.t_search_files(self.ctx, {"query": "folds"})
        self.assertIn("analysis/results/summary.json #1", out)
        self.assertIn("folds", out)
        out, _ = agent.t_search_files(self.ctx, {"query": "zebra migration"})
        self.assertIn("Nothing in the author's files matches", out)
        out, _ = agent.t_search_files(self.ctx, {"query": "the of"})
        self.assertIn("Say what to look for", out)

    def test_a_file_is_read_by_name_and_stays_in_front_of_the_model(self):
        ctx = self.ctx
        out, _ = agent.t_read_file(ctx, {"query": "train.py"})
        self.assertTrue(out.startswith("Read: file analysis/train.py"), out[:80])
        self.assertIn("def fit(x)", out)
        self.assertIn("<paper_read_so_far>", ctx.read_block())
        self.assertIn("def fit(x)", ctx.read_block())
        again, _ = agent.t_read_file(ctx, {"query": "analysis/train.py"})
        self.assertTrue(again.startswith("You already have"), again)
        self.assertNotIn("def fit(x)", again)

    def test_parts_can_be_asked_for_and_are_listed_in_order(self):
        ctx = self.ctx
        part, _ = agent.t_read_file(ctx, {"query": "run.log #1"})
        self.assertIn("Read: file analysis/results/run.log (part 1 of", part)
        ctx.remember("F:b.py#10", "[b.py #10] ten")
        ctx.remember("F:b.py#2", "[b.py #2] two")
        block = ctx.read_block()
        self.assertLess(block.index("[b.py #2]"), block.index("[b.py #10]"), "parts are in numeric order, not as text")
        self.assertLess(block.index("[analysis/results/run.log #1]"), block.index("[b.py #2]"))

    def test_a_name_that_matches_nothing_or_many_files_is_answered_with_the_list_and_never_a_path(self):
        ctx = self.ctx
        none, _ = agent.t_read_file(ctx, {"query": "../../etc/passwd"})
        self.assertIn("None of the author's files is called", none)
        self.assertIn("analysis/train.py", none)
        many, _ = agent.t_read_file(ctx, {"query": "results/"})
        self.assertIn("More than one file fits", many)
        some, _ = agent.t_read_file(ctx, {"query": "metrics"})
        self.assertIn("Read: file analysis/results/metrics.csv", some)
        listing, _ = agent.t_read_file(ctx, {"query": "list"})
        self.assertIn("analysis/notebooks/explore.ipynb", listing)

    def test_what_the_author_switched_off_is_not_read(self):
        config.set_paper_setting(self.proj, "use_analysis", "never")
        ctx = agent.Ctx(self.name, self.proj, "t", "x")
        self.assertEqual(ctx.file_list(), [])
        self.assertNotIn("<files", ctx.context())
        out, _ = agent.t_read_file(ctx, {"query": "train.py"})
        self.assertIn("switched off in Settings", out)
        out, _ = agent.t_search_files(ctx, {"query": "folds"})
        self.assertIn("switched off in Settings", out)

    def test_it_looks_in_the_files_then_writes_a_passage_that_rests_on_them_and_says_what_it_read(self):
        self.say(act("search_files", "Looking for the number of folds", query="folds"),
                 act("read_file", "Reading the results summary", query="summary.json"),
                 act("edit_text", "Adding the validation detail", chunks=[self.study], files=["summary.json"], instruction="Add one sentence saying how many folds the validation used."),
                 reply(("Each region has a distinct climate and soil.", "Each region has a distinct climate and soil, and the model was validated with 5 folds.")),
                 act("finish", message="The methods did not say how the model was validated. summary.json shows 5 folds; I suggested one sentence saying so. I read summary.json."))
        r = self.send("add anything missing about the validation to the methods")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        b = self.bodies()
        self.assertIn("<files", b[0])
        self.assertIn("analysis/results/summary.json", b[0])
        from tests.test_revise import user_of
        writing = user_of([r["body"] for r in self.oa.requests if r.get("body")][3])
        self.assertIn("<source_material>", writing, "the writing tool is given the author's file as its source")
        self.assertIn('"folds": 5', writing, "with the numbers of the file")
        self.assertIn("[analysis/results/summary.json", writing, "named by the folder's name and the path inside it")
        self.assertNotIn(str(self.dir), writing, "the writing tool does not see the path on the author's computer either")
        self.assertNotIn(str(self.tmp), " ".join(self.bodies()), "and nothing that goes to the model holds it")
        self.assertIn("The passage must rest on these files of the author", writing)
        self.assertIn("summary.json", writing)
        res = [m for m in self.texts() if m["kind"] == "result"]
        self.assertTrue(res, "a suggestion was made")
        self.assertIn("5 folds", self.texts()[-1]["text"])
        self.assertIn("I read summary.json", self.texts()[-1]["text"])
        steps = [x["label"] for x in self.texts()[-1]["data"]["steps"]]
        self.assertIn("Looking for the number of folds", steps)


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


if __name__ == "__main__":
    unittest.main()
