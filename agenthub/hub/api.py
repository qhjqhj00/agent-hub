"""The /v1 handlers and their route table (contract: docs/refactor/api.md).

Each handler is `fn(ctx, query, body, *path_groups) -> (status, dict)`.
`ctx["node"]` comes from identity.context() (node token or tailnet), never from the
client. An agent is known only by its session token (`X-Hub-Session`), which the
server puts in `ctx["session_token"]`; names in request bodies are ignored.
"""
import re

from . import bus, core, identity, marketplace, shared
from .core import HubError
from .store import HUMAN, db, jloads


def _q(q, key, default=""):
    return (q.get(key) or [default])[0]


def _qd(q):
    return {k: v[0] for k, v in q.items() if v}


def _me(conn, ctx):
    """The calling agent's session. Every agent-facing route needs one."""
    s = core.session_for(conn, ctx, ctx.get("session_token"))
    if not s:
        raise HubError(401, "X-Hub-Session required: register first (POST /v1/sessions)")
    ctx["agent"] = s["agent"]
    return s


def _actor(conn, ctx):
    """An agent session, or the human when the caller holds the admin token (the dashboard)."""
    if ctx.get("session_token"):
        return _me(conn, ctx)["agent"]
    if ctx.get("role") == "admin":
        return HUMAN
    raise HubError(401, "X-Hub-Session required: register first (POST /v1/sessions)")


# ---------------------------------------------------------------- sessions

def api_register(ctx, q, b):
    with db() as conn:
        return 200, core.register(conn, ctx, b)


def _session_op(fn):
    def handler(ctx, q, b, sid):
        with db() as conn:
            s = _me(conn, ctx)
            core.own(s, sid)
            out = fn(conn, s, b)
        return 200, out or {"ok": True}
    handler.__name__ = "api_session_" + fn.__name__
    return handler


api_session_end = _session_op(lambda conn, s, b: core.end_session(conn, s))
api_session_beat = _session_op(lambda conn, s, b: {"ok": True, "queued": core._queued(conn, s["agent"])})
api_session_state = _session_op(lambda conn, s, b: core.set_state(conn, s, b.get("state")))
api_session_meta = _session_op(lambda conn, s, b: core.set_meta(conn, s, b))
api_session_delivered = _session_op(lambda conn, s, b: core.delivered(conn, s, b.get("ids")))


def api_session_pull(ctx, q, b, sid):
    return 200, core.pull(db, ctx, ctx.get("session_token"), sid, _q(q, "wait", "50") or 0)


def api_whoami(ctx, q, b):
    with db() as conn:
        if not ctx.get("session_token"):
            return 200, {"agent": None, "node": ctx.get("node"), "role": ctx.get("role")}
        s = _me(conn, ctx)
        return 200, {"agent": core.agent_card(conn, core.agent_row(conn, s["agent"])),
                     "session": core.session_dict(s)}


def api_inbox(ctx, q, b):
    with db() as conn:
        return 200, core.inbox(conn, _me(conn, ctx))


# ---------------------------------------------------------------- agents / collaboration

def api_agents(ctx, q, b):
    with db() as conn:
        if ctx.get("session_token"):
            _me(conn, ctx)
        which = {"1": True, "only": True, "all": None}.get(_q(q, "archived"), False)
        return 200, {"agents": core.list_agents(conn, _q(q, "project"), which)}


def api_agent(ctx, q, b, name):
    with db() as conn:
        r = core.agent_row(conn, name)
        if not r:
            raise HubError(404, "no agent %r" % name)
        card = core.agent_card(conn, r)
        card["tasks"] = core.list_tasks(conn, {"owner": name, "limit": 20})
        card["sessions"] = [core.session_dict(s) for s in conn.execute(
            "SELECT * FROM sessions WHERE agent=? ORDER BY started_at DESC LIMIT 10", (name,))]
        return 200, card


def api_post(ctx, q, b):
    with db() as conn:
        return 200, core.send(conn, _actor(conn, ctx), b.get("to") or "", b.get("body") or "", b.get("reply_to"))


def api_thread(ctx, q, b, thread_id):
    with db() as conn:
        return 200, {"posts": core.thread(conn, thread_id)}


def api_delegate(ctx, q, b):
    with db() as conn:
        by = _actor(conn, ctx)
        t = core.delegate(conn, by, b.get("to") or "", b.get("title") or "", b.get("goal") or "",
                          b.get("context") or "", b.get("acceptance") or "", b.get("project") or "",
                          b.get("parent_id"))
        return 200, {"task": t}


