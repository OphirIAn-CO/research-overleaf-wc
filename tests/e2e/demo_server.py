#!/usr/bin/env python3
"""A self-contained demo of the web app for browser tests and screenshots: two fixture papers, the mini book imported, a fake OpenAI
and a fake step-6b service, and some synthetic history so the Monitor has something to draw. Nothing leaves this machine.

  python3 tests/e2e/demo_server.py [--port 8799]      prints the URL, runs until killed
"""
import argparse
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
home = tempfile.mkdtemp(prefix="ols-demo-")
os.environ["OVERLEAF_SKILL_HOME"] = home
WITH_REAL = "--real" in sys.argv         # --real: the panes show an ordinary, unautomated browser on a virtual screen (the mode used for QuillBot)
WITH_BROWSER = "--browser" in sys.argv or WITH_REAL   # --browser: a real Chromium behind the side panes, pointed at a stand-in site that refuses framing
if not WITH_BROWSER:
    os.environ["OLS_BROWSER"] = "off"

from tests.fake_openai import FakeOpenAI, passage_of  # noqa: E402
from tests.helpers import common, git, load_ui_server  # noqa: E402
from tests.test_paper import BIB, METHODS  # noqa: E402
from tests.test_v2_chat import MAIN  # noqa: E402  (has a figure and a table, so 'before Figure 1' can be tried)
from tests.test_ui_server import FakeWatermark  # noqa: E402
from writing_core import book_index, config, events, paper_index, retrieve  # noqa: E402
from tests.helpers import make_paper  # noqa: E402

TOKEN = "demo-token-" + "x" * 30



