import re
import unittest

from tests.fake_openai import FakeOpenAI
from tests.helpers import IsolatedHome, common
from tests.test_book import FIXTURE, needs_pdf
from tests.test_paper import paper
from tests.test_revise import reply
from writing_core import book_index, config, openai_client
import overleaf_mcp as mcp


class Tools(IsolatedHome):
    def test_tool_list_has_the_original_eight_and_the_four_new_ones(self):
        self.assertEqual(set(mcp.TOOLS), {"overleaf_projects", "overleaf_pull", "overleaf_status", "overleaf_sync", "mendeley_fetch",
                                          "mendeley_list_groups", "mendeley_catalog_search", "mendeley_add_reference",
                                          "book_query", "paper_query", "chatgpt_revise", "revise_record"})
        self.assertIn("OPT-IN ONLY", mcp.TOOLS["chatgpt_revise"]["description"])
        for t in mcp.TOOLS.values():
            self.assertIn("type", t["inputSchema"])

    def test_unknown_paper_and_missing_book_are_plain_messages(self):
        self.assertIn("unknown project", mcp.tool_paper_query({"repo_path": "nope"}))
        self.assertIn("which book?", mcp.tool_book_query({"query": "methods"}))
        self.assertIn("provide query", mcp.tool_book_query({}))

    def test_paper_query_resolves_and_marks_the_active_paper(self):
        name, proj = paper(self)
        out = mcp.tool_paper_query({"repo_path": name, "query": "improve my methods"})
        self.assertIn("[paper alpha: 2 chunks", out)
        self.assertIn("sections/methods.tex:2-5", out)
        self.assertEqual(common.get_active()[0], "alpha")
        self.assertIn("Materials and Methods", mcp.tool_paper_query({"repo_path": name}))  # empty query = outline

    def test_bad_part_is_reported_not_raised(self):
        name, _ = paper(self)
        self.assertIn("unknown part", mcp.tool_paper_query({"repo_path": name, "query": "x", "part": "introdution"}))


@needs_pdf
class Flow(IsolatedHome):
    def setUp(self):
        super().setUp()
        openai_client.SLEEP = lambda s: None
        self.srv = FakeOpenAI("responses")
        common.save("openai.json", {"api_key": "sk-test", "model": "m", "base_url": self.srv.url})
        book_index.ingest(FIXTURE, "mini")
        self.name, self.proj = paper(self)

    def tearDown(self):
        self.srv.close()
        super().tearDown()

    def test_book_query_tool(self):
        out = mcp.tool_book_query({"query": "how do I report the statistical analysis", "repo_path": self.name})
        self.assertIn("B:mini:c02.s03.k01", out)
        self.assertIn("pp.6-7", out)

    def test_the_whole_opt_in_flow_as_claude_would_drive_it(self):
        args = {"repo_path": self.name, "instruction": "tighten my methods", "part": "methods.data"}
        preview = mcp.tool_chatgpt_revise(args)
        self.assertIn("DRY RUN: nothing was sent", preview)
        self.assertEqual(self.srv.requests, [])
        h = re.search(r"payload_hash=([0-9a-f]{64})", preview).group(1)
        # skipping the confirmation does not send
        self.assertIn("DRY RUN", mcp.tool_chatgpt_revise({**args, "dry_run": False}))
        self.assertEqual(self.srv.requests, [])
        self.srv.script = [reply(("We sampled 40 farms by stratified random sampling.", "We drew a stratified random sample of 40 farms."))]
        out = mcp.tool_chatgpt_revise({**args, "dry_run": False, "confirm": h})
        self.assertIn("[u1.h1]", out)
        self.assertIn("Nothing has been written", out)
        pid = re.search(r"Proposal (\S+) \(proposed\)", out).group(1)
        rec = mcp.tool_revise_record({"repo_path": self.name, "proposal_id": pid, "accepted": "all", "applied": False})
        self.assertIn("1 accepted", rec)
        self.assertIn("error", mcp.tool_revise_record({"repo_path": self.name, "proposal_id": "nope"}))

    def test_missing_openai_config_is_a_helpful_message(self):
        (config.data_dir() / "openai.json").unlink()
        out = mcp.tool_chatgpt_revise({"repo_path": self.name, "instruction": "tighten my methods"})
        self.assertIn("openai.json", out)
        self.assertNotIn("Traceback", out)

    def test_openai_errors_are_reported(self):
        config.set_paper_setting(self.proj, "dry_run", False)
        self.srv.script = [lambda b: (401, {"error": {"message": "nope"}})]
        out = mcp.tool_chatgpt_revise({"repo_path": self.name, "instruction": "tighten my methods", "part": "methods"})
        self.assertIn("FAILED", out)
        self.assertIn("rejected the API key", out)


if __name__ == "__main__":
    unittest.main()