def api_tasks(ctx, q, b):
    with db() as conn:
        return 200, {"tasks": core.list_tasks(conn, _qd(q))}


def api_task(ctx, q, b, tid):
    with db() as conn:
        t = core.task_dict(conn, core.get_task(conn, tid), full=True)
        return 200, {"task": t, "posts": t.pop("posts")}


def _task_write(conn, by, tid, b):
    kind = b.get("kind") or ""
    if kind == "close":
        return core.close(conn, tid, by, b.get("state") or "", b.get("body") or "")
    return core.update(conn, tid, by, kind, b.get("body") or "", b.get("to") or "")


def api_task_update(ctx, q, b, tid):
    with db() as conn:
        return 200, {"task": _task_write(conn, _actor(conn, ctx), tid, b)}


# ---------------------------------------------------------------- skills

def api_skills(ctx, q, b):
    return 200, marketplace.list_skills()


def api_skills_export(ctx, q, b):
    return 200, {"skills": marketplace.export_skills()}


def api_skill_publish(ctx, q, b):
    with db() as conn:
        by = _actor(conn, ctx)
    out = marketplace.publish_skill(b.get("name", ""), b.get("files") or {}, by, b.get("plugin") or "team-skills")
    bus.bump()
    return 200, out


def api_skill_delete(ctx, q, b, name):
    with db() as conn:
        by = _actor(conn, ctx)
    out = marketplace.remove_skill(name, by)
    bus.bump()
    return 200, out


# ---------------------------------------------------------------- dashboard

def api_overview(ctx, q, b):
    with db() as conn:
        return 200, core.overview(conn)


def api_health(ctx, q, b):
    return 200, {"ok": True, "version": __import__("agenthub").__version__}


def api_config(ctx, q, b):
    """What the dashboard needs before it has a token."""
    return 200, {"version": __import__("agenthub").__version__, "identity": identity.mode(),
                 "authenticated": bool(ctx), "admin": bool(ctx) and ctx.get("role") == "admin"}


# ---------------------------------------------------------------- admin (admin token only; see server.py)

def api_admin_project(ctx, q, b, name):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO projects (name, created_at) VALUES (?, datetime('now'))", (name,))
        for col in ("label", "description", "liaison"):
            if col in b:
                if col == "liaison" and b[col] and not core.agent_row(conn, b[col]):
                    raise HubError(404, "no agent %r" % b[col])
                conn.execute("UPDATE projects SET %s=? WHERE name=?" % col, (b[col] or "", name))
        if "archived" in b:
            conn.execute("UPDATE projects SET archived=? WHERE name=?", (1 if b["archived"] else 0, name))
        r = conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
    bus.bump()
    return 200, dict(r)


def api_admin_agent(ctx, q, b, name):
    with db() as conn:
        if not core.agent_row(conn, name):
            raise HubError(404, "no agent %r" % name)
        if "kind" in b:
            if b["kind"] not in core.KINDS:
                raise HubError(400, "kind must be one of %s" % ", ".join(core.KINDS))
            conn.execute("UPDATE agents SET kind=? WHERE name=?", (b["kind"], name))
        for col in ("description", "workspace"):
            if col in b:
                conn.execute("UPDATE agents SET %s=? WHERE name=?" % col, (b[col] or "", name))
        if "archived" in b or "hidden" in b:
            flag = b["archived"] if "archived" in b else b["hidden"]
            conn.execute("UPDATE agents SET hidden=? WHERE name=?", (1 if flag else 0, name))
        for sc in b.get("add_scopes") or []:
            p, _, r = sc.partition("/")
            core.add_scope(conn, name, p, r)
        for sc in b.get("remove_scopes") or []:
            p, _, r = sc.partition("/")
            conn.execute("DELETE FROM scopes WHERE agent=? AND project=? AND role=?", (name, p, r))
        card = core.agent_card(conn, core.agent_row(conn, name))
    bus.bump()
    return 200, card


def api_admin_archive(ctx, q, b):
    """Archive many at once: {"names": [...]} or {"inactive_days": N} (no session within N days)."""
    from .store import ago_ts, LIVE_AFTER
    with db() as conn:
        if b.get("names"):
            names = [n for n in b["names"] if core.agent_row(conn, n)]
        elif b.get("inactive_days") is not None:
            cut = ago_ts(float(b["inactive_days"]) * 86400)
            names = [r["name"] for r in conn.execute(
                "SELECT name FROM agents WHERE hidden=0 AND (last_seen IS NULL OR last_seen < ?) AND name NOT IN "
                "(SELECT agent FROM sessions WHERE ended_at IS NULL AND last_seen >= ?)", (cut, ago_ts(LIVE_AFTER)))]
        else:
            raise HubError(400, "names or inactive_days")
        restore = bool(b.get("restore"))
        for n in names:
            conn.execute("UPDATE agents SET hidden=? WHERE name=?", (0 if restore else 1, n))
    bus.bump()
    return 200, {"restored" if restore else "archived": sorted(names)}


