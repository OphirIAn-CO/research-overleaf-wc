"""Citing in the text: references found online (their abstract, once they are in the bibliography), documents of the author's library that the bibliography does not have yet (their
entry is written when the citing text is saved), and the author's answers asking for a citation. doi.org is replaced by a table; nothing touches the network."""
import json
from pathlib import Path

import doi_bot
from tests.pdfmaker import make_pdf
from tests.test_book import needs_pdf
from tests.test_library import BIB, SOIL
from tests.test_revise import ReviseBase, reply, user_of
from writing_core import chat, config, library, revise, scholar

SAMPLED = "We sampled 40 farms by stratified random sampling."
WITH_CITE = "We sampled 40 farms by stratified random sampling [[LX901]]."
FOUND = {"doi": "10.1016/j.enpol.2019.05.012", "key": "rojas2019armed", "title": "Armed conflict and pipeline attacks in Colombia", "authors": ["Rojas, Camila", "Smith, John"],
         "year": 2019, "venue": "Energy Policy", "abstract": "We study 412 attacks on oil pipelines in Colombia between 2000 and 2016 and show that armed groups targeted infrastructure near disputed territory.",
         "url": "https://doi.org/10.1016/j.enpol.2019.05.012"}
ASK_FOUND = "support the sampling statement with a source about armed groups attacking pipelines and cite it"
DOI_ENTRY = ("@article{x,\n author={Smith, Jane and Lee, Kim},\n title={Soil moisture and crop yield in semi-arid farms},\n year={2020},\n journal={J. Soil},\n doi={10.1234/abc.2020.001}\n}")


def system_of(body):
    return body.get("instructions") or body["messages"][0]["content"]


class Offline(ReviseBase):
    """No test here may ask doi.org: it answers from DOIS, or fails."""
    DOIS = {}

    def setUp(self):
        super().setUp()
        self.live()
        self._fetch = doi_bot.fetch_bibtex
        self.asked = []

        def fetch(doi):
            self.asked.append(doi)
            if doi in self.DOIS:
                return self.DOIS[doi]
            raise RuntimeError("HTTP 404")
        doi_bot.fetch_bibtex = fetch
        library._ENTRY_CACHE.clear()
        self.bib = Path(self.proj["path"]) / self.proj["bib"]

    def tearDown(self):
        doi_bot.fetch_bibtex = self._fetch
        library._ENTRY_CACHE.clear()
        super().tearDown()


@needs_pdf
class FoundOnline(Offline):
    def setUp(self):
        super().setUp()
        self.bib.write_text(self.bib.read_text() + "@article{rojas2019armed, author={Rojas, Camila and Smith, John}, title={Armed conflict and pipeline attacks in Colombia}, year={2019}, doi={10.1016/j.enpol.2019.05.012}}\n")
        scholar.remember(self.proj, [FOUND])

    def test_a_request_for_sources_offers_what_was_found_with_a_tag_and_says_it_is_only_the_abstract(self):
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        r = self.go(ASK_FOUND, part="methods")
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        body = self.srv.requests[-1]["body"]
        user = user_of(body)
        self.assertIn("[S1] Rojas and Smith 2019, abstract (found online). Cite it with [[LX901]]. Only its abstract is known, found online: state nothing the abstract does not say.", user)
        self.assertIn("412 attacks on oil pipelines", user)
        self.assertIn("Where a source is only an abstract", system_of(body))
        u = r["proposal"]["units"][0]
        self.assertEqual((u["new_tags"], u["added_cites"]), (["LX901"], ["\\cite{rojas2019armed}"]))
        lib = r["proposal"]["library"][0]
        self.assertEqual((lib["online"], lib["pending"], lib["cite_key"], lib["tag"]), (True, False, "rojas2019armed", "LX901"))
        self.assertEqual(r["proposal"]["bib_adds"], [], "it is in the bibliography already")
        self.assertEqual(revise.apply(self.name, self.proj, r["proposal"]["id"], accept="all", unclean_ok=True)["status"], "applied")
        self.assertIn("stratified random sampling \\cite{rojas2019armed}.", (Path(self.proj["path"]) / "sections" / "methods.tex").read_text())

    def test_a_request_that_does_not_ask_for_sources_leaves_it_out(self):
        self.srv.script = [reply(("We sampled", "We drew a sample of"))]
        self.go("tighten my methods", part="methods")
        self.assertNotIn("library_sources", user_of(self.srv.requests[-1]["body"]))

    def test_the_authors_answers_can_be_the_request_for_a_citation(self):
        facts = "Question: What would justify this?\nAnswer: add a citation of how armed groups attacking pipelines affects infrastructure"
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        self.go("Describe the sampling in more detail.", part="methods", facts=facts)
        user = user_of(self.srv.requests[-1]["body"])
        self.assertIn("<library_sources>", user)
        self.assertIn("Rojas and Smith 2019", user)
        self.srv.script = [reply(("We sampled", "We drew a sample of"))]
        self.go("Describe the sampling in more detail.", part="methods")
        self.assertNotIn("library_sources", user_of(self.srv.requests[-1]["body"]), "without that answer the request does not ask for one")

    def test_a_reference_the_author_picked_goes_along_whatever_the_request_or_the_setting_says(self):
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        r = self.go("tighten my methods", part="methods", online=[FOUND["doi"]], use_library="never")
        user = user_of(self.srv.requests[-1]["body"])
        self.assertIn("Rojas and Smith 2019", user)
        self.assertEqual(r["proposal"]["units"][0]["added_cites"], ["\\cite{rojas2019armed}"])

    def test_the_preview_and_the_real_request_are_the_same_request(self):
        a = self.go(ASK_FOUND, part="methods", dry_run=True)
        self.assertEqual([x["tag"] for x in a["library"]], ["LX901"])
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        b = self.go(ASK_FOUND, part="methods", dry_run=False, confirm=a["payload_hash"])
        self.assertEqual(b["status"], "proposed")

    def test_a_citation_that_was_not_offered_is_still_refused(self):
        self.srv.script = [reply((SAMPLED, SAMPLED[:-1] + " [[LX902]].")), reply((SAMPLED, SAMPLED[:-1] + " [[LX902]]."))]
        r = self.go(ASK_FOUND, part="methods")
        self.assertEqual(r["status"], "failed")

    def test_chat_plans_carry_the_picked_references(self):
        ids = [c["id"] for c in revise.resolve_target(self.name, self.proj, "x", "methods", None, "test")[0]]
        pl = chat.plan(self.name, self.proj, "tighten my methods", ids, online=[FOUND["doi"]])
        self.assertEqual(pl["online"], [FOUND["doi"]])
        self.assertEqual(pl["library"][0]["online"], True)


