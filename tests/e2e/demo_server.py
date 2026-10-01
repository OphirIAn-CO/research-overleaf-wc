#!/usr/bin/env python3
"""A self-contained demo of the web app for browser tests and screenshots: two fixture papers, the mini book imported, a fake OpenAI
and a fake step-6b service, and some synthetic history so the Monitor has something to draw. Nothing leaves this machine.

  python3 tests/e2e/demo_server.py [--port 8799]      prints the URL, runs until killed
"""
import argparse
import json
import os
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
home = tempfile.mkdtemp(prefix="ols-demo-")
os.environ["OVERLEAF_SKILL_HOME"] = home

from tests.fake_openai import FakeOpenAI, passage_of  # noqa: E402
from tests.helpers import common, git, load_ui_server  # noqa: E402
from tests.test_paper import BIB, MAIN, METHODS  # noqa: E402
from tests.test_ui_server import FakeWatermark  # noqa: E402
from writing_core import book_index, config, events, paper_index, retrieve  # noqa: E402
from tests.helpers import make_paper  # noqa: E402

TOKEN = "demo-token-" + "x" * 30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args()
    ui = load_ui_server()
    papers_dir = Path(home) / "papers"
    names = {}
    for name, tweak in (("soil-yield", lambda s: s), ("traffic-gnn", lambda s: s.replace("Soil Moisture Effects on Crop Yield", "Graph Networks for Traffic")
                                                         .replace("Crop yield depends on water.", "Traffic forecasting depends on graph structure."))):
        root = make_paper(papers_dir, name, tweak(MAIN), bib=BIB, extra={"sections/methods.tex": tweak(METHODS)})
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "init")
        common.register(str(root), name)
        names[name] = common.resolve(name)

    oa = FakeOpenAI("responses")

    def edit(body):
        t = passage_of(body)
        t = t.replace("We sampled 40 farms by stratified random sampling.", "We drew a stratified random sample of 40 farms.")
        t = t.replace("Informed consent was obtained from all farmers.", "All farmers gave informed consent.")
        t = t.replace("We fitted", "We estimated")
        return {"revised_text": t, "questions": ["What was the response rate?"], "changes": [
            {"before": "Informed consent was obtained from all farmers.", "after": "All farmers gave informed consent.", "reason": "Use the active voice when the actor matters.", "rule": "B:mini:c05.s01.k01"},
            {"before": "We sampled 40 farms by stratified random sampling.", "after": "We drew a stratified random sample of 40 farms.", "reason": "Name the sampling method plainly.", "rule": "B:mini:c02.s01.k01"}]}
    oa.script = [edit] * 50
    common.save("openai.json", {"api_key": "sk-demo", "model": "demo-model", "base_url": oa.url, "prices": {"input_per_1m": 2.0, "output_per_1m": 8.0}})
    wm = FakeWatermark()
    common.save("ui.json", {"watermark_url": wm.url, "idle_minutes": 240})

    pdf = ROOT / "tests" / "fixtures" / "mini_book.pdf"
    config.inbox_dir().mkdir(parents=True, exist_ok=True)
    (config.inbox_dir() / "mini_book.pdf").write_bytes(pdf.read_bytes())
    book_index.ingest(pdf, "mini")

    # real activity
    for n, (name, proj) in names.items():
        paper_index.query(name, proj, "improve my methods", source="claude")
        retrieve.book_query("mini", "how do I describe the statistical analysis", paper=name, paper_scope=config.paper_dir(proj), source="claude")
        paper_index.query(name, proj, "tense and passive voice in the discussion", source="claude")
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
