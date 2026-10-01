import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import IsolatedHome
from writing_core import book_eval, book_index, chunk_book, config, events, retrieve, store

FIXTURE = Path(__file__).parent / "fixtures" / "mini_book.pdf"
EXAMPLE_EVAL = Path(__file__).parent.parent / "examples" / "book_eval.example.json"
HAVE_POPPLER = bool(shutil.which("pdftotext") and shutil.which("pdftohtml") and shutil.which("pdfinfo"))
needs_pdf = unittest.skipUnless(HAVE_POPPLER, "poppler not installed")


class Cleaning(unittest.TestCase):
    def test_running_headers_footers_and_page_numbers_removed(self):
        def page(i):  # a realistic page: header, many distinct body lines, bare page number
            words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike".split()
            body = "\n".join(f"Sentence {i}-{j} is about {words[(i + j) % 13]} and {words[(i * j + 3) % 13]} only." for j in range(12))
            return f"My Book Title\n\n{body}\n\n{i}\n"

        cleaned = chunk_book.clean_pages([page(i) for i in range(1, 9)])
        for i, lines in enumerate(cleaned, 1):
            text = "\n".join(lines)
            self.assertNotIn("My Book Title", text)
            self.assertIn(f"Sentence {i}-0 is about", text)
            self.assertIn(f"Sentence {i}-11 is about", text)  # edge lines of the body survive
            self.assertNotIn(f"\n{i}\n", "\n" + text + "\n")

    def test_ligatures_and_soft_hyphens_fixed(self):
        out = chunk_book.clean_pages(["ﬁnal ef­fect\n"] * 1)
        self.assertEqual(out[0][0], "final effect")


class Headings(unittest.TestCase):
    def test_find_heading_ignores_numbers_and_finds_wrapped_titles(self):
        lines = ["some long text of a paragraph that is not a heading at all, really, truly not.",
                 "2.3 Reporting the statistical", "analysis", "Body."]
        self.assertEqual(chunk_book.find_heading("Reporting the statistical analysis", lines), 1)
        self.assertEqual(chunk_book.find_heading("Reporting the statistical analysis", ["2.3  Reporting the statistical analysis"]), 0)
        self.assertIsNone(chunk_book.find_heading("Nope", lines))

    def test_body_sentence_containing_title_is_not_a_heading(self):
        lines = ["In this book we explain reporting the statistical analysis in detail for beginners."]
        self.assertIsNone(chunk_book.find_heading("Reporting the statistical analysis", lines))


class Toc(unittest.TestCase):
    def test_dotted_and_gap_styles_levels_and_roman_pages(self):
        dotted = "\n".join([
            "Preface ........................ ix",
            "1 Writing the Introduction ........................ 1",
            "   1.1 Opening with the problem ........................ 1",
            "   1.2 Stating the gap ........................ 3",
            "2 Writing the Methods ........................ 11",
            "   2.1 Participants ........................ 11",
            "Index ........................ 301"])
        entries, last = chunk_book.parse_toc([dotted], min_per_page=5)
        self.assertEqual(last, 0)
        self.assertEqual([(e["level"], e["number"], e["page"]) for e in entries],
                         [(1, "1", 1), (2, "1.1", 1), (2, "1.2", 3), (1, "2", 11), (2, "2.1", 11), (1, "", 301)])
        self.assertNotIn("Preface", [e["title"] for e in entries])  # roman page = front matter

        gap = "\n".join([f"{n}  Chapter title {n}{' ' * 20}{n * 10}" for n in range(1, 8)])
        entries, _ = chunk_book.parse_toc([gap], min_per_page=5)
        self.assertEqual(len(entries), 7)
        self.assertEqual(entries[2]["page"], 30)

    def test_no_toc_found(self):
        self.assertEqual(chunk_book.parse_toc(["just prose\n" * 50]), ([], -1))

    def test_unnumbered_toc_uses_indentation_for_levels(self):
        t = "\n".join(["Alpha chapter ........ 1", "    Sub one ........ 2", "    Sub two ........ 3",
                       "Beta chapter ........ 5", "    Sub three ........ 6"])
        entries, _ = chunk_book.parse_toc([t], min_per_page=5)
        self.assertEqual([e["level"] for e in entries], [1, 2, 2, 1, 2])


