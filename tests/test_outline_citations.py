"""Writing from an outline with a literature library: the sections of the outline that were asked for are used, the subject of the paper (not the words about how to write) decides what is
looked up in the library and on the internet, a guide to writing is never a reference, and the library's papers become citations through their DOI. doi.org, Crossref and the web are
replaced by tables; nothing touches the network."""
import json
from pathlib import Path

import doi_bot
from tests.pdfmaker import make_pdf
from tests.test_book import needs_pdf
from tests.test_revise import ReviseBase, reply as revise_reply, user_of
from tests.test_scholar import Net, routes
from tests.test_v2_chat import MAIN
from tests.test_v2_chat_api import ChatBase
from writing_core import chat, config, draft, files, library, openai_client, paper_index, revise, scholar

PAPER = r"""\documentclass{article}
\title{Risk perception}
\begin{document}
\begin{abstract}
We study how people perceive risk across hazards and why aggregate models hide heterogeneity.
\end{abstract}
\end{document}
"""
OUTLINE = """General: write it as a funnel, from the wide problem to the aim of this paper.

1. Why risk is hard to study
Risk perception shapes how people act toward hazards, so measuring it matters.

2. How risk perception has been measured
Psychometric scales, factor approaches and regression models.

3. Heterogeneity across people and contexts
Perception differs by country and income, so aggregate models hide differences.

4. The gap and the aim
No study compares perception across hazards.

5. Roadmap
SECTIONFIVE: the rest of the paper is organised as follows.
"""
ASK = ("Write the introduction of my paper based on outlined_intro.md sections 1-4 and the literature review in my library. Use the rules for academic writing of the book to get "
       "cohesion, flow, grammar and orthography right. Cite properly in-text.")

# an article whose DOI is on its third page (a publisher's footer), not on the first two
RISK = [["Risk perception and trust across hazards", "Paul Smith and Ana Lopez, University of Nowhere", "# Introduction",
         "Risk perception is measured with psychometric scales and regression models of perceived risk toward hazards such as floods and earthquakes."],
        ["# Results", "Perceived risk differs between people, and aggregate models of risk perception hide this heterogeneity across hazards."],
        ["Published online 3 March 2019. https://doi.org/10.5555/risk.2019.007"]]
# the same article, but a file that says nothing else about itself: a DOI, and neither authors nor a year
RISK_PLAIN = [RISK[0], RISK[1], ["https://doi.org/10.5555/risk.2019.007"]]
# an article that names no DOI at all
HETERO = [["Heterogeneity of hazard perception across countries and contexts", "Maria Garcia, University of Nowhere", "# Introduction",
           "Hazard perception is heterogeneous across countries and socioeconomic contexts, so a single model of risk perception does not fit."]]
# a guide to writing that also uses the subject's words as examples
GUIDE = [["Academic writing guide for graduate students", "# Cohesion and flow",
          "Academic writing rules for the introduction of a research paper: keep cohesion and flow between paragraphs, mind grammar and orthography. For example, an introduction on "
          "risk perception moves from the wide problem to the aim. Introduction structure in research papers follows a funnel."]]
BIB_RISK = "@article{x,\n author={Smith, Paul and Lopez, Ana},\n title={Risk perception and trust across hazards},\n year={2019},\n journal={Risk Analysis},\n doi={10.5555/risk.2019.007}\n}"
BIB_HETERO = "@article{y,\n author={Garcia, Maria},\n title={Heterogeneity of hazard perception across countries and contexts},\n year={2018},\n journal={Risk Analysis},\n doi={10.5555/het.2018.003}\n}"
CROSSREF = json.dumps({"message": {"items": [{"DOI": "10.5555/het.2018.003", "title": ["Heterogeneity of hazard perception across countries and contexts"], "type": "journal-article",
                                              "author": [{"family": "Garcia", "given": "Maria"}], "issued": {"date-parts": [[2018]]}, "container-title": ["Risk Analysis"]}]}})


def draft_reply(text, basis=None):
    return {"approach": "The introduction follows your outline.", "draft_text": text, "basis": basis if basis is not None else [{"statement": "Risk perception", "source": "request"}], "questions": []}


