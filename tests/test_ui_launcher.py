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

    def test_folders_command_shares_a_folder_without_needing_docker(self):
        shared = self.tmp / "clones"
        shared.mkdir()
        env = {"OVERLEAF_SKILL_HOME": str(common.HOME), "PATH": "/usr/bin:/bin", "HOME": str(self.tmp)}
        run = lambda *a: subprocess.run(["bash", str(ROOT / "ui.sh"), "folders", *a], capture_output=True, text=True, env=env)  # noqa: E731
        r = run("add", str(shared))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Restart the app", r.stdout)
        self.assertIn(str(shared.resolve()), run("list").stdout)
        paths = subprocess.run(["python3", str(ROOT / "scripts" / "common.py"), "paths"], capture_output=True, text=True, env=env).stdout.split()
        self.assertEqual(paths, [str(shared.resolve())])          # mounted read-write at the same path, like registered papers
        self.assertIn("no folder is shared", (run("rm", str(shared)), run("list"))[1].stdout)
        self.assertNotEqual(run("add", str(self.tmp / "missing")).returncode, 0)
        self.assertIn("folders add", UI_SH)
        self.assertIn('OLS_HOST_DIR="$DIR"', UI_SH)

    def test_the_hardening_flags_are_present_and_nothing_is_published_to_the_network(self):
        for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user \"$(id -u):$(id -g)\"", "--init",
                     "-p \"127.0.0.1:${1}:${1}\"", 'mount_arg "$DIR/scripts" /app readonly'):
            self.assertIn(flag, UI_SH, flag)
        for bad in ("--privileged", "--network host", "--net=host", "-p \"${PORT}:", "-p \"${1}:", "-p \"${cand}:", "0.0.0.0:${PORT}", "0.0.0.0:${1}", "--cap-add", "docker.sock", "--pid=host"):
            self.assertNotIn(bad, UI_SH, bad)
        self.assertNotIn("-v /:", UI_SH)
        self.assertNotIn('-v "$HOME:', UI_SH)  # never the whole home folder: only the data dir and registered papers
        self.assertNotRegex(UI_SH, r'(?m)^(?!\s*#).*(?<!command)\s-v\s')  # no "-v src:dst" anywhere: it breaks on paths containing a colon

    def test_the_browser_for_quillbot_and_overleaf_has_its_own_container_without_the_secrets(self):
        start = UI_SH.index('docker rm -f "$BROWSER"')
        block = UI_SH[start:UI_SH.index("start_container() {")]
        for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", '--user "$(id -u):$(id -g)"', "--init", '--network "$NET"', "--pids-limit", "--memory"):
            self.assertIn(flag, block, flag)
        self.assertNotRegex(block, r"(?m)\s-p\s|--publish", "nothing of the browser container is published on the computer's network")
        mounts = re.findall(r'--mount "\$\(mount_arg ([^)]*)\)"', block)
        self.assertEqual(mounts, ['"$DATA/browser" /profiles', '"$DATA/.browser_token" /run/browser_token readonly', '"$DIR/scripts" /app readonly'],
                         "only its sign-in folder, its token and the app code: never the data folder, the papers, the book or the rubric")
        for secret in ('mount_arg "$DATA"', "mounts[@]", "rubric", "OVERLEAF_UI_TOKEN_FILE", "openai", "GIT_"):
            self.assertNotIn(secret, block, secret)
        run = UI_SH[UI_SH.index("start_container() {"):UI_SH.index("PORT=\"\"; rc=1")]
        self.assertIn('browser_env[@]', run)
        self.assertIn("OLS_BROWSER_URL=http://${BROWSER}:9233", block)
        self.assertIn('NET="overleaf-skill-net"', UI_SH)
        self.assertIn('docker rm -f "$BROWSER"', UI_SH[UI_SH.index("  down)"):])

    def test_the_dockerfile_brings_the_browser_and_pins_what_it_needs(self):
        d = (ROOT / "ui" / "Dockerfile").read_text()
        self.assertRegex(d, r"playwright==\d+\.\d+\.\d+")
        self.assertIn("playwright install --with-deps chromium", d)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", d)
        self.assertIn("bookworm", d, "a Debian release Playwright supports")
        self.assertIn('IMAGE="overleaf-skill-ui:2"', UI_SH, "a new image tag, so an old image without the browser is rebuilt")

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
    if [ -z "$port" ]; then   # the built-in browser's container publishes nothing
      if [ -n "${FAKE_BROWSER_ERROR:-}" ]; then echo "$FAKE_BROWSER_ERROR" >&2; exit 125; fi
      echo cafebabe; exit 0
    fi
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
        self.base = self.free_run()

    @staticmethod
    def free_run(n=4):
        """A port with the next n-1 ports also free: the launcher walks upwards from the one it is given, and other programs may hold a neighbour."""
        for _ in range(50):
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            base = s.getsockname()[1]
            s.close()
            held = []
            try:
                for p in range(base, base + n):
                    h = socket.socket()
                    h.bind(("127.0.0.1", p))
                    held.append(h)
                return base
            except OSError:
                continue
            finally:
                for h in held:
                    h.close()
        raise RuntimeError("no run of free ports")

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
        return [ln for ln in self.log.read_text().splitlines() if ln.startswith("run ") and " --name overleaf-ui " in ln]   # the web app's container (the browser has its own)

    def browser_runs(self):
        return [ln for ln in self.log.read_text().splitlines() if ln.startswith("run ") and " --name overleaf-browser " in ln]

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
        self.base = self.free_run(30)      # all thirty must be free for the launcher to ask docker about each of them
        r = self.up(FAKE_BUSY_PORTS=" ".join(str(self.base + i) for i in range(30)))
        self.assertEqual(r.returncode, 1)
        self.assertIn("Could not find a free port", r.stdout)
        self.assertEqual(len(self.runs()), 30)

    def test_the_browser_container_starts_first_on_a_private_network_and_the_app_is_told_where_it_is(self):
        r = self.up()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.log.read_text().splitlines()
        self.assertTrue(any(c.startswith("network create overleaf-skill-net") for c in calls) or any(c.startswith("network inspect") for c in calls))
        self.assertEqual(len(self.browser_runs()), 1)
        self.assertLess(calls.index(self.browser_runs()[0]), calls.index(self.runs()[0]), "the browser is up before the app that uses it")
        self.assertIn("--network overleaf-skill-net", self.runs()[0])
        self.assertIn("OLS_BROWSER_URL=http://overleaf-browser:9233", self.runs()[0])
        self.assertNotIn("-p 127.0.0.1", self.browser_runs()[0])
        token = (common.HOME / ".browser_token")
        self.assertEqual(oct(token.stat().st_mode & 0o777), "0o600")
        self.assertNotIn(token.read_text().strip(), self.log.read_text(), "the token is passed as a file, never on the command line")
        self.assertEqual(oct((common.HOME / "browser").stat().st_mode & 0o777), "0o700")

    def test_if_the_browser_cannot_start_the_app_still_does_and_says_what_will_happen(self):
        r = self.up(FAKE_BROWSER_ERROR="docker: Error response from daemon: no space left on device")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("built-in browser could not start", r.stdout)
        self.assertIn("window beside the app", r.stdout)
        self.assertNotIn("OLS_BROWSER_URL", self.runs()[0])
        self.assertNotIn("--network overleaf-skill-net", self.runs()[0])

    def test_down_stops_both_containers(self):
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), "OVERLEAF_SKILL_HOME": str(common.HOME), "FAKE_LOG": str(self.log), "FAKE_PIDFILE": str(self.pid)}
        subprocess.run(["bash", str(ROOT / "ui.sh"), "down"], capture_output=True, text=True, env=e, timeout=30)
        calls = self.log.read_text().splitlines()
        self.assertIn("rm -f overleaf-browser", calls)
        self.assertIn("rm -f overleaf-ui", calls)

    def test_a_leftover_container_of_its_own_is_removed_first(self):
        self.up()
        calls = self.log.read_text().splitlines()
        first_run = next(i for i, c in enumerate(calls) if c.startswith("run "))
        self.assertTrue(any(c.startswith("rm -f overleaf-ui") for c in calls[:first_run]))


if __name__ == "__main__":
    unittest.main()
