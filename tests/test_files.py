"""The author's own files: uploads saved safely, split into parts, found again by a request, and used (visibly) in the correction pass."""
import io
import json
import os
import zipfile
from pathlib import Path
from unittest import mock

from tests.fake_openai import passage_of
from tests.helpers import IsolatedHome, common, git
from tests.test_book import FIXTURE, needs_pdf
from tests.test_paper import paper
from tests.test_revise import ReviseBase, reply
from writing_core import chunk_book, config, files, paper_index

CSV = "model,rmse,runs\n" + "\n".join(f"m{i % 4},{0.5 + i / 100:.2f},{i}" for i in range(60)) + "\n"
SCORE_PY = '''"""Scores a model."""
import numpy as np


def rmse(a, b):
    """Root mean squared error."""
    return float(np.sqrt(np.mean((a - b) ** 2)))


class Report:
    def table(self):
        return "best rmse 0.82"
'''


def docx(paragraphs):
    xml = "".join(f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>' for style, text in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{xml}</w:body></w:document>')
    return buf.getvalue()


class FilesBase(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self, extra={"data/results.csv": CSV, "analysis/score.py": SCORE_PY, "notes/README.md": "# Notes\n\nThe survey ran in 2021.\n",
                                                  ".env": "KEY=abc", "analysis/api_token.json": "{}", "node_modules/x/readme.md": "x"})
        self.root = Path(self.proj["path"])

    def index(self):
        paper_index.refresh(self.name, self.proj, trigger="test", source="test")
        return files.listing(self.name, self.proj)

    def by_name(self):
        return {f["file"]: f for f in self.index()}


class Saving(FilesBase):
    def test_a_file_lands_in_the_references_folder_under_a_safe_name(self):
        r = files.save_upload(self.proj, "../../etc/Smith 2020: Soil?.pdf", b"%PDF-1.4 fake")
        self.assertEqual(r["name"], "Smith 2020_ Soil_.pdf")
        self.assertEqual(r["rel"], "references/Smith 2020_ Soil_.pdf")
        self.assertTrue((self.root / r["rel"]).is_file())
        self.assertEqual([p.name for p in (self.root / "references").iterdir()], [r["name"]], "no temporary file is left behind")

    def test_a_second_file_with_the_same_name_never_replaces_the_first(self):
        a = files.save_upload(self.proj, "data.csv", b"a,b\n1,2\n")
        b = files.save_upload(self.proj, "data.csv", b"a,b\n3,4\n")
        again = files.save_upload(self.proj, "data.csv", b"a,b\n1,2\n")
        self.assertEqual((a["name"], b["name"]), ("data.csv", "data (2).csv"))
        self.assertTrue(again["duplicate"] and again["name"] == "data.csv", "the same content twice is recognised")
        self.assertEqual((self.root / a["rel"]).read_bytes(), b"a,b\n1,2\n")

    def test_what_is_refused_and_why(self):
        for name, data, word in (("run.exe", b"MZ", "cannot add .exe"), ("noending", b"x", "no name or no ending"), (".env", b"KEY=1", "no ending"),
                                 ("my_password.txt", b"x", "password or key"), ("fake.pdf", b"not a pdf", "real .pdf"), ("t.txt", b"a\0b", "real .txt"),
                                 ("empty.txt", b"", "empty")):
            with self.assertRaises(files.FileProblem) as cm:
                files.save_upload(self.proj, name, data)
            self.assertIn(word, str(cm.exception), name)
        with mock.patch.object(files, "MAX_UPLOAD", 10), self.assertRaises(files.FileProblem) as cm:
            files.save_upload(self.proj, "big.txt", b"x" * 11)
        self.assertIn("limit", str(cm.exception))
        self.assertFalse((self.root / "references").exists() and any((self.root / "references").iterdir()), "nothing was written")

    def test_a_folder_named_references_that_is_really_a_file_is_explained(self):
        (self.root / "references").write_text("x")
        with self.assertRaises(files.FileProblem) as cm:
            files.save_upload(self.proj, "a.txt", b"hello")
        self.assertIn("Rename that file", str(cm.exception))

    def test_only_uploads_can_be_removed(self):
        r = files.save_upload(self.proj, "a.txt", b"hello")
        with self.assertRaises(files.FileProblem):
            files.remove_upload(self.proj, "main.tex")
        with self.assertRaises(files.FileProblem):
            files.remove_upload(self.proj, "references/../main.tex")
        self.assertTrue(files.remove_upload(self.proj, r["rel"]))
        self.assertFalse((self.root / r["rel"]).exists())


