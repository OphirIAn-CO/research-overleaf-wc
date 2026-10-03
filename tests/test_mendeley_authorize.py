"""Signing in to Mendeley on a second computer: what is pasted is checked before it is sent, and a refused code is explained."""
import io
import json
import unittest
from unittest import mock

from tests.helpers import IsolatedHome, common
import mendeley_sync as ms


class Paste(unittest.TestCase):
    def test_the_address_the_browser_ended_up_at_or_the_bare_code_gives_the_code(self):
        for pasted in ("https://localhost/callback?code=LRiPzysup", "  https://localhost/callback?code=LRiPzysup&state=x  ", "'https://localhost/callback?code=LRiPzysup'", "code=LRiPzysup", "LRiPzysup"):
            self.assertEqual(ms.extract_code(pasted), ("LRiPzysup", None), pasted)

    def test_the_first_link_an_error_and_nonsense_are_explained_not_sent(self):
        link = "https://api.mendeley.com/oauth/authorize?client_id=25865&redirect_uri=https%3A%2F%2Flocalhost%2Fcallback&response_type=code&scope=all"
        code, why = ms.extract_code(link)
        self.assertIsNone(code)
        self.assertIn("FIRST link", why)
        self.assertIn("https://localhost/callback?code=", why)
        code, why = ms.extract_code("https://localhost/callback?error=access_denied&error_description=User+denied")
        self.assertIsNone(code)
        self.assertIn("User denied", why)
        self.assertIsNotNone(ms.extract_code("hello world")[1])
        self.assertIsNotNone(ms.extract_code("")[1])
        self.assertIsNotNone(ms.extract_code("https://example.com/nothing")[1])


class Authorize(IsolatedHome):
    def setUp(self):
        super().setUp()
        ms.CREDS_PATH = str(common.HOME / "mendeley_credentials.json")
        ms.TOKENS_PATH = str(common.HOME / "mendeley_tokens.json")
        (common.HOME / "mendeley_credentials.json").write_text(json.dumps({"client_id": "25865", "client_secret": "s3cret"}))

    def run_authorize(self, pasted, reply):
        calls = []

        def fake(url, data=None, headers=None, method=None):
            calls.append((url, data, headers))
            return reply

        args = mock.Mock(code=pasted)
        with mock.patch.object(ms, "http_request", fake), mock.patch("sys.stdout", new_callable=io.StringIO):
            try:
                ms.cmd_authorize(args)
                return calls, None
            except SystemExit as e:
                return calls, str(e)

    def test_a_good_code_is_exchanged_once_with_the_right_fields_and_saved_privately(self):
        calls, err = self.run_authorize("https://localhost/callback?code=ABC123", (200, json.dumps({"access_token": "a", "refresh_token": "r"}).encode(), {}))
        self.assertIsNone(err)
        url, data, headers = calls[0]
        self.assertEqual((url, data), (ms.TOKEN_URL, {"grant_type": "authorization_code", "code": "ABC123", "redirect_uri": "https://localhost/callback"}))
        self.assertTrue(headers["Authorization"].startswith("Basic "))
        self.assertEqual(oct((common.HOME / "mendeley_tokens.json").stat().st_mode & 0o777), "0o600")

    def test_pasting_the_first_link_sends_nothing(self):
        calls, err = self.run_authorize("https://api.mendeley.com/oauth/authorize?client_id=25865&response_type=code", (200, b"{}", {}))
        self.assertEqual(calls, [])
        self.assertIn("FIRST link", err)

    def test_a_refused_code_says_what_to_do_and_a_wrong_secret_says_so(self):
        calls, err = self.run_authorize("LRiPzysup", (400, b'{"error":"invalid_grant","error_description":"Invalid access code"}', {}))
        self.assertIn("works ONCE", err)
        self.assertIn("NEW link", err)
        self.assertIn("https://localhost/callback", err)
        self.assertNotIn("s3cret", err)
        calls, err = self.run_authorize("LRiPzysup", (401, b'{"error":"invalid_client"}', {}))
        self.assertIn("client_secret", err)
        self.assertNotIn("s3cret", err)


if __name__ == "__main__":
    unittest.main()