class Offline(ReviseBase):
    """doi.org answers from DOIS; Crossref (the title lookup) from `crossref`; anything else fails."""
    DOIS = {"10.5555/risk.2019.007": BIB_RISK, "10.5555/het.2018.003": BIB_HETERO}

    def setUp(self):
        super().setUp()
        self.live()
        self._doi, self._fetch = doi_bot.fetch_bibtex, scholar.fetch
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", self._doi)
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self.asked = []

        def fetch(doi):
            self.asked.append(doi)
            if doi in self.DOIS:
                return self.DOIS[doi]
            raise RuntimeError("HTTP 404")
        doi_bot.fetch_bibtex = fetch
        library._ENTRY_CACHE.clear()
        library._TITLE_CACHE.clear()
        self.addCleanup(library._ENTRY_CACHE.clear)
        self.addCleanup(library._TITLE_CACHE.clear)
        self.net = scholar.fetch = Net({"https://api.crossref.org/works": CROSSREF})
        self.bib = Path(self.proj["path"]) / self.proj["bib"]
        self.main = Path(self.proj["path"]) / "main.tex"
        self.main.write_text(PAPER)
        paper_index.refresh(self.name, self.proj, trigger="test")
        files.save_upload(self.proj, "outlined_intro.md", OUTLINE.encode(), purpose="outline")
        self.lib = self.tmp / "Documents" / "Literature"
        self.lib.mkdir(parents=True)

    def read_library(self, **pdfs):
        for name, pages in pdfs.items():
            make_pdf(self.lib / name, pages)
        library.set_folders([str(self.lib)])
        library.update()

    def prepare(self, text=ASK, **kw):
        loc = draft.locate(self.name, self.proj, text)
        self.assertEqual(loc["status"], "ready", loc)
        return loc, draft.prepare(self.name, self.proj, text, loc["anchor"], loc["part"], **kw)


@needs_pdf
class OutlineSections(Offline):
    def test_only_the_sections_asked_for_go_with_the_request_and_the_plan_says_which(self):
        loc, p = self.prepare()
        user = openai_client.user_text(p["body"])
        self.assertIn("<author_outline>", user)
        for kept in ("Why risk is hard to study", "How risk perception has been measured", "Heterogeneity across people", "The gap and the aim", "General: write it as a funnel"):
            self.assertIn(kept, user)
        self.assertNotIn("SECTIONFIVE", user, "section 5 was not asked for")
        self.assertEqual((p["outline"][0]["sections"], p["outline"][0]["where"]), ([1, 2, 3, 4], "sections 1-4 of 5"))

    def test_the_whole_outline_goes_when_the_request_names_no_sections(self):
        loc, p = self.prepare("Write the introduction of my paper based on outlined_intro.md")
        self.assertIn("SECTIONFIVE", openai_client.user_text(p["body"]))
        self.assertEqual(p["outline"][0]["where"], "all of it")

    def test_a_request_that_says_the_outline_means_the_one_the_paper_has(self):
        loc, p = self.prepare("Write the introduction of my paper from the outline, sections 2-3")
        user = openai_client.user_text(p["body"])
        self.assertIn("<author_outline>", user)
        self.assertIn("How risk perception has been measured", user)
        self.assertNotIn("Why risk is hard to study", user)
        self.assertNotIn("The gap and the aim", user)

    def test_with_several_outlines_a_vague_mention_is_not_guessed(self):
        files.save_upload(self.proj, "outlined_discussion.md", b"1. Limits\nThe limits of the study.\n", purpose="outline")
        paper_index.refresh(self.name, self.proj, trigger="test")
        text, shown, notes = files.outline_select(self.name, self.proj, "write the paper from the outline")
        self.assertEqual((text, shown), ("", []))
        self.assertIn("several", notes[0])
        text, shown, notes = files.outline_select(self.name, self.proj, "write the introduction from the outline")
        self.assertEqual([s["file"].rsplit("/", 1)[-1] for s in shown], ["outlined_intro.md"], "the introduction's outline, by its name")

    def test_sections_the_outline_does_not_have_send_all_of_it_and_say_so(self):
        loc, p = self.prepare("Write the introduction from outlined_intro.md sections 8-9")
        self.assertIn("SECTIONFIVE", openai_client.user_text(p["body"]))
        self.assertTrue(any("numbers its sections 1, 2, 3, 4, 5" in w for w in p["warnings"]), p["warnings"])


