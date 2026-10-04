"""What a request is about, as opposed to how it asks for it: the sections of an outline that were asked for, the words that say how to write (never searched for), and the guides to
writing (never a reference). No model, no network."""
import unittest

from writing_core import scholar, subject

OUTLINE = """General: write it as a funnel, from the wide problem to the paper's aim.

1. Why risk is hard to study
Risk perception shapes how people act toward hazards.
1) a sub-bullet that restarts at one
2) another sub-bullet

2. How risk perception has been measured
Psychometric scales, factor approaches and regression models.

3. Heterogeneity across people and contexts
Perception differs by country and income.

4. The gap and the aim
No study compares perception across hazards.

5. Roadmap
The rest of the paper is organised as follows.
"""


class Sections(unittest.TestCase):
    def test_the_sections_a_request_asks_for(self):
        for text, want in (("write the introduction from outlined_intro.md sections 1-4", {1, 2, 3, 4}), ("sections 1–4 of the outline", {1, 2, 3, 4}), ("use sections 1, 2 and 3", {1, 2, 3}),
                           ("section 2 only", {2}), ("the first three sections", {1, 2, 3}), ("points 2 to 5", {2, 3, 4, 5}), ("follow outlined_intro.md", None),
                           ("based on the outline", None), ("Table 1 shows it", None), ("in 2024 we surveyed 40 farms", None)):
            self.assertEqual(subject.requested_sections(text), want, text)

    def test_a_number_inside_a_file_name_is_not_a_section(self):
        self.assertIsNone(subject.requested_sections("follow section_2.md and Prompt-3.md"))

    def test_the_outline_is_split_at_its_numbered_sections_and_bullets_stay_inside_them(self):
        blocks = subject.split_outline(OUTLINE)
        self.assertEqual([n for n, _ in blocks], [None, 1, 2, 3, 4, 5])
        self.assertIn("2) another sub-bullet", blocks[1][1], "a list that starts again at 1 is a bullet list, not new sections")

    def test_only_the_sections_asked_for_and_the_general_guidance_are_kept(self):
        text, kept, present = subject.pick_sections(OUTLINE, {1, 2, 3, 4})
        self.assertEqual((kept, present), ([1, 2, 3, 4], [1, 2, 3, 4, 5]))
        self.assertIn("General: write it as a funnel", text)
        self.assertIn("No study compares perception across hazards", text)
        self.assertNotIn("organised as follows", text)

    def test_numbers_the_outline_does_not_have_give_back_all_of_it(self):
        text, kept, present = subject.pick_sections(OUTLINE, {9})
        self.assertEqual((text, kept), (OUTLINE, []))
        flat = "Move from risk to risk perception.\nThen say why it is heterogeneous."
        self.assertEqual(subject.pick_sections(flat, {1, 2})[1], [])

    def test_marked_headings_win_over_plain_numbered_lines(self):
        md = "## Section 1 Context\nx\n1. a list item\n## Section 2 Gap\ny\n## Section 3 Aim\nz"
        self.assertEqual([n for n, _ in subject.split_outline(md)], [1, 2, 3])

    def test_a_long_outline_is_cut_at_a_paragraph_not_in_the_middle_of_one(self):
        out = subject.trim_to(OUTLINE, 40)
        self.assertTrue(out.endswith(".") or out.endswith("hazards.") or "\n" not in out[-3:])
        self.assertLess(len(out), len(OUTLINE))
        self.assertNotIn("Roadmap", out)


class HowNotWhat(unittest.TestCase):
    ASK = ("Write the introduction of my paper based on outlined_intro.md sections 1-4 and the literature review. Use the rules for academic writing of the book to get cohesion, flow, "
           "grammar and orthography right. Cite properly in-text.")

    def test_sentences_that_only_say_how_to_write_are_dropped(self):
        got = subject.drop_how("Write the introduction. Use the rules of academic writing of the book for cohesion and flow. Risk perception is heterogeneous.")
        self.assertNotIn("cohesion", got)
        self.assertIn("Risk perception is heterogeneous", got)

    def test_the_title_of_a_book_named_in_the_request_is_not_a_subject(self):
        got = subject.drop_how("Follow the book Academic Writing for Graduate Students for cohesion. Risk perception matters.")
        self.assertNotIn("Graduate", got)
        self.assertIn("Risk perception matters", got)
        self.assertNotIn("Writing Science", subject.drop_how('use the rules of the book "Writing Science" and write about risk in Colombia'))

    def test_the_search_words_of_the_request_are_about_the_subject(self):
        terms = scholar.focus_terms(subject.of(outline=OUTLINE, abstract="We study how people perceive risk across hazards.", request=self.ASK))
        self.assertIn("perception", terms)
        for meta in ("writing", "academic", "cohesion", "grammar", "orthography", "book", "rules", "outlined", "introduction"):
            self.assertNotIn(meta, terms)

    def test_without_a_model_the_queries_are_the_subject_too(self):
        about = subject.of(outline=OUTLINE, abstract="We study how people perceive risk across hazards.", request=self.ASK)
        queries, usage, how = scholar.choose_queries("", request=self.ASK, subject_text=about, cfg=None)
        self.assertEqual((usage, how), (None, "local"))
        joined = " ".join(queries)
        self.assertIn("perception", joined)
        self.assertNotIn("writing", joined)
        self.assertNotIn("academic", joined)

    def test_a_query_that_is_about_writing_is_refused_unless_the_paper_is(self):
        self.assertTrue(scholar.bad_query("academic writing guidelines", "risk perception hazards"))
        self.assertTrue(scholar.bad_query("introduction structure in research papers", "risk perception"))
        self.assertFalse(scholar.bad_query("risk perception heterogeneity hazards", "risk perception"))
        self.assertFalse(scholar.bad_query("academic writing guidelines", "a study of academic writing in engineering"), "a paper about writing may search for it")


class Guides(unittest.TestCase):
    def test_a_guide_to_writing_is_not_literature(self):
        for t in ("Academic Writing for Graduate Students", "The Elements of Style", "How to write a thesis", "Writing Science: how to write papers that get cited", "A style manual for engineers",
                  "Structure of an introduction in research papers", "Grammar and punctuation in scientific English"):
            self.assertTrue(subject.is_writing_guide(t), t)
        for t in ("Risk perception and trust", "Social cohesion and risk perception after floods", "Chemical composition of urban dust", "Grammar induction for protein sequences", "Armed conflict and pipeline attacks in Colombia", "Heterogeneity of hazard perception across countries", "Soil moisture and crop yield"):
            self.assertFalse(subject.is_writing_guide(t), t)

    def test_a_copy_of_the_imported_book_is_a_guide_whatever_it_is_called(self):
        self.assertTrue(subject.is_style_source("Untitled", "Books/scan-0042.pdf", books={"scan-0042.pdf"}))
        self.assertFalse(subject.is_style_source("Risk perception", "Books/risk.pdf", books={"scan-0042.pdf"}))


if __name__ == "__main__":
    unittest.main()
