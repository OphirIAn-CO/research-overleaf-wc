#!/usr/bin/env bash
# The one-place web app, in Docker, only when you want it.
#   ./ui.sh build       build the image once (Python + git + poppler)
#   ./ui.sh up          start it, print the URL (with this launch's access token) and open the browser (--no-open to skip)
#   ./ui.sh down        stop it     |  ./ui.sh status  |  ./ui.sh logs
#   ./ui.sh folders add ~/Documents/overleaf   let the app list and add the Overleaf clones in that folder (also: rm <folder>, list)
# Safe by construction: published on 127.0.0.1 only; runs as you (not root) with a read-only filesystem and no extra privileges;
# sees only your registered paper folders (and folders you chose with "folders add"), the data folder and the inbox; stops itself when idle.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DATA="${OVERLEAF_SKILL_HOME:-$HOME/.config/overleaf-skill}"
NAME="overleaf-ui"
IMAGE="overleaf-skill-ui:1"

need_docker() {
  command -v docker >/dev/null 2>&1 || { echo "Docker is not installed. Install Docker Desktop (or Docker Engine), then run this again."; exit 1; }
  docker info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start Docker Desktop, wait until it says running, then run this again."; exit 1; }
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
    docker build -t "$IMAGE" -f "$DIR/ui/Dockerfile" "$DIR/ui"
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
    docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "First run: building the image (a minute or two)..."; docker build -q -t "$IMAGE" -f "$DIR/ui/Dockerfile" "$DIR/ui" >/dev/null; }
    mkdir -p "$DATA/inbox"; chmod 700 "$DATA" 2>/dev/null || true
    ( umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$DATA/.ui_token" )
    mounts=(); n=0
    while IFS= read -r p; do
      [ -n "$p" ] && [ -d "$p" ] && mounts+=(--mount "$(mount_arg "$p" "$p")") && n=$((n + 1))
    done < <(python3 "$DIR/scripts/common.py" paths)
    rubric_env=()   # the peer review rubric of the academic-paper skill, read-only, so the app reads the live file like Claude does
    RUBRIC_DIR="$HOME/.claude/skills/academic-paper/agents"
    if [ -f "$RUBRIC_DIR/peer_reviewer_agent.md" ]; then
      mounts+=(--mount "$(mount_arg "$RUBRIC_DIR" /rubric readonly)"); rubric_env=(-e OLS_RUBRIC=/rubric/peer_reviewer_agent.md)
    fi
    GN="$(git config user.name 2>/dev/null || true)"; GE="$(git config user.email 2>/dev/null || true)"
    start_container() {  # start_container PORT -> 0 started | 98 port taken | 1 other failure (message printed)
      local out
      if out="$(docker run -d --rm --init --name "$NAME" --label "ols.port=$1" \
        --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp:rw,mode=1777,size=128m \
        --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 \
        -p "127.0.0.1:${1}:${1}" \
        -e OVERLEAF_UI_BIND=0.0.0.0 -e OVERLEAF_UI_PORT="$1" -e OVERLEAF_UI_PUBLIC_PORT="$1" \
        -e OVERLEAF_UI_TOKEN_FILE="$DATA/.ui_token" -e OVERLEAF_SKILL_HOME="$DATA" -e OLS_HOST_DIR="$DIR" -e HOME=/tmp/home ${rubric_env[@]+"${rubric_env[@]}"} \
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
    echo "Folders visible to it: $n   (a paper outside them, registered later, needs ./ui.sh down, then up; or share its parent folder: ./ui.sh folders add <folder>)"
    echo "$URL"
    open_url "$URL" "${2:-}"
    ;;
  folders)
    shift
    python3 "$DIR/scripts/common.py" folders "$@"
    case "${1:-}" in add|rm|remove) echo "Restart the app so it can see the change: ./ui.sh down && ./ui.sh up" ;; esac
    ;;
  down)
    need_docker
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "Stopped." || echo "It was not running."
    ;;
  status)
    need_docker
    if running; then echo "Running: http://127.0.0.1:$(running_port || cfg_port)/ (token in $DATA/.ui_token)"; else echo "Not running. Start it with ./ui.sh up"; fi
    ;;
  logs)
    need_docker
    docker logs -f "$NAME"
    ;;
  *)
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
