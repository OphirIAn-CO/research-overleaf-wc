"""Book advice that finds itself: tags from what a passage is about, exercise pages set aside, rule cards kept when the book is prepared again."""
import json
import unittest
from pathlib import Path

from tests.helpers import IsolatedHome
from tests.test_ui_server import UiBase
from tests.test_book import FIXTURE, needs_pdf
from writing_core import book_index, chunk_book, config, events, retrieve, store, taxonomy

METHODS_PAGE = ("In the methods section describe the participants and how the sample was drawn. State the sampling procedure, the data collection "
                "instruments and the measures. Explain the data analysis and the statistical analysis so that another researcher can repeat the study. "
                "A good methodology paragraph names each procedure once and in the order it was carried out.")
EXERCISE_PAGE = ("2. Do you think the level of detail is sufficient or insufficient? In other words, is there enough information to allow the study to be adapted?\n\n"
                 "3. Do you think the sample scenarios are needed? Should the authors have described how the final sample was derived? 4. How is the information organized? "
                 "What subheadings could you add to help readers? 5. What verb tense dominates? Why did the authors choose this tense? 6. Does active or passive voice dominate? Why?")
HANDBOOK_ADVICE = ("Keep sentences short. Put the main point at the start of each paragraph and avoid long chains of nouns. Prefer the active voice, "
                   "and use the same word for the same thing throughout. Every paragraph should make one point and link to the next.")


class Tagging(unittest.TestCase):
    def test_a_passage_is_tagged_by_what_it_is_about(self):
        self.assertEqual(taxonomy.content_tags(METHODS_PAGE)[:1], ["methods"])
        self.assertEqual(taxonomy.content_tags(HANDBOOK_ADVICE), [])                  # everyday writing advice is not "about" a paper part
        self.assertEqual(taxonomy.content_tags("The results show that results differ."), [])   # one or two mentions are not enough
        self.assertNotIn("style", taxonomy.content_tags("paragraph sentence words style clarity " * 20))   # style stays a heading-level tag

    def test_counts_every_occurrence_not_just_presence(self):
        self.assertGreaterEqual(taxonomy.counts("sample sample sample participants")["methods"], 4)

    def test_exercise_pages_are_recognised_and_advice_is_not(self):
        self.assertTrue(chunk_book.is_exercise(EXERCISE_PAGE))
        self.assertFalse(chunk_book.is_exercise(METHODS_PAGE))
        self.assertFalse(chunk_book.is_exercise(HANDBOOK_ADVICE + " Why does this matter? Because readers skim."))


def chunk(i, text, kind="", parts=None, card=""):
    return {"id": f"B:t:c01.s00.k{i:02d}", "seq": i, "chapter": 1, "section": 0, "k": i, "chapter_number": "1", "section_number": "", "chapter_title": "Handbook",
            "section_title": "", "heading_path": "Handbook", "pdf_start": i, "pdf_end": i, "page_start": i, "page_end": i, "label": f'ch01 "Handbook" · p.{i}',
            "text": text, "tokens": store.approx_tokens(text), "parts": parts if parts is not None else [], "rule_card": card, "kind": kind}