@needs_pdf
class UploadedButNotInTheBibliography(Offline):
    DOIS = {"10.1234/abc.2020.001": DOI_ENTRY}

    def setUp(self):
        super().setUp()
        self.lib = self.tmp / "Documents" / "Literature"
        self.lib.mkdir(parents=True)
        make_pdf(self.lib / "Smith et al. - 2020 - Soil moisture.pdf", SOIL)
        library.set_folders([str(self.lib)])
        library.update()
        self.methods = Path(self.proj["path"]) / "sections" / "methods.tex"

    ASK = "support the sampling statement with my library and cite it"

    def test_the_document_is_cited_and_its_entry_is_written_only_when_the_text_is_saved(self):
        before = self.bib.read_text()
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        r = self.go(self.ASK, part="methods")
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        user = user_of(self.srv.requests[-1]["body"])
        self.assertIn("Cite it with [[LX901]].", user)
        self.assertNotIn("Not in the paper's bibliography", user)
        prop = r["proposal"]
        self.assertEqual([a["key"] for a in prop["bib_adds"]], ["Smith2020Soil"])
        self.assertEqual(prop["bib_adds"][0]["doi"], "10.1234/abc.2020.001")
        self.assertEqual((prop["library"][0]["pending"], prop["library"][0]["cite_key"]), (True, "Smith2020Soil"))
        self.assertEqual(prop["units"][0]["added_cites"], ["\\cite{Smith2020Soil}"])
        self.assertEqual(self.bib.read_text(), before, "making the suggestion does not touch the bibliography")
        res = revise.apply(self.name, self.proj, prop["id"], accept="all", unclean_ok=True)
        self.assertEqual([a["key"] for a in res["bibliography"]["added"]], ["Smith2020Soil"])
        self.assertIn("@article{Smith2020Soil,", self.bib.read_text())
        self.assertIn("stratified random sampling \\cite{Smith2020Soil}.", self.methods.read_text())
        self.assertEqual(revise.get_proposal(self.proj, prop["id"])["bib_added"][0]["key"], "Smith2020Soil")

    def test_what_is_asked_of_doi_org_is_the_same_for_the_preview_and_the_request(self):
        a = self.go(self.ASK, part="methods", dry_run=True)
        self.assertEqual([x["pending"] for x in a["library"]][:1], [True])
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        b = self.go(self.ASK, part="methods", dry_run=False, confirm=a["payload_hash"])
        self.assertEqual(b["status"], "proposed")
        self.assertEqual(self.asked, ["10.1234/abc.2020.001"], "doi.org was asked once")

    def test_if_doi_org_does_not_know_the_document_its_entry_is_made_from_what_the_file_says_and_flagged(self):
        self.DOIS.clear()
        self.addCleanup(self.DOIS.update, {"10.1234/abc.2020.001": DOI_ENTRY})
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        r = self.go(self.ASK, part="methods")
        self.assertIn("Cite it with [[LX901]].", user_of(self.srv.requests[-1]["body"]))
        add = r["proposal"]["bib_adds"][0]
        self.assertEqual((add["key"], add["origin"], add["doi"]), ("Smith2020Soil", "file", ""))
        self.assertIn("doi = {10.1234/abc.2020.001}", add["bibtex"], "the DOI the file named is kept in the entry")
        self.assertIn("Entry made from the file", add["bibtex"])
        self.assertTrue(any("made from the title, authors and year the file itself gives" in w for w in r["proposal"]["warnings"]), r["proposal"]["warnings"])
        self.assertEqual(r["proposal"]["units"][0]["added_cites"], ["\\cite{Smith2020Soil}"])

    def test_a_key_that_is_taken_by_another_entry_gets_a_free_one(self):
        self.bib.write_text(self.bib.read_text() + "@article{Smith2020Soil, author={Other, A.}, title={Something else entirely}, year={2020}}\n")
        self.srv.script = [reply((SAMPLED, SAMPLED[:-1] + " [[LX901]]."))]
        r = self.go(self.ASK, part="methods")
        self.assertEqual([a["key"] for a in r["proposal"]["bib_adds"]], ["Smith2020Soilb"])
        self.assertEqual(r["proposal"]["units"][0]["added_cites"], ["\\cite{Smith2020Soilb}"])

    def test_an_entry_is_not_written_for_a_citation_that_is_not_in_what_was_saved(self):
        prop = {"bib_adds": [{"key": "Smith2020Soil", "doi": "10.1234/abc.2020.001", "bibtex": DOI_ENTRY.replace("{x,", "{Smith2020Soil,"), "title": "Soil"}]}
        out = revise._add_bib_entries(self.proj, prop, ["sections/methods.tex"])
        self.assertEqual(out, {"added": [], "warnings": []})
        self.assertNotIn("Smith2020Soil", self.bib.read_text())

    def test_if_the_key_got_used_by_another_entry_meanwhile_the_author_is_warned(self):
        self.methods.write_text(self.methods.read_text() + "\nSee \\cite{Smith2020Soil}.\n")
        self.bib.write_text(self.bib.read_text() + "@article{Smith2020Soil, author={Other, A.}, title={Something else entirely}, year={2020}}\n")
        prop = {"bib_adds": [{"key": "Smith2020Soil", "doi": "10.1234/abc.2020.001", "bibtex": DOI_ENTRY.replace("{x,", "{Smith2020Soil,"), "title": "Soil moisture"}]}
        out = revise._add_bib_entries(self.proj, prop, ["sections/methods.tex"])
        self.assertEqual(out["added"], [])
        self.assertIn("now used by another entry", out["warnings"][0])

    def test_a_record_from_a_bib_file_in_the_library_is_added_as_it_is(self):
        (self.lib / "mine.bib").write_text(BIB)
        library.update()
        self.bib.write_text("@book{doe2019, author={Doe, A.}, title={Soil}, year=2019}\n")
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        r = self.go("support the claim about yield and moisture with my library and cite it", part="methods", use_library="always")
        adds = r["proposal"]["bib_adds"]
        self.assertTrue(adds and adds[0]["bibtex"].startswith("@article{" + adds[0]["key"] + ","), adds)
        self.assertIn("We relate yield to moisture.", adds[0]["bibtex"], "the entry is the author's own record, not a rebuilt one")


