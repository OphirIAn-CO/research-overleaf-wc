"""Peer review from the chat: always asks first, reads the whole paper once, keeps the report, and turns issues into 'Fix this'."""
import json
import os
import subprocess
import unittest
from pathlib import Path

from tests.fake_openai import FakeOpenAI
from tests.helpers import IsolatedHome, common
from tests.test_paper import paper
from tests.test_ui_server import UiBase
from tests.test_v2_chat import MAIN
from writing_core import chat, config, events, openai_client, paper_index, review

RUBRIC = "# Peer reviewer\nScore five dimensions from 1 to 5. Verdict: accept, minor revision, major revision or reject.\nRUBRIC-MARKER-XYZ\n"
REPORT = """## Summary
A clear study of soil moisture and yield.

## Scores
Originality 4, Methodology 3.

```json
{"verdict": "minor revision", "scores": {"methodology": 3, "clarity": 4},
 "issues": [
  {"severity": "major", "where": "Study area", "quote": "Each region has a distinct climate and soil.", "problem": "The regions are not characterised.", "suggestion": "Give rainfall and soil type for each region."},
  {"severity": "minor", "where": "Results", "quote": "", "problem": "No uncertainty is reported.", "suggestion": "Add confidence intervals."},
  {"severity": "suggestion", "where": "A section that does not exist", "quote": "", "problem": "Consider a limitations paragraph.", "suggestion": "Add one."},
  {"severity": "huh", "where": "", "quote": "", "problem": "", "suggestion": ""}
 ]}
```
"""


class ReviewEnv:
    """The rubric file, the fake OpenAI and an openai.json: set up after the temp home exists, undone before it goes."""

    def env_up(self):
        self.rubric = self.tmp / "rubric" / "peer_reviewer_agent.md"
        self.rubric.parent.mkdir()
        self.rubric.write_text(RUBRIC)
        self._env = os.environ.get("OLS_RUBRIC")
        os.environ["OLS_RUBRIC"] = str(self.rubric)
        openai_client.SLEEP = lambda s: None
        self.oa = FakeOpenAI("responses")
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url, "prices": {"input_per_1m": 1.0, "output_per_1m": 2.0}})

    def env_down(self):
        self.oa.close()
        if self._env is None:
            os.environ.pop("OLS_RUBRIC", None)
        else:
            os.environ["OLS_RUBRIC"] = self._env


class Base(IsolatedHome, ReviewEnv):
    def setUp(self):
        super().setUp()
        self.env_up()

    def tearDown(self):
        self.env_down()
        super().tearDown()


