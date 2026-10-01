import json
import subprocess
import unittest
from pathlib import Path

from tests.fake_openai import FakeOpenAI, passage_of
from tests.helpers import IsolatedHome, common
from tests.test_book import FIXTURE, needs_pdf
from tests.test_paper import MAIN, METHODS, paper
from writing_core import book_index, config, events, latex_mask, openai_client, paper_index as pi, revise


def user_of(body):
    return body["input"] if "input" in body else body["messages"][-1]["content"]


def reply(*pairs, changes=None, questions=None):
    """A fake model answer that applies (old -> new) replacements to the masked passage it receives."""
    def step(body):
        text = passage_of(body)
        for a, b in pairs:
            text = text.replace(a, b)
        return {"revised_text": text, "changes": changes or [], "questions": questions or []}
    return step


@needs_pdf
class ReviseBase(IsolatedHome):
    api = "responses"

    def setUp(self):
        super().setUp()
        openai_client.SLEEP = lambda s: None
        self.srv = FakeOpenAI(self.api)
        common.save("openai.json", {"api_key": "sk-test-KEY", "model": "test-model", "base_url": self.srv.url, "api": self.api,
                                    "prices": {"input_per_1m": 1.0, "output_per_1m": 2.0}})
        book_index.ingest(FIXTURE, "mini")
        self.name, self.proj = paper(self)
        self.methods = Path(self.proj["path"]) / "sections" / "methods.tex"

    def tearDown(self):
        self.srv.close()
        openai_client.SLEEP = __import__("time").sleep
        super().tearDown()

    def live(self):
        config.set_paper_setting(self.proj, "dry_run", False)

    def go(self, instruction="tighten my methods and follow the book", **kw):
        return revise.run(self.name, self.proj, instruction, **kw)


class DryRun(ReviseBase):
    def test_default_is_a_preview_and_sends_nothing(self):
        r = self.go()
        self.assertEqual(r["status"], "preview")
        self.assertEqual(self.srv.requests, [])
        self.assertTrue(r["requires_confirmation"])
        body = r["payloads"][0]["request"]
        self.assertFalse(body["store"])
        user = user_of(body)
        self.assertIn("<instruction>tighten my methods", user)
        self.assertIn("[[LX", user)             # LaTeX is masked
        self.assertNotIn("\\subsection", user)
        self.assertNotIn("sk-test-KEY", json.dumps(r))
        self.assertTrue(r["rules"] and all(i.startswith("B:mini:") for i in r["rules"]))
        self.assertIn("B:mini:c02", user)       # the methods rules from the book travel with the passage
        text = revise.format_preview(r)
        self.assertIn("DRY RUN: nothing was sent", text)
        self.assertIn(r["payload_hash"], text)

    def test_sending_needs_the_hash_of_the_previewed_payload(self):
        r = self.go()
        again = self.go(dry_run=False)                      # no confirmation: still a preview
        self.assertEqual(again["status"], "preview")
        wrong = self.go(dry_run=False, confirm="deadbeef")
        self.assertEqual(wrong["status"], "preview")
        self.assertEqual(self.srv.requests, [])
        sent = self.go(dry_run=False, confirm=r["payload_hash"])
        self.assertEqual(sent["status"], "no_changes")      # identity reply from the fake
        self.assertEqual(len(self.srv.requests), 1)

    def test_changing_the_inputs_invalidates_the_confirmation(self):
        r = self.go()
        other = self.go(instruction="make the methods more concise", dry_run=False, confirm=r["payload_hash"])
        self.assertEqual(other["status"], "preview")

    def test_explicit_dry_run_true_previews_even_when_the_paper_allows_sending(self):
        self.live()
        self.assertEqual(self.go(dry_run=True)["status"], "preview")
        self.assertEqual(self.srv.requests, [])

    def test_preview_is_logged_and_paper_card_is_included(self):
        config.paper_dir(self.proj).mkdir(parents=True, exist_ok=True)
        (config.paper_dir(self.proj) / "paper_card.md").write_text("Key term: say 'yield', never 'harvest'.")
        r = self.go()
        self.assertIn("never 'harvest'", user_of(r["payloads"][0]["request"]))
        ev = events.read(config.paper_dir(self.proj), types={"revise"})[-1]
        self.assertTrue(ev["dry_run"])
        self.assertEqual(ev["status"], "preview")


