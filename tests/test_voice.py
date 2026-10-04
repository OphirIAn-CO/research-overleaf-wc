"""How new text reads: in the author's words, with a connecting comment now and then, and never more than the author's material supports. The checks run on this computer, with no
model; the drafts below come from a stand-in model."""
import unittest

from tests.test_book import needs_pdf
from tests.test_outline_citations import Offline, RISK, GUIDE, draft_reply
from writing_core import draft, library, openai_client, revise, voice

MATERIAL = ("Risk perception is measured with psychometric scales and regression models of perceived risk toward hazards such as floods and earthquakes. "
            "Perception differs by country and income, so aggregate models hide differences. No study compares perception across hazards.")
LIBRARY = "Perceived risk differs between people, and aggregate models of risk perception hide this heterogeneity across hazards and across the countries that were studied."
ASK = "Write the introduction of my paper based on outlined_intro.md sections 1-4 and my library."

NATURAL = ("Risk perception is usually measured with psychometric scales, and most of what is known about how people judge hazards comes from them. These instruments are often "
           "complemented by regression models of perceived risk, which relate it to individual characteristics.\n\n"
           "Perception, however, is not the same everywhere. It differs between people and between countries, which suggests that one aggregate model may hide differences that matter.")
NATURAL_BASIS = [{"statement": "Risk perception is usually measured with", "source": "request"}, {"statement": "Perception, however, is not the same everywhere", "source": "request"},
                 {"statement": "It differs between people and between countries, which suggests", "source": "commentary"}]


def names(results):
    return {r["name"] for r in results if not r["ok"]}


