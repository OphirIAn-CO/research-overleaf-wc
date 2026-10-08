import tempfile
import unittest
from pathlib import Path

from writing_core import bibtidy


class Prune(unittest.TestCase):
    def test_only_uncited_entries_go(self):
        d = Path(tempfile.mkdtemp())
        (d / "main.tex").write_text("A \\citep{a, b}. % \\cite{c}\n")
        (d / "r.bib").write_text("@string{x = {y}}\n@article{a,\n title={A {B}},\n}\n\n@book{b, title={B}}\n@misc{c, title={C}}\n@misc{d,\n title={D}\n}\n")
        r = bibtidy.prune({"path": str(d), "bib": "r.bib"})
        self.assertEqual(r["removed"], ["c", "d"])
        t = (d / "r.bib").read_text()
        self.assertIn("@string", t); self.assertIn("@article{a", t); self.assertNotIn("@misc", t)
        self.assertTrue((d / ".overleaf-skill" / "r.bib.bak").exists())


if __name__ == "__main__":
    unittest.main()