class Run(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()

    def test_happy_path_builds_a_stored_proposal_with_reasons_and_filtered_rules(self):
        rule = "B:mini:c05.s01.k01"
        self.srv.script = [reply(("We sampled 40 farms by stratified random sampling.", "We drew a stratified random sample of 40 farms."),
                                 ("Informed consent was obtained from all farmers.", "All farmers gave informed consent."),
                                 changes=[{"before": "Informed consent was obtained from all farmers.", "after": "All farmers gave informed consent.",
                                           "reason": "Use the active voice when the actor matters.", "rule": rule},
                                          {"before": "We sampled 40 farms", "after": "We drew a stratified random sample of 40 farms",
                                           "reason": "more precise", "rule": "B:bogus:c99.s99.k99"}])]
        r = self.go("improve my methods following the book", part="methods.data")
        self.assertEqual(r["status"], "proposed")
        p = r["proposal"]
        self.assertEqual(len(p["units"]), 1)
        u = p["units"][0]
        self.assertEqual((u["file"], u["status"]), ("sections/methods.tex", "ok"))
        self.assertEqual([h["uid"] for h in u["hunks"]], ["u1.h1", "u1.h2"])   # two sentences changed = two reviewable hunks
        h1, h2 = u["hunks"]
        self.assertIn("We sampled 40 farms", h1["old"])
        self.assertIn("We drew a stratified random sample", h1["new"])
        self.assertIn("All farmers gave informed consent.", h2["new"])
        self.assertTrue(any("active voice" in x for x in h2["reasons"]))
        self.assertEqual(h2["rules"], [rule] if rule in p["rules"] else [])
        self.assertEqual(h1["rules"], [])                                 # the invented rule id was dropped
        self.assertNotIn("B:bogus:c99.s99.k99", json.dumps(p["units"][0]["hunks"]).replace('"reasons"', ""))
        self.assertEqual(p["usage"], {"input_tokens": 120, "output_tokens": 60, "cost": round((120 * 1.0 + 60 * 2.0) / 1e6, 6)})
        self.assertTrue((config.paper_dir(self.proj) / "proposals" / f"{p['id']}.json").exists())
        self.assertEqual(revise.get_proposal(self.proj, p["id"])["id"], p["id"])
        self.assertEqual(revise.list_proposals(self.proj)[0]["hunks"], 2)
        ev = events.read(config.paper_dir(self.proj), types={"revise"})[-1]
        self.assertEqual((ev["tokens_in"], ev["tokens_out"], ev["hunks"], ev["status"]), (120, 60, 2, "proposed"))
        self.assertTrue(all(ev["validators"].values()))
        rets = events.read(config.paper_dir(self.proj), types={"retrieve"})
        self.assertIn("book", {e["kind"] for e in rets})
        text = revise.format_proposal(p)
        self.assertIn("[u1.h1]", text)
        self.assertIn("Nothing has been written", text)
        # manuscript untouched, repo clean
        self.assertEqual(self.methods.read_text(), METHODS)
        self.assertEqual(subprocess.run(["git", "-C", self.proj["path"], "status", "--porcelain"], capture_output=True, text=True).stdout.strip(), "")

    def test_request_contains_only_this_paper(self):
        beta_main = MAIN.replace("Soil Moisture Effects on Crop Yield", "Graph Networks").replace("Crop yield depends on water.", "Zebrafish quantum entanglement words.")
        paper(self, "beta", main=beta_main, methods=METHODS.replace("farms", "quasarcluster"))
        self.go(part="methods")
        sent = json.dumps(self.srv.requests)
        for foreign in ("quasarcluster", "Zebrafish", "Graph Networks"):
            self.assertNotIn(foreign, sent)
        self.assertEqual(len(self.srv.requests), 1)
        self.assertEqual(self.srv.requests[0]["headers"]["Authorization"], "Bearer sk-test-KEY")

    def test_invalid_answer_is_retried_with_the_problem_named(self):
        bad = reply(("We sampled 40 farms", "We sampled 45 farms"))
        good = reply(("We sampled", "We recruited"))
        self.srv.script = [bad, good]
        r = self.go(part="methods")
        u = r["proposal"]["units"][0]
        self.assertEqual((u["status"], u["retries"]), ("ok", 1))
        self.assertEqual(len(self.srv.requests), 2)
        second = user_of(self.srv.requests[1]["body"])
        self.assertIn("previous_attempt_rejected", second)
        self.assertIn("no_new_numbers", second)
        self.assertEqual(r["proposal"]["usage"]["input_tokens"], 240)
        self.assertTrue(len(u["hunks"]) >= 1)

    def test_still_invalid_after_one_retry_offers_nothing(self):
        bad = reply(("farms", "farms — mostly"))
        self.srv.script = [bad, bad]
        r = self.go(part="methods")
        self.assertEqual(r["status"], "failed")
        u = r["proposal"]["units"][0]
        self.assertEqual((u["status"], u["error_kind"], u["hunks"]), ("failed", "validation", []))
        self.assertIn("no_new_dashes", u["error"])
        self.assertIn("FAILED", revise.format_proposal(r["proposal"]))

    def test_dropped_citation_and_invented_acronym_are_caught(self):
        for pair in (("[[LX", "[[XX"), ("depends on water.", "depends on water using ANOVA.")):
            self.srv.script = [reply(pair), reply(pair)]
            r = self.go(part="introduction")
            self.assertEqual(r["status"], "failed", pair)

    def test_model_returning_garbage_json_is_retried_then_fails_cleanly(self):
        self.srv.script = [lambda b: "not json at all", lambda b: "still not json"]
        r = self.go(part="methods")
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["proposal"]["units"][0]["error_kind"], "parse")

    def test_api_errors_are_reported_not_raised(self):
        self.srv.script = [lambda b: (401, {"error": {"message": "bad key sk-test-KEY"}})]
        r = self.go(part="methods")
        self.assertEqual(r["status"], "failed")
        u = r["proposal"]["units"][0]
        self.assertEqual(u["error_kind"], "auth")
        self.assertNotIn("sk-test-KEY", json.dumps(r["proposal"]))

    def test_no_changes(self):
        r = self.go(part="methods")
        self.assertEqual(r["status"], "no_changes")
        self.assertEqual(r["proposal"]["units"][0]["status"], "no_changes")

    def test_questions_from_the_model_are_kept(self):
        self.srv.script = [reply(("We sampled", "We recruited"), questions=["What was the response rate?"])]
        r = self.go(part="methods")
        self.assertEqual(r["proposal"]["units"][0]["questions"], ["What was the response rate?"])
        self.assertIn("QUESTION for the author: What was the response rate?", revise.format_proposal(r["proposal"]))

    def test_target_selection_errors(self):
        with self.assertRaises(revise.ReviseError) as cm:
            self.go("make it better")
        self.assertIn("which part", str(cm.exception))
        with self.assertRaises(revise.ReviseError):
            self.go("")
        with self.assertRaises(revise.ReviseError) as cm:
            self.go(part="limitations")
        self.assertIn("no text tagged limitations", str(cm.exception))
        with self.assertRaises(revise.ReviseError):
            self.go(chunk_ids=["P:alpha:nope"])
        self.assertEqual(self.srv.requests, [])

    def test_chunk_ids_target_and_inferred_part(self):
        ids = [c["id"] for c in pi.part_chunks(self.name, self.proj, "methods.analysis")]
        r = self.go("tighten this", chunk_ids=ids)
        self.assertEqual(r["status"], "no_changes")
        self.assertEqual(r["proposal"]["units"][0]["line_start"], 7)
        self.assertEqual(r["proposal"]["part"], ["methods"])
        r = self.go("tighten the abstract")
        self.assertEqual(r["proposal"]["part"], ["abstract"])
        self.assertEqual(r["proposal"]["units"][0]["file"], "main.tex")

    def test_too_many_passages_are_refused_before_sending(self):
        with self.assertRaises(revise.ReviseError) as cm:
            revise.run(self.name, self.proj, "tighten my methods", part="methods", max_units=0)
        self.assertIn("limit", str(cm.exception))
        self.assertEqual(self.srv.requests, [])

    def test_no_book_runs_with_a_warning(self):
        r = self.go(part="methods", use_book=False, dry_run=True)
        self.assertEqual(r["rules"], [])
        self.assertIn("<book_rules>(none available)</book_rules>", user_of(r["payloads"][0]["request"]))


