"""A model that answers in JSON sometimes doubles the backslash of a command (\\S for \S). LaTeX reads that as a line break and a stray letter, so it must never reach the paper."""
import json
from pathlib import Path

from tests.fake_openai import passage_of
from tests.helpers import IsolatedHome, common
from tests.test_book import needs_pdf
from tests.test_revise import ReviseBase
from tests.test_paper import paper
from writing_core import latex_mask as lm, revise

ORIGINAL = r"The design is outlined in \S{[[LX001]]} \textit{Study area}. Next, \S[[LX002]] shows the data. A new line follows\\ here."


class Tokens(IsolatedHome):
    def test_a_doubled_command_is_put_back_where_the_paper_has_it_single(self):
        doubled = r"The design is outlined in \\S{[[LX001]]} \\textit{Study area}. Next, \\S[[LX002]] shows the data. A new line follows\\ here."
        fixed, names = lm.collapse_doubled(ORIGINAL, doubled)
        self.assertEqual(fixed, ORIGINAL)
        self.assertEqual(names, ["\\S", "\\textit", "\\S"])

    def test_a_real_line_break_is_left_alone_and_so_is_a_command_the_paper_never_had(self):
        out, names = lm.collapse_doubled(ORIGINAL, ORIGINAL + r" Then\\Break and \\foo")
        self.assertEqual(names, [])
        self.assertTrue(out.endswith(r"Then\\Break and \\foo"))

    def test_the_check_tells_a_doubled_backslash_from_a_single_one(self):
        r = {x["name"]: x for x in lm.validate(ORIGINAL, ORIGINAL.replace("\\S{", "\\\\S{"))}
        self.assertFalse(r["commands_unchanged"]["ok"])
        self.assertEqual(r["commands_unchanged"]["severity"], "error")
        self.assertIn("\\\\S", r["commands_unchanged"]["detail"])
        self.assertFalse(r["commands_kept"]["ok"])                       # the single \S in that place is gone
        self.assertTrue(all(x["ok"] for x in lm.validate(ORIGINAL, ORIGINAL.replace("Next,", "Then,"))))

    def test_a_command_added_again_or_dropped_is_noticed(self):
        added = {x["name"]: x for x in lm.validate(ORIGINAL, ORIGINAL + r" See \S[[LX002]] again.")}
        self.assertFalse(added["commands_unchanged"]["ok"])
        dropped = {x["name"]: x for x in lm.validate(ORIGINAL, ORIGINAL.replace(r"\textit{Study area}", "Study area"))}
        self.assertTrue(dropped["commands_unchanged"]["ok"])
        self.assertEqual((dropped["commands_kept"]["ok"], dropped["commands_kept"]["severity"]), (False, "warn"))

    def test_the_author_text_is_repaired_the_same_way_and_says_so(self):
        doubled = ORIGINAL.replace("\\S{", "\\\\S{")
        fixed, results, missing = lm.check_author_text(ORIGINAL.replace("[[LX001]]", "[[LX001]]"), doubled, {"LX001": "\\ref{a}", "LX002": "\\ref{b}"})
        self.assertEqual(fixed, ORIGINAL)
        self.assertEqual(lm.errors(results), [])
        note = [r for r in results if r["name"] == "doubled_backslashes_fixed"][0]
        self.assertEqual(note["severity"], "warn")
        self.assertIn("single backslash", note["detail"])


def doubling(old, new):
    """A model that edits the sentence and doubles every backslash it writes."""
    def step(body):
        text = passage_of(body).replace(old, new).replace("\\", "\\\\")
        return {"approach": "The sentence now reads more smoothly.", "revised_text": text, "changes": [], "questions": []}
    return step


@needs_pdf
class InTheRun(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()
        self.methods.write_text("\\section{Materials and Methods}\n\\subsection{Study area}\nThe design is outlined in \\S{\\ref{sec:a}} \\textit{Study area} and the sampling frame was prepared by the regional office.\n")

    def test_a_model_that_doubles_its_backslashes_still_gives_the_paper_its_single_ones(self):
        self.srv.script = [doubling("was prepared by", "came from")] * 3
        r = self.go("improve the flow of the methods", part="methods")
        u = r["proposal"]["units"][0]
        self.assertEqual(u["status"], "ok", u.get("error"))
        suggested = revise.unit_views(u)["suggested_masked"]
        self.assertIn("\\S{[[", suggested)
        self.assertNotIn("\\\\S", suggested)
        self.assertNotIn("\\\\textit", suggested)
        note = [v for v in u["validators"] if v["name"] == "doubled_backslashes_fixed"]
        self.assertTrue(note and "OpenAI doubled the backslash" in note[0]["detail"])
        self.assertEqual(len(self.srv.requests), 1, "no second request was needed")
        self.assertTrue(all("\\\\" not in h["new"] for h in u["hunks"]), "the change shows the real edit only")

    def test_what_is_saved_in_the_file_has_single_backslashes(self):
        self.srv.script = [doubling("was prepared by", "came from")] * 3
        r = self.go("improve the flow of the methods", part="methods")
        pid = r["proposal"]["id"]
        revise.apply(self.name, self.proj, pid, accept="all", unclean_ok=True, source="test")
        text = self.methods.read_text()
        self.assertIn("\\S{\\ref{sec:a}} \\textit{Study area} and the sampling frame came from", text)
        self.assertNotIn("\\\\", text)
