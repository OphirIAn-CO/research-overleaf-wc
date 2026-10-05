"""The literature library as references: counting what the paper's files hold, turning every document of the library into a bibliography entry (a record, a DOI, a title Crossref
knows, or the file's own details), and saying honestly when a request is not something the app does. doi.org and Crossref are tables; nothing touches the network."""
import json
import re
import unittest
from pathlib import Path

import doi_bot
from tests.pdfmaker import make_pdf
from tests.test_book import needs_pdf
from tests.test_outline_citations import BIB_RISK, HETERO, RISK, RISK_PLAIN
from tests.test_scholar import Net
from tests.test_v2_chat_api import ChatBase
from writing_core import chat, config, intent, library, scholar

SCAN = [["# Methods", "We sampled 40 farms by stratified random sampling and measured moisture weekly with capacitance probes at thirty centimetres."]]
NO_CROSSREF = json.dumps({"message": {"items": []}})


class Rules(unittest.TestCase):
    def test_asking_for_the_library_as_references_is_told_from_the_other_requests(self):
        yes = ["convert the documents in my library to references", "turn my literature into a bibliography", "bring my library into the bibliography", "make references from all my papers",
               "convert the 65 literature-review documents into references with tags"]
        for t in yes:
            self.assertEqual(chat.intent(t), "bibliography", t)
        no = ["add citations from my library to the introduction", "what do my articles say about irrigation?", "write the introduction using my literature library and cite in-text"]
        for t in no:
            self.assertNotEqual(chat.intent(t), "bibliography", t)

    def test_a_question_about_what_the_files_hold_is_a_count_not_a_search(self):
        for t in ["how many references are there now in my .bib file ?", "how many figures does my paper have", "count the tables", "which documents of my library are not in the bibliography"]:
            self.assertEqual(chat.intent(t), "inspect", t)
        self.assertNotEqual(chat.intent("improve the flow of the text before Figure 1"), "inspect")

    def test_the_new_kinds_are_known_to_the_reader(self):
        for k in ("inspect", "bibliography", "other"):
            self.assertIn(k, intent.INTENTS)
            self.assertIn(k, intent.ALL)

    def test_the_questions_that_ask_the_author_to_choose_sources_are_told_from_the_others(self):
        for q in ["Which bibliography sources should replace the reference placeholders for risk definitions?", "Can you provide the source for the comparison of air travel and car travel?",
                  "Which citations would support the claim about security?"]:
            self.assertTrue(library.is_source_question(q), q)
        for q in ["What was the response rate of the farmers?", "Which data source should I use for the sample?", "What is the source of the data in Table 2?", "Which period should the trend cover?"]:
            self.assertFalse(library.is_source_question(q), q)
        keep, dropped = library.drop_source_questions(["Which sources support the claim?", "How many farms were sampled?"])
        self.assertEqual((keep, len(dropped)), (["How many farms were sampled?"], 1))