class Checks(unittest.TestCase):
    def check(self, text, **kw):
        kw.setdefault("material", MATERIAL)
        return voice.check(text, **kw)

    def test_the_longest_shared_run_of_words(self):
        n, run = voice.longest_run("Overall, risk perception is measured with psychometric scales and regression models of perceived risk, as is well documented.", MATERIAL)
        self.assertEqual(n, 13)
        self.assertTrue(run.startswith("risk perception is measured"))
        self.assertEqual(voice.longest_run("Nothing here is alike at all in any way.", MATERIAL), (0, ""))

    def test_text_copied_from_a_library_source_is_refused_and_a_near_copy_is_flagged(self):
        copied = "As the literature shows, perceived risk differs between people, and aggregate models of risk perception hide this heterogeneity across hazards."
        r = self.check(copied, library_text=LIBRARY)
        self.assertIn("copies_a_source", names(r))
        self.assertTrue([x for x in r if x["name"] == "copies_a_source"][0]["severity"] == "error")
        near = "Aggregate models of risk perception hide this heterogeneity across hazards, so one number for everyone is a poor summary."
        r = self.check(near, library_text=LIBRARY)
        self.assertEqual((names(r) & {"copies_a_source", "close_to_a_source"}), {"close_to_a_source"})
        own = "People judge hazards differently, and a single model fitted to everyone averages those differences away."
        self.assertFalse(names(self.check(own, library_text=LIBRARY)) & {"copies_a_source", "close_to_a_source"})

    def test_repeating_the_authors_own_outline_word_for_word_is_a_warning_not_an_error(self):
        r = self.check("Risk perception is measured with psychometric scales and regression models of perceived risk toward hazards such as floods and earthquakes, as the field agrees.")
        bad = [x for x in r if x["name"] == "stays_close_to_your_wording"][0]
        self.assertEqual((bad["ok"], bad["severity"]), (False, "warn"))
        self.assertFalse(voice.longest_run(NATURAL, MATERIAL)[0] >= voice.RUN_AUTHOR_WARN)

    def test_claims_the_material_does_not_make_are_refused_unless_the_material_makes_them(self):
        for claim in ("This is done for the first time here.", "The scales prove that perception differs.", "The gap is unprecedented in this field.",
                      "Perception is significantly higher in poorer countries.", "It is well known that perception varies.", "Our novel framework fixes this."):
            r = self.check(claim)
            self.assertIn("claims_beyond_the_material", names(r), claim)
            self.assertEqual([x for x in r if x["name"] == "claims_beyond_the_material"][0]["severity"], "error")
        said = self.check("This is done for the first time here.", material=MATERIAL + " The authors say it is the first time this was done: for the first time in this field.")
        self.assertNotIn("claims_beyond_the_material", names(said))

    def test_commentary_that_stays_on_the_material_passes_and_one_that_brings_in_a_topic_is_flagged(self):
        basis = [{"statement": "Perception differs between people", "source": "request"}, {"statement": "This suggests that", "source": "commentary"}]
        facts = "Risk perception is measured with psychometric scales and regression models. Perception differs between people and countries, and between hazards as well. No study compares perception across hazards. "
        ok = facts + "This suggests that one aggregate model hides differences."
        self.assertEqual(names(voice.check(ok, basis, material=MATERIAL)) & {"commentary_stays_on_topic", "commentary_proportion"}, set())
        drift = facts + "This suggests that nuclear regulation, insurance pricing and mortgage lending deserve attention."
        r = voice.check(drift, basis, material=MATERIAL)
        self.assertIn("commentary_stays_on_topic", names(r))
        self.assertIn("nuclear", [x for x in r if x["name"] == "commentary_stays_on_topic"][0]["detail"])
        self.assertEqual([x for x in r if x["name"] == "commentary_stays_on_topic"][0]["severity"], "warn")

    def test_a_draft_that_is_mostly_comment_is_flagged(self):
        text = "Perception differs by country. This matters for the question. It also frames what follows. That shapes the aim of the paper. Taken together this points to the gap."
        basis = [{"statement": "This matters for the question", "source": "commentary"}, {"statement": "It also frames what follows", "source": "commentary"},
                 {"statement": "That shapes the aim", "source": "commentary"}, {"statement": "Taken together this points", "source": "commentary"}]
        self.assertIn("commentary_proportion", names(voice.check(text, basis, material=MATERIAL)))
        self.assertNotIn("commentary_proportion", names(voice.check(NATURAL, NATURAL_BASIS, material=MATERIAL + " " + NATURAL)))

    def test_without_marked_commentary_there_is_nothing_to_measure(self):
        self.assertFalse(names(voice.check(NATURAL, [], material=MATERIAL)) & {"commentary_proportion", "commentary_stays_on_topic"})
        self.assertEqual(voice.commentary_of(NATURAL, [{"statement": "x", "source": "commentary"}]), [])
        self.assertEqual(len(voice.commentary_of(NATURAL, NATURAL_BASIS)), 1)

    def test_stock_phrases_and_a_row_of_moreovers_are_flagged(self):
        r = self.check("It is important to note that risk plays a crucial role in today's world. Moreover, it varies. Furthermore, it differs.")
        detail = [x for x in r if x["name"] == "no_padding"][0]["detail"]
        self.assertIn("it is important to note", detail)
        self.assertIn("plays a crucial role", detail)
        self.assertIn("Moreover/Furthermore", detail)
        self.assertNotIn("no_padding", names(self.check(NATURAL)))

    def test_a_draft_much_longer_than_asked_for_is_flagged(self):
        long = " ".join(["Perception differs between people and countries."] * 40)
        self.assertIn("close_to_the_length_asked", names(voice.check(long, material=MATERIAL, target_words=100)))
        self.assertNotIn("close_to_the_length_asked", names(voice.check(NATURAL, material=MATERIAL, target_words=60)))


class WhatTheModelIsToldIs(unittest.TestCase):
    def test_the_prompt_asks_for_commentary_without_new_facts_and_for_it_to_be_marked(self):
        s = draft.SYSTEM_PROMPT
        for needed in ("connecting commentary", "never adds a fact, number, name, cause", "Put it in your own words", "one commentary sentence for every two or three", "stock phrases",
                       'source" = "commentary"', "uses part of the length asked for"):
            self.assertIn(needed, s)
        self.assertIn("The only thing you may add of your own is the connecting commentary", s)
        self.assertNotIn("—", s)


