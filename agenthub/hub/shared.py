"""The shared board: what every agent may use.

Two kinds of entry:

  vault  secrets -- api keys, tokens, a working .env. The value leaves the hub
         only when someone asks for that name, and every ask is logged. Never
         pushed, never in the feed, never on Telegram.
  notes  non-secret knowledge -- endpoints, model lists, conventions, how to
         use a service and its pitfalls. Edited in place, history kept. A note
         can reference vault names, so the method and the credential stay
         separate but findable from each other.

Agents learn what exists from `manifest()`, which goes into each session's
channel instructions: names and one-line purposes, never values.
"""
import json
import re
import shlex

from . import bus
from .core import HubError
from .store import HUMAN, db, jloads, now, resolve, scopes_of

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MANIFEST_MAX = 30


def _q(q, key, default=""):
    return (q.get(key) or [default])[0]


def _by(b, q=None):
    return (b or {}).get("agent") or (q and _q(q, "agent")) or HUMAN


def _tags(v):
    if isinstance(v, str):
        v = [t.strip() for t in v.split(",")]
    return sorted({t.strip().lower() for t in (v or []) if t and t.strip()})


def _env(v):
    """{"VAR": "json_key" | ""}; also accepts "VAR=key" lines."""
    if isinstance(v, str):
        out = {}
        for line in v.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            var, _, key = line.partition("=")
            out[var.strip()] = key.strip()
        v = out
    v = v or {}
    for var in v:
        if not ENV_RE.match(var):
            raise HubError(400, "bad env var name %r" % var)
    return v


def _vault_meta(conn, r):
    d = {k: r[k] for k in ("name", "note", "owner", "project", "version", "updated_at")}
    d["tags"] = jloads(r["tags"], [])
    d["env"] = jloads(r["env"], {})
    last = conn.execute("SELECT agent, ts FROM vault_log WHERE name=? AND action='get' ORDER BY id DESC LIMIT 1",
                        (r["name"],)).fetchone()
    d["last_get"] = dict(last) if last else None
    d["gets"] = conn.execute("SELECT COUNT(*) FROM vault_log WHERE name=? AND action='get'", (r["name"],)).fetchone()[0]
    d["is_json"] = isinstance(jloads(r["value"], None), dict)
    return d


def _note_dict(r, full=True):
    d = dict(r)
    d["tags"] = jloads(d["tags"], [])
    d["refs"] = jloads(d["refs"], [])
    if not full:
        d.pop("body", None)
    return d


def _vlog(conn, name, agent, action):
    conn.execute("INSERT INTO vault_log (name,agent,action,ts) VALUES (?,?,?,?)", (name, agent, action, now()))


# ---------------------------------------------------------------- the board

def api_shared(ctx, q, b):
    """Everything on the board, without secret values."""
    with db() as conn:
        vault = [_vault_meta(conn, r) for r in conn.execute("SELECT * FROM vault ORDER BY name")]
        notes = [_note_dict(r) for r in conn.execute("SELECT * FROM notes ORDER BY name")]
    tags = sorted({t for x in vault + notes for t in x["tags"]})
    return 200, {"vault": vault, "notes": notes, "tags": tags}


def manifest(conn, agent=None):
    """The few lines every session is told: what exists and what it is for. No values."""
    projects = {s["project"] for s in scopes_of(conn, agent)} if agent else set()

    def visible(r):
        return not r["project"] or r["project"] in projects

    vs = [r for r in conn.execute("SELECT name, note, tags, project, env FROM vault ORDER BY name") if visible(r)]
    ns = [r for r in conn.execute("SELECT name, title, tags, project FROM notes ORDER BY name") if visible(r)]
    if not vs and not ns:
        return ""
    lines = []

    def tagstr(r):
        t = jloads(r["tags"], [])
        return "[%s] " % ",".join(t) if t else ""

    for r in vs[:MANIFEST_MAX]:
        env = list(jloads(r["env"], {}))
        lines.append("- secret %s %s%s%s" % (r["name"], tagstr(r), _first_line(r["note"]),
                                             "  (env: %s)" % ", ".join(env) if env else ""))
    for r in ns[:max(0, MANIFEST_MAX - len(vs))]:
        lines.append("- note   %s %s%s" % (r["name"], tagstr(r), _first_line(r["title"])))
    more = len(vs) + len(ns) - len(lines)
    if more > 0:
        lines.append("- ... and %d more (hub_shared lists everything)" % more)
    return "\n".join(lines)


