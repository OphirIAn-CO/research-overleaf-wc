import json
import unittest
import urllib.error

from tests.fake_openai import FakeOpenAI
from writing_core import openai_client as oc

KEY = "sk-test-SECRET-123"


def cfg(server, **kw):
    base = {"api_key": KEY, "model": "m-1", "base_url": server.url, "max_output_tokens": 500, "timeout": 5, "api": server.api}
    base.update(kw)
    return base


class Client(unittest.TestCase):
    def setUp(self):
        oc.SLEEP = lambda s: None
        self.srv = FakeOpenAI("responses")

    def tearDown(self):
        self.srv.close()
        oc.SLEEP = __import__("time").sleep

    def test_responses_request_shape_and_parse(self):
        self.srv.script = [{"revised_text": "ok", "changes": [], "questions": []}]
        r = oc.call(cfg(self.srv), "SYS", "USER")
        req = self.srv.requests[0]
        self.assertEqual(req["path"], "/v1/responses")
        b = req["body"]
        self.assertEqual((b["model"], b["instructions"], b["input"], b["store"], b["max_output_tokens"]), ("m-1", "SYS", "USER", False, 500))
        self.assertEqual(b["text"]["format"]["type"], "json_schema")
        self.assertTrue(b["text"]["format"]["strict"])
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + KEY)
        self.assertNotIn(KEY, json.dumps(b))  # the key is only ever a header
        self.assertEqual(oc.parse_json(r["text"])["revised_text"], "ok")
        self.assertEqual(r["usage"], {"input_tokens": 120, "output_tokens": 60})
        self.assertNotIn("temperature", b)

    def test_chat_api_variant(self):
        srv = FakeOpenAI("chat")
        try:
            srv.script = [{"revised_text": "chat ok", "changes": [], "questions": []}]
            r = oc.call(cfg(srv), "SYS", "USER")
            req = srv.requests[0]
            self.assertEqual(req["path"], "/v1/chat/completions")
            b = req["body"]
            self.assertEqual([m["role"] for m in b["messages"]], ["system", "user"])
            self.assertEqual((b["store"], b["max_completion_tokens"]), (False, 500))
            self.assertEqual(b["response_format"]["type"], "json_schema")
            self.assertEqual(oc.parse_json(r["text"])["revised_text"], "chat ok")
            self.assertEqual(r["usage"], {"input_tokens": 120, "output_tokens": 60})
        finally:
            srv.close()

    def test_json_modes_temperature_and_extra_body(self):
        _, b = oc.build_request(cfg(self.srv, json_mode="object", temperature=0.2, extra_body={"reasoning": {"effort": "low"}}), "S", "U")
        self.assertEqual(b["text"]["format"], {"type": "json_object"})
        self.assertEqual((b["temperature"], b["reasoning"]), (0.2, {"effort": "low"}))
        _, b = oc.build_request(cfg(self.srv, json_mode="none"), "S", "U")
        self.assertNotIn("text", b)
        _, b = oc.build_request(cfg(self.srv, api="chat", json_mode="object"), "S", "U")
        self.assertEqual(b["response_format"], {"type": "json_object"})

    def test_parse_json_tolerates_fences_and_prose(self):
        self.assertEqual(oc.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(oc.parse_json('Sure! {"a": 2} hope that helps'), {"a": 2})
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.parse_json("no json here")
        self.assertEqual(cm.exception.kind, "parse")

    def test_auth_error_does_not_leak_the_key_and_is_not_retried(self):
        self.srv.script = [lambda b: (401, {"error": {"message": f"Incorrect API key provided: {KEY}"}})]
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.call(cfg(self.srv), "S", "U")
        self.assertEqual(cm.exception.kind, "auth")
        self.assertNotIn(KEY, str(cm.exception))
        self.assertEqual(len(self.srv.requests), 1)

    def test_unknown_model_and_bad_request(self):
        self.srv.script = [lambda b: (404, {"error": {"message": "The model `m-1` does not exist"}})]
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.call(cfg(self.srv), "S", "U")
        self.assertEqual(cm.exception.kind, "not_found")
        self.assertIn("model id", str(cm.exception))
        self.srv.script = [lambda b: (400, {"error": {"message": "bad param"}})]
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.call(cfg(self.srv), "S", "U")
        self.assertEqual((cm.exception.kind, cm.exception.status), ("bad_request", 400))

    def test_rate_limit_is_retried_then_succeeds_quota_is_not(self):
        ok = {"revised_text": "x", "changes": [], "questions": []}
        self.srv.script = [lambda b: (429, {"error": {"message": "slow down"}}), lambda b: (500, {"error": {"message": "oops"}}), ok]
        r = oc.call(cfg(self.srv), "S", "U")
        self.assertEqual((r["tries"], len(self.srv.requests)), (3, 3))
        self.srv.requests.clear()
        self.srv.script = [lambda b: (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}})]
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.call(cfg(self.srv), "S", "U")
        self.assertEqual(cm.exception.kind, "quota")
        self.assertEqual(len(self.srv.requests), 1)

    def test_server_errors_give_up_after_retries(self):
        self.srv.script = [lambda b: (503, {"error": {"message": "down"}})] * 5
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.call(cfg(self.srv), "S", "U", retries=2)
        self.assertEqual(cm.exception.kind, "server")
        self.assertEqual(len(self.srv.requests), 3)

    def test_truncated_and_refusal(self):
        self.srv.script = [lambda b: (200, {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": [], "usage": {}})]
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.call(cfg(self.srv), "S", "U")
        self.assertEqual(cm.exception.kind, "truncated")
        self.srv.script = [lambda b: (200, {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "I can't"}]}], "usage": {}})]
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.call(cfg(self.srv), "S", "U")
        self.assertEqual(cm.exception.kind, "refusal")

    def test_network_failure(self):
        c = cfg(self.srv, base_url="http://127.0.0.1:9/v1", timeout=1)
        with self.assertRaises(oc.OpenAIError) as cm:
            oc.call(c, "S", "U", retries=0)
        self.assertEqual(cm.exception.kind, "network")


if __name__ == "__main__":
    unittest.main()
