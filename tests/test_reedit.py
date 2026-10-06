"""Writing a part again without starting over: only the passages that were answered or ticked are asked again (the others are carried as they are, in one suggestion), what the author said in earlier
rounds comes along (and a question they skipped is not asked again), an answer that is cut off is asked again with more room, and asking for a shorter text is not what gets an answer thrown away."""
import json
import unittest
from pathlib import Path

from tests.fake_openai import passage_of
from tests.test_book import needs_pdf
from tests.test_revise import ReviseBase, reply, user_of
from tests.test_v2_chat_api import ChatBase
from writing_core import config, paper_index as pi, revise

FILLER = " ".join(f"The step number {i} of the procedure is carried out in the way that was described before." for i in range(1, 45))


def doc():
    out = ["\\documentclass{article}", "\\begin{document}", ""]
    for name in ("Alpha", "Beta", "Gamma"):
        out += [f"\\section{{{name}}}", f"The {name.lower()} marker sentence is very wordy and long indeed. {FILLER}", ""]
    out.append("\\end{document}")
    return "\n".join(out)


@needs_pdf
class Base(ReviseBase):
    def setUp(self):
        super().setUp()
        config.set_paper_setting(self.proj, "dry_run", False)
        (Path(self.proj["path"]) / "main.tex").write_text(doc())
        pi.refresh(self.name, self.proj, trigger="test")
        got = [c for c in pi.list_chunks(self.name, self.proj)["chunks"] if c["kind"] == "tex" and c["file"] == "main.tex"]
        self.ids = {n: next(c["id"] for c in got if n in (c.get("section_path") or "")) for n in ("Alpha", "Beta", "Gamma")}
        self.all = list(self.ids.values())

    def first(self, *pairs, questions=None):
        """A whole suggestion for the three passages, each answered by the same script."""
        self.srv.script = [reply(*pairs, questions=questions) for _ in range(3)]
        return self.go("tighten", chunk_ids=self.all)["proposal"]


