import io
import json
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import writing as cli
from tests.fake_openai import FakeOpenAI
from tests.helpers import IsolatedHome, common
from tests.test_book import FIXTURE, needs_pdf
from tests.test_paper import paper
from tests.test_revise import reply
from tests.test_ui_server import UiBase
from writing_core import book_index, config, events, openai_client, preferences, retrieve, revise, rule_cards

R_BAD, R_GOOD = "B:mini:c05.s02.k01", "B:mini:c05.s01.k01"


def decisions(proj, rows):
    config.ensure_paper_dir(proj)
    with open(config.paper_dir(proj) / "decisions.jsonl", "w", encoding="utf-8") as f:
        for i, (status, rules, old, new, reasons) in enumerate(rows):
            f.write(json.dumps({"ts": 1000 + i, "proposal": "p", "hunk": f"u1.h{i}", "status": status, "rules": rules, "old": old, "new": new, "reasons": reasons}) + "\n")


class Preferences(IsolatedHome):
    def test_empty_history_adds_nothing(self):
        _, proj = paper(self)
        self.assertEqual(preferences.digest(proj), "")
        self.assertEqual(preferences.stats(proj), {"accepted": 0, "rejected": 0})

    def test_a_rule_rejected_repeatedly_is_called_out_and_examples_are_shown(self):
        _, proj = paper(self)
        decisions(proj, [("rejected", [R_BAD], "Changed to past tense here.", "Changed to present tense.", ["tense"]),
                         ("rejected", [R_BAD], "We observed yield.", "Yield was observed.", ["voice"]),
                         ("rejected", [R_BAD], "It was big.", "It was substantial.", []),
                         ("accepted", [R_BAD], "a", "b", []),
                         ("accepted", [R_GOOD], "Consent was obtained.", "Farmers gave consent.", ["active voice"]),
                         ("rejected", [R_GOOD], "x", "y", [])])
        d = preferences.digest(proj)
        self.assertIn(f"rejected changes based on book rule {R_BAD} 3 times (accepted 1)", d)
        self.assertNotIn(f"book rule {R_GOOD}", d)               # rejected once only: not a pattern yet
        self.assertIn('"It was big." -> "It was substantial."', d)
        self.assertIn("Recently accepted", d)
        self.assertIn('"Consent was obtained." -> "Farmers gave consent."', d)
        self.assertLessEqual(len(d), preferences.MAX_CHARS)
        self.assertEqual(preferences.stats(proj), {"accepted": 2, "rejected": 4})

    def test_long_histories_stay_inside_the_budget(self):
        _, proj = paper(self)
        decisions(proj, [("rejected", [R_BAD], "o" * 400, "n" * 400, ["r" * 300]) for _ in range(80)])
        self.assertLessEqual(len(preferences.digest(proj)), preferences.MAX_CHARS)

    def test_forget_erases_only_the_log(self):
        _, proj = paper(self)
        decisions(proj, [("accepted", [], "a", "b", []), ("rejected", [], "c", "d", [])])
        self.assertEqual(preferences.forget(proj), 2)
        self.assertEqual(preferences.digest(proj), "")
        self.assertEqual(preferences.forget(proj), 0)
        self.assertTrue((Path(proj["path"]) / "main.tex").exists())


@needs_pdf
class PreferencesTravel(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.oa = FakeOpenAI("responses")
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url})
        book_index.ingest(FIXTURE, "mini")
        self.a = paper(self, "alpha")
        self.b = paper(self, "beta")

    def tearDown(self):
        self.oa.close()
        super().tearDown()

    def payload(self, ref):
        r = revise.run(*ref, "tighten my methods", part="methods", dry_run=True)
        return json.dumps(r["payloads"])

    def test_the_digest_rides_in_this_papers_request_only_and_is_visible_in_the_preview(self):
        decisions(self.a[1], [("rejected", [R_BAD], "Alpha secret phrase one.", "Alpha secret phrase two.", [])] * 3)
        pa = self.payload(self.a)
        self.assertIn("author_preferences", pa)
        self.assertIn("Alpha secret phrase one", pa)
        pb = self.payload(self.b)
        self.assertNotIn("author_preferences", pb)
        self.assertNotIn("Alpha secret phrase", pb)
        preferences.forget(self.a[1])
        self.assertNotIn("author_preferences", self.payload(self.a))

    def test_recording_real_decisions_feeds_the_next_pass(self):
        config.set_paper_setting(self.a[1], "dry_run", False)
        for _ in range(2):  # two passes, the author rejects the change both times
            self.oa.script = [reply(("We sampled 40 farms by stratified random sampling.", "We drew a stratified random sample of 40 farms."),
                                    changes=[{"before": "We sampled 40 farms by stratified random sampling", "after": "We drew a stratified random sample of 40 farms",
                                              "reason": "x", "rule": R_GOOD}])]
            p = revise.run(*self.a, "tighten my methods", part="methods.data")["proposal"]
            revise.record(*self.a, p["id"], accepted=[], rejected="all")
        d = preferences.digest(self.a[1])
        self.assertIn(f"book rule {R_GOOD} 2 times", d)
        config.set_paper_setting(self.a[1], "dry_run", True)
        self.assertIn(f"book rule {R_GOOD} 2 times", self.payload(self.a))


