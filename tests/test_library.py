"""The literature library: folders of books, articles and PDFs, read once, cut into labelled parts, searched locally, matched to the paper's bibliography."""
import os
import time
import unittest
from pathlib import Path

from tests.helpers import IsolatedHome, common, git, make_paper
from tests.pdfmaker import make_pdf
from tests.test_book import needs_pdf
from tests.test_files import docx
from writing_core import library


SOIL = [["Soil moisture and crop yield in semi-arid farms", "Jane Smith and Kim Lee, University of Nowhere",
         "Received 3 March 2020; Accepted 10 May 2020. DOI: 10.1234/abc.2020.001",
         "# Introduction", "Crop yield depends on soil moisture. We study forty farms in three regions of the country and relate yield to measured moisture in the root zone during the growing season.",
         "Farmers need guidance on irrigation timing, and the literature offers little evidence at the farm scale."],
        ["# Methods", "We sampled 40 farms by stratified random sampling and measured moisture weekly with capacitance probes installed at 30 cm depth.",
         "# References", "[1] Doe A. Soil. 2019. Quotable reference entry that must never be searched as text."]]
IRRIGATION = [["# Irrigation scheduling", "Deficit irrigation saves water without a large loss of yield when applied during the vegetative stage only; evidence from field trials supports this in maize and cotton."]]
BIB = ("@article{smith2020soil, author={Smith, Jane and Lee, Kim}, title={Soil moisture and crop yield in semi-arid farms}, journal={J. Soil}, year={2020}, doi={10.1234/abc.2020.001},\n"
       "  keywords={soil, yield}, abstract={We relate yield to moisture.}}\n"
       "@book{doe2019, author={Doe, Alex}, title={Soil: a short introduction}, year={2019}, publisher={Press}}\n")


