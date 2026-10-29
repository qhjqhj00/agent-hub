"""The client as one file.

Nodes install `agenthub` by downloading /client/agenthub.pyz: a zipapp of this
package, built from the hub's own copy. One file to copy, nothing to install,
and every node runs the same code as the hub that served it.
"""
import hashlib
import io
import os
import threading
import zipfile

from .store import PKG

_lock = threading.Lock()
_cache = {"key": None, "data": b""}
SKIP_DIRS = {"__pycache__", "web"}
MAIN = b"from agenthub.cli import main\nmain()\n"


def _files():
    for root, dirs, files in os.walk(PKG):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if not f.endswith((".pyc", ".pyo")):
                yield os.path.join(root, f)


def pyz():
    files = list(_files())
    key = hashlib.sha1(repr([(f, os.path.getmtime(f)) for f in files]).encode()).hexdigest()
    with _lock:
        if _cache["key"] == key:
            return _cache["data"]
        buf = io.BytesIO()
        buf.write(b"#!/usr/bin/env python3\n")
        base = os.path.dirname(PKG)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("__main__.py", MAIN)
            for f in files:
                info = zipfile.ZipInfo(os.path.relpath(f, base), date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o755 if f.endswith(".sh") else 0o644) << 16
                with open(f, "rb") as fh:
                    z.writestr(info, fh.read())
        _cache.update(key=key, data=buf.getvalue())
        return _cache["data"]
