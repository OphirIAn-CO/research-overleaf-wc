"""The chat's brain: conversations per paper, and reading 'the text before Figure 1 in the methods' as exact lines."""
import subprocess
import unittest
from pathlib import Path

from tests.helpers import IsolatedHome, common
from tests.test_paper import paper
from tests.test_revise import ReviseBase, reply
from writing_core import chat, config, paper_index, revise

MAIN = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We study how soil moisture shapes crop yield across forty farms in three regions of the country.
\end{abstract}

\section{Introduction}
Crop yield depends on water and on soil. Farmers need guidance on irrigation \cite{smith2020}. We ask whether moisture predicts yield.

\section{Materials and Methods}
\subsection{Study area}
The study was conducted in three regions that differ in rainfall and elevation. Each region has a distinct climate and soil.

The second paragraph describes soils, slopes and the access roads that were used to reach every farm in the sample.

\begin{figure}
\centering
\includegraphics{map.png}
\caption{Map of the study area.}\label{fig:map}
\end{figure}

After the map we describe the sampling frame. Farms were listed by the regional office and sorted by size before sampling.

\subsection{Statistical analysis}
We fitted a mixed-effects regression of yield on moisture with farm as a random intercept.

\section{Results}
Yield rose with moisture in all three regions, as Table~\ref{tab:a} shows.
\begin{table}
\begin{tabular}{ll}
a & b \\
\end{tabular}
\caption{Means.}\label{tab:a}
\end{table}