class Splitting(FilesBase):
    def test_the_files_around_the_paper_are_found_and_secrets_and_junk_are_not(self):
        found = self.by_name()
        self.assertEqual(sorted(found), ["analysis/score.py", "data/results.csv", "notes/README.md"])
        self.assertTrue(all(f["kind"] == "project" for f in found.values()))
        for bad in (".env", "analysis/api_token.json", "node_modules/x/readme.md"):
            self.assertNotIn(bad, found)

    def test_a_table_gets_a_summary_and_blocks_of_rows_with_their_numbers(self):
        self.index()
        parts = files.parts_of_file(self.name, self.proj, "data/results.csv")
        self.assertEqual(len(parts), 1 + 3, "a summary and 60 rows in blocks of 25")
        self.assertIn("Summary of the table", parts[0]["where"])
        con = paper_index.open_index(self.name, self.proj)
        text = con.execute("SELECT text FROM chunks WHERE id=?", (parts[0]["id"],)).fetchone()[0]
        con.close()
        self.assertIn("60 rows and 3 columns", text)
        self.assertIn("rmse (numbers: min 0.5, max 1.09", text)
        self.assertIn("model (text: 4 different", text)
        self.assertIn("rows 2 to 26", parts[1]["where"])

    def test_code_is_split_at_its_functions(self):
        self.index()
        parts = files.parts_of_file(self.name, self.proj, "analysis/score.py")
        self.assertTrue(parts)
        con = paper_index.open_index(self.name, self.proj)
        rows = con.execute("SELECT title, text, line_start FROM chunks WHERE file='analysis/score.py' ORDER BY ord").fetchall()
        con.close()
        joined = "\n".join(r["text"] for r in rows)
        self.assertIn("def rmse", joined)
        self.assertIn("class Report", joined)
        self.assertEqual(rows[0]["line_start"], 1)

    def test_markdown_is_split_by_its_headings(self):
        self.index()
        parts = files.parts_of_file(self.name, self.proj, "notes/README.md")
        self.assertEqual(len(parts), 1)
        self.assertIn("Notes", parts[0]["where"])

    def test_json_and_notebooks_and_word_files(self):
        nb = {"cells": [{"cell_type": "markdown", "source": ["# Results\n", "We ran it."]},
                        {"cell_type": "code", "source": ["print(0.91)"], "outputs": [{"output_type": "stream", "text": ["0.91\n"]},
                                                                                       {"output_type": "display_data", "data": {"image/png": "AAAA"}}]}]}
        (self.root / "analysis").joinpath("run.ipynb").write_text(json.dumps(nb))
        (self.root / "data" / "config.json").write_text(json.dumps({"alpha": 0.05, "folds": [1, 2, 3]}))
        files.save_upload(self.proj, "Interview.docx", docx([("Heading1", "Findings"), ("Normal", "Farmers said the water came late in 2021. " * 3)]))
        found = self.by_name()
        self.assertEqual(found["references/Interview.docx"]["kind"], "source")
        con = paper_index.open_index(self.name, self.proj)
        nbtext = "\n".join(r[0] for r in con.execute("SELECT text FROM chunks WHERE file='analysis/run.ipynb'"))
        wtext = con.execute("SELECT section_path, text FROM chunks WHERE file='references/Interview.docx'").fetchall()
        jtext = "\n".join(r[0] for r in con.execute("SELECT text FROM chunks WHERE file='data/config.json'"))
        con.close()
        self.assertIn("[code]\nprint(0.91)\n[output]\n0.91", nbtext)
        self.assertNotIn("AAAA", nbtext, "pictures in a notebook are not read")
        self.assertIn("Findings", wtext[0]["section_path"])
        self.assertIn('"alpha": 0.05', jtext)

    def test_pictures_are_kept_but_not_read_and_big_or_unreadable_files_say_why(self):
        files.save_upload(self.proj, "figure.png", b"\x89PNG....")
        (self.root / "data" / "bad.json").write_text("{not json")
        with mock.patch.object(files, "MAX_PROJECT_FILE", 100):
            found = self.by_name()
        self.assertIn("not read", found["references/figure.png"]["note"])
        self.assertIn("Too big", found["data/results.csv"]["note"])
        found = self.by_name()
        self.assertIn("cannot be read", found["data/bad.json"]["note"])

    def test_a_long_table_is_cut_with_a_note_not_a_flood(self):
        big = "x,y\n" + "\n".join(f"{i},{i * 2}" for i in range(5000)) + "\n"
        (self.root / "data" / "big.csv").write_text(big)
        found = self.by_name()
        self.assertEqual(found["data/big.csv"]["parts"], files.MAX_PARTS_PER_FILE)
        self.assertIn("first", found["data/big.csv"]["note"])

    def test_linked_folders_outside_the_project_are_read_and_a_missing_one_is_explained(self):
        other = Path(self._tmp.name) / "analysis-repo"
        (other / "src").mkdir(parents=True)
        (other / "src" / "model.py").write_text("def fit(x):\n    return x\n")
        config.set_paper_setting(self.proj, "data_folders", [str(other), str(other / "nope"), "relative/path"])
        status = files.folders(self.proj)
        self.assertEqual([f["ok"] for f in status], [True, False, False])
        self.assertIn("cannot see this folder", status[1]["note"])
        self.assertIn("full path", status[2]["note"])
        found = self.by_name()
        self.assertEqual(found[str(other / "src" / "model.py")]["kind"], "project")

    def test_folders_that_are_too_broad_hidden_or_hold_the_apps_own_keys_are_refused(self):
        data = config.data_dir()
        data.mkdir(parents=True, exist_ok=True)
        (data / "openai.json").write_text("{}")
        deep = Path(self._tmp.name) / "work" / "analysis"
        deep.mkdir(parents=True)
        hidden = Path(self._tmp.name) / "work" / ".secret-stuff"
        hidden.mkdir()
        config.set_paper_setting(self.proj, "data_folders", ["/", "/usr/lib", "/home/user", str(hidden), str(data), str(data.parent), str(deep)])
        notes = {f["path"]: (f["ok"], f["note"]) for f in files.folders(self.proj)}
        self.assertEqual([k for k, v in notes.items() if v[0]], [str(deep)])
        self.assertIn("very broad", notes["/"][1])
        self.assertIn("system folder", notes["/usr/lib"][1])
        self.assertIn("Hidden", notes[str(hidden)][1])
        self.assertIn("own settings", notes[str(data)][1])
        found = self.by_name()
        self.assertFalse([f for f in found if f.startswith(str(data))], "the keys are never read")

    def test_links_that_lead_out_of_the_folder_are_not_followed(self):
        secret = Path(self._tmp.name) / "outside.txt"
        secret.write_text("private")
        os.symlink(secret, self.root / "notes" / "link.txt")
        os.symlink(self.root.parent, self.root / "up")
        self.assertNotIn("notes/link.txt", self.by_name())

    def test_the_manuscript_itself_is_unchanged_by_all_this(self):
        res = paper_index.refresh(self.name, self.proj, trigger="test", force=True)
        self.assertGreater(res["tex_tokens"], 0)
        self.assertEqual(paper_index.outline(self.name, self.proj, refresh_first=False)["manuscript_tokens"], res["tex_tokens"])
        q = paper_index.query(self.name, self.proj, "rmse model results", refresh_first=False)
        self.assertTrue(all(c["kind"] in ("tex", "memory", "review") for c in q["chunks"]), "ordinary queries still see only the manuscript")
        q = paper_index.query(self.name, self.proj, "rmse model", kind="project", refresh_first=False)
        self.assertTrue(q["chunks"] and all(c["kind"] == "project" for c in q["chunks"]))

    def test_nothing_is_read_again_when_nothing_changed(self):
        (self.root / "notes" / "extra.md").write_text("# Extra\n\nMore notes about the soil.\n")
        self.index()
        with mock.patch.object(files, "parts_of", side_effect=AssertionError("read again")):
            paper_index.refresh(self.name, self.proj, trigger="test", force=True)
        (self.root / "notes" / "extra.md").write_text("# Extra\n\nMore notes about the soil and the climate.\n")
        with mock.patch.object(files, "parts_of", wraps=files.parts_of) as spy:
            paper_index.refresh(self.name, self.proj, trigger="test", force=True)
        self.assertEqual([c.args[0].name for c in spy.call_args_list], ["extra.md"], "only the file that changed is read again")


