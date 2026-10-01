"""SQLite + hashing helpers shared by the book and paper indexes. Stdlib only.

Rules every index follows:
  * rollback-journal mode, never WAL: WAL's shared-memory file is unsafe across a Docker bind mount, and the
    host (Claude's MCP server) and the container (web app) can touch the same file;
  * short transactions plus a busy timeout, and a per-index write lease for multi-statement refreshes;
  * an index is a DERIVED CACHE: `open_checked` runs quick_check and rebuilds from source when it is damaged.
"""
import hashlib
import json
import os
import socket
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

BUSY_MS = 8000


def fts5_available():
    try:
        con = sqlite3.connect(":memory:")
        con.execute("create virtual table t using fts5(a, tokenize='porter unicode61')")
        con.close()
        return True
    except sqlite3.Error:
        return False


def connect(path, readonly=False):
    path = Path(path)
    if readonly:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=BUSY_MS / 1000)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=BUSY_MS / 1000)
        con.execute("PRAGMA journal_mode=DELETE")
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    return con


def open_checked(path, rebuild):
    """Open a derived index. If it is missing or fails quick_check, move it aside and call rebuild()
    (which must create the file at `path`), then open the fresh one."""
    path = Path(path)
    if path.exists():
        try:
            con = connect(path)
            if con.execute("PRAGMA quick_check").fetchone()[0] == "ok":
                return con
            con.close()
        except sqlite3.DatabaseError:
            pass
        os.replace(path, path.with_name(path.name + ".corrupt"))
    rebuild()
    return connect(path)


@contextmanager
def write_lease(db_path, timeout=30.0, stale=300.0):
    """Exclusive writer lease for one index, as an O_EXCL lease file next to it.
    Staleness is by file age, not pid: host and container have different pid namespaces."""
    lease = Path(str(db_path) + ".lease")
    lease.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    me = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "ts": time.time()}).encode()
    while True:
        try:
            fd = os.open(lease, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, me)
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lease.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale:
                try:
                    lease.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.time() > deadline:
                raise TimeoutError(f"another process is writing {Path(db_path).name}; try again in a moment")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lease.unlink()
        except FileNotFoundError:
            pass


def meta_get(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def meta_set(con, key, value):
    con.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def approx_tokens(s):
    """Rough token estimate (about 4 characters per token for English). An estimate, used for budgets and
    the Monitor; the OpenAI response reports exact usage."""
    return max(1, (len(s) + 3) // 4) if s else 0
