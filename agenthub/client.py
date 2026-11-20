"""Talking to the hub from a node: config, HTTP, and the local session files.

Config lives in ~/.agent-hub/config.json ({"url": ..., "token": ...}); HUB_URL /
HUB_TOKEN override it. The token there is the *node* token (who this machine is).

A running agent is a hub *session*: `POST /v1/sessions` returns a session id and
token, which every later request carries as `X-Hub-Session`. Whoever registers
(the MCP server, the pi extension) writes ~/.agent-hub/sessions/<name>.json (0600)
so the other processes serving the same agent -- a bridge, a hook -- act as that
session too. See docs/refactor/api.md.
"""
import json
import os
import urllib.error
import urllib.request

HOME = os.path.expanduser(os.environ.get("AGENTHUB_HOME") or "~/.agent-hub")
CFG = os.path.join(HOME, "config.json")
SESSIONS = os.path.join(HOME, "sessions")
RUN = os.path.join(HOME, "run")  # <claude pid>.chan / .conv: how Claude Code hooks find their MCP server
DEFAULT_URL = "http://127.0.0.1:8787"


class HubUnavailable(RuntimeError):
    pass


class HubRefused(RuntimeError):
    def __init__(self, code, msg):
        RuntimeError.__init__(self, "hub %s: %s" % (code, msg))
        self.code = code


def _write_private(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def load():
    try:
        with open(CFG) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save(**kw):
    """Merge keys into the config file (0600: it holds a token)."""
    d = load()
    d.update({k: v for k, v in kw.items() if v is not None})
    _write_private(CFG, d)
    return d


def url():
    return (os.environ.get("HUB_URL") or load().get("url") or DEFAULT_URL).rstrip("/")


def token():
    return os.environ.get("HUB_TOKEN") or load().get("token") or ""


def call(method, path, body=None, timeout=20, session=None, base=None):
    """JSON in, JSON out. `session` is a session token. Raises HubRefused or HubUnavailable."""
    base = (base or url()).rstrip("/")
    req = urllib.request.Request(base + path, method=method)
    tok = token()
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    if session:
        req.add_header("X-Hub-Session", session)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error")
        except Exception:
            msg = e.reason
        raise HubRefused(e.code, msg)
    except (urllib.error.URLError, OSError) as e:
        raise HubUnavailable("hub unreachable at %s (%s)" % (base, getattr(e, "reason", e)))


def fetch(path, timeout=60):
    """Raw bytes (the client bundle, skill files)."""
    req = urllib.request.Request(url() + path)
    tok = token()
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ------------------------------------------------------------------ session files

def session_path(name):
    return os.path.join(SESSIONS, "%s.json" % name)


def write_session(name, data):
    _write_private(session_path(name), data)


def read_session(name):
    try:
        with open(session_path(name)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def remove_session(name, session_id=None):
    """Delete the file, but only while it still describes `session_id` (a newer session may own it)."""
    d = read_session(name)
    if d and (session_id is None or d.get("session_id") == session_id):
        try:
            os.remove(session_path(name))
        except OSError:
            pass


def sessions():
    out = []
    try:
        names = sorted(os.listdir(SESSIONS))
    except OSError:
        return out
    for fn in names:
        if fn.endswith(".json"):
            d = read_session(fn[:-5])
            if d:
                out.append(d)
    return out


def session_call(sess, method, path, body=None, timeout=20):
    """A request made as the session a session file describes."""
    return call(method, path, body, timeout, session=sess["token"], base=sess.get("hub"))