@needs_pdf
class RuleCards(IsolatedHome):
    def setUp(self):
        super().setUp()
        openai_client.SLEEP = lambda s: None
        self.oa = FakeOpenAI("responses")
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url, "prices": {"input_per_1m": 1.0, "output_per_1m": 2.0}})
        book_index.ingest(FIXTURE, "mini")

    def tearDown(self):
        self.oa.close()
        openai_client.SLEEP = __import__("time").sleep
        super().tearDown()

    def cards_for(self, body, rules=("Describe every procedure so it can be repeated.", "State the sampling method plainly.")):
        user = body["input"]
        ids = re.findall(r'<excerpt id="([^"]+)"', user)
        return {"cards": [{"id": i, "rules": list(rules)} for i in ids]}

    def test_plan_counts_batches_and_sends_nothing(self):
        st = rule_cards.status("mini")
        self.assertEqual((st["chunks"], st["with_cards"], st["todo"]), (20, 0, 20))
        pl = rule_cards.plan("mini")
        self.assertEqual((pl["chunks"], pl["calls"]), (20, 5))
        self.assertGreater(pl["est_tokens_in"], 2000)
        self.assertIsNotNone(pl["est_cost"])
        self.assertEqual(pl["payload_hash"], rule_cards.plan("mini")["payload_hash"])  # stable
        self.assertEqual(self.oa.requests, [])

    def test_without_the_confirmation_nothing_is_sent(self):
        for bad in (None, "", "nope"):
            with self.assertRaises(ValueError):
                rule_cards.run("mini", bad)
        self.assertEqual(self.oa.requests, [])
        self.assertEqual(rule_cards.status("mini")["with_cards"], 0)

    def test_run_writes_cards_rebuilds_search_and_is_resumable(self):
        self.oa.script = [lambda b: self.cards_for(b)] * 5
        pl = rule_cards.plan("mini")
        seen = []
        res = rule_cards.run("mini", pl["payload_hash"], progress=lambda i, n: seen.append((i, n)))
        self.assertEqual((res["status"], res["written"], res["with_cards"], res["todo"]), ("done", 20, 20, 0))
        self.assertEqual(seen[-1], (5, 5))
        self.assertEqual(len(self.oa.requests), 5)
        chunks = book_index.read_chunks("mini")
        self.assertTrue(all(c["rule_card"].startswith("- Describe every procedure") for c in chunks))
        self.assertEqual(book_index.manifest("mini")["rule_cards"], 20)
        self.assertEqual(book_index.manifest("mini")["n_chunks"], 20)  # enrichment, not re-chunking
        self.assertEqual(rule_cards.run("mini", "anything")["status"], "nothing_to_do")
        ev = [e for e in events.read(book_index.paths("mini")["dir"], types={"book_ingest"}) if e.get("stage") == "rule_cards"][-1]
        self.assertEqual((ev["chunks"], ev["calls"], ev["tokens_in"], ev["tokens_out"]), (20, 5, 600, 300))
        self.assertEqual(ev["cost"], round((600 * 1.0 + 300 * 2.0) / 1e6, 6))

    def test_a_failure_midway_keeps_the_finished_batches_and_resumes_later(self):
        self.oa.script = [lambda b: self.cards_for(b), lambda b: (401, {"error": {"message": "key revoked"}})]
        res = rule_cards.run("mini", rule_cards.plan("mini")["payload_hash"])
        self.assertEqual(res["status"], "stopped")
        self.assertIn("rejected the API key", res["error"])
        self.assertEqual((res["with_cards"], res["todo"]), (4, 16))
        self.oa.script = [lambda b: self.cards_for(b)] * 4
        res = rule_cards.run("mini", rule_cards.plan("mini")["payload_hash"])
        self.assertEqual((res["status"], res["with_cards"]), ("done", 20))

    def test_model_output_is_validated(self):
        def odd(body):
            ids = re.findall(r'<excerpt id="([^"]+)"', body["input"])
            return {"cards": [{"id": ids[0], "rules": ["  spaced   out  rule  ", "", "x" * 500] + [f"r{i}" for i in range(20)]},
                              {"id": "B:mini:c99.s00.k01", "rules": ["invented chunk"]}, {"id": ids[1], "rules": []}]}
        self.oa.script = [odd] + [lambda b: {"cards": []}] * 4
        res = rule_cards.run("mini", rule_cards.plan("mini")["payload_hash"])
        chunks = {c["id"]: c for c in book_index.read_chunks("mini")}
        first = next(c for c in chunks.values() if c["rule_card"])
        lines = first["rule_card"].split("\n")
        self.assertEqual(lines[0], "- spaced out rule")
        self.assertLessEqual(len(lines), 8)
        self.assertTrue(all(len(x) <= 242 for x in lines))
        self.assertEqual(res["with_cards"], 1)   # the invented id, the empty list and the empty batches wrote nothing

    def test_cards_make_chunks_findable_and_replace_raw_text_in_requests(self):
        self.oa.script = [lambda b: self.cards_for(b, ("Always report an effect size with a confidence interval.",))] * 5
        rule_cards.run("mini", rule_cards.plan("mini")["payload_hash"])
        res = retrieve.book_query("mini", "effect size confidence interval", part="style")   # words only the cards contain, style part ignored? routed anyway
        self.assertTrue(res["chunks"])
        self.assertTrue(any("effect size" in c["rule_card"] for c in res["chunks"]))
        name, proj = paper(self)
        pv = revise.run(name, proj, "tighten my methods", part="methods", dry_run=True)
        user = pv["payloads"][0]["request"]["input"]
        self.assertIn("Always report an effect size with a confidence interval.", user)
        self.assertNotIn("Name each statistical test and the software and version used", user)   # raw book sentence no longer travels

    def test_cli_dry_run_then_send(self):
        self.oa.script = [lambda b: self.cards_for(b)] * 5
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["book-rules", "--id", "mini"]), 0)
        text = out.getvalue()
        self.assertIn("DRY RUN: nothing was sent", text)
        self.assertEqual(self.oa.requests, [])
        h = re.search(r"--send ([0-9a-f]{64})", text).group(1)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["book-rules", "--id", "mini", "--send", h]), 0)
        self.assertIn("done: 20/20 chunks now have rule cards", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["book-rules", "--id", "mini"])
        self.assertIn("already has a rule card", out.getvalue())


