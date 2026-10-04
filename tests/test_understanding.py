"""Understanding a request before acting on it: the request is read as a whole, in its context; when the reading is not clearly right the author sees it and says yes, corrects it, adds
to it or sets it aside; what they confirm or correct is remembered for the next reading; and they can say the kind of request themselves. The reader is a stand-in model here."""
import json
import unittest

from tests.test_book import needs_pdf
from tests.test_outline_citations import Net, routes
from tests.test_pasted_request import ASK as PASTED_ASK, PAPER as PASTED_PAPER
from tests.test_v2_chat_api import ASK, ChatBase
from writing_core import chat, config, intent, paper_index, scholar


def reading(kind="edit", confidence=0.95, summary="You want me to improve the flow of that text.", target="the methods", question="", alternatives=(), library=False, online=False, outline=False):
    return {"intent": kind, "confidence": confidence, "summary": summary, "target": target, "uses": {"library": library, "online": online, "outline": outline},
            "question": question, "alternatives": list(alternatives)}


class Rules(unittest.TestCase):
    def test_when_the_author_is_asked(self):
        sure = reading("edit")
        self.assertFalse(intent.needs_card("unsure", sure, "edit"))
        self.assertTrue(intent.needs_card("always", sure, "edit"))
        self.assertFalse(intent.needs_card("never", reading("edit", 0.1), "draft"))
        self.assertTrue(intent.needs_card("unsure", reading("edit", 0.6), "edit"), "not confident")
        self.assertTrue(intent.needs_card("unsure", reading("draft"), "edit"), "the word-based guess says something else")
        self.assertTrue(intent.needs_card("unsure", reading("edit", question="Which paragraph?"), "edit"), "it has a question")
        self.assertTrue(intent.needs_card("unsure", reading("layout"), "edit"), "changing the layout is always shown first")
        self.assertTrue(intent.needs_card("unsure", reading("edit", 0.85, alternatives=["draft"]), "edit"), "a close alternative")
        self.assertFalse(intent.needs_card("unsure", reading("cite"), "edit"), "an edit that asks for citations is a cite")
        self.assertFalse(intent.needs_card("unsure", reading("cite", 0.9), "refsearch"))

    def test_the_older_guess_agrees_with_a_nearby_reading(self):
        self.assertTrue(intent.agrees("cite", "edit"))
        self.assertTrue(intent.agrees("refsearch", "cite"))
        self.assertFalse(intent.agrees("draft", "edit"))
        self.assertFalse(intent.agrees("layout", "edit"))

    def test_the_reader_is_not_shown_the_pasted_manuscript_as_if_it_were_the_request(self):
        shown = intent.masked_message(PASTED_ASK)
        self.assertIn("[pasted manuscript text,", shown)
        self.assertNotIn("create realistic routing alternatives", shown)
        self.assertIn("Specifically for this paragraph", shown)


