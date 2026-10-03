"""Side panes (QuillBot, Overleaf) next to the chat: which addresses they use and whether the sites allow being shown in a frame.

The addresses are the author's own (QuillBot's paraphraser, the paper's Overleaf project) and are checked before use: https only, the right
kind of host and path. The probe sends nothing about the author or the paper: a plain request to the public page, with no cookies.
"""
import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request

QUILLBOT_URL = "https://quillbot.com/paraphrasing-tool"
FRAME_HOSTS = ("https://quillbot.com", "https://*.quillbot.com", "https://www.overleaf.com")  # the CSP frame-src allow-list
UA = "Mozilla/5.0 (compatible; overleaf-skill pane check)"
_ID = re.compile(r"^[0-9a-f]{20,40}$")


def overleaf_project_id(proj):
    """The full project id is the last segment of the paper's Overleaf Git address (the registry keeps it as `remote`)."""
    last = (proj.get("remote") or "").rstrip("/").rsplit("/", 1)[-1]
    return last if _ID.match(last) else None


def overleaf_id(value):
    """'https://www.overleaf.com/project/6ac15fc3b1b29fb3...' or a bare project id -> the id (lower case), or None."""
    v = str(value or "").strip()
    if _ID.match(v.lower()):
        return v.lower()
    if not valid_overleaf(v):
        return None
    last = urllib.parse.urlsplit(v).path.rstrip("/").rsplit("/", 1)[-1].lower()
    return last if _ID.match(last) else None


def _parts(url):
    try:
        u = urllib.parse.urlsplit(str(url))
        return u if u.scheme == "https" and u.hostname and not u.username and not u.password and len(str(url)) <= 300 else None
    except ValueError:
        return None


def valid_quillbot(url):
    u = _parts(url)
    return bool(u) and (u.hostname == "quillbot.com" or u.hostname.endswith(".quillbot.com"))


def valid_overleaf(url):
    """https://<host>/project/<id>: Overleaf's own site or a university installation; never an IP address or a local name."""
    u = _parts(url)
    if not u or not re.match(r"^/project/[0-9A-Za-z]{10,40}/?$", u.path) or u.query or "." not in u.hostname or u.hostname.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ipaddress.ip_address(u.hostname)
        return False
    except ValueError:
        return True


def quillbot_url(ui):
    return ui.get("quillbot_url") if valid_quillbot(ui.get("quillbot_url")) else QUILLBOT_URL


def overleaf_url(proj, override=None):
    """The paper's Overleaf project: the address the author gave, else built from the project id in the paper's Git address."""
    if override and valid_overleaf(override):
        return override
    pid = overleaf_project_id(proj)
    return f"https://www.overleaf.com/project/{pid}" if pid else None


def frameable_host(url):
    """The Content-Security-Policy of the app lets only these hosts appear inside a frame."""
    u = _parts(url)
    return bool(u) and (u.hostname == "quillbot.com" or u.hostname.endswith(".quillbot.com") or u.hostname == "www.overleaf.com")


def frame_ancestors(csp_headers):
    """The `frame-ancestors` sources of the first policy that has the directive, or None."""
    for h in csp_headers:
        for policy in h.split(","):
            for directive in policy.split(";"):
                parts = directive.strip().split()
                if parts and parts[0].lower() == "frame-ancestors":
                    return parts[1:]
    return None


def _allows_local(sources):
    for s in sources:
        s = s.strip("'\"").lower()
        if s in ("*", "http:") or s.startswith(("http://127.0.0.1", "http://localhost")):
            return True
    return False


def decide(xfo, fa):
    """-> (embeddable, why). frame-ancestors wins over X-Frame-Options, as in current browsers."""
    if fa is not None:
        if _allows_local(fa):
            return True, "the site's Content-Security-Policy allows other pages to frame it"
        return False, "the site's Content-Security-Policy (frame-ancestors) allows only " + (" ".join(fa) or "nobody")
    if xfo:
        v = xfo.strip().lower()
        if v in ("deny", "sameorigin"):
            return False, f"the site sends X-Frame-Options: {xfo.strip()}"
        return None, f"unusual X-Frame-Options: {xfo.strip()}"
    return True, "the site sends no framing restriction"


def probe(url, timeout=8):
    """probe_any() for a QuillBot or Overleaf project address only: no other URL can be made to touch the network from the web app."""
    if not (valid_quillbot(url) or valid_overleaf(url)):
        raise ValueError("only the QuillBot and Overleaf addresses can be checked")
    return probe_any(url, timeout)


def decide_inside(mode, probe_result, url):
    """Show the site inside the app (True) or in a window beside it (False), and why. mode: auto | window | inside."""
    if mode == "window":
        return False, "You chose to open it in a window beside the app."
    if not frameable_host(url):
        return False, "This address is not one the app may show inside itself, so it opens in a window beside the app."
    if mode == "inside":
        return True, ""
    r = probe_result or {}
    if r.get("error"):
        return False, "The app could not reach this site to check whether it allows being shown inside another page, so it opens in a window beside the app."
    if r.get("embeddable") is True:
        return True, ""
    return False, "This site does not allow being shown inside another page" + (f" ({r['why']})" if r.get("why") else "") + ", so it opens in a window beside the app."


def probe_any(url, timeout=8):
    """One plain GET (headers only are read). -> {url, final_url, status, xfo, frame_ancestors, embeddable, why} or {url, error}."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:  # 4xx/5xx still carry the headers we want
        r = e
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": f"could not reach it from here: {str(e)[:160]}"}
    try:
        hd = r.headers
        xfo = hd.get("X-Frame-Options")
        fa = frame_ancestors(hd.get_all("Content-Security-Policy") or [])
        ok, why = decide(xfo, fa)
        return {"url": url, "final_url": r.geturl() if hasattr(r, "geturl") else url, "status": getattr(r, "status", None) or r.code,
                "xfo": xfo, "frame_ancestors": fa, "embeddable": ok, "why": why}
    finally:
        r.close()