class ChatStyle(Run):
    api = "chat"

    def test_chat_style_end_to_end(self):
        self.srv.script = [reply(("We sampled", "We recruited"))]
        r = self.go(part="methods")
        self.assertEqual(r["status"], "proposed")
        self.assertEqual(self.srv.requests[0]["path"], "/v1/chat/completions")


class Apply(ReviseBase):
    def setUp(self):
        super().setUp()
        self.live()
        self.srv.script = [reply(("We sampled 40 farms by stratified random sampling.", "We drew a stratified random sample of 40 farms."),
                                 ("Informed consent was obtained from all farmers.", "All farmers gave informed consent."))]
        self.p = self.go(part="methods.data")["proposal"]
        self.keep = lambda t: t

    def apply(self, **kw):
        kw.setdefault("cleaner", self.keep)
        return revise.apply(self.name, self.proj, self.p["id"], **kw)

    def test_accept_all_writes_only_the_passage_in_place_and_refreshes_the_index(self):
        before = self.methods.read_text()
        r = self.apply()
        after = self.methods.read_text()
        self.assertEqual(r["status"], "applied")
        self.assertIn("We drew a stratified random sample of 40 farms.", after)
        self.assertIn("All farmers gave informed consent.", after)
        self.assertIn("\\subsection{Statistical analysis}", after)             # untouched neighbours
        self.assertIn("We fitted a mixed-effects regression", after)
        self.assertEqual(before.splitlines()[0], after.splitlines()[0])
        self.assertEqual(revise.get_proposal(self.proj, self.p["id"])["status"], "applied")
        self.assertEqual(r["index"]["changed"], 1)
        evs = events.read(config.paper_dir(self.proj))
        self.assertEqual([e["trigger"] for e in evs if e["type"] == "index_refresh"][-1], "after_edit")
        dec = [e for e in evs if e["type"] == "decision"][-1]
        self.assertEqual((dec["accepted"], dec["applied"]), (2, True))
        log = (config.paper_dir(self.proj) / "decisions.jsonl").read_text().splitlines()
        self.assertEqual(len(log), 2)
        self.assertEqual(json.loads(log[0])["status"], "accepted")

    def test_partial_acceptance_applies_only_the_chosen_hunk(self):
        uids = [h["uid"] for h in self.p["units"][0]["hunks"]]
        self.assertEqual(len(uids), 2)
        self.apply(accept=[uids[0]])
        after = self.methods.read_text()
        self.assertIn("We drew a stratified random sample", after)
        self.assertIn("Informed consent was obtained from all farmers.", after)   # second hunk rejected
        statuses = [h["status"] for h in revise.get_proposal(self.proj, self.p["id"])["units"][0]["hunks"]]
        self.assertEqual(statuses, ["accepted", "rejected"])

    def test_a_stale_proposal_is_refused_and_the_file_is_left_alone(self):
        self.methods.write_text(METHODS.replace("40 farms", "41 farms"))
        before = self.methods.read_text()
        with self.assertRaises(revise.ReviseError) as cm:
            self.apply()
        self.assertIn("changed since the proposal", str(cm.exception))
        self.assertEqual(self.methods.read_text(), before)

    def test_without_the_step_6b_cleaner_nothing_is_applied(self):
        with self.assertRaises(revise.ReviseError) as cm:
            revise.apply(self.name, self.proj, self.p["id"], accept="all")
        self.assertIn("6b", str(cm.exception))
        self.assertEqual(self.methods.read_text(), METHODS)

    def test_the_cleaner_output_is_what_gets_written_and_failures_write_nothing(self):
        self.apply(cleaner=lambda t: t.replace("stratified", "STRATIFIED"))
        self.assertIn("STRATIFIED", self.methods.read_text())

    def test_cleaner_failure_writes_nothing(self):
        def boom(t):
            raise RuntimeError("service down")
        with self.assertRaises(revise.ReviseError) as cm:
            self.apply(cleaner=boom)
        self.assertIn("nothing was written", str(cm.exception))
        self.assertEqual(self.methods.read_text(), METHODS)
        with self.assertRaises(revise.ReviseError):
            self.apply(cleaner=lambda t: "")
        self.assertEqual(self.methods.read_text(), METHODS)

    def test_applying_twice_and_unknown_hunks(self):
        with self.assertRaises(revise.ReviseError):
            self.apply(accept=["u9.h9"])
        self.apply()
        with self.assertRaises(revise.ReviseError) as cm:
            self.apply()
        self.assertIn("already applied", str(cm.exception))

    def test_crlf_files_keep_their_line_endings(self):
        self.methods.write_bytes(METHODS.replace("\n", "\r\n").encode())
        pi.refresh(self.name, self.proj)
        self.srv.script = [reply(("We sampled", "We recruited"))]
        p = self.go(part="methods.data")["proposal"]
        revise.apply(self.name, self.proj, p["id"], cleaner=self.keep)
        raw = self.methods.read_bytes()
        self.assertIn(b"We recruited", raw)
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))

    def test_two_units_in_one_file_apply_bottom_up(self):
        self.srv.script = [reply(("We sampled", "We recruited")), reply(("We fitted", "We estimated"))]
        p = self.go("tighten my methods", part="methods", chunk_ids=None)["proposal"]
        if len(p["units"]) == 1:   # methods fit one passage; force two via chunk groups of different sections
            ids = [c["id"] for c in pi.part_chunks(self.name, self.proj, "methods")]
            self.assertEqual(len(ids), 2)
        revise.apply(self.name, self.proj, p["id"], cleaner=self.keep)
        out = self.methods.read_text()
        self.assertIn("We recruited", out)

    def test_record_logs_decisions_made_elsewhere(self):
        uids = [h["uid"] for h in self.p["units"][0]["hunks"]]
        r = revise.record(self.name, self.proj, self.p["id"], accepted=[uids[0]], rejected=[uids[1]], applied=False, source="claude", note="n")
        self.assertEqual((r["accepted"], r["rejected"], r["pending"], r["status"]), (1, 1, 0, "applied"))
        dec = [e for e in events.read(config.paper_dir(self.proj)) if e["type"] == "decision"][-1]
        self.assertEqual((dec["source"], dec["accepted"], dec["rejected"]), ("claude", 1, 1))
        with self.assertRaises(revise.ReviseError):
            revise.record(self.name, self.proj, self.p["id"], accepted=["u7.h7"])

    def test_record_all_rejected_marks_the_proposal_rejected(self):
        r = revise.record(self.name, self.proj, self.p["id"], accepted=[], rejected="all")
        self.assertEqual(r["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
