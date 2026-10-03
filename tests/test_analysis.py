"""The folder where the author's analysis lives: code, notebooks, tables, logs and figures are read; a request about the results brings the right parts, and the figures
as pictures; numbers still have to come from the text."""
import json
import unittest
from pathlib import Path

from PIL import Image

from tests.fake_openai import passage_of
from tests.test_book import needs_pdf
from tests.test_files import docx
from tests.test_paper import paper
from tests.test_revise import ReviseBase, reply, user_of
from tests.helpers import IsolatedHome
from tests.test_ui_server import UiBase
from writing_core import config, files, openai_client, paper_index

NOTEBOOK = {"cells": [{"cell_type": "markdown", "source": ["# Explore\n", "RMSE by model."]},
                      {"cell_type": "code", "source": ["print(df.groupby('model').rmse.mean())"], "outputs": [{"output_type": "stream", "text": ["gnn 0.412\n", "lstm 0.588\n"]}]}]}


def make_analysis(root):
    root = Path(root)
    for d in ("results", "figures", "notebooks", ".git", "__pycache__"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "train.py").write_text("def fit(x):\n    return x * 0.412\n\n\ndef evaluate(model):\n    return {'rmse': 0.412}\n")
    (root / "results" / "metrics.csv").write_text("model,rmse,mae\ngnn,0.412,0.301\nlstm,0.588,0.455\n")
    (root / "results" / "summary.json").write_text(json.dumps({"best": "gnn", "rmse": 0.412, "folds": 5}))
    (root / "results" / "run.log").write_text("epoch 10 loss 0.31\nepoch 20 loss 0.24\nfinal rmse 0.412\n")
    (root / "results" / "table1.tex").write_text("\\begin{tabular}{lr}\ngnn & 0.412 \\\\\nlstm & 0.588 \\\\\n\\end{tabular}\n")
    (root / "notebooks" / "explore.ipynb").write_text(json.dumps(NOTEBOOK))
    Image.new("RGB", (1600, 900), (40, 90, 200)).save(root / "figures" / "rmse_by_model.png")
    Image.new("RGB", (800, 600), (200, 90, 40)).save(root / "figures" / "loss_curve.jpg")
    (root / ".git" / "config.py").write_text("x = 1\n")
    (root / "secret_token.py").write_text("T = 'abc'\n")
    return root


