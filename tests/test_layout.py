"""Changing the form of the paper on request: move a table to the appendix and refer to it, restyle a table, adjust a figure. The model answers with exact edits; every one is checked on this
computer (balanced, labels kept, references resolve, citations and the words of tables and figures unchanged, nothing unsafe, still compiles) and the suggestion is applied whole or not at all."""
import json
import shutil
import unittest
from pathlib import Path

from tests.test_book import needs_pdf
from tests.test_revise import ReviseBase
from tests.test_v2_chat_api import ChatBase
from writing_core import chat, config, intent, latex_mask, layout, openai_client, paper_index, revise

TABLE = r"""\begin{table}[h]
\centering
\caption{Means of yield by region.}\label{tab:means}
\begin{tabular}{lc}
Region & Yield \\
\hline
North & 4.1 \\
South & 3.8 \\
\end{tabular}
\end{table}"""
FIGURE = r"""\begin{figure}
\centering
\fbox{map of the study area}
\caption{Map of the study area.}\label{fig:map}
\end{figure}"""
MAIN = ("\\documentclass{article}\n\\usepackage{booktabs}\n\\begin{document}\n\\section{Methods}\nWe measured yield on forty farms \\cite{smith2020}, as Table~\\ref{tab:means} shows.\n\n"
        + TABLE + "\n\n\\section{Results}\nFigure~\\ref{fig:map} shows the study area.\n\n" + FIGURE + "\n\n\\end{document}\n")
APPENDIX = "\\appendix\n\\section{Supplementary tables}\\label{app:tables}\n" + TABLE + "\n\n\\end{document}"


def move_to_appendix():
    return {"approach": "Table 1 now sits in a new appendix, and the sentence that mentions it points there.", "questions": [], "edits": [
        {"file": "main.tex", "find": TABLE + "\n\n", "replace": "", "reason": "the table leaves the methods"},
        {"file": "main.tex", "find": "\\end{document}", "replace": APPENDIX, "reason": "a new appendix at the end, with the table and its label"},
        {"file": "main.tex", "find": "as Table~\\ref{tab:means} shows.", "replace": "as Table~\\ref{tab:means} in Appendix~\\ref{app:tables} shows.", "reason": "the text still points to the table"}]}


