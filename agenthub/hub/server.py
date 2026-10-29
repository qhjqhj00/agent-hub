"""HTTP plumbing: routing, caller identity, SSE, static files, and the
maintenance loop that keeps queued work moving.
"""
import json
import os
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api, bus, core, identity, marketplace
from .core import HubError
from .store import DATA_DIR, HUMAN, PKG, db, now

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
INSTALL_SH = os.path.join(PKG, "node", "install.sh")
PUBLIC_URL = os.environ.get("HUB_URL", "")
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
        ".md": "text/markdown; charset=utf-8"}
# reachable without a token: the dashboard shell (it asks for the token itself), the installer
# and the client it downloads (both are the open-source code, nothing team-specific)
PUBLIC = ("/", "/index.html", "/healthz", "/install.sh", "/client/agenthub.pyz", "/v1/login", "/v1/config")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "agent-hub/" + __import__("agenthub").__version__

    def log_message(self, fmt, *args):
        if os.environ.get("HUB_ACCESS_LOG"):
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    # -- helpers
    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else (
            json.dumps(body, ensure_ascii=False).encode() if ctype == "application/json" else body.encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _ctx(self, query=None):
        return identity.context(self.client_address[0], self.headers, query)

    def _hub_url(self):
        return PUBLIC_URL or "http://%s" % self.headers.get("Host", "localhost")

    def _static(self, path):
        full = os.path.realpath(path)
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        with open(full, "rb") as fh:
            data = fh.read()
        ext = os.path.splitext(full)[1]
        self._send(200, data, MIME.get(ext, "application/octet-stream"))

    # -- verbs
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        u = urllib.parse.urlsplit(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        try:
            public = path in PUBLIC or path.startswith("/static/")
            try:
                ctx = self._ctx(q)
            except identity.Unauthorized as e:
                if not public:
                    extra = {"WWW-Authenticate": 'Basic realm="agent-hub"'} if path.startswith("/marketplace.git/") else None
                    return self._send(401, {"error": "%s (run `agenthub token` on the hub to get one)" % e}, extra=extra)
                ctx = None
            if path == "/" and q.get("token") and ctx:
                # a link printed by `agenthub up`: keep the token in a cookie and drop it from the address bar
                return self._send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": self._cookie(q["token"][0])})
            if path == "/v1/login" and method == "POST":
                return self._login()
            if path.startswith("/marketplace.git/"):
                return self._git(path, u.query, method)
            if method == "GET" and not path.startswith("/v1/") and path != "/healthz":
                return self._serve_file(path)
            if path == "/v1/stream":
                return self._stream()
            body = {}
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                raw = self.rfile.read(n)
                try:
                    body = json.loads(raw.decode() or "{}")
                except ValueError:
                    return self._send(400, {"error": "body must be JSON"})
            for m, rx, fn in api.ROUTES:
                if m != method:
                    continue
                mt = rx.match(path)
                if mt:
                    if fn.__name__.startswith("api_admin") and (not ctx or ctx["role"] != "admin"):
                        return self._send(403, {"error": "admin token required"})
                    groups = [urllib.parse.unquote(g) for g in mt.groups()]
                    code, out = fn(ctx or {}, q, body, *groups)
                    return self._send(code, out)
            self._send(404, {"error": "no route %s %s" % (method, path)})
        except HubError as e:
            self._send(e.code, {"error": str(e)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # pragma: no cover
            traceback.print_exc()
            try:
                self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
            except Exception:
                pass

    def _cookie(self, token):
        return "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000" % (identity.COOKIE, token)

    def _login(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            tok = (json.loads(self.rfile.read(n).decode() or "{}").get("token") or "").strip()
        except ValueError:
            tok = ""
        try:
            identity.context(self.client_address[0], {"Authorization": "Bearer " + tok})
        except identity.Unauthorized:
            time.sleep(1)
            return self._send(401, {"error": "wrong token"})
        return self._send(200, {"ok": True}, extra={"Set-Cookie": self._cookie(tok)})

    def _git(self, path, query, method):
        """Smart HTTP via `git http-backend` (Claude Code clones shallow, which dumb HTTP cannot serve)."""
        import subprocess
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        env = dict(os.environ, GIT_PROJECT_ROOT=os.path.dirname(marketplace.BARE), GIT_HTTP_EXPORT_ALL="1",
                   PATH_INFO=path, QUERY_STRING=query or "", REQUEST_METHOD=method,
                   CONTENT_TYPE=self.headers.get("Content-Type", ""), CONTENT_LENGTH=str(len(body)),
                   REMOTE_ADDR=self.client_address[0], HTTP_CONTENT_ENCODING=self.headers.get("Content-Encoding", ""),
                   GIT_PROTOCOL=self.headers.get("Git-Protocol", ""))
        if not path.endswith(("/info/refs", "/git-upload-pack", "/HEAD")) and "/objects/" not in path:
            return self._send(403, {"error": "read-only"})
        if path.endswith("/git-receive-pack"):
            return self._send(403, {"error": "read-only"})
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True, timeout=120).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        if not _:
            head, _, payload = out.partition(b"\n\n")
        code, headers = 200, {}
        for line in head.decode("latin-1").splitlines():
            k, _, v = line.partition(":")
            if k.lower() == "status":
                code = int(v.strip().split()[0])
            elif k:
                headers[k.strip()] = v.strip()
        ctype = headers.pop("Content-Type", "application/octet-stream")
        headers.pop("Content-Length", None)
        self._send(code, payload, ctype, headers)

    def _serve_file(self, path):
        if path in ("/", "/index.html"):
            return self._static(os.path.join(WEB, "index.html"))
        if path.startswith("/static/"):
            return self._static(os.path.join(WEB, path[len("/static/"):].replace("..", "")))
        if path == "/client/agenthub.pyz":
            from . import bundle
            return self._send(200, bundle.pyz(), "application/octet-stream")
        if path == "/install.sh":
            with open(INSTALL_SH) as fh:
                return self._send(200, fh.read().replace("__HUB_URL__", self._hub_url()), "text/x-shellscript")
        self._send(404, {"error": "not found"})

    def _stream(self):
        """SSE: one `change` event per bus version bump. The page re-fetches what it shows."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True
        v = -1
        try:
            while True:
                nv = bus.wait(v, 25)
                if nv != v:
                    v = nv
                    self.wfile.write(("event: change\ndata: %d\n\n" % v).encode())
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                time.sleep(0.4)  # coalesce bursts of writes into one refresh
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def _vt(v):
    import re
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3])


def _hubd_version():
    return __import__("agenthub").__version__


def maintenance():
    """Every 30s: re-wake agents with stale queued work, expire what nobody will answer."""
    while True:
        try:
            with db() as conn:
                # sent into a poll that was never acknowledged: the session died holding it
                conn.execute("UPDATE deliveries SET sent_at=NULL, session='' WHERE delivered_at IS NULL AND "
                             "sent_at < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-75 seconds') AND agent!=?", (HUMAN,))
                for r in conn.execute(
                        "SELECT DISTINCT agent FROM deliveries WHERE delivered_at IS NULL AND agent!=? "
                        "AND kind!='verdict' AND created_at < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-60 seconds')",
                        (HUMAN,)).fetchall():
                    core.ensure_listening(conn, r["agent"], "queued work")
                # taken by a poll that was never acknowledged: that hubd died mid-poll (restart,
                # self-update) and the order went into a dead socket -- hand it out again
                for n in conn.execute("SELECT name, info FROM nodes").fetchall():
                    if True:
                        if conn.execute("UPDATE orders SET state='pending', taken_at=NULL WHERE node=? AND "
                                        "state='taken' AND acked_at IS NULL AND taken_at < "
                                        "strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-75 seconds')",
                                        (n["name"],)).rowcount:
                            bus.bump()
                conn.execute("UPDATE orders SET state='stale', done_at=? WHERE state='taken' AND "
                             "taken_at < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-10 minutes')", (now(),))
                conn.execute("UPDATE orders SET state='expired', done_at=? WHERE state='pending' AND "
                             "created_at < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-1 day')", (now(),))
                conn.execute("UPDATE sessions SET ended_at=? WHERE ended_at IS NULL AND "
                             "last_seen < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-15 minutes')", (now(),))
                # nodes running an older client installed from this hub (not pip): bring them up to date
                want = _hubd_version()
                for n in conn.execute("SELECT name, info FROM nodes WHERE hubd_seen > "
                                      "strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-2 minutes')").fetchall():
                    info = json.loads(n["info"] or "{}")
                    have = info.get("hubd", "0")
                    if want and _vt(have) < _vt(want) and info.get("install") == "pyz" and not conn.execute(
                            "SELECT 1 FROM orders WHERE node=? AND kind='self_update' AND created_at > "
                            "strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-15 minutes')", (n["name"],)).fetchone():
                        core.order(conn, n["name"], "self_update", "", {}, "hubd %s -> %s" % (have, want))
                conn.execute("UPDATE permissions SET state='expired' WHERE state='pending' AND "
                             "created_at < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-30 minutes')")
                # verdicts nobody picked up are worthless after the dialog is gone
                conn.execute("DELETE FROM deliveries WHERE kind='verdict' AND delivered_at IS NULL AND "
                             "created_at < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-30 minutes')")
        except Exception:
            traceback.print_exc()
        time.sleep(30)


def serve(host, port, ready=None):
    """Run the hub until the process ends. `ready` (an Event) is set once it accepts connections."""
    m = identity.mode()
    if m == "open" and not identity.is_loopback(host) and not os.environ.get("HUB_I_KNOW_ITS_OPEN"):
        raise SystemExit("refusing to serve without authentication on %s; use --identity token, bind to "
                         "127.0.0.1, or set HUB_I_KNOW_ITS_OPEN=1" % host)
    identity.admin_token()
    try:
        marketplace.sync()
    except Exception:
        traceback.print_exc()
    threading.Thread(target=maintenance, daemon=True).start()
    from . import notify
    notify.start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print("agent-hub %s listening on http://%s:%d (identity: %s, data: %s)"
          % (__import__("agenthub").__version__, host, httpd.server_port, m, DATA_DIR), flush=True)
    if ready is not None:
        ready.port = httpd.server_port
        ready.set()
    httpd.serve_forever()