@needs_pdf
class AnEditFollowsTheOutlineToo(Offline):
    def test_a_rewrite_that_names_the_outline_gets_the_sections_asked_for_and_a_rule_for_them(self):
        ids = [c["id"] for c in revise.resolve_target(self.name, self.proj, "x", "abstract", None, "test")[0]]
        r = revise.run(self.name, self.proj, "rewrite the abstract following outlined_intro.md section 3", chunk_ids=ids, dry_run=True)
        body = r["payloads"][0]["request"]
        user = user_of(body)
        self.assertIn("<author_outline>", user)
        self.assertIn("Perception differs by country and income", user)
        self.assertNotIn("Why risk is hard to study", user)
        self.assertNotIn("SECTIONFIVE", user)
        self.assertEqual((r["outline"][0]["sections"], r["outline"][0]["where"]), ([3], "section(s) 3 of 5"))
        self.assertIn("<author_outline> holds the author's own plan", body.get("instructions") or body["messages"][0]["content"])

    def test_without_naming_it_the_outline_stays_out_of_an_edit(self):
        ids = [c["id"] for c in revise.resolve_target(self.name, self.proj, "x", "abstract", None, "test")[0]]
        r = revise.run(self.name, self.proj, "tighten the abstract", chunk_ids=ids, dry_run=True)
        self.assertNotIn("<author_outline>", user_of(r["payloads"][0]["request"]))
        self.assertEqual(r["outline"], [])


@needs_pdf
class LibraryByTheSubject(Offline):
    def setUp(self):
        super().setUp()
        self.read_library(**{"Smith et al. - 2019 - Risk perception and trust.pdf": RISK, "Garcia - 2018 - Heterogeneity of hazard perception.pdf": HETERO, "Academic writing guide.pdf": GUIDE})

    def test_a_doi_on_a_later_page_is_found_and_a_document_with_none_is_looked_up_by_its_title_later(self):
        docs = {d["rel"]: d for d in library.docs()}
        self.assertEqual(docs["Smith et al. - 2019 - Risk perception and trust.pdf"]["doi"], "10.5555/risk.2019.007", "the publisher's footer on page 3")
        self.assertEqual(docs["Garcia - 2018 - Heterogeneity of hazard perception.pdf"]["doi"], "")

    def test_the_subject_picks_the_papers_and_the_writing_guide_is_never_one_of_them(self):
        loc, p = self.prepare()
        labels = [x["label"] for x in p["library"]]
        self.assertTrue(any("Smith" in x for x in labels) and any("Garcia" in x for x in labels), labels)
        self.assertFalse(any("guide" in x.lower() or "academic writing" in x.lower() for x in labels), labels)
        user = openai_client.user_text(p["body"])
        self.assertNotIn("cohesion and flow between paragraphs", user, "the guide's text never reaches the request as literature")
        self.assertIn("psychometric scales", user)

    def test_the_papers_become_citations_through_their_doi_or_their_title_and_are_written_when_saved(self):
        before = self.bib.read_text()
        loc, p = self.prepare()
        self.assertEqual(sorted(a["key"] for a in p["bib_adds"]), ["Garcia2018Heterogeneity", "Smith2019Risk"])
        self.assertEqual({a["doi"] for a in p["bib_adds"]}, {"10.5555/risk.2019.007", "10.5555/het.2018.003"}, "the second came from Crossref by its title")
        tags = {x["cite_key"]: x["tag"] for x in p["library"] if x["tag"]}
        self.assertEqual(set(tags), {"Smith2019Risk", "Garcia2018Heterogeneity"})
        self.assertEqual(self.bib.read_text(), before)
        pl = draft.plan(self.name, self.proj, ASK, loc["anchor"], loc["part"])
        text = (f"Risk perception is measured with psychometric scales [[{tags['Smith2019Risk']}]], but it is not homogeneous across hazards or countries "
                f"[[{tags['Garcia2018Heterogeneity']}]], so aggregate models hide differences.")
        self.srv.script = [draft_reply(text)]
        res = draft.run(self.name, self.proj, ASK, loc["anchor"], loc["part"], confirm=pl["payload_hash"])
        self.assertEqual(res["status"], "proposed", res["proposal"]["units"][0].get("error"))
        self.assertEqual(sorted(res["proposal"]["units"][0]["added_cites"]), ["\\cite{Garcia2018Heterogeneity}", "\\cite{Smith2019Risk}"])
        out = revise.apply(self.name, self.proj, res["proposal"]["id"], accept="all", unclean_ok=True)
        self.assertEqual(out["status"], "applied")
        self.assertIn("@article{Smith2019Risk,", self.bib.read_text())
        self.assertIn("@article{Garcia2018Heterogeneity,", self.bib.read_text())
        self.assertIn("psychometric scales \\cite{Smith2019Risk}", self.main.read_text())

    def test_the_book_rules_still_come_from_the_book_and_only_as_rules(self):
        loc, p = self.prepare()
        self.assertTrue(p["rule_ids"] and all(i.startswith("B:mini:") for i in p["rule_ids"]))
        self.assertIn("<book_rules>", openai_client.user_text(p["body"]))

    def test_an_edit_that_names_the_book_does_not_search_the_library_for_the_book(self):
        ids = [c["id"] for c in revise.resolve_target(self.name, self.proj, "x", "abstract", None, "test")[0]]
        r = revise.run(self.name, self.proj, "improve the flow of the abstract using the rules for academic writing of the book, and support it with my library", chunk_ids=ids, dry_run=True)
        self.assertFalse(any("guide" in x["label"].lower() or "academic writing" in x["label"].lower() for x in r["library"]), r["library"])


