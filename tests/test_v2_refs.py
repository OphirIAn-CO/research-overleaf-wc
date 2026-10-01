"""Pasting a DOI adds it everywhere: bibliography, Mendeley (when the paper is linked), Overleaf; failures wait and are retried."""
import json
import time
import unittest
from pathlib import Path

import doi_bot
from tests.helpers import IsolatedHome, common
from tests.test_paper import paper
from tests.test_phase5 import porcelain
from tests.test_ui_server import UiBase
from writing_core import config, events


class Stubs:
    """No network: doi.org returns a fixed entry, Mendeley is a recorder that can be made to fail."""

    def install(self):
        self.orig = (doi_bot.fetch_bibtex, doi_bot.to_mendeley, doi_bot.git_sync)
        self.mendeley_calls, self.fail_mendeley, self.pushes, self.push_ok = [], False, [], True
        n = [0]

        def fetch(d):  # what doi.org returns: an entry that carries its own doi field
            if "bad" in d:
                raise RuntimeError("HTTP 404")
            n[0] += 1
            author = "Lee, K." if n[0] == 1 else f"Other{n[0]}, K."
            return "@article{x,\n author={%s},\n title={Soil Water Dynamics Under Irrigation},\n year={2021},\n journal={Demo Journal},\n doi={%s}\n}" % (author, d)

        def mend(bib, doi, bib_path, group):
            self.mendeley_calls.append((doi, group))
            return "Mendeley FAILED: no network" if self.fail_mendeley else f"Mendeley: added to {group}."

        def sync(bib_path, message):
            self.pushes.append(message)
            return (True, "Pushed to Overleaf.") if self.push_ok else (False, "Push FAILED: ! [rejected] master -> master (fetch first)")

        doi_bot.fetch_bibtex, doi_bot.to_mendeley, doi_bot.git_sync = fetch, mend, sync

    def restore(self):
        doi_bot.fetch_bibtex, doi_bot.to_mendeley, doi_bot.git_sync = self.orig


class AddReference(IsolatedHome, Stubs):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)
        self.bib = common.bib_path(self.proj)
        self.install()

    def tearDown(self):
        self.restore()
        super().tearDown()

    def link(self, group="PhD Paper 1"):
        common.register(self.proj["path"], self.name, group={"id": "g1", "name": group})
        self.proj = common.resolve(self.name)[1]

    def test_a_linked_paper_always_reaches_mendeley(self):
        self.link()
        r = doi_bot.add_reference("10.1234/abc", self.bib, source="web")
        self.assertEqual((r["status"], r["key"], r["cite"]), ("added", "Lee2021Soil", "\\cite{Lee2021Soil}"))
        self.assertEqual((r["title"], r["year"], r["journal"]), ("Soil Water Dynamics Under Irrigation", "2021", "Demo Journal"))
        self.assertEqual(r["mendeley"], {"state": "added", "group": "PhD Paper 1", "detail": "Mendeley: added to PhD Paper 1."})
        self.assertEqual(self.mendeley_calls, [("10.1234/abc", "PhD Paper 1")])
        self.assertIn("Lee2021Soil", Path(self.bib).read_text())
        ev = events.read(config.paper_dir(self.proj), types={"doi_added"})[-1]
        self.assertEqual((ev["mendeley_state"], ev["mendeley"]), ("added", True))

    def test_every_old_path_does_it_too(self):
        self.link()
        msg = doi_bot.add("10.1234/abc", self.bib)                       # the CLI / bot function: no group passed, the paper's own is used
        self.assertIn("Mendeley: added to PhD Paper 1.", msg)
        self.assertEqual(self.mendeley_calls, [("10.1234/abc", "PhD Paper 1")])

    def test_an_unlinked_paper_says_so_and_does_not_call_mendeley(self):
        r = doi_bot.add_reference("10.1234/abc", self.bib)
        self.assertEqual(r["mendeley"]["state"], "not_linked")
        self.assertEqual(self.mendeley_calls, [])

    def test_the_preference_and_a_one_off_opt_out(self):
        self.link()
        config.set_paper_setting(self.proj, "mirror_to_mendeley", False)
        self.assertEqual(doi_bot.add_reference("10.1234/abc", self.bib)["mendeley"]["state"], "off")
        config.set_paper_setting(self.proj, "mirror_to_mendeley", True)
        self.assertEqual(doi_bot.add_reference("10.1234/two", self.bib, mirror=None)["mendeley"]["state"], "off")
        self.assertEqual(self.mendeley_calls, [])

    def test_a_failure_keeps_the_entry_and_waits_then_a_later_success_catches_up(self):
        self.link()
        self.fail_mendeley = True
        r = doi_bot.add_reference("10.1234/abc", self.bib)
        self.assertEqual((r["status"], r["mendeley"]["state"]), ("added", "failed"))
        self.assertIn("kept in the waiting list", r["message"])
        self.assertIn("Lee2021Soil", Path(self.bib).read_text())                    # the bibliography is never held back by Mendeley
        waiting = doi_bot.pending_list(self.proj)
        self.assertEqual([(w["doi"], w["group"]) for w in waiting], [("10.1234/abc", "PhD Paper 1")])
        self.assertTrue((Path(self.proj["path"]) / ".overleaf-skill" / "pending_mendeley.json").exists())
        self.assertEqual(porcelain(Path(self.proj["path"])), "M references.bib")    # only the bibliography; the waiting list stays out of git
        self.fail_mendeley = False
        r2 = doi_bot.add_reference("10.1234/two", self.bib)                          # network is back: the new one goes, and so does the old one
        self.assertEqual(r2["mendeley"]["state"], "added")
        self.assertEqual(doi_bot.pending_list(self.proj), [])
        self.assertEqual([c[0] for c in self.mendeley_calls], ["10.1234/abc", "10.1234/two", "10.1234/abc"])

    def test_retry_by_hand(self):
        self.link()
        self.fail_mendeley = True
        doi_bot.add_reference("10.1234/abc", self.bib)
        self.assertEqual(doi_bot.retry_pending(self.bib), {"tried": 1, "done": 0, "left": 1})
        self.fail_mendeley = False
        self.assertEqual(doi_bot.retry_pending(self.bib), {"tried": 1, "done": 1, "left": 0})

    def test_duplicates_unknown_dois_and_non_dois(self):
        doi_bot.add_reference("10.1234/abc", self.bib)
        d = doi_bot.add_reference("https://doi.org/10.1234/abc", self.bib)
        self.assertEqual((d["status"], d["key"], d["cite"]), ("duplicate", "Lee2021Soil", "\\cite{Lee2021Soil}"))
        self.assertEqual(doi_bot.add_reference("10.9999/bad", self.bib)["status"], "error")
        self.assertEqual(doi_bot.add_reference("hello", self.bib)["status"], "invalid")


