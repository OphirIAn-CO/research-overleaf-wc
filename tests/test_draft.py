"""New text written from the author's own sources: where it goes, what is sent, what is refused, and how it is saved between two lines."""
import json
import unittest
from pathlib import Path

from tests.test_analysis import make_analysis
from tests.test_book import needs_pdf
from tests.test_revise import ReviseBase, user_of
from tests.test_v2_chat_api import ChatBase
from writing_core import chat, config, draft, files, library, paper_index, revise

RESULTS_TEXT = ("The best model reached an RMSE of 0.412 on the held-out farms, against 0.588 for the recurrent baseline, as Table~[[LX910]] shows.\n\n"
                "Training loss fell from 0.31 to 0.24 between epochs 10 and 20.")
BASIS = [{"statement": "The best model reached an RMSE of 0.412", "source": "S1"}, {"statement": "Training loss fell from 0.31", "source": "S2"}]


def answer(text=RESULTS_TEXT, basis=None, questions=None, approach="The paragraphs report the error of each model and how training went."):
    return {"approach": approach, "draft_text": text, "basis": BASIS if basis is None else basis, "questions": questions or []}


@needs_pdf
class Drafting(ReviseBase):
    ASK = "write the results from my analysis"

    def setUp(self):
        super().setUp()
        self.live()
        self.dir = make_analysis(self.tmp / "work" / "analysis")
        files.set_folders(self.proj, [str(self.dir)], "analysis")
        paper_index.refresh(self.name, self.proj, trigger="files")
        self.main = Path(self.proj["path"]) / "main.tex"

    def locate(self, text=None):
        return draft.locate(self.name, self.proj, text or self.ASK)

    def write(self, text=None, **kw):
        loc = self.locate(text)
        self.assertEqual(loc["status"], "ready", loc)
        text = text or self.ASK
        pl = draft.plan(self.name, self.proj, text, loc["anchor"], loc["part"])
        return loc, draft.run(self.name, self.proj, text, loc["anchor"], loc["part"], confirm=pl["payload_hash"], **kw)

    # ---- where
    def test_it_finds_the_place_in_the_paper_for_what_the_author_asked(self):
        loc = self.locate()
        lines = self.main.read_text().split("\n")
        end = next(i for i, ln in enumerate(lines, 1) if ln.startswith("\\end{table}"))
        self.assertEqual((loc["part"], loc["anchor"]["file"], loc["anchor"]["mode"]), ("results", "main.tex", "at the end"))
        self.assertIn("at the end of the results", loc["where"])
        start = self.locate("write the results from my analysis at the start of the results")
        self.assertEqual(start["anchor"]["mode"], "at the start")
        heading = next(i for i, ln in enumerate(lines, 1) if ln.startswith("\\section{Results}"))
        self.assertEqual(start["anchor"]["after_line"], heading)
        self.assertLess(start["anchor"]["after_line"], loc["anchor"]["after_line"])

    def test_before_or_after_a_figure_or_table_and_the_cases_that_need_a_question(self):
        after = self.locate("add a paragraph on the results after Table 1")
        lines = self.main.read_text().split("\n")
        end = next(i for i, ln in enumerate(lines, 1) if ln.startswith("\\end{table}"))
        self.assertEqual((after["status"], after["anchor"]["after_line"], after["where"]), ("ready", end, f"after Table 1 (after line {end} of main.tex)"))
        before = self.locate("add a paragraph on the results before Table 1")
        self.assertEqual(before["anchor"]["after_line"], next(i for i, ln in enumerate(lines, 1) if ln.startswith("\\begin{table}")) - 1)
        self.assertEqual(self.locate("draft a new paragraph")["status"], "ask")
        self.assertEqual(self.locate("add a paragraph on the results after Figure 7")["status"], "ask")
        new = self.locate("write the limitations")                  # the paper has no limitations section: it is made, with its heading, where it belongs
        self.assertEqual((new["status"], new["anchor"]["heading"]), ("ready", "\\section{Limitations}"))
        self.assertTrue(new["anchor"]["mode"].startswith("as a new limitations section after the "), new["anchor"]["mode"])

    def test_a_section_that_already_has_text_is_added_to_not_replaced(self):
        long = "\n".join(["Yield rose with moisture in every region of the study area and in every season we measured it. " * 3] * 2)
        text = self.main.read_text().replace("Yield rose with moisture.", long)
        self.main.write_text(text)
        loc = self.locate("write the discussion of the results")
        self.assertEqual(loc["part"], "discussion")
        self.assertGreater(loc["existing_words"], 80)
        self.assertIn("nothing already there changes", loc["notes"][0])

    # ---- what is sent
    def test_the_plan_shows_the_sources_and_changes_with_them_and_never_sends(self):
        loc = self.locate()
        pl = draft.plan(self.name, self.proj, self.ASK, loc["anchor"], loc["part"])
        self.assertEqual(self.srv.requests, [])
        names = {s["file"].rsplit("/", 1)[-1] for s in pl["sources"]}
        self.assertTrue({"metrics.csv", "summary.json", "run.log"} <= names, names)
        self.assertEqual(sorted(x["name"] for x in pl["pictures"]), ["loss_curve.jpg", "rmse_by_model.png"])
        self.assertEqual(pl["label"], "Results")
        self.assertNotIn("base64,", json.dumps(pl["payload"]))
        body = draft.prepare(self.name, self.proj, self.ASK, loc["anchor"], loc["part"])["body"]
        user = openai_text(body)
        self.assertIn("[S1]", user)
        self.assertIn("<usable_tags>\n[[LX910]] = the number of Table 1 (caption: Means.)", user)
        self.assertIn("<book_rules>", user)
        self.assertIn("rmse_by_model.png", user)
        self.assertIn("<surrounding_text>", user)
        self.assertIn("Yield rose" if "Yield rose" in self.main.read_text().split("\\section{Results}")[1] else "results here", user)
        pl2 = draft.plan(self.name, self.proj, self.ASK, loc["anchor"], loc["part"], facts="Answer: 40 farms.")
        self.assertNotEqual(pl["payload_hash"], pl2["payload_hash"])

    def test_the_hash_must_match_before_anything_is_sent(self):
        loc = self.locate()
        r = draft.run(self.name, self.proj, self.ASK, loc["anchor"], loc["part"], confirm="deadbeef")
        self.assertEqual(r["status"], "preview")
        self.assertEqual(self.srv.requests, [])

    def test_the_papers_switches_decide_what_is_used(self):
        config.set_paper_setting(self.proj, "use_analysis", "never")
        loc = self.locate()
        pl = draft.plan(self.name, self.proj, self.ASK, loc["anchor"], loc["part"])
        self.assertEqual((pl["sources"], pl["pictures"]), ([], []))

    # ---- what comes back
    def test_a_draft_that_rests_on_the_sources_becomes_a_suggestion_to_insert(self):
        self.srv.script = [answer()]
        loc, r = self.write()
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        prop = r["proposal"]
        u = prop["units"][0]
        a = loc["anchor"]["after_line"]
        self.assertEqual((prop["kind"], u["insert"], u["line_start"], u["line_end"], u["original_text"]), ("draft", True, a + 1, a, ""))
        self.assertEqual(len(u["hunks"]), 2, "one change per paragraph, so some can be kept and others left")
        self.assertEqual(u["hunks"][0]["rests_on"], ["S1"])
        self.assertIn("Table~\\ref{tab:a}", u["hunks"][0]["new"])
        self.assertEqual(u["new_tags"], ["LX910"])
        self.assertIn("0.412", u["from_files"])
        self.assertEqual([b["source"] for b in u["basis"]], ["S1", "S2"])
        body = self.srv.requests[-1]["body"]
        self.assertIn("never read an exact value off a picture", body["instructions"])
        self.assertEqual(body["text"]["format"]["name"], "draft")

    def test_saving_inserts_the_text_between_the_two_lines_with_a_blank_line_each_side(self):
        self.srv.script = [answer()]
        loc, r = self.write()
        before = self.main.read_text().split("\n")
        a = loc["anchor"]["after_line"]
        res = revise.apply(self.name, self.proj, r["proposal"]["id"], accept="all", unclean_ok=True)
        self.assertEqual(res["status"], "applied")
        after = self.main.read_text().split("\n")
        new = RESULTS_TEXT.replace("[[LX910]]", "\\ref{tab:a}").split("\n")
        pad_before = [""] if before[a - 1].strip() else []
        pad_after = [""] if before[a].strip() else []
        self.assertEqual(after, before[:a] + pad_before + new + pad_after + before[a:])
        self.assertNotIn("\n\n\n", "\n".join(after[a - 2:a + len(new) + 4]))

    def test_only_the_paragraphs_the_author_keeps_are_inserted(self):
        self.srv.script = [answer()]
        loc, r = self.write()
        keep = [h["uid"] for h in r["proposal"]["units"][0]["hunks"]][1:]
        revise.apply(self.name, self.proj, r["proposal"]["id"], accept=keep, unclean_ok=True)
        text = self.main.read_text()
        self.assertIn("Training loss fell", text)
        self.assertNotIn("0.588", text)
        self.assertNotIn("\n\n\n\n", text)

    def test_if_the_place_changed_since_it_was_drafted_nothing_is_written(self):
        self.srv.script = [answer()]
        loc, r = self.write()
        lines = self.main.read_text().split("\n")
        a = loc["anchor"]["after_line"]
        lines.insert(a, "A new sentence typed in Overleaf meanwhile.")
        self.main.write_text("\n".join(lines))
        with self.assertRaises(revise.ReviseError) as cm:
            revise.apply(self.name, self.proj, r["proposal"]["id"], accept="all", unclean_ok=True)
        self.assertIn("changed since", str(cm.exception))
        self.assertNotIn("0.412", self.main.read_text())

    def test_the_authors_own_version_is_checked_like_any_other_and_may_use_the_numbers_of_the_sources(self):
        self.srv.script = [answer()]
        loc, r = self.write()
        pid, u = r["proposal"]["id"], r["proposal"]["units"][0]
        mine = revise.unit_views(u)["suggested_masked"].replace("held-out farms", "test farms")
        ok = revise.check_adopted(self.proj, pid, {u["unit_id"]: mine})[u["unit_id"]]
        self.assertTrue(ok["ok"], ok["messages"])
        worse = revise.check_adopted(self.proj, pid, {u["unit_id"]: mine.replace("0.412", "0.999")})[u["unit_id"]]
        self.assertFalse(worse["ok"])
        invented = revise.check_adopted(self.proj, pid, {u["unit_id"]: mine + " [[LX999]]"})[u["unit_id"]]
        self.assertFalse(invented["ok"])
        self.assertEqual(revise.adopt(self.name, self.proj, pid, {u["unit_id"]: mine})["ok"], True)
        revise.apply(self.name, self.proj, pid, accept="all", unclean_ok=True)
        self.assertIn("test farms", self.main.read_text())

    # ---- what is refused
    def test_a_number_that_is_in_no_source_is_refused_even_after_a_second_try(self):
        bad = answer("The best model reached an RMSE of 0.777 on the held-out farms.")
        self.srv.script = [bad, bad]
        loc, r = self.write()
        self.assertEqual(r["status"], "failed")
        self.assertIn("0.777", r["proposal"]["units"][0]["error"])
        self.assertEqual(len(self.srv.requests), 2)
        self.assertIn("previous_attempt_rejected", user_of(self.srv.requests[-1]["body"]))

    def test_a_tag_that_was_not_offered_headings_and_lists_are_refused(self):
        for text, why in (("The error was 0.412 as Table~[[LX955]] shows.", "invented"), ("# Results\n\nThe error was 0.412.", "plain prose"), ("- The error was 0.412\n- The loss was 0.24", "plain prose"),
                          ("The error was 0.412 \\textbf{here}.", "commands")):
            self.srv.script = [answer(text), answer(text)]
            loc, r = self.write()
            self.assertEqual(r["status"], "failed", text)
            self.assertIn(why, r["proposal"]["units"][0]["error"].replace("_", " "), text)

    def test_a_dash_and_an_over_long_draft_are_refused(self):
        self.srv.script = [answer("The error was 0.412 — the lowest."), answer("The error was 0.412 — the lowest.")]
        self.assertEqual(self.write()[1]["status"], "failed")
        long = " ".join(["The error was 0.412 on the held-out farms."] * 130)
        self.srv.script = [answer(long), answer(long)]
        self.assertIn("draft length", self.write()[1]["proposal"]["units"][0]["error"].replace("_", " "))

    def test_a_question_from_the_model_and_an_answer_of_the_author_change_what_may_be_said(self):
        self.srv.script = [answer("The error was 0.412 across 77 farms.", questions=["How many farms were held out?"]), answer("The error was 0.412 across 77 farms.", questions=["How many farms were held out?"])]
        loc, r = self.write()
        self.assertEqual(r["status"], "failed", "77 is in no source")
        self.srv.script = [answer("The error was 0.412 across 77 farms.", questions=[])]
        loc = self.locate()
        pl = draft.plan(self.name, self.proj, self.ASK, loc["anchor"], loc["part"], facts="Answer: 77 farms were held out.")
        r = draft.run(self.name, self.proj, self.ASK, loc["anchor"], loc["part"], facts="Answer: 77 farms were held out.", confirm=pl["payload_hash"])
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        self.assertIn("<author_answers>\nAnswer: 77 farms were held out.", user_of(self.srv.requests[-1]["body"]))

    def test_a_model_that_takes_no_pictures_is_asked_again_without_them(self):
        refuse = (400, {"error": {"message": "This model does not support image inputs", "code": "invalid_request"}})
        self.srv.script = [refuse, answer()]
        loc, r = self.write()
        self.assertEqual(r["status"], "proposed")
        self.assertTrue(r["proposal"]["units"][0]["no_pictures"])
        self.assertTrue(any("would not take pictures" in w for w in r["proposal"]["warnings"]))

    def test_a_citation_from_the_library_is_added_only_through_its_tag(self):
        from tests.pdfmaker import make_pdf
        from tests.test_library import SOIL
        lib = self.tmp / "Documents" / "Literature"
        lib.mkdir(parents=True)
        make_pdf(lib / "Smith et al. - 2020 - Soil moisture.pdf", SOIL)
        library.set_folders([str(lib)])
        library.update()
        bib = Path(self.proj["path"]) / self.proj["bib"]
        bib.write_text(bib.read_text() + "@article{smithetal2020soil, author={Smith, J.}, title={Soil moisture and crop yield in semi-arid farms}, year={2020}, doi={10.1234/abc.2020.001}}\n")
        ask = "write the results from my analysis and cite my library"
        loc = self.locate(ask)
        user = openai_text(draft.prepare(self.name, self.proj, ask, loc["anchor"], loc["part"])["body"])
        self.assertIn("[[LX901]] = a citation of L1", user)
        self.srv.script = [answer("The error was 0.412, in line with earlier work on soil moisture [[LX901]].", basis=[{"statement": "The error was 0.412", "source": "S1"}])]
        pl = draft.plan(self.name, self.proj, ask, loc["anchor"], loc["part"])
        r = draft.run(self.name, self.proj, ask, loc["anchor"], loc["part"], confirm=pl["payload_hash"])
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        self.assertEqual(r["proposal"]["units"][0]["added_cites"], ["\\cite{smithetal2020soil}"])
        revise.apply(self.name, self.proj, r["proposal"]["id"], accept="all", unclean_ok=True)
        self.assertIn("soil moisture \\cite{smithetal2020soil}.", self.main.read_text())


