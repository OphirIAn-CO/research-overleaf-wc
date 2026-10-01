#!/usr/bin/env bash
# The one-place web app, in Docker, only when you want it.
#   ./ui.sh build       build the image once (Python + git + poppler)
#   ./ui.sh up          start it, print the URL (with this launch's access token) and open the browser (--no-open to skip)
#   ./ui.sh down        stop it     |  ./ui.sh status  |  ./ui.sh logs
# Safe by construction: published on 127.0.0.1 only; runs as you (not root) with a read-only filesystem and no extra privileges;
# sees only your registered paper folders, the data folder and the inbox; stops itself after the idle time set in Settings.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DATA="${OVERLEAF_SKILL_HOME:-$HOME/.config/overleaf-skill}"
NAME="overleaf-ui"
IMAGE="overleaf-skill-ui:1"

need_docker() {
  command -v docker >/dev/null 2>&1 || { echo "Docker is not installed. Install Docker Desktop (or Docker Engine), then run this again."; exit 1; }
  docker info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start Docker Desktop, wait until it says running, then run this again."; exit 1; }
}
running() { [ -n "$(docker ps -q -f "name=^/${NAME}$" 2>/dev/null)" ]; }
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

cmd="${1:-help}"
case "$cmd" in
  build)
    need_docker
    docker build -t "$IMAGE" -f "$DIR/ui/Dockerfile" "$DIR/ui"
    ;;
  up)
    need_docker
    PORT="$(cfg_port)"
    if running; then
      echo "Already running."
      URL="http://127.0.0.1:${PORT}/?token=$(cat "$DATA/.ui_token")"; echo "$URL"; open_url "$URL" "${2:-}"; exit 0
    fi
    docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "First run: building the image (a minute or two)..."; docker build -q -t "$IMAGE" -f "$DIR/ui/Dockerfile" "$DIR/ui" >/dev/null; }
    mkdir -p "$DATA/inbox"; chmod 700 "$DATA" 2>/dev/null || true
    ( umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$DATA/.ui_token" )
    mounts=(); n=0
    while IFS= read -r p; do
      [ -n "$p" ] && [ -d "$p" ] && mounts+=(-v "$p:$p") && n=$((n + 1))
    done < <(python3 "$DIR/scripts/common.py" paths)
    GN="$(git config user.name 2>/dev/null || true)"; GE="$(git config user.email 2>/dev/null || true)"
    docker run -d --rm --init --name "$NAME" \
      --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp:rw,mode=1777,size=128m \
      --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 \
      -p "127.0.0.1:${PORT}:${PORT}" \
      -e OVERLEAF_UI_BIND=0.0.0.0 -e OVERLEAF_UI_PORT="$PORT" -e OVERLEAF_UI_PUBLIC_PORT="$PORT" \
      -e OVERLEAF_UI_TOKEN_FILE="$DATA/.ui_token" -e OVERLEAF_SKILL_HOME="$DATA" -e HOME=/tmp/home \
      -e GIT_AUTHOR_NAME="${GN:-Overleaf skill}" -e GIT_COMMITTER_NAME="${GN:-Overleaf skill}" \
      -e GIT_AUTHOR_EMAIL="${GE:-overleaf-skill@localhost}" -e GIT_COMMITTER_EMAIL="${GE:-overleaf-skill@localhost}" \
      -e GIT_CONFIG_COUNT=2 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
      -e GIT_CONFIG_KEY_1=credential.https://git.overleaf.com.helper -e "GIT_CONFIG_VALUE_1=!python3 /app/git_cred.py" \
      -v "$DATA:$DATA" -v "$DIR/scripts:/app:ro" ${mounts[@]+"${mounts[@]}"} \
      "$IMAGE" >/dev/null
    if ! wait_ready "$PORT"; then echo "It did not start. Last log lines:"; docker logs --tail 20 "$NAME" 2>&1 || true; exit 1; fi
    URL="http://127.0.0.1:${PORT}/?token=$(cat "$DATA/.ui_token")"
    echo "Running. Only this computer can reach it (127.0.0.1:${PORT}); it closes itself when idle."
    echo "Papers visible to it: $n   (a paper registered later needs ./ui.sh down, then up)"
    echo "$URL"
    open_url "$URL" "${2:-}"
    ;;
  down)
    need_docker
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "Stopped." || echo "It was not running."
    ;;
  status)
    need_docker
    if running; then echo "Running: http://127.0.0.1:$(cfg_port)/ (token in $DATA/.ui_token)"; else echo "Not running. Start it with ./ui.sh up"; fi
    ;;
  logs)
    need_docker
    docker logs -f "$NAME"
    ;;
  *)
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
