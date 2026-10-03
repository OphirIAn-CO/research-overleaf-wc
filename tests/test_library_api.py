"""The library in the web app: choosing the folders, reading them with progress, looking inside, searching, and a folder of writing material for one paper that may hold PDFs."""
import unittest
from pathlib import Path

from tests.helpers import common
from tests.pdfmaker import make_pdf
from tests.test_book import needs_pdf
from tests.test_files import docx
from tests.test_library import BIB, IRRIGATION, SOIL
from tests.test_paper import paper
from tests.test_ui_server import UiBase
from tests.test_v2_refs import Stubs
from writing_core import library


class LibraryApi(UiBase):
    def setUp(self):
        super().setUp()
        self.c.login()
        self.lib = self.tmp / "Documents" / "Literature"
        (self.lib / "soil").mkdir(parents=True)
        make_pdf(self.lib / "soil" / "Smith et al. - 2020 - Soil moisture.pdf", SOIL)
        make_pdf(self.lib / "irrigation.pdf", IRRIGATION)
        (self.lib / "refs.bib").write_text(BIB)

    def post_folders(self, *paths):
        st, r = self.c.post("/api/library/folders", {"folders": list(paths)})
        self.assertEqual(st, 200, r)
        return r["status"]

    def read_all(self, rebuild=False):
        st, r = self.c.post("/api/library/index", {"rebuild": rebuild})
        self.assertEqual(st, 200, r)
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        return j

    def test_it_starts_empty_and_says_what_to_do(self):
        st, r = self.c.get("/api/library")
        self.assertEqual(st, 200)
        self.assertEqual((r["status"]["folders"], r["status"]["docs"], r["docs"], r["status"]["in_docker"]), ([], 0, [], False))
        st, r = self.c.post("/api/library/index", {})
        self.assertEqual(st, 400)
        self.assertIn("Add a folder", r["error"])

    def test_the_folders_are_saved_with_their_reason_and_read_with_progress(self):
        st = self.post_folders(str(self.lib), str(self.tmp / "missing" / "x" / "y"))
        self.assertEqual([f["ok"] for f in st["folders"]], [True, False])
        self.assertEqual((st["new"], st["docs"]), (3, 0), "three files wait to be read (two articles and the .bib)")
        j = self.read_all()
        self.assertEqual(j["progress"], [3, 3])
        self.assertEqual((j["result"]["new"], j["result"]["failed"], j["result"]["docs"]), (3, 0, 3))
        st, r = self.c.get("/api/library")
        self.assertEqual((r["status"]["docs"], r["status"]["new"], r["status"]["changed"], r["matching"]), (3, 0, 0, 3))
        self.assertEqual(r["status"]["folders"][0]["docs"], 3)
        titles = {d["rel"]: d for d in r["docs"]}
        self.assertEqual(titles["soil/Smith et al. - 2020 - Soil moisture.pdf"]["label"], "Smith and Lee 2020")
        self.assertEqual([d["rel"] for d in self.c.get("/api/library?q=smith")[1]["docs"]], ["soil/Smith et al. - 2020 - Soil moisture.pdf"])

    def test_the_parts_of_a_document_and_a_search_through_all_of_them(self):
        self.post_folders(str(self.lib))
        self.read_all()
        doc = next(d for d in self.c.get("/api/library")[1]["docs"] if d["rel"] == "irrigation.pdf")
        st, r = self.c.get(f"/api/library/parts?doc={doc['id']}")
        self.assertEqual((st, r["doc"]["rel"], len(r["parts"])), (200, "irrigation.pdf", 1))
        self.assertIn("Deficit irrigation", r["parts"][0]["preview"])
        self.assertEqual(self.c.get("/api/library/parts?doc=nope")[0], 404)
        st, r = self.c.get("/api/library/search?q=deficit+irrigation+maize")
        self.assertEqual((st, r["chunks"][0]["rel"]), (200, "irrigation.pdf"))
        self.assertEqual(self.c.get("/api/library/search?q=")[0], 400)
        self.assertEqual(self.c.get("/api/library/search?q=quantum+chromodynamics")[1]["chunks"], [])

    def test_a_search_for_a_paper_says_which_documents_it_already_cites(self):
        name, proj = paper(self)
        bib = Path(proj["path"]) / proj["bib"]
        bib.write_text(bib.read_text() + "\n@article{smithetal2020, author={Smith, J.}, title={Soil moisture and crop yield in semi-arid farms}, year={2020}, doi={10.1234/abc.2020.001}}\n")
        self.post_folders(str(self.lib))
        self.read_all()
        hits = self.c.get("/api/library/search?q=soil+moisture+crop+yield+farms&paper=alpha")[1]["chunks"]
        smith = next(h for h in hits if h["label"] == "Smith and Lee 2020")
        self.assertEqual((smith["in_bib"], smith["cite_key"]), (True, "smithetal2020"))
        self.assertEqual(self.c.get("/api/library/search?q=soil&paper=nope")[0], 404)

    def test_new_and_removed_files_show_before_reading_and_a_rebuild_starts_over(self):
        self.post_folders(str(self.lib))
        self.read_all()
        make_pdf(self.lib / "new.pdf", [["# Roots", "Root depth controls access to deep water during dry spells in many crops and soils."]])
        (self.lib / "irrigation.pdf").unlink()
        st = self.c.get("/api/library")[1]["status"]
        self.assertEqual((st["new"], st["changed"], st["removed"]), (1, 0, 1))
        j = self.read_all()
        self.assertEqual((j["result"]["new"], j["result"]["removed"], j["result"]["unchanged"]), (1, 1, 2))
        j = self.read_all(rebuild=True)
        self.assertEqual((j["result"]["new"], j["result"]["unchanged"]), (3, 0), "starting over reads everything again")

    def test_two_readings_at_once_are_refused(self):
        self.post_folders(str(self.lib))
        library._STATE["running"] = True
        try:
            st, r = self.c.post("/api/library/index", {})
            self.assertEqual(st, 409)
            self.assertIn("being read already", r["error"])
        finally:
            library._STATE["running"] = False

    def test_bad_requests_are_refused(self):
        self.assertEqual(self.c.post("/api/library/folders", {"folders": "nope"})[0], 400)
        self.assertEqual(self.c.post("/api/library/folders", {"folders": ["a\nb"]})[0], 400)
        self.assertEqual(self.c.post("/api/library/folders", {"folders": [f"/tmp/x/y/z{i}" for i in range(20)]})[0], 400)

    def test_folders_shared_with_the_app_are_offered_by_path(self):
        common.save("ui.json", {"data_roots": [str(self.lib)]})
        st = self.c.get("/api/library")[1]["status"]
        self.assertEqual(st["shared"], [str(self.lib.resolve())])