@needs_pdf
class DraftsCiteToo(Offline):
    """New text written from the author's sources can cite what was found online, and the author's answers can be the request for it."""

    def setUp(self):
        super().setUp()
        self.bib.write_text(self.bib.read_text() + "@article{rojas2019armed, author={Rojas, Camila and Smith, John}, title={Armed conflict and pipeline attacks in Colombia}, year={2019}, doi={10.1016/j.enpol.2019.05.012}}\n")
        scholar.remember(self.proj, [FOUND])

    def test_a_draft_is_offered_the_references_that_were_picked_and_cites_them(self):
        from writing_core import draft
        loc = draft.locate(self.name, self.proj, "write the introduction about armed groups attacking pipelines")
        self.assertEqual(loc["status"], "ready", loc)
        pl = draft.plan(self.name, self.proj, "write the introduction about armed groups attacking pipelines", loc["anchor"], loc["part"], online=[FOUND["doi"]])
        self.assertEqual(pl["online"], [FOUND["doi"]])
        self.assertEqual([x["tag"] for x in pl["library"]], ["LX901"])
        self.assertTrue(pl["library"][0]["online"])
        text = "Armed groups have attacked oil pipelines in Colombia [[LX901]]."
        self.srv.script = [{"approach": "One sentence from the abstract.", "draft_text": text, "basis": [{"statement": "Armed groups have attacked oil pipelines", "source": "L1"}], "questions": []}]
        res = draft.run(self.name, self.proj, "write the introduction about armed groups attacking pipelines", loc["anchor"], loc["part"], confirm=pl["payload_hash"], online=[FOUND["doi"]])
        self.assertEqual(res["status"], "proposed", res["proposal"]["units"][0].get("error"))
        u = res["proposal"]["units"][0]
        self.assertEqual(u["added_cites"], ["\\cite{rojas2019armed}"])
        self.assertIn("Only its abstract is known", json.dumps(self.srv.requests[-1]["body"]))
