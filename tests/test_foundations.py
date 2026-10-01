import json
import multiprocessing
import os
import sqlite3
import time
import unittest

from tests.helpers import IsolatedHome, common  # noqa: F401  (sets sys.path)
from writing_core import config, events, store, taxonomy


def _hammer(args):
    db, n, worker = args
    for i in range(n):
        with store.write_lease(db):
            con = store.connect(db)
            con.execute("INSERT INTO t(worker, i) VALUES(?, ?)", (worker, i))
            con.commit()
            con.close()
    return True


class AtomicSave(IsolatedHome):
    def test_save_roundtrip_private_and_no_temp_left(self):
        common.save("x.json", {"a": 1})
        self.assertEqual(common.load("x.json"), {"a": 1})
        self.assertEqual(oct(os.stat(common.HOME / "x.json").st_mode & 0o777), "0o600")
        self.assertEqual([p.name for p in common.HOME.iterdir() if p.name.endswith(".tmp")], [])

    def test_overwrite_replaces_whole_file(self):
        common.save("x.json", {"a": "x" * 5000})
        common.save("x.json", {"a": 1})
        self.assertEqual(common.load("x.json"), {"a": 1})


class Events(IsolatedHome):
    def test_log_and_read_back_with_clipping(self):
        d = self.tmp / "scope"
        ev = events.log(d, events.RETRIEVE, paper="p1", ids=["a", "b"], q="x" * 1000)
        self.assertEqual(ev["type"], "retrieve")
        got = events.read(d)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["ids"], ["a", "b"])
        self.assertLessEqual(len(got[0]["q"]), events.MAX_STR)  # passage text cannot slip in whole
        self.assertEqual(oct(os.stat(events.month_file(d)).st_mode & 0o777), "0o600")

    def test_bad_lines_skipped_and_filters(self):
        d = self.tmp / "scope"
        events.log(d, events.ERROR, message="boom")
        events.log(d, events.REVISE, tokens_in=5)
        with open(events.month_file(d), "a") as f:
            f.write("not json\n")
        self.assertEqual(len(events.read(d)), 2)
        self.assertEqual([e["type"] for e in events.read(d, types={"revise"})], ["revise"])
        self.assertEqual(events.read(d, since=time.time() + 100), [])

    def test_log_never_raises(self):
        blocker = self.tmp / "file"
        blocker.write_text("x")
        self.assertIsNone(events.log(blocker / "sub", events.ERROR, message="x"))


class Store(IsolatedHome):
    def test_fts5_present(self):
        self.assertTrue(store.fts5_available())

    def test_rollback_journal_not_wal(self):
        con = store.connect(self.tmp / "a.sqlite")
        self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_open_checked_rebuilds_damaged_index(self):
        p = self.tmp / "idx.sqlite"
        calls = []

        def rebuild():
            calls.append(1)
            c = store.connect(p)
            c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            store.meta_set(c, "ok", True)
            c.commit()
            c.close()

        store.open_checked(p, rebuild).close()
        self.assertEqual(len(calls), 1)
        store.open_checked(p, rebuild).close()
        self.assertEqual(len(calls), 1)  # healthy: not rebuilt
        p.write_bytes(b"this is not a sqlite database" * 100)
        con = store.open_checked(p, rebuild)
        self.assertEqual(len(calls), 2)
        self.assertTrue(store.meta_get(con, "ok"))
        self.assertTrue((self.tmp / "idx.sqlite.corrupt").exists())

    def test_readonly_cannot_write(self):
        p = self.tmp / "ro.sqlite"
        c = store.connect(p)
        c.execute("CREATE TABLE t(a)")
        c.commit()
        c.close()
        ro = store.connect(p, readonly=True)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("INSERT INTO t VALUES(1)")

    def test_lease_is_exclusive_and_stale_ones_are_broken(self):
        p = self.tmp / "l.sqlite"
        with store.write_lease(p):
            with self.assertRaises(TimeoutError):
                with store.write_lease(p, timeout=0.2):
                    pass
        lease = self.tmp / "l.sqlite.lease"
        lease.write_text("{}")
        old = time.time() - 1000
        os.utime(lease, (old, old))
        with store.write_lease(p, timeout=1):  # stale lease is taken over
            pass
        self.assertFalse(lease.exists())

    def test_multiprocess_writers_do_not_lose_rows(self):
        db = self.tmp / "stress.sqlite"
        c = store.connect(db)
        c.execute("CREATE TABLE t(worker INT, i INT)")
        c.commit()
        c.close()
        with multiprocessing.Pool(4) as pool:
            self.assertTrue(all(pool.map(_hammer, [(str(db), 25, w) for w in range(4)])))
        c = store.connect(db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM t").fetchone()[0], 100)
        self.assertEqual(c.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_tokens_and_hashes(self):
        self.assertEqual(store.approx_tokens(""), 0)
        self.assertEqual(store.approx_tokens("abcd"), 1)
        self.assertEqual(store.sha256_text("a"), store.sha256_text("a"))
        self.assertNotEqual(store.sha256_text("a"), store.sha256_text("b"))


class Taxonomy(unittest.TestCase):
    def test_requests_route_to_parts(self):
        self.assertEqual(taxonomy.classify("improve my methodology")[0], "methods")
        self.assertEqual(taxonomy.classify("polish the Materials and Methods")[0], "methods")
        self.assertIn("methods.analysis", taxonomy.classify("how to report the statistical analysis"))
        self.assertEqual(taxonomy.classify("tighten the abstract"), ["abstract"])
        self.assertEqual(taxonomy.classify("zzz qqq"), [])

    def test_headings_route_to_parts(self):
        self.assertEqual(taxonomy.classify("3.2 Writing the Methods Section")[0], "methods")
        self.assertEqual(taxonomy.classify("Writing clear sentences")[0], "style")
        self.assertEqual(taxonomy.classify("Related Work")[0], "background")
        self.assertEqual(taxonomy.classify("Threats to Validity")[0], "limitations")

    def test_plural_insensitive(self):
        self.assertEqual(taxonomy.classify("Limitations")[0], "limitations")
        self.assertEqual(taxonomy.classify("Result")[0], "results")


class Config(IsolatedHome):
    def test_openai_cfg_requires_key_and_model_and_has_no_default_model(self):
        self.assertEqual(config.openai_status()[0], "missing")
        common.save("openai.json", {"api_key": "sk-x"})
        self.assertEqual(config.openai_status()[0], "invalid")
        common.save("openai.json", {"api_key": "sk-x", "model": "m"})
        self.assertEqual(config.openai_status(), ("ok", "model m"))
        self.assertNotIn("sk-x", config.openai_status()[1])
        self.assertEqual(config.openai_cfg()["base_url"], "https://api.openai.com/v1")

    def test_book_id_validation(self):
        for bad in ("", "../x", "a b", "a/b"):
            with self.assertRaises(config.ConfigError):
                config.book_dir(bad)
        self.assertEqual(config.book_dir("my-book_2").name, "my-book_2")

    def test_paper_settings_dry_run_default_on(self):
        proj = {"path": str(self.tmp / "paper")}
        self.assertTrue(config.paper_settings(proj)["dry_run"])
        config.set_paper_setting(proj, "dry_run", False)
        self.assertFalse(config.paper_settings(proj)["dry_run"])


if __name__ == "__main__":
    unittest.main()
