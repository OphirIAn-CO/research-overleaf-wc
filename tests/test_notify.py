"""The "your request is ready" message: sent through the Telegram bot that adds DOIs, only for long chat requests, never with any text of the paper,
and never able to break the job it announces. Offline: the Telegram address is a stand-in."""
import json
import time
import unittest
import urllib.error
from unittest import mock

from tests.helpers import IsolatedHome, common
from tests.test_ui_server import UiBase, ui
from writing_core import config, notify


def job(status="done", took=60, result=None, **meta):
    now = time.time()
    return {"id": "j1", "status": status, "started": now - took, "finished": now, "result": result, "meta": {"paper": "thesis", "chat": "c1", **meta}}


class Reply:
    def __init__(self, body=b'{"ok": true}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class Sending(IsolatedHome):
    def setUp(self):
        super().setUp()
        common.save("telegram.json", {"token": "123:SECRET-BOT", "allowed_chat_ids": [11, 22]})
        self.sent = []

        def fake(req, timeout=None):
            self.sent.append((req.full_url, req.data.decode()))
            return Reply()
        p = mock.patch.object(notify.urllib.request, "urlopen", fake)
        p.start()
        self.addCleanup(p.stop)

    def test_it_goes_to_every_allowed_chat_through_the_bot(self):
        sent, problem = notify.send_telegram("Ready")
        self.assertEqual((sent, problem), (2, None))
        self.assertEqual([u for u, _ in self.sent], ["https://api.telegram.org/bot123:SECRET-BOT/sendMessage"] * 2)
        self.assertIn("chat_id=11", self.sent[0][1])
        self.assertIn("chat_id=22", self.sent[1][1])

    def test_not_set_up_sends_nothing_and_says_so(self):
        common.save("telegram.json", {"token": "123:SECRET-BOT", "allowed_chat_ids": []})
        self.assertEqual(notify.telegram_state(), ("not_set_up", 0))
        sent, problem = notify.send_telegram("Ready")
        self.assertEqual(sent, 0)
        self.assertIn("not set up", problem)
        self.assertEqual(self.sent, [])

    def test_a_network_failure_is_a_sentence_and_never_leaks_the_token(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("[Errno 111] Connection refused for /bot123:SECRET-BOT/sendMessage")
        with mock.patch.object(notify.urllib.request, "urlopen", boom):
            sent, problem = notify.send_telegram("Ready")
        self.assertEqual(sent, 0)
        self.assertIn("could not reach Telegram", problem)
        self.assertNotIn("SECRET", problem)

    def test_a_refused_token_is_explained_without_the_token(self):
        def refuse(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        with mock.patch.object(notify.urllib.request, "urlopen", refuse):
            sent, problem = notify.send_telegram("Ready")
        self.assertEqual(sent, 0)
        self.assertIn("token", problem)
        self.assertNotIn("SECRET", problem)


class WhatIsAnnounced(IsolatedHome):
    def test_a_long_request_with_suggestions(self):
        t = notify.job_text(job(result={"actions": 5, "made": 3}))
        self.assertIn("3 suggestions", t)
        self.assertIn("thesis", t)

    def test_one_suggestion_is_singular(self):
        self.assertIn("1 suggestion to review", notify.job_text(job(result={"made": 1})))

    def test_an_answer_without_suggestions(self):
        self.assertIn("is done", notify.job_text(job(result={"made": 0})))
        self.assertIn("is done", notify.job_text(job(result=None)))

    def test_an_error_says_it_stopped_and_not_why(self):
        t = notify.job_text(job("error"))
        self.assertIn("Stopped", t)

    def test_a_quick_request_is_not_announced(self):
        self.assertIsNone(notify.job_text(job(took=5, result={"made": 2})))

    def test_only_chat_requests_are_announced(self):
        j = job(result={"made": 2})
        j["meta"] = {"paper": "thesis"}
        self.assertIsNone(notify.job_text(j))
        self.assertIsNone(notify.job_text(job("running")))

    def test_it_can_be_switched_off_and_the_wait_changed(self):
        common.save("ui.json", {"notify_telegram": False})
        self.assertIsNone(notify.job_text(job(result={"made": 2})))
        common.save("ui.json", {"notify_min_seconds": 120})
        self.assertIsNone(notify.job_text(job(took=60, result={"made": 2})))
        self.assertIsNotNone(notify.job_text(job(took=200, result={"made": 2})))

    def test_nothing_of_the_paper_is_in_the_message(self):
        j = job(result={"made": 2, "text": "The secret sentence of the manuscript", "actions": [{"note": "secret"}]})
        self.assertNotIn("secret", notify.job_text(j).lower())


class JobHook(UiBase):
    def test_a_finished_job_is_announced_and_a_failing_announcer_changes_nothing(self):
        got = []
        jobs = ui.Jobs(on_finish=got.append)
        jid = jobs.submit("x", lambda: {"made": 1}, meta={"paper": "thesis", "chat": "c"})
        self._until(lambda: got)
        self.assertEqual([(g["status"], g["result"]) for g in got], [("done", {"made": 1})])

        def explode(j):
            raise RuntimeError("telegram is down")
        broken = ui.Jobs(on_finish=explode)
        jid = broken.submit("y", lambda: "fine")
        self._until(lambda: broken.get(jid)["status"] != "running")
        time.sleep(0.1)       # the failing announcer has run by now
        j = broken.get(jid)
        self.assertEqual((j["status"], j["result"]), ("done", "fine"))

    def test_a_job_that_fails_is_announced_too(self):
        got = []
        jobs = ui.Jobs(on_finish=got.append)

        def bad():
            raise ValueError("it did not work")
        jid = jobs.submit("x", bad, meta={"paper": "thesis", "chat": "c"})
        self._until(lambda: got)
        self.assertEqual(got[0]["status"], "error")

    def _until(self, cond, seconds=10):
        t0 = time.time()
        while time.time() - t0 < seconds:
            if cond():
                return
            time.sleep(0.02)
        self.fail("it did not happen")

    def test_the_app_sends_the_message_when_a_long_chat_job_ends(self):
        common.save("telegram.json", {"token": "123:SECRET-BOT", "allowed_chat_ids": [11]})
        sent = []
        with mock.patch.object(notify, "send_telegram", lambda text: sent.append(text) or (1, None)):
            jobs = self.srv.app.jobs
            jid = jobs.submit("Working", lambda: {"made": 2}, meta={"paper": "thesis", "chat": "c"})
            self._until(lambda: jobs.get(jid)["status"] == "done")
            time.sleep(0.1)
            self.assertEqual(sent, [])            # it took no time at all: not announced
            common.save("ui.json", {"notify_min_seconds": 0})
            jid = jobs.submit("Working", lambda: {"made": 2}, meta={"paper": "thesis", "chat": "c"})
            self._until(lambda: sent)
        self.assertEqual(len(sent), 1)
        self.assertIn("2 suggestions", sent[0])


class Settings(UiBase):
    def setUp(self):
        super().setUp()
        self.c.login()

    def test_the_defaults_and_the_state(self):
        st, state = self.c.get("/api/state")
        ui_ = state["settings"]["ui"]
        self.assertEqual((ui_["notify_telegram"], ui_["notify_sound"], ui_["notify_browser"], ui_["notify_min_seconds"]), (True, True, True, 20))
        st, c = self.c.get("/api/connections")
        self.assertFalse(c["notify"]["telegram_ready"])
        common.save("telegram.json", {"token": "123:SECRET-BOT", "allowed_chat_ids": [1]})
        self.assertTrue(self.c.get("/api/connections")[1]["notify"]["telegram_ready"])
        self.assertNotIn("SECRET", json.dumps(self.c.get("/api/connections")[1]))

    def test_they_are_saved_and_the_wait_is_limited(self):
        self.assertEqual(self.c.post("/api/settings", {"ui": {"notify_sound": False, "notify_telegram": False, "notify_min_seconds": 90}})[0], 200)
        cfg = config.ui_cfg()
        self.assertEqual((cfg["notify_sound"], cfg["notify_telegram"], cfg["notify_min_seconds"]), (False, False, 90))
        self.c.post("/api/settings", {"ui": {"notify_min_seconds": 99999}})
        self.assertEqual(config.ui_cfg()["notify_min_seconds"], 3600)
        self.assertEqual(self.c.post("/api/settings", {"ui": {"notify_min_seconds": "soon"}})[0], 400)

    def test_the_test_button(self):
        st, r = self.c.post("/api/test/notify", {})
        self.assertEqual((st, r["ok"]), (200, False))
        self.assertIn("not set up", r["message"])
        common.save("telegram.json", {"token": "123:SECRET-BOT", "allowed_chat_ids": [1, 2]})
        with mock.patch.object(notify, "send_telegram", lambda text: (2, None)) as send:
            st, r = self.c.post("/api/test/notify", {})
        self.assertEqual((st, r["ok"]), (200, True))
        self.assertIn("2 chats", r["message"])
        with mock.patch.object(notify, "send_telegram", lambda text: (0, "This computer could not reach Telegram just now.")):
            st, r = self.c.post("/api/test/notify", {})
        self.assertFalse(r["ok"])
        self.assertIn("could not reach Telegram", r["message"])
        self.assertNotIn("SECRET", json.dumps(r))


if __name__ == "__main__":
    unittest.main()
