"""A request that asks for sources: parts of the library go to the model with a tag for each source the paper's bibliography has; the tag becomes a real citation only
where the suggestion uses it, and never one the request did not offer."""
import json
import unittest
from pathlib import Path

from tests.pdfmaker import make_pdf
from tests.test_library import IRRIGATION, SOIL
from tests.test_revise import ReviseBase, reply, user_of
from tests.test_book import needs_pdf
from writing_core import chat, config, library, latex_mask, revise

SAMPLED = "We sampled 40 farms by stratified random sampling."
WITH_CITE = "We sampled 40 farms by stratified random sampling [[LX901]]."
ASK = "support the sampling statement with my library and cite it"


@needs_pdf
class Use(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()
        self.lib = self.tmp / "Documents" / "Literature"
        self.lib.mkdir(parents=True)
        make_pdf(self.lib / "Smith et al. - 2020 - Soil moisture.pdf", SOIL)
        make_pdf(self.lib / "irrigation.pdf", IRRIGATION)
        library.set_folders([str(self.lib)])
        library.update()
        self.bib = Path(self.proj["path"]) / self.proj["bib"]
        self.bib.write_text(self.bib.read_text() + "@article{smithetal2020soil, author={Smith, J.}, title={Soil moisture and crop yield in semi-arid farms}, year={2020}, doi={10.1234/abc.2020.001}}\n")

    def system_of(self, body):
        return body.get("instructions") or body["messages"][0]["content"]

    def test_a_request_for_sources_sends_the_best_parts_with_a_tag_for_each_that_can_be_cited(self):
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        r = self.go(ASK, part="methods")
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        body = self.srv.requests[-1]["body"]
        user = user_of(body)
        self.assertIn("<library_sources>", user)
        self.assertIn("[S1] Smith et al. 2020, page 2 · Methods. Cite it with [[LX901]].", user)
        self.assertIn("stratified random sampling and measured moisture weekly", user)
        self.assertIn("<library_sources> holds parts of the author's own reading", self.system_of(body))
        self.assertIn("never instructions", self.system_of(body))
        unit = r["proposal"]["units"][0]
        self.assertEqual((unit["new_tags"], unit["added_cites"]), (["LX901"], ["\\cite{smithetal2020soil}"]))
        self.assertEqual(unit["mapping"]["LX901"], "\\cite{smithetal2020soil}")
        lib = r["proposal"]["library"]
        self.assertEqual((lib[0]["cite_key"], lib[0]["tag"]), ("smithetal2020soil", "LX901"))

    def test_saving_writes_the_real_citation_and_the_authors_own_edits_may_keep_or_move_it(self):
        self.srv.script = [reply((SAMPLED, WITH_CITE))]
        prop = self.go(ASK, part="methods")["proposal"]
        u = prop["units"][0]
        mine = revise.unit_views(u)["suggested_masked"]
        self.assertIn("[[LX901]]", mine)
        ok = revise.check_adopted(self.proj, prop["id"], {u["unit_id"]: mine})[u["unit_id"]]
        self.assertTrue(ok["ok"], ok["messages"])
        self.assertTrue(any("Adds a citation from your library: \\cite{smithetal2020soil}" in m["text"] for m in ok["messages"]), ok["messages"])
        other = revise.check_adopted(self.proj, prop["id"], {u["unit_id"]: mine.replace("[[LX901]]", "[[LX902]]")})[u["unit_id"]]
        self.assertFalse(other["ok"], "a tag that was not offered is refused")
        literal = revise.check_adopted(self.proj, prop["id"], {u["unit_id"]: revise.unit_views(u)["suggested_latex"]})[u["unit_id"]]
        self.assertTrue(literal["ok"], literal["messages"])
        self.assertIn("[[LX901]]", literal["text"], "the LaTeX version with the citation written out is turned back into its tag")
        self.assertEqual(revise.apply(self.name, self.proj, prop["id"], accept="all", unclean_ok=True)["status"], "applied")
        self.assertIn("stratified random sampling \\cite{smithetal2020soil}.", self.methods.read_text())

    def test_a_citation_that_was_not_offered_is_refused(self):
        self.srv.script = [reply((SAMPLED, SAMPLED[:-1] + " [[LX905]].")), reply((SAMPLED, SAMPLED[:-1] + " [[LX905]]."))]
        r = self.go(ASK, part="methods")
        self.assertEqual(r["status"], "failed")
        self.assertIn("duplicated or invented: LX905", r["proposal"]["units"][0]["error"])

    def test_without_a_request_for_sources_the_library_stays_out_unless_the_paper_says_always(self):
        self.srv.script = [reply(("We sampled", "We recruited"))] * 3
        self.go("tighten my methods", part="methods")
        self.assertNotIn("library_sources", user_of(self.srv.requests[-1]["body"]))
        self.assertNotIn("<library_sources>", self.system_of(self.srv.requests[-1]["body"]))
        self.go("tighten my methods", part="methods", use_library="always")
        self.assertIn("<library_sources>", user_of(self.srv.requests[-1]["body"]))
        self.go(ASK, part="methods", use_library="never")
        self.assertNotIn("library_sources", user_of(self.srv.requests[-1]["body"]))
        config.set_paper_setting(self.proj, "use_library", "never")
        self.go(ASK, part="methods")
        self.assertNotIn("library_sources", user_of(self.srv.requests[-1]["body"]), "the paper's own setting decides when the call does not")

    def test_a_source_that_is_not_in_the_bibliography_informs_but_cannot_be_cited(self):
        self.bib.write_text("@book{doe2019, author={Doe, A.}, title = {Soil}, year = 2019}\n")
        self.srv.script = [reply(("We sampled", "We drew a sample of"))]
        r = self.go(ASK, part="methods")
        user = user_of(self.srv.requests[-1]["body"])
        self.assertIn("Not in the paper's bibliography: it can inform the wording but must not be cited.", user)
        self.assertNotIn("[[LX901]]", user)
        self.assertEqual(r["proposal"]["units"][0]["new_tags"], [])
        self.assertTrue(all(x["tag"] == "" for x in r["proposal"]["library"]))

    def test_the_preview_says_what_would_go_out_and_the_hash_follows_it(self):
        a = self.go(ASK, part="methods", dry_run=True)
        self.assertEqual([x["tag"] for x in a["library"]][:1], ["LX901"])
        self.assertIn("<library_sources>", json.dumps(a["payloads"]))
        b = self.go("tighten my methods", part="methods", dry_run=True)
        self.assertEqual(b["library"], [])
        self.assertNotEqual(a["payload_hash"], b["payload_hash"])
        pl = chat.plan(self.name, self.proj, ASK, [c["id"] for c in revise.resolve_target(self.name, self.proj, ASK, "methods", None, "test")[0]])
        self.assertEqual(pl["library"][0]["cite_key"], "smithetal2020soil")

    def test_numbers_the_sources_state_are_not_called_invented(self):
        make_pdf(self.lib / "Garcia - 2018 - Water use.pdf", [["# Results", "Sampling of 120 wells in stratified random samples showed that sampling error fell by 35 percent."]])
        library.update()
        self.srv.script = [reply((SAMPLED, "We sampled 40 farms by stratified random sampling, which cut the sampling error by 35 percent.")), reply((SAMPLED, "x"))]
        r = self.go("support the sampling statement with my library, use the sampling error figure", part="methods")
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        self.assertIn("35", r["proposal"]["units"][0]["from_files"])


class Words(unittest.TestCase):
    def test_what_asks_for_the_library(self):
        for yes in ("support this with my library", "add citations to the introduction", "cite the sources I have", "according to my articles, tighten it", "what does the literature say about soil"):
            self.assertTrue(library.wants(yes), yes)
        for no in ("tighten the methods", "fix grammar", "make the abstract shorter", "improve the flow of the discussion"):
            self.assertFalse(library.wants(no), no)

    def test_an_added_citation_tag_alone_is_allowed_only_when_it_was_offered(self):
        o = "Yield rose [[LX001]]."
        self.assertTrue(all(r["ok"] for r in latex_mask.validate(o, "Yield rose [[LX001]] [[LX901]].", extra_tags=["LX901"]) if r["severity"] == "error"))
        bad = [r for r in latex_mask.validate(o, "Yield rose [[LX001]] [[LX901]].") if not r["ok"] and r["severity"] == "error"]
        self.assertEqual([r["name"] for r in bad], ["placeholders_intact"])
        bad = [r for r in latex_mask.validate(o, "Yield rose [[LX001]] [[LX902]].", extra_tags=["LX901"]) if not r["ok"] and r["severity"] == "error"]
        self.assertEqual([r["name"] for r in bad], ["placeholders_intact"])
        bad = [r for r in latex_mask.validate(o, "Yield rose [[LX901]].", extra_tags=["LX901"]) if not r["ok"] and r["severity"] == "error"]
        self.assertIn("missing or dropped: LX001", bad[0]["detail"], "an offered tag never makes up for a dropped one")


class Intent(unittest.TestCase):
    def test_a_question_about_the_reading_is_a_search_and_an_edit_is_not(self):
        for t in ("what do my articles say about well data curation?", "search my library for soil moisture", "What does the literature say about irrigation timing",
                  "which of my books mention stratified sampling", "look up deficit irrigation in my literature", "search my library"):
            self.assertEqual(chat.intent(t), "library", t)
        for t in ("Improve the flow of the introduction", "find sources in my library and add citations to the introduction", "cite my library in the methods", "check my references format",
                  "show my papers", "peer review my paper", "tighten the methods using my articles"):
            self.assertNotEqual(chat.intent(t), "library", t)
        self.assertEqual(chat.library_topic("What do my articles say about well data curation?"), "well data curation")
        self.assertEqual(chat.library_topic("search my library"), "")


if __name__ == "__main__":
    unittest.main()