def tiny_png(w=120, h=80):
    """A small valid PNG (a grey gradient) made by hand, so the demo paper's figure exists without any imaging library."""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes((x * 2 + y * 3) % 200 + 30 for x in range(w)) for y in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--no-book", action="store_true")
    args = ap.parse_args()
    ui = load_ui_server()
    import doi_bot
    seen = []

    def fake_bibtex(d):  # no network in the demo: doi.org answers with a fixed entry that carries its own doi
        if "bad" in d:
            raise RuntimeError("HTTP 404")
        seen.append(d)
        return ("@article{x,\n author={%s},\n title={Soil Water Dynamics Under Irrigation},\n year={2021},\n journal={Demo Journal},\n doi={%s}\n}"
                % ("Lee, K." if len(seen) == 1 else f"Other{len(seen)}, K.", d))
    doi_bot.fetch_bibtex = fake_bibtex
    from writing_core import scholar  # noqa: E402
    from tests.test_scholar import Net, routes  # noqa: E402
    scholar.fetch = Net(routes())          # the internet search answers from fixed pages: a search page, a journal page, OpenAlex and Crossref
    doi_bot.to_mendeley = lambda bib, doi, bib_path, group: f"Mendeley: added to {group}."
    doi_bot.git_sync = lambda bib_path, message: (True, "Pushed to Overleaf.")
    papers_dir = Path(home) / "papers"
    names = {}
    for name, tweak in (("soil-yield", lambda s: s), ("traffic-gnn", lambda s: s.replace("Soil Moisture Effects on Crop Yield", "Graph Networks for Traffic")
                                                         .replace("Crop yield depends on water and on soil.", "Traffic forecasting depends on graph structure."))):
        root = make_paper(papers_dir, name, tweak(MAIN), bib=BIB, extra={"sections/methods.tex": tweak(METHODS)})
        (root / "map.png").write_bytes(tiny_png())             # the figure the paper includes, so a compile has a picture to find
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "init")
        common.register(str(root), name, group="Demo group" if name == "soil-yield" else None)
        names[name] = common.resolve(name)

    # the author's reading and material live outside the data folder (the app refuses to read its own settings)
    from tests.pdfmaker import make_pdf  # noqa: E402
    from tests.test_library import BIB as LIB_BIB, IRRIGATION, SOIL  # noqa: E402
    shelf = Path(home + "-library")
    (shelf / "Literature" / "soil").mkdir(parents=True)
    make_pdf(shelf / "Literature" / "soil" / "Smith et al. - 2020 - Soil moisture.pdf", SOIL, title="Soil moisture and crop yield in semi-arid farms", author="Jane Smith; Kim Lee")
    make_pdf(shelf / "Literature" / "irrigation.pdf", IRRIGATION)
    (shelf / "Literature" / "refs.bib").write_text(LIB_BIB)
    (shelf / "Material").mkdir()
    (shelf / "Material" / "interviews.md").write_text("# Interviews\n\nFarmers said the water came late in 2021 and that wells ran dry in July.\n")
    make_pdf(shelf / "Material" / "report.pdf", [["# Field report", "The field team visited forty farms in three regions between March and June and measured soil moisture weekly."]])

    from PIL import Image  # noqa: E402
    (shelf / "Analysis" / "results").mkdir(parents=True)
    (shelf / "Analysis" / "figures").mkdir()
    (shelf / "Analysis" / "train.py").write_text("def evaluate(model):\n    return {'rmse': 0.412}\n")
    (shelf / "Analysis" / "results" / "metrics.csv").write_text("model,rmse\ngnn,0.412\nlstm,0.588\n")
    (shelf / "Analysis" / "results" / "run.log").write_text("epoch 20 loss 0.24\nfinal rmse 0.412\n")
    Image.new("RGB", (1200, 700), (40, 90, 200)).save(shelf / "Analysis" / "figures" / "rmse_by_model.png")

    oa = FakeOpenAI("responses")

    rubric = Path(home) / "rubric" / "peer_reviewer_agent.md"
    rubric.parent.mkdir()
    rubric.write_text("# Peer reviewer\nScore five dimensions from 1 to 5 and give a verdict.\n")
    os.environ["OLS_RUBRIC"] = str(rubric)
    from tests.test_v2_review import REPORT

    def edit(body):
        if "text" not in body and "peer reviewer" in str(body.get("instructions", "")).lower():
            return REPORT          # a review asks for free text, not the JSON of an edit
        from writing_core import openai_client
        user = openai_client.user_text(body)
        if (((body.get("text") or {}).get("format")) or {}).get("name") == "queries":      # the search words for finding references
            return {"queries": ["armed conflict attacks on oil pipelines Colombia", "violence and the security of energy infrastructure"]}
        if (((body.get("text") or {}).get("format")) or {}).get("name") == "draft" and "literature review" in user:      # new text from an outline the author pasted in the request
            return {"approach": "The introduction follows your outline: why risk perception is not the same for everyone, and why models that average over people can miss it.",
                    "draft_text": "Risk perception is not homogeneous across people, hazards or countries.\n\nModels based only on aggregate relationships may obscure this heterogeneity.",
                    "basis": [{"statement": "Risk perception is not homogeneous", "source": "request"}, {"statement": "Models based only on aggregate", "source": "request"}], "questions": []}
        if (((body.get("text") or {}).get("format")) or {}).get("name") == "draft":     # new text from the author's analysis, resting on the sources it was given
            m = re.search(r"\[\[(LX\d+)\]\] = the number of Table 1", user)
            return {"approach": "The first paragraph compares the models by their error; the second says how training went. Both rest on your results files.",
                    "draft_text": "The best model reached an RMSE of 0.412, against 0.588 for the recurrent baseline" + (f", as Table~[[{m.group(1)}]] shows" if m else "") + ".\n\n"
                                  "Training loss reached 0.24 by epoch 20.",
                    "basis": [{"statement": "The best model reached an RMSE of 0.412", "source": "S1"}, {"statement": "Training loss reached 0.24", "source": "S2"}], "questions": []}
        t = passage_of(body)
        answered = "<author_answers>" in user                       # the author replied to the question below: use the reply
        t = t.replace("We sampled 40 farms by stratified random sampling.", "We drew a stratified random sample of 40 farms.")
        if "metrics.csv" in user:          # the request carried the author's analysis: state a result that stands in it
            t = t.replace(", as Table~", ", and the best model reached an RMSE of 0.412, as Table~")
        if "Cite it with [[LX901]]" in user:          # the request offered a source from the author's library: cite it where the frame is described
            t = t.replace("sorted by size before sampling.", "sorted by size before sampling [[LX901]].")
        t = t.replace("Informed consent was obtained from all farmers.", "All farmers gave informed consent.")
        t = t.replace("We fitted", "We estimated")
        t = t.replace("Yield rose with moisture in all three regions", "Yield rose with moisture in each of the three regions" + (", judged on wells with at least 6 months of data" if answered else ""))
        t = t.replace("Each region has a distinct climate and soil.", "Each region has its own climate and soil.")
        t = t.replace("The study was conducted in three regions that differ in rainfall and elevation.", "The study covers three regions that differ in rainfall and elevation.")
        return {"approach": "The opening sentences were passive and repeated the same idea. I made the actor explicit and joined the two sentences so the paragraph reads in one line of thought.",
                "revised_text": t, "questions": (["What was the response rate?"] if "farmers" in t else []) + (["What specific criteria were used for the wells?"] if "reviewer comment" in user and not answered else []) + (["Which citations would support the claim about security and violence?"] if "security" in user and not answered else []), "changes": [
            {"before": "The study was conducted in three regions that differ in rainfall and elevation.", "after": "The study covers three regions that differ in rainfall and elevation.", "reason": "Active voice reads faster.", "rule": "B:mini:c05.s01.k01"},
            {"before": "Each region has a distinct climate and soil.", "after": "Each region has its own climate and soil.", "reason": "Shorter and plainer.", "rule": "B:mini:c02.s01.k01"},
            {"before": "Informed consent was obtained from all farmers.", "after": "All farmers gave informed consent.", "reason": "Use the active voice when the actor matters.", "rule": "B:mini:c05.s01.k01"},
            {"before": "We sampled 40 farms by stratified random sampling.", "after": "We drew a stratified random sample of 40 farms.", "reason": "Name the sampling method plainly.", "rule": "B:mini:c02.s01.k01"}]}
    oa.script = [edit] * 50
    common.save("openai.json", {"api_key": "sk-demo", "model": "demo-model", "base_url": oa.url, "prices": {"input_per_1m": 2.0, "output_per_1m": 8.0}})
    wm = FakeWatermark()
    make_paper(papers_dir, "extra-draft", MAIN, bib=BIB, remote="https://git.overleaf.com/" + "5a1b2c3d" * 3)   # a clone in a shared folder that is not added yet
    common.save("ui.json", {"watermark_url": wm.url, "idle_minutes": 240, "workspace_roots": [str(papers_dir)]})

    pdf = ROOT / "tests" / "fixtures" / "mini_book.pdf"
    NO_BOOK = "--no-book" in sys.argv                        # --no-book: nothing imported yet, to try uploading the book from Settings
    if not NO_BOOK:
        config.inbox_dir().mkdir(parents=True, exist_ok=True)
        (config.inbox_dir() / "mini_book.pdf").write_bytes(pdf.read_bytes())
        book_index.ingest(pdf, "mini")

    # real activity
    for n, (name, proj) in names.items():
        paper_index.query(name, proj, "improve my methods", source="claude")
        if not NO_BOOK:
            retrieve.book_query("mini", "how do I describe the statistical analysis", paper=name, paper_scope=config.paper_dir(proj), source="claude")
        paper_index.query(name, proj, "tense and passive voice in the discussion", source="claude")
    if not NO_BOOK:
        retrieve.book_query("mini", "what to put in limitations", source="ui")
    soil = names["soil-yield"]

    # ten days of synthetic history (metadata only, like the real logs)
    rnd = random.Random(7)
    now = time.time()
    scope = config.paper_dir(soil[1])
    month = events.month_file(scope)
    with open(month, "a", encoding="utf-8") as f:
        for d in range(10, 0, -1):
            base = now - d * 86400
            for k in range(rnd.randint(1, 4)):
                ts = base + k * 3000
                f.write(json.dumps({"v": 1, "ts": ts, "type": "index_refresh", "paper": "soil-yield", "trigger": rnd.choice(["pass_start", "after_edit", "after_pull"]),
                                    "added": 0, "changed": rnd.choice([0, 1, 1, 2, 3]), "removed": rnd.choice([0, 0, 0, 1]), "unchanged": 10, "total": 11,
                                    "skipped_scan": rnd.random() < 0.3, "duration_s": 0.02}) + "\n")
                f.write(json.dumps({"v": 1, "ts": ts + 5, "type": "retrieve", "paper": "soil-yield", "kind": "paper", "query": "improve my methods", "ids": ["P:soil-yield:main.tex#introduction#k01"],
                                    "tokens": rnd.randint(60, 400), "budget": 2500, "manuscript_tokens": 246, "mode": "part"}) + "\n")
            if d % 3 == 0:
                f.write(json.dumps({"v": 1, "ts": base + 100, "type": "revise", "paper": "soil-yield", "dry_run": False, "status": "proposed", "model": "demo-model",
                                    "tokens_in": rnd.randint(500, 1500), "tokens_out": rnd.randint(150, 500), "latency_ms": rnd.randint(2000, 9000), "retries": rnd.choice([0, 0, 1]),
                                    "hunks": 2, "manuscript_tokens": 246, "validators": {"placeholders_intact": True, "no_new_numbers": True, "no_new_dashes": rnd.random() > 0.2}, "cost": 0.004}) + "\n")
                f.write(json.dumps({"v": 1, "ts": base + 200, "type": "decision", "paper": "soil-yield", "accepted": 2, "rejected": rnd.choice([0, 1]),
                                    "rules_rejected": ["B:mini:c05.s02.k01"] if d % 6 == 0 else [], "rules_accepted": ["B:mini:c05.s01.k01"]}) + "\n")
    srv = ui.make_server("127.0.0.1", args.port, TOKEN)
    if WITH_BROWSER:
        from http.server import ThreadingHTTPServer
        from tests.test_webview import Site, chromium
        from writing_core import panes, webview
        site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        site_url = f"http://127.0.0.1:{site.server_address[1]}/"
        panes.quillbot_url = lambda _ui: site_url                      # the stand-in site plays both QuillBot and Overleaf
        panes.overleaf_url = lambda proj, override=None: site_url + "login"
        if WITH_REAL:
            os.environ["OLS_REAL_CHROMIUM"] = chromium()
        srv.app._browser = webview.Browsers(Path(home) / "browser", exe=chromium(), allow_private=True, real_sites=("quillbot", "overleaf") if WITH_REAL else ())
        print(f"SITE {site_url}", flush=True)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    print(f"URL http://127.0.0.1:{args.port}/?token={TOKEN}", flush=True)
    print(f"HOME {home}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
