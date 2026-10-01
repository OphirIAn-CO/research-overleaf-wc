import json
import multiprocessing
import subprocess
import unittest
from pathlib import Path

from tests.helpers import IsolatedHome, common, git, make_paper
from writing_core import chunk_tex, config, events, paper_index as pi, store

MAIN = r"""\documentclass{article}
\usepackage{amsmath}
\title{Soil Moisture Effects on Crop Yield}
\author{A. Author}
\begin{document}
\maketitle
\begin{abstract}
We study how soil moisture shapes crop yield across 40 farms. Yield rose 12\% under moderate irrigation.
\end{abstract}
\keywords{soil moisture, yield, irrigation}

\section{Introduction}\label{sec:intro}
Crop yield depends on water. % TODO cite more
Farmers need guidance \cite{smith2020}. We ask whether moisture predicts yield.

\input{sections/methods}

\section{Results}
We report the results here. Table~\ref{tab:a} shows means.
\begin{table}
\begin{tabular}{ll}
a & b \\

c & d \\
\end{tabular}
\caption{Means.}\label{tab:a}
\end{table}

\paragraph{Effect size} The effect was $d=0.8$.

\section{Discussion}
Yield rose with moisture.
\begin{comment}
hidden text should not be indexed
\end{comment}

\bibliographystyle{plain}
\bibliography{references}
\end{document}
"""
METHODS = r"""\section{Materials and Methods}
\subsection{Participants and sampling}
We sampled 40 farms by stratified random sampling. Inclusion required at least five hectares.

Informed consent was obtained from all farmers.

\subsection{Statistical analysis}
We fitted a mixed-effects regression of yield on moisture with farm as a random intercept.
"""
BIB = ("@article{smith2020, author={Smith, J. and Lee, K.}, title={Water and {Yield}}, year={2020}}\n"
       "@book{doe2019, author={Doe, A.}, title = {Soil}, year = 2019}\n@comment{ignored}\n")