@needs_pdf
class Articles(FilesBase):
    def test_a_pdf_is_split_by_page_and_the_reference_list_is_left_out(self):
        pages = [["Abstract", "We studied soil moisture in forty farms across three regions. " * 6],
                 ["1. Introduction", "Yield depends on water and on soil in every region we know. " * 8],
                 ["2. Methods", "We fitted a mixed model with farm as a random intercept term. " * 8],
                 ["References", "Smith J. (2020) Water and yield. Journal of Soil 12, 1-9. " * 5]]
        with mock.patch.object(chunk_book, "extract_pages", return_value=["\n\n".join(p) for p in pages]):
            parts, noun = files.parts_pdf(Path("x.pdf"))
        self.assertEqual(noun, "pages")
        heads = [p["head"] for p in parts]
        self.assertEqual(heads[:3], ["Abstract", "1. Introduction", "2. Methods"])
        self.assertNotIn("References", heads)
        self.assertEqual([(p["lo"], p["hi"]) for p in parts][:3], [(1, 1), (2, 2), (3, 3)])
        self.assertTrue(all(p["text"].count("\n") == p["text"].count("\n\n") * 2 for p in parts), "paragraphs are joined into running text")

    def test_a_scanned_pdf_says_so(self):
        with mock.patch.object(chunk_book, "extract_pages", return_value=["", " ", ""]), self.assertRaises(files.FileProblem) as cm:
            files.parts_pdf(Path("scan.pdf"))
        self.assertIn("OCR", str(cm.exception))

    def test_a_real_pdf_is_read_through_poppler(self):
        r = files.save_upload(self.proj, "article.pdf", FIXTURE.read_bytes())
        found = self.by_name()
        self.assertGreater(found[r["rel"]]["parts"], 0)
        parts = files.parts_of_file(self.name, self.proj, r["rel"])
        self.assertRegex(parts[0]["where"], r"pages? \d+")