class Partial(Base):
    def test_three_passages_are_three_units(self):
        prop = self.first(("very wordy and long indeed", "wordy"))
        self.assertEqual([u["unit_id"] for u in prop["units"]], ["u1", "u2", "u3"])
        self.assertEqual([u["chunk_ids"] for u in prop["units"]], [[self.ids["Alpha"]], [self.ids["Beta"]], [self.ids["Gamma"]]], "each passage knows which pieces of the paper it is")

    def test_only_the_ticked_passage_is_asked_again_and_the_others_stay_as_they_were(self):
        old = self.first(("very wordy and long indeed", "wordy"))
        self.srv.script = [reply(("very wordy and long indeed", "plainly wordy"))]
        n = len(self.srv.requests)
        new = self.go("tighten", chunk_ids=[self.ids["Beta"]], facts="Answer: ok")["proposal"]
        self.assertEqual(len(self.srv.requests) - n, 1, "one passage, one call: not three")
        merged = revise.carry_over(self.proj, old["id"], new["id"], ["u1", "u3"])
        self.assertEqual([u["section_path"].split(" > ")[-1] for u in merged["units"]], ["Alpha", "Beta", "Gamma"], "in the order of the paper")
        by = {u["section_path"].split(" > ")[-1]: u for u in merged["units"]}
        self.assertTrue(by["Alpha"]["carried"] and by["Gamma"]["carried"] and not by["Beta"].get("carried"))
        self.assertIn("plainly wordy", by["Beta"]["hunks"][0]["new"])
        self.assertIn("wordy", by["Alpha"]["hunks"][0]["new"])
        self.assertNotIn("plainly", by["Alpha"]["hunks"][0]["new"], "the passage that was not asked again is exactly what it was")
        uids = [h["uid"] for u in merged["units"] for h in u["hunks"]]
        self.assertEqual(len(uids), len(set(uids)), "no change can be mistaken for another")
        self.assertEqual(merged["status"], "proposed")

    def test_the_merged_suggestion_can_be_saved_and_writes_all_three_passages(self):
        old = self.first(("very wordy and long indeed", "wordy"))
        self.srv.script = [reply(("very wordy and long indeed", "plainly wordy"))]
        new = self.go("tighten", chunk_ids=[self.ids["Beta"]], facts="Answer: ok")["proposal"]
        merged = revise.carry_over(self.proj, old["id"], new["id"], ["u1", "u3"])
        self.assertTrue(revise.supersede(self.proj, old["id"], new["id"]))
        res = revise.apply(self.name, self.proj, merged["id"], accept="all", unclean_ok=True)
        self.assertEqual(res["status"], "applied")
        text = (Path(self.proj["path"]) / "main.tex").read_text()
        self.assertIn("The alpha marker sentence is wordy.", text)
        self.assertIn("The beta marker sentence is plainly wordy.", text)
        self.assertIn("The gamma marker sentence is wordy.", text)
        with self.assertRaises(revise.ReviseError):
            revise.apply(self.name, self.proj, old["id"], accept="all", unclean_ok=True)           # the replaced one cannot be saved

    def test_a_passage_that_fails_when_asked_again_keeps_the_suggestion_the_author_had(self):
        old = self.first(("very wordy and long indeed", "wordy"))
        # the model answers with a number that is nowhere in the passage, twice: the passage fails its check
        bad = reply(("very wordy and long indeed", "wordy in 1987"))
        self.srv.script = [bad, bad]
        new = self.go("tighten", chunk_ids=[self.ids["Beta"]])["proposal"]
        self.assertEqual(new["status"], "failed")
        # the others were not asked again; with one of them kept the failed one falls back to the earlier suggestion
        new2 = json.loads(json.dumps(new))
        merged = revise.carry_over(self.proj, old["id"], new["id"], ["u1"])
        names = {u["section_path"].split(" > ")[-1]: u for u in merged["units"]}
        self.assertEqual(sorted(names), ["Alpha", "Beta"], names)
        self.assertTrue(names["Beta"]["carried"], "the passage that could not be written again is the earlier one")
        self.assertTrue(any("could not write" in w and "Beta" in w for w in merged["warnings"]), merged["warnings"])
        self.assertEqual(merged["status"], "proposed")
        self.assertTrue(new2)


class Answers(Base):
    def test_what_was_said_in_earlier_rounds_comes_along_and_a_skipped_question_is_not_dropped(self):
        a = "Question: How long did it last?\nAnswer: 180 days."
        b = "Question: Which software?\nAnswer: (skipped: the author chose not to answer. Do not ask it again; leave that point as it is in the passage and do not guess.)"
        merged = revise.merge_facts(a, b)
        self.assertEqual(merged, a + "\n\n" + b)
        c = "Question: Which software?\nAnswer: R 4.3"
        again = revise.merge_facts(merged, c)
        self.assertIn("R 4.3", again)
        self.assertNotIn("skipped", again, "answering a question that was skipped replaces the skip")
        self.assertIn("180 days", again)
        self.assertEqual(revise.merge_facts(a, a), a, "the same thing said twice is kept once")

    def test_when_there_is_too_much_the_oldest_goes_first(self):
        old = "Question: first\nAnswer: " + "x" * 2000
        new = "Question: second\nAnswer: " + "y" * 2000
        merged = revise.merge_facts(old, new, limit=3000)
        self.assertIn("second", merged)
        self.assertNotIn("first", merged)

    def test_the_model_is_told_not_to_ask_again_what_it_was_told_or_what_was_skipped(self):
        self.srv.script = [reply(("very wordy and long indeed", "wordy"))]
        self.go("tighten", chunk_ids=[self.ids["Alpha"]], facts="Question: x?\nAnswer: y")
        system = self.srv.requests[-1]["body"].get("instructions") or ""
        self.assertIn("never ask what they already answer", system)
        self.assertIn("A question that stands there without an answer was skipped on purpose: do not ask it again", system)
        self.assertIn("at most three", system)


