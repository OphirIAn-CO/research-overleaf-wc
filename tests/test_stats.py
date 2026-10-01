import unittest
from pathlib import Path

from tests.helpers import IsolatedHome, common
from tests.test_book import FIXTURE, needs_pdf
from tests.test_paper import METHODS, paper
from writing_core import book_index, config, events, paper_index as pi, retrieve, stats


@needs_pdf
class Stats(IsolatedHome):
    def setUp(self):
        super().setUp()
        book_index.ingest(FIXTURE, "mini")
        self.a = paper(self, "alpha")
        self.b = paper(self, "beta")

    def scope(self, ref):
        return config.paper_dir(ref[1])

    def test_overview_counts_and_pass_rate(self):
        sa = self.scope(self.a)
        pi.refresh(*self.a)
        events.log(sa, "revise", paper="alpha", dry_run=True, status="preview")
        events.log(sa, "revise", paper="alpha", dry_run=False, status="proposed", tokens_in=1000, tokens_out=200, cost=0.01, validators={"a": True})
        events.log(sa, "revise", paper="alpha", dry_run=False, status="failed", tokens_in=500, tokens_out=50, cost=0.005, validators={"a": False})
        events.log(sa, "decision", paper="alpha", accepted=3, rejected=1, rules_accepted=["B:mini:c05.s01.k01"], rules_rejected=["B:mini:c05.s02.k01"])
        events.log(sa, "doi_added", paper="alpha", source="telegram")
        m = stats.monitor()
        o = m["overview"]
        self.assertEqual((o["papers"], o["papers_indexed"], o["books"], o["book_chunks"]), (2, 1, 1, 20))
        self.assertEqual((o["passes"], o["previews"], o["pass_rate"]), (2, 1, 0.5))
        self.assertEqual((o["tokens_in"], o["tokens_out"], o["cost"]), (1500, 250, 0.015))
        self.assertEqual((o["accepted"], o["rejected"], o["accept_rate"], o["doi_added"]), (3, 1, 0.75, 1))
        cg = m["chatgpt"]
        self.assertEqual(cg["validators"]["a"], {"pass": 1, "fail": 1})
        self.assertEqual(cg["rules_rejected"], [["B:mini:c05.s02.k01", 1]])
        self.assertIn("papers 1/2 indexed", stats.format_text(m))

    def test_timeline_buckets_events_by_day_and_type(self):
        sa = self.scope(self.a)
        events.log(sa, "error", message="x")
        events.log(sa, "error", message="y")
        events.log(sa, "revise", dry_run=True, status="preview")
        t = stats.timeline(stats.gather(), days=7)
        self.assertEqual(len(t["days"]), 7)
        self.assertEqual(t["series"]["error"][-1], 2)
        self.assertEqual(t["series"]["revise"][-1], 1)

    def test_index_health_shows_only_changed_chunks_on_a_later_pass(self):
        pi.refresh(*self.a, trigger="pass_start")
        pi.refresh(*self.a, trigger="pass_start")
        (Path(self.a[1]["path"]) / "sections" / "methods.tex").write_text(METHODS.replace("40 farms", "42 farms"))
        pi.refresh(*self.a, trigger="after_edit")
        rows = stats.index_health(stats.gather(paper="alpha"))
        self.assertEqual([(r["added"], r["changed"], r["skipped_scan"]) for r in rows], [(11, 0, False), (0, 0, True), (0, 1, False)])
        self.assertEqual(rows[2]["unchanged"], 10)
        self.assertEqual([r["trigger"] for r in rows], ["pass_start", "pass_start", "after_edit"])
        self.assertTrue(all(r["paper"] == "alpha" for r in rows))

    def test_book_usage_heatmap_counts_and_never_used(self):
        retrieve.book_query("mini", "statistical analysis in methods")
        retrieve.book_query("mini", "statistical analysis in methods")
        retrieve.book_query("mini", "polish the abstract")
        bu = stats.book_usage(stats.gather())[0]
        self.assertEqual(bu["retrievals"], 3)
        self.assertEqual(bu["total_chunks"], 20)
        self.assertGreater(bu["never_used"], 5)
        counts = {k["id"]: k["count"] for c in bu["chapters"] for sec in c["sections"] for k in sec["chunks"]}
        self.assertGreaterEqual(counts["B:mini:c02.s03.k01"], 2)          # asked for twice
        self.assertEqual(counts["B:mini:c04.s01.k01"], 0)                  # never asked for
        self.assertGreaterEqual(bu["top"][0]["count"], 2)
        ch2 = next(c for c in bu["chapters"] if c["chapter"] == 2)
        self.assertGreaterEqual(ch2["count"], 2)

    def test_retrieval_inspector_does_not_double_count_book_lookups_made_for_a_paper(self):
        retrieve.book_query("mini", "limitations", paper="alpha", paper_scope=self.scope(self.a))
        retrieve.book_query("mini", "abstract")           # no paper
        rows = stats.retrievals(stats.gather())
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["paper"] for r in rows}, {"alpha", None})

    def test_efficiency_compares_sent_tokens_with_manuscript_and_book(self):
        pi.query(*self.a, "improve my methods")
        e = stats.efficiency(stats.gather())
        self.assertEqual(e["book_tokens"], book_index.manifest("mini")["tokens"])
        row = e["rows"][0]
        self.assertEqual(row["what"], "paper query")
        self.assertLess(row["sent"], row["manuscript"])

    def test_isolation_audit_is_clean_for_normal_use_and_flags_a_foreign_id(self):
        pi.query(*self.a, "methods")
        pi.query(*self.b, "methods")
        retrieve.book_query("mini", "methods", paper="alpha", paper_scope=self.scope(self.a))
        self.assertTrue(stats.isolation(stats.gather())["ok"])
        events.log(self.scope(self.b), "retrieve", paper="beta", kind="paper", ids=["P:alpha:main.tex#results#k01"])
        events.log(self.scope(self.a), "retrieve", paper="alpha", kind="paper", ids=["B:mini:c01.s00.k01", "weird"])
        events.log(config.book_dir("mini"), "retrieve", paper="alpha", ids=[])
        iso = stats.isolation(stats.gather())
        self.assertFalse(iso["ok"])
        problems = " | ".join(v["problem"] for v in iso["violations"])
        self.assertIn("used P:alpha:main.tex#results#k01", problems)
        self.assertIn("unrecognized id 'weird'", problems)
        self.assertIn("book log names a paper", problems)
        self.assertIn("LOOK AT THIS", stats.format_text(stats.monitor()))

    def test_paper_filter_and_empty_state(self):
        pi.query(*self.a, "methods")
        m = stats.monitor(paper="beta")
        self.assertEqual(m["overview"]["passes"], 0)
        self.assertEqual(m["retrievals"], [])
        self.assertTrue(m["isolation"]["ok"])


if __name__ == "__main__":
    unittest.main()