class Finding(FilesBase):
    def setUp(self):
        super().setUp()
        self.index()

    def rel(self, instruction, mode="auto", passage="", **kw):
        return files.relevant(self.name, self.proj, instruction, passage, mode=mode, **kw)

    def test_a_file_that_is_named_comes_whole_when_it_fits_and_with_its_summary_when_not(self):
        r = self.rel("write the results using results.csv")
        self.assertEqual((r["why"], r["files"]), ("named", ["data/results.csv"]))
        self.assertEqual(len(r["chunks"]), 4, "it fits the budget, so all of it comes, summary first")
        self.assertIn("Summary of the table", r["chunks"][0]["where"])
        r = self.rel("write the results using results.csv", budget=120)
        self.assertLessEqual(len(r["chunks"]), 2)
        self.assertIn("Summary of the table", r["chunks"][0]["where"], "a file too big for the budget still brings its summary")
        r = self.rel("what does score.py do", budget=3000)
        self.assertEqual(r["files"], ["analysis/score.py"])

    def test_the_author_can_talk_about_the_files_without_naming_one(self):
        for text in ("improve the results based on my data", "check it against my files", "use my files: rmse model", "rewrite using the uploaded article"):
            r = self.rel(text + " rmse model")
            self.assertIsNotNone(r, text)
            self.assertEqual(r["why"], "asked")

    def test_an_ordinary_request_sends_no_files(self):
        for text in ("improve the flow of the data collection section", "make it more concise", "fix the grammar in the results", "improve the flow of the text before Figure 1"):
            self.assertIsNone(self.rel(text + " rmse"), text)

    def test_the_paper_setting_decides_in_the_end(self):
        self.assertIsNone(self.rel("write the results using results.csv", mode="never"))
        r = self.rel("tighten the wording of rmse and model", mode="always")
        self.assertEqual(r["why"], "always")

    def test_a_request_that_names_a_file_reads_that_file_and_no_other(self):
        r = self.rel("use results.csv for rmse and model")
        self.assertEqual(r["files"], ["data/results.csv"])
        r = self.rel("use my files for rmse and model")
        self.assertEqual(sorted(r["files"]), ["analysis/score.py", "data/results.csv"], "with no file named, all of them are searched")

    def test_the_parts_come_back_in_file_order_within_the_budget(self):
        r = self.rel("use my files for rmse and model", budget=500)
        self.assertLessEqual(sum(c["tokens"] for c in r["chunks"][1:]), 500)
        self.assertEqual([c["ord"] for c in r["chunks"] if c["file"] == "data/results.csv"], sorted(c["ord"] for c in r["chunks"] if c["file"] == "data/results.csv"))

    def test_nothing_without_files(self):
        for f in list(self.root.glob("data/*")) + list(self.root.glob("analysis/*")) + list(self.root.glob("notes/*")):
            f.unlink()
        paper_index.refresh(self.name, self.proj, trigger="test")
        self.assertIsNone(self.rel("write the results using results.csv"))