class Paragraphs(unittest.TestCase):
    def test_short_sentence_final_line_ends_a_paragraph_and_blank_lines_too(self):
        long = "x" * 100 + " words go here and continue"
        stream = [(1, long), (1, long), (1, "and the paragraph ends here."), (1, long), (1, "second one ends."),
                  (1, ""), (1, "third after blank")]
        paras = chunk_book.to_paragraphs(stream)
        self.assertEqual(len(paras), 3)
        self.assertTrue(paras[0]["text"].endswith("ends here."))

    def test_paragraph_continues_across_page_and_hyphen_is_undone(self):
        long = "word " * 20
        stream = [(1, long.strip()), (1, "an incom-"), (2, "plete sentence that continues here and goes on a bit more to be long enough yes."), (2, "end.")]
        paras = chunk_book.to_paragraphs(stream)
        self.assertEqual((paras[0]["p0"], paras[0]["p1"]), (1, 2))
        self.assertIn("incomplete sentence", paras[0]["text"])


class Packing(unittest.TestCase):
    def paras(self, n, words=60):
        return [{"text": ("w%d " % i) * words, "p0": 1, "p1": 1} for i in range(n)]

    def test_chunks_respect_target_and_never_exceed_max(self):
        chunks = chunk_book.pack_chunks(self.paras(12, 100), target=200, maximum=300, minimum=50)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(store.approx_tokens(c["text"]) <= 300 for c in chunks))

    def test_tiny_tail_merges_into_previous_chunk(self):
        paras = self.paras(2, 100) + [{"text": "tiny tail.", "p0": 2, "p1": 2}]
        chunks = chunk_book.pack_chunks(paras, target=150, maximum=400, minimum=60)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[-1]["text"].endswith("tiny tail."))

    def test_overlong_paragraph_is_split_on_sentences(self):
        text = " ".join(f"This is sentence number {i} of a very long paragraph." for i in range(120))
        chunks = chunk_book.pack_chunks([{"text": text, "p0": 1, "p1": 1}], target=200, maximum=300, minimum=50)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c["text"].rstrip().endswith(".") for c in chunks))


@needs_pdf
class OutlineAndChunks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outline = chunk_book.analyze_outline(FIXTURE)
        cls.pages = chunk_book.clean_pages(chunk_book.extract_pages(FIXTURE))
        cls.chunks, cls.stats = chunk_book.build_chunks(cls.pages, cls.outline, "mini")

    def test_toc_outline_with_offset_and_all_entries_verified(self):
        o = self.outline
        self.assertEqual((o["method"], o["page_offset"], o["n_pdf_pages"]), ("toc", 2, 17))
        self.assertEqual(len(o["entries"]), 21)
        self.assertTrue(all(e["verified"] for e in o["entries"]))
        by_title = {e["title"]: e for e in o["entries"]}
        self.assertEqual((by_title["Writing the Methods"]["pdf_page"], by_title["Writing the Methods"]["page"]), (6, 4))
        self.assertTrue(by_title["Index"]["skip"])

    def test_font_heading_fallback_finds_the_same_structure(self):
        heads = chunk_book.detect_headings_xml(FIXTURE)
        self.assertEqual(len(heads), 21)
        self.assertEqual([h["level"] for h in heads[:3]], [1, 2, 2])
        self.assertEqual(heads[3]["title"], "Writing the Methods")

    def test_window_fallback_when_nothing_else_works(self):
        with mock.patch.object(chunk_book, "parse_toc", return_value=([], -1)), \
                mock.patch.object(chunk_book, "detect_headings_xml", return_value=[]):
            o = chunk_book.analyze_outline(FIXTURE, window=5)
        self.assertEqual(o["method"], "windows")
        self.assertEqual([e["pdf_page"] for e in o["entries"]], [1, 6, 11, 16])
        self.assertTrue(any("windows" in w for w in o["warnings"]))

    def test_chunks_are_named_in_reading_order(self):
        ids = [c["id"] for c in self.chunks]
        self.assertEqual([c["seq"] for c in self.chunks], list(range(1, len(self.chunks) + 1)))
        self.assertEqual(ids[0], "B:mini:c01.s00.k01")
        self.assertIn("B:mini:c02.s03.k01", ids)
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(self.chunks), 20)

    def test_labels_use_printed_pages_not_pdf_pages(self):
        c = next(c for c in self.chunks if c["id"] == "B:mini:c02.s03.k01")
        self.assertEqual((c["page_start"], c["page_end"], c["pdf_start"], c["pdf_end"]), (6, 7, 8, 9))
        self.assertEqual(c["label"], 'ch02 §2.3 "Reporting the statistical analysis" · pp.6-7')
        self.assertEqual(c["heading_path"], "Writing the Methods > Reporting the statistical analysis")

    def test_index_and_front_matter_are_not_chunked(self):
        text = " ".join(c["text"] for c in self.chunks)
        self.assertNotIn("effect size, 6. gap, 2.", text)  # the index page
        self.assertNotIn("Test fixture", text)
        self.assertNotIn("A Short Guide to Writing Research Papers", text)  # running header removed

    def test_chunks_stay_inside_their_section_and_pages_cross_correctly(self):
        sec11 = next(c for c in self.chunks if c["id"] == "B:mini:c01.s01.k01")
        sec12 = next(c for c in self.chunks if c["id"] == "B:mini:c01.s02.k01")
        # the paragraph that runs over the page break belongs to 1.1, the heading that follows starts 1.2
        self.assertTrue(sec11["text"].endswith("waiting for the contribution."))
        self.assertTrue(sec12["text"].startswith("A gap is a specific thing"))
        self.assertNotIn("Stating the gap", sec11["text"])

    def test_parts_are_tagged_from_headings(self):
        tags = {c["id"].split(":")[-1]: set(c["parts"]) for c in self.chunks}
        self.assertIn("methods.analysis", tags["c02.s03.k01"])
        self.assertIn("methods", tags["c02.s03.k01"])
        self.assertIn("style", tags["c05.s02.k01"])
        self.assertIn("limitations", tags["c04.s02.k01"])


