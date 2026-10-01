"""v2 engine: plain-language steps, the model's explanation, the cost guard, cut-down passages, and the author's own version."""
import json
import re
import unittest
from pathlib import Path

from tests.fake_openai import passage_of
from tests.test_revise import ReviseBase, reply
from writing_core import config, events, latex_mask, paper_index, revise

SAMPLING = "We sampled 40 farms by stratified random sampling."
SAMPLING2 = "We drew a stratified random sample of 40 farms."


class ForgivingTags(unittest.TestCase):
    ORIGINAL = r"We sampled farms \cite{smith2020} in 2019. The model $y=ax$ fits."

    def setUp(self):
        self.masked, self.mapping = latex_mask.mask(self.ORIGINAL)

    def test_tag_spellings_that_another_tool_may_produce(self):
        for variant in ("[LX001]", "[[ LX001 ]]", "LX001", "[ [LX001] ]", "[[LX001]]"):
            text = self.masked.replace("[[LX001]]", variant)
            fixed, results, missing = latex_mask.check_author_text(self.masked, text, self.mapping)
            self.assertEqual((fixed, latex_mask.errors(results), missing), (self.masked, [], []), variant)

    def test_pasting_the_latex_version_puts_the_tags_back(self):
        text = self.masked.replace("[[LX001]]", r"\cite{smith2020}").replace("[[LX002]]", "$y=ax$")
        fixed, results, missing = latex_mask.check_author_text(self.masked, text, self.mapping)
        self.assertEqual((fixed, missing), (self.masked, []))

    def test_a_lost_piece_is_described_by_the_words_around_it(self):
        text = self.masked.replace(" [[LX001]]", "")
        fixed, results, missing = latex_mask.check_author_text(self.masked, text, self.mapping)
        self.assertEqual([m["tag"] for m in missing], ["LX001"])
        self.assertEqual(missing[0]["latex"], r"\cite{smith2020}")
        self.assertTrue(missing[0]["words_before"].endswith("sampled farms"))
        self.assertTrue(missing[0]["words_after"].startswith("in 2019"))
        sentences = revise.describe_checks(results, missing, 2)
        self.assertTrue(any(s["level"] == "error" and "citation is missing" in s["text"] and "sampled farms" in s["text"] for s in sentences))

    def test_a_duplicate_or_invented_tag_is_an_error(self):
        text = self.masked + " [[LX001]] [[LX009]]"
        fixed, results, missing = latex_mask.check_author_text(self.masked, text, self.mapping)
        self.assertTrue(latex_mask.errors(results))


class Frame(unittest.TestCase):
    def test_headings_at_the_ends_are_cut_out_and_rejoin_exactly(self):
        for text in ("[[LX001]]\n[[LX002]]\nProse here.\n\nMore prose.\n", "Prose only.", "\n\n[[LX001]] [[LX002]]\nBody line.\n[[LX003]]\n\n",
                     "[[LX001]]\nBody.\n[[LX002]]\nMiddle heading kept.\n[[LX003]]", "[[LX001]]\n[[LX002]]\n", ""):
            head, body, tail = latex_mask.split_frame(text)
            self.assertEqual(head + body + tail, text, repr(text))
        head, body, tail = latex_mask.split_frame("[[LX001]]\n[[LX002]]\nProse here.\n\nMore prose.\n")
        self.assertEqual((head, body, tail), ("[[LX001]]\n[[LX002]]\n", "Prose here.\n\nMore prose.", "\n"))
        self.assertEqual(latex_mask.split_frame("[[LX001]]\nBody.\n[[LX002]]\nMiddle.\n[[LX003]]")[1], "Body.\n[[LX002]]\nMiddle.")   # a heading in the middle stays
        self.assertEqual(latex_mask.split_frame("[[LX001]]\n[[LX002]]\n")[1], "[[LX001]]\n[[LX002]]\n")                                   # nothing to edit: untouched


