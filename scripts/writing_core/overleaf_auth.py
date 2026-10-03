"""Why Overleaf would not take the sign-in, in words the author can act on.

Git says "Authentication failed" or "could not read Password" for very different situations: the token was never sent (a password written into the paper's address wins over the
saved token, another saved login wins, the app's helper is not wired), or it was sent and refused (wrong account, revoked, copied incompletely, no Git on that plan), or the
account is fine and the project is not reachable with it. This module tells them apart, so the Settings "Test" says which one it is instead of one sentence for all of them.

The token itself is never returned or logged: only whether it exists, whether it looks like an Overleaf Git token (they begin with olp_) and whether git would send it.
"""
import os
import re
import subprocess
import urllib.parse

HOST = "git.overleaf.com"
TOKEN_PREFIX = "olp_"


def _git(path, *args, stdin=None, timeout=40):
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, input=stdin, timeout=timeout,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "LANG": "C"})
    return r.returncode, r.stdout, r.stderr


def parse(url):
    u = urllib.parse.urlsplit(url or "")
    return {"scheme": u.scheme, "host": (u.hostname or "").lower(), "user": urllib.parse.unquote(u.username or ""), "has_password": u.password is not None,
            "project": re.sub(r"\.git$", "", u.path.strip("/").split("/")[-1]) if u.path.strip("/") else ""}


def clean_address(url):
    """The same address without a user name or password in it, or None when there is nothing to remove or it is not Overleaf's."""
    u = urllib.parse.urlsplit(url or "")
    if u.scheme != "https" or (u.hostname or "").lower() != HOST or (u.username is None and u.password is None):
        return None
    return urllib.parse.urlunsplit(("https", HOST, u.path, u.query, ""))


def _result(ok, kind, message, steps=None, fix=None, **facts):
    return {"ok": ok, "kind": kind, "message": message, "steps": steps or [], "fix": fix, "facts": facts}


def classify(stderr):
    """git's own words -> what happened. kind: rejected | forbidden | not_found | no_credentials | unreachable | unknown"""
    t = (stderr or "").lower()
    if "could not read" in t or "terminal prompts disabled" in t or "no such device or address" in t:
        return "no_credentials"
    if "authentication failed" in t or "invalid username or password" in t or "access denied" in t or "error: 401" in t or " 401" in t:
        return "rejected"
    if "error: 403" in t or "forbidden" in t:
        return "forbidden"
    if "error: 404" in t or "not found" in t or "does not exist" in t:
        return "not_found"
    if "could not resolve" in t or "timed out" in t or "connection" in t or "network is unreachable" in t or "ssl" in t or "tunnel failed" in t or "failed to connect" in t:
        return "unreachable"
    return "unknown"


def _last(text):
    return (text.strip().splitlines() or ["no answer"])[-1][:200]


GIT_BASE = "https://git.overleaf.com/"          # a test points this at a folder of local repositories


def git_address(project_id):
    return f"{GIT_BASE}{project_id}"