class Dead_ends(Base):
    def incomplete(self, body):
        return (200, {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": [], "usage": {}})

    def test_an_answer_that_is_cut_off_is_asked_again_with_twice_the_room(self):
        self.srv.script = [self.incomplete, reply(("very wordy and long indeed", "wordy"))]
        r = self.go("tighten", chunk_ids=[self.ids["Alpha"]])
        sizes = [q["body"]["max_output_tokens"] for q in self.srv.requests if q.get("body")]
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        self.assertEqual(len(sizes), 2)
        self.assertEqual(sizes[1], min(revise.OUT_CAP, sizes[0] * 2))
        self.assertEqual(r["proposal"]["units"][0]["retries"], 1)

    def test_it_keeps_asking_for_more_room_up_to_the_cap_and_then_says_what_to_do(self):
        self.srv.script = [self.incomplete] * 12
        r = self.go("tighten", chunk_ids=[self.ids["Alpha"]])
        unit = r["proposal"]["units"][0]
        sizes = [q["body"]["max_output_tokens"] for q in self.srv.requests if q.get("body")]
        self.assertEqual(sizes[-1], revise.OUT_CAP)
        self.assertEqual(sizes, sorted(sizes))
        self.assertEqual(unit["status"], "failed")
        self.assertIn("did not fit even when I allowed it as much room as I can", unit["error_plain"])
        self.assertNotIn("max_output_tokens", unit["error_plain"], "no instruction to edit a file")

    def test_a_long_passage_is_given_more_room_from_the_start(self):
        cfg = config.openai_cfg()
        self.assertEqual(revise.out_budget(cfg, "short text"), cfg["max_output_tokens"])
        long_text = "word " * 1800
        self.assertGreater(revise.out_budget(cfg, long_text), cfg["max_output_tokens"])
        self.assertLessEqual(revise.out_budget(cfg, long_text * 10), revise.OUT_CAP)

    def cut_to_a_third(self, body):
        text = passage_of(body)
        keep = text[: len(text) // 3].rsplit(" ", 1)[0]
        return {"approach": "Cut it down.", "revised_text": keep, "changes": [], "questions": []}

    def test_asking_for_a_shorter_text_allows_a_much_shorter_answer(self):
        self.srv.script = [self.cut_to_a_third]
        r = self.go("reduce the length and remove the verbosity", chunk_ids=[self.ids["Alpha"]])
        unit = r["proposal"]["units"][0]
        self.assertEqual(unit["status"], "ok", unit.get("error"))
        self.assertTrue(all(v["ok"] for v in unit["validators"] if v["name"] == "length_within_bounds"))

    def test_without_that_request_the_same_cut_is_still_refused_and_the_reason_says_what_to_do(self):
        self.srv.script = [self.cut_to_a_third, self.cut_to_a_third]
        r = self.go("improve the flow", chunk_ids=[self.ids["Alpha"]])
        unit = r["proposal"]["units"][0]
        self.assertEqual(unit["status"], "failed")
        self.assertIn("changed the length of this passage too much", unit["error_plain"])
        self.assertIn("say so in your request", unit["error_plain"])

    def test_what_the_author_said_in_the_answers_counts_as_asking_for_it(self):
        self.assertEqual(revise.length_bounds("improve", "Further instructions from the author: keep it to two sentences, much shorter")[0], 0.2)
        self.assertEqual(revise.length_bounds("improve", "")[0], 0.5)
        self.assertEqual(revise.length_bounds("expand the explanation", "")[1], 2.5)


@needs_pdf
class Api(ChatBase):
    """The same through the chat: the part is written again from the card, the answers of every round come along, and what was not asked again stays in the one suggestion."""
    Q = "Which software did you use for the analysis?"

    def setUp(self):
        super().setUp()
        self.main.write_text(doc())
        pi.refresh(self.name, self.proj, trigger="test")
        got = [c for c in pi.list_chunks(self.name, self.proj)["chunks"] if c["kind"] == "tex" and c["file"] == "main.tex"]
        self.all = [next(c["id"] for c in got if n in (c.get("section_path") or "")) for n in ("Alpha", "Beta", "Gamma")]
        tighten = ("very wordy and long indeed", "wordy")
        self.oa.script = [reply(tighten), reply(tighten, questions=[self.Q]), reply(tighten)]
        r = self.send("tighten these", chunk_ids=self.all)
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.pid = self.last("result")["data"]["proposal"]

    def prop(self, pid):
        return self.c.get(f"/api/proposal?paper=alpha&id={pid}")[1]

    def again(self, pid, redo, keep, facts):
        self.oa.script = [reply(("very wordy and long indeed", "plainly wordy"))] * 3
        before = len([q for q in self.oa.requests if q.get("body")])
        r = self.send("tighten these", chunk_ids=redo, keep=keep, facts=facts, revises=pid)
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        return self.last("result"), [q for q in self.oa.requests if q.get("body")][before:]

    def test_the_first_suggestion_has_three_passages_and_one_question(self):
        units = self.prop(self.pid)["units"]
        self.assertEqual([len(u["questions"]) for u in units], [0, 1, 0])
        self.assertEqual([len(u["chunk_ids"]) for u in units], [1, 1, 1])

    def test_answering_the_question_writes_again_only_that_passage_and_keeps_the_rest_in_the_same_suggestion(self):
        units = self.prop(self.pid)["units"]
        facts = f"Question: {self.Q}\nAnswer: R 4.3"
        msg, calls = self.again(self.pid, units[1]["chunk_ids"], ["u1", "u3"], facts)
        self.assertEqual(len(calls), 1, "one passage was asked again, not three")
        self.assertIn("R 4.3", user_of(calls[0]["body"]))
        new = self.prop(msg["data"]["proposal"])
        self.assertEqual([u["section_path"].split(" > ")[-1] for u in new["units"]], ["Alpha", "Beta", "Gamma"])
        self.assertEqual([bool(u.get("carried")) for u in new["units"]], [True, False, True])
        self.assertEqual(self.prop(self.pid)["status"], "superseded", "the earlier one is replaced")
        self.assertEqual(len(msg["data"]["chunk_ids"]), 3, "the card keeps all of its pieces, so the next round can choose among all three")
        self.assertEqual(new["status"], "proposed")

    def test_the_second_round_still_knows_the_first_answers_and_a_skipped_question(self):
        units = self.prop(self.pid)["units"]
        skipped = f"Question: {self.Q}\nAnswer: (skipped: the author chose not to answer. Do not ask it again; leave that point as it is in the passage and do not guess.)"
        first, _ = self.again(self.pid, units[1]["chunk_ids"], ["u1", "u3"], skipped)
        new = self.prop(first["data"]["proposal"])
        beta = next(u for u in new["units"] if not u.get("carried"))
        second, calls = self.again(new["id"], beta["chunk_ids"], [u["unit_id"] for u in new["units"] if u is not beta], "Further instructions from the author: keep it to one sentence")
        sent = user_of(calls[0]["body"])
        self.assertIn("Do not ask it again", sent, "the skip from the first round is still told to the model")
        self.assertIn("keep it to one sentence", sent)
        self.assertEqual(self.prop(second["data"]["proposal"])["facts"].count("Question:"), 1)

    def test_without_the_list_of_passages_everything_is_written_again_as_before(self):
        units = self.prop(self.pid)["units"]
        self.oa.script = [reply(("very wordy and long indeed", "plainly wordy"))] * 3
        r = self.send("tighten these", chunk_ids=self.all, facts=f"Question: {self.Q}\nAnswer: R", revises=self.pid)
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        new = self.prop(self.last("result")["data"]["proposal"])
        self.assertEqual([bool(u.get("carried")) for u in new["units"]], [False, False, False])

    def test_a_keep_list_that_is_not_ids_is_ignored(self):
        msg, calls = self.again(self.pid, self.prop(self.pid)["units"][1]["chunk_ids"], ["../../etc", "u1;rm", {"x": 1}], f"Question: {self.Q}\nAnswer: R")
        new = self.prop(msg["data"]["proposal"])
        self.assertEqual(len(new["units"]), 1, "nothing was carried: the keep list had no valid passage")


if __name__ == "__main__":
    unittest.main()