@needs_pdf
class RuleCardsInTheApp(UiBase):
    def setUp(self):
        super().setUp()
        self.oa = FakeOpenAI("responses")
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.oa.url})
        book_index.ingest(FIXTURE, "mini")
        self.c.login()

    def tearDown(self):
        self.oa.close()
        super().tearDown()

    def test_status_preview_and_a_job_with_progress(self):
        st, s = self.c.get("/api/book/mini/rules")
        self.assertEqual((s["chunks"], s["with_cards"]), (20, 0))
        st, pl = self.c.post("/api/book/mini/rules/preview", {})
        self.assertEqual((pl["calls"], pl["chunks"]), (5, 20))
        self.assertNotIn("sample", pl)                       # the page gets the plan, not the request bodies
        self.assertEqual(self.oa.requests, [])
        st, r = self.c.post("/api/book/mini/rules/run", {"confirm": "wrong"})
        self.assertEqual(self.wait_job(r["job"])["status"], "error")
        self.assertEqual(self.oa.requests, [])
        self.oa.script = [lambda b: {"cards": [{"id": i, "rules": ["Be clear."]} for i in re.findall(r'<excerpt id="([^"]+)"', b["input"])]}] * 5
        st, r = self.c.post("/api/book/mini/rules/run", {"confirm": pl["payload_hash"]})
        j = self.wait_job(r["job"])
        self.assertEqual((j["status"], j["result"]["status"], j["result"]["with_cards"]), ("done", "done", 20))
        self.assertEqual(j["progress"], [5, 5])
        self.assertEqual(self.c.get("/api/book/mini/rules")[1]["todo"], 0)
        self.assertEqual(self.c.post("/api/book/nope/rules/preview", {})[0], 404)

    def test_preferences_and_forget_endpoints(self):
        name, proj = paper(self)
        decisions(proj, [("rejected", [R_BAD], "o1", "n1", [])] * 3)
        st, p = self.c.get("/api/paper/alpha/preferences")
        self.assertIn(R_BAD, p["digest"])
        self.assertEqual((p["accepted"], p["rejected"]), (0, 3))
        st, f = self.c.post("/api/paper/alpha/forget", {})
        self.assertEqual(f["forgotten"], 3)
        self.assertEqual(self.c.get("/api/paper/alpha/preferences")[1]["digest"], "")
        self.assertEqual(self.c.post("/api/paper/zzz/forget", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