class InTheCorrectionPass(ReviseBase):
    """The parts that go out are shown, labelled, and the numbers they hold are allowed (and pointed out)."""

    def setUp(self):
        super().setUp()
        self.live()
        (Path(self.proj["path"]) / "data").mkdir(exist_ok=True)
        (Path(self.proj["path"]) / "data" / "results.csv").write_text(CSV)
        self.srv.script = [reply(("We report the results here.", "The best model reached an RMSE of 0.82."))] * 4

    def request(self, instruction):
        return self.go(instruction, part="results")

    def test_a_request_that_names_a_file_carries_its_parts_and_the_rule_for_using_them(self):
        r = self.request("rewrite the results sentence using results.csv")
        body = self.srv.requests[0]["body"]
        text = json.dumps(body)
        self.assertIn("<source_material>", text)
        self.assertIn("data/results.csv", text)
        self.assertIn("Summary of the table", text)
        self.assertIn("7. <source_material> holds parts of the author's own files", text)
        prop = r["proposal"]
        self.assertEqual({s["file"] for s in prop["sources"]}, {"data/results.csv"})
        self.assertTrue(prop["steps"][1]["label"] == "Looking at your files" or any(s["label"] == "Looking at your files" for s in prop["steps"]))

    def test_a_number_from_the_file_is_allowed_and_pointed_out_but_one_from_nowhere_is_not(self):
        CSV_HAS = "0.82"
        (Path(self.proj["path"]) / "data" / "results.csv").write_text("model,rmse\nA,0.82\nB,0.91\n")
        r = self.request("rewrite the results sentence using results.csv")
        u = r["proposal"]["units"][0]
        self.assertEqual(u["status"], "ok", u.get("error"))
        self.assertEqual(u["from_files"], [CSV_HAS])
        self.srv.script = [reply(("We report the results here.", "The best model reached an RMSE of 0.77."))] * 4
        r = self.request("rewrite the results sentence using results.csv")
        self.assertEqual(r["proposal"]["units"][0]["status"], "failed", "a number found in no file is still refused")

    def test_an_ordinary_request_is_exactly_what_it_was(self):
        self.srv.script = [reply(("We report the results here.", "We report the results."))] * 4
        r = self.request("make the results more concise")
        text = json.dumps(self.srv.requests[0]["body"])
        self.assertNotIn("source_material", text)
        self.assertEqual(r["proposal"]["sources"], [])
        self.assertNotIn("7. <source_material>", text)

    def test_the_preview_and_the_hash_include_the_parts(self):
        a = self.go("rewrite the results sentence using results.csv", part="results", dry_run=True)
        b = self.go("rewrite the results sentence", part="results", dry_run=True)
        self.assertEqual({s["file"] for s in a["sources"]}, {"data/results.csv"})
        self.assertEqual(b["sources"], [])
        self.assertNotEqual(a["payload_hash"], b["payload_hash"])
        self.assertGreater(a["est_tokens_in"], b["est_tokens_in"])
        self.assertEqual(self.srv.requests, [], "a preview sends nothing")

    def test_the_setting_never_keeps_them_out_even_when_named(self):
        config.set_paper_setting(self.proj, "use_files", "never")
        r = self.request("rewrite the results sentence using results.csv")
        self.assertNotIn("source_material", json.dumps(self.srv.requests[0]["body"]))
        self.assertEqual(r["proposal"]["sources"], [])