class Repair(unittest.TestCase):
    def test_intact_text_is_left_alone(self):
        t = "Intro [[LX001]] text.\n\n[[LX002]]\nMore."
        self.assertEqual(latex_mask.repair(t, t), (t, []))

    def test_a_heading_on_its_own_line_goes_back_before_its_paragraph_even_when_the_paragraph_was_reworded(self):
        original = "Opening paragraph about sampling.\n\n[[LX001]]\nWe fitted a mixed-effects regression of yield.\n"
        for revised in ("Opening paragraph about sampling.\n\nWe fitted a mixed-effects regression of yield.\n",
                        "Opening paragraph about sampling.\n\nWe estimated a mixed-effects model of yield.\n"):
            fixed, restored = latex_mask.repair(original, revised)
            self.assertEqual([r["tag"] for r in restored], ["LX001"], revised)
            self.assertTrue(restored[0]["own_line"])
            self.assertEqual(latex_mask.placeholders(fixed), ["LX001"])
            self.assertLess(fixed.index("[[LX001]]"), fixed.index("mixed-effects"))
            self.assertGreater(fixed.index("[[LX001]]"), fixed.index("sampling."))

    def test_inline_pieces_go_back_after_the_words_they_followed(self):
        original = "We sampled farms [[LX001]] in 2019. The model [[LX002]] fits well."
        fixed, restored = latex_mask.repair(original, "We sampled farms in 2019. The model fits well.")
        self.assertEqual(fixed, original)
        self.assertEqual([(r["tag"], r["own_line"]) for r in restored], [("LX001", False), ("LX002", False)])
        fixed, _ = latex_mask.repair(original, "In 2019 we sampled farms. The model fits well.")      # words rearranged: the nearest anchor still works
        self.assertEqual(sorted(latex_mask.placeholders(fixed)), ["LX001", "LX002"])

    def test_what_cannot_be_placed_stays_missing_so_the_normal_check_rejects_it(self):
        original = "We sampled farms [[LX001]] in 2019."
        fixed, restored = latex_mask.repair(original, "Completely different words here.")
        self.assertEqual((fixed, restored), ("Completely different words here.", []))
        self.assertTrue(latex_mask.errors(latex_mask.validate(original, fixed)))

    def test_a_piece_that_closed_the_passage_goes_back_at_the_end(self):
        fixed, restored = latex_mask.repair("Some prose.\n[[LX001]]", "Some prose, revised.")
        self.assertTrue(fixed.rstrip().endswith("[[LX001]]"))