def api_admin_agent_delete(ctx, q, b, name):
    with db() as conn:
        conn.execute("UPDATE sessions SET ended_at=datetime('now') WHERE agent=? AND ended_at IS NULL", (name,))
        conn.execute("DELETE FROM scopes WHERE agent=?", (name,))
        conn.execute("UPDATE projects SET liaison='' WHERE liaison=?", (name,))
        conn.execute("DELETE FROM inbox WHERE agent=? AND delivered_at IS NULL", (name,))
        conn.execute("DELETE FROM agents WHERE name=?", (name,))
    bus.bump()
    return 200, {"deleted": name}


def api_admin_task(ctx, q, b, tid):
    with db() as conn:
        return 200, {"task": _task_write(conn, HUMAN, tid, b)}


def api_admin_nodes(ctx, q, b):
    """GET: the node tokens issued. POST {name}: issue (or re-issue) a token for a node."""
    if b.get("name"):
        name = b["name"].strip().lower()
        if not re.match(r"^[a-z0-9][a-z0-9_.\-]{0,62}$", name):
            raise HubError(400, "node name: lowercase letters, digits, - _ .")
        return 200, {"name": name, "token": identity.create_node_token(name)}
    return 200, {"nodes": identity.node_tokens()}


def api_admin_node_delete(ctx, q, b, name):
    if not identity.revoke_node_token(name):
        raise HubError(404, "no token for node %r" % name)
    return 200, {"revoked": name}


ROUTES = [(m, re.compile(rx), fn) for m, rx, fn in [
    ("GET", r"^/healthz$", api_health),
    ("GET", r"^/v1/config$", api_config),
    ("GET", r"^/v1/whoami$", api_whoami),
    ("POST", r"^/v1/sessions$", api_register),
    ("POST", r"^/v1/sessions/([^/]+)/end$", api_session_end),
    ("POST", r"^/v1/sessions/([^/]+)/beat$", api_session_beat),
    ("POST", r"^/v1/sessions/([^/]+)/state$", api_session_state),
    ("POST", r"^/v1/sessions/([^/]+)/meta$", api_session_meta),
    ("POST", r"^/v1/sessions/([^/]+)/delivered$", api_session_delivered),
    ("GET", r"^/v1/sessions/([^/]+)/pull$", api_session_pull),
    ("GET", r"^/v1/inbox$", api_inbox),
    ("GET", r"^/v1/agents$", api_agents),
    ("GET", r"^/v1/agents/([^/]+)$", api_agent),
    ("POST", r"^/v1/posts$", api_post),
    ("GET", r"^/v1/threads/([^/]+)$", api_thread),
    ("POST", r"^/v1/tasks$", api_delegate),
    ("GET", r"^/v1/tasks$", api_tasks),
    ("GET", r"^/v1/tasks/(\d+)$", api_task),
    ("POST", r"^/v1/tasks/(\d+)$", api_task_update),
    ("GET", r"^/v1/skills$", api_skills),
    ("GET", r"^/v1/skills/export$", api_skills_export),
    ("POST", r"^/v1/skills$", api_skill_publish),
    ("DELETE", r"^/v1/skills/([^/]+)$", api_skill_delete),
    ("GET", r"^/v1/overview$", api_overview),
    ("POST", r"^/v1/admin/projects/([^/]+)$", api_admin_project),
    ("POST", r"^/v1/admin/archive$", api_admin_archive),
    ("POST", r"^/v1/admin/agents/([^/]+)$", api_admin_agent),
    ("DELETE", r"^/v1/admin/agents/([^/]+)$", api_admin_agent_delete),
    ("POST", r"^/v1/admin/tasks/(\d+)$", api_admin_task),
    ("GET", r"^/v1/admin/nodes$", api_admin_nodes),
    ("POST", r"^/v1/admin/nodes$", api_admin_nodes),
    ("DELETE", r"^/v1/admin/nodes/([^/]+)$", api_admin_node_delete),
] + shared.ROUTES]