def paper(test, name="alpha", main=MAIN, methods=METHODS, extra=None):
    files = {"sections/methods.tex": methods, **(extra or {})}
    root = make_paper(test.tmp / "papers", name, main, bib=BIB, extra=files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    common.register(str(root), name)
    return common.resolve(name)


class TexParsing(unittest.TestCase):
    def test_comment_stripping_respects_escapes(self):
        self.assertEqual(chunk_tex.strip_comment(r"50\% of farms % note"), r"50\% of farms ")
        self.assertEqual(chunk_tex.strip_comment(r"a \\% real comment"), r"a \\")
        self.assertEqual(chunk_tex.strip_comment("no comment"), "no comment")

    def test_chunks_have_exact_line_ranges_and_raw_text(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(MAIN)
            (root / "sections" / "methods.tex").write_text(METHODS)
            r = chunk_tex.parse_paper(root, "alpha")
            files = {"main.tex": MAIN.split("\n"), "sections/methods.tex": METHODS.split("\n")}
            for c in r["chunks"]:
                self.assertEqual(c["text"], "\n".join(files[c["file"]][c["line_start"] - 1:c["line_end"]]), c["id"])
            by = {c["id"].split("#", 1)[1]: c for c in r["chunks"]}
            self.assertEqual(by["introduction#k01"]["part"], "introduction")
            self.assertIn("methods.analysis", by["materials-and-methods-statistical-analysis#k01"]["tags"])
            self.assertEqual(r["files"], ["main.tex", "sections/methods.tex"])

    def test_comment_env_bibliography_and_preamble_are_not_indexed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(MAIN)
            (root / "sections" / "methods.tex").write_text(METHODS)
            text = " ".join(c["text"] for c in chunk_tex.parse_paper(root, "alpha")["chunks"])
            self.assertNotIn("hidden text", text)
            self.assertNotIn("bibliographystyle", text)
            self.assertNotIn("usepackage", text)
            intro = next(c for c in chunk_tex.parse_paper(root, "alpha")["chunks"] if "#introduction#" in c["id"])
            self.assertIn("TODO cite more", intro["text"])  # raw text keeps comments so edits stay exact
            self.assertNotIn("TODO", intro["plain"])  # but search never sees them

    def test_environment_blank_lines_do_not_split_a_table(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "main.tex").write_text(MAIN)
            (Path(d) / "sections").mkdir()
            (Path(d) / "sections" / "methods.tex").write_text(METHODS)
            res = next(c for c in chunk_tex.parse_paper(d, "p")["chunks"] if "#results#" in c["id"])
            self.assertIn(r"\begin{tabular}", res["text"])
            self.assertIn(r"\end{tabular}", res["text"])
            self.assertIn(r"c & d", res["text"])

    def test_includes_stay_inside_the_paper_cycles_and_missing_files_warn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "paper"
            root.mkdir()
            (Path(d) / "secret.tex").write_text(r"\section{Secret}" + "\nTop secret words here and there.\n")
            (root / "main.tex").write_text("\\begin{document}\n\\input{../secret}\n\\input{missing}\n\\input{a}\n\\end{document}\n")
            (root / "a.tex").write_text("\\section{A}\nSome words in a section here.\n\\input{a}\n")
            r = chunk_tex.parse_paper(root, "p")
            self.assertNotIn("Top secret", " ".join(c["text"] for c in r["chunks"]))
            joined = " ".join(r["warnings"])
            self.assertIn("missing", joined)
            self.assertIn("cycle", joined)

    def test_multiline_starred_and_optional_arg_headings_and_duplicates(self):
        import tempfile
        tex = ("\\begin{document}\n\\section*{A long\n title}\nText one is here with words.\n"
               "\\subsection[short]{Same}\nSub text one with words.\n\\subsection{Same}\nSub text two with words.\n\\end{document}\n")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "main.tex").write_text(tex)
            r = chunk_tex.parse_paper(d, "p")
            titles = [c["title"] for c in r["chunks"]]
            self.assertEqual(titles, ["A long title", "Same", "Same"])
            ids = [c["id"] for c in r["chunks"]]
            self.assertEqual(len(ids), len(set(ids)))  # duplicate headings get distinct ids
            self.assertTrue(ids[2].endswith("~2"))

    def test_ids_and_hashes_are_stable_when_another_section_changes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sections").mkdir()
            (root / "main.tex").write_text(MAIN)
            (root / "sections" / "methods.tex").write_text(METHODS)
            a = {c["id"]: c["hash"] for c in chunk_tex.parse_paper(root, "p")["chunks"]}
            (root / "main.tex").write_text(MAIN.replace("Yield rose with moisture.", "Yield rose strongly with moisture today."))
            b = {c["id"]: c["hash"] for c in chunk_tex.parse_paper(root, "p")["chunks"]}
            self.assertEqual(set(a), set(b))
            diff = [i for i in a if a[i] != b[i]]
            self.assertEqual(len(diff), 1)
            self.assertIn("#discussion#", diff[0])

    def test_bib_and_markdown_sources(self):
        entries = chunk_tex.parse_bib(BIB, "p", "references.bib")
        self.assertEqual([e["title"] for e in entries], ["smith2020", "doe2019"])
        self.assertIn("Water and Yield", entries[0]["text"])
        self.assertIn("2019", entries[1]["text"])
        md = "# Overleaf skill memory\n\n## Project snapshot\n- Repo path: x\n- Key files: main.tex and references.bib with words\n\n## Session log\n\n### 2026-05-01\n- What was done: fetched several Mendeley references for the methods section\n"
        chunks = chunk_tex.parse_markdown(md, "p", ".overleaf-skill/memory.md", "memory")
        self.assertEqual([c["title"] for c in chunks], ["Project snapshot", "2026-05-01"])
        self.assertTrue(all(c["kind"] == "memory" for c in chunks))


class Refresh(IsolatedHome):
    def test_first_refresh_second_is_a_free_skip(self):
        name, proj = paper(self)
        r1 = pi.refresh(name, proj, "t")
        self.assertEqual((r1["added"], r1["changed"], r1["removed"]), (r1["total"], 0, 0))
        self.assertEqual(r1["total"], 11)  # 9 tex + 2 bib
        before = pi.index_path(proj).stat().st_mtime_ns
        r2 = pi.refresh(name, proj, "t")
        self.assertTrue(r2["skipped_scan"])
        self.assertEqual((r2["added"], r2["changed"], r2["removed"], r2["unchanged"]), (0, 0, 0, 11))
        self.assertEqual(pi.index_path(proj).stat().st_mtime_ns, before)  # no writes at all

    def test_one_edited_paragraph_is_exactly_one_changed_chunk(self):
        name, proj = paper(self)
        pi.refresh(name, proj)
        root = Path(proj["path"])
        (root / "sections" / "methods.tex").write_text(METHODS.replace("40 farms", "42 farms"))
        r = pi.refresh(name, proj, "after_edit")
        self.assertEqual((r["added"], r["changed"], r["removed"], r["unchanged"]), (0, 1, 0, 10))
        con = pi.open_index(name, proj)
        self.assertIn("42 farms", con.execute("SELECT text FROM chunks WHERE id LIKE '%participants-and-sampling%'").fetchone()[0])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM chunks_fts WHERE plain MATCH '\"42\"'").fetchone()[0], 1)
        con.close()

    def test_removed_and_added_sections(self):
        name, proj = paper(self)
        pi.refresh(name, proj)
        root = Path(proj["path"])
        (root / "main.tex").write_text(MAIN.replace("\\section{Discussion}\nYield rose with moisture.\n", "")
                                       .replace("\\bibliographystyle", "\\section{Conclusion}\nIn short, moisture matters for yield.\n\\bibliographystyle"))
        r = pi.refresh(name, proj)
        self.assertEqual((r["added"], r["removed"]), (1, 1))
        con = pi.open_index(name, proj)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM chunks WHERE id LIKE '%#discussion#%'").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunk_id LIKE '%#discussion#%'").fetchone()[0], 0)
        con.close()

    def test_every_pass_is_logged_with_counts(self):
        name, proj = paper(self)
        pi.refresh(name, proj, "pass_start")
        pi.refresh(name, proj, "after_pull")
        evs = events.read(config.paper_dir(proj), types={"index_refresh"})
        self.assertEqual([e["trigger"] for e in evs], ["pass_start", "after_pull"])
        self.assertEqual((evs[0]["added"], evs[1]["added"], evs[1]["unchanged"]), (11, 0, 11))
        self.assertTrue(all(e["paper"] == "alpha" for e in evs))

    def test_memory_and_review_files_are_indexed(self):
        name, proj = paper(self)
        d = config.paper_dir(proj)
        (d / "reviews").mkdir(parents=True)
        (d / "memory.md").write_text("# m\n\n## Project snapshot\n- Mendeley group: Soil papers and several other words here\n\n## Session log\n\n### 2026-05-01\n- fetched twelve references about irrigation into the bib file today\n")
        (d / "reviews" / "2026-05-02-review.md").write_text("## Verdict\nMajor revision: the sampling frame is unclear and needs more detail on exclusion.\n")
        pi.refresh(name, proj)
        r = pi.query(name, proj, "irrigation references fetched", kind="memory")
        self.assertEqual([c["kind"] for c in r["chunks"]], ["memory"])
        r = pi.query(name, proj, "sampling frame unclear", kind=["review"])
        self.assertEqual([c["kind"] for c in r["chunks"]], ["review"])

    def test_nothing_is_ever_visible_to_git(self):
        name, proj = paper(self)
        pi.refresh(name, proj)
        (config.paper_dir(proj) / "memory.md").write_text("x")
        events.log(config.paper_dir(proj), "error", message="m")
        out = subprocess.run(["git", "-C", proj["path"], "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "")  # .overleaf-skill/ excluded without touching .gitignore
        self.assertFalse((Path(proj["path"]) / ".gitignore").exists())
        self.assertIn(".overleaf-skill/", (Path(proj["path"]) / ".git" / "info" / "exclude").read_text())

    def test_damaged_index_rebuilds_itself(self):
        name, proj = paper(self)
        pi.refresh(name, proj)
        pi.index_path(proj).write_bytes(b"junk" * 1000)
        r = pi.refresh(name, proj)
        self.assertEqual(r["added"], 11)
        self.assertTrue(pi.index_path(proj).with_name("index.sqlite.corrupt").exists())


class Query(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)

    def q(self, *a, **kw):
        return pi.query(self.name, self.proj, *a, **kw)

    def test_my_methods_returns_the_whole_part_in_document_order(self):
        r = self.q("improve my methodology")
        self.assertEqual(r["mode"], "part")
        self.assertEqual([c["title"] for c in r["chunks"]], ["Participants and sampling", "Statistical analysis"])
        self.assertTrue(all(c["file"] == "sections/methods.tex" for c in r["chunks"]))
        self.assertGreater(r["manuscript_tokens"], 200)
        self.assertLess(r["tokens"], r["manuscript_tokens"] / 2)  # the RAG point: a fraction of the manuscript

    def test_big_part_over_budget_is_ranked_not_dumped(self):
        r = self.q("methods regression", budget=40)
        self.assertEqual(r["mode"], "part-ranked")
        self.assertEqual([c["title"] for c in r["chunks"]], ["Statistical analysis"])
        self.assertEqual(r["truncated"], 1)

    def test_explicit_part_and_sub_part(self):
        r = self.q("", part="methods.analysis")
        self.assertEqual([c["title"] for c in r["chunks"]], ["Statistical analysis"])  # the sub-part narrows the part
        self.assertEqual(r["mode"], "part")
        r = self.q("polish the statistical analysis")
        self.assertEqual([c["title"] for c in r["chunks"]], ["Statistical analysis"])
        r = self.q("", part="methods")
        self.assertEqual(len(r["chunks"]), 2)
        with self.assertRaises(ValueError):
            self.q("x", part="introdution")

    def test_search_finds_text_without_a_part(self):
        r = self.q("stratified random sampling")
        self.assertEqual(r["mode"], "part")  # "sampling" routes to methods, which fits the budget
        r = self.q("farm random intercept")
        self.assertEqual(r["chunks"][0]["title"], "Statistical analysis")
        self.assertEqual(self.q("zzzzqqqq")["mode"], "none")

    def test_empty_query_is_the_outline(self):
        r = self.q("")
        self.assertEqual(r["mode"], "outline")
        self.assertEqual(r["main"], "main.tex")
        self.assertIn("Materials and Methods", [o["title"] for o in r["outline"]])
        text = pi.format_paper_result(r)
        self.assertIn("sections/methods.tex:7", text)

    def test_kind_bib_is_explicit_only(self):
        self.assertTrue(all(c["kind"] != "bib" for c in self.q("soil water yield")["chunks"]))
        r = self.q("Smith water yield", kind="bib")
        self.assertEqual(r["chunks"][0]["title"], "smith2020")

    def test_trace_reports_tokens_and_index_state(self):
        pi.refresh(self.name, self.proj)
        r = self.q("improve my methods")
        self.assertRegex(r["trace"], r"^\[paper alpha: 2 chunks, \d+ of \d+ tok \(methods\), part \| index: 0 changed, 11 unchanged\]$")
        ev = events.read(config.paper_dir(self.proj), types={"retrieve"})[-1]
        self.assertEqual(ev["kind"], "paper")
        self.assertEqual(ev["paper"], "alpha")
        self.assertTrue(all(i.startswith("P:alpha:") for i in ev["ids"]))

    def test_get_chunks_and_part_chunks(self):
        ids = [c["id"] for c in pi.part_chunks(self.name, self.proj, "methods")]
        got = pi.get_chunks(self.name, self.proj, ids + ["P:alpha:nope"])
        self.assertEqual([c["id"] for c in got], ids)
        self.assertIn("random intercept", got[1]["text"])


class Isolation(IsolatedHome):
    """The core requirement: no memory shared between papers."""

    def setUp(self):
        super().setUp()
        self.a = paper(self, "alpha", extra={"notes.tex": ""})
        beta_main = MAIN.replace("Soil Moisture Effects on Crop Yield", "Graph Neural Networks for Traffic")
        beta_main = beta_main.replace("Crop yield depends on water.", "Traffic forecasting depends on graph structure.")
        beta_methods = METHODS.replace("40 farms by stratified random sampling", "sensors by a quasar-calibrated protocol")
        self.b = paper(self, "beta", main=beta_main, methods=beta_methods)
        # the unique-to-A word lives only in alpha: write it into A's memory and review files too
        da = config.paper_dir(self.a[1])
        (da / "reviews").mkdir(parents=True)
        (da / "reviews" / "r.md").write_text("## Verdict\nAlphaOnlyReviewerWord: major revision needed on the rhubarb protocol section.\n")
        (da / "memory.md").write_text("# m\n\n## Project snapshot\n- AlphaOnlyMemoryWord is the Mendeley group for this paper only.\n")

    def test_a_term_unique_to_alpha_returns_nothing_for_beta(self):
        for word in ("AlphaOnlyReviewerWord", "AlphaOnlyMemoryWord", "rhubarb", "stratified"):
            ra = pi.query(*self.a, word, kind=["tex", "memory", "review"])
            self.assertTrue(ra["chunks"], word)
            rb = pi.query(*self.b, word, kind=["tex", "memory", "review"])
            self.assertEqual(rb["chunks"], [], word)

    def test_every_id_returned_for_a_paper_belongs_to_that_paper(self):
        for ref, tag in ((self.a, "P:alpha:"), (self.b, "P:beta:")):
            for q in ("methods", "results", "irrigation", "regression yield", ""):
                for c in pi.query(*ref, q, budget=100000).get("chunks", []):
                    self.assertTrue(c["id"].startswith(tag), (c["id"], tag))

    def test_a_copied_index_file_is_refused(self):
        pi.refresh(*self.a)
        pi.refresh(*self.b)
        import shutil
        shutil.copy(pi.index_path(self.a[1]), pi.index_path(self.b[1]))
        with self.assertRaises(pi.IsolationError):
            pi.query(*self.b, "methods")
        pi.index_path(self.b[1]).unlink()  # the documented fix: delete, it rebuilds from B's own files
        self.assertTrue(pi.query(*self.b, "methods")["chunks"])

    def test_event_logs_are_per_paper_and_ids_match_their_paper(self):
        pi.query(*self.a, "methods")
        pi.query(*self.b, "methods")
        for (name, proj), tag in ((self.a, "P:alpha:"), (self.b, "P:beta:")):
            evs = events.read(config.paper_dir(proj), types={"retrieve"})
            self.assertEqual(len(evs), 1)
            self.assertTrue(all(i.startswith(tag) for e in evs for i in e["ids"]))
            self.assertTrue(all(e["paper"] == name for e in evs))

    def test_renamed_paper_rebuilds_instead_of_mixing_ids(self):
        pi.refresh(*self.a)
        common.register(self.a[1]["path"], "alpha2")
        name2, proj2 = common.resolve("alpha2")
        r = pi.refresh(name2, proj2)
        self.assertEqual(r["added"], r["total"])
        ids = [c["id"] for c in pi.query(name2, proj2, "methods")["chunks"]]
        self.assertTrue(ids and all(i.startswith("P:alpha2:") for i in ids))


def _refresh_worker(args):
    name, proj, home = args
    common.HOME = Path(home)
    for _ in range(3):
        pi.refresh(name, proj, force=True)
    return True


class Concurrency(IsolatedHome):
    def test_parallel_refreshes_leave_a_consistent_index(self):
        name, proj = paper(self)
        pi.refresh(name, proj)
        with multiprocessing.Pool(4) as pool:
            self.assertTrue(all(pool.map(_refresh_worker, [(name, proj, str(common.HOME))] * 4)))
        con = pi.open_index(name, proj)
        self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], 11)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0], 11)
        con.close()


if __name__ == "__main__":
    unittest.main()
