#!/usr/bin/env bash
# The one-place web app, in Docker, only when you want it.
#   ./ui.sh build       build the image once (Python + git + poppler + LaTeX + the built-in browser; OLS_WITH_LATEX=0 leaves LaTeX out)
#   ./ui.sh up          start it, print the URL (with this launch's access token) and open the browser (--no-open to skip)
#   ./ui.sh down        stop it     |  ./ui.sh status  |  ./ui.sh logs [browser]
#   ./ui.sh folders add ~/Documents/overleaf   let the app list and add the Overleaf clones in that folder (also: rm <folder>, list)
#   ./ui.sh folders add --read-only ~/code/analysis   let the app READ the code and results in that folder (never change them)
# Safe by construction: published on 127.0.0.1 only; runs as you (not root) with a read-only filesystem and no extra privileges;
# sees only your registered paper folders (and folders you chose with "folders add"), the data folder and the inbox; stops itself when idle.
# The built-in browser (QuillBot and Overleaf inside the app) runs in a SECOND container on a private network: it sees none of that, only its
# own sign-in folder, so the pages of those sites never run next to your keys or your papers.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DATA="${OVERLEAF_SKILL_HOME:-$HOME/.config/overleaf-skill}"
NAME="overleaf-ui"
BROWSER="overleaf-browser"
NET="overleaf-skill-net"
IMAGE="overleaf-skill-ui:3"

need_docker() {
  command -v docker >/dev/null 2>&1 || { echo "Docker is not installed. Install Docker Desktop (or Docker Engine), then run this again."; exit 1; }
  docker info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start Docker Desktop, wait until it says running, then run this again."; exit 1; }
}
# What Docker itself was given. The built-in browser and a LaTeX compile are the heavy parts, and on a Mac they run inside Docker Desktop's virtual machine, whose
# default share of the computer is small: that, more than anything in the app, is what makes them slow.
resource_note() {
  local info ncpu mem arch host
  info="$(docker info --format '{{.NCPU}} {{.MemTotal}} {{.Architecture}}' 2>/dev/null || true)"
  [ -n "$info" ] || return 0
  read -r ncpu mem arch <<<"$info"
  case "$ncpu$mem" in ""|*[!0-9]*) return 0 ;; esac
  if [ "$ncpu" -lt 4 ] || [ "$mem" -lt 5800000000 ]; then
    echo "Note: Docker has only ${ncpu} CPU(s) and $((mem / 1000000000)) GB of memory for this. The built-in browser and LaTeX are much smoother with 4+ CPUs and 6+ GB:"
    echo "      Docker Desktop > Settings > Resources, raise CPUs and Memory, then Apply."
  fi
  host="$(uname -m 2>/dev/null || true)"
  case "$host/$arch" in
    arm64/x86_64|arm64/amd64|aarch64/x86_64|aarch64/amd64) echo "Note: this Mac is Apple silicon but Docker runs x86 images by emulation, which is several times slower. Turn off \"Use Rosetta/emulation\" for x86, or rebuild the image: ./ui.sh build" ;;
  esac
}
# Bind mounts use --mount, never -v: "-v source:target" breaks on any path that contains a colon (a folder like ".../2026:27/..."
# is perfectly legal on macOS), and --mount with quoted fields also copes with spaces, commas and quotes.
mount_arg() {  # mount_arg SOURCE TARGET [readonly]
  local s="${1//\"/\"\"}" d="${2//\"/\"\"}"
  printf 'type=bind,"source=%s","target=%s"%s' "$s" "$d" "${3:+,readonly}"
}
running() { [ -n "$(docker ps -q -f "name=^/${NAME}$" 2>/dev/null)" ]; }
running_port() { docker inspect -f '{{index .Config.Labels "ols.port"}}' "$NAME" 2>/dev/null || true; }
port_free() {  # port_free PORT: true when nothing on this computer is listening there (another program or another container)
  python3 - "$1" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}