class Reading(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)
        self.dir = make_analysis(self.tmp / "work" / "analysis")

    def link(self, role="analysis", path=None):
        return files.set_folders(self.proj, [str(path or self.dir)], role)

    def by_name(self):
        paper_index.refresh(self.name, self.proj, trigger="files")
        return {f["file"].rsplit("/", 1)[-1]: f for f in files.listing(self.name, self.proj)}

    def test_code_tables_logs_notebooks_and_figures_are_read_and_the_rest_is_left(self):
        self.assertTrue(self.link()[0]["ok"])
        found = self.by_name()
        self.assertEqual(sorted(found), ["explore.ipynb", "loss_curve.jpg", "metrics.csv", "rmse_by_model.png", "run.log", "summary.json", "table1.tex", "train.py"])
        self.assertTrue(all(f["role"] == "analysis" and f["parts"] for f in found.values()), found)
        self.assertNotIn("config.py", found, "hidden folders are not read")
        self.assertNotIn("secret_token.py", found, "nor anything that looks like a secret")

    def test_a_picture_is_one_part_with_its_name_and_size_and_nothing_is_guessed_about_what_it_shows(self):
        self.link()
        self.by_name()
        parts = files.parts_of_file(self.name, self.proj, str(self.dir / "figures" / "rmse_by_model.png"))
        self.assertEqual(len(parts), 1)
        self.assertIn("1600 by 900 pixels", parts[0]["preview"])
        self.assertIn("rmse by model", parts[0]["preview"])
        self.assertEqual(parts[0]["where"], "picture 1")

    def test_the_two_kinds_of_folders_are_kept_apart(self):
        notes = self.tmp / "work" / "notes"
        notes.mkdir()
        (notes / "interviews.md").write_text("# Interviews\n\nFarmers said the water came late in 2021.\n")
        make_analysis(notes / "stray")
        self.link("material", notes)
        self.link("analysis")
        self.assertEqual([f["path"] for f in files.folders(self.proj, "material")], [str(notes)])
        self.assertEqual([f["path"] for f in files.folders(self.proj, "analysis")], [str(self.dir)])
        self.assertEqual(len(files.folders(self.proj)), 2)
        found = self.by_name()
        self.assertEqual(found["interviews.md"]["role"], "material")
        self.assertNotIn("rmse_by_model.png", [f["file"].rsplit("/", 1)[-1] for f in files.listing(self.name, self.proj) if f["role"] == "material"], "a figure is read only in a code and results folder")
        self.assertNotIn("run.log", [f["file"].rsplit("/", 1)[-1] for f in files.listing(self.name, self.proj) if f["role"] == "material"])
        self.assertIsNone(files.folders(self.proj, "analysis")[0].get("bad"))
        files.unlink_folder(self.proj, self.dir, "analysis")
        self.assertEqual(files.folders(self.proj, "analysis"), [])
        self.assertEqual(len(files.folders(self.proj, "material")), 1)

    def test_the_same_folder_written_three_ways_is_one_and_a_missing_one_is_explained(self):
        got = files.set_folders(self.proj, [str(self.dir), str(self.dir) + "/", str(self.dir), str(self.tmp / "nowhere" / "a" / "b")], "analysis")
        self.assertEqual([f["ok"] for f in got], [True, False])
        self.assertIn("cannot see this folder", got[1]["note"])
        self.assertEqual(got[0]["role"], "analysis")

    def test_a_request_about_the_results_brings_the_numbers_the_code_and_the_figures(self):
        self.link()
        self.by_name()
        r = files.relevant(self.name, self.proj, "explain my results by model", "We compare two models.", budget=3000)
        self.assertEqual(r["why"], "asked")
        names = {f.rsplit("/", 1)[-1] for f in r["files"]}
        self.assertIn("metrics.csv", names)
        self.assertEqual([i["name"] for i in r["images"]][:1], ["rmse_by_model.png"], "the figure whose name shares words with the request goes first")
        text = files.block(r)
        self.assertIn("gnn,0.412", text.replace(" ", ""))
        self.assertIn("The picture itself is attached", text)

    def test_every_figure_goes_when_the_analysis_has_only_a_few_and_the_request_is_about_it(self):
        self.link()
        self.by_name()
        r = files.relevant(self.name, self.proj, "describe the results", "x", budget=3000)
        self.assertEqual(sorted(i["name"] for i in r["images"]), ["loss_curve.jpg", "rmse_by_model.png"])

    def test_at_most_four_pictures_go_out(self):
        for i in range(7):
            Image.new("RGB", (50, 50), (i * 30, 0, 0)).save(self.dir / "figures" / f"fig{i}.png")
        self.link()
        self.by_name()
        r = files.relevant(self.name, self.proj, "describe the results", "x", budget=3000)
        self.assertEqual(r["images"], [], "too many to send them all blindly: a request has to name them or share words with their names")
        r = files.relevant(self.name, self.proj, "describe the results in fig1 fig2 fig3 fig4 fig5 fig6", "x", budget=3000)
        self.assertEqual(len(r["images"]), files.MAX_IMAGES)

    def test_an_ordinary_edit_or_a_switch_off_leaves_the_analysis_out(self):
        self.link()
        self.by_name()
        self.assertIsNone(files.relevant(self.name, self.proj, "Improve the flow of the results section", "Yield rose."))
        self.assertIsNone(files.relevant(self.name, self.proj, "explain my results", "x", analysis_mode="never"))
        self.assertIsNotNone(files.relevant(self.name, self.proj, "Improve the flow", "gnn rmse model", analysis_mode="always"))
        self.assertIsNone(files.relevant(self.name, self.proj, "explain my results", "x", mode="never", analysis_mode="never"))

    def test_a_request_about_the_analysis_does_not_pull_in_notes_and_a_request_about_notes_does_not_pull_in_the_analysis(self):
        notes = self.tmp / "work" / "notes"
        notes.mkdir()
        (notes / "interviews.md").write_text("# Interviews\n\nFarmers said the water came late in 2021 and wells ran dry.\n")
        self.link("material", notes)
        self.link()
        self.by_name()
        r = files.relevant(self.name, self.proj, "interpret the figures for the wells", "x", budget=3000)
        self.assertNotIn("interviews.md", {f.rsplit("/", 1)[-1] for f in r["files"]})
        r = files.relevant(self.name, self.proj, "use my files to describe the wells", "water wells", budget=3000)
        self.assertEqual({f.rsplit("/", 1)[-1] for f in r["files"]}, {"interviews.md"})

    def test_a_picture_is_shrunk_before_it_goes_and_a_damaged_one_is_left_out(self):
        big = files.image_payload(self.dir / "figures" / "rmse_by_model.png")
        im = Image.open(__import__("io").BytesIO(__import__("base64").b64decode(big["b64"])))
        self.assertEqual((big["mime"], max(im.size)), ("image/png", files.IMAGE_SIDE))
        self.assertEqual(files.image_payload(self.dir / "figures" / "loss_curve.jpg")["mime"], "image/jpeg")
        (self.dir / "figures" / "broken.png").write_bytes(b"\x89PNG not really")
        self.assertIsNone(files.image_payload(self.dir / "figures" / "broken.png"))
        self.assertIsNone(files.image_payload(self.dir / "figures" / "missing.png"))

    def test_image_sizes_are_read_without_a_library(self):
        for name, size in (("rmse_by_model.png", (1600, 900)), ("loss_curve.jpg", (800, 600))):
            self.assertEqual(tuple(files.image_size(self.dir / "figures" / name)), size)
        Image.new("RGB", (30, 20)).save(self.dir / "g.gif")
        Image.new("RGB", (31, 21)).save(self.dir / "w.webp", lossless=True)
        self.assertEqual(tuple(files.image_size(self.dir / "g.gif")), (30, 20))
        self.assertEqual(tuple(files.image_size(self.dir / "w.webp")), (31, 21))
        self.assertIsNone(files.image_size(self.dir / "train.py"))


