"""Answer and revise: the author replies to the model's questions (and may change what was asked); the new suggestion uses those replies, replaces the old one, and the
reviewer comment it belongs to follows it."""
import json
import unittest

from tests.fake_openai import passage_of
from tests.test_book import needs_pdf
from tests.test_revise import ReviseBase, reply, user_of
from tests.test_review_tracker import RESULTS, STUDY, Tracker
from writing_core import config, revise


def say(old_new, questions=None):
    return reply(*old_new, questions=questions or [])


@needs_pdf
class Engine(ReviseBase):
    ASK = "We sampled 40 farms by stratified random sampling."
    WITH_NUMBER = "We sampled 40 farms by stratified random sampling during 180 days."

    def setUp(self):
        super().setUp()
        config.set_paper_setting(self.proj, "dry_run", False)

    def test_the_answers_go_to_the_model_as_their_own_block_with_their_own_rule(self):
        self.srv.script = [reply((self.ASK, self.WITH_NUMBER))]
        r = self.go("say how long the sampling lasted", part="methods", facts="Question: How long did it last?\nAnswer: 180 days.")
        body = self.srv.requests[-1]["body"]
        self.assertIn("<author_answers>\nQuestion: How long did it last?\nAnswer: 180 days.\n</author_answers>", user_of(body))
        system = body.get("instructions") or body["messages"][0]["content"]
        self.assertIn("<author_answers> holds the author's replies", system)
        self.assertIn("The one exception is what <author_answers> states", system)
        self.assertEqual(r["proposal"]["facts"], "Question: How long did it last?\nAnswer: 180 days.")

    def test_a_number_the_author_gave_is_not_called_invented_but_the_same_number_without_it_is(self):
        self.srv.script = [reply((self.ASK, self.WITH_NUMBER)), reply((self.ASK, self.WITH_NUMBER))]
        bad = self.go("say how long the sampling lasted", part="methods")
        self.assertEqual(bad["status"], "failed", "an invented number is refused")
        self.srv.script = [reply((self.ASK, self.WITH_NUMBER))]
        good = self.go("say how long the sampling lasted", part="methods", facts="Answer: the sampling lasted 180 days.")
        self.assertEqual(good["status"], "proposed", good["proposal"]["units"][0].get("error"))
        self.assertIn("180 days", good["proposal"]["units"][0]["hunks"][0]["new"])

    def test_the_answers_change_what_is_sent_so_the_confirmation_hash_changes_with_them(self):
        a = self.go("tighten", part="methods", dry_run=True)
        b = self.go("tighten", part="methods", dry_run=True, facts="Answer: 180 days.")
        self.assertNotEqual(a["payload_hash"], b["payload_hash"])
        self.assertNotIn("author_answers", json.dumps(a["payloads"]))
        self.assertNotIn("<author_answers> holds", json.dumps(a["payloads"]))

    def test_without_answers_nothing_about_them_is_sent(self):
        self.srv.script = [reply(("We sampled", "We recruited"))]
        self.go("tighten", part="methods")
        body = self.srv.requests[-1]["body"]
        self.assertNotIn("author_answers", user_of(body))
        self.assertNotIn("<author_answers>", body.get("instructions") or body["messages"][0]["content"])

    def test_the_answers_are_kept_to_a_sensible_size(self):
        self.srv.script = [reply(("We sampled", "We recruited"))]
        r = self.go("tighten", part="methods", facts="x" * 9000)
        self.assertEqual(len(r["proposal"]["facts"]), 3000)

    def test_saving_and_the_authors_own_edits_accept_what_the_model_was_allowed_to_use(self):
        self.srv.script = [reply((self.ASK, self.WITH_NUMBER))]
        prop = self.go("say how long the sampling lasted", part="methods", facts="Answer: 180 days.")["proposal"]
        self.assertIn("180 days", prop["units"][0]["context"])
        mine = revise.unit_views(prop["units"][0])["suggested_masked"].replace("during 180 days", "over 180 days")
        checks = revise.check_adopted(self.proj, prop["id"], {prop["units"][0]["unit_id"]: mine})
        self.assertTrue(next(iter(checks.values()))["ok"], "the author's version may use the number the author gave")
        other = mine.replace("180", "190")
        self.assertFalse(next(iter(revise.check_adopted(self.proj, prop["id"], {prop["units"][0]["unit_id"]: other}).values()))["ok"], "any other number is still caught")
        self.assertEqual(revise.apply(self.name, self.proj, prop["id"], accept="all", unclean_ok=True)["status"], "applied")

    def test_a_replaced_suggestion_can_no_longer_be_saved(self):
        self.srv.script = [reply(("We sampled", "We recruited")), reply(("We sampled", "We enrolled"))]
        old = self.go("tighten", part="methods")["proposal"]
        new = self.go("tighten", part="methods", facts="Answer: yes.")["proposal"]
        self.assertTrue(revise.supersede(self.proj, old["id"], new["id"]))
        self.assertFalse(revise.supersede(self.proj, old["id"], new["id"]), "only a suggestion that still waits can be replaced")
        got = revise.get_proposal(self.proj, old["id"])
        self.assertEqual((got["status"], got["superseded_by"]), ("superseded", new["id"]))
        with self.assertRaises(revise.ReviseError) as cm:
            revise.apply(self.name, self.proj, old["id"], accept="all", unclean_ok=True)
        self.assertIn("replaced by a revised one", str(cm.exception))
        self.assertEqual(revise.apply(self.name, self.proj, new["id"], accept="all", unclean_ok=True)["status"], "applied")
        self.assertFalse(revise.supersede(self.proj, "20200101-000000-abcd", new["id"]), "an unknown suggestion is ignored")