@needs_pdf
class BookIndexLifecycle(IsolatedHome):
    def ingest(self, **kw):
        return book_index.ingest(FIXTURE, "mini", **kw)

    def test_ingest_once_then_skip_with_zero_writes(self):
        self.assertEqual(self.ingest()["status"], "ingested")
        p = book_index.paths("mini")
        before = {k: (p[k].stat().st_mtime_ns, p[k].stat().st_size) for k in ("chunks", "db", "manifest", "outline")}
        r = self.ingest()
        self.assertEqual((r["status"], r["writes"]), ("skipped", 0))
        after = {k: (p[k].stat().st_mtime_ns, p[k].stat().st_size) for k in before}
        self.assertEqual(before, after)
        evs = events.read(p["dir"], types={"book_ingest"})
        self.assertEqual([e.get("skipped") for e in evs], [False, True])
        self.assertEqual(evs[0]["chunks"], 20)
        self.assertEqual(evs[0]["method"], "toc")

    def test_different_pdf_for_same_id_is_refused(self):
        self.ingest()
        other = self.tmp / "other.pdf"
        other.write_bytes(FIXTURE.read_bytes() + b"\n% changed")
        with self.assertRaises(book_index.BookError):
            book_index.ingest(other, "mini")
        self.assertEqual(book_index.ingest(other, "mini", force=True)["status"], "rebuilt")

    def test_outline_checkpoint_edit_then_rebuild_applies_it(self):
        r = self.ingest(outline_only=True)
        self.assertEqual(r["status"], "outline")
        self.assertIsNone(book_index.manifest("mini"))  # nothing built yet
        o = book_index.read_outline("mini")
        for e in o["entries"]:
            if e["title"] == "Reporting Results":
                e["skip"] = True  # the reader edits outline.json
        book_index.write_outline("mini", o)
        built = self.ingest()["manifest"]  # plain run builds from the EDITED outline
        self.assertLess(built["n_chunks"], 20)
        chunks = book_index.read_chunks("mini")
        self.assertFalse(any(c["chapter"] == 3 and c["section"] == 0 for c in chunks))
        o["entries"] = [dict(e, skip=False) if e["title"] == "Reporting Results" else e for e in o["entries"]]
        book_index.write_outline("mini", o)
        r = self.ingest()
        self.assertEqual(r["status"], "skipped")
        self.assertIn("--rebuild", r["note"])  # edits are noticed, never applied silently
        self.assertEqual(self.ingest(rebuild=True)["manifest"]["n_chunks"], 20)

    def test_search_index_is_a_disposable_cache(self):
        self.ingest()
        p = book_index.paths("mini")
        p["db"].unlink()
        con = book_index.open_book("mini")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], 20)
        con.close()
        p["db"].write_bytes(b"garbage" * 500)
        con = book_index.open_book("mini")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0], 20)
        con.close()

    def test_not_imported_and_scanned_pdf_errors_are_clear(self):
        with self.assertRaises(book_index.BookError):
            book_index.open_book("nope")
        with mock.patch.object(chunk_book, "extract_pages", return_value=["", "", ""]), \
                mock.patch.object(chunk_book, "page_count", return_value=3):
            with self.assertRaises(book_index.BookError) as cm:
                book_index.ingest(FIXTURE, "scanned")
        self.assertIn("selectable text", str(cm.exception))

    def test_tree_and_neighbours(self):
        self.ingest()
        t = book_index.tree("mini")
        self.assertEqual([c["chapter"] for c in t], [1, 2, 3, 4, 5, 6])
        self.assertEqual(t[1]["sections"][0]["title"], "(chapter opening)")
        c = book_index.get_chunk("mini", "B:mini:c02.s01.k01")
        self.assertEqual((c["prev"], c["next"]), ("B:mini:c02.s00.k01", "B:mini:c02.s02.k01"))
        with self.assertRaises(book_index.BookError):
            book_index.get_chunk("mini", "B:mini:c99.s00.k01")


