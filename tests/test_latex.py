"""The LaTeX workspace: the paper's source files and the PDF made from them, on this computer. Real TeX is used when it is installed (the tests skip otherwise)."""
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from tests.helpers import IsolatedHome, common, git, make_paper
from tests.test_ui_server import UiBase
from writing_core import latexbuild

HAVE_TEX = bool((shutil.which("latexmk") or shutil.which("pdflatex")) and shutil.which("pdftoppm") and shutil.which("pdfinfo"))
needs_tex = unittest.skipUnless(HAVE_TEX, "LaTeX and poppler are not installed here")

MAIN = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{natbib}
\begin{document}
\title{A test}\author{Me}\maketitle
\section{Intro}\label{sec:intro}
Hello world \citep{smith2020}. See Section~\ref{sec:intro}.

\input{sections/two}
\bibliographystyle{plainnat}
\bibliography{references}
\end{document}
"""
TWO = "\\section{Two}\nEquation $a^2+b^2=c^2$ and more words so the page has some text on it.\n"
BIB = "@article{smith2020, author={Smith, J.}, title={Title}, journal={J}, year={2020}}\n"


def make(test, main=MAIN, two=TWO, name="alpha"):
    root = make_paper(test.tmp / "papers", name, main, bib=BIB, extra={"sections/two.tex": two})
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    common.register(str(root), name)
    return common.resolve(name)[1]


@needs_tex
class Engine(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.proj = make(self)
        self.root = Path(self.proj["path"])
        self._env = os.environ.pop("OLS_BUILD_DIR", None)

    def tearDown(self):
        os.environ.pop("OLS_BUILD_DIR", None)
        if self._env is not None:
            os.environ["OLS_BUILD_DIR"] = self._env
        super().tearDown()

    def test_the_paper_compiles_with_its_bibliography_and_leaves_nothing_to_send(self):
        r = latexbuild.compile(self.proj)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["main"], r["engine"], r["pages"], r["errors"], r["undefined"]), ("main.tex", "pdflatex", 1, [], {"citations": [], "references": []}))
        self.assertTrue((self.root / ".overleaf-skill" / "build" / "main.pdf").is_file())
        status = subprocess.run(["git", "-C", str(self.root), "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "", "a compile must add nothing that would be sent to Overleaf")
        self.assertEqual(latexbuild.last(self.proj)["pages"], 1)
        self.assertEqual(latexbuild.info(self.proj)["pdf"], True)

    def test_errors_name_the_file_and_line_and_a_pdf_is_still_made(self):
        (self.root / "sections" / "two.tex").write_text(TWO + "\\end{equation}\n")
        r = latexbuild.compile(self.proj)
        self.assertFalse(r["ok"])
        self.assertTrue(r["pdf"] and r["fresh"])
        first = r["errors"][0]
        self.assertEqual((first["file"], first["line"]), ("sections/two.tex", 3))
        self.assertIn("Bad math environment delimiter", first["message"])

    def test_undefined_citations_and_references_are_listed(self):
        (self.root / "sections" / "two.tex").write_text(TWO + "See \\ref{sec:nowhere} and \\citep{ghost2021}.\n")
        r = latexbuild.compile(self.proj)
        self.assertEqual(r["undefined"], {"citations": ["ghost2021"], "references": ["sec:nowhere"]})
        self.assertTrue(any("undefined" in w for w in r["warnings"]))
        self.assertEqual(r["errors"], [], "an undefined citation is a warning, not an error")

    def test_a_missing_package_is_reported(self):
        (self.root / "main.tex").write_text(MAIN.replace("\\usepackage{amsmath}", "\\usepackage{nosuchpackage}"))
        r = latexbuild.compile(self.proj)
        self.assertFalse(r["ok"])
        self.assertIn("nosuchpackage.sty", " ".join(r["missing"]))

    def test_pages_are_drawn_once_per_pdf_at_the_size_asked_for(self):
        latexbuild.compile(self.proj)
        a = latexbuild.page_image(self.proj, 1, 110)
        self.assertEqual(a.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(latexbuild.page_image(self.proj, 1, 110), a, "the same picture is reused")
        self.assertNotEqual(latexbuild.page_image(self.proj, 1, 200), a)
        self.assertTrue(latexbuild.page_image(self.proj, 1, 5).name.startswith("p1-36."), "far too small is raised")
        self.assertTrue(latexbuild.page_image(self.proj, 1, 9000).name.startswith("p1-300."), "far too large is lowered")
        for bad in (0, 2, "x"):
            with self.assertRaises((latexbuild.LatexProblem, ValueError)):
                latexbuild.page_image(self.proj, bad)
        old = latexbuild.info(self.proj)["id"]
        (self.root / "sections" / "two.tex").write_text(TWO + "A new sentence changes the pdf.\n")
        latexbuild.compile(self.proj)
        self.assertNotEqual(latexbuild.info(self.proj)["id"], old)
        self.assertFalse(a.exists(), "pictures of an older PDF are removed")

    def test_a_line_of_the_source_and_its_place_in_the_pdf_lead_to_each_other(self):
        latexbuild.compile(self.proj)
        fw = latexbuild.forward(self.proj, "main.tex", 7)
        self.assertEqual(fw["page"], 1)
        back = latexbuild.inverse(self.proj, fw["page"], fw["x"] + 20, fw["y"] - 3)
        self.assertEqual(back["file"], "main.tex")
        self.assertIn(back["line"], (6, 7, 8))
        sec = latexbuild.forward(self.proj, "sections/two.tex", 2)           # the second file is followed too
        self.assertEqual(latexbuild.inverse(self.proj, sec["page"], sec["x"] + 20, sec["y"] - 3)["file"], "sections/two.tex")
        self.assertEqual(latexbuild.forward(self.proj, "main.tex", 3)["page"], 1, "a line with nothing printed leads to the nearest place")
        with self.assertRaises(latexbuild.LatexProblem) as cm:
            latexbuild.forward(self.proj, "references.bib", 1)                 # the bibliography file is not typeset line by line
        self.assertEqual(cm.exception.code, "nowhere")

    def test_only_the_papers_own_text_files_can_be_read_or_written(self):
        for bad in ("../outside.tex", "/etc/passwd", ".git/config", ".overleaf-skill/settings.json", "sections/../../x.tex", "main.pdf", "figure.png"):
            with self.assertRaises(latexbuild.LatexProblem, msg=bad):
                latexbuild.read_file(self.proj, bad)
        (self.tmp / "secret.tex").write_text("secret")
        (self.root / "link.tex").symlink_to(self.tmp / "secret.tex")
        with self.assertRaises(latexbuild.LatexProblem):
            latexbuild.read_file(self.proj, "link.tex")
        with self.assertRaises(latexbuild.LatexProblem):
            latexbuild.write_file(self.proj, "../escape.tex", "x", "abc")
        self.assertFalse((self.tmp / "escape.tex").exists())
        self.assertNotIn("link.tex", [f["file"] for f in latexbuild.list_files(self.proj)])

    def test_saving_checks_that_the_file_did_not_change_underneath(self):
        got = latexbuild.read_file(self.proj, "sections/two.tex")
        ok = latexbuild.write_file(self.proj, "sections/two.tex", got["text"] + "More.\n", got["hash"])
        self.assertEqual((self.root / "sections" / "two.tex").read_text(), TWO + "More.\n")
        (self.root / "sections" / "two.tex").write_text("changed elsewhere\n")             # the assistant saved a suggestion meanwhile
        with self.assertRaises(latexbuild.LatexProblem) as cm:
            latexbuild.write_file(self.proj, "sections/two.tex", "my edit\n", ok["hash"])
        self.assertEqual(cm.exception.code, "stale")
        self.assertEqual((self.root / "sections" / "two.tex").read_text(), "changed elsewhere\n", "nothing was overwritten")
        self.assertEqual([f.name for f in (self.root / "sections").iterdir() if f.name.endswith(".part")], [])
        with self.assertRaises(latexbuild.LatexProblem) as cm:                                   # a save that does not say which version it started from is refused too
            latexbuild.write_file(self.proj, "sections/two.tex", "no base\n", "")
        self.assertEqual(cm.exception.code, "stale")

    def test_the_files_are_listed_main_first_and_changes_since_the_last_send_are_found(self):
        self.assertEqual([f["file"] for f in latexbuild.list_files(self.proj)], ["main.tex", "sections/two.tex", "references.bib"])
        self.assertTrue(latexbuild.list_files(self.proj)[0]["main"])
        self.assertEqual(latexbuild.changed(self.proj), {})
        (self.root / "sections" / "two.tex").write_text("\\section{Two}\nchanged line\nmore words\n")
        (self.root / "notes.txt").write_text("a\nb\nc\n")
        (self.root / "fig.png").write_bytes(b"\x89PNG")
        ch = latexbuild.changed(self.proj)
        self.assertEqual(ch, {"sections/two.tex": [[2, 3]], "notes.txt": [[1, 3]]})

    def test_a_document_that_never_ends_is_stopped_with_its_helpers(self):
        (self.root / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\n\\def\\a{\\a}\\a\n\\end{document}\n")
        t0 = time.time()
        r = latexbuild.compile(self.proj, timeout=3)
        self.assertLess(time.time() - t0, 30)
        self.assertTrue(r["timed_out"])
        self.assertIn("longer than 3 seconds", r["errors"][0]["message"])
        left = subprocess.run(["pgrep", "-f", str(self.root)], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [], "no TeX process is left running")

    def test_a_document_cannot_run_commands_or_write_outside_the_paper(self):
        marker = self.tmp / "ran.txt"
        evil = self.tmp / "evil.txt"
        (self.root / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nHi.\n"
                                            f"\\immediate\\write18{{touch {marker}}}\n"
                                            f"\\immediate\\openout3={evil}\n\\immediate\\write3{{x}}\n\\end{{document}}\n")
        latexbuild.compile(self.proj)
        self.assertFalse(marker.exists(), "shell escape is off")
        self.assertFalse(evil.exists(), "TeX may not write outside the paper")

    def test_a_paper_cannot_run_a_program_through_latexmkrc_or_read_files_outside_itself(self):
        marker = self.tmp / "rc-ran.txt"
        secret = self.tmp / "secret.txt"
        secret.write_text("TOP-SECRET-KEY")
        (self.root / "latexmkrc").write_text(f'system("touch {marker}");\n$pdflatex = "touch {marker} ; pdflatex %O %S";\n')
        (self.root / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nHi.\n"
                                            f"\\input{{{secret}}}\n\\openin5={secret}\n\\end{{document}}\n")
        r = latexbuild.compile(self.proj)
        self.assertFalse(marker.exists(), "a latexmkrc in the paper is a program and is not run")
        log = (self.root / ".overleaf-skill" / "build" / "main.log").read_text(errors="replace")
        self.assertNotIn("TOP-SECRET-KEY", log, "a file outside the paper is not read into the document")
        self.assertTrue(r["errors"], "the attempt shows up as an error, not as silence")

    def test_a_journal_class_with_its_own_bibliography_style_and_a_picture_folder_compiles(self):
        import struct
        import zlib
        raw = b"".join(b"\x00" + bytes((x * 3 + y) % 200 + 20 for x in range(60)) for y in range(40))
        chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)       # noqa: E731
        (self.root / "images").mkdir()
        (self.root / "images" / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 60, 40, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        bst = subprocess.run(["kpsewhich", "elsarticle-harv.bst"], capture_output=True, text=True).stdout.strip()
        if not bst:
            self.skipTest("the elsarticle class is not installed here")
        shutil.copy(bst, self.root / "elsarticle-harv.bst")                       # Overleaf projects carry their own .bst
        (self.root / "main.tex").write_text(
            "\\documentclass[review,3p,times]{elsarticle}\n\\usepackage{graphicx}\n\\usepackage{booktabs}\n\\usepackage{hyperref}\n\\graphicspath{{images/}}\n\\journal{Test}\n"
            "\\begin{document}\n\\begin{frontmatter}\n\\title{T}\n\\author{A}\n\\address{B}\n\\begin{abstract}x\\end{abstract}\n\\end{frontmatter}\n"
            "\\section{Intro}\nKnown \\citep{smith2020}.\n\\begin{figure}\\includegraphics[width=3cm]{fig}\\caption{F}\\end{figure}\n"
            "\\bibliographystyle{elsarticle-harv}\n\\bibliography{references}\n\\end{document}\n")
        r = latexbuild.compile(self.proj)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["undefined"], {"citations": [], "references": []}, "BibTeX found the style and the entries")

    def test_the_build_can_live_elsewhere_and_the_paper_gets_nothing(self):
        os.environ["OLS_BUILD_DIR"] = str(self.tmp / "scratch")
        r = latexbuild.compile(self.proj)
        self.assertTrue(r["ok"], r)
        self.assertTrue(any(self.tmp.joinpath("scratch").glob("alpha-*/main.pdf")))
        self.assertFalse((self.root / ".overleaf-skill" / "build").exists())
        self.assertTrue(latexbuild.page_image(self.proj, 1).is_file())

    def test_a_magic_comment_chooses_the_engine_and_a_main_file_can_be_named(self):
        self.assertEqual(latexbuild._engine(self.root, "main.tex"), "pdflatex")
        (self.root / "main.tex").write_text("% !TEX program = xelatex\n" + MAIN)
        self.assertEqual(latexbuild._engine(self.root, "main.tex"), "xelatex")
        (self.root / "main.tex").write_text("% !TEX program = rm -rf\n" + MAIN)
        self.assertEqual(latexbuild._engine(self.root, "main.tex"), "pdflatex", "only the known engines")
        (self.root / "paper.tex").write_text(MAIN)
        self.assertEqual(latexbuild.main_file({**self.proj, "main": "paper.tex"}), "paper.tex")


class LogParsing(unittest.TestCase):
    def test_a_log_is_read_without_tex(self):
        log = ("(./main.tex\n./main.tex:12: Undefined control sequence.\nl.12 \\foo\n\n"
               "! LaTeX Error: File `nope.sty' not found.\n\nType X to quit\n"
               "LaTeX Warning: Citation `ghost' on page 2 undefined on input line 9.\n"
               "LaTeX Warning: Reference `sec:x' on page 1 undefined on input line 3.\n")
        r = latexbuild.parse_log(log, "/p")
        self.assertEqual((r["errors"][0]["file"], r["errors"][0]["line"], r["errors"][0]["message"]), ("main.tex", 12, "Undefined control sequence."))
        self.assertEqual(r["missing"], ["nope.sty"])
        self.assertEqual(r["undefined"], {"citations": ["ghost"], "references": ["sec:x"]})
        self.assertEqual(len(r["warnings"]), 2)

    def test_nothing_in_a_log_means_nothing_found(self):
        r = latexbuild.parse_log("This is pdfTeX\nOutput written on main.pdf (1 page).\n", "/p")
        self.assertEqual((r["errors"], r["warnings"], r["missing"]), ([], [], []))


@needs_tex
class Api(UiBase):
    def setUp(self):
        super().setUp()
        self.proj = make(self)
        self.root = Path(self.proj["path"])
        self.c.login()
        self._env = os.environ.pop("OLS_BUILD_DIR", None)

    def tearDown(self):
        if self._env is not None:
            os.environ["OLS_BUILD_DIR"] = self._env
        super().tearDown()

    def compile(self):
        st, r = self.c.post("/api/latex/compile", {"paper": "alpha"})
        self.assertEqual(st, 200, r)
        j = self.wait_job(r["job"], 120)
        self.assertEqual(j["status"], "done", j)
        return j["result"]

    def test_status_lists_the_files_and_what_this_computer_can_do(self):
        st, s = self.c.get("/api/latex/status?paper=alpha")
        self.assertEqual(st, 200, s)
        self.assertTrue(s["available"])
        self.assertEqual((s["main"], s["changed"], s["last"], s["job"]), ("main.tex", {}, None, None))
        self.assertEqual([f["file"] for f in s["files"]], ["main.tex", "sections/two.tex", "references.bib"])

    def test_compile_in_the_background_then_pictures_of_the_pages_and_the_pdf(self):
        res = self.compile()
        self.assertTrue(res["ok"], res)
        st, s = self.c.get("/api/latex/status?paper=alpha")
        self.assertEqual((s["last"]["pages"], s["last"]["ok"]), (1, True))
        pid = res["id"]
        st, hd, body = self.c.raw("GET", f"/latex/page?paper=alpha&n=1&dpi=100&id={pid}")
        self.assertEqual((st, hd["content-type"], body[:4]), (200, "image/png", b"\x89PNG"))
        self.assertIn("immutable", hd["cache-control"])
        self.assertEqual(hd["cache-control"].count("max-age"), 1)
        self.assertEqual(self.c.raw("GET", "/latex/page?paper=alpha&n=1&dpi=100&id=old")[0], 409, "a picture of an older PDF is refused")
        self.assertEqual(self.c.raw("GET", f"/latex/page?paper=alpha&n=7&id={pid}")[0], 400)
        st, hd, body = self.c.raw("GET", "/latex/pdf?paper=alpha")
        self.assertEqual((st, hd["content-type"], body[:5]), (200, "application/pdf", b"%PDF-"))
        self.assertIn('filename="main.pdf"', hd["content-disposition"])

    def test_asking_again_while_it_compiles_gives_the_same_job(self):
        (self.root / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\n\\def\\a{\\a}\\a\n\\end{document}\n")
        os.environ["OLS_TEX_TIMEOUT"] = "5"
        latexbuild.COMPILE_TIMEOUT = 5
        try:
            st, a = self.c.post("/api/latex/compile", {"paper": "alpha"})
            st, b = self.c.post("/api/latex/compile", {"paper": "alpha"})
            self.assertEqual(a["job"], b["job"])
            self.assertTrue(b["already"])
            st, s = self.c.get("/api/latex/status?paper=alpha")
            self.assertEqual(s["job"], a["job"])
            self.wait_job(a["job"], 60)
        finally:
            latexbuild.COMPILE_TIMEOUT = int(os.environ.get("OLS_TEX_TIMEOUT", 180))

    def test_a_file_is_read_edited_and_saved_with_the_stale_check(self):
        st, f = self.c.get("/api/latex/file?paper=alpha&file=sections/two.tex")
        self.assertEqual((st, f["text"], f["lines"]), (200, TWO, 2))
        st, saved = self.c.post("/api/latex/save", {"paper": "alpha", "file": "sections/two.tex", "text": TWO + "New line.\n", "base": f["hash"]})
        self.assertEqual(st, 200, saved)
        self.assertEqual(saved["ranges"], [[3, 3]], "the new line is marked as not sent yet")
        self.assertEqual((self.root / "sections" / "two.tex").read_text(), TWO + "New line.\n")
        st, r = self.c.post("/api/latex/save", {"paper": "alpha", "file": "sections/two.tex", "text": "stale\n", "base": f["hash"]})
        self.assertEqual((st, r["code"]), (409, "stale"))
        self.assertEqual((self.root / "sections" / "two.tex").read_text(), TWO + "New line.\n")
        for bad in ("../x.tex", ".git/config", "nope.tex"):
            self.assertEqual(self.c.get(f"/api/latex/file?paper=alpha&file={bad}")[0], 400, bad)
            self.assertEqual(self.c.post("/api/latex/save", {"paper": "alpha", "file": bad, "text": "x", "base": "abc"})[0], 400, bad)
        self.assertEqual(self.c.post("/api/latex/save", {"paper": "alpha", "file": "main.tex", "text": 5, "base": "abc"})[0], 400)

    def test_syncing_source_and_pdf_through_the_server(self):
        self.compile()
        st, fw = self.c.post("/api/latex/forward", {"paper": "alpha", "file": "main.tex", "line": 7})
        self.assertEqual((st, fw["page"]), (200, 1), fw)
        st, back = self.c.post("/api/latex/inverse", {"paper": "alpha", "page": 1, "x": fw["x"] + 20, "y": fw["y"] - 3})
        self.assertEqual((st, back["file"]), (200, "main.tex"), back)
        self.assertEqual(self.c.post("/api/latex/forward", {"paper": "alpha", "file": "main.tex", "line": "7"})[0], 400)
        self.assertEqual(self.c.post("/api/latex/inverse", {"paper": "alpha", "page": "a", "x": 1, "y": 1})[0], 400)

    def test_nothing_is_reachable_without_signing_in_and_unknown_papers_are_refused(self):
        from tests.test_ui_server import Client
        other = Client(self.srv)
        for method, path in (("GET", "/api/latex/status?paper=alpha"), ("GET", "/latex/page?paper=alpha&n=1"), ("GET", "/latex/pdf?paper=alpha"), ("GET", "/latex.js")):
            self.assertEqual(other.raw(method, path)[0], 401, path)
        self.assertEqual(self.c.get("/api/latex/status?paper=nope")[0], 404)
        self.assertEqual(self.c.raw("GET", "/latex/page?paper=nope&n=1")[0], 404)
        st, hd, body = self.c.raw("GET", "/latex.js")
        self.assertEqual((st, hd["content-type"].split(";")[0]), (200, "text/javascript"))


class Unavailable(UiBase):
    def setUp(self):
        super().setUp()
        self.proj = make(self)
        self.c.login()
        self._real = latexbuild.available
        latexbuild.available = lambda: (False, "LaTeX is not installed on this computer.")

    def tearDown(self):
        latexbuild.available = self._real
        super().tearDown()

    def test_without_latex_the_pane_still_shows_the_source_and_says_why_it_cannot_compile(self):
        st, s = self.c.get("/api/latex/status?paper=alpha")
        self.assertEqual((st, s["available"], s["why"], s["last"]), (200, False, "LaTeX is not installed on this computer.", None))
        self.assertEqual(len(s["files"]), 3)
        st, r = self.c.post("/api/latex/compile", {"paper": "alpha"})
        self.assertEqual((st, r["code"]), (409, "unavailable"))
        self.assertEqual(self.c.get("/api/latex/file?paper=alpha&file=main.tex")[0], 200)

    def test_the_message_fits_where_the_app_runs(self):
        latexbuild.available = self._real
        old_path, old_docker = os.environ.get("PATH"), os.environ.get("OVERLEAF_UI_IN_DOCKER")
        try:
            os.environ["PATH"] = "/nonexistent"
            os.environ["OVERLEAF_UI_IN_DOCKER"] = "1"
            ok, why = latexbuild.available()
            self.assertFalse(ok)
            self.assertIn("./ui.sh build", why)
            os.environ.pop("OVERLEAF_UI_IN_DOCKER")
            self.assertIn("MacTeX", latexbuild.available()[1])
        finally:
            os.environ["PATH"] = old_path
            if old_docker is not None:
                os.environ["OVERLEAF_UI_IN_DOCKER"] = old_docker


if __name__ == "__main__":
    unittest.main()
