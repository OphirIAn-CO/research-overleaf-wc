"""Writing a section into a paper that does not have it yet, from a request that carries a long pasted outline: the right section is found (the request is at the start or the end of
the message, not in the middle of the pasted material), the section is made with its heading where it belongs, and the pasted outline counts as the author's own source."""
import unittest
from pathlib import Path

from tests.test_book import needs_pdf
from tests.test_revise import ReviseBase
from tests.test_v2_chat_api import ChatBase
from writing_core import chat, draft, paper_index, revise

EMPTY = r"""\documentclass{article}
\title{Risk perception}
\begin{document}
\maketitle
\begin{abstract}
We study how people perceive risk and why aggregate models hide heterogeneity.
\end{abstract}
\end{document}
"""
WITH_LATER = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We study how people perceive risk.
\end{abstract}

\section{Methods}
We fitted a mixed-effects regression of risk perception on exposure with respondent as a random intercept.

\section{Results}
Perceived risk rose with exposure in every country we studied, as the regression shows.
\end{document}
"""
OUTLINE = """3. Why the methodological problem is hard
This section explains why the methodological problem is difficult before discussing how researchers have attempted to solve it.
4. How risk perception has traditionally been measured and modeled
Now move from theory to methods.
Synthesize the major approaches identified in your literature review rather than describing papers individually: psychometric scales, factor approaches, regression models, structural models, Bayesian approaches.
A useful distinction is: Measurement, explanation, prediction.
5. Risk perception is heterogeneous and context dependent
Your literature should establish that risk perception is not homogeneous across people, hazards, socioeconomic contexts or countries. Therefore RP_i differs from RP_j even when individuals face nominally similar hazards.
This leads to an important methodological implication: models based exclusively on aggregate relationships may obscure substantial heterogeneity.
"""
ASK = "I need you to, based on the literature review I am going to give you, write the introduction of my paper.\n\n"


def reply(text, basis=None, questions=None):
    return {"approach": "The introduction follows your outline: why the problem is hard, how it has been measured, and why risk perception is heterogeneous.", "draft_text": text,
            "basis": basis if basis is not None else [{"statement": "Risk perception is not homogeneous", "source": "request"}], "questions": questions or []}


class WhichPart(unittest.TestCase):
    def test_a_long_message_is_read_by_its_start_and_its_end_not_by_the_material_in_the_middle(self):
        text = ASK + OUTLINE
        self.assertEqual(chat.intent(text), "draft")
        self.assertEqual(chat.draft_part(text), "introduction", "'move from theory to methods' is pasted material")
        tail = OUTLINE + "\n\nPlease write the discussion section from this outline."
        self.assertEqual(chat.draft_part(tail), "discussion")
        self.assertEqual(chat.intent(tail), "draft")

    def test_short_requests_are_read_as_before(self):
        for text, part in (("explain the results in the discussion", "discussion"), ("write the results from my code", "results"), ("add a paragraph on the sampling to the methods", "methods"),
                           ("write the discussion of these results", "discussion"), ("draft the abstract", "abstract"), ("write the introduction", "introduction")):
            self.assertEqual(chat.draft_part(text), part, text)

    def test_words_that_only_appear_in_pasted_material_do_not_change_what_kind_of_request_it_is(self):
        material = "Please review the literature and improve the text where it needs it. Find references for this. " * 3
        self.assertEqual(chat.intent(ASK + material + OUTLINE * 2), "draft")


@needs_pdf
class MakingTheSection(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()
        self.main = Path(self.proj["path"]) / "main.tex"

    def paper(self, text):
        self.main.write_text(text)
        paper_index.refresh(self.name, self.proj, trigger="test")

    def test_an_introduction_is_made_after_the_abstract_when_the_paper_has_nothing_yet(self):
        self.paper(EMPTY)
        loc = draft.locate(self.name, self.proj, ASK + OUTLINE)
        self.assertEqual((loc["status"], loc["part"], loc["anchor"]["heading"]), ("ready", "introduction", "\\section{Introduction}"))
        lines = EMPTY.split("\n")
        self.assertEqual(loc["anchor"]["after_line"], lines.index("\\end{abstract}") + 1)
        self.assertEqual(loc["anchor"]["mode"], "as a new introduction section after the abstract")
        self.assertIn("as a new introduction section after the abstract (after line 7 of main.tex)", loc["where"])
        self.assertIn("comes with its own heading, \\section{Introduction}", loc["notes"][0])

    def test_a_missing_section_goes_before_the_first_section_that_normally_follows_it(self):
        self.paper(WITH_LATER)
        loc = draft.locate(self.name, self.proj, "write the introduction from my notes")
        lines = WITH_LATER.split("\n")
        self.assertEqual(loc["anchor"]["mode"], "as a new introduction section before the methods")
        self.assertEqual(loc["anchor"]["after_line"], lines.index("\\section{Methods}"), "right above the Methods heading, not inside it")
        loc = draft.locate(self.name, self.proj, "write the discussion of my results")
        self.assertEqual(loc["anchor"]["mode"], "as a new discussion section after the results")
        self.assertEqual(loc["anchor"]["after_line"], lines.index("\\end{document}"), "after the last line of the Results")

    def test_a_missing_abstract_still_asks_for_its_environment_instead_of_inventing_one(self):
        self.paper(r"\documentclass{article}" + "\n" + r"\begin{document}" + "\n" + r"\section{Introduction}" + "\nSome text about risk.\n" + r"\end{document}" + "\n")
        loc = draft.locate(self.name, self.proj, "write the abstract")
        self.assertEqual(loc["status"], "missing")

    def test_the_draft_is_written_checked_and_saved_with_its_heading_and_the_outline_is_its_source(self):
        self.paper(EMPTY)
        loc = draft.locate(self.name, self.proj, ASK + OUTLINE)
        pl = draft.plan(self.name, self.proj, ASK + OUTLINE, loc["anchor"], loc["part"])
        self.assertIn("RP_i differs from RP_j", self.srv_user())
        text = ("Risk perception is not homogeneous across people, hazards or countries, so individual perceptions differ even when people face similar hazards.\n\n"
                "Models based only on aggregate relationships may obscure this heterogeneity.")
        self.srv.script = [reply(text)]
        res = draft.run(self.name, self.proj, ASK + OUTLINE, loc["anchor"], loc["part"], confirm=pl["payload_hash"])
        self.assertEqual(res["status"], "proposed", res["proposal"]["units"][0].get("error"))
        u = res["proposal"]["units"][0]
        self.assertEqual(u["heading"], "\\section{Introduction}")
        self.assertEqual([b["source"] for b in u["basis"]], ["request"])
        self.assertFalse([v for v in u["validators"] if not v["ok"] and v["name"] == "basis_known"], "'request' is a source the model may name")
        views = revise.selected_views(self.proj, res["proposal"]["id"])
        self.assertTrue(views[0]["latex"].startswith("\\section{Introduction}\n\nRisk perception"), "the copy of the new text carries the heading")
        self.assertFalse(views[0]["masked"].startswith("\\section"), "but the text for QuillBot does not")
        out = revise.apply(self.name, self.proj, res["proposal"]["id"], accept="all", unclean_ok=True)
        self.assertEqual(out["status"], "applied")
        saved = self.main.read_text()
        self.assertIn("\\end{abstract}\n\n\\section{Introduction}\n\nRisk perception is not homogeneous", saved)
        self.assertTrue(saved.rstrip().endswith("\\end{document}"))
        self.assertIn("heterogeneity.\n\n\\end{document}", saved, "blank lines on both sides, the document still closes")
        paper_index.refresh(self.name, self.proj, trigger="test")
        self.assertTrue(paper_index.part_chunks(self.name, self.proj, ["introduction"], refresh_first=False), "the paper now has an introduction")

    def srv_user(self):
        from writing_core import openai_client
        self.srv.script = []
        loc = draft.locate(self.name, self.proj, ASK + OUTLINE)
        p = draft.prepare(self.name, self.proj, ASK + OUTLINE, loc["anchor"], loc["part"])
        return openai_client.user_text(p["body"])

    def test_a_number_the_model_invents_is_still_refused_with_the_outline_in_the_request(self):
        self.paper(EMPTY)
        loc = draft.locate(self.name, self.proj, ASK + OUTLINE)
        pl = draft.plan(self.name, self.proj, ASK + OUTLINE, loc["anchor"], loc["part"])
        bad = "Across 47 countries risk perception is not homogeneous."
        self.srv.script = [reply(bad), reply(bad)]
        res = draft.run(self.name, self.proj, ASK + OUTLINE, loc["anchor"], loc["part"], confirm=pl["payload_hash"])
        self.assertEqual(res["status"], "failed")


@needs_pdf
class InTheChat(ChatBase):
    def test_the_request_reaches_a_plan_for_a_new_section_and_a_message_over_the_limit_is_not_cut_silently(self):
        self.main.write_text(EMPTY)
        paper_index.refresh(self.name, self.proj, trigger="test")
        r = self.send(ASK + OUTLINE)
        kinds = [m["kind"] for m in r["messages"]]
        self.assertEqual(kinds, ["text", "draft_plan"], r["messages"])
        plan = r["messages"][1]
        self.assertIn("as a new introduction section after the abstract", plan["data"]["where"])
        self.assertIn("\\section{Introduction}", plan["data"]["notes"][0])
        self.wait_job(r["job"])
        huge = ASK + "x" * (chat.MAX_TEXT + 500)
        r = self.send(huge)
        self.assertEqual(r["messages"][0]["text"], huge[:chat.MAX_TEXT])
        self.assertIn("Your message is longer than", r["messages"][1]["text"])
        self.assertIn("Add a file", r["messages"][1]["text"])


if __name__ == "__main__":
    unittest.main()