class Engine(Base):
    def setUp(self):
        super().setUp()
        text = MAIN.replace("Crop yield depends on water and on soil.", "Crop yield depends on water and on soil. % SECRET NOTE TO SELF")
        self.name, self.proj = paper(self, main=text)
        (Path(self.proj["path"]) / "main.tex").write_text(text)
        paper_index.refresh(self.name, self.proj)

    def test_the_rubric_is_found_or_the_review_says_why_not(self):
        self.assertEqual(review.rubric_path(), self.rubric)
        self.rubric.unlink()
        os.environ.pop("OLS_RUBRIC")
        with self.assertRaises(review.ReviewError) as cm:
            review.plan(self.name, self.proj)
        self.assertIn("academic-paper", str(cm.exception))

    def test_plan_reports_size_cost_and_refuses_what_is_too_long(self):
        pl = review.plan(self.name, self.proj)
        self.assertEqual((pl["scope"], pl["chunk_ids"], pl["model"]), ("the whole paper", None, "m"))
        self.assertGreater(pl["tokens"], 100)
        self.assertIsNotNone(pl["est_cost"])
        self.assertEqual(self.oa.requests, [])
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url, "review_max_input_tokens": 50})
        with self.assertRaises(review.ReviewError) as cm:
            review.plan(self.name, self.proj)
        self.assertIn("Ask for a part", str(cm.exception))

    def test_a_review_reads_the_whole_paper_with_the_rubric_and_keeps_the_report(self):
        self.oa.script = [REPORT]
        steps = []
        res = review.run(self.name, self.proj, progress=lambda s: steps.append([i["key"] for i in s]))
        body = self.oa.requests[0]["body"]
        self.assertNotIn("text", body)                                           # free text, not the JSON schema of an edit
        self.assertEqual(body["max_output_tokens"], 6000)
        self.assertIn("RUBRIC-MARKER-XYZ", body["instructions"])
        user = body["input"]
        for needle in ("Crop yield depends on water", "regression of yield", "Yield rose with moisture", "smith2020 | 2020 | Water and Yield", "doe2019 | 2019 | Soil"):
            self.assertIn(needle, user)
        self.assertNotIn("SECRET NOTE", user)                                    # comments never leave
        self.assertNotIn("sk-test", json.dumps(body))
        self.assertEqual((res["verdict"], res["scores"]["methodology"]), ("minor revision", 3))
        self.assertNotIn("```json", res["report"])
        self.assertIn("A clear study", res["report"])
        self.assertEqual([i["key"] for i in res["steps"]], ["read", "rubric", "ask", "save"])
        self.assertEqual(steps[0], ["read"])
        saved = Path(self.proj["path"]) / res["path"]
        self.assertIn("Peer review of the whole paper", saved.read_text())
        self.assertEqual(subprocess.run(["git", "-C", self.proj["path"], "status", "--porcelain"], capture_output=True, text=True).stdout.strip(), "")
        paper_index.refresh(self.name, self.proj, force=True)
        self.assertEqual(paper_index.peek(self.proj)["counts"].get("review"), 1)   # later improve requests can use the reviewer's comments
        ev = events.read(config.paper_dir(self.proj), types={"review"})[-1]
        self.assertEqual((ev["verdict"], ev["issues"], ev["status"]), ("minor revision", 3, "done"))
        self.assertNotIn("regression", json.dumps(ev))                          # metadata only

    def test_issues_point_at_the_place_they_are_about(self):
        self.oa.script = [REPORT]
        issues = review.run(self.name, self.proj)["issues"]
        self.assertEqual([i["severity"] for i in issues], ["major", "minor", "suggestion"])       # the empty one is dropped
        tex = {c["id"]: c for c in paper_index.list_chunks(self.name, self.proj)["chunks"] if c["kind"] == "tex"}
        self.assertEqual([tex[i]["section_path"].split(" > ")[-1] for i in issues[0]["chunk_ids"]], ["Study area"])     # found by the quote
        self.assertEqual([tex[i]["section_path"].split(" > ")[-1] for i in issues[1]["chunk_ids"]], ["Results"])        # found by the section name
        self.assertEqual(issues[2]["chunk_ids"], [])

    def test_a_review_of_a_part_sends_only_that_part(self):
        ids, scope = chat.review_scope(self.name, self.proj, "review the methods")
        self.assertEqual(scope, "the methods")
        self.oa.script = [REPORT]
        review.run(self.name, self.proj, ids, scope)
        user = self.oa.requests[0]["body"]["input"]
        self.assertIn("regression of yield", user)
        self.assertNotIn("Crop yield depends", user)
        self.assertIn('scope="the methods"', user)

    def test_scope_words(self):
        self.assertEqual(chat.review_scope(self.name, self.proj, "peer review my paper"), (None, "the whole paper"))
        self.assertEqual(chat.review_scope(self.name, self.proj, "please critique this draft")[1], "the whole paper")
        ids, scope = chat.review_scope(self.name, self.proj, "act as a reviewer for the study area section")
        self.assertIn("Study area", scope)
        self.assertTrue(ids)

    def test_a_changed_paper_or_a_bad_answer(self):
        with self.assertRaises(review.ReviewError):
            review.run(self.name, self.proj, confirm="not-the-hash")
        self.assertEqual(self.oa.requests, [])
        self.oa.script = ["   "]
        with self.assertRaises(review.ReviewError):
            review.run(self.name, self.proj)

    def test_split_report(self):
        self.assertEqual(review.split_report("just text"), ("just text", {}))
        r, b = review.split_report("body\n```json\n{not json}\n```")
        self.assertEqual(b, {})
        self.assertIn("body", r)
        r, b = review.split_report("body\n```json\n{\"verdict\": \"x\"}\n```\n")
        self.assertEqual((r, b), ("body", {"verdict": "x"}))