class Api(UiBase, Stubs):
    def setUp(self):
        super().setUp()
        self.name, self.proj = paper(self)
        self.bib = common.bib_path(self.proj)
        common.register(self.proj["path"], self.name, group={"id": "g1", "name": "PhD Paper 1"})
        self.install()
        self.c.login()

    def tearDown(self):
        self.restore()
        super().tearDown()

    def test_add_sends_the_bibliography_by_default_and_reports_each_step(self):
        st, a = self.c.post("/api/refs/add", {"paper": "alpha", "dois": ["10.1234/abc", "10.1234/two"]})
        self.assertEqual(st, 200, a)
        self.assertEqual([r["status"] for r in a["results"]], ["added", "added"])
        self.assertEqual(a["results"][0]["mendeley"]["state"], "added")
        self.assertEqual(self.pushes, ["Add references (web app)"])               # one push for the whole batch
        self.assertTrue(a["push"]["ok"])
        self.assertEqual(a["pending"], 0)

    def test_the_papers_preference_and_the_request_decide_about_pushing(self):
        config.set_paper_setting(self.proj, "send_refs_to_overleaf", False)
        self.c.post("/api/refs/add", {"paper": "alpha", "dois": ["10.1234/abc"]})
        self.assertEqual(self.pushes, [])
        self.c.post("/api/refs/add", {"paper": "alpha", "dois": ["10.1234/two"], "push": True})
        self.assertEqual(len(self.pushes), 1)

    def test_a_push_that_overleaf_refuses_is_explained(self):
        self.push_ok = False
        st, a = self.c.post("/api/refs/add", {"paper": "alpha", "dois": ["10.1234/abc"]})
        self.assertFalse(a["push"]["ok"])
        self.assertIn("Get latest from Overleaf", a["push"]["friendly"])
        self.assertIn("Lee2021Soil", Path(self.bib).read_text())

    def test_pending_retry_and_groups(self):
        self.fail_mendeley = True
        st, a = self.c.post("/api/refs/add", {"paper": "alpha", "dois": ["10.1234/abc"], "push": False})
        self.assertEqual((a["pending"], a["results"][0]["mendeley"]["state"]), (1, "failed"))
        st, p = self.c.get("/api/refs/pending?paper=alpha")
        self.assertEqual([x["doi"] for x in p["pending"]], ["10.1234/abc"])
        self.fail_mendeley = False
        st, r = self.c.post("/api/refs/retry", {"paper": "alpha"})
        self.assertEqual((r["done"], r["left"]), (1, 0))
        self.assertEqual(self.c.post("/api/refs/link", {"paper": "alpha", "group_id": "g2", "group_name": "Other group"})[1]["group"], "Other group")
        self.assertEqual(common.registry()["projects"]["alpha"]["mendeley_group"], {"id": "g2", "name": "Other group"})
        self.assertEqual(self.c.post("/api/refs/link", {"paper": "alpha", "group_id": "", "group_name": ""})[0], 400)

    def test_listing_mendeley_groups_parses_the_cli_output(self):
        from tests.test_ui_server import ui
        orig = ui.overleaf_mcp.tool_mendeley_list_groups
        ui.overleaf_mcp.tool_mendeley_list_groups = lambda a: "6fe5b0e8-1111-2222-3333-444455556666  PhD Paper 1\n0a1b2c3d-aaaa-bbbb-cccc-ddddeeeeffff  Thesis"
        try:
            st, g = self.c.get("/api/refs/groups")
            self.assertEqual([x["name"] for x in g["groups"]], ["PhD Paper 1", "Thesis"])
            ui.overleaf_mcp.tool_mendeley_list_groups = lambda a: "no credentials: run authorize first"
            self.assertEqual(self.c.get("/api/refs/groups")[0], 409)
        finally:
            ui.overleaf_mcp.tool_mendeley_list_groups = orig

    def test_activity_tells_it_in_sentences(self):
        self.c.post("/api/refs/add", {"paper": "alpha", "dois": ["10.1234/abc"], "push": False})
        st, a = self.c.get("/api/activity?paper=alpha")
        texts = [i["text"] for i in a["items"]]
        self.assertTrue(any("Added the reference \\cite{Lee2021Soil}" in t and "in Mendeley" in t for t in texts), texts)
        self.assertEqual(a["summary"]["references"], 1)
        self.assertEqual(a["waiting"], {"suggestions": 0, "handed_to_claude": 0, "mendeley": 0})
        self.assertEqual(self.c.get("/api/activity?paper=nope")[0], 404)
        self.assertTrue(self.c.get("/api/activity")[1]["items"])


if __name__ == "__main__":
    unittest.main()
