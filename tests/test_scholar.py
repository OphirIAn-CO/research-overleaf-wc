"""Finding references on the internet: reading a search page, reading a paper's own page, the open scholarly indexes, merging and ranking, and what is kept for a later request.
Nothing here touches the network: `scholar.fetch` is replaced by a table of pages."""
import base64
import json
import subprocess
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tests.helpers import IsolatedHome, common, make_paper
from tests.fake_openai import FakeOpenAI
from writing_core import openai_client, scholar

DDG = """<html><body>
<div class="result results_links"><h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.sciencedirect.com%2Fscience%2Farticle%2Fpii%2FS0001&amp;rut=abc">Armed conflict and <b>pipeline</b> attacks in Colombia</a></h2>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">Attacks on <b>pipelines</b> by armed groups in <b>Colombia</b>.</a></div>
<div class="result"><a class="result__a" href="//duckduckgo.com/y.js?ad_provider=bing&amp;u=1">An advertisement</a></div>
<div class="result"><h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Freport.html">A report without a DOI | Example</a></h2>
<div class="result__snippet">Security of energy infrastructure.</div></div>
</body></html>"""

BING_URL = "https://www.mdpi.com/2071-1050/12/5/1234"
BING = f"""<html><body><ol id="b_results">
<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&&p=xyz&u=a1{base64.urlsafe_b64encode(BING_URL.encode()).decode().rstrip('=')}&ntb=1">Violence and infrastructure</a></h2>
<div class="b_caption"><p>How violence damages <strong>infrastructure</strong>.</p></div></li>
<li class="b_algo"><h2><a href="https://example.org/other">Other</a></h2><p>Second snippet.</p></li>
</ol></body></html>"""

PAPER1 = """<html><head><title>Armed conflict and pipeline attacks in Colombia - ScienceDirect</title>
<meta name="citation_title" content="Armed conflict and pipeline attacks in Colombia">
<meta name="citation_author" content="Rojas, Camila"><meta name="citation_author" content="Smith, John">
<meta name="citation_doi" content="10.1016/j.enpol.2019.05.012">
<meta name="citation_publication_date" content="2019/08/01"><meta name="citation_journal_title" content="Energy Policy">
<meta name="citation_abstract" content="We study 412 attacks on oil pipelines in Colombia between 2000 and 2016 and show that armed groups targeted infrastructure near disputed territory.">
</head><body></body></html>"""

PAPER2 = """<html><head><title>Violence and infrastructure | MDPI</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"ScholarlyArticle","headline":"Violence and infrastructure in conflict regions",
"author":[{"@type":"Person","name":"Ana Lopez"},{"@type":"Person","name":"Mark Ruiz"}],"datePublished":"2020-03-02","identifier":"https://doi.org/10.3390/su12051234",
"description":"Infrastructure operators in conflict regions restrict the release of coordinates for safety reasons.","isPartOf":{"@type":"Periodical","name":"Sustainability"}}</script>
</head></html>"""

NO_DOI = "<html><head><title>A report without a DOI</title><meta name='description' content='Security of energy infrastructure.'></head></html>"

OPENALEX = {"results": [
    {"doi": "https://doi.org/10.1016/J.ENPOL.2019.05.012", "title": "Armed conflict and pipeline attacks in Colombia", "display_name": "x", "publication_year": 2019, "cited_by_count": 55,
     "authorships": [{"author": {"display_name": "Camila Rojas"}}, {"author": {"display_name": "John Smith"}}],
     "primary_location": {"source": {"display_name": "Energy Policy"}}, "abstract_inverted_index": {"We": [0], "study": [1], "attacks": [2], "on": [3], "pipelines": [4]}, "type": "article"},
    {"doi": "https://doi.org/10.1000/other.1", "title": "Open data for water networks", "publication_year": 2018, "authorships": [], "primary_location": None,
     "abstract_inverted_index": None, "cited_by_count": 0},
    {"doi": None, "title": "No DOI at all", "publication_year": 2018}]}