\end{document}
"""


class Base(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self, main=MAIN)
        paper_index.refresh(self.name, self.proj)
        self.main = Path(self.proj["path"]) / "main.tex"

    def lines(self, ids):
        out = []
        for i in ids:
            cid, a, b = revise.split_range_id(i)
            c = paper_index.get_chunks(self.name, self.proj, [cid])[0]
            a, b = a or c["line_start"], b or c["line_end"]
            out.append("\n".join(self.main.read_text().split("\n")[a - 1:b]))
        return "\n".join(out)


class Conversations(Base):
    def test_threads_are_stored_inside_the_paper_and_git_never_sees_them(self):
        t = chat.new_thread(self.proj)
        chat.append(self.proj, t["id"], "user", "text", "improve the flow of my methods please")
        self.assertTrue((Path(self.proj["path"]) / ".overleaf-skill" / "chats" / f"{t['id']}.json").exists())
        status = subprocess.run(["git", "-C", self.proj["path"], "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")
        self.assertEqual(chat.list_threads(self.proj)[0]["title"], "improve the flow of my methods please")

    def test_messages_update_rename_delete_and_ids_are_validated(self):
        cid = chat.new_thread(self.proj)["id"]
        m = chat.append(self.proj, cid, "assistant", "plan", "ready", units=2)
        chat.update(self.proj, cid, m["id"], sent=True)
        self.assertEqual(chat.get_message(self.proj, cid, m["id"])["data"], {"units": 2, "sent": True})
        chat.rename(self.proj, cid, "  My   methods  ")
        self.assertEqual(chat.list_threads(self.proj)[0]["title"], "My methods")
        chat.delete(self.proj, cid)
        self.assertEqual(chat.list_threads(self.proj), [])
        for bad in ("../x", "c123", "cZZZZZZZZZZ", "", None):
            with self.assertRaises(ValueError):
                chat.get_thread(self.proj, bad)
        with self.assertRaises(ValueError):
            chat.append(self.proj, "c0123456789", "user", "text", "x")

    def test_each_paper_has_its_own_conversations(self):
        other_name, other = paper(self, name="beta", main=MAIN)
        a = chat.new_thread(self.proj)["id"]
        chat.append(self.proj, a, "user", "text", "secret plan for alpha only")
        self.assertEqual(chat.list_threads(other), [])
        self.assertIsNone(chat.get_thread(other, a))
        self.assertEqual(chat.ensure_thread(other) != a, True)

    def test_the_last_target_is_what_the_assistant_last_worked_on(self):
        cid = chat.new_thread(self.proj)["id"]
        self.assertIsNone(chat.last_target(chat.get_thread(self.proj, cid)))
        chat.append(self.proj, cid, "assistant", "plan", "x", chunk_ids=["a", "b"])
        chat.append(self.proj, cid, "user", "text", "shorter")
        self.assertEqual(chat.last_target(chat.get_thread(self.proj, cid)), ["a", "b"])


class Intent(unittest.TestCase):
    def test_kinds_of_message(self):
        for text, kind in [("10.1016/j.cageo.2020.104524", "refs"), ("please add https://doi.org/10.1038/s41586-020-2649-2 thanks", "refs"),
                           ("peer review my paper", "review"), ("Review the methods section", "review"), ("critique this draft", "review"),
                           ("improve the flow of the methods", "edit"), ("make it shorter", "edit"), ("help", "help"), ("How does this work?", "help"),
                           ("implement the reviewer comments in the discussion", "edit")]:
            self.assertEqual(chat.intent(text), kind, text)
        self.assertEqual(chat.find_dois("see 10.1016/j.x.2020.1, and 10.1038/abc."), ["10.1016/j.x.2020.1,"[:-1], "10.1038/abc"])

    def test_explicit_creation_verbs_are_new_text_requests(self):
        for text in ("create a new introduction paragraph", "develop the discussion from my analysis"):
            self.assertEqual(chat.intent(text), "draft", text)


class Understand(Base):
    def test_before_figure_1_in_the_methods_is_cut_at_the_figure(self):
        r = chat.understand(self.name, self.proj, "improve the flow of the text that is before figure 1 inside methods")
        self.assertEqual(r["status"], "ready", r)
        self.assertEqual(r["units"], 1)
        text = self.lines(r["chunk_ids"])
        self.assertIn("The study was conducted in three regions", text)
        self.assertIn("second paragraph describes soils", text)
        self.assertNotIn("\\begin{figure}", text)
        self.assertNotIn("After the map", text)
        self.assertNotIn("regression", text)               # the next subsection is not "before figure 1"
        self.assertIn("Before Figure 1", r["notes"][0])
        self.assertTrue(all("@" in i for i in r["chunk_ids"]))

    def test_after_figure_1_and_table_1(self):
        r = chat.understand(self.name, self.proj, "tighten what comes after the first figure in the methods")
        text = self.lines(r["chunk_ids"])
        self.assertIn("After the map we describe the sampling frame", text)
        self.assertNotIn("The study was conducted", text)
        self.assertNotIn("\\end{figure}", text)
        r = chat.understand(self.name, self.proj, "fix grammar before table 1 in the results")
        self.assertEqual(r["status"], "ready")             # the sentence that introduces the table sits before it
        text = self.lines(r["chunk_ids"])
        self.assertIn("Yield rose with moisture", text)
        self.assertNotIn("tabular", text)
        r = chat.understand(self.name, self.proj, "fix grammar after table 1 in the results")
        self.assertEqual(r["status"], "choose")            # nothing follows the table: asked, not guessed

    def test_a_missing_figure_says_so_and_still_works(self):
        r = chat.understand(self.name, self.proj, "improve the methods before figure 7")
        self.assertEqual(r["status"], "ready")
        self.assertIn("could not find Figure 7", r["notes"][0])

    def test_first_and_last_paragraph(self):
        r = chat.understand(self.name, self.proj, "improve the first paragraph of the methods")
        text = self.lines(r["chunk_ids"])
        self.assertIn("The study was conducted", text)
        self.assertNotIn("second paragraph", text)
        self.assertEqual(r["notes"], ["Only the first paragraph."])
        r = chat.understand(self.name, self.proj, "shorten the last paragraph of the methods")
        self.assertIn("We fitted a mixed-effects", self.lines(r["chunk_ids"]))
        r = chat.understand(self.name, self.proj, "improve the second paragraph in the methods")
        self.assertIn("second paragraph describes soils", self.lines(r["chunk_ids"]))

    def test_a_section_named_by_its_title(self):
        r = chat.understand(self.name, self.proj, "fix the grammar in the study area section")
        self.assertEqual(r["status"], "ready", r)
        self.assertIn("The study was conducted", self.lines(r["chunk_ids"]))
        self.assertNotIn("regression", self.lines(r["chunk_ids"]))

    def test_a_part_word_picks_the_whole_part_and_sub_parts_narrow_it(self):
        whole = chat.understand(self.name, self.proj, "improve the methods")
        self.assertIn("regression", self.lines(whole["chunk_ids"]))
        self.assertIn("The study was conducted", self.lines(whole["chunk_ids"]))
        narrow = chat.understand(self.name, self.proj, "improve the statistical analysis")
        self.assertIn("regression", self.lines(narrow["chunk_ids"]))
        self.assertNotIn("The study was conducted", self.lines(narrow["chunk_ids"]))

    def test_with_no_place_it_asks_with_the_authors_own_section_names(self):
        r = chat.understand(self.name, self.proj, "make it better")
        self.assertEqual(r["status"], "choose")
        self.assertEqual([s["label"] for s in r["sections"]], ["Abstract", "Introduction", "Materials and Methods", "Results"])
        self.assertTrue(all(o["preview"] for o in r["options"]))
        self.assertTrue(all(len(s["chunk_ids"]) >= 1 for s in r["sections"]))

    def test_a_follow_up_without_a_place_reuses_the_last_one(self):
        first = chat.understand(self.name, self.proj, "improve the first paragraph of the methods")
        again = chat.understand(self.name, self.proj, "make it shorter", last_ids=first["chunk_ids"])
        self.assertEqual((again["status"], again["chunk_ids"]), ("ready", first["chunk_ids"]))
        self.assertIn("Same place", again["notes"][0])
        para = chat.understand(self.name, self.proj, "just the second paragraph", last_ids=first["chunk_ids"])
        self.assertEqual(para["status"], "ready")

    def test_ticked_pieces_are_taken_as_they_are_and_stale_ids_are_refused(self):
        r = chat.understand(self.name, self.proj, "improve the methods")
        one = chat.understand(self.name, self.proj, "anything", chunk_ids=r["chunk_ids"][:1])
        self.assertEqual((one["status"], one["chunk_ids"]), ("ready", r["chunk_ids"][:1]))
        gone = chat.understand(self.name, self.proj, "anything", chunk_ids=["P:alpha:main.tex#nope#k01"])
        self.assertEqual(gone["status"], "empty")

    def test_too_many_passages_asks_which_ones_and_ticks_the_first_batch(self):
        words = " ".join(f"word{i}" for i in range(700))
        subs = "\n".join(f"\\subsection{{Part {k}}}\n{words}.\n" for k in range(6))
        self.main.write_text("\\documentclass{article}\\begin{document}\n\\section{Methods}\n" + subs + "\\end{document}\n")
        paper_index.refresh(self.name, self.proj)
        tex = [c for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["kind"] == "tex"]
        whole = chat.understand(self.name, self.proj, "improve the methods", max_units=50)
        self.assertEqual(whole["status"], "ready")
        self.assertGreater(whole["units"], 2)
        r = chat.understand(self.name, self.proj, "improve the methods", max_units=2)
        self.assertEqual(r["status"], "choose")
        self.assertIn("separate passages", r["why"])
        self.assertEqual(len(chat._units_of(tex, r["preselect"])), 2)
        self.assertGreater(len(r["in_part"]), len(r["preselect"]))
        self.assertFalse(chat.count(self.name, self.proj, [c["id"] for c in tex])["ok"] and False)

    def test_count_uses_the_same_grouping_as_the_real_run(self):
        r = chat.understand(self.name, self.proj, "improve the methods")
        c = chat.count(self.name, self.proj, r["chunk_ids"])
        self.assertEqual((c["units"], c["ok"]), (r["units"], True))
        self.assertEqual(chat.count(self.name, self.proj, [])["ok"], False)

    def test_an_empty_paper_says_what_to_do(self):
        n, p = paper(self, name="empty", main="\\documentclass{article}\\begin{document}\\end{document}\n", methods="")
        self.assertEqual(chat.understand(n, p, "improve the methods")["status"], "empty")


class Wording(unittest.TestCase):
    def test_a_place_alone_gets_a_general_polish_but_a_real_instruction_is_kept(self):
        self.assertIn("Improve the clarity", chat.instruction_for("the text before figure 1 in methods"))
        self.assertIn("Improve the clarity", chat.instruction_for("methods"))
        self.assertEqual(chat.instruction_for("tighten the first paragraph of the methods"), "tighten the first paragraph of the methods")
        self.assertEqual(chat.instruction_for("fix grammar errors"), "fix grammar errors")


class EndToEnd(ReviseBase):
    """The cut-down passage really is all that is sent, and all that changes on disk."""

    def setUp(self):
        super().setUp()
        self.live()
        from tests import test_v2_chat as me
        p = Path(self.proj["path"]) / "main.tex"
        p.write_text(me.MAIN)
        paper_index.refresh(self.name, self.proj)

    def test_plan_run_and_apply_touch_only_the_lines_before_the_figure(self):
        r = chat.understand(self.name, self.proj, "improve the flow of the text before figure 1 in methods")
        self.assertEqual(r["status"], "ready", r)
        pl = chat.plan(self.name, self.proj, chat.instruction_for("improve the flow of the text before figure 1 in methods"), r["chunk_ids"])
        self.assertEqual(pl["units"], 1)
        self.assertEqual(self.srv.requests, [])                                   # planning sends nothing
        self.assertGreater(pl["est_tokens_in"], 50)
        self.assertEqual(pl["passages"][0]["section"].split(" > ")[-1], "Study area")
        self.srv.script = [reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."))]
        out = revise.run(self.name, self.proj, "improve the flow", chunk_ids=r["chunk_ids"], dry_run=False)
        prop = out["proposal"]
        sent = str(self.srv.requests[0]["body"])
        self.assertNotIn("After the map", sent)
        self.assertNotIn("includegraphics", sent)
        res = revise.apply(self.name, self.proj, prop["id"], accept="all", cleaner=lambda t: t)
        written = (Path(self.proj["path"]) / "main.tex").read_text()
        self.assertIn("Each region has its own climate and soil.", written)
        self.assertIn("\\begin{figure}", written)                                  # the figure and everything after stays as it was
        self.assertIn("After the map we describe the sampling frame.", written)
        self.assertEqual(res["status"], "applied")


if __name__ == "__main__":
    unittest.main()
