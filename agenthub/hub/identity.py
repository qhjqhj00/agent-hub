"""Who is calling, and may they.

Every request gets a context: which node it comes from and with what authority.
`workspace@node` is only worth something if `node` cannot be forged, so the
node never comes from what a client claims about itself. It comes from one of:

  token      (default) each machine joins with its own node token
             (`agenthub node add <name>`); the token names the node. The admin
             token (data/admin.token) is the human's and the hub machine's own.
  tailscale  the hub sits on a tailnet; `tailscale whois` names the calling
             device. Tailnet peers need no token. Anything else still does.
  open       no checks at all. Only for a hub bound to loopback, or a network
             you trust completely.
"""
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import threading
import time

from .store import DATA_DIR, db, now

MODES = ("token", "tailscale", "open")
ADMIN_FILE = os.path.join(DATA_DIR, "admin.token")
COOKIE = "agenthub_token"

_cache = {}
_lock = threading.Lock()
TTL = 600


class Unauthorized(Exception):
    pass


def mode():
    m = (os.environ.get("HUB_IDENTITY") or "token").lower()
    if m not in MODES:
        raise SystemExit("HUB_IDENTITY must be one of %s" % ", ".join(MODES))
    return m


def admin_token():
    """The admin token, created on first use. Kept in the data dir, readable by the hub's user only."""
    t = os.environ.get("HUB_TOKEN", "")
    if t:
        return t
    try:
        with open(ADMIN_FILE) as fh:
            t = fh.read().strip()
    except OSError:
        t = ""
    if not t:
        os.makedirs(DATA_DIR, exist_ok=True)
        t = "ahk_" + secrets.token_urlsafe(24)
        fd = os.open(ADMIN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(t + "\n")
    return t


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_node_token(name):
    """A fresh token for node `name` (replacing any previous one). Shown once, stored hashed."""
    tok = "ahn_" + secrets.token_urlsafe(24)
    with db() as conn:
        conn.execute("INSERT INTO node_tokens (name,token_hash,created_at) VALUES (?,?,?) "
                     "ON CONFLICT(name) DO UPDATE SET token_hash=excluded.token_hash, created_at=excluded.created_at, "
                     "last_used=NULL", (name, _hash(tok), now()))
    return tok


def revoke_node_token(name):
    with db() as conn:
        return conn.execute("DELETE FROM node_tokens WHERE name=?", (name,)).rowcount


def node_tokens():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT name, created_at, last_used FROM node_tokens ORDER BY name")]


_used = {}


def _node_for(token):
    h = _hash(token)
    with db() as conn:
        r = conn.execute("SELECT name FROM node_tokens WHERE token_hash=?", (h,)).fetchone()
        if r and time.time() - _used.get(r["name"], 0) > 60:
            _used[r["name"]] = time.time()
            conn.execute("UPDATE node_tokens SET last_used=? WHERE name=?", (now(), r["name"]))
    return r["name"] if r else None


def presented(headers, query=None):
    """The token on a request: bearer header, cookie, basic-auth password (git), or ?token=."""
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    if auth.startswith("Basic "):
        import base64
        try:
            return base64.b64decode(auth[6:]).decode().partition(":")[2]
        except Exception:
            return ""
    for part in headers.get("Cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    if query and query.get("token"):
        return query["token"][0]
    return ""


def is_loopback(ip):
    return ip.startswith("127.") or ip in ("::1", "::ffff:127.0.0.1")


def context(ip, headers, query=None):
    """{'node', 'ip', 'owner', 'os', 'role'} for a request, or raise Unauthorized.

    role: admin (the admin token), node (a node token), peer (trusted by the network)."""
    m = mode()
    tok = presented(headers, query)
    claimed = (headers.get("X-Hub-Node") or "").strip().lower()
    if tok and hmac.compare_digest(tok, admin_token()):
        # The admin is trusted to say which machine it is on (the hub's own node, scripts).
        node = claimed or (local()["node"] if is_loopback(ip) else "") or _whois_node(ip) or ip
        return {"node": node, "ip": ip, "owner": "", "os": "", "role": "admin"}
    if tok:
        name = _node_for(tok)
        if name:
            return {"node": name, "ip": ip, "owner": "", "os": "", "role": "node"}
        raise Unauthorized("bad token")
    if m == "open":
        node = claimed or (local()["node"] if is_loopback(ip) else ip)
        return {"node": node, "ip": ip, "owner": "", "os": "", "role": "peer"}
    if m == "tailscale":
        info = whois(ip)
        if info.get("node"):
            return dict(info, role="peer")
    raise Unauthorized("token required")


# ---------------------------------------------------------------- tailscale

def _whois_node(ip):
    return whois(ip).get("node", "") if mode() == "tailscale" else ""


def whois(ip):
    """{'node': short hostname, 'owner': login, 'os': ...} or {} if tailscaled does not know the address."""
    if not ip:
        return {}
    if is_loopback(ip):
        return local()
    with _lock:
        hit = _cache.get(ip)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    info = {}
    try:
        out = subprocess.run(["tailscale", "whois", "--json", ip], capture_output=True,
                             timeout=5, text=True)
        if out.returncode == 0:
            d = json.loads(out.stdout)
            node = d.get("Node") or {}
            name = (node.get("ComputedName") or node.get("Name") or "").split(".")[0]
            info = {"node": name.lower(), "owner": (d.get("UserProfile") or {}).get("LoginName", ""),
                    "os": (node.get("Hostinfo") or {}).get("OS", ""), "ip": ip}
    except Exception:
        info = {}
    with _lock:
        _cache[ip] = (time.time(), info)
    return info


_local = {}


def local():
    """This machine: its tailnet name when it has one, else its hostname."""
    if not _local:
        name = os.environ.get("HUB_NODE_NAME", "")
        if not name and mode() == "tailscale":
            try:
                out = subprocess.run(["tailscale", "status", "--self", "--json"], capture_output=True,
                                     timeout=5, text=True)
                d = json.loads(out.stdout)["Self"]
                name = d["DNSName"].split(".")[0]
            except Exception:
                name = ""
        _local.update({"node": (name or socket.gethostname().split(".")[0]).lower(), "owner": "", "os": "",
                       "ip": "127.0.0.1"})
    return dict(_local)
