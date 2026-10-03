"""Choosing the writing book's PDF in Settings: it is checked, named safely, put in the inbox, and can then be prepared like any book."""
import os
import socket
import unittest
from urllib.parse import quote

from tests.helpers import common
from tests.test_book import FIXTURE, needs_pdf
from tests.test_ui_server import TOKEN, UiBase
from writing_core import config

PDF = {"Content-Type": "application/pdf"}


class Upload(UiBase):
    def setUp(self):
        super().setUp()
        self.c.login()
        self.inbox = config.inbox_dir()

    def put(self, name, data, **kw):
        return self.c.api("POST", f"/api/book/upload?name={quote(name)}", data, {**PDF, **kw})

    def leftovers(self):
        return sorted(p.name for p in self.inbox.iterdir()) if self.inbox.exists() else []

    def test_a_pdf_lands_in_the_inbox_privately_and_is_listed(self):
        data = b"%PDF-1.4\n" + b"x" * 5000
        st, r = self.put("My Writing Handbook.pdf", data)
        self.assertEqual((st, r["name"], r["size"], r["duplicate"]), (200, "My Writing Handbook.pdf", len(data), False), r)
        self.assertEqual((self.inbox / "My Writing Handbook.pdf").read_bytes(), data)
        self.assertEqual(oct((self.inbox / "My Writing Handbook.pdf").stat().st_mode & 0o777), "0o600")
        self.assertEqual(self.leftovers(), ["My Writing Handbook.pdf"], "no temporary file is left behind")
        self.assertIn("My Writing Handbook.pdf", self.c.get("/api/state")[1]["inbox"])

    def test_names_are_made_safe_and_a_second_different_file_does_not_replace_the_first(self):
        st, r = self.put("../../etc/passwd.pdf", b"%PDF-1.4 a")
        self.assertEqual(st, 200, r)
        self.assertEqual(r["name"], "passwd.pdf")
        st, r = self.put("Smith: Writing? <draft>.pdf", b"%PDF-1.4 b")
        self.assertEqual(r["name"], "Smith_ Writing_ _draft_.pdf")
        st, r = self.put("passwd.pdf", b"%PDF-1.4 different")
        self.assertEqual((st, r["name"], r["duplicate"]), (200, "passwd (2).pdf", False))
        self.assertEqual((self.inbox / "passwd.pdf").read_bytes(), b"%PDF-1.4 a", "the first one is untouched")
        st, r = self.put("passwd.pdf", b"%PDF-1.4 a")
        self.assertEqual((r["name"], r["duplicate"]), ("passwd.pdf", True), "the same file again is recognised")
        self.assertEqual(len([n for n in self.leftovers() if n.endswith(".pdf")]), 3)
        self.assertFalse([n for n in self.leftovers() if n.startswith(".upload")])
        self.assertEqual(self.put("", b"%PDF-1.4 z")[1]["name"], "book.pdf")

    def test_what_is_not_a_pdf_empty_or_too_big_is_refused_and_leaves_nothing(self):
        st, r = self.put("notes.pdf", b"just some text, not a pdf")
        self.assertEqual(st, 400)
        self.assertIn("does not look like a PDF", r["error"])
        self.assertEqual(self.put("e.pdf", b"")[0], 400)
        self.assertEqual(self.leftovers(), [])
        # the limit lives in the module the test server was built from
        import tests.test_ui_server as t
        old = t.ui.BOOK_MAX
        t.ui.BOOK_MAX = 1000
        try:
            st, r = self.put("big.pdf", b"%PDF-1.4" + b"x" * 2000)
            self.assertEqual(st, 413)
            self.assertIn("MB limit", r["error"])
        finally:
            t.ui.BOOK_MAX = old
        self.assertEqual(self.leftovers(), [])

    def test_it_needs_the_sign_in_and_the_page_header(self):
        st, hd, _ = self.c.raw("POST", "/api/book/upload?name=a.pdf", b"%PDF-1.4 a", {**PDF, "Cookie": "ols_session=wrong", "X-Requested-With": "ols"})
        self.assertEqual(st, 401)
        st, hd, _ = self.c.raw("POST", "/api/book/upload?name=a.pdf", b"%PDF-1.4 a", PDF)
        self.assertEqual(st, 403)
        self.assertEqual(self.leftovers(), [])

    def test_an_upload_that_stops_half_way_is_refused_and_cleaned_up(self):
        s = socket.create_connection(("127.0.0.1", self.c.port), timeout=10)
        s.sendall((f"POST /api/book/upload?name=cut.pdf HTTP/1.0\r\nHost: 127.0.0.1:{self.c.port}\r\nCookie: {self.c.cookie}\r\nX-Requested-With: ols\r\n"
                   "Content-Type: application/pdf\r\nContent-Length: 5000\r\n\r\n%PDF-1.4 only a little").encode())
        s.shutdown(socket.SHUT_WR)
        reply = s.recv(4096).decode("latin-1")
        s.close()
        self.assertIn("400", reply.split("\r\n")[0])
        self.assertIn("cut off", reply)
        self.assertEqual(self.leftovers(), [])

    @needs_pdf
    def test_the_uploaded_book_is_prepared_like_any_other(self):
        st, r = self.put("Handbook.pdf", FIXTURE.read_bytes())
        self.assertEqual(st, 200, r)
        st, j = self.c.post("/api/book/ingest", {"pdf": r["name"], "id": "handbook", "mode": "build"})
        self.assertEqual(st, 200, j)
        done = self.wait_job(j["job"], timeout=120)
        self.assertEqual(done["status"], "done", done)
        self.assertEqual([b["id"] for b in self.c.get("/api/state")[1]["books"]], ["handbook"])


if __name__ == "__main__":
    unittest.main()