class Base(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.lib = self.tmp / "Documents" / "Literature"
        (self.lib / "soil" / "deeper").mkdir(parents=True)
        self._docker = os.environ.pop("OVERLEAF_UI_IN_DOCKER", None)

    def tearDown(self):
        if self._docker is not None:
            os.environ["OVERLEAF_UI_IN_DOCKER"] = self._docker
        super().tearDown()

    def use(self, *extra):
        return library.set_folders([str(self.lib), *extra])


class Folders(Base):
    def test_folders_are_saved_checked_and_kept_with_their_reason(self):
        out = self.use("relative/path", str(self.tmp / "missing"), "/etc", "/Users/someone")
        by = {f["path"]: f for f in out}
        self.assertTrue(by[str(self.lib)]["ok"])
        self.assertEqual(by["relative/path"]["note"], "Use the full path of the folder.")
        self.assertIn("does not exist", by[str(self.tmp / "missing")]["note"])
        self.assertIn("system folder", by["/etc"]["note"])
        self.assertIn("very broad", by["/Users/someone"]["note"])
        self.assertEqual([f["path"] for f in library.folders()], [f["path"] for f in out], "they are remembered")

    def test_in_docker_the_reason_is_the_command_that_shares_the_folder(self):
        os.environ["OVERLEAF_UI_IN_DOCKER"] = "1"
        note = self.use(str(self.tmp / "Not shared"))[1]["note"]
        self.assertIn("./ui.sh folders add --read-only", note)
        self.assertIn(str(self.tmp / "Not shared"), note)

    def test_hidden_folders_the_app_settings_and_odd_lists_are_refused(self):
        self.assertIn("Hidden", self.use(str(self.tmp / ".hidden" / "x" / "y"))[1]["note"])
        self.assertIn("settings and keys", self.use(str(common.HOME / "inbox" / "a" / "b"))[1]["note"])
        with self.assertRaises(library.LibraryError):
            library.set_folders(["a\nb"])
        with self.assertRaises(library.LibraryError):
            library.set_folders([str(self.tmp / f"f{i}") for i in range(library.MAX_FOLDERS + 1)])
        self.assertEqual([f["path"] for f in library.set_folders(["~/x/y/z", "~/x/y/z/", str(Path("~/x/y/z").expanduser())])], [str(Path("~/x/y/z").expanduser())], "the same folder written three ways is one")


@needs_pdf
class Reading(Base):
    def setUp(self):
        super().setUp()
        make_pdf(self.lib / "soil" / "Smith et al. - 2020 - Soil moisture.pdf", SOIL, title="Soil moisture and crop yield in semi-arid farms", author="Jane Smith; Kim Lee")
        make_pdf(self.lib / "soil" / "deeper" / "other.pdf", IRRIGATION)
        (self.lib / "notes.md").write_text("# Notes on sampling\n\nStratified sampling reduces variance when strata differ in their means; allocate in proportion to stratum size and variability.\n")
        self.use()

    def docs(self):
        return {d["rel"]: d for d in library.docs()}

    def test_a_folder_tree_is_read_into_labelled_parts_with_their_pages_and_headings(self):
        r = library.update()
        self.assertEqual((r["new"], r["failed"], r["docs"]), (3, 0, 3), r)
        d = self.docs()
        soil = d["soil/Smith et al. - 2020 - Soil moisture.pdf"]
        self.assertEqual((soil["title"], soil["doi"], soil["year"], soil["pages"]), ("Soil moisture and crop yield in semi-arid farms", "10.1234/abc.2020.001", "2020", 2))
        self.assertEqual(soil["label"], "Smith and Lee 2020")
        parts = library.parts(soil["id"])
        self.assertEqual([p["where"] for p in parts], ["page 1", "page 1 · Introduction", "page 2 · Methods"])
        self.assertFalse(any("Quotable reference entry" in p["preview"] for p in parts), "the reference list of an article is not text to write from")
        self.assertEqual(d["notes.md"]["parts"], 1)

    def test_details_come_from_the_text_then_the_file_name_and_say_so(self):
        library.update()
        other = self.docs()["soil/deeper/other.pdf"]
        self.assertEqual(other["source"], "text")
        named = self.lib / "Garcia - 2018 - Water use in maize.txt"
        named.write_text("Maize water use rises with the leaf area index during the vegetative stage.\n\n" * 3)
        library.update()
        g = self.docs()["Garcia - 2018 - Water use in maize.txt"]
        self.assertEqual((g["authors"], g["year"], g["title"], g["source"]), ("Garcia", "2018", "Water use in maize", "name"))
        self.assertEqual(g["label"], "Garcia 2018")

    def test_a_bib_file_gives_the_right_details_and_does_not_show_twice(self):
        (self.lib / "refs.bib").write_text(BIB)
        library.update()
        d = self.docs()
        records = [x for x in library.docs() if x["rel"] == "refs.bib"]
        self.assertEqual([x["bib_key"] for x in records], ["doe2019"], "an entry that matches a document is not listed apart from it; one that matches none is")
        soil = d["soil/Smith et al. - 2020 - Soil moisture.pdf"]
        self.assertEqual((soil["source"], soil["bib_key"], soil["authors"]), ("bib", "smith2020soil", "Smith, Jane; Lee, Kim"))
        hits = library.query("soil moisture crop yield")["chunks"]
        self.assertTrue(hits)
        self.assertFalse(any(h["where"] == "entry" and h["bib_key"] == "smith2020soil" for h in hits), "the entry of a document that is there is not a second hit")
        # an entry with no file stays findable on its own, and takes the file's place if the file goes
        self.assertTrue(any(h["where"] == "entry" for h in library.query("short introduction soil Doe")["chunks"]))
        (self.lib / "refs.bib").unlink()
        library.update()
        self.assertEqual(self.docs()["soil/Smith et al. - 2020 - Soil moisture.pdf"]["source"], "text", "without the .bib the file's own details are back")

    def test_only_what_changed_is_read_again(self):
        library.update()
        again = library.update()
        self.assertEqual((again["new"], again["changed"], again["unchanged"], again["removed"]), (0, 0, 3, 0))
        st = library.status()
        self.assertEqual((st["new"], st["changed"], st["removed"], st["docs"]), (0, 0, 0, 3))
        time.sleep(0.02)
        (self.lib / "notes.md").write_text("# Notes\n\nSomething else entirely about phenology.\n")
        make_pdf(self.lib / "new.pdf", [["# Roots", "Root depth controls access to deep water during dry spells in many crops."]])
        (self.lib / "soil" / "deeper" / "other.pdf").unlink()
        st = library.status()
        self.assertEqual((st["new"], st["changed"], st["removed"]), (1, 1, 1))
        r = library.update()
        self.assertEqual((r["new"], r["changed"], r["removed"], r["unchanged"]), (1, 1, 1, 1))
        self.assertEqual(sorted(self.docs()), ["new.pdf", "notes.md", "soil/Smith et al. - 2020 - Soil moisture.pdf"])
        self.assertEqual(library.query("phenology")["chunks"][0]["rel"], "notes.md")
        self.assertEqual(library.query("cotton maize")["chunks"], [], "what is gone is not found")

    def test_a_file_that_cannot_be_read_is_noted_and_the_rest_goes_on(self):
        (self.lib / "broken.pdf").write_bytes(b"this is not a pdf at all")
        make_pdf(self.lib / "blank.pdf", [[]])
        (self.lib / "tiny.txt").write_text("  \n\n \n")
        r = library.update()
        self.assertEqual(r["docs"], 3)
        self.assertEqual(r["failed"], 3, r)
        d = self.docs()
        self.assertEqual(d["broken.pdf"]["parts"], 0)
        self.assertIn("damaged", d["broken.pdf"]["note"])
        self.assertNotIn("Syntax", d["broken.pdf"]["note"], "no tool output in what the author reads")
        self.assertIn("no text", d["blank.pdf"]["note"].lower())
        self.assertIn("no text", d["tiny.txt"]["note"].lower())
        self.assertEqual(library.status()["failed"], 3)
        self.assertEqual(library.update()["failed"], 0, "a file that failed is not tried again until it changes")

    def test_hidden_things_links_secrets_and_other_file_types_are_left_alone(self):
        (self.lib / ".hidden").mkdir()
        make_pdf(self.lib / ".hidden" / "x.pdf", IRRIGATION)
        make_pdf(self.lib / "secret_notes.pdf", IRRIGATION)
        make_pdf(self.lib / "~$lock.pdf", IRRIGATION)
        (self.lib / "photo.png").write_bytes(b"\x89PNG")
        (self.lib / "data.csv").write_text("a,b\n1,2\n")
        (self.tmp / "outside.pdf").write_bytes((self.lib / "soil" / "deeper" / "other.pdf").read_bytes())
        (self.lib / "link.pdf").symlink_to(self.tmp / "outside.pdf")
        found, _ = library.discover()
        self.assertEqual(sorted(f["rel"] for f in found), ["notes.md", "soil/Smith et al. - 2020 - Soil moisture.pdf", "soil/deeper/other.pdf"])

    def test_two_folders_with_the_same_file_read_it_once(self):
        other = self.tmp / "Documents" / "Copy"
        other.mkdir()
        (other / "same.pdf").symlink_to(self.lib / "soil" / "deeper" / "other.pdf")
        self.use(str(other))
        found, _ = library.discover()
        self.assertEqual(len([f for f in found if f["path"].endswith(("other.pdf", "same.pdf"))]), 1, "a file reached twice is read once")

    def test_only_one_update_runs_at_a_time_and_nothing_is_read_without_a_folder(self):
        library.set_folders([])
        with self.assertRaises(library.LibraryError):
            library.update()
        self.use()
        ran = []

        def slow(done, total):
            ran.append(done)
            if done == 1:
                with self.assertRaises(library.LibraryError) as cm:
                    library.update()
                self.assertIn("already", str(cm.exception))
        library.update(progress=slow)
        self.assertEqual(ran, [1, 2, 3])
        self.assertIsNone(library.running())

    def test_an_index_made_by_older_code_or_damaged_is_made_again(self):
        library.update()
        library.LOGIC_VERSION += 1
        try:
            self.assertEqual(library.update()["new"], 3, "everything is read again")
        finally:
            library.LOGIC_VERSION -= 1
        library._db_path().write_bytes(b"garbage")
        self.assertEqual(library.update()["new"], 3)
        self.assertEqual(len(library.docs()), 3)


@needs_pdf
class Searching(Base):
    def setUp(self):
        super().setUp()
        make_pdf(self.lib / "Smith et al. - 2020 - Soil moisture.pdf", SOIL)
        make_pdf(self.lib / "irrigation.pdf", IRRIGATION)
        (self.lib / "refs.bib").write_text(BIB)
        self.use()
        library.update()

    def test_the_best_parts_come_first_with_where_they_are_from(self):
        r = library.query("deficit irrigation in maize and cotton")
        self.assertEqual(r["chunks"][0]["rel"], "irrigation.pdf")
        self.assertEqual(r["chunks"][0]["where"], "page 1 · Irrigation scheduling")
        self.assertEqual(r["docs"], len({c["doc"] for c in r["chunks"]}))
        self.assertIn("vegetative stage", r["chunks"][0]["text"])
        r = library.query("stratified random sampling capacitance probes")
        self.assertEqual((r["chunks"][0]["label"], r["chunks"][0]["where"]), ("Smith and Lee 2020", "page 2 · Methods"))
        self.assertEqual(library.query("")["chunks"], [])
        self.assertEqual(library.query("the and of")["chunks"], [])
        self.assertEqual(library.query("quantum chromodynamics")["chunks"], [])

    def test_few_parts_per_document_a_budget_and_a_choice_of_documents(self):
        many = [["# Section %d" % i, ("Wheat yield responds to nitrogen fertilizer timing and rate in field trial number %d, in dry seasons, and the response differs between soils. " % i) * 9] for i in range(6)]
        make_pdf(self.lib / "wheat.pdf", many)
        library.update()
        r = library.query("wheat yield nitrogen fertilizer", k=10, per_doc=2)
        self.assertEqual(len([c for c in r["chunks"] if c["rel"] == "wheat.pdf"]), 2)
        tiny = library.query("wheat yield nitrogen fertilizer", k=10, per_doc=6, budget=1)
        self.assertEqual(len(tiny["chunks"]), 1, "at least one part always, then the budget decides")
        only = library.docs("irrigation")
        self.assertEqual(len(only), 1)
        self.assertEqual([c["rel"] for c in library.query("yield", doc_ids=[only[0]["id"]])["chunks"]], ["irrigation.pdf"])
        self.assertEqual(library.get_chunks([r["chunks"][0]["id"], "L:nope#1"])[0]["id"], r["chunks"][0]["id"])

    def test_documents_can_be_filtered_and_their_parts_listed(self):
        self.assertEqual({d["rel"] for d in library.docs("smith")}, {"Smith et al. - 2020 - Soil moisture.pdf"})
        self.assertEqual({d["rel"] for d in library.docs("10.1234")}, {"Smith et al. - 2020 - Soil moisture.pdf"})
        p = library.parts(library.docs("irrigation")[0]["id"])
        self.assertEqual(len(p), 1)
        self.assertIn("Deficit irrigation", p[0]["preview"])


@needs_pdf
class Citing(Base):
    def setUp(self):
        super().setUp()
        make_pdf(self.lib / "Smith et al. - 2020 - Soil moisture.pdf", SOIL)
        make_pdf(self.lib / "irrigation.pdf", IRRIGATION)
        (self.lib / "refs.bib").write_text(BIB)
        self.use()
        library.update()
        root = make_paper(self.tmp / "papers", "alpha", "\\documentclass{article}\\begin{document}Hi.\\end{document}\n",
                          bib="@article{smithetal2020, author={Smith, J. and Lee, K.}, title={Soil Moisture and Crop Yield in Semi-Arid Farms}, year={2020}, doi={10.1234/ABC.2020.001}}\n"
                              "@book{other, author={O}, title={Something else}, year={2001}}\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "init")
        common.register(str(root), "alpha")
        self.name, self.proj = common.resolve("alpha")

    def test_a_document_in_the_papers_bibliography_gets_its_cite_key(self):
        hits = library.annotate(library.query("soil moisture crop yield farms")["chunks"], self.proj)
        smith = next(h for h in hits if h["label"] == "Smith and Lee 2020")
        self.assertEqual((smith["in_bib"], smith["cite_key"], smith["can_add"]), (True, "smithetal2020", False))

    def test_a_document_that_is_not_there_can_be_added_by_its_doi_or_copied_from_the_library_bib(self):
        bib = library.paper_bib(self.proj)
        Path(bib["path"]).write_text("@book{other, author={O}, title={Something else}, year={2001}}\n")
        hits = library.annotate(library.query("soil moisture crop yield farms")["chunks"], self.proj)
        smith = next(h for h in hits if h["label"] == "Smith and Lee 2020")
        self.assertEqual((smith["in_bib"], smith["cite_key"], smith["can_add"]), (False, "", True), "its DOI can be used to add it")
        irr = next(h for h in library.annotate(library.query("deficit irrigation maize")["chunks"], self.proj))
        self.assertEqual((irr["in_bib"], irr["can_add"]), (False, False), "a document with neither a DOI nor an entry cannot be cited yet")
        key = library.add_entry_from_record(smith["doc"], bib["path"])
        self.assertEqual(key, "smith2020soil")
        self.assertIn("@article{smith2020soil", Path(bib["path"]).read_text())
        self.assertEqual(library.add_entry_from_record(smith["doc"], bib["path"]), "smith2020soil", "adding it again does not duplicate it")
        self.assertEqual(Path(bib["path"]).read_text().count("@article{smith2020soil"), 1)
        self.assertIsNone(library.add_entry_from_record(irr["doc"], bib["path"]))
        again = library.annotate(library.query("soil moisture crop yield farms")["chunks"], self.proj)
        self.assertEqual(next(h for h in again if h["label"] == "Smith and Lee 2020")["cite_key"], "smith2020soil")

    def test_a_key_that_is_taken_by_another_work_gets_a_free_one(self):
        bib = library.paper_bib(self.proj)
        Path(bib["path"]).write_text("@book{smith2020soil, author={X}, title={Another work entirely}, year={2020}, doi={10.5555/other}}\n")
        doc = next(d for d in library.docs() if d["bib_key"] == "smith2020soil")
        self.assertEqual(library.add_entry_from_record(doc["id"], bib["path"]), "smith2020soilb")
        self.assertIn("@article{smith2020soilb,", Path(bib["path"]).read_text())

    def test_names_are_split_the_way_people_write_them(self):
        self.assertEqual(library.author_list("Smith, J. and Lee, K."), ["Smith, J.", "Lee, K."])
        self.assertEqual(library.author_list("Smith, John"), ["Smith, John"])
        self.assertEqual(library.author_list("John Smith, Jane Doe"), ["John Smith", "Jane Doe"])
        self.assertEqual(library.author_list("A. Smith; B. Lee; C. Wu"), ["A. Smith", "B. Lee", "C. Wu"])
        self.assertEqual([library.surname(x) for x in ("Smith, J.", "John Smith")], ["Smith", "Smith"])
        self.assertEqual(library._label("Smith, J.; Lee, K.; Wu, X.", "2020", "T", "f"), "Smith et al. 2020")
        self.assertEqual(library._label("", "", "A long title about things", "file"), "A long title about things")


class Formats(Base):
    def test_word_html_text_and_latex_files_are_read_too(self):
        (self.lib / "Garcia - 2018 - Water use.docx").write_bytes(docx([("Heading1", "Findings"), ("Normal", "Farmers said the water came late in 2021. " * 3)]))
        (self.lib / "page.html").write_text("<html><head><style>p{color:red}</style><title>t</title></head><body><h2>Results of the trial</h2><p>Yield rose with moisture in all regions and seasons of the trial.</p><script>var x=1;</script></body></html>")
        (self.lib / "chapter.tex").write_text("\\section{Background}\nSoil moisture controls plant growth in dry climates and varies strongly in space.\n")
        self.use()
        r = library.update()
        self.assertEqual((r["new"], r["failed"]), (3, 0), r)
        d = {x["rel"]: x for x in library.docs()}
        self.assertEqual((d["Garcia - 2018 - Water use.docx"]["authors"], d["Garcia - 2018 - Water use.docx"]["year"]), ("Garcia", "2018"))
        hit = library.query("yield moisture trial")["chunks"][0]
        self.assertEqual((hit["rel"], hit["where"]), ("page.html", "Results of the trial"))
        self.assertNotIn("color:red", hit["text"])
        self.assertNotIn("var x", hit["text"])
        self.assertTrue(library.query("farmers water late")["chunks"])
        self.assertTrue(library.query("plant growth dry climates")["chunks"])


class Headings(unittest.TestCase):
    def test_a_short_line_above_wider_text_is_a_heading_but_a_wrapped_paragraph_is_not(self):
        wide = "x" * 80
        self.assertEqual(library._loose_heading(["Irrigation scheduling", wide, wide]), "Irrigation scheduling")
        self.assertIsNone(library._loose_heading(["Irrigation scheduling", "tiny"]), "the next line must be clearly wider")
        self.assertIsNone(library._loose_heading([wide, wide]), "a long line is text")
        self.assertIsNone(library._loose_heading(["It ends here.", wide]))
        self.assertIsNone(library._loose_heading(["Table 3 12.4 15.6 18.2 19.9", wide]), "rows of numbers are not headings")
        self.assertIsNone(library._loose_heading(["lowercase continuation", wide]))
        self.assertIsNone(library._loose_heading(["One line only"]))

    def test_titles_are_the_first_lines_not_the_text_below_them(self):
        page = "Soil moisture and crop yield\nin semi-arid farms of the region\n\nJane Smith, University of Nowhere\n\nAbstract\n" + "The text starts here and goes on for long. " * 3
        self.assertEqual(library._title_from_page(page), "Soil moisture and crop yield in semi-arid farms of the region")
        self.assertEqual(library._title_from_page("Irrigation scheduling\n" + "w" * 88 + "\nmore"), "Irrigation scheduling")
        self.assertEqual(library._title_from_page("Research Article\nhttps://doi.org/10.1/xyz\nSoil moisture matters for crop yield in farms\nJ. Smith\n"), "Soil moisture matters for crop yield in farms")


if __name__ == "__main__":
    unittest.main()
