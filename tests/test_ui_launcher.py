import os
import re
import signal
import socket
import stat
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
                     "-p \"127.0.0.1:${1}:${1}\"", 'mount_arg "$DIR/scripts" /app readonly'):
            self.assertIn(flag, UI_SH, flag)
        for bad in ("--privileged", "--network host", "--net=host", "-p \"${PORT}:", "-p \"${1}:", "-p \"${cand}:", "0.0.0.0:${PORT}", "0.0.0.0:${1}", "--cap-add", "docker.sock", "--pid=host"):
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


FAKE_DOCKER = r"""#!/usr/bin/env bash
# a stand-in `docker` for testing ui.sh's port handling without a daemon
echo "$@" >> "$FAKE_LOG"
case "$1" in
  info|rm|logs|image|ps|inspect) exit 0 ;;
  run)
    port=""
    for a in "$@"; do case "$a" in 127.0.0.1:*:*) port="${a#127.0.0.1:}"; port="${port%%:*}" ;; esac; done
    if [ -n "${FAKE_BUSY_PORTS:-}" ] && [[ " $FAKE_BUSY_PORTS " == *" $port "* ]]; then
      echo "docker: Error response from daemon: failed to set up container networking: driver failed programming external connectivity on endpoint overleaf-ui (abc): Bind for 127.0.0.1:$port failed: port is already allocated" >&2
      exit 125
    fi
    if [ -n "${FAKE_RUN_ERROR:-}" ]; then echo "$FAKE_RUN_ERROR" >&2; exit 125; fi
    python3 - "$port" "$FAKE_PIDFILE" >/dev/null 2>&1 <<'PY' &
import http.server, os, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
open(sys.argv[2], "w").write(str(os.getpid()))
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
    sleep 0.3; echo deadbeef; exit 0 ;;
esac
exit 0
"""


class PortHandling(IsolatedHome):
    """ui.sh must survive a busy port, as happened on a real Mac: 'Bind for 127.0.0.1:8765 failed: port is already allocated'."""

    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        d = self.bin / "docker"
        d.write_text(FAKE_DOCKER)
        d.chmod(d.stat().st_mode | stat.S_IEXEC)
        lsof = self.bin / "lsof"   # deterministic whatever the machine has: prints the command name in FAKE_LSOF_CMD (lsof -Fc format)
        lsof.write_text('#!/usr/bin/env bash\n[ -n "${FAKE_LSOF_CMD:-}" ] && printf "p4242\\nc%s\\n" "$FAKE_LSOF_CMD"\nexit 0\n')
        lsof.chmod(lsof.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "docker.log"
        self.pid = self.tmp / "server.pid"
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.base = s.getsockname()[1]
        s.close()

    def tearDown(self):
        try:
            os.kill(int(self.pid.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        super().tearDown()

    def up(self, **env):
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), "OVERLEAF_SKILL_HOME": str(common.HOME), "OVERLEAF_UI_PORT": str(self.base),
             "FAKE_LOG": str(self.log), "FAKE_PIDFILE": str(self.pid), **{k: str(v) for k, v in env.items()}}
        return subprocess.run(["bash", str(ROOT / "ui.sh"), "up", "--no-open"], capture_output=True, text=True, env=e, timeout=60)

    def runs(self):
        return [ln for ln in self.log.read_text().splitlines() if ln.startswith("run ")]

    def test_a_port_docker_refuses_falls_through_to_the_next_one(self):
        r = self.up(FAKE_BUSY_PORTS=self.base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"Using port {self.base + 1} instead of {self.base}", r.stdout)
        self.assertIn(f"http://127.0.0.1:{self.base + 1}/?token=", r.stdout)
        self.assertEqual(len(self.runs()), 2)
        self.assertIn(f"127.0.0.1:{self.base + 1}:{self.base + 1}", self.runs()[1])
        self.assertIn(f"ols.port={self.base + 1}", self.runs()[1])

    def test_a_program_already_listening_is_skipped_before_docker_is_even_asked(self):
        hog = socket.socket()
        hog.bind(("127.0.0.1", self.base))
        hog.listen()
        try:
            r = self.up()
        finally:
            hog.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"Port {self.base} is already in use by another program on this computer", r.stdout)
        self.assertEqual(len(self.runs()), 1)
        self.assertIn(f"127.0.0.1:{self.base + 1}:", self.runs()[0])

    def busy_up(self, **env):
        hog = socket.socket()
        hog.bind(("127.0.0.1", self.base))
        hog.listen()
        try:
            return self.up(**env)
        finally:
            hog.close()

    def test_a_port_held_by_docker_desktop_itself_is_named_and_explained(self):
        # what a real Mac showed: lsof lists com.docker.backend (Docker Desktop's forwarder) on 127.0.0.1:8765
        r = self.busy_up(FAKE_LSOF_CMD="com.docke")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("in use by Docker Desktop itself", r.stdout)
        self.assertIn("docker rm -f", r.stdout)
        self.assertIn(f"Using port {self.base + 1}", r.stdout)

    def test_another_program_is_named_when_lsof_can_tell(self):
        r = self.busy_up(FAKE_LSOF_CMD="Anki")
        self.assertIn('in use by the program "Anki"', r.stdout)
        self.assertNotIn("docker rm -f", r.stdout)

    def test_the_normal_case_uses_the_wanted_port_and_says_nothing_about_ports(self):
        r = self.up()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("instead", r.stdout)
        self.assertIn(f"http://127.0.0.1:{self.base}/?token=", r.stdout)

    def test_a_real_error_is_shown_once_and_not_retried(self):
        r = self.up(FAKE_RUN_ERROR="docker: invalid spec: something else is wrong")
        self.assertEqual(r.returncode, 1)
        self.assertIn("something else is wrong", r.stderr)
        self.assertEqual(len(self.runs()), 1)

    def test_it_stops_with_a_clear_message_when_every_port_is_taken(self):
        r = self.up(FAKE_BUSY_PORTS=" ".join(str(self.base + i) for i in range(30)))
        self.assertEqual(r.returncode, 1)
        self.assertIn("Could not find a free port", r.stdout)
        self.assertEqual(len(self.runs()), 30)

    def test_a_leftover_container_of_its_own_is_removed_first(self):
        self.up()
        calls = self.log.read_text().splitlines()
        first_run = next(i for i, c in enumerate(calls) if c.startswith("run "))
        self.assertTrue(any(c.startswith("rm -f overleaf-ui") for c in calls[:first_run]))


if __name__ == "__main__":
    unittest.main()