@needs_pdf
class WritingMaterial(UiBase):
    """A folder chosen for one paper: notes, interviews and articles, read in place, with their pages and headings."""

    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)
        self.c.login()
        self.mat = self.tmp / "Documents" / "Material"
        (self.mat / "interviews").mkdir(parents=True)
        make_pdf(self.mat / "report.pdf", IRRIGATION)
        (self.mat / "interviews" / "Garcia.docx").write_bytes(docx([("Heading1", "Findings"), ("Normal", "Farmers said the water came late in 2021. " * 3)]))
        (self.mat / "notes.md").write_text("# Notes\n\nThe survey ran in 2021 across forty farms and three regions.\n")

    def test_pdf_and_word_files_in_a_chosen_folder_are_read_with_the_folder_counted(self):
        st, r = self.c.post("/api/files/folders", {"paper": "alpha", "folders": [str(self.mat)]})
        self.assertEqual(st, 200, r)
        self.assertEqual(sorted(f["name"] for f in r["files"]), ["Garcia.docx", "notes.md", "report.pdf"])
        self.assertTrue(all(f["parts"] for f in r["files"]), r["files"])
        folder = r["folders"][0]
        self.assertEqual((folder["ok"], folder["files"], folder["parts"]), (True, 3, sum(f["parts"] for f in r["files"])))
        st, g = self.c.get("/api/files?paper=alpha")
        self.assertEqual(g["folders"][0]["files"], 3)
        self.assertEqual(g["shared"], [])

    def test_inside_the_project_a_pdf_is_still_a_figure_not_material(self):
        proj_dir = Path(self.proj["path"])
        make_pdf(proj_dir / "fig1.pdf", IRRIGATION)
        st, g = self.c.get("/api/files?paper=alpha")
        self.assertNotIn("fig1.pdf", [f["name"] for f in g["files"]])