@needs_pdf
class InADraft(Offline):
    def setUp(self):
        super().setUp()
        self.read_library(**{"Smith et al. - 2019 - Risk perception and trust.pdf": RISK, "Academic writing guide.pdf": GUIDE})

    def write(self, *answers, text=ASK):
        self.srv.script = list(answers)
        loc = draft.locate(self.name, self.proj, text)
        self.assertEqual(loc["status"], "ready", loc)
        pl = draft.plan(self.name, self.proj, text, loc["anchor"], loc["part"])
        return draft.run(self.name, self.proj, text, loc["anchor"], loc["part"], confirm=pl["payload_hash"])

    def test_a_draft_that_connects_its_facts_is_accepted_and_the_connecting_sentence_is_marked(self):
        res = self.write(draft_reply(NATURAL, NATURAL_BASIS))
        self.assertEqual(res["status"], "proposed", res["proposal"]["units"][0].get("error"))
        u = res["proposal"]["units"][0]
        self.assertEqual(u["retries"], 0)
        self.assertEqual([b["source"] for b in u["basis"]].count("commentary"), 1)
        self.assertFalse([v for v in u["validators"] if not v["ok"] and v["name"] == "basis_known"], "'commentary' is a label the model may use")
        self.assertIn("commentary", u["hunks"][1]["rests_on"], "the card can say which paragraph holds a connecting comment")
        self.assertEqual([v["name"] for v in u["validators"] if not v["ok"]], [], u["validators"])

    def test_the_request_tells_the_model_how_to_write_it(self):
        self.write(draft_reply(NATURAL, NATURAL_BASIS))
        system = self.srv.requests[-1]["body"].get("instructions") or self.srv.requests[-1]["body"]["messages"][0]["content"]
        self.assertIn("connecting commentary", system)

    def test_a_claim_nothing_supports_is_sent_back_once_and_the_second_try_is_used(self):
        boasting = NATURAL.replace("is usually measured", "is, for the first time, measured")
        res = self.write(draft_reply(boasting, NATURAL_BASIS), draft_reply(NATURAL, NATURAL_BASIS))
        self.assertEqual(res["status"], "proposed", res["proposal"]["units"][0].get("error"))
        u = res["proposal"]["units"][0]
        self.assertEqual(u["retries"], 1)
        again = openai_client.user_text(self.srv.requests[-1]["body"])
        self.assertIn("claims_beyond_the_material", again)
        self.assertIn("for the first time", again)

    def test_text_copied_from_a_library_article_is_sent_back_to_be_put_in_new_words(self):
        copied = ("Risk perception is measured with psychometric scales and regression models of perceived risk toward hazards such as floods and earthquakes.\n\n"
                  "Perception, however, is not the same everywhere.")
        res = self.write(draft_reply(copied, NATURAL_BASIS), draft_reply(NATURAL, NATURAL_BASIS))
        self.assertEqual(res["status"], "proposed", res["proposal"]["units"][0].get("error"))
        self.assertEqual(res["proposal"]["units"][0]["retries"], 1)
        self.assertIn("copies_a_source", openai_client.user_text(self.srv.requests[-1]["body"]))

    def test_padding_and_drift_are_shown_to_the_author_but_do_not_stop_the_draft(self):
        padded = NATURAL + "\n\nIt is important to note that risk plays a crucial role in today's world."
        res = self.write(draft_reply(padded, NATURAL_BASIS))
        self.assertEqual(res["status"], "proposed")
        u = res["proposal"]["units"][0]
        self.assertIn("no_padding", {v["name"] for v in u["validators"] if not v["ok"]})
        self.assertEqual(u["retries"], 0, "a warning is for the author, not a reason to ask again")

    def test_a_draft_that_keeps_to_the_length_asked_is_not_flagged_for_length(self):
        res = self.write(draft_reply(NATURAL, NATURAL_BASIS), text=ASK + " About 80 words.")
        self.assertNotIn("close_to_the_length_asked", {v["name"] for v in res["proposal"]["units"][0]["validators"] if not v["ok"]})
