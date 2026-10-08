import subprocess, tempfile, unittest
from pathlib import Path
import overleaf_mcp as m


class PushGuard(unittest.TestCase):
    def test_flags_what_overleaf_would_break(self):
        d = tempfile.mkdtemp()
        g = lambda *a: subprocess.run(["git", "-C", d, *a], capture_output=True)  # noqa: E731
        g("init", "-q"); g("config", "user.email", "a@b"); g("config", "user.name", "t")
        f = Path(d) / "main.tex"
        f.write_text("x" * 5000); g("add", "-A"); g("commit", "-qm", "a")
        for data, word in ((b"x" * 100, "shrank"), (b"ok\x00" + b"x" * 5000, "null"), (b"caf\xe9" + b"x" * 5000, "UTF-8"), (b"fine " * 1000, None)):
            f.write_bytes(data); g("add", "-A")
            bad = m.unsafe_staged(d)
            self.assertTrue(word is None and not bad or word in bad[0], bad)


if __name__ == "__main__":
    unittest.main()
