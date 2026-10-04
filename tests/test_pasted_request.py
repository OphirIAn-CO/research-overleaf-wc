"""A paragraph of the manuscript pasted into a message is the author's text, not the request: its verbs ("to create routing alternatives") must not turn the message into "write new text
for the results", and its \\cite is not a demand for citations. What the author asks is in the rest of the message."""
import json
import unittest

from tests.test_book import needs_pdf
from tests.test_outline_citations import Net, routes
from tests.test_v2_chat_api import ChatBase
from writing_core import chat, library, paper_index, scholar, subject

PARA = (r"Using the QuickOSM plugin \citep{QGISDevelopmentTeam2025QuickOSM} in QGIS, we extracted forest cover and primary transportation routes to create realistic routing alternatives. "
        "Thus, pipelines were engineered to bypass forested areas and align with corridors near major transportation routes, adhering to established engineering standards and the "
        "topographical constraints of Colombia. This methodology was also applied to project expected infrastructure expansions specified in the national gas development plan.")
ASK = ("Specifically for this paragraph, lets add the incite citation finding online from relevant information to the statements that are being held in th paragraph\n\n" + PARA)
PAPER = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We study how forests and roads shape the routing of gas pipelines in Colombia.
\end{abstract}

\section{Methodology}
\subsection{Georeferenced Network Reconstruction}
The current representation of the Colombian natural gas transmission network is documented by the planning unit and reproduced in the appendix.

""" + PARA.replace("\\citep{QGISDevelopmentTeam2025QuickOSM}", "\\citep{QGISDevelopmentTeam2025QuickOSM}") + r"""

\section{Results}
Pipelines avoided forested areas in every region we studied.
\end{document}
"""


class Reading(unittest.TestCase):
    def test_the_pasted_paragraph_is_taken_out_of_the_request(self):
        self.assertEqual(subject.request_part(ASK), ASK.split("\n\n")[0])
        self.assertEqual(subject.request_part("improve the flow of this paragraph:\n" + PARA), "improve the flow of this paragraph:", "also without a blank line")
        after = PARA + "\n\nPlease add a citation here."
        self.assertEqual(subject.request_part(after), "Please add a citation here.")

    def test_a_message_that_is_only_text_or_has_none_of_it_is_left_alone(self):
        for t in (PARA, "tighten my methods", "write the discussion\n\nof these results, short", "A first paragraph of prose without any markup that runs on for quite a few words indeed, "
                  "yes it does go on.\n\nA second one, equally plain, that also goes on for a good while longer than a short request would."):
            self.assertEqual(subject.request_part(t), t)

    def test_its_verbs_do_not_make_the_message_a_request_to_write_new_text(self):
        self.assertEqual(chat.intent(ASK), "refsearch")
        self.assertFalse(chat.is_draft_request(ASK))
        edit = "Improve the flow of this paragraph:\n\n" + PARA
        self.assertEqual(chat.intent(edit), "edit")

    def test_a_real_request_to_write_is_still_one(self):
        self.assertEqual(chat.intent("Write the discussion of these results.\n\n" + PARA), "draft")
        self.assertEqual(chat.draft_part("Write the discussion of these results.\n\n" + PARA), "discussion")

    def test_asking_for_citations_found_online_is_a_search_for_references(self):
        for t in ("add the citation finding online for the statements in this paragraph", "cite it with sources from the internet", "find citations online for this", "add references from the web"):
            self.assertEqual(chat.intent(t), "refsearch", t)
        for t in ("add a citation from my library", "improve the flow, it reads online-ish", "write a paragraph on online learning"):
            self.assertNotEqual(chat.intent(t), "refsearch", t)

    def test_a_cite_inside_the_pasted_paragraph_is_not_a_demand_for_citations(self):
        para = "We drew a sample of forty farms from the regional list \\cite{smith2020} and sorted them by size before sampling began in every region of the study area."
        self.assertFalse(library.requires_citations("tighten this paragraph\n\n" + para))
        self.assertTrue(library.requires_citations("add an in-text citation to this paragraph\n\n" + para))
        self.assertTrue(library.requires_citations("cite it"))


@needs_pdf
class InTheChat(ChatBase):
    def setUp(self):
        super().setUp()
        self._fetch = scholar.fetch
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self.net = scholar.fetch = Net(routes())
        self.main.write_text(PAPER)
        paper_index.refresh(self.name, self.proj, trigger="test")

    def test_asking_for_citations_found_online_for_a_pasted_paragraph_searches_for_that_paragraph(self):
        self.oa.script = [{"queries": ["forest cover and pipeline routing in Colombia", "transportation corridors and gas pipeline siting"]}]
        r = self.send(ASK)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text"], "no plan to write results: it goes to the search")
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        d = self.last("refsearch")["data"]
        self.assertEqual(len(d["ctx"]["chunk_ids"]), 1, "the references are for that paragraph")
        self.assertEqual(d["ctx"]["where"], "the paragraph you pasted")
        self.assertFalse(d["ctx"]["draft"])
        sent = json.dumps(self.oa.requests[0]["body"]["input"])
        self.assertIn("QuickOSM", sent, "the paragraph is what the search words are made from")
        self.assertEqual(d["queries"][0], "forest cover and pipeline routing in Colombia")

    def test_a_request_to_improve_the_paragraph_is_an_edit_of_that_paragraph_not_new_results(self):
        r = self.send("Improve the flow of this paragraph:\n\n" + PARA)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "plan"], r["messages"])
        plan = r["messages"][1]["data"]
        self.assertEqual((plan["plan"]["units"], plan["title"]), (1, "the paragraph you pasted"))
        self.wait_job(r["job"])

    def test_the_pasted_paragraph_is_found_even_when_the_words_around_it_name_another_part(self):
        r = self.send("This is wrong, the results say otherwise, tighten the sentences of this paragraph:\n\n" + PARA)
        self.assertEqual(r["messages"][1]["kind"], "plan")
        self.assertEqual(r["messages"][1]["data"]["title"], "the paragraph you pasted")
        self.wait_job(r["job"])
