#!/usr/bin/env bash
# ./install.sh              create the data dir + register the MCP server with Claude Code
# ./install.sh bot-service  also run the Telegram DOI bot at login (macOS launchd)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
export OVERLEAF_SKILL_HOME="${OVERLEAF_SKILL_HOME:-$HOME/.config/overleaf-skill}"
mkdir -p "$OVERLEAF_SKILL_HOME" && chmod 700 "$OVERLEAF_SKILL_HOME"
[ -f "$OVERLEAF_SKILL_HOME/projects.json" ] || echo '{"default": null, "projects": {}}' > "$OVERLEAF_SKILL_HOME/projects.json"

if command -v claude >/dev/null; then
  claude mcp remove overleaf -s user >/dev/null 2>&1 || true
  claude mcp add overleaf -s user -- python3 "$DIR/scripts/overleaf_mcp.py"
else
  echo "claude CLI not found; register manually: claude mcp add overleaf -s user -- python3 $DIR/scripts/overleaf_mcp.py"
fi

if [ "${1:-}" = "bot-service" ]; then
  [ -f "$OVERLEAF_SKILL_HOME/telegram.json" ] || { echo "create $OVERLEAF_SKILL_HOME/telegram.json first (see examples/)"; exit 1; }
  PL="$HOME/Library/LaunchAgents/com.overleaf-skill.doibot.plist"
  cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.overleaf-skill.doibot</string>
<key>ProgramArguments</key><array><string>$(command -v python3)</string><string>-u</string><string>$DIR/scripts/doi_bot.py</string><string>run</string></array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin</string></dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$OVERLEAF_SKILL_HOME/bot.log</string>
<key>StandardErrorPath</key><string>$OVERLEAF_SKILL_HOME/bot.log</string>
</dict></plist>
PLIST
  launchctl unload "$PL" 2>/dev/null || true
  launchctl load "$PL"
  echo "bot running under launchd (log: $OVERLEAF_SKILL_HOME/bot.log)"
fi
echo "done. Next: python3 $DIR/scripts/common.py add /path/to/overleaf/clone --group \"Mendeley group\""
