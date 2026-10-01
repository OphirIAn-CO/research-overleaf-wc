"""Side panes (QuillBot, Overleaf) next to the chat: which addresses they use and whether the sites allow being shown in a frame.

Only fixed addresses are ever used (no user-supplied URL reaches the network), and the probe sends nothing about the author or
the paper: a plain request to the public page, with no cookies.
"""
import re
import urllib.error
import urllib.request

QUILLBOT_URL = "https://quillbot.com/"
FRAME_HOSTS = ("https://quillbot.com", "https://*.quillbot.com", "https://www.overleaf.com")  # the CSP frame-src allow-list
UA = "Mozilla/5.0 (compatible; overleaf-skill pane check)"
_ID = re.compile(r"^[0-9a-f]{20,40}$")


def overleaf_project_id(proj):
    """The full project id is the last segment of the paper's Overleaf Git address (the registry keeps it as `remote`)."""
    last = (proj.get("remote") or "").rstrip("/").rsplit("/", 1)[-1]
    return last if _ID.match(last) else None


def overleaf_url(proj):
    pid = overleaf_project_id(proj)
    return f"https://www.overleaf.com/project/{pid}" if pid else None


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
    """probe_any() for the two fixed addresses only: no other URL can be made to touch the network from the web app."""
    if url != QUILLBOT_URL and not re.match(r"^https://www\.overleaf\.com/project/[0-9a-f]{20,40}$", str(url)):
        raise ValueError("only the QuillBot and Overleaf addresses can be checked")
    return probe_any(url, timeout)


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