@needs_pdf
class WhyNotCitable(Offline):
    """A document the file says too little about (no authors, no year) and that has no record, no DOI that works and no title Crossref knows cannot be cited: it says why."""

    def setUp(self):
        super().setUp()
        self.read_library(**{"a-hetero.pdf": HETERO, "b-risk.pdf": RISK_PLAIN})

    def test_each_document_that_cannot_be_cited_says_why(self):
        self.DOIS.clear()
        self.addCleanup(self.DOIS.update, {"10.5555/risk.2019.007": BIB_RISK, "10.5555/het.2018.003": BIB_HETERO})
        scholar.fetch = Net({})
        loc, p = self.prepare()
        self.assertEqual(p["cites"], [])
        why = {u["label"]: u["why"] for u in p["uncitable"]}
        self.assertEqual(len(why), 2, why)
        self.assertTrue(any("doi.org" in w and "10.5555/risk.2019.007" in w for w in why.values()), why)
        self.assertTrue(any("names no DOI" in w for w in why.values()), why)
        self.assertTrue(any("hetero" in w.lower() for w in p["warnings"]), "the reasons are in the warning the author sees")

    def test_a_title_that_crossref_does_not_know_is_not_made_a_citation(self):
        scholar.fetch = Net({"https://api.crossref.org/works": json.dumps({"message": {"items": [{"DOI": "10.1/zzz", "title": ["Something else entirely about soil"], "type": "journal-article"}]}})})
        loc, p = self.prepare()
        self.assertEqual({a["doi"] for a in p["bib_adds"]}, {"10.5555/risk.2019.007"})
        self.assertTrue(any("Crossref has no work with its title" in u["why"] for u in p["uncitable"]), p["uncitable"])

    def test_a_title_that_could_not_be_looked_up_is_not_asked_again_at_once(self):
        scholar.fetch = Net({})
        self.prepare()
        asked = len(scholar.fetch.asked)
        self.assertGreater(asked, 0)
        self.prepare()
        self.assertEqual(len(scholar.fetch.asked), asked, "a library without internet does not wait for Crossref with every request")

    def test_with_internet_search_switched_off_the_title_is_not_looked_up(self):
        config.set_paper_setting(self.proj, "use_online", "never")
        scholar.fetch = Net({})
        loc, p = self.prepare()
        self.assertEqual({a["doi"] for a in p["bib_adds"]}, {"10.5555/risk.2019.007"})
        self.assertEqual(scholar.fetch.asked, [])


def openalex(*items):
    """OpenAlex's answer for a few works: (doi, title, year)"""
    return json.dumps({"results": [{"doi": "https://doi.org/" + doi, "title": title, "publication_year": year, "authorships": [], "primary_location": None, "abstract_inverted_index": None,
                                    "cited_by_count": 3} for doi, title, year in items]})


GUIDE_HIT = ("10.1/guide", "Academic writing guidelines for graduate students", 2015)
RISK_HIT = ("10.5555/risk.2019.007", "Risk perception and trust across hazards", 2019)
JUNK_HIT = ("10.1016/j.hm.2016.07.002", "On Khinchin's paper Ideas of intuitionism and the struggle for a subject matter in contemporary mathematics", 2016)


