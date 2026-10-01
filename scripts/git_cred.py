#!/usr/bin/env python3
"""git credential helper for the web-app container. Answers ONLY for https://git.overleaf.com, from the file you create:

  ~/.config/overleaf-skill/overleaf_git.json   {"token": "<Overleaf Git authentication token>"}     (mode 0600)

Overleaf's git bridge takes any username with the token as password; "git" is used. For every other host, and for store/erase,
it prints nothing, so git fails cleanly instead of prompting (GIT_TERMINAL_PROMPT=0 in the container). Never logs the token.
"""
import json
import os
import sys
from pathlib import Path

HOST = "git.overleaf.com"


def main(argv, stdin=None):
    if (argv[:1] or [""])[0] != "get":
        return 0
    req = {}
    for line in (stdin or sys.stdin).read().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            req[k] = v
    if req.get("protocol") != "https" or req.get("host") != HOST:
        return 0
    home = Path(os.environ.get("OVERLEAF_SKILL_HOME", "~/.config/overleaf-skill")).expanduser()
    try:
        cfg = json.loads((home / "overleaf_git.json").read_text())
        token = cfg["token"]
    except (OSError, ValueError, KeyError):
        return 0
    if isinstance(token, str) and token:
        sys.stdout.write(f"username={cfg.get('username') or 'git'}\npassword={token}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