class FromTheTerminal(FilesBase):
    """Claude Code and the terminal read the same files on this computer, no Docker in between, and can link a folder without the web app."""

    def cli(self, *args):
        import subprocess
        import sys
        env = {**os.environ, "OVERLEAF_SKILL_HOME": str(common.HOME), "OLS_BROWSER": "off"}
        return subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "writing.py"), *args, "--paper", self.name], capture_output=True, text=True, env=env, cwd=self.root)

    def test_a_folder_is_linked_listed_and_unlinked_from_the_terminal(self):
        other = Path(self._tmp.name) / "soil" / "analysis"
        (other / "src").mkdir(parents=True)
        (other / "src" / "model.py").write_text("def fit(x):\n    return x\n")
        (other / "out").mkdir()
        (other / "out" / "table2.csv").write_text("model,rmse\nA,0.81\nB,0.77\n")
        r = self.cli("settings", "--link-folder", str(other))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"linked {other.resolve()}", r.stdout)
        r = self.cli("files")
        self.assertIn(str(other / "out" / "table2.csv"), r.stdout)
        self.assertRegex(r.stdout, r"project\s+2 parts .*table2\.csv")
        r = self.cli("files", "--parts", str(other / "out" / "table2.csv"))
        self.assertIn("Summary of the table", r.stdout)
        q = self.cli("query", "rmse of model", "--kind", "project", "--no-text")
        self.assertIn("table2.csv", q.stdout)
        r = self.cli("settings", "--unlink-folder", str(other))
        self.assertNotIn(str(other / "out"), self.cli("files").stdout)

    def test_a_folder_that_is_too_broad_or_missing_is_refused_with_the_reason(self):
        for bad, word in (("/", "broad"), ("/usr/lib", "system"), (str(self.tmp / "nope"), "not a folder")):
            r = self.cli("settings", "--link-folder", bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn(word, r.stderr + r.stdout)

    def test_the_setting_for_when_requests_use_files(self):
        r = self.cli("settings", "--use-files", "never")
        self.assertIn('"use_files": "never"', r.stdout)