class Base(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()
        self.main = Path(self.proj["path"]) / "main.tex"
        self.main.write_text(MAIN)
        for f in ("sections/methods.tex",):
            (Path(self.proj["path"]) / f).write_text("")
        paper_index.refresh(self.name, self.proj, trigger="test")

    def texts(self):
        return {rel: layout.read(self.proj, rel) for rel in layout.tex_files(self.name, self.proj)}

    def resolve(self, text, ids=None):
        return layout.resolve(self.name, self.proj, text, ids)

    def ask(self, text, answer, **kw):
        self.srv.script = [answer] if isinstance(answer, dict) else list(answer)
        res = self.resolve(text)
        self.assertFalse(res["ask"], res["ask"])
        pl = layout.plan(self.name, self.proj, text, res)
        return layout.run(self.name, self.proj, text, res, confirm=pl["payload_hash"], compile_check_on=kw.get("compile", False))


class Finding(Base):
    def test_the_figures_and_tables_are_numbered_like_latex_numbers_them(self):
        fl = layout.scan_floats(self.texts())
        self.assertEqual([(f["kind"], f["n"], f["label"], f["file"]) for f in fl], [("table", 1, "tab:means", "main.tex"), ("figure", 1, "fig:map", "main.tex")])
        self.assertEqual(fl[0]["caption"], "Means of yield by region.")
        self.assertEqual(layout.refs_in(self.texts(), "tab:means"), [{"file": "main.tex", "line": 5}])

    def test_what_is_meant_by_a_number_an_ordinal_a_label_or_a_caption(self):
        for text, want in (("move Table 1 to the appendix", ["table 1"]), ("improve Fig. 1", ["figure 1"]), ("resize the last figure", ["figure 1"]), ("restyle the first table", ["table 1"]),
                           ("fix tab:means", ["table 1"]), ("improve the table of yield by region", ["table 1"]), ("make all the figures smaller", ["figure 1"]), ("move this table to the appendix", ["table 1"]),
                           ("move table 1 and figure 1 to the end", ["table 1", "figure 1"])):
            got = [f"{f['kind']} {f['n']}" for f in self.resolve(text)["targets"]]
            self.assertEqual(got, want, text)

    def test_with_no_clue_and_several_it_asks_which_one(self):
        res = self.resolve("improve the visualization")
        self.assertEqual(res["targets"], [])
        self.assertIn("Which figure or table do you mean?", res["ask"])
        self.assertIn("Table 1: Means of yield by region.", res["ask"])
        self.assertIn("Figure 1: Map of the study area.", res["ask"])

    def test_the_page_itself_needs_no_figure(self):
        res = self.resolve("make the margins narrower and use a smaller font size")
        self.assertEqual((res["targets"], res["document"], res["ask"]), ([], True, None))

    def test_a_request_about_the_documents_own_latex_is_not_taken_for_a_question_about_a_figure(self):
        res = self.resolve("in the latex before the end document include the bibliography with the .bib file")
        self.assertEqual((res["targets"], res["document"], res["ask"]), ([], True, None), "no figure or table is named, so the model gets the document, not a question")

    def test_with_no_figure_or_table_in_the_paper_a_request_about_the_bibliography_still_goes_ahead(self):
        (Path(self.proj["path"]) / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nWe measured yield \\cite{smith2020}.\n\\end{document}\n")
        paper_index.refresh(self.name, self.proj, trigger="test")
        res = self.resolve("include the bibliography with my .bib file")
        self.assertEqual((res["targets"], res["document"], res["ask"]), ([], True, None))
        res = self.resolve("improve the visualization of the table")
        self.assertIn("I cannot see any figure or table", res["ask"], "a request that does name a table still gets this answer")

    def test_the_model_is_told_which_bibliography_files_and_system_the_paper_has(self):
        res = self.resolve("include the bibliography with the .bib file")
        pl = layout.plan(self.name, self.proj, "include the bibliography with the .bib file", res)
        user = json.dumps(pl["payload"])
        self.assertIn("<paper_setup>", user)
        self.assertIn("references.bib", user)
        self.assertIn("bibliography system: BibTeX", user)
        self.assertIn("<end_of_document", user)

    def test_pasted_latex_of_a_float_is_found_by_its_label(self):
        res = self.resolve("Make this one look better:\n\n" + TABLE.replace("4.1", "4.2"))
        self.assertEqual([f["label"] for f in res["targets"]], ["tab:means"])

    def test_the_pieces_the_author_ticked_are_found(self):
        ch = [c for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["kind"] == "tex" and "Results" in c["section_path"]]
        res = self.resolve("improve it", [ch[0]["id"]])
        self.assertTrue(any(f["kind"] == "figure" for f in res["targets"]))

    def test_what_the_model_is_given_is_verbatim_with_the_places_that_mention_it(self):
        res = self.resolve("move Table 1 to the appendix")
        user, shown = layout.build(res["texts"], res["floats"], res["targets"], False, "move Table 1 to the appendix")
        self.assertIn(TABLE, user, "copied from the file, so a find taken from it matches")
        self.assertIn('<mention of="tab:means"', user)
        self.assertIn("as Table~\\ref{tab:means} shows.", user)
        self.assertIn("<end_of_document", user)
        self.assertIn("\\usepackage{booktabs}", user)
        self.assertIn("existing_labels>fig:map, tab:means", user)
        self.assertEqual(shown[0]["lines"], [7, 16])


class Bibliography(Base):
    """Why the references are in the .bib and still do not print: facts about the LaTeX found here and given to the model, and a warning when the references are saved."""

    def problems(self, preamble, tail):
        self.main.write_text("\\documentclass{article}\n" + preamble + "\n\\begin{document}\nWe measured yield \\cite{smith2020}.\n" + tail + "\n\\end{document}\n")
        paper_index.refresh(self.name, self.proj, trigger="test")
        return layout.bibliography_problems(self.proj)

    def test_printbibliography_without_biblatex_is_the_undefined_control_sequence(self):
        why = self.problems("", "\\printbibliography")
        self.assertEqual(len(why), 1)
        self.assertIn("biblatex is not loaded", why[0])
        self.assertIn("Undefined control sequence", why[0])

    def test_citations_and_no_bibliography_command_print_as_question_marks(self):
        why = self.problems("", "")
        self.assertIn("no bibliography command", why[0])

    def test_a_working_bibliography_has_no_problem(self):
        self.assertEqual(self.problems("", "\\bibliographystyle{plain}\n\\bibliography{references}"), [])
        self.assertEqual(self.problems("\\usepackage[backend=biber]{biblatex}\n\\addbibresource{references.bib}", "\\printbibliography"), [])

    def test_the_two_systems_mixed_and_a_missing_resource_are_named(self):
        self.assertIn("together with biblatex", self.problems("\\usepackage{biblatex}\n\\addbibresource{references.bib}", "\\bibliography{references}")[0])
        self.assertIn("no \\addbibresource", self.problems("\\usepackage{biblatex}", "\\printbibliography")[0])

    def test_the_model_is_given_the_problem_and_the_last_compile_of_the_latex_pane(self):
        from writing_core import latexbuild
        self.problems("", "\\printbibliography")
        keep = latexbuild.last
        self.addCleanup(setattr, latexbuild, "last", keep)
        latexbuild.last = lambda proj: {"errors": [{"file": "main.tex", "line": 6, "message": "Undefined control sequence.", "context": "\\printbibliography"}],
                                        "undefined": {"citations": ["Anon1998Reliabili"], "references": []}, "warnings": []}
        res = self.resolve("the bibliography is not rendering, fix the latex")
        self.assertEqual((res["targets"], res["document"], res["ask"]), ([], True, None))
        payload = layout.plan(self.name, self.proj, "fix it", res)["payload"]
        user = payload["input"] + payload["instructions"]
        self.assertIn("problem: \\printbibliography is used but biblatex is not loaded", user)
        self.assertIn("Undefined control sequence", user)
        self.assertIn("Anon1998Reliabili", user)
        self.assertIn("keep the system whose package the preamble already loads", user)

    def test_the_users_case_printbibliography_without_biblatex_is_repaired_and_the_references_print(self):
        """References were in the .bib, \\printbibliography was in the document, biblatex was not: "Undefined control sequence" and every citation showed as missing. The model repairs the LaTeX; the
        repaired paper is compiled here, and it compiles with the bibliography printed."""
        from writing_core import latexbuild
        if not latexbuild.available()[0]:
            self.skipTest("LaTeX is not installed here")
        (Path(self.proj["path"]) / self.proj["bib"]).write_text("@article{smith2020,\n author={Smith, Jane},\n title={Soil moisture and yield},\n year={2020},\n journal={Soil Journal}\n}\n")
        self.main.write_text("\\documentclass{article}\n\\begin{document}\n\\section{Methods}\nWe measured yield on forty farms \\cite{smith2020}.\n\n\\printbibliography\n\\end{document}\n")
        paper_index.refresh(self.name, self.proj, trigger="test")
        broken = latexbuild.compile(self.proj)
        self.assertTrue(broken["errors"], "the paper as it is reports the error the author saw")
        self.assertIn("Undefined control sequence", broken["errors"][0]["message"])
        answer = {"approach": "The paper uses BibTeX, so I replaced \\printbibliography with the BibTeX commands for references.bib.", "questions": [], "edits": [
            {"file": "main.tex", "find": "\\printbibliography", "replace": "\\bibliographystyle{plain}\n\\bibliography{references}", "reason": "\\printbibliography needs biblatex, which this paper does not load"}]}
        out = self.ask("the bibliography is not rendering, fix the latex", answer, compile=True)
        self.assertEqual(out["status"], "proposed", out["proposal"].get("error"))
        self.assertTrue(any(v["name"] == "compiles" and v["ok"] for v in out["proposal"]["validators"]), out["proposal"]["validators"])
        layout.apply(self.name, self.proj, out["proposal"]["id"])
        fixed = latexbuild.compile(self.proj)
        self.assertEqual((fixed["errors"], fixed["undefined"]["citations"]), ([], []), "no error and no citation without an entry")

    def test_saving_references_whose_document_cannot_print_them_says_so(self):
        self.problems("", "")
        add = {"key": "Smith2020Soil", "doi": "", "title": "Soil", "bibtex": "@misc{Smith2020Soil,\n title={Soil}\n}", "doc": "d1"}
        self.main.write_text(self.main.read_text().replace("\\cite{smith2020}", "\\cite{Smith2020Soil}"))
        out = revise._add_bib_entries(self.proj, {"bib_adds": [add]}, ["main.tex"])
        self.assertEqual([a["key"] for a in out["added"]], ["Smith2020Soil"])
        self.assertTrue(any("will not print them yet" in w and "no bibliography command" in w for w in out["warnings"]), out["warnings"])


class Edits(unittest.TestCase):
    T = {"a.tex": "one\n  two\n  three\nfour\none\n"}

    def test_a_find_must_stand_in_exactly_one_place(self):
        new, problems = layout.apply_edits(self.T, [{"file": "a.tex", "find": "four", "replace": "4"}])
        self.assertEqual((problems, new["a.tex"]), ([], "one\n  two\n  three\n4\none\n"))
        _, problems = layout.apply_edits(self.T, [{"file": "a.tex", "find": "one", "replace": "1"}])
        self.assertIn("more than one place", problems[0])
        _, problems = layout.apply_edits(self.T, [{"file": "a.tex", "find": "zero", "replace": "0"}])
        self.assertIn("not in a.tex", problems[0])
        _, problems = layout.apply_edits(self.T, [{"file": "b.tex", "find": "one", "replace": "1"}])
        self.assertIn("a file I did not send", problems[0])

    def test_indentation_that_was_copied_a_little_differently_still_finds_its_place(self):
        new, problems = layout.apply_edits(self.T, [{"file": "a.tex", "find": "two\nthree", "replace": "2\n3"}])
        self.assertEqual((problems, new["a.tex"].split("\n")[1:3]), ([], ["2", "3"]))

    def test_edits_that_overlap_are_refused_and_nothing_is_applied(self):
        new, problems = layout.apply_edits(self.T, [{"file": "a.tex", "find": "two\n  three", "replace": "x"}, {"file": "a.tex", "find": "three\nfour", "replace": "y"}])
        self.assertTrue(problems)
        self.assertEqual(new, self.T)


class Checks(Base):
    def names(self, after, request="move the table", edits=()):
        return {r["name"]: r for r in layout.check(self.texts(), after, request, edits) if not r["ok"]}

    def change(self, old, new):
        t = self.texts()
        t["main.tex"] = t["main.tex"].replace(old, new)
        return t

    def test_a_clean_move_passes_every_check(self):
        after, problems = layout.apply_edits(self.texts(), move_to_appendix()["edits"])
        self.assertEqual(problems, [])
        self.assertEqual(self.names(after), {})

    def test_losing_a_label_or_leaving_a_reference_dangling_is_an_error(self):
        bad = self.names(self.change("\\label{tab:means}", ""))
        self.assertIn("labels_kept", bad)
        self.assertIn("references_resolve", bad)
        self.assertIn("tab:means", bad["references_resolve"]["detail"])
        self.assertIn("labels_unique", self.names(self.change("\\label{fig:map}", "\\label{tab:means}")))

    def test_unbalanced_environments_and_braces_are_an_error(self):
        self.assertIn("braces_and_environments_balanced", self.names(self.change("\\end{tabular}\n", "")))
        self.assertIn("braces_and_environments_balanced", self.names(self.change("\\caption{Means of yield by region.}", "\\caption{Means of yield by region.")))

    def test_a_citation_or_a_picture_file_may_not_change(self):
        self.assertIn("citations_unchanged", self.names(self.change("\\cite{smith2020}", "\\cite{lee2021}")))
        self.assertIn("pictures_unchanged", self.names(self.change("\\fbox{map of the study area}", "\\includegraphics{map.png}\\fbox{map of the study area}")))

    def test_the_words_and_numbers_of_a_table_may_not_change_unless_the_request_says_so(self):
        bad = self.names(self.change("North & 4.1", "North & 4.10"), "restyle the table")
        self.assertIn("numbers_kept", bad)
        self.assertIn("4.1", bad["numbers_kept"]["detail"])
        self.assertIn("table_and_figure_text_kept", self.names(self.change("South", "Sud"), "make the table nicer"))
        self.assertNotIn("numbers_kept", self.names(self.change("North & 4.1", "North & 4.10"), "round the numbers to two decimals"))
        self.assertNotIn("table_and_figure_text_kept", self.names(self.change("Yield \\\\", "Yield (t/ha) \\\\"), "add units to the table header"))

    def test_formatting_commands_of_a_table_may_change_freely(self):
        after = self.texts()
        after["main.tex"] = (after["main.tex"].replace("\\hline", "\\toprule").replace("\\end{tabular}", "\\bottomrule\n\\end{tabular}")
                             .replace("\\centering\n\\caption{Means", "\\centering\\small\n\\caption{Means"))
        self.assertEqual(self.names(after, "improve the visualization of the table"), {})

    def test_words_taken_out_of_the_text_are_an_error_and_a_few_added_are_fine(self):
        self.assertIn("text_kept", self.names(self.change("on forty farms", "on farms"), "move the table"))
        self.assertNotIn("text_added_is_small", self.names(self.change("as Table~\\ref{tab:means} shows.", "as Table~\\ref{tab:means} shows, in the appendix."), "move the table"))

    def test_unsafe_commands_are_an_error_and_new_packages_are_named(self):
        self.assertIn("no_unsafe_commands", self.names(self.change("\\centering\n\\caption{Means", "\\centering\\immediate\\write18{ls}\n\\caption{Means")))
        self.assertIn("no_unsafe_commands", self.names(self.change("\\begin{document}", "\\input{/etc/passwd}\n\\begin{document}")))
        pk = self.names(self.change("\\usepackage{booktabs}", "\\usepackage{booktabs}\n\\usepackage{tabularx}"))
        self.assertIn("packages", pk)
        self.assertEqual(pk["packages"]["severity"], "warn")
        self.assertIn("tabularx", pk["packages"]["detail"])


@needs_pdf
class Asking(Base):
    def test_a_move_to_the_appendix_is_checked_stored_and_applied_whole(self):
        r = self.ask("move Table 1 to the appendix and add the respective reference in tex", move_to_appendix())
        self.assertEqual(r["status"], "proposed", r["proposal"].get("error"))
        prop = r["proposal"]
        self.assertEqual((prop["kind"], len(prop["edits"]), prop["units"]), ("layout", 3, []))
        self.assertEqual([e["reason"] for e in prop["edits"]][2], "the text still points to the table")
        self.assertEqual(prop["edits"][0]["line_start"], 7)
        sent = self.srv.requests[-1]["body"]
        from writing_core import openai_client
        self.assertIn(TABLE, openai_client.user_text(sent))
        before = self.main.read_text()
        self.assertEqual(self.main.read_text(), before, "making the suggestion changes nothing")
        out = layout.apply(self.name, self.proj, prop["id"])
        self.assertEqual((out["status"], [w["file"] for w in out["written"]]), ("applied", ["main.tex"]))
        now = self.main.read_text()
        self.assertTrue(now.index("\\appendix") < now.index("\\label{tab:means}") < now.index("\\end{document}"))
        self.assertIn("in Appendix~\\ref{app:tables} shows.", now)
        self.assertEqual(now.count("\\label{tab:means}"), 1)
        self.assertEqual(revise.get_proposal(self.proj, prop["id"])["status"], "applied")
        with self.assertRaises(revise.ReviseError):
            layout.apply(self.name, self.proj, prop["id"])

    def test_the_same_apply_is_reached_from_revise_for_the_terminal_and_claude_code(self):
        prop = self.ask("move Table 1 to the appendix", move_to_appendix())["proposal"]
        self.assertEqual(revise.apply(self.name, self.proj, prop["id"], source="cli")["status"], "applied")

    def test_a_file_that_changed_since_stops_everything(self):
        prop = self.ask("move Table 1 to the appendix", move_to_appendix())["proposal"]
        self.main.write_text(self.main.read_text().replace("forty farms", "fifty farms"))
        with self.assertRaises(revise.ReviseError) as cm:
            layout.apply(self.name, self.proj, prop["id"])
        self.assertIn("changed since", str(cm.exception))
        self.assertIn("fifty farms", self.main.read_text())
        self.assertNotIn("\\appendix", self.main.read_text())

    def test_an_answer_that_loses_the_table_is_sent_back_once_with_the_reason(self):
        lost = {"approach": "Removed.", "questions": [], "edits": [{"file": "main.tex", "find": TABLE + "\n\n", "replace": "", "reason": "gone"}]}
        r = self.ask("move Table 1 to the appendix", [lost, move_to_appendix()])
        self.assertEqual(r["status"], "proposed", r["proposal"].get("error"))
        self.assertEqual(r["proposal"].get("retries"), 1)
        from writing_core import openai_client
        again = openai_client.user_text(self.srv.requests[-1]["body"])
        self.assertIn("previous_attempt_rejected", again)
        self.assertIn("labels_kept", again)

    def test_two_bad_answers_in_a_row_change_nothing_and_say_why(self):
        lost = {"approach": "Removed.", "questions": [], "edits": [{"file": "main.tex", "find": TABLE + "\n\n", "replace": "", "reason": "gone"}]}
        r = self.ask("move Table 1 to the appendix", [lost, lost])
        self.assertEqual(r["status"], "failed")
        self.assertIn("did not use it", r["proposal"]["error_plain"])
        self.assertIn("\\label{tab:means}", self.main.read_text())

    def test_a_find_that_is_not_in_the_file_is_not_applied_to_a_guessed_place(self):
        guess = {"approach": "x", "questions": [], "edits": [{"file": "main.tex", "find": "\\begin{table}[ht]", "replace": "\\begin{table}[H]", "reason": "x"}]}
        r = self.ask("fix the table position", [guess, guess])
        self.assertEqual(r["status"], "failed")
        self.assertIn("not in main.tex", r["proposal"]["error"])

    def test_the_model_puts_the_bibliography_in_the_document_and_every_check_passes(self):
        text = "in the latex before the end document include the bibliography with the .bib file"
        answer = {"approach": "The bibliography now prints at the end, from references.bib.", "questions": [], "edits": [
            {"file": "main.tex", "find": "\\end{document}", "replace": "\\bibliographystyle{plain}\n\\bibliography{references}\n\\end{document}", "reason": "print the bibliography before the end of the document"}]}
        out = self.ask(text, answer)
        self.assertEqual(out["status"], "proposed", out["proposal"].get("error"))
        self.assertFalse(latex_mask.errors(out["proposal"]["validators"]), out["proposal"]["validators"])
        sent = json.dumps(self.srv.requests[-1]["body"])
        self.assertIn("references.bib", sent)

    def test_a_question_and_no_edit_is_passed_on(self):
        r = self.ask("improve the graph", {"approach": "I cannot see the data.", "questions": ["The graph is a PNG: what should change in the picture itself?"], "edits": []})
        self.assertEqual(r["status"], "no_changes")
        self.assertIn("PNG", r["proposal"]["questions"][0])

    def test_a_change_to_a_table_that_keeps_its_content_is_accepted(self):
        restyled = TABLE.replace("\\centering", "\\centering\\small").replace("\\hline", "\\toprule").replace("\\end{tabular}", "\\bottomrule\n\\end{tabular}")
        r = self.ask("improve the visualization of Table 1", {"approach": "Booktabs rules and a smaller font.", "questions": [], "edits": [{"file": "main.tex", "find": TABLE, "replace": restyled, "reason": "booktabs"}]})
        self.assertEqual(r["status"], "proposed", r["proposal"].get("error"))
        layout.apply(self.name, self.proj, r["proposal"]["id"])
        self.assertIn("\\toprule", self.main.read_text())

    @unittest.skipUnless(shutil.which("pdflatex") and shutil.which("latexmk"), "needs LaTeX")
    def test_a_change_that_stops_the_paper_compiling_is_refused_and_one_that_compiles_is_kept(self):
        broken = {"approach": "x", "questions": [], "edits": [{"file": "main.tex", "find": "\\centering\n\\fbox", "replace": "\\centering\n\\undefinedlayoutcommand\\fbox", "reason": "x"}]}
        r = self.ask("make the figure look better", [broken, broken], compile=True)
        self.assertEqual(r["status"], "failed")
        self.assertIn("no longer compiles", r["proposal"]["error"])
        good = self.ask("move Table 1 to the appendix", move_to_appendix(), compile=True)
        self.assertEqual(good["status"], "proposed", good["proposal"].get("error"))
        self.assertTrue(any(v["name"] == "compiles" and v["ok"] for v in good["proposal"]["validators"]))


@needs_pdf
class InTheChat(ChatBase):
    def setUp(self):
        super().setUp()
        self.main.write_text(MAIN)
        paper_index.refresh(self.name, self.proj, trigger="test")

    def test_a_request_to_move_a_table_becomes_a_suggestion_the_author_applies(self):
        self.oa.script = [move_to_appendix()]
        r = self.send("move this table to appendix and add the the respective reference in tex")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "layout_plan"], r["messages"])
        plan = r["messages"][1]["data"]["plan"]
        self.assertEqual([(t["kind"], t["n"]) for t in plan["targets"]], [("table", 1)])
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        res = self.last("result")
        self.assertTrue(res["data"]["layout"])
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={res['data']['proposal']}")
        self.assertEqual((st, prop["kind"], len(prop["edits"])), (200, "layout", 3))
        st, out = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": prop["id"], "chat": self.chat_thread()["id"]})
        self.assertEqual((st, out["ok"], out["files"]), (200, True, ["main.tex"]), out)
        self.assertIn("\\appendix", self.main.read_text())
        self.assertIn("Saved in main.tex.", self.last("note")["text"])
        self.assertNotIn("text-cleaning", self.last("note")["text"], "a layout change is LaTeX, not prose")

    def test_the_exact_request_that_was_refused_now_reaches_the_model_and_the_bibliography_is_written(self):
        """The reading was right ("add the LaTeX commands to display the bibliography from your .bib file before \\end{document}") and the answer was "I cannot see any figure or table"."""
        self.oa.script = [{"approach": "The bibliography now prints at the end, from references.bib.", "questions": [], "edits": [
            {"file": "main.tex", "find": "\\end{document}", "replace": "\\bibliographystyle{plain}\n\\bibliography{references}\n\\end{document}", "reason": "print the bibliography"}]}]
        r = self.send("in the latex before the end document include the bibliography with the .bib file")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "layout_plan"], r["messages"])
        self.assertEqual(r["messages"][1]["data"]["plan"]["targets"], [], "it is about the document, not a figure or a table")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        res = self.last("result")
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={res['data']['proposal']}")
        self.assertEqual((st, prop["status"], len(prop["edits"])), (200, "proposed", 1), prop.get("error"))
        st, out = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": prop["id"], "chat": self.chat_thread()["id"]})
        self.assertEqual((st, out["ok"]), (200, True), out)
        text = self.main.read_text()
        self.assertIn("\\bibliography{references}\n\\end{document}", text)
        self.assertEqual(text.count("\\end{document}"), 1)

    def test_not_now_sets_the_suggestion_aside_and_writes_nothing(self):
        self.oa.script = [move_to_appendix()]
        r = self.send("move Table 1 to the appendix")
        self.wait_job(r["job"])
        pid = self.last("result")["data"]["proposal"]
        before = self.main.read_text()
        st, out = self.c.post("/api/proposal/record", {"paper": "alpha", "id": pid, "accepted": [], "rejected": "all", "chat": self.chat_thread()["id"]})
        self.assertEqual(st, 200, out)
        self.assertEqual(revise.get_proposal(self.proj, pid)["status"], "rejected")
        self.assertEqual(self.main.read_text(), before)
        self.assertEqual(self.c.post("/api/proposal/approve", {"paper": "alpha", "id": pid})[0], 409, "a suggestion that was set aside cannot be applied")

    def test_a_layout_change_has_no_text_to_edit_by_hand(self):
        self.oa.script = [move_to_appendix()]
        r = self.send("move Table 1 to the appendix")
        self.wait_job(r["job"])
        pid = self.last("result")["data"]["proposal"]
        self.assertEqual(self.c.post("/api/proposal/approve", {"paper": "alpha", "id": pid, "texts": {"u1": "x"}})[0], 400)

    def test_the_words_alone_find_a_layout_request_when_no_model_reads_it(self):
        self.assertEqual(chat.intent("improve the visualization of the table"), "layout")
        self.oa.script = [{"approach": "x", "questions": ["Which way?"], "edits": []}]
        r = self.send("adjust the position of the image and the letter which has their respective names")
        self.assertEqual(r["messages"][1]["kind"], "layout_plan")
        self.wait_job(r["job"])

    def test_with_no_clue_which_figure_it_asks(self):
        r = self.send("improve the visualization")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "text"])
        self.assertIn("Which figure or table do you mean?", r["messages"][1]["text"])
        self.assertEqual(self.oa.requests, [])

    def test_a_layout_request_waits_for_a_yes_when_the_paper_is_set_to_ask(self):
        config.set_paper_setting(self.proj, "confirm_before_send", True)
        r = self.send("move Table 1 to the appendix")
        plan = r["messages"][1]
        self.assertEqual((plan["kind"], plan["data"]["state"], r["job"]), ("layout_plan", "waiting", None))
        self.assertEqual(self.oa.requests, [])
        self.oa.script = [move_to_appendix()]
        st, run = self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})
        self.assertEqual(st, 200, run)
        self.assertEqual(self.wait_job(run["job"])["status"], "done")

    def test_a_reading_that_says_layout_is_always_shown_first(self):
        config.set_paper_setting(self.proj, "understand", "unsure")
        self.oa.script = [{"intent": "layout", "confidence": 0.99, "summary": "You want Table 1 moved to the appendix.", "target": "Table 1", "uses": {"library": False, "online": False, "outline": False},
                           "question": "", "alternatives": []}]
        r = self.send("put the first table at the back")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "understanding"], r["messages"])
        self.assertEqual(r["job"], None)
        self.oa.script = [move_to_appendix()]
        st, res = self.c.post("/api/chat/understood", {"paper": "alpha", "message": r["messages"][1]["id"], "action": "go", "intent": "layout"})
        self.assertEqual(st, 200, res)
        self.assertEqual(res["messages"][1]["kind"], "layout_plan")
        self.wait_job(res["job"])


class Intent(unittest.TestCase):
    def test_layout_is_a_kind_of_request_the_reader_knows(self):
        self.assertIn("layout", intent.INTENTS)
        self.assertIn("layout", intent.ALL)
        self.assertIn("move, restyle or resize a table", intent.SYSTEM)

    def test_the_words_do_not_take_prose_requests_for_layout(self):
        for t in ("improve the flow of the text before Figure 1 in methods", "tighten the paragraph about Table 1", "improve the caption wording of figure 2", "fix the grammar in the introduction",
                  "explain the results in Table 1 in the discussion", "find references about tables"):
            self.assertNotEqual(chat.intent(t), "layout", t)
        for t in ("move this table to appendix and add the the respective reference in tex", "improve the visualization of the table", "improve the graph in the following way: larger fonts",
                  "adjust the position of the image and the letter which has their respective names", "put Table 2 in the appendix", "center the table and resize it"):
            self.assertEqual(chat.intent(t), "layout", t)


if __name__ == "__main__":
    unittest.main()
