"""The browser code: every script is served, none can turn text into markup or code."""
import re
import unittest
from pathlib import Path

from tests.helpers import load_ui_server

STATIC = Path(__file__).resolve().parent.parent / "scripts" / "ui" / "static"
ui = load_ui_server()


class Static(unittest.TestCase):
    def code(self, path):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)                 # comments may talk about innerHTML
        return re.sub(r"(?m)^\s*//.*$", "", text)

    def test_no_script_can_turn_text_into_markup_or_code(self):
        for p in sorted(STATIC.glob("*.js")):
            code = self.code(p)
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"", "setTimeout('"):
                self.assertNotIn(bad, code, f"{p.name} uses {bad}")

    def test_every_script_is_served_and_loaded_by_the_page(self):
        page = (STATIC / "index.html").read_text()
        served = {path for path, (f, _) in ui.FILES.items() if f.endswith(".js")}
        on_disk = {"/" + p.name for p in STATIC.glob("*.js")}
        self.assertEqual(served, on_disk)
        for path in served:
            self.assertIn(f'src="{path}"', page, path)
        self.assertNotRegex(page, r"https?://")                          # nothing is loaded from the internet
        self.assertNotIn("<script>", page)                                # and no inline script (the CSP forbids it)

    def test_no_script_reaches_for_the_internet_by_itself(self):
        for p in sorted(STATIC.glob("*.js")):
            urls = re.findall(r"""['"`](https?://[^'"`\s]+)""", self.code(p))
            # the only addresses in the code are the sites shown in the side panes and an Overleaf hint
            for u in urls:
                self.assertRegex(u, r"^https?://(quillbot\.com|www\.overleaf\.com|platform\.openai\.com|www\.w3\.org/2000/svg|host\.docker\.internal:PORT)", f"{p.name}: {u}")   # the last two are an SVG namespace and an input placeholder, not requests


if __name__ == "__main__":
    unittest.main()