def _first_line(s, n=110):
    s = (s or "").strip().splitlines()[0] if (s or "").strip() else ""
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------- vault

def api_vault_list(ctx, q, b):
    with db() as conn:
        rows = [_vault_meta(conn, r) for r in conn.execute("SELECT * FROM vault ORDER BY name")]
    return 200, {"vault": rows}


def api_vault_get(ctx, q, b, name):
    with db() as conn:
        r = conn.execute("SELECT * FROM vault WHERE name=?", (name,)).fetchone()
        if not r:
            raise HubError(404, "no vault entry %r" % name)
        who = _q(q, "agent") or "%s@%s" % (HUMAN, ctx.get("node", "?"))
        _vlog(conn, name, who, "get")
        out = _vault_meta(conn, r)
        out["value"] = r["value"]
    bus.bump()
    return 200, out


def env_lines(name, value, env, fmt="export"):
    data = jloads(value, None)
    lines = []
    for var, key in env.items():
        if key:
            if not isinstance(data, dict) or key not in data:
                raise HubError(400, "%s: value has no JSON key %r for %s" % (name, key, var))
            v = data[key]
            v = v if isinstance(v, str) else json.dumps(v)
        else:
            v = value
        lines.append(("export %s=%s" if fmt == "export" else "%s=%s") % (var, shlex.quote(v)))
    return lines


def api_vault_env(ctx, q, b, name):
    """Shell lines for the entry's env mapping: `eval "$(agenthub env NAME)"` or `> .env`."""
    fmt = _q(q, "format", "export")
    with db() as conn:
        r = conn.execute("SELECT * FROM vault WHERE name=?", (name,)).fetchone()
        if not r:
            raise HubError(404, "no vault entry %r" % name)
        env = jloads(r["env"], {})
        if not env:
            raise HubError(400, "%s has no env mapping; set one on the Shared page or with --env" % name)
        lines = env_lines(name, r["value"], env, fmt)
        _vlog(conn, name, _q(q, "agent") or "%s@%s" % (HUMAN, ctx.get("node", "?")), "get")
    bus.bump()
    return 200, {"name": name, "lines": lines}


def api_vault_put(ctx, q, b, name):
    """Create or update. Without `value`, only the metadata of an existing entry changes."""
    if not NAME_RE.match(name):
        raise HubError(400, "name: letters, digits, - _ . (max 64)")
    by = _by(b)
    with db() as conn:
        r = conn.execute("SELECT * FROM vault WHERE name=?", (name,)).fetchone()
        has_value = "value" in b and b["value"] is not None and b["value"] != ""
        if not r and not has_value:
            raise HubError(400, "value required for a new entry")
        tags = _tags(b["tags"]) if "tags" in b else (jloads(r["tags"], []) if r else [])
        env = _env(b["env"]) if "env" in b else (jloads(r["env"], {}) if r else {})
        note = b["note"] if "note" in b else (r["note"] if r else "")
        project = b["project"] if "project" in b else (r["project"] if r else "")
        value = b["value"] if has_value else r["value"]
        if env:
            env_lines(name, value, env)  # validate the mapping against the value now, not at use
        changed = not r or value != r["value"]
        ver = (r["version"] + 1 if changed else r["version"]) if r else 1
        conn.execute(
            "INSERT OR REPLACE INTO vault (name,value,note,owner,project,version,updated_at,tags,env) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (name, value, note, by if changed else r["owner"], project, ver, now(),
             json.dumps(tags), json.dumps(env)))
        _vlog(conn, name, by, "put" if changed else "edit")
    bus.bump()
    return 200, {"name": name, "version": ver, "value_changed": changed}