class StepsAndApproach(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()

    def test_steps_come_in_order_in_plain_words_and_are_kept(self):
        seen = []
        self.srv.script = [reply((SAMPLING, SAMPLING2), approach="Sampling was described passively; now the actor leads.")]
        r = self.go("improve the sampling", part="methods.data", progress=lambda items: seen.append([(i["key"], i["state"]) for i in items]))
        p = r["proposal"]
        keys = [i["key"] for i in p["steps"]]
        self.assertEqual(keys, ["read", "advice", "ask1", "check1", "finish"])
        self.assertTrue(all(i["state"] in ("done", "skipped") for i in p["steps"]))
        self.assertEqual(p["steps"][0]["label"], "Read your methods")
        self.assertIn("1 passage", p["steps"][0]["detail"])
        self.assertIn("Asking test-model", p["steps"][2]["label"])
        self.assertEqual(seen[0], [("read", "running")])          # the callback fires as work starts, not only at the end
        self.assertEqual(seen[-1][-1], ("finish", "done"))
        self.assertEqual(p["units"][0]["approach"], "Sampling was described passively; now the actor leads.")
        self.assertEqual(revise.get_proposal(self.proj, p["id"])["steps"], p["steps"])

    def test_the_request_asks_for_an_approach_and_the_text_never_carries_tags(self):
        self.srv.script = [reply((SAMPLING, SAMPLING2), approach="Fixed flow [[LX001]] and grammar.")]
        p = self.go("improve the sampling", part="methods.data")["proposal"]
        self.assertIn("approach", revise.SYSTEM_PROMPT)
        self.assertIn("approach", self.srv.requests[0]["body"]["text"]["format"]["schema"]["required"])
        self.assertNotIn("[[LX", p["units"][0]["approach"])

    def test_a_model_that_omits_the_approach_still_works(self):
        self.srv.script = [lambda body: {"revised_text": passage_of(body).replace(SAMPLING, SAMPLING2), "changes": [], "questions": []}]
        p = self.go("improve the sampling", part="methods.data")["proposal"]
        self.assertEqual(p["status"], "proposed")
        self.assertEqual(p["units"][0]["approach"], "")

    def test_failed_units_are_marked_in_the_steps(self):
        bad = lambda body: {"approach": "x", "revised_text": "", "changes": [], "questions": []}
        self.srv.script = [bad, bad]
        p = self.go("improve the sampling", part="methods.data")["proposal"]
        self.assertEqual(p["status"], "failed")
        self.assertEqual([i["state"] for i in p["steps"] if i["key"] in ("ask1", "check1")], ["failed", "failed"])


class ProtectedPieces(ReviseBase):
    """What the model is not shown, what it drops, and what is put back."""

    def setUp(self):
        super().setUp()
        from tests.test_v2_chat import MAIN
        self.live()
        (Path(self.proj["path"]) / "main.tex").write_text(MAIN)
        paper_index.refresh(self.name, self.proj)

    def test_headings_at_the_top_are_never_sent_and_still_in_the_result(self):
        seen = []

        def model(body):
            seen.append(passage_of(body))
            return {"approach": "x", "revised_text": passage_of(body).replace("Each region has a distinct climate and soil.", "Each region has its own climate and soil."),
                    "changes": [], "questions": []}
        self.srv.script = [model]
        ids = [c["id"] for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["section_path"].endswith("Study area")]
        p = self.go("improve the study area section", chunk_ids=ids)["proposal"]
        self.assertEqual(p["status"], "proposed", p["units"][0].get("error"))
        self.assertNotIn("[[LX001]]", seen[0])                                      # the \\subsection line was not shown
        u = p["units"][0]
        self.assertIn("[[LX001]]", u["masked_original"])
        self.assertEqual(len(u["hunks"]), 1)                                        # only the sentence changed
        revise.apply(self.name, self.proj, p["id"], accept="all", cleaner=lambda t: t)
        written = (Path(self.proj["path"]) / "main.tex").read_text()
        self.assertIn("\\subsection{Study area}", written)
        self.assertIn("Each region has its own climate and soil.", written)

    def test_an_inline_citation_the_model_dropped_is_put_back_and_flagged(self):
        def model(body):
            t = re.sub(r"\s*\[\[LX\d+\]\]", "", passage_of(body))             # the model forgot every citation
            return {"approach": "x", "revised_text": t.replace("Farmers need guidance on irrigation", "Farmers need guidance about irrigation"), "changes": [], "questions": []}
        self.srv.script = [model]
        ids = [c["id"] for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["section_path"] == "Introduction"]
        p = self.go("improve the introduction", chunk_ids=ids)["proposal"]
        u = p["units"][0]
        self.assertEqual(p["status"], "proposed", u.get("error"))
        self.assertEqual([(r["kind"], r["own_line"]) for r in u["restored"]], [("citation", False)])
        self.assertIn("\\cite{smith2020}", u["restored"][0]["latex"])
        self.assertEqual(len(self.srv.requests), 1)                                  # repaired on the spot: no second request was needed
        self.assertEqual([i["detail"] for i in p["steps"] if i["key"] == "check1"], ["1 dropped piece put back; all intact"])

    def test_a_mid_passage_heading_the_model_dropped_is_put_back_quietly(self):
        def model(body):
            t = passage_of(body)
            t = "\n".join(ln for ln in t.split("\n") if not ln.startswith("[[LX"))
            return {"approach": "x", "revised_text": t.replace("We fitted", "We estimated"), "changes": [], "questions": []}
        self.srv.script = [model]
        ids = [c["id"] for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["section_path"].startswith("Materials and Methods")]
        p = self.go("improve the methods", chunk_ids=ids)["proposal"]
        u = p["units"][0]
        self.assertEqual(p["status"], "proposed", u.get("error"))
        self.assertTrue(u["restored"] and all(r["own_line"] for r in u["restored"]))
        self.assertEqual(len(self.srv.requests), 1)

    def test_an_answer_that_cannot_be_repaired_is_refused_in_plain_words(self):
        bad = lambda body: {"approach": "x", "revised_text": "Entirely new text with [[LX099]] invented.", "changes": [], "questions": []}
        self.srv.script = [bad, bad]
        ids = [c["id"] for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["section_path"] == "Introduction"]
        p = self.go("improve the introduction", chunk_ids=ids)["proposal"]
        u = p["units"][0]
        self.assertEqual((p["status"], u["error_kind"]), ("failed", "validation"))
        self.assertIn("dropped or changed", u["error_plain"])
        self.assertIn("citation", u["error_plain"])
        self.assertIn("Nothing was changed", u["error_plain"])
        self.assertNotIn("LX0", u["error_plain"])                                    # no internal tag names in what a person reads
        check = [i for i in p["steps"] if i["key"] == "check1"][0]
        self.assertIn("dropped or changed", check["detail"])


class Guard(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()

    def test_a_big_request_stops_for_confirmation_with_an_estimate(self):
        r = self.go("improve my methods", part="methods", guard_units=0)
        self.assertEqual(r["status"], "needs_confirm")
        self.assertEqual(self.srv.requests, [])
        self.assertGreaterEqual(r["units"], 1)
        self.assertGreater(r["est_tokens_in"], 100)
        self.assertIsNotNone(r["est_cost"])                     # prices are configured in this fixture
        self.assertTrue(r["passages"][0]["section"])

    def test_confirming_sends_it(self):
        r = self.go("improve my methods", part="methods", guard_units=0, confirm_big=True)
        self.assertEqual(r["status"], "no_changes")
        self.assertEqual(len(self.srv.requests), 1)

    def test_no_guard_means_old_behaviour_and_the_hard_ceiling_still_applies(self):
        self.assertEqual(self.go("improve my methods", part="methods")["status"], "no_changes")
        with self.assertRaises(revise.ReviseError) as cm:
            self.go("improve my methods", part="methods", max_units=0)
        self.assertIn("limit", str(cm.exception))


class CutDownPassages(ReviseBase):
    def test_a_chunk_id_with_a_line_range_sends_only_those_lines(self):
        self.live()
        paper_index.refresh(self.name, self.proj)
        chunks = [c for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["file"] == "sections/methods.tex"]
        c = chunks[0]
        a, b = c["line_start"], c["line_start"] + 1
        r = self.go("improve this", chunk_ids=[f"{c['id']}@{a}-{b}"], dry_run=True)
        self.assertEqual(r["passages"][0]["lines"], [a, b])
        sent = json.dumps(r["payloads"][0]["request"])
        self.assertIn("sampled 40 farms", sent)
        self.assertNotIn("Informed consent", sent)        # the rest of the chunk stays home

    def test_ranges_never_widen_a_chunk(self):
        paper_index.refresh(self.name, self.proj)
        chunks = [c for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["file"] == "sections/methods.tex"]
        c = chunks[0]
        r = self.go("improve this", chunk_ids=[f"{c['id']}@1-9999"], dry_run=True)
        self.assertEqual(r["passages"][0]["lines"], [c["line_start"], c["line_end"]])
        self.assertEqual(revise.split_range_id("P:x:a.tex#b#k01@12-30"), ("P:x:a.tex#b#k01", 12, 30))
        self.assertEqual(revise.split_range_id("P:x:a.tex#b#k01"), ("P:x:a.tex#b#k01", None, None))


class AuthorVersion(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()
        self.srv.script = [reply((SAMPLING, SAMPLING2), ("Informed consent was obtained from all farmers.", "All farmers gave informed consent."))]
        self.p = self.go("improve the sampling", part="methods.data")["proposal"]
        self.u = self.p["units"][0]
        self.views = revise.unit_views(self.u)

    def edit(self, **kw):
        t = self.views["suggested_masked"]
        for a, b in kw.items():
            t = t.replace(a.replace("_", " "), b)
        return t

    def test_the_views_offer_tagged_and_latex_text(self):
        v = self.views
        self.assertIn(SAMPLING2, v["suggested_masked"])
        self.assertIn("[[LX", v["original_masked"]) if v["tags"] else None
        self.assertIn(SAMPLING2, v["suggested_latex"])
        self.assertNotIn("[[LX", v["suggested_latex"])
        self.assertIn("\\subsection{Participants and sampling}", v["suggested_latex"])

    def test_checks_are_in_plain_words(self):
        c = revise.check_adopted(self.proj, self.p["id"], {"u1": self.views["suggested_masked"]})["u1"]
        self.assertTrue(c["ok"])
        self.assertEqual(c["messages"][0]["level"], "ok")
        self.assertIn("still there", c["messages"][0]["text"])

    def test_a_lost_protected_piece_is_named_with_its_place(self):
        text = latex_mask.PH.sub("", self.views["suggested_masked"], count=1)
        if not self.views["tags"]:
            self.skipTest("fixture passage has no protected piece")
        c = revise.check_adopted(self.proj, self.p["id"], {"u1": text})["u1"]
        self.assertFalse(c["ok"])
        self.assertTrue(any("missing" in m["text"] and m["level"] == "error" for m in c["messages"]))

    def test_numbers_dashes_and_loose_tags(self):
        base = self.views["suggested_masked"]
        c = revise.check_adopted(self.proj, self.p["id"], {"u1": base.replace("40 farms", "41 farms")})["u1"]
        self.assertFalse(c["ok"])
        self.assertTrue(any("41" in m["text"] for m in c["messages"]))
        c = revise.check_adopted(self.proj, self.p["id"], {"u1": base.replace("Inclusion required", "Inclusion — required")})["u1"]
        self.assertFalse(c["ok"])
        loose = base
        for key in latex_mask.placeholders(base):
            loose = loose.replace(f"[[{key}]]", f"[ {key} ]")
        self.assertTrue(revise.check_adopted(self.proj, self.p["id"], {"u1": loose})["u1"]["ok"])

    def test_adopt_then_apply_writes_the_authors_text_not_the_models(self):
        mine = self.views["suggested_masked"].replace("All farmers gave informed consent.", "Every farmer gave informed consent.")
        res = revise.adopt(self.name, self.proj, self.p["id"], {"u1": mine})
        self.assertTrue(res["ok"])
        stored = revise.get_proposal(self.proj, self.p["id"])
        self.assertTrue(stored["edited_by_author"])
        self.assertIn("model_version", stored["units"][0])           # the model's own answer is kept beside it
        self.assertEqual(stored["units"][0]["hunks"][0]["reasons"], ["Your own edit"])
        out = revise.apply(self.name, self.proj, self.p["id"], accept="all", cleaner=lambda t: t, source="web")
        self.assertEqual(out["status"], "applied")
        written = (Path(self.proj["path"]) / "sections" / "methods.tex").read_text()
        self.assertIn("Every farmer gave informed consent.", written)
        self.assertNotIn("All farmers gave informed consent.", written)
        self.assertIn(SAMPLING2, written)
        self.assertTrue(events.read(config.paper_dir(self.proj), types={"decision"})[-1]["edited"])

    def test_errors_block_and_warnings_need_a_tick(self):
        bad = self.views["suggested_masked"].replace("40 farms", "41 farms")
        res = revise.adopt(self.name, self.proj, self.p["id"], {"u1": bad})
        self.assertEqual((res["ok"], res["needs_ack"], res["blocked"]), (False, False, ["u1"]))
        self.assertEqual(revise.get_proposal(self.proj, self.p["id"])["status"], "proposed")        # nothing changed
        warn = self.views["suggested_masked"].replace("stratified random sample", "stratified random Zorblax sample")
        res = revise.adopt(self.name, self.proj, self.p["id"], {"u1": warn})
        self.assertEqual((res["ok"], res["needs_ack"]), (False, True))
        res = revise.adopt(self.name, self.proj, self.p["id"], {"u1": warn}, acknowledge=True)
        self.assertTrue(res["ok"])

    def test_a_changed_file_still_blocks_the_write(self):
        mine = self.views["suggested_masked"].replace("All farmers", "Every farmer")
        revise.adopt(self.name, self.proj, self.p["id"], {"u1": mine})
        m = Path(self.proj["path"]) / "sections" / "methods.tex"
        m.write_text(m.read_text() + "\nMore words added on the web.\n")
        m.write_text(m.read_text().replace("Inclusion required", "Inclusion needed"))
        with self.assertRaises(revise.ReviseError) as cm:
            revise.apply(self.name, self.proj, self.p["id"], accept="all", cleaner=lambda t: t)
        self.assertIn("changed since", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