class InTheChat(ChatBase):
    """The chat: citations are asked for, the library cannot supply them, so the app looks on the internet itself, on the subject of the outline, and cites what is about that subject."""
    def setUp(self):
        super().setUp()
        self._fetch, self._doi = scholar.fetch, doi_bot.fetch_bibtex
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self.addCleanup(setattr, doi_bot, "fetch_bibtex", self._doi)
        doi_bot.fetch_bibtex = lambda d: BIB_RISK if d == RISK_HIT[0] else (_ for _ in ()).throw(RuntimeError("HTTP 404"))
        self.net = scholar.fetch = Net({**routes(), "https://api.openalex.org/works": openalex(GUIDE_HIT, JUNK_HIT, RISK_HIT), "https://api.crossref.org/works": json.dumps({"message": {"items": []}})})
        self.main.write_text(PAPER)
        paper_index.refresh(self.name, self.proj, trigger="test")
        files.save_upload(self.proj, "outlined_intro.md", OUTLINE.encode(), purpose="outline")
        self.bib = Path(self.proj["path"]) / self.proj["bib"]

    def proposal(self):
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={self.last('result')['data']['proposal']}")
        return prop

    def test_the_app_searches_the_subject_of_the_outline_itself_and_cites_what_fits(self):
        self.oa.script = [draft_reply("Risk perception shapes how people act toward hazards [[LX901]]. It has been measured with psychometric scales.",
                                      [{"statement": "Risk perception", "source": "L1"}, {"statement": "It has been", "source": "request"}])]
        before = self.bib.read_text()
        r = self.send(ASK)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "draft_plan"], r["messages"])
        plan = r["messages"][1]["data"]
        self.assertEqual(plan["plan"]["web"], {"ask": []}, "the app looks for the sources itself")
        self.assertIsNone(plan["confirm"], "no stop to ask the author to choose")
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        self.assertIn("web", [s["key"] for s in j["steps"]])
        asked = " ".join(u for u in self.net.asked if "openalex" in u).lower()
        self.assertIn("perception", asked, "the words are the subject of the outline")
        for bad in ("writing", "cohesion", "academic", "orthography"):
            self.assertNotIn(bad, asked, "how to write is never searched for")
        prop = self.proposal()
        unit = prop["units"][0]
        self.assertEqual(len(unit["added_cites"]), 1, unit.get("error"))
        self.assertEqual([a["doi"] for a in prop["bib_adds"]], [RISK_HIT[0]])
        shown = [x["label"] + " " + x["title"] for x in prop["library"]]
        self.assertTrue(any("Risk perception and trust" in t for t in shown), shown)
        self.assertFalse(any("Academic writing" in t or "Khinchin" in t for t in shown), "a guide to writing and a paper on mathematics are not offered: " + str(shown))
        self.assertEqual(self.bib.read_text(), before, "its entry is written only when the text that cites it is saved")
        self.assertFalse([m for m in self.chat_thread()["thread"]["messages"] if m["kind"] == "refsearch"], "no candidates to choose from")

    def test_records_that_are_not_about_the_subject_are_never_cited_and_the_author_is_told(self):
        self.net = scholar.fetch = Net({**routes(), "https://api.openalex.org/works": openalex(GUIDE_HIT, JUNK_HIT), "https://api.crossref.org/works": json.dumps({"message": {"items": []}})})
        self.oa.script = [draft_reply("Risk perception shapes how people act toward hazards.")]
        r = self.send(ASK)
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "error", j)
        err = self.last("error")["text"]
        self.assertIn("nothing", err.lower())
        self.assertIn("internet", err.lower())
        self.assertEqual(self.oa.requests, [], "no uncited draft was asked for")

    def test_internet_search_switched_off_says_what_is_missing_instead(self):
        config.set_paper_setting(self.proj, "use_online", "never")
        r = self.send(ASK)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "error"], r["messages"])
        self.assertIn("requires in-text citations", r["messages"][1]["text"])
        self.assertIsNone(r.get("job"))

    def test_a_pasted_paragraph_that_needs_a_citation_is_searched_and_cited_by_the_app(self):
        para = "The second paragraph describes soils, slopes and the access roads that were used to reach every farm in the sample."
        self.main.write_text(MAIN)
        paper_index.refresh(self.name, self.proj, trigger="test")
        hit = ("10.5555/roads.2012.001", "Access roads, soils and slopes in farm surveys", 2012)
        self.net = scholar.fetch = Net({**routes(), "https://api.openalex.org/works": openalex(hit, JUNK_HIT), "https://api.crossref.org/works": json.dumps({"message": {"items": []}})})
        doi_bot.fetch_bibtex = lambda d: ("@article{x,\n author={Diaz, Ana},\n title={Access roads, soils and slopes in farm surveys},\n year={2012},\n journal={Survey Methods},\n doi={%s}\n}" % hit[0]) if d == hit[0] else (_ for _ in ()).throw(RuntimeError("HTTP 404"))
        self.oa.script = [revise_reply((para, para[:-1] + " [[LX901]]."))]
        r = self.send(f"For this paragraph:\n\n{para}\n\nthere was no in-text citation added in the methodology")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "plan"], r["messages"])
        self.assertIn("web", r["messages"][1]["data"]["plan"])
        j = self.wait_job(r["job"])
        self.assertEqual(j["status"], "done", j)
        asked = " ".join(u for u in self.net.asked if "openalex" in u).lower()
        self.assertIn("roads", asked, "the paragraph is what the search words are made from")
        prop = self.proposal()
        self.assertEqual([a["doi"] for a in prop["bib_adds"]], [hit[0]])
        self.assertEqual(len(prop["units"][0]["added_cites"]), 1)
