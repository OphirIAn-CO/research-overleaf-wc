"""A tiny PDF writer for tests: pages of plain text lines in a standard font, with an optional title and author. No dependencies; pdftotext reads the result."""
import textwrap
from pathlib import Path


def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1", "replace").decode("latin-1")


def make_pdf(path, pages, title=None, author=None, size=11, width=88):
    """pages: a list of pages; a page is a list of paragraphs (strings; a string starting with '# ' is a heading). Long paragraphs are wrapped; paragraphs are separated by a gap."""
    objs = []                                           # object bodies, 1-based ids

    def add(body):
        objs.append(body)
        return len(objs)

    catalog = add(None)
    pages_id = add(None)
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    bold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    kids = []
    for page in pages:
        y, ops = 740, []
        for par in page:
            head = par.startswith("# ")
            text = par[2:] if head else par
            for ln in (textwrap.wrap(text, width) or [""]):
                ops.append(f"BT /{'F2' if head else 'F1'} {size + 2 if head else size} Tf 72 {y} Td ({_esc(ln)}) Tj ET")
                y -= size + 4
            y -= size + 14
        stream = "\n".join(ops)
        cid = add(f"<< /Length {len(stream.encode('latin-1', 'replace'))} >>\nstream\n{stream}\nendstream")
        kids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] /Contents {cid} 0 R /Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> >>"))
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>"
    objs[pages_id - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    info = None
    if title or author:
        info = add("<< " + (f"/Title ({_esc(title)}) " if title else "") + (f"/Author ({_esc(author)}) " if author else "") + ">>")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1", "replace")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R {f'/Info {info} 0 R ' if info else ''}>>\nstartxref\n{xref}\n%%EOF\n".encode()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(out))
    return Path(path)