who_holds() {  # who_holds PORT: a short description of what holds that port
  local c
  c="$(docker ps --filter "publish=$1" --format '{{.Names}}' 2>/dev/null | head -1)"
  if [ -n "$c" ]; then echo "the Docker container \"$c\""; return; fi
  if command -v lsof >/dev/null 2>&1; then
    c="$(lsof -nP -iTCP:"$1" -sTCP:LISTEN -Fc 2>/dev/null | sed -n 's/^c//p' | head -1)"
    case "$c" in
      "") ;;
      com.docke*|vpnkit*|docker*) echo "Docker Desktop itself (a container, possibly stopped, published it earlier)"; return ;;
      *) echo "the program \"$c\""; return ;;
    esac
  fi
  echo "another program on this computer"
}
cfg_port() {
  if [ -n "${OVERLEAF_UI_PORT:-}" ]; then echo "$OVERLEAF_UI_PORT"; return; fi
  python3 - "$DATA/ui.json" <<'PY'
import json, sys
try:
    print(int(json.load(open(sys.argv[1])).get("port", 8765)))
except Exception:
    print(8765)
PY
}
open_url() {
  [ "${2:-}" = "--no-open" ] && return 0
  if command -v open >/dev/null 2>&1; then open "$1" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true; fi
}
wait_ready() {
  local port="$1" i
  for i in $(seq 1 60); do
    python3 - "$port" <<'PY' && return 0
import sys, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=1)
except Exception:
    sys.exit(1)
PY
    sleep 0.5
  done
  return 1
}