@needs_pdf
class BookQuery(IsolatedHome):
    def setUp(self):
        super().setUp()
        book_index.ingest(FIXTURE, "mini")

    def ids(self, res):
        return [c["id"].split(":")[-1] for c in res["chunks"]]

    def test_request_routes_to_part_and_only_that_part_comes_back(self):
        res = retrieve.book_query("mini", "improve my methodology")
        self.assertEqual(res["parts"], ["methods"])
        self.assertTrue(all("methods" in c["parts"] for c in res["chunks"] if c["via"] == "routed"))
        self.assertTrue(all(i.startswith("c02") for i in self.ids(res)))

    def test_subpart_boost_puts_the_analysis_section_first(self):
        res = retrieve.book_query("mini", "how do I report the statistical analysis in my methods")
        self.assertEqual(self.ids(res)[0], "c02.s03.k01")

    def test_style_topic_and_part_in_one_request_route_to_both(self):
        res = retrieve.book_query("mini", "tense and passive voice in my methods")
        self.assertEqual(set(res["parts"]), {"style", "methods"})
        self.assertEqual(self.ids(res)[0], "c05.s01.k01")

    def test_budget_is_respected_and_style_chunks_are_capped(self):
        res = retrieve.book_query("mini", "describe the sampling and participants in methods", budget=250)
        self.assertLessEqual(res["tokens"], 250 * 1.25)
        self.assertLessEqual(len([c for c in res["chunks"] if c["via"] == "style"]), 2)
        big = retrieve.book_query("mini", "describe the sampling and participants in methods", budget=5000, max_chunks=6)
        self.assertLessEqual(len([c for c in big["chunks"] if c["via"] == "routed"]), 6)

    def test_explicit_part_wins_and_bad_part_is_rejected(self):
        res = retrieve.book_query("mini", "anything about words", part="abstract")
        self.assertEqual(res["parts"], ["abstract"])
        routed = [c["id"].split(":")[-1] for c in res["chunks"] if c["via"] == "routed"]
        self.assertTrue(routed and all(i.startswith("c06") for i in routed))  # style chunks may ride along, capped
        with self.assertRaises(ValueError):
            retrieve.book_query("mini", "x", part="introdution")

    def test_part_with_no_tagged_chunks_falls_back_to_the_whole_book(self):
        res = retrieve.book_query("mini", "writing the conclusion")
        self.assertEqual(res["fallback"], "whole-book")
        self.assertTrue(res["chunks"])

    def test_reading_order_fallback_when_only_part_words_are_given(self):
        res = retrieve.book_query("mini", "improve my methodology")
        self.assertEqual(res["fallback"], "reading-order")
        self.assertEqual(self.ids(res), sorted(self.ids(res)))

    def test_events_book_log_has_no_paper_and_paper_log_has_ids(self):
        scope = self.tmp / "paper" / ".overleaf-skill"
        retrieve.book_query("mini", "statistical analysis", paper="p1", paper_scope=scope)
        book_ev = events.read(book_index.paths("mini")["dir"], types={"retrieve"})[-1]
        paper_ev = events.read(scope, types={"retrieve"})[-1]
        self.assertNotIn("paper", book_ev)
        self.assertEqual(paper_ev["paper"], "p1")
        self.assertEqual(book_ev["ids"], paper_ev["ids"])
        self.assertTrue(all(i.startswith("B:mini:") for i in paper_ev["ids"]))

    def test_trace_and_format(self):
        res = retrieve.book_query("mini", "statistical analysis")
        self.assertTrue(res["trace"].startswith("[book mini:"))
        text = retrieve.format_book_result(res)
        self.assertIn("B:mini:c02.s03.k01", text)
        self.assertIn('§2.3 "Reporting the statistical analysis" · pp.6-7', text)

    def test_example_eval_file_meets_the_baseline_on_the_fixture(self):
        out = book_eval.run("mini", json.loads(EXAMPLE_EVAL.read_text()), k=3)
        self.assertGreaterEqual(out["hit_at_k"], 0.8)  # the fixture lacks 4 of the 25 topics by design
        self.assertEqual(len(book_eval.from_headings("mini", n=25)), 14)


if __name__ == "__main__":
    unittest.main()
