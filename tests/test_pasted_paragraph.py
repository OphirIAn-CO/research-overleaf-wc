"""A paragraph of the paper pasted into the message ("for this paragraph: ...") is what the request is about, not the whole section the words around it happen to name; and "the ones
selected" means the references picked earlier in the chat."""
import json
import unittest

from tests.test_book import needs_pdf
from tests.test_refsearch_api import DOI, Search
from tests.test_revise import reply
from tests.test_v2_chat_api import ChatBase
from writing_core import chat, paper_index

PARA = "The second paragraph describes soils, slopes and the access roads that were used to reach every farm in the sample."
INTRO = "Crop yield depends on water and on soil. Farmers need guidance on irrigation. We ask whether moisture predicts yield."


@needs_pdf
class Pasted(ChatBase):
    def understand(self, text):
        return chat.understand(self.name, self.proj, text)

    def test_a_pasted_paragraph_is_the_target_even_when_the_words_around_it_name_a_whole_section(self):
        u = self.understand(f"For this paragraph:\n\n{PARA}\n\nthe methodology needs an in-text citation here")
        self.assertEqual((u["status"], u["units"], u["title"]), ("ready", 1, "the paragraph you pasted"))
        self.assertEqual(len(u["chunk_ids"]), 1)
        self.assertIn("@", u["chunk_ids"][0], "a line range inside the section, not the whole of it")
        self.assertIn("main.tex line", u["notes"][0])
        self.assertIn("Only that paragraph", u["notes"][0])
        self.assertEqual(u["request"], "For this paragraph:\n\n[this paragraph]\n\nthe methodology needs an in-text citation here", "the request does not carry the paragraph twice")

    def test_protected_pieces_that_differ_do_not_stop_the_match(self):
        u = self.understand(f"For this paragraph, add a citation:\n\n{INTRO}\n\nthanks")          # the file has \cite{smith2020} in it, the pasted text does not
        self.assertEqual((u["status"], u["units"]), ("ready", 1))
        self.assertIn("Introduction", u["passages"][0]["label"] if "label" in u["passages"][0] else json.dumps(u["passages"]))

    def test_a_pasted_sentence_finds_its_paragraph(self):
        u = self.understand("Rewrite this sentence: The study was conducted in three regions that differ in rainfall and elevation. so that it reads better in the methodology")
        self.assertEqual((u["status"], u["units"], u["title"]), ("ready", 1, "the paragraph you pasted"))

    def test_text_that_is_not_in_the_paper_is_not_taken_for_a_paragraph(self):
        u = self.understand("Improve the flow of the text before figure 1 in methods. I think the sentences are too long and the reader gets lost when they follow the argument.")
        self.assertNotEqual(u.get("title"), "the paragraph you pasted")
        self.assertEqual(u["status"], "ready")
        self.assertGreater(len(u["chunk_ids"]), 0)

    def test_a_paragraph_that_is_not_in_the_paper_is_said_so_instead_of_silently_using_the_whole_section(self):
        elsewhere = ("Rainfall was measured with tipping bucket gauges installed beside every plot and read by the field crew each morning, and the readings were checked against "
                     "the national weather service stations nearest to each farm.")
        u = self.understand(f"For this paragraph in the methodology:\n\n{elsewhere}\n\nadd a citation")
        self.assertEqual(u["status"], "ready")
        self.assertNotEqual(u["title"], "the paragraph you pasted")
        self.assertIn("did not find the paragraph you pasted", u["notes"][0])

    def test_through_the_chat_only_that_paragraph_goes_to_the_model(self):
        r = self.send(f"For this paragraph:\n\n{PARA}\n\nthe sentences are too long in the methodology")
        plan = r["messages"][1]["data"]
        self.assertEqual((plan["plan"]["units"], plan["title"]), (1, "the paragraph you pasted"))
        self.wait_job(r["job"])
        sent = json.dumps(self.oa.requests[-1]["body"])
        self.assertIn("access roads", sent)
        self.assertNotIn("mixed-effects regression", sent, "the rest of the Methods stays on this computer")
        self.assertNotIn("After the map we describe", sent)
        self.assertNotIn("Statistical analysis", sent)


@needs_pdf
class TheOnesSelected(Search):
    def test_the_references_picked_earlier_in_the_chat_go_with_a_request_that_refers_to_them(self):
        j, m = self.search()
        pick = self.c.post("/api/chat/refs/pick", {"paper": "alpha", "message": m["id"], "dois": [DOI]})[1]
        self.assertEqual(self.wait_job(pick["job"])["status"], "done")
        self.oa.script = [reply(("Each region has a distinct climate and soil.", "Each region has a distinct climate and soil [[LX901]]."))]
        r = self.send("For this paragraph:\n\nThe study was conducted in three regions that differ in rainfall and elevation. Each region has a distinct climate and soil.\n\n"
                      "there was no in-text citation added based on the ones selected, in one of the previous answers")
        plan = r["messages"][1]["data"]
        self.assertEqual(plan["plan"]["online"], [DOI], "nobody had to pass the references: the chat knows which ones were picked")
        self.assertEqual(plan["plan"]["units"], 1)
        done = self.wait_job(r["job"])
        self.assertEqual(done["status"], "done", done)
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={self.last('result')['data']['proposal']}")
        self.assertEqual(prop["units"][0]["added_cites"], ["\\cite{Rojas2019Armed}"], prop["units"][0].get("error"))

    def test_without_a_reference_to_them_nothing_is_added_on_its_own(self):
        j, m = self.search()
        pick = self.c.post("/api/chat/refs/pick", {"paper": "alpha", "message": m["id"], "dois": [DOI]})[1]
        self.wait_job(pick["job"])
        r = self.send("improve the flow of the text before figure 1 inside methods")
        self.assertEqual(r["messages"][1]["data"]["plan"]["online"], [])


class Unit(unittest.TestCase):
    def test_what_counts_as_a_reference_to_earlier_picks(self):
        for text in ("there was no citation added based on the ones selected", "use the references I selected", "cite the sources I added", "the papers I picked earlier"):
            self.assertTrue(chat.mentions_selected_references(text), text)
        for text in ("improve the flow", "I added a figure to the methods", "fix the grammar in the introduction"):
            self.assertFalse(chat.mentions_selected_references(text), text)


if __name__ == "__main__":
    unittest.main()