@needs_pdf
class Bring(ChatBase):
    def setUp(self):
        super().setUp()
        self._doi, self._fetch = doi_bot.fetch_bibtex, scholar.fetch
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", self._doi)
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self._push = doi_bot.git_sync
        self.addCleanup(setattr, doi_bot, "git_sync", self._push)
        self.pushed = []
        doi_bot.git_sync = lambda bib, msg: (self.pushed.append(msg) or True, "Pushed to Overleaf.")
        doi_bot.fetch_bibtex = lambda d: BIB_RISK if d == "10.5555/risk.2019.007" else (_ for _ in ()).throw(RuntimeError("HTTP 404"))
        library._ENTRY_CACHE.clear()
        library._TITLE_CACHE.clear()
        self.addCleanup(library._ENTRY_CACHE.clear)
        self.addCleanup(library._TITLE_CACHE.clear)
        scholar.fetch = Net({"https://api.crossref.org/works": NO_CROSSREF})
        self.bib = Path(self.proj["path"]) / self.proj["bib"]
        self.lib = self.tmp / "Documents" / "Literature"
        self.lib.mkdir(parents=True)
        make_pdf(self.lib / "Smith et al. - 2019 - Risk perception and trust across hazards.pdf", RISK)
        make_pdf(self.lib / "Garcia - 2018 - Heterogeneity of hazard perception across countries and contexts.pdf", HETERO)
        make_pdf(self.lib / "scan0001.pdf", SCAN)
        library.set_folders([str(self.lib)])
        library.update()

    def test_counting_what_the_files_hold_answers_at_once_and_asks_nobody(self):
        r = self.send("how many references are there now in my .bib file ?")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "text"])
        self.assertIsNone(r.get("job"))
        text = r["messages"][1]["text"]
        n = len(library.paper_bib(self.proj)["entries"])
        self.assertIn(f"has {n} entr", text)
        self.assertIn("literature library has 3 documents", text)
        self.assertIn("bring my library into the bibliography", text)
        self.assertEqual(self.oa.requests, [], "nothing is sent to OpenAI to count")

    def test_the_library_becomes_reference_entries_the_author_ticks_and_adds(self):
        before = self.bib.read_text()
        r = self.send("convert the documents in my library to references")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text"])
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        m = self.last("bring")
        rows = {x["label"]: x for x in m["data"]["rows"]}
        self.assertEqual(m["data"]["counts"], {"new": 2, "have": 0, "cannot": 1})
        by_status = {s: [x for x in m["data"]["rows"] if x["status"] == s] for s in ("new", "cannot")}
        self.assertEqual(sorted(x["origin"] for x in by_status["new"]), ["doi", "file"], "one has a DOI, the other only what its own file says")
        self.assertEqual(len(by_status["cannot"]), 1)
        self.assertIn("too little", by_status["cannot"][0]["why"] + " too little", "the reason is given")
        self.assertEqual(self.bib.read_text(), before, "nothing is written until the author presses the button")
        ids = [x["doc"] for x in by_status["new"]]
        st, res = self.c.post("/api/library/bring", {"paper": "alpha", "message": m["id"], "doc_ids": ids})
        self.assertEqual(st, 200, res)
        self.assertEqual(self.wait_job(res["job"])["status"], "done")
        text = self.bib.read_text()
        for x in by_status["new"]:
            self.assertRegex(text, r"@\w+\s*\{\s*" + re.escape(x["key"]) + r"\s*,")
        self.assertIn("Entry made from the file", text, "an entry made from a file is marked so that it gets checked")
        done = self.last("bring")
        self.assertEqual((done["data"]["state"], len(done["data"]["result"]["added"])), ("done", 2))
        self.assertTrue(done["data"]["pushed"] and self.pushed, "the bibliography goes to Overleaf like any added reference")
        st, again = self.c.post("/api/library/bring", {"paper": "alpha", "message": m["id"], "doc_ids": ids})
        self.assertEqual(st, 409, "a list is used once")
        # asking again: everything that could be added is in the bibliography now
        r2 = self.send("turn my literature into a bibliography")
        self.wait_job(r2["job"])
        self.assertEqual(self.last("bring")["data"]["counts"], {"new": 0, "have": 2, "cannot": 1})

    def test_only_documents_of_the_list_can_be_added(self):
        r = self.send("bring my library into the bibliography")
        self.wait_job(r["job"])
        m = self.last("bring")
        cannot = next(x["doc"] for x in m["data"]["rows"] if x["status"] == "cannot")
        st, res = self.c.post("/api/library/bring", {"paper": "alpha", "message": m["id"], "doc_ids": [cannot, "not-a-document"]})
        self.assertEqual(st, 400, res)

    def test_an_empty_library_says_so(self):
        library.set_folders([])
        r = self.send("convert the documents in my library to references")
        self.assertIn("empty or not chosen", r["messages"][1]["text"])
        self.assertIsNone(r.get("job"))

    def test_a_request_the_app_cannot_do_is_said_instead_of_doing_something_else(self):
        r = self.send("paint my paper blue", flag="other")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "text"])
        self.assertIn("not something I can do yet", r["messages"][1]["text"])
        self.assertEqual(self.oa.requests, [])


@needs_pdf
class EveryDocumentIsCitable(ChatBase):
    """The library is read for a request to write, and a document whose file is all there is still becomes a citation (marked), so the model is never asked to choose sources for the author."""

    def test_a_file_with_authors_and_a_title_but_no_doi_is_cited_with_an_entry_made_from_the_file(self):
        scholar_fetch = scholar.fetch
        self.addCleanup(setattr, scholar, "fetch", scholar_fetch)
        scholar.fetch = Net({"https://api.crossref.org/works": NO_CROSSREF})
        lib = self.tmp / "Documents" / "Literature"
        lib.mkdir(parents=True)
        make_pdf(lib / "Garcia - 2018 - Heterogeneity of hazard perception across countries and contexts.pdf", HETERO)
        library.set_folders([str(lib)])
        library.update()
        d = library.docs_all()[0]
        entry, why = library.entry_for(self.proj, {"doc": d["id"], "title": d["title"], "label": d["label"]}, "", ())
        self.assertEqual(why, "")
        self.assertEqual(entry["origin"], "file")
        self.assertIn("Garcia", entry["key"])
        self.assertIn("Check them before submitting", entry["bibtex"])
        self.assertIn("file_entry_warning", dir(library))
        self.assertIn("1 of the sources have no bibliography record", library.file_entry_warning([entry]))


class FileEntries(unittest.TestCase):
    def test_a_journal_header_is_not_taken_for_the_title_and_the_file_name_is(self):
        d = {"title": "Reliabili- Engineering and System Safety 59 (1998) 49\u201362", "authors": "", "year": "1998", "rel": "The role of risk perception for risk management.pdf"}
        bib, key = library.local_entry(d, "")
        self.assertIn("The role of risk perception for risk management", bib)
        self.assertNotIn("Engineering and System Safety", bib)
        self.assertTrue(key.startswith("Anon1998"), key)
        self.assertIsNone(library.local_entry(dict(d, rel="scan0001.pdf"), ""), "a header and a file name that says nothing: too little to cite")

    def test_the_note_says_what_really_happened(self):
        d = {"title": "Public readiness for hydrogen infrastructure in community settings", "authors": "Ning Huan", "year": "2025", "rel": "Dimitros1.pdf"}
        with_doi = library.local_entry(d, "", doi="10.1016/j.cities.2025.106600")[0]
        self.assertIn("doi.org did not answer for its DOI", with_doi)
        self.assertNotIn("no DOI or record was found", with_doi)
        without = library.local_entry(d, "")[0]
        self.assertIn("no DOI or record was found", without)


if __name__ == "__main__":
    unittest.main()
