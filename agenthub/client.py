"""Talking to the hub from a node: config, HTTP, and who "I" am.

Config lives in ~/.agent-hub/config.json ({"url": ..., "token": ...});
HUB_URL / HUB_TOKEN override it. Every client (CLI, MCP server, hubd, the
`agenthub run` wrapper) goes through `call()`.
"""
import json
import os
import urllib.error
import urllib.request

HOME = os.path.expanduser(os.environ.get("AGENTHUB_HOME") or "~/.agent-hub")
CFG = os.path.join(HOME, "config.json")
RUN = os.path.join(HOME, "run")
DEFAULT_URL = "http://127.0.0.1:8787"


class HubUnavailable(RuntimeError):
    pass


class HubRefused(RuntimeError):
    def __init__(self, code, msg):
        RuntimeError.__init__(self, "hub %s: %s" % (code, msg))
        self.code = code


def load():
    try:
        with open(CFG) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save(**kw):
    """Merge keys into the config file (created 0600: it holds a token)."""
    d = load()
    d.update({k: v for k, v in kw.items() if v is not None})
    os.makedirs(HOME, exist_ok=True)
    tmp = CFG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(d, fh, indent=2)
    os.replace(tmp, CFG)
    return d


def url():
    return (os.environ.get("HUB_URL") or load().get("url") or DEFAULT_URL).rstrip("/")


def token():
    return os.environ.get("HUB_TOKEN") or load().get("token") or ""


def call(method, path, body=None, timeout=20):
    """JSON in, JSON out. Raises HubRefused (the hub said no) or HubUnavailable (no hub)."""
    base = url()
    req = urllib.request.Request(base + path, method=method)
    tok = token()
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
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


# ------------------------------------------------------------------ identity on this node

def run_file(pid):
    return os.path.join(RUN, "%d.json" % pid)


def wrapper_info():
    """The `agenthub run` wrapper this process runs under, if any: {session, agent, kind, ...}.

    Agents do not reliably pass environment variables on to the tools and MCP servers they
    start (Codex filters them), so the wrapper leaves a file keyed by its pid and we look
    for it up the process tree."""
    sid = os.environ.get("HUB_SESSION")
    if sid and os.environ.get("HUB_WRAPPER_PID"):
        d = _read_run(int(os.environ["HUB_WRAPPER_PID"]))
        if d and d.get("session") == sid:
            return d
    from .node import procs
    for pid in procs.ancestors(os.getpid()):
        d = _read_run(pid)
        if d:
            return d
    return None


def _read_run(pid):
    try:
        with open(run_file(pid)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