@needs_pdf
class Reading(ChatBase):
    def setUp(self):
        super().setUp()
        config.set_paper_setting(self.proj, "understand", "unsure")
        self._fetch = scholar.fetch
        self.addCleanup(setattr, scholar, "fetch", self._fetch)
        self.net = scholar.fetch = Net(routes())

    def say(self, *answers):
        self.oa.script = list(answers) + self.oa.script

    def understood_card(self, r):
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "understanding"], r["messages"])
        return r["messages"][1]

    def go(self, card, **body):
        st, res = self.c.post("/api/chat/understood", {"paper": "alpha", "message": card["id"], "action": "go", **body})
        self.assertEqual(st, 200, res)
        return res

    # ---- when it is not clearly right, the author is asked first
    def test_a_reading_that_is_not_clearly_right_is_shown_and_nothing_is_done_yet(self):
        self.say(reading("cite", 0.6, "You want me to add citations found online to the paragraph you pasted.", "the paragraph you pasted", online=True))
        r = self.send(PASTED_ASK)
        card = self.understood_card(r)
        self.assertEqual((card["data"]["state"], card["data"]["reading"]["intent"], r["job"]), ("waiting", "cite", None))
        self.assertIn("citations found online", card["text"])
        self.assertEqual(len(self.oa.requests), 1, "one cheap call to read it, nothing else")
        sent = json.dumps(self.oa.requests[0]["body"]["input"])
        self.assertIn("[pasted manuscript text", sent)
        self.assertNotIn("create realistic routing alternatives", sent, "the paper's own verbs are not shown to the reader as a request")
        self.assertIn("word_based_guess", sent)

    def test_yes_go_ahead_does_what_was_understood(self):
        self.main.write_text(PASTED_PAPER)
        paper_index.refresh(self.name, self.proj, trigger="test")
        self.say(reading("cite", 0.6, "You want references from the internet for that paragraph.", online=True), {"queries": ["forest cover and pipeline routing", "transport corridors gas pipelines"]})
        card = self.understood_card(self.send(PASTED_ASK))
        res = self.go(card, intent="cite", uses={"library": False, "online": True, "outline": False})
        self.wait_job(res["job"])
        d = self.last("refsearch")["data"]
        self.assertEqual(len(d["ctx"]["chunk_ids"]), 1, "references for that paragraph, found on the internet")
        msgs = self.chat_thread()["thread"]["messages"]
        self.assertEqual([m["data"].get("state") for m in msgs if m["kind"] == "understanding"], ["confirmed"])

    def test_another_kind_of_request_can_be_chosen_and_is_remembered_as_a_correction(self):
        self.say(reading("draft", 0.55, "You want me to write new text.", "the methods"))
        card = self.understood_card(self.send(ASK))
        res = self.go(card, intent="edit")
        self.assertEqual([m["kind"] for m in res["messages"]], ["understanding", "plan"], res["messages"])
        self.wait_job(res["job"])
        memory = json.loads((self.main.parent / ".overleaf-skill" / "understanding.json").read_text())
        self.assertEqual((memory[-1]["guess"], memory[-1]["final"], memory[-1]["outcome"]), ("draft", "edit", "corrected"))

    def test_a_note_with_the_yes_goes_to_the_request_as_the_authors_own_words(self):
        self.say(reading("edit", 0.6))
        card = self.understood_card(self.send(ASK))
        res = self.go(card, intent="edit", note="keep it under two sentences")
        plan = res["messages"][1]["data"]["plan"]
        self.assertIn("keep it under two sentences", plan["facts"])
        self.wait_job(res["job"])

    def test_unticking_the_internet_on_the_card_means_no_search_for_that_request(self):
        self.main.write_text(PASTED_PAPER)
        paper_index.refresh(self.name, self.proj, trigger="test")
        self.say(reading("cite", 0.6, "You want citations in that paragraph.", online=True, library=True))
        card = self.understood_card(self.send(PASTED_ASK))
        res = self.go(card, intent="cite", uses={"library": True, "online": False, "outline": False})
        self.assertIsNone(res.get("job"))
        self.assertIn("switched off", json.dumps(res["messages"]), "it says why nothing could be cited, instead of searching anyway")
        self.assertEqual(self.net.asked, [], "nothing went to the internet")

    def test_a_search_of_the_internet_the_author_asked_for_is_not_blocked_by_a_reading_that_does_not_mention_it(self):
        self.say(reading("refsearch", 0.97, "You want references about pipelines.", online=False))
        r = self.send("find references about armed groups attacking pipelines in Colombia")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text"], "no card, and not refused")
        self.assertEqual(self.wait_job(r["job"])["status"], "done")
        self.assertTrue(self.last("refsearch")["data"]["candidates"])

    def test_reading_it_again_with_a_correction_replaces_the_reading(self):
        self.say(reading("edit", 0.6, "You want me to improve the text."), reading("draft", 0.9, "You want me to write a new paragraph for the methods.", "the methods"))
        card = self.understood_card(self.send(ASK))
        st, res = self.c.post("/api/chat/understood", {"paper": "alpha", "message": card["id"], "action": "change", "note": "no, write a new paragraph"})
        self.assertEqual(st, 200, res)
        new = res["messages"][0]["data"]
        self.assertEqual((new["reading"]["intent"], new["state"], new["correction"]), ("draft", "waiting", "no, write a new paragraph"))
        self.assertIn("no, write a new paragraph", json.dumps(self.oa.requests[-1]["body"]["input"]), "the reader saw the correction")

    def test_not_what_i_meant_sets_it_aside_and_does_nothing(self):
        self.say(reading("edit", 0.5))
        card = self.understood_card(self.send(ASK))
        st, res = self.c.post("/api/chat/understood", {"paper": "alpha", "message": card["id"], "action": "cancel", "note": "I only wanted references"})
        self.assertEqual(st, 200, res)
        self.assertEqual((res["messages"][0]["data"]["state"], res["job"]), ("cancelled", None))
        self.assertIn("I did nothing", res["messages"][1]["text"])
        self.assertEqual(self.c.post("/api/chat/understood", {"paper": "alpha", "message": card["id"], "action": "go"})[0], 409, "answered once")
        memory = json.loads((self.main.parent / ".overleaf-skill" / "understanding.json").read_text())
        self.assertEqual(memory[-1]["outcome"], "rejected")

    # ---- when it is clearly right, it goes ahead and says what it understood
    def test_a_clear_request_goes_ahead_and_the_message_says_how_it_was_read(self):
        self.say(reading("edit", 0.96, "You want me to improve the flow of the text before Figure 1 in the methods."))
        r = self.send(ASK)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "plan"], r["messages"])
        self.assertEqual(r["messages"][0]["data"]["understood"]["intent"], "edit")
        self.assertIn("flow of the text", r["messages"][0]["data"]["understood"]["summary"])
        self.wait_job(r["job"])

    def test_not_what_i_meant_after_it_went_ahead_opens_the_question(self):
        self.say(reading("edit", 0.96))
        r = self.send(ASK)
        self.wait_job(r["job"])
        st, res = self.c.post("/api/chat/understood", {"paper": "alpha", "message": r["messages"][0]["id"], "action": "reconsider"})
        self.assertEqual(st, 200, res)
        self.assertEqual((res["messages"][0]["kind"], res["messages"][0]["data"]["state"]), ("understanding", "waiting"))

    # ---- the author's choice of how much to be asked, and of the kind of request
    def test_always_asks_and_never_does_not_read_at_all(self):
        config.set_paper_setting(self.proj, "understand", "always")
        self.say(reading("edit", 0.99))
        self.understood_card(self.send(ASK))
        config.set_paper_setting(self.proj, "understand", "never")
        before = len(self.oa.requests)
        r = self.send(ASK)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "plan"])
        self.assertEqual(len(self.oa.requests), before, "no model call to read it")
        self.wait_job(r["job"])

    def test_the_setting_is_validated(self):
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "understand": "sometimes"})[0], 400)
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "understand": "always"})[0], 200)

    def test_a_kind_chosen_by_the_author_is_used_without_reading(self):
        before = len(self.oa.requests)
        r = self.send(ASK, flag="edit")
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "plan"])
        self.assertEqual(len(self.oa.requests) - before, 0, "nothing to read: they said what it is")
        self.wait_job(r["job"])
        self.assertEqual(self.send("what can you do", flag="bogus")["messages"][0]["kind"], "text", "an unknown kind is ignored")

    def test_without_a_working_reader_the_word_based_flow_runs_as_before(self):
        self.say({"intent": "nonsense"})
        r = self.send(ASK)
        self.assertEqual([m["kind"] for m in r["messages"]], ["text", "plan"], "an answer that does not fit is not trusted")
        self.wait_job(r["job"])

    # ---- it learns
    def test_what_the_author_corrected_is_shown_to_the_next_reading(self):
        self.say(reading("draft", 0.55, "Write.", "the methods"))
        card = self.understood_card(self.send(ASK))
        self.go(card, intent="edit", note="I meant improve the sentences")
        self.say(reading("edit", 0.95))
        self.wait_job(self.send(ASK + " please")["job"])
        sent = json.dumps([x["body"].get("input") for x in self.oa.requests if x["body"] and "this_authors_earlier_requests" in json.dumps(x["body"].get("input"))])
        self.assertIn("was read as draft but the author meant edit", sent)
        self.assertIn("I meant improve the sentences", sent)

    def test_the_reader_is_told_what_just_happened_in_the_conversation(self):
        self.say(reading("edit", 0.96))
        self.wait_job(self.send(ASK)["job"])
        self.say(reading("edit", 0.96))
        r = self.send("make it shorter")
        self.wait_job(r["job"])
        sent = json.dumps([x["body"].get("input") for x in self.oa.requests if x["body"] and "recent_conversation" in json.dumps(x["body"].get("input"))])
        self.assertIn("planned an improvement", sent)

    def test_forgetting_removes_the_memory(self):
        intent.remember(self.proj, "tighten this", "edit", "edit", "confirmed")
        self.assertTrue(intent.examples(self.proj, "tighten this paragraph"))
        self.assertEqual(self.c.post("/api/settings", {"paper": "alpha", "forget_understanding": True})[0], 200)
        self.assertEqual(intent.examples(self.proj, "tighten this paragraph"), [])
