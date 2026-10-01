import re
import subprocess
import unittest
from pathlib import Path

from tests.helpers import IsolatedHome, common, git, make_paper

ROOT = Path(__file__).resolve().parent.parent
UI_SH = (ROOT / "ui.sh").read_text()


class Launcher(IsolatedHome):
    def test_scripts_parse(self):
        for f in ("ui.sh", "install.sh"):
            self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / f)]).returncode, 0, f)

    def test_paths_lists_exactly_the_registered_papers(self):
        a = make_paper(self.tmp / "p", "a", "x")
        b = make_paper(self.tmp / "p", "b", "y")
        common.register(str(a), "a")
        common.register(str(b), "b")
        out = subprocess.run(["python3", str(ROOT / "scripts" / "common.py"), "paths"], capture_output=True, text=True,
                             env={"OVERLEAF_SKILL_HOME": str(common.HOME), "PATH": "/usr/bin:/bin"}).stdout.split()
        self.assertEqual(sorted(out), sorted([str(a.resolve()), str(b.resolve())]))

    def test_the_hardening_flags_are_present_and_nothing_is_published_to_the_network(self):
        for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user \"$(id -u):$(id -g)\"", "--init",
                     "-p \"127.0.0.1:${PORT}:${PORT}\"", 'mount_arg "$DIR/scripts" /app readonly'):
            self.assertIn(flag, UI_SH, flag)
        for bad in ("--privileged", "--network host", "--net=host", "-p \"${PORT}:", "0.0.0.0:${PORT}", "--cap-add", "docker.sock", "--pid=host"):
            self.assertNotIn(bad, UI_SH, bad)
        self.assertNotIn("-v /:", UI_SH)
        self.assertNotIn('-v "$HOME:', UI_SH)  # never the whole home folder: only the data dir and registered papers
        self.assertNotRegex(UI_SH, r'(?m)^(?!\s*#).*(?<!command)\s-v\s')  # no "-v src:dst" anywhere: it breaks on paths containing a colon

    def mount_arg(self, src, dst, *extra):
        r = subprocess.run(["bash", "-c", f'UI_SH_SOURCE_ONLY=1 source "{ROOT / "ui.sh"}"; mount_arg "$@"', "x", src, dst, *extra],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_mount_arguments_survive_colons_spaces_commas_and_quotes(self):
        # the real-world failure: a paper under ".../Investigacion/2026:27/research_proposal/doc_overleaf"
        p = "/Users/me/Documents/DOCTORADO/Investigacion/2026:27/research_proposal/doc_overleaf"
        self.assertEqual(self.mount_arg(p, p), f'type=bind,"source={p}","target={p}"')
        self.assertEqual(self.mount_arg("/a/scripts", "/app", "readonly"), 'type=bind,"source=/a/scripts","target=/app",readonly')
        self.assertEqual(self.mount_arg('/odd, name/a"b:c', '/odd, name/a"b:c'), 'type=bind,"source=/odd, name/a""b:c","target=/odd, name/a""b:c"')

    def test_sourcing_the_script_for_tests_runs_nothing(self):
        r = subprocess.run(["bash", "-c", f'UI_SH_SOURCE_ONLY=1 source "{ROOT / "ui.sh"}"; echo sourced'], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "sourced"))

    def test_the_access_token_is_private_and_not_passed_on_the_command_line(self):
        self.assertIn("umask 077", UI_SH)
        self.assertIn("OVERLEAF_UI_TOKEN_FILE", UI_SH)
        self.assertNotRegex(UI_SH, r"-e\s+OVERLEAF_UI_TOKEN=")

    def test_dockerfile_does_not_bake_in_code_or_secrets(self):
        d = (ROOT / "ui" / "Dockerfile").read_text()
        self.assertNotRegex(d, r"(?m)^\s*(COPY|ADD)\b")
        self.assertIn("poppler-utils", d)
        self.assertIn("git", d)
        self.assertRegex(d, r"FROM python:3\.")
        self.assertEqual([p.name for p in (ROOT / "ui").iterdir()], ["Dockerfile"])  # the build context holds nothing else


if __name__ == "__main__":
    unittest.main()