def diagnose(path, remote, token, git=None, overleaf_id=None):
    """path: the paper's folder; remote: its `origin` address; token: the saved Overleaf Git token or ''. git: (args, stdin) -> (code, out, err), replaced in tests.
    overleaf_id: the id of the paper's Overleaf project, from the web address saved for it (Settings), when there is one.
    -> {ok, kind, message, steps: [what to try], fix: 'clean_address' | 'clone_overleaf' | None, facts}"""
    run = git or (lambda args, stdin=None: _git(path, *args, stdin=stdin))
    a = parse(remote)
    token = token or ""
    shape = {"token_saved": bool(token), "token_looks_right": token.startswith(TOKEN_PREFIX), "token_length": len(token), "project": a["project"][:8]}
    if not remote:
        return _result(False, "no_remote", "This folder is not connected to an Overleaf project (it has no address to send to).", ["Clone the project again from Overleaf, or add this folder from the Papers list."], **shape)
    if a["scheme"] != "https":
        return _result(False, "not_https", "This paper's Overleaf address is not a web address (https), and the app can only sign in to those.",
                       ["Overleaf's Git address looks like https://git.overleaf.com/<project id>. Clone the project that way."], **shape)
    if a["host"] != HOST:
        github = a["host"] == "github.com" or a["host"].endswith(".github.com")
        addr = git_address(overleaf_id) if overleaf_id else None
        steps = ([f"Overleaf's own Git address for this project is {addr} (the id comes from the project's web address saved for this paper, in Settings)."] if addr else
                 ["Overleaf gives every project its own Git address, https://git.overleaf.com/<project id>, where the id is the part after /project/ in the project's web address. "
                  "Save that web address for this paper (Settings, further down on this page) and press Test again."])
        if addr:
            steps.append("Easiest: press \"Clone it from Overleaf\" below. It makes a copy of the project that comes straight from Overleaf, in a folder shared with the app, and adds it as a paper. "
                         "Use that copy from now on; this GitHub copy stays as it is.")
        if github:
            steps.append("If you would rather keep working through GitHub: Overleaf only takes up what is on GitHub when you choose Menu, Sync, \"Pull GitHub changes into Overleaf\" inside Overleaf. "
                         "This app cannot do that step for you.")
        else:
            steps.append("If your university runs its own Overleaf, this app cannot sign in to it yet.")
        msg = (f"This paper's folder was cloned from GitHub ({a['host']}), not from Overleaf, so sending from this app goes to GitHub, where the Overleaf token cannot be used." if github else
               f"This paper's address points to {a['host']}, and the token saved here is only sent to {HOST}.")
        return _result(False, "other_host", msg, steps, fix="clone_overleaf" if addr else None, **shape)
    if a["has_password"]:
        return _result(False, "url_password", "The address this paper was cloned from has a password (or token) written into it. Git uses that one and never looks at the token saved in Settings, "
                       "so a new token cannot help until it is removed.",
                       ["Press \"Remove it from the address\" below. It changes only this paper's address, not its text, and the old password is deleted with it.",
                        "Then press Test again."], fix="clean_address", **shape)
    if not token:
        return _result(False, "no_token", "No Overleaf token is saved yet.", ["In Overleaf: Account Settings, Git Integration, create a token, paste it above and press Save."], **shape)

    # what git would send if it asked for a sign-in now
    code, out, err = run(["credential", "fill"], f"protocol=https\nhost={HOST}\n\n")
    sent = dict(line.split("=", 1) for line in out.splitlines() if "=" in line) if code == 0 else {}
    if not sent.get("password"):
        return _result(False, "no_credentials", "Git has no sign-in to offer for Overleaf in this app, so the token was never sent.",
                       ["Stop and start the app again (./ui.sh down, then ./ui.sh up). It sets the sign-in up when it starts.",
                        "If you started the app some other way, it uses whatever sign-in your computer's Git has for Overleaf."], **shape)
    if sent["password"] != token:
        return _result(False, "other_login", "Git sends a different password to Overleaf than the token saved here: another saved sign-in takes priority.",
                       ["Delete the old Overleaf entry saved by your computer's Git (on a Mac: Keychain Access, search for git.overleaf.com), or start the app with ./ui.sh down and ./ui.sh up.",
                        "Then press Test again."], **shape)

    code, out, err = run(["ls-remote", "--heads", "origin"], None)
    if code == 0:
        return _result(True, "ok", "Overleaf accepted the sign-in for this paper.", **shape)
    kind = classify(err or out)
    tried = f"git ls-remote https://{HOST}/{a['project'] or '<project id>'}"
    again = [f"To tell whether it is the token or this app: in Terminal run  {tried}  When it asks for a user name type  git  and for the password paste the token. "
             "If that is refused too, the problem is the token or the account, not the app."]
    if kind == "rejected":
        steps = ["Make sure the token was created in the Overleaf account that can open this project. A token belongs to one account.",
                 "Create a fresh token (Account Settings, Git Integration, Add token) and copy it with the copy button right when it is shown: Overleaf shows it only once, and a token copied "
                 "with a character missing is refused. Paste it above, press Save, then Test.",
                 "Overleaf offers Git tokens only on plans that include Git integration. If Account Settings has no Git Integration section, that account cannot use it."]
        if not shape["token_looks_right"]:
            steps.insert(0, f"The saved token does not begin with {TOKEN_PREFIX}, which Overleaf Git tokens do. It may be another kind of token or some other text.")
        return _result(False, kind, "Overleaf received the token and refused it.", steps + again, **shape)
    if kind == "forbidden":
        return _result(False, kind, "Overleaf accepted the token but did not let it into this project.",
                       ["The account that made the token must be able to open this project in Overleaf (as owner or as a collaborator invited with that account's email).",
                        "Open the project in Overleaf while signed in to that account to check."] + again, **shape)
    if kind == "not_found":
        return _result(False, kind, "Overleaf says this project does not exist for this token.",
                       [f"Check the project id in this paper's address ({a['project'] or 'none'}): it must be the one in the project's Overleaf web address, and the project must not be deleted.",
                        "If it is right, the project may belong to another Overleaf account than the one that made the token."] + again, **shape)
    if kind == "no_credentials":
        return _result(False, kind, "Git asked for a sign-in and had none to give, although a token is saved.",
                       ["Stop and start the app again (./ui.sh down, then ./ui.sh up).", "Check that the file with the token can be read by the app: save the token again in Settings."], **shape)
    if kind == "unreachable":
        return _result(False, kind, "I could not reach Overleaf: " + _last(err or out), ["Check this computer's internet connection (and VPN or proxy), then try again."], **shape)
    return _result(False, kind, "Overleaf did not let git in: " + _last(err or out), again, **shape)