class Retrieval(IsolatedHome):
    def make(self, chunks):
        p = book_index.paths("t")
        p["dir"].mkdir(parents=True)
        book_index._atomic_write(p["chunks"], "".join(json.dumps(c) + "\n" for c in chunks))
        book_index.build_sqlite(chunks, p["db"], {"book_id": "t"})
        book_index._atomic_write(p["manifest"], json.dumps({"book_id": "t", "schema": book_index.SCHEMA, "n_chunks": len(chunks)}))

    def test_exercise_pages_are_never_handed_out_even_by_the_whole_book_search(self):
        self.make([chunk(1, METHODS_PAGE, parts=["methods"]), chunk(2, EXERCISE_PAGE + " methods sample", kind="exercise", parts=[]),
                   chunk(3, HANDBOOK_ADVICE, parts=["style"])])
        res = retrieve.book_query("t", "how do I write the methods sample", log=False)
        ids = [c["id"] for c in res["chunks"]]
        self.assertIn("B:t:c01.s00.k01", ids)
        self.assertNotIn("B:t:c01.s00.k02", ids)
        res = retrieve.book_query("t", "sample derived organized subheadings", part=None, log=False)       # words only the exercise page has
        self.assertNotIn("B:t:c01.s00.k02", [c["id"] for c in res["chunks"]])

    def test_log_false_leaves_no_trace(self):
        self.make([chunk(1, METHODS_PAGE, parts=["methods"])])
        retrieve.book_query("t", "methods sample", log=False)
        self.assertEqual(list(book_index.paths("t")["dir"].glob("events-*.jsonl")), [])

    def test_an_index_built_by_older_code_is_made_again(self):
        self.make([chunk(1, METHODS_PAGE, parts=["methods"])])
        p = book_index.paths("t")
        con = store.connect(p["db"])
        store.meta_set(con, "schema", 1)
        con.execute("ALTER TABLE chunks RENAME TO chunks_old")       # what a version-1 index looks like to the new queries: no kind column
        con.commit()
        con.close()
        res = retrieve.book_query("t", "methods sample", log=False)
        self.assertEqual(len(res["chunks"]), 1)


@needs_pdf
class Prepare(IsolatedHome):
    def test_preparing_again_keeps_the_advice_summaries_and_records_the_version(self):
        man = book_index.ingest(FIXTURE, "mini")["manifest"]
        self.assertEqual(man["logic_version"], chunk_book.LOGIC_VERSION)
        first = book_index.read_chunks("mini")[0]["id"]
        book_index.update_rule_cards("mini", {first: "1. Do this. (p.1)"})
        # pretend an older release built it, then prepare again
        mp = book_index.paths("mini")["manifest"]
        old = json.loads(mp.read_text())
        old["logic_version"] = 1
        mp.write_text(json.dumps(old))
        res = book_index.ingest(FIXTURE, "mini", rebuild=True)
        self.assertEqual(res["status"], "rebuilt")
        self.assertEqual(res["manifest"]["logic_version"], chunk_book.LOGIC_VERSION)
        kept = {c["id"]: c["rule_card"] for c in book_index.read_chunks("mini")}
        self.assertEqual(kept[first], "1. Do this. (p.1)")
        self.assertEqual(res["manifest"]["rule_cards"], 1)


@needs_pdf
class BookApi(UiBase):
    def setUp(self):
        super().setUp()
        book_index.ingest(FIXTURE, "mini")
        self.c.login()

    def test_state_flags_and_the_check_for_each_part(self):
        st, state = self.c.get("/api/state")
        b = state["books"][0]
        self.assertEqual((b["outdated"], b["rule_cards"], b["exercises"]), (False, 0, 0))
        before = len(events.read(book_index.paths("mini")["dir"], types={"retrieve"}))
        st, chk = self.c.get("/api/book/mini/check")
        self.assertEqual(st, 200, chk)
        self.assertEqual([r["part"] for r in chk["rows"]], ["title", "abstract", "introduction", "background", "methods", "results", "discussion", "conclusion"])
        methods = [r for r in chk["rows"] if r["part"] == "methods"][0]
        self.assertTrue(methods["chunks"])
        self.assertTrue(all(c["label"] and c["pages"] for c in methods["chunks"]))
        self.assertEqual(len(events.read(book_index.paths("mini")["dir"], types={"retrieve"})), before)      # the check is a look, not a use
        self.assertEqual(self.c.get("/api/book/nope/check")[0], 404)
        mp = book_index.paths("mini")["manifest"]
        old = json.loads(mp.read_text())
        old.pop("logic_version")
        mp.write_text(json.dumps(old))
        self.assertTrue(self.c.get("/api/state")[1]["books"][0]["outdated"])


if __name__ == "__main__":
    unittest.main()