@needs_pdf
class InTheChat(UiBase, Stubs):
    """"What do my articles say about...?" is answered from the library on this computer, with how to cite what it finds."""

    def setUp(self):
        super().setUp()
        self.install()
        self.name, self.proj = paper(self)
        self.c.login()
        self.lib = self.tmp / "Documents" / "Literature"
        self.lib.mkdir(parents=True)
        make_pdf(self.lib / "Smith et al. - 2020 - Soil moisture.pdf", SOIL)
        make_pdf(self.lib / "irrigation.pdf", IRRIGATION)

    def tearDown(self):
        self.restore()
        super().tearDown()

    def ask(self, text):
        st, r = self.c.post("/api/chat/send", {"paper": "alpha", "text": text})
        self.assertEqual(st, 200, r)
        return r["messages"][-1]

    def read_library(self, *extra):
        self.c.post("/api/library/folders", {"folders": [str(self.lib)]})
        for f in extra:
            f()
        st, r = self.c.post("/api/library/index", {})
        self.assertEqual(self.wait_job(r["job"])["status"], "done")

    def doc(self, rel):
        return next(d for d in self.c.get("/api/library")[1]["docs"] if d["rel"] == rel)

    def test_without_a_library_it_says_where_to_set_one_up(self):
        m = self.ask("what do my articles say about soil moisture?")
        self.assertEqual(m["kind"], "text")
        self.assertIn("Settings", m["text"])
        self.read_library()
        self.assertEqual(self.ask("search my library")["kind"], "text", "asked to search for nothing, it asks what for")

    def test_the_answer_lists_parts_with_where_they_are_from_and_nothing_goes_to_openai(self):
        self.read_library()
        m = self.ask("what do my articles say about stratified random sampling?")
        self.assertEqual(m["kind"], "library")
        d = m["data"]
        self.assertEqual(d["topic"], "stratified random sampling")
        top = d["hits"][0]
        self.assertEqual((top["label"], top["where"]), ("Smith et al. 2020", "page 2 · Methods"))
        self.assertIn("capacitance probes", top["text"])
        self.assertEqual((top["in_bib"], top["can_add"], top["has_entry"]), (False, True, False), "it has a DOI, so it can be added")
        self.assertEqual(d["command"], "\\cite")
        self.assertEqual(self.c.get("/api/library/search?q=nothing+matches+qqqq")[1]["chunks"], [])
        self.assertEqual(self.ask("which of my books mention zebrafish genomes")["data"]["hits"], [])

    def test_a_document_with_a_doi_is_added_the_usual_way_and_is_cited_afterwards(self):
        self.read_library()
        doc = self.doc("Smith et al. - 2020 - Soil moisture.pdf")
        st, r = self.c.post("/api/library/cite", {"paper": "alpha", "doc": doc["id"]})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["status"], "added")
        self.assertTrue(r["cite"].startswith("\\cite{") and r["key"], r)
        self.assertIn("10.1234/abc.2020.001", Path(common.bib_path(self.proj)).read_text())
        top = self.ask("what do my articles say about stratified random sampling?")["data"]["hits"][0]
        self.assertEqual((top["in_bib"], top["cite_key"]), (True, r["key"]))
        st, again = self.c.post("/api/library/cite", {"paper": "alpha", "doc": doc["id"]})
        self.assertEqual((st, again["status"]), (200, "in_bib"))

    def test_an_entry_from_a_bib_file_in_the_library_is_copied_without_asking_anyone(self):
        (self.lib / "refs.bib").write_text(BIB)
        self.read_library()
        calls = len(self.mendeley_calls) + len(self.pushes)
        doc = self.doc("Smith et al. - 2020 - Soil moisture.pdf")
        st, r = self.c.post("/api/library/cite", {"paper": "alpha", "doc": doc["id"]})
        self.assertEqual((st, r["status"], r["key"]), (200, "added", "smith2020soil"), r)
        self.assertIn("@article{smith2020soil", Path(common.bib_path(self.proj)).read_text())
        self.assertEqual(len(self.mendeley_calls) + len(self.pushes), calls, "nothing was looked up or sent")
        self.assertIn("next send", r["message"])

    def test_a_document_that_cannot_be_cited_says_why_and_bad_ids_are_refused(self):
        self.read_library()
        st, r = self.c.post("/api/library/cite", {"paper": "alpha", "doc": self.doc("irrigation.pdf")["id"]})
        self.assertEqual(st, 400)
        self.assertIn("no DOI", r["error"])
        self.assertEqual(self.c.post("/api/library/cite", {"paper": "alpha", "doc": "nope"})[0], 404)
        self.assertEqual(self.c.post("/api/library/cite", {"paper": "nope", "doc": "x"})[0], 404)
        self.assertEqual(self.c.post("/api/library/cite", {"paper": "alpha"})[0], 400)

    def test_the_library_setting_of_a_paper_is_saved_and_checked(self):
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "use_library": "sometimes"})[0], 400)
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "use_library": "never"})[0], 200)
        self.assertEqual(common.resolve("alpha")[0], "alpha")
        from writing_core import config
        self.assertEqual(config.paper_settings(self.proj)["use_library"], "never")


if __name__ == "__main__":
    unittest.main()