@needs_pdf
class InARequest(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()
        self.dir = make_analysis(self.tmp / "work" / "analysis")
        files.set_folders(self.proj, [str(self.dir)], "analysis")
        paper_index.refresh(self.name, self.proj, trigger="files")

    def test_the_pictures_travel_with_the_request_in_either_api_style_and_the_preview_never_shows_them(self):
        pre = self.go("describe the results", part="methods", dry_run=True)
        self.assertEqual(sorted(x["name"] for x in pre["pictures"]), ["loss_curve.jpg", "rmse_by_model.png"])
        shown = json.dumps(pre["payloads"])
        self.assertIn("[picture,", shown)
        self.assertNotIn("base64,", shown, "the pictures themselves are not in what is shown")
        self.assertIn("The picture itself is attached", shown)
        self.srv.script = [reply(("We sampled", "We drew a sample of"))]
        r = self.go("describe the results", part="methods")
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        body = self.srv.requests[-1]["body"]
        content = body["input"][0]["content"]
        self.assertEqual([c["type"] for c in content], ["input_text", "input_image", "input_image"])
        self.assertTrue(content[1]["image_url"].startswith("data:image/"))
        system = body["instructions"]
        self.assertIn("never read an exact value off a picture", system)
        self.assertEqual(sorted(x["name"] for x in r["proposal"]["pictures"]), ["loss_curve.jpg", "rmse_by_model.png"])
        no = self.go("tighten my methods", part="methods", dry_run=True)
        self.assertEqual(no["pictures"], [])
        self.assertNotEqual(no["payload_hash"], pre["payload_hash"])
        self.assertNotIn("never read an exact value", json.dumps(no["payloads"]))

    def test_a_model_that_takes_no_pictures_is_asked_again_without_them_and_the_author_is_told(self):
        refuse = (400, {"error": {"message": "This model does not support image inputs", "code": "invalid_request"}})
        self.srv.script = [refuse, reply(("We sampled", "We drew a sample of"))]
        r = self.go("describe the results", part="methods")
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        first, second = self.srv.requests[-2]["body"], self.srv.requests[-1]["body"]
        self.assertIsInstance(first["input"], list)
        self.assertIsInstance(second["input"], str)
        self.assertIn("could not be attached", second["input"])
        self.assertTrue(r["proposal"]["units"][0]["no_pictures"])
        self.assertTrue(any("would not take pictures" in w for w in r["proposal"]["warnings"]))

    def test_numbers_from_the_results_are_allowed_and_pointed_out_but_a_number_that_is_nowhere_is_not(self):
        self.srv.script = [reply(("We fitted a mixed-effects", "The best model reached an RMSE of 0.412. We fitted a mixed-effects"))]
        r = self.go("explain my results by model", part="methods")
        self.assertEqual(r["status"], "proposed", r["proposal"]["units"][0].get("error"))
        self.assertIn("0.412", r["proposal"]["units"][0]["from_files"])
        self.srv.script = [reply(("We fitted a mixed-effects", "The best model reached an RMSE of 0.777. We fitted a mixed-effects"))] * 2
        bad = self.go("explain my results by model", part="methods")
        self.assertEqual(bad["status"], "failed")
        self.assertIn("0.777", bad["proposal"]["units"][0]["error"])

    def test_the_papers_own_switch_decides(self):
        config.set_paper_setting(self.proj, "use_analysis", "never")
        pre = self.go("explain my results by model", part="methods", dry_run=True)
        self.assertEqual((pre["pictures"], pre["sources"]), ([], []))

    def test_the_chat_style_api_gets_its_pictures_as_image_urls(self):
        cfg = {**config.openai_cfg(), "api": "chat"}
        img = files.image_payload(self.dir / "figures" / "loss_curve.jpg")
        _, body = openai_client.build_request(cfg, "sys", "hello", images=[img])
        self.assertEqual([c["type"] for c in body["messages"][1]["content"]], ["text", "image_url"])
        self.assertEqual(openai_client.user_text(body), "hello")
        self.assertNotIn("base64,", json.dumps(openai_client.redact(body)))
        _, plain = openai_client.build_request(cfg, "sys", "hello")
        self.assertEqual(plain["messages"][1]["content"], "hello")


class Api(UiBase):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)
        self.c.login()
        self.dir = make_analysis(self.tmp / "work" / "analysis")

    def test_the_folder_is_saved_as_code_and_results_and_counted_by_kind(self):
        st, r = self.c.post("/api/files/folders", {"paper": "alpha", "folders": [str(self.dir)], "role": "analysis"})
        self.assertEqual(st, 200, r)
        f = r["folders"][0]
        self.assertEqual((f["ok"], f["role"], f["files"]), (True, "analysis", 8))
        self.assertEqual(f["groups"], {"code": 1, "table": 2, "results": 1, "log": 1, "notebook": 1, "figure": 2})
        self.assertTrue(all(x["role"] == "analysis" for x in r["files"]))
        st, g = self.c.get("/api/files?paper=alpha")
        self.assertEqual((g["folders"], [x["path"] for x in g["analysis_folders"]], g["use_analysis"]), ([], [str(self.dir)], "auto"))
        self.assertEqual(g["group_names"]["figure"], "Figures")
        self.assertEqual({x["name"]: x["group"] for x in g["files"]}["loss_curve.jpg"], "figure")

    def test_bad_roles_and_lists_are_refused_and_the_setting_is_checked(self):
        self.assertEqual(self.c.post("/api/files/folders", {"paper": "alpha", "folders": [], "role": "other"})[0], 400)
        self.assertEqual(self.c.post("/api/files/folders", {"paper": "alpha", "folders": "x", "role": "analysis"})[0], 400)
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "use_analysis": "sometimes"})[0], 400)
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "use_analysis": "always"})[0], 200)
        self.assertEqual(self.c.get("/api/files?paper=alpha")[1]["use_analysis"], "always")

    def test_the_command_line_links_and_unlinks_it_too(self):
        import subprocess
        import sys
        import os
        env = {**os.environ, "OVERLEAF_SKILL_HOME": str(common_home())}
        py = [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "writing.py")]
        out = subprocess.run(py + ["settings", "--paper", "alpha", "--link-analysis", str(self.dir)], capture_output=True, text=True, env=env)
        self.assertIn("as code and results", out.stdout, out.stderr)
        self.assertEqual([f["path"] for f in files.folders(self.proj, "analysis")], [str(self.dir)])
        subprocess.run(py + ["settings", "--paper", "alpha", "--unlink-analysis", str(self.dir)], capture_output=True, text=True, env=env)
        self.assertEqual(files.folders(self.proj, "analysis"), [])


def common_home():
    from tests.helpers import common
    return common.HOME


if __name__ == "__main__":
    unittest.main()
