"""What happened, in sentences. Reads the metadata-only event logs and the suggestions of one paper (or all) and tells the story
in plain words: what you asked, what OpenAI suggested, what you kept, what was sent to Overleaf, which references were added.
Read-only; nothing here is ever sent to a model.
"""
import time
from datetime import datetime

import common

from . import config, events, revise, stats, taxonomy

KINDS = {"revise", "decision", "doi_added", "handoff", "error", "review"}


def _clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _day(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _what(parts):
    return ", ".join(taxonomy.label(p) for p in parts or []) or "your text"


def _instruction(proj, pid):
    try:
        p = revise.get_proposal(proj, pid)
        return p.get("instruction", ""), p
    except revise.ReviseError:
        return "", None


def _short(text, n=70):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def items_for(name, proj, since):
    scope = config.paper_dir(proj)
    out = []
    for e in events.read(scope, since=since):
        t = e["type"]
        if t == "revise" and not e.get("dry_run") and e.get("status") not in (None, "preview"):
            ask, prop = _instruction(proj, e.get("proposal"))
            parts = (prop or {}).get("part")
            n = e.get("hunks", 0)
            text = (f"You asked: “{_short(ask)}”. " if ask else "") + \
                   (f"OpenAI suggested {n} change{'s' if n != 1 else ''} in {_what(parts)}" if e["status"] in ("proposed", "no_changes") and n else
                    "OpenAI found nothing to change" if e["status"] in ("proposed", "no_changes") else "I could not get a usable answer") + \
                   f" ({e.get('tokens_in', 0)} tokens in, {e.get('tokens_out', 0)} out)."
            out.append({"ts": e["ts"], "paper": name, "kind": "ask", "text": text, "proposal": e.get("proposal")})
        elif t == "decision" and (e.get("accepted") or e.get("rejected") or e.get("applied") or e.get("synced")):
            bits = []
            if e.get("accepted") or e.get("rejected"):
                bits.append(f"You kept {e.get('accepted', 0)} and set aside {e.get('rejected', 0)}")
            if e.get("edited"):
                bits.append("with your own edits")
            if e.get("applied"):
                bits.append("saved in your files")
            if e.get("synced"):
                bits.append("sent to Overleaf")
            if not bits:
                continue
            out.append({"ts": e["ts"], "paper": name, "kind": "decision", "text": ", ".join(bits).capitalize()[0:1] + ", ".join(bits)[1:] + ".", "proposal": e.get("proposal")})
        elif t == "doi_added":
            m = {"added": " and in Mendeley", "failed": "; Mendeley is waiting to be tried again", "not_linked": "", "off": ""}.get(e.get("mendeley_state"), " and in Mendeley" if e.get("mendeley") else "")
            src = {"telegram": " from your phone", "web": ""}.get(e.get("source"), f" ({e.get('source')})" if e.get("source") else "")
            out.append({"ts": e["ts"], "paper": name, "kind": "reference", "text": f"Added the reference \\cite{{{e.get('key')}}} to your bibliography{m}{src}."})
        elif t == "handoff":
            out.append({"ts": e["ts"], "paper": name, "kind": "handoff", "text": f"You handed {e.get('hunks', 0)} change{'s' if e.get('hunks') != 1 else ''} to Claude to save."})
        elif t == "review":
            out.append({"ts": e["ts"], "paper": name, "kind": "review", "text": f"Peer review of {e.get('scope') or 'the paper'}: verdict {e.get('verdict') or 'ready'} ({e.get('tokens_in', 0)} tokens in)."})
        elif t == "error":
            out.append({"ts": e["ts"], "paper": name, "kind": "error", "text": "Something went wrong: " + _short(str(e.get("message") or e.get("error") or ""), 120)})
    return out


def waiting(name, proj):
    """What is waiting for the author on this paper."""
    import doi_bot
    props = revise.list_proposals(proj)
    return {"suggestions": sum(1 for p in props if p["status"] == "proposed" and not p.get("handoff") and p.get("hunks")),
            "handed_to_claude": sum(1 for p in props if p["status"] == "proposed" and p.get("handoff")),
            "mendeley": len(doi_bot.pending_list(proj))}


def summary(paper=None, days=30):
    g = stats.gather(paper=paper, days=days)
    o = stats.overview(g)
    return {"requests": o["passes"], "tokens_in": o["tokens_in"], "tokens_out": o["tokens_out"], "cost": o["cost"], "accepted": o["accepted"], "rejected": o["rejected"],
            "references": o["doi_added"]}


def timeline(paper=None, days=30, limit=150):
    reg = common.registry()["projects"]
    since = time.time() - days * 86400
    items = []
    for name, proj in reg.items():
        if paper and name != paper:
            continue
        if config.paper_dir(proj).exists():
            items += items_for(name, proj, since)
    items.sort(key=lambda i: -i["ts"])
    for i in items:
        i["time"], i["day"] = _clock(i["ts"]), _day(i["ts"])
    return items[:limit]