def api_vault_delete(ctx, q, b, name):
    with db() as conn:
        conn.execute("DELETE FROM vault WHERE name=?", (name,))
        _vlog(conn, name, _by(b, q), "delete")
    bus.bump()
    return 200, {"ok": True}


def api_vault_log(ctx, q, b, name):
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT agent, action, ts FROM vault_log WHERE name=? ORDER BY id DESC LIMIT 100", (name,))]
    return 200, {"log": rows}


# ---------------------------------------------------------------- notes

def api_notes(ctx, q, b):
    with db() as conn:
        rows = [_note_dict(r, full=bool(_q(q, "full"))) for r in conn.execute("SELECT * FROM notes ORDER BY name")]
    return 200, {"notes": rows}


def api_note(ctx, q, b, name):
    with db() as conn:
        r = conn.execute("SELECT * FROM notes WHERE name=?", (name,)).fetchone()
        if not r:
            raise HubError(404, "no note %r" % name)
        d = _note_dict(r)
        d["history"] = [dict(x) for x in conn.execute(
            "SELECT version, agent, ts, body FROM note_log WHERE name=? ORDER BY id DESC LIMIT 20", (name,))]
    return 200, d


def api_note_put(ctx, q, b, name):
    if not NAME_RE.match(name):
        raise HubError(400, "name: letters, digits, - _ . (max 64)")
    by = _by(b)
    with db() as conn:
        r = conn.execute("SELECT * FROM notes WHERE name=?", (name,)).fetchone()
        if not r and not (b.get("body") or b.get("title")):
            raise HubError(400, "title or body required")
        cur = _note_dict(r) if r else {"title": "", "body": "", "tags": [], "refs": [], "project": "", "version": 0}
        title = b.get("title", cur["title"])
        body = b.get("body", cur["body"])
        tags = _tags(b["tags"]) if "tags" in b else cur["tags"]
        refs = _tags(b["refs"]) if "refs" in b else cur["refs"]
        project = b.get("project", cur["project"])
        unknown = [x for x in refs if not conn.execute("SELECT 1 FROM vault WHERE lower(name)=?", (x,)).fetchone()]
        if unknown:
            raise HubError(400, "refs must be vault names; unknown: %s" % ", ".join(unknown))
        refs = [conn.execute("SELECT name FROM vault WHERE lower(name)=?", (x,)).fetchone()[0] for x in refs]
        ver = cur["version"] + 1 if body != cur["body"] or not r else cur["version"]
        conn.execute("INSERT OR REPLACE INTO notes (name,title,body,tags,refs,project,owner,version,updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (name, title, body, json.dumps(tags), json.dumps(refs), project, by, ver, now()))
        if ver != cur["version"]:
            conn.execute("INSERT INTO note_log (name,version,body,agent,ts) VALUES (?,?,?,?,?)",
                         (name, ver, body, by, now()))
    bus.bump()
    return 200, {"name": name, "version": ver}


def api_note_delete(ctx, q, b, name):
    with db() as conn:
        conn.execute("DELETE FROM notes WHERE name=?", (name,))
    bus.bump()
    return 200, {"ok": True}


ROUTES = [
    ("GET", r"^/v1/shared$", api_shared),
    ("GET", r"^/v1/vault$", api_vault_list),
    ("GET", r"^/v1/vault/([^/]+)/env$", api_vault_env),
    ("GET", r"^/v1/vault/([^/]+)/log$", api_vault_log),
    ("GET", r"^/v1/vault/([^/]+)$", api_vault_get),
    ("POST", r"^/v1/vault/([^/]+)$", api_vault_put),
    ("DELETE", r"^/v1/vault/([^/]+)$", api_vault_delete),
    ("GET", r"^/v1/notes$", api_notes),
    ("GET", r"^/v1/notes/([^/]+)$", api_note),
    ("POST", r"^/v1/notes/([^/]+)$", api_note_put),
    ("DELETE", r"^/v1/notes/([^/]+)$", api_note_delete),
]