class ChatFlow(UiBase, ReviewEnv):
    def setUp(self):
        super().setUp()
        self.env_up()
        self.name, self.proj = paper(self, main=MAIN)
        self.c.login()

    def tearDown(self):
        import time
        t0 = time.time()
        while self.srv.app.jobs.running() and time.time() - t0 < 15:
            time.sleep(0.05)
        self.env_down()
        super().tearDown()

    def send(self, text, **kw):
        st, r = self.c.post("/api/chat/send", {"paper": "alpha", "text": text, **kw})
        self.assertEqual(st, 200, r)
        return r

    def test_a_review_always_waits_for_a_yes_then_runs_and_fix_this_makes_an_edit(self):
        r = self.send("peer review my paper")
        plan = r["messages"][1]
        self.assertEqual((plan["kind"], plan["data"]["state"], r.get("job")), ("review_plan", "waiting", None))
        self.assertEqual(self.oa.requests, [])                                   # nothing was sent, whatever the edit setting says
        self.oa.script = [REPORT]
        st, run = self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})
        j = self.wait_job(run["job"])
        self.assertEqual((j["status"], j["result"]["issues"]), ("done", 3), j)
        self.assertEqual([s["key"] for s in j["steps"]], ["read", "rubric", "ask", "save"])
        msgs = self.c.get("/api/chat?paper=alpha")[1]["thread"]["messages"]
        rv = [m for m in msgs if m["kind"] == "review"][0]
        self.assertEqual((rv["data"]["verdict"], rv["data"]["scope"]), ("minor revision", "the whole paper"))
        self.assertEqual(self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})[0], 409)
        # 'Fix this' is an ordinary improve request aimed at the place the issue points to
        issue = rv["data"]["issues"][0]
        self.oa.script = [lambda b: {"approach": "x", "revised_text": __import__("tests.fake_openai", fromlist=["passage_of"]).passage_of(b), "changes": [], "questions": []}]
        r2 = self.send("Address this reviewer comment: " + issue["problem"] + " " + issue["suggestion"], chunk_ids=issue["chunk_ids"])
        self.assertEqual(r2["messages"][1]["kind"], "plan")
        self.assertTrue(r2["job"])
        self.wait_job(r2["job"])

    def test_cancel_a_review_and_the_missing_rubric(self):
        plan = self.send("review the methods")["messages"][1]
        self.assertIn("methods", plan["data"]["plan"]["scope"])
        self.assertEqual(self.c.post("/api/chat/cancel", {"paper": "alpha", "message": plan["id"]})[0], 200)
        self.assertEqual(self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})[0], 409)
        self.rubric.unlink()
        os.environ.pop("OLS_RUBRIC")
        m = self.send("peer review my paper")["messages"][1]
        self.assertEqual(m["kind"], "error")
        self.assertIn("academic-paper", m["text"])

    def test_the_paper_changing_before_the_yes_stops_the_review(self):
        plan = self.send("peer review my paper")["messages"][1]
        main = Path(self.proj["path"]) / "main.tex"
        main.write_text(main.read_text().replace("Farmers need guidance", "Farmers want guidance"))
        st, run = self.c.post("/api/chat/run", {"paper": "alpha", "message": plan["id"]})
        j = self.wait_job(run["job"])
        self.assertEqual(j["status"], "error")
        self.assertIn("changed", j["error"])
        self.assertEqual(self.oa.requests, [])

    def test_missing_openai_setup_is_explained(self):
        (common.HOME / "openai.json").unlink()
        m = self.send("peer review my paper")["messages"][1]
        self.assertEqual(m["kind"], "error")
        self.assertIn("Settings", m["text"])


if __name__ == "__main__":
    unittest.main()
