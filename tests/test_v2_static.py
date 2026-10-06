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
                self.assertRegex(u, r"^https?://(quillbot\.com|www\.overleaf\.com|platform\.openai\.com|doi\.org/|www\.w3\.org/2000/svg|host\.docker\.internal:PORT)", f"{p.name}: {u}")   # doi.org/ is the link on a reference that the author clicks; the last two are an SVG namespace and an input placeholder, not requests


    def test_the_chat_never_rebuilds_what_is_already_on_screen_and_never_appends_a_null(self):
        js = (STATIC / "chat.js").read_text()
        for needed in ("st.cache", "syncChildren", "updateWorking", "ols-show-steps", "st.stuck"):
            self.assertIn(needed, js, needed)
        self.assertNotIn("clear(threadEl)", js, "the thread is reconciled, not cleared and redrawn")
        self.assertNotRegex(js, r"(?m)^\s*(?:el|holder|box|into|out|threadEl)\.append\([^\n]*: null\);", "Element.append(null) writes the word null on the page: spread an array instead")

    def test_what_the_author_has_typed_is_kept_when_the_page_changes(self):
        app = (STATIC / "app.js").read_text()
        chat = (STATIC / "chat.js").read_text()
        self.assertIn("sessionStorage", app)
        self.assertIn("drafts", app)
        for needed in ("drafts.bind(ta, `composer:", "drafts.bind(box, `answer:", "drafts.bind(reqBox, `req:", "drafts.bind(moreBox, `more:", "drafts.bind(mine, `final:"):
            self.assertIn(needed, chat, needed)
        self.assertRegex(chat, r"drafts\.clear\(`composer:", "a sent message empties its draft")
        # a text box a program fills in must tell the saver, and no secret field may ever be bound
        self.assertNotRegex(chat + (STATIC / "settings.js").read_text(), r"drafts\.bind\([^\n]*type: 'password'")

    def test_saving_without_a_text_cleaning_service_is_the_authors_explicit_choice_in_the_request(self):
        js = (STATIC / "chat.js").read_text()
        self.assertIn("unclean_ok", js)
        self.assertIn("Save only", js)
        self.assertIn("Save and send to Overleaf", js)


if __name__ == "__main__":
    unittest.main()