CROSSREF = {"message": {"items": [
    {"DOI": "10.1016/j.enpol.2019.05.012", "title": ["Armed conflict and pipeline attacks in Colombia"], "author": [{"family": "Rojas", "given": "Camila"}], "issued": {"date-parts": [[2019, 8]]},
     "container-title": ["Energy Policy"], "is-referenced-by-count": 55, "type": "journal-article", "abstract": "<jats:p>We study <jats:italic>attacks</jats:italic> on pipelines.</jats:p>"},
    {"DOI": "10.5555/not-a-paper", "title": ["A peer review report"], "type": "peer-review"},
    {"DOI": "10.1111/xyz", "title": ["A report without a DOI"], "type": "report", "author": [{"family": "Gomez", "given": "Luis"}], "issued": {"date-parts": [[2021]]}, "container-title": []}]}}


class Net:
    """A table of pages: url prefix -> text, or an exception. Records what was asked."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, url, accept="", timeout=0, ua=""):
        self.asked.append(url)
        for prefix, page in self.pages.items():
            if url.startswith(prefix):
                if isinstance(page, Exception):
                    raise page
                return (page(url) if callable(page) else page), url
        raise urllib.error.URLError("unreachable in this test: " + url)


def routes(**extra):
    return {"https://html.duckduckgo.com/html/": DDG, "https://api.openalex.org/works": json.dumps(OPENALEX), "https://api.crossref.org/works": json.dumps(CROSSREF),
            "https://www.sciencedirect.com/": PAPER1, "https://example.org/report.html": NO_DOI, **extra}


class Base(IsolatedHome):
    def setUp(self):
        super().setUp()
        self._fetch = scholar.fetch
        self.addCleanup(setattr, scholar, "fetch", self._fetch)

    def net(self, pages):
        scholar.fetch = Net(pages)
        return scholar.fetch


class ReadingPages(Base):
    def test_a_duckduckgo_page_gives_the_pages_it_points_to_and_skips_the_adverts(self):
        r = scholar.parse_results("ddg", DDG)
        self.assertEqual([x["url"] for x in r], ["https://www.sciencedirect.com/science/article/pii/S0001", "https://example.org/report.html"])
        self.assertEqual(r[0]["title"], "Armed conflict and pipeline attacks in Colombia")
        self.assertIn("Attacks on pipelines by armed groups in Colombia.", r[0]["snippet"])
        self.assertEqual(r[1]["snippet"], "Security of energy infrastructure.")

    def test_a_bing_page_gives_the_real_address_behind_its_redirect_link(self):
        r = scholar.parse_results("bing", BING)
        self.assertEqual([x["url"] for x in r], [BING_URL, "https://example.org/other"])
        self.assertEqual(r[0]["title"], "Violence and infrastructure")
        self.assertEqual(r[0]["snippet"], "How violence damages infrastructure.")

    def test_a_page_that_is_not_a_results_page_gives_nothing(self):
        self.assertEqual(scholar.parse_results("ddg", "<html><body>Please verify you are human</body></html>"), [])
        self.assertEqual(scholar.parse_results("bing", ""), [])

    def test_the_citation_tags_of_a_journal_page(self):
        c = scholar.parse_landing(PAPER1, "https://www.sciencedirect.com/science/article/pii/S0001")
        self.assertEqual((c["doi"], c["year"], c["venue"]), ("10.1016/j.enpol.2019.05.012", 2019, "Energy Policy"))
        self.assertEqual(c["authors"], ["Rojas, Camila", "Smith, John"])
        self.assertEqual(c["title"], "Armed conflict and pipeline attacks in Colombia")
        self.assertTrue(c["abstract"].startswith("We study 412 attacks"))

    def test_a_page_with_only_structured_data(self):
        c = scholar.parse_landing(PAPER2, BING_URL)
        self.assertEqual((c["doi"], c["year"], c["venue"], c["authors"]), ("10.3390/su12051234", 2020, "Sustainability", ["Ana Lopez", "Mark Ruiz"]))
        self.assertIn("restrict the release of coordinates", c["abstract"])

    def test_the_doi_can_be_in_the_address(self):
        page = "<html><head><meta name='citation_title' content='A title'></head></html>"
        self.assertEqual(scholar.parse_landing(page, "https://onlinelibrary.wiley.com/doi/full/10.1002/ABC.123?casa=1")["doi"], "10.1002/abc.123")

    def test_a_page_with_no_doi_or_no_title_is_not_a_candidate(self):
        self.assertIsNone(scholar.parse_landing(NO_DOI, "https://example.org/report.html"))
        self.assertIsNone(scholar.parse_landing("<meta name='citation_doi' content='10.1/x'>", ""))

    def test_dois_are_normalised(self):
        for raw, doi in (("https://doi.org/10.1016/J.Foo.2020.01.001/pdf?x=1", "10.1016/j.foo.2020.01.001"), ("doi:10.1000/xyz(1)-2.", "10.1000/xyz(1)-2"), ("https://x.org/a", "")):
            self.assertEqual(scholar.norm_doi(raw), doi)

    def test_the_indexes(self):
        net = self.net({"https://api.openalex.org/works": json.dumps(OPENALEX), "https://api.crossref.org/works": json.dumps(CROSSREF)})
        a = scholar.openalex("pipeline attacks")
        self.assertEqual([c["doi"] for c in a], ["10.1016/j.enpol.2019.05.012", "10.1000/other.1"])
        self.assertEqual((a[0]["abstract"], a[0]["venue"], a[0]["authors"], a[0]["cited_by"]), ("We study attacks on pipelines", "Energy Policy", ["Camila Rojas", "John Smith"], 55))
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(net.asked[0]).query)
        self.assertEqual((q["search"], q["filter"]), (["pipeline attacks"], ["has_doi:true"]))
        c = scholar.crossref("pipeline attacks")
        self.assertEqual([x["doi"] for x in c], ["10.1016/j.enpol.2019.05.012", "10.1111/xyz"], "a peer review report is not a reference")
        self.assertEqual((c[0]["abstract"], c[0]["year"], c[0]["authors"]), ("We study attacks on pipelines.", 2019, ["Rojas, Camila"]))


class Searching(Base):
    def test_the_web_and_the_indexes_are_merged_by_doi_and_ranked(self):
        net = self.net(routes())
        out = scholar.search(["armed conflict pipeline attacks Colombia"])
        dois = [c["doi"] for c in out["candidates"]]
        self.assertEqual(dois[0], "10.1016/j.enpol.2019.05.012")
        self.assertEqual(len(dois), len(set(dois)))
        first = out["candidates"][0]
        self.assertEqual(sorted(first["sources"]), ["crossref", "openalex", "web"])
        self.assertEqual((first["label"], first["id"]), ("Rojas and Smith 2019", first["doi"]))
        self.assertTrue(first["abstract"].startswith("We study 412 attacks"), "the longest abstract is kept")
        self.assertEqual({t["source"]: t["ok"] for t in out["tried"]}, {"web": True, "openalex": True, "crossref": True})
        self.assertTrue(any("sciencedirect.com" in u for u in net.asked), "the page itself was read")

    def test_a_page_without_a_doi_is_looked_up_by_its_title_and_otherwise_listed_apart(self):
        crossref = {"message": {"items": [{"DOI": "10.1111/xyz", "title": ["A report without a DOI"], "type": "report", "author": [{"family": "Gomez", "given": "Luis"}],
                                           "issued": {"date-parts": [[2021]]}, "container-title": []}]}}
        self.net(routes(**{"https://api.crossref.org/works": json.dumps(crossref)}))
        out = scholar.search(["energy infrastructure security"], use_index=False)
        self.assertIn("10.1111/xyz", [c["doi"] for c in out["candidates"]])
        self.net(routes(**{"https://api.crossref.org/works": json.dumps({"message": {"items": []}})}))
        out = scholar.search(["energy infrastructure security"])
        self.assertEqual([o["url"] for o in out["others"]], ["https://example.org/report.html"])

    def test_bing_is_asked_when_duckduckgo_gives_nothing(self):
        net = self.net(routes(**{"https://html.duckduckgo.com/html/": "<html>captcha</html>", "https://www.bing.com/search": BING, "https://www.mdpi.com/": PAPER2}))
        out = scholar.search(["violence infrastructure"], use_index=False)
        self.assertEqual([c["doi"] for c in out["candidates"]], ["10.3390/su12051234"])
        self.assertTrue(any(u.startswith("https://www.bing.com/search") for u in net.asked))

    def test_when_the_web_fails_the_indexes_still_answer_and_the_failure_is_reported(self):
        self.net(routes(**{"https://html.duckduckgo.com/html/": urllib.error.HTTPError("u", 429, "Too Many", {}, None), "https://www.bing.com/search": urllib.error.URLError("refused")}))
        out = scholar.search(["armed conflict pipeline attacks Colombia"])
        self.assertTrue(out["candidates"])
        web = next(t for t in out["tried"] if t["source"] == "web")
        self.assertFalse(web["ok"])
        self.assertIn("429", web["note"])

    def test_when_nothing_can_be_reached_it_says_so_plainly(self):
        self.net({})
        with self.assertRaises(scholar.ScholarError) as e:
            scholar.search(["anything about pipelines"])
        self.assertIn("could not reach the internet", str(e.exception))

    def test_an_empty_search_asks_what_to_look_for(self):
        self.net(routes())
        with self.assertRaises(scholar.ScholarError):
            scholar.search(["  "])

    def test_candidates_come_only_from_what_a_page_or_an_index_said(self):
        self.net(routes())
        out = scholar.search(["pipeline attacks"], limit=50)
        known = {"10.1016/j.enpol.2019.05.012", "10.1000/other.1", "10.1111/xyz"}
        self.assertTrue({c["doi"] for c in out["candidates"]} <= known)

    def test_progress_steps_are_reported(self):
        from writing_core import revise
        self.net(routes())
        seen = []
        scholar.search(["armed conflict pipeline attacks"], steps=revise.Steps(lambda items: seen.append([(i["key"], i["state"]) for i in items])))
        self.assertEqual(seen[-1], [("web", "done"), ("index", "done"), ("pages", "done")])


class Safety(Base):
    def test_a_search_result_cannot_send_the_app_to_its_own_network(self):
        for url in ("http://127.0.0.1:8765/api/state", "http://localhost/", "http://169.254.169.254/latest/meta-data/", "http://192.168.1.1/", "http://[::1]/", "http://printer.local/", "file:///etc/passwd",
                    "ftp://example.org/x"):
            with self.assertRaises(scholar.ScholarError, msg=url):
                scholar._check_url(url)
        scholar._check_url("https://93.184.216.34/")

    def test_a_redirect_to_a_private_address_is_refused(self):
        h = scholar._SafeRedirects()
        with self.assertRaises(scholar.ScholarError):
            h.redirect_request(urllib.request.Request("https://example.org/"), None, 302, "Found", {}, "http://127.0.0.1:8765/")

    def test_a_blocked_page_is_just_a_page_that_could_not_be_read(self):
        pages = routes(**{"https://www.sciencedirect.com/": scholar.ScholarError("I only open public web pages."), "https://api.crossref.org/works": json.dumps({"message": {"items": []}})})
        self.net(pages)
        out = scholar.search(["pipeline attacks"], use_index=False)
        self.assertEqual(out["candidates"], [])
        self.assertEqual(len(out["others"]), 2)


class SearchWords(Base):
    ANSWER = ("Question: What specific additional citations would help justify the points made about security and its effects on dataset reliability?\n"
              "Answer: As in Colombia, there is a high issue with security of infrastructure of this type, there is a lot of third party interference, but for justifying this find relevant "
              "information and add a citation of how this security and violence affects the infrastructure")

    def test_the_subject_of_the_request_not_the_words_about_the_task(self):
        ts = scholar.focus_terms(self.ANSWER, "Address this reviewer comment: the description of the data curation process is insufficient")
        self.assertEqual(ts[0], "colombia", "a place name comes first")
        for w in ("security", "infrastructure", "violence"):
            self.assertIn(w, ts)
        for w in ("reviewer", "comment", "citation", "information", "justifying"):
            self.assertNotIn(w, ts)

    def test_local_queries_are_short(self):
        qs = scholar.local_queries(self.ANSWER)
        self.assertTrue(1 <= len(qs) <= 2 and all(len(q.split()) <= 7 for q in qs), qs)

    def test_the_model_makes_the_queries_and_the_cost_is_reported(self):
        srv = FakeOpenAI("responses")
        self.addCleanup(srv.close)
        cfg = {"api_key": "sk-test", "model": "m", "base_url": srv.url, "api": "responses", "timeout": 10, "max_output_tokens": 500}
        srv.script = [{"queries": ["armed conflict attacks on energy infrastructure Colombia", "security restrictions geospatial data release critical infrastructure",
                                   "10.1016/j.enpol.2019.05.012", "x"]}]
        qs, usage = scholar.plan_queries(cfg, "Address this reviewer comment", self.ANSWER, "The pipeline network was georeferenced from public records.", "What citations would help?")
        self.assertEqual(qs, ["armed conflict attacks on energy infrastructure Colombia", "security restrictions geospatial data release critical infrastructure"])
        self.assertEqual(usage["input_tokens"], 120)
        sent = openai_client.user_text(srv.requests[0]["body"])
        self.assertIn("<author_answers>", sent)
        self.assertIn("georeferenced from public records", sent)

    def test_if_the_model_fails_the_local_queries_are_used(self):
        srv = FakeOpenAI("responses")
        self.addCleanup(srv.close)
        cfg = {"api_key": "sk-test", "model": "m", "base_url": srv.url, "api": "responses", "timeout": 10, "max_output_tokens": 500}
        srv.script = [(500, {"error": {"message": "boom"}}), (500, {"error": {"message": "boom"}})]
        openai_client.SLEEP = lambda s: None
        qs, usage = scholar.plan_queries(cfg, "x", self.ANSWER)
        self.assertIsNone(usage)
        self.assertTrue(qs and "colombia" in qs[0])


class Remembering(Base):
    def setUp(self):
        super().setUp()
        self.p = make_paper(self.tmp, "paper", "\\section{Intro}\nHello.\n", bib="@article{rojas2019armed, title={Armed conflict}}\n")
        self.proj = {"path": str(self.p), "bib": "references.bib"}
        self.item = {"doi": "10.1016/j.enpol.2019.05.012", "key": "rojas2019armed", "title": "Armed conflict and pipeline attacks in Colombia", "authors": ["Rojas, Camila", "Smith, John"],
                     "year": 2019, "venue": "Energy Policy", "abstract": "We study 412 attacks on oil pipelines in Colombia and show that armed groups targeted infrastructure.", "url": "https://doi.org/10.1016/j.enpol.2019.05.012"}

    def test_a_remembered_reference_comes_back_for_a_request_about_its_subject(self):
        scholar.remember(self.proj, [self.item])
        got = scholar.recall(self.proj, "justify that armed groups attack pipelines and infrastructure in Colombia")
        self.assertEqual([c["cite_key"] for c in got], ["rojas2019armed"])
        c = got[0]
        self.assertEqual((c["label"], c["where"], c["online"], c["in_bib"]), ("Rojas and Smith 2019", "abstract (found online)", True, True))
        self.assertIn("412 attacks", c["text"])

    def test_unrelated_requests_get_nothing(self):
        scholar.remember(self.proj, [self.item])
        self.assertEqual(scholar.recall(self.proj, "make the methods more concise and fix grammar"), [])

    def test_a_reference_that_left_the_bibliography_is_not_offered(self):
        scholar.remember(self.proj, [self.item])
        (self.p / "references.bib").write_text("@article{other, title={X}}\n")
        self.assertEqual(scholar.recall(self.proj, "armed groups attack pipelines infrastructure Colombia"), [])

    def test_the_memory_is_in_the_papers_hidden_folder_and_is_kept_between_runs(self):
        scholar.remember(self.proj, [self.item])
        scholar.remember(self.proj, [dict(self.item, title="Armed conflict and pipeline attacks in Colombia (2nd)")])
        data = json.loads((self.p / ".overleaf-skill" / "found_refs.json").read_text())
        self.assertEqual(len(data), 1)
        self.assertIn("2nd", data[0]["title"])
        ignored = subprocess.run(["git", "-C", str(self.p), "check-ignore", "-q", ".overleaf-skill/found_refs.json"])
        self.assertEqual(ignored.returncode, 0, "git ignores the folder")


if __name__ == "__main__":
    unittest.main()