class Flow(Tracker):
    """Through the chat: a reviewer comment, a suggestion that asks a question, the author answers, a new suggestion replaces it."""

    def first_suggestion(self, questions=("What specific criteria were used?",)):
        rv = self.review()
        res = self.fix(rv, 0, reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil."), questions=list(questions)))
        return rv, res

    def revise_it(self, rv, res, facts, step, text="Address this reviewer comment: the criteria are missing.", **kw):
        self.oa.script = [step]
        r = self.send(text, chunk_ids=res["data"]["chunk_ids"], facts=facts, revises=res["data"]["proposal"], issue=res["data"]["issue"], **kw)
        self.wait_job(r["job"])
        return r, [m for m in self.chat_get()["thread"]["messages"] if m["kind"] == "result"][-1]

    def test_the_question_reaches_the_author_and_the_answer_makes_a_new_suggestion_that_replaces_the_old(self):
        rv, old = self.first_suggestion()
        st, prop = self.c.get(f"/api/proposal?paper=alpha&id={old['data']['proposal']}")
        self.assertEqual(prop["units"][0]["questions"], ["What specific criteria were used?"])
        facts = "Question: What specific criteria were used?\nAnswer: wells with at least 6 months of data."
        step = reply(("Each region has a distinct climate and soil.", "Each region has its own climate and soil, judged on wells with at least 6 months of data."))
        r, new = self.revise_it(rv, old, facts, step)
        self.assertEqual(r["messages"][0]["data"]["facts"], facts)                                      # the conversation shows what went with the request
        self.assertEqual((new["data"]["revises"], new["data"]["replaced"], new["data"]["issue"]), (old["data"]["proposal"], True, old["data"]["issue"]))
        st, was = self.c.get(f"/api/proposal?paper=alpha&id={old['data']['proposal']}")
        self.assertEqual((was["status"], was["superseded_by"]), ("superseded", new["data"]["proposal"]))
        st, now = self.c.get(f"/api/proposal?paper=alpha&id={new['data']['proposal']}")
        self.assertEqual(now["status"], "proposed")
        self.assertIn("6 months", now["units"][0]["hunks"][0]["new"])
        self.assertEqual(now["facts"], facts)
        self.assertEqual(self.states(rv), ["decide", "open", "open"], "the reviewer comment follows the new suggestion")
        self.assertEqual(self.c.post("/api/proposal/approve", {"paper": "alpha", "id": old["data"]["proposal"], "unclean_ok": True})[0], 409, "the old one cannot be saved")
        st, ok = self.c.post("/api/proposal/approve", {"paper": "alpha", "id": new["data"]["proposal"], "chat": self.chat_get()["id"], "unclean_ok": True})
        self.assertEqual(st, 200, ok)
        self.assertEqual(self.states(rv), ["saved", "open", "open"])
        self.assertIn("6 months", self.main.read_text())

    def test_when_the_new_try_fails_or_changes_nothing_the_old_suggestion_stays_open(self):
        rv, old = self.first_suggestion()
        r, new = self.revise_it(rv, old, "Answer: nothing to add.", lambda b: {"approach": "Fine.", "revised_text": passage_of(b), "changes": [], "questions": []})
        self.assertEqual(new["data"]["status"], "no_changes")
        self.assertFalse(new["data"]["replaced"])
        st, was = self.c.get(f"/api/proposal?paper=alpha&id={old['data']['proposal']}")
        self.assertEqual(was["status"], "proposed", "a revision that came to nothing leaves the first suggestion to decide on")

    def test_made_up_ids_and_answers_without_a_place_are_ignored(self):
        rv, old = self.first_suggestion()
        self.oa.script = [STUDY]
        r = self.send("Address this: x", chunk_ids=old["data"]["chunk_ids"], facts="Answer: yes.", revises="not-an-id")
        self.wait_job(r["job"])
        plan = [m for m in self.chat_get()["thread"]["messages"] if m["kind"] == "plan"][-1]
        self.assertNotIn("revises", plan["data"])
        self.assertEqual(plan["data"]["plan"]["facts"], "Answer: yes.")
        r = self.send("Improve the flow of the introduction", facts="Answer: sneaky.", revises=old["data"]["proposal"])       # no chosen place: the answers are not used
        self.assertNotIn("facts", r["messages"][0]["data"])
        self.assertEqual(self.c.get(f"/api/proposal?paper=alpha&id={old['data']['proposal']}")[1]["status"], "proposed")


if __name__ == "__main__":
    unittest.main()