[ "${UI_SH_SOURCE_ONLY:-}" = 1 ] && return 0 2>/dev/null  # lets the tests load the helper functions without running anything
cmd="${1:-help}"
case "$cmd" in
  build)
    need_docker
    docker build --build-arg "WITH_LATEX=${OLS_WITH_LATEX:-1}" -t "$IMAGE" -f "$DIR/ui/Dockerfile" "$DIR/ui"
    ;;
  up)
    need_docker
    WANT="$(cfg_port)"
    if running; then
      P="$(running_port)"; P="${P:-$WANT}"
      echo "Already running."
      URL="http://127.0.0.1:${P}/?token=$(cat "$DATA/.ui_token")"; echo "$URL"; open_url "$URL" "${2:-}"; exit 0
    fi
    docker rm -f "$NAME" >/dev/null 2>&1 || true   # a half-started leftover of our own, if any
    docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "Building the image. This takes several minutes the first time (and about 1.5 GB of disk): it includes LaTeX for the paper preview and the built-in browser for QuillBot and Overleaf..."; docker build -q --build-arg "WITH_LATEX=${OLS_WITH_LATEX:-1}" -t "$IMAGE" -f "$DIR/ui/Dockerfile" "$DIR/ui" >/dev/null; }
    resource_note
    mkdir -p "$DATA/inbox" "$DATA/browser"; chmod 700 "$DATA" "$DATA/browser" 2>/dev/null || true
    ( umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$DATA/.ui_token" )
    ( umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$DATA/.browser_token" )
    mounts=(); n=0
    while IFS= read -r p; do
      [ -n "$p" ] && [ -d "$p" ] && mounts+=(--mount "$(mount_arg "$p" "$p")") && n=$((n + 1))
    done < <(python3 "$DIR/scripts/common.py" paths)
    while IFS= read -r p; do   # analysis folders shared read-only: the app may read the code and results there, never change them
      [ -n "$p" ] && [ -d "$p" ] && mounts+=(--mount "$(mount_arg "$p" "$p" readonly)") && n=$((n + 1))
    done < <(python3 "$DIR/scripts/common.py" ropaths)
    rubric_env=()   # the peer review rubric of the academic-paper skill, read-only, so the app reads the live file like Claude does
    RUBRIC_DIR="$HOME/.claude/skills/academic-paper/agents"
    if [ -f "$RUBRIC_DIR/peer_reviewer_agent.md" ]; then
      mounts+=(--mount "$(mount_arg "$RUBRIC_DIR" /rubric readonly)"); rubric_env=(-e OLS_RUBRIC=/rubric/peer_reviewer_agent.md)
    fi
    GN="$(git config user.name 2>/dev/null || true)"; GE="$(git config user.email 2>/dev/null || true)"
    # The built-in browser, in its own container on a private network. It gets NO secrets, no papers, no book: only its sign-in folder (read-write),
    # a token file and the app code (read-only). Nothing is published on the computer's network; only the web app container can reach it.
    browser_env=()
    # the browser should claim the time zone of this computer (a browser in another time zone than its connection looks odd to sign-in pages)
    TZNAME="${TZ:-}"; [ -n "$TZNAME" ] || TZNAME="$(readlink /etc/localtime 2>/dev/null | sed -n 's#.*/zoneinfo/\(.*\)#\1#p')"
    tz_env=(); case "$TZNAME" in ""|*[!A-Za-z0-9_+/-]*) ;; *) tz_env=(-e "OLS_BROWSER_TZ=$TZNAME") ;; esac
    docker rm -f "$BROWSER" >/dev/null 2>&1 || true
    if docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null 2>&1; then
      if bout="$(docker run -d --rm --init --name "$BROWSER" --network "$NET" \
        --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp:rw,mode=1777,size=512m --shm-size=512m \
        --cap-drop ALL --security-opt no-new-privileges --pids-limit 1024 --memory 3g --log-driver local --log-opt max-size=10m --log-opt max-file=3 \
        --tmpfs /var/lib/xkb:rw,mode=1777,size=8m ${tz_env[@]+"${tz_env[@]}"} -e OLS_BROWSER_REAL=quillbot -e HOME=/tmp/home -e OLS_BROWSER_BIND=0.0.0.0 -e OLS_BROWSER_PORT=9233 -e OLS_BROWSER_TOKEN_FILE=/run/browser_token -e OLS_BROWSER_PROFILES=/profiles \
        --mount "$(mount_arg "$DATA/browser" /profiles)" --mount "$(mount_arg "$DATA/.browser_token" /run/browser_token readonly)" --mount "$(mount_arg "$DIR/scripts" /app readonly)" \
        "$IMAGE" python3 /app/ui/browser_service.py 2>&1)"; then
        browser_env=(--network "$NET" -e "OLS_BROWSER_URL=http://${BROWSER}:9233" -e "OLS_BROWSER_TOKEN_FILE=$DATA/.browser_token")
      else
        echo "Note: the built-in browser could not start, so QuillBot and Overleaf will open in a window beside the app. (${bout##*$'\n'})"
        docker rm -f "$BROWSER" >/dev/null 2>&1 || true
      fi
    else
      echo "Note: Docker would not create the private network for the built-in browser, so QuillBot and Overleaf will open in a window beside the app."
    fi
    start_container() {  # start_container PORT -> 0 started | 98 port taken | 1 other failure (message printed)
      local out
      if out="$(docker run -d --rm --init --name "$NAME" --label "ols.port=$1" \
        --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp:rw,mode=1777,size=640m \
        --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 2g --log-driver local --log-opt max-size=10m --log-opt max-file=3 \
        -p "127.0.0.1:${1}:${1}" \
        ${browser_env[@]+"${browser_env[@]}"} \
        -e OVERLEAF_UI_BIND=0.0.0.0 -e OVERLEAF_UI_PORT="$1" -e OVERLEAF_UI_PUBLIC_PORT="$1" \
        -e OVERLEAF_UI_TOKEN_FILE="$DATA/.ui_token" -e OVERLEAF_SKILL_HOME="$DATA" -e OLS_HOST_DIR="$DIR" -e HOME=/tmp/home -e OLS_BUILD_DIR=/tmp/ols-build ${rubric_env[@]+"${rubric_env[@]}"} \
        -e GIT_AUTHOR_NAME="${GN:-Overleaf skill}" -e GIT_COMMITTER_NAME="${GN:-Overleaf skill}" \
        -e GIT_AUTHOR_EMAIL="${GE:-overleaf-skill@localhost}" -e GIT_COMMITTER_EMAIL="${GE:-overleaf-skill@localhost}" \
        -e GIT_CONFIG_COUNT=2 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
        -e GIT_CONFIG_KEY_1=credential.https://git.overleaf.com.helper -e "GIT_CONFIG_VALUE_1=!python3 /app/git_cred.py" \
        --mount "$(mount_arg "$DATA" "$DATA")" --mount "$(mount_arg "$DIR/scripts" /app readonly)" ${mounts[@]+"${mounts[@]}"} \
        "$IMAGE" 2>&1)"; then return 0; fi
      docker rm -f "$NAME" >/dev/null 2>&1 || true
      case "$out" in
        *"port is already allocated"*|*"address already in use"*|*"ports are not available"*) return 98 ;;
      esac
      echo "$out" >&2; return 1
    }
    PORT=""; rc=1
    for i in $(seq 0 29); do   # the wanted port, else the next free one
      cand=$((WANT + i))
      if ! port_free "$cand"; then
        holder="$(who_holds "$cand")"
        if [ "$i" -eq 0 ]; then
          echo "Port ${cand} is already in use by ${holder}; looking for a free one."
          case "$holder" in "Docker Desktop itself"*) echo "  (to free it: docker ps -a, then docker rm -f <name>; or restart Docker Desktop)" ;; esac
        fi
        continue
      fi
      rc=0; start_container "$cand" || rc=$?
      if [ "$rc" -eq 0 ]; then PORT="$cand"; break; fi
      [ "$rc" -eq 98 ] || exit 1   # a real error was already printed
    done
    [ -n "$PORT" ] || { echo "Could not find a free port between ${WANT} and $((WANT + 29)). Free one, or set another start: OVERLEAF_UI_PORT=9000 ./ui.sh up"; exit 1; }
    [ "$PORT" != "$WANT" ] && echo "Using port ${PORT} instead of ${WANT}."
    if ! wait_ready "$PORT"; then echo "It did not start. Last log lines:"; docker logs --tail 20 "$NAME" 2>&1 || true; exit 1; fi
    URL="http://127.0.0.1:${PORT}/?token=$(cat "$DATA/.ui_token")"
    echo "Running. Only this computer can reach it (127.0.0.1:${PORT}); it closes itself when idle."
    echo "Folders visible to it: $n   (a paper outside them, registered later, needs ./ui.sh down, then up; or share its parent folder: ./ui.sh folders add <folder>; code and results you only want it to read: ./ui.sh folders add --read-only <folder>)"
    echo "$URL"
    open_url "$URL" "${2:-}"
    ;;
  folders)
    shift
    python3 "$DIR/scripts/common.py" folders "$@"
    case "${1:-}" in add|rm|remove|--read-only) echo "Restart the app so it can see the change: ./ui.sh down && ./ui.sh up" ;; esac
    ;;
  down)
    need_docker
    docker rm -f "$BROWSER" >/dev/null 2>&1 || true
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "Stopped." || echo "It was not running."
    docker network rm "$NET" >/dev/null 2>&1 || true
    ;;
  status)
    need_docker
    if running; then echo "Running: http://127.0.0.1:$(running_port || cfg_port)/ (token in $DATA/.ui_token)"; else echo "Not running. Start it with ./ui.sh up"; fi
    if [ -n "$(docker ps -q -f "name=^/${BROWSER}$" 2>/dev/null)" ]; then echo "Built-in browser: running (sign-ins are kept in $DATA/browser)"; else echo "Built-in browser: not running"; fi
    resource_note
    ;;
  logs)
    need_docker
    if [ "${2:-}" = "browser" ]; then docker logs -f "$BROWSER"; else docker logs -f "$NAME"; fi
    ;;
  *)
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