class ThroughTheChat(ChatBase):
    """Asking in the chat: a plan that says what it rests on, a suggestion that inserts, a revised draft after the author answers a question."""

    def setUp(self):
        super().setUp()
        self.dir = make_analysis(self.tmp / "work" / "analysis")
        st, r = self.c.post("/api/files/folders", {"paper": "alpha", "folders": [str(self.dir)], "role": "analysis"})
        self.assertEqual(st, 200, r)
        self.oa.script = [answer()] * 4

    def test_the_request_becomes_a_plan_then_a_suggestion_that_inserts_new_text(self):
        r = self.send("write the results from my analysis")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "draft_plan"])
        plan = r["messages"][1]
        self.assertEqual(plan["data"]["state"], "sent")
        self.assertIn("at the end of the results", plan["data"]["where"])
        self.assertTrue(any(s["file"].endswith("metrics.csv") for s in plan["data"]["plan"]["sources"]))
        self.assertEqual(len(plan["data"]["plan"]["pictures"]), 2)
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        self.assertEqual([s["key"] for s in j["steps"]], ["read", "sources", "ask", "check", "finish"])
        res = self.last("result")
        self.assertTrue(res["data"]["draft"])
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={res['data']['proposal']}")
        u = prop["units"][0]
        self.assertEqual((prop["kind"], u["insert"], len(u["hunks"])), ("draft", True, 2))
        self.assertNotIn("mapping", u)
        self.assertIn("Table~\\ref{", u["views"]["suggested_latex"])
        before = self.main.read_text()
        st, ok = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": prop["id"], "chat": self.chat_thread()["id"], "unclean_ok": True})
        self.assertEqual(st, 200, ok)
        after = self.main.read_text()
        self.assertIn("The best model reached an RMSE of 0.412", after)
        self.assertEqual(after.count("\n\n\n"), before.count("\n\n\n"), "the new text is set off by single blank lines")

    def test_with_confirmation_on_it_waits_and_can_be_sent_or_cancelled(self):
        config.set_paper_setting(self.proj, "confirm_before_send", True)
        r = self.send("write the results from my analysis")
        plan = r["messages"][1]
        self.assertEqual((plan["kind"], plan["data"].get("state"), r.get("job")), ("draft_plan", "waiting", None), plan)
        self.assertEqual(self.oa.requests, [])
        st, c = self.c.post("/api/chat/cancel", {"paper": "alpha", "id": self.chat_thread()["id"], "message": plan["id"]})
        self.assertEqual(st, 200, c)
        r = self.send("write the results from my analysis")
        st, run = self.c.post("/api/chat/run", {"paper": "alpha", "id": self.chat_thread()["id"], "message": r["messages"][1]["id"]})
        self.assertEqual(st, 200, run)
        self.assertEqual(self.wait_job(run["job"])["status"], "done")
        self.assertEqual(self.last("result")["data"]["status"], "proposed")

    def test_a_missing_section_is_made_and_a_missing_place_is_asked_about_and_nothing_is_sent(self):
        r = self.send("write the limitations")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "draft_plan"])
        self.assertIn("as a new limitations section after the ", r["messages"][1]["data"]["where"])
        self.assertIn("\\section{Limitations}", r["messages"][1]["data"]["notes"][0])
        self.wait_job(r["job"])
        del self.oa.requests[:]
        r = self.send("draft a new paragraph")
        self.assertIn("Which part of the paper", r["messages"][1]["text"])
        self.assertEqual(self.oa.requests, [])

    def test_an_answer_to_the_models_question_writes_it_again_for_the_same_place(self):
        self.oa.script = [answer(questions=["How many farms were held out?"])]
        r = self.send("write the results from my analysis")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        old = self.last("result")["data"]["proposal"]
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={old}")
        self.assertEqual(prop["units"][0]["questions"], ["How many farms were held out?"])
        self.oa.script = [answer("The error was 0.412 across 77 farms.")]
        r = self.send("write the results from my analysis", facts="Question: How many farms were held out?\nAnswer: 77 farms.", redraft=old)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "draft_plan"])
        self.assertEqual(r["messages"][1]["data"]["revises"], old)
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        new = self.last("result")
        self.assertEqual((new["data"]["revises"], new["data"]["replaced"]), (old, True))
        self.assertEqual(self.c.get(f"/api/proposal?paper=alpha&id={old}")[1]["status"], "superseded")
        self.assertIn("Answer: 77 farms.", self.oa.requests[-1]["body"]["input"][0]["content"][0]["text"])
        self.assertEqual(self.c.post("/api/proposal/approve", {"paper": "alpha", "id": old, "unclean_ok": True})[0], 409, "the replaced one can no longer be saved")


def openai_text(body):
    from writing_core import openai_client
    return openai_client.user_text(body)


class Words(unittest.TestCase):
    def test_writing_new_text_is_told_apart_from_editing_what_is_there(self):
        for t in ("write the results from my analysis", "Draft the discussion of these results", "explain the results in the discussion", "add a paragraph about the sampling to the methods",
                  "generate the results section based on my code and figures"):
            self.assertEqual(chat.intent(t), "draft", t)
        for t in ("rewrite the results", "improve the results", "fix grammar in the discussion", "add citations to the introduction", "write a peer review of my paper"):
            self.assertNotEqual(chat.intent(t), "draft", t)
        self.assertEqual(chat.draft_part("explain the results in the discussion"), "discussion")
        self.assertEqual(chat.draft_part("write the discussion of these results"), "discussion")
        self.assertEqual(chat.draft_part("write the results from my code"), "results")
        self.assertIsNone(chat.draft_part("write something"))


if __name__ == "__main__":
    unittest.main()
