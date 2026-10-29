"""The /v1 handlers and their route table.

Each handler is `fn(ctx, query, body, *path_groups) -> (status, dict)`.
`ctx["node"]` is who identity.context() says is calling (a node token, or the
tailnet), never what the client claims.
"""
import json
import os
import re

from . import bus, core, i18n, identity, marketplace, shared
from .core import HubError
from .store import (EVENT_KINDS, HUMAN, OPEN_STATES, SECRETISH, ago, db, jloads, live_session,
                    now, presence, resolve, scopes_of, setting)


def _q(q, key, default=""):
    return (q.get(key) or [default])[0]


def _by(ctx, b, q=None):
    """The acting agent. A request without one is the human (dashboard, bot)."""
    name = (b or {}).get("agent") or (q and _q(q, "agent")) or ""
    return name or HUMAN


# ---------------------------------------------------------------- sessions

def _git(cwd_info):
    return cwd_info.get("repo", ""), cwd_info.get("branch", "")


def api_session_open(ctx, q, b):
    """A channel server announcing its session. Binds an identity if it can.

    Sent again whenever the session switches conversation (`/resume` inside a running
    claude): the identity follows the conversation, not the session id."""
    sid = b.get("session") or ""
    if not sid:
        raise HubError(400, "session required")
    node, cwd = ctx["node"], b.get("cwd", "")
    conv = b.get("conversation") or ""
    with db() as conn:
        old = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        # /resume picks another conversation (maybe another agent's); /clear starts a new one for the same agent
        switched = bool(conv and old and old["conversation"] and old["conversation"] != conv
                        and b.get("source") == "resume")
        agent = resolve(conn, b.get("agent") or _conversation_agent(conn, conv)
                        or (old["agent"] if old and not switched else "") or "")
        ts = now()
        conn.execute(
            "INSERT INTO sessions (id,agent,node,cwd,channel,pid,started_at,last_seen,conversation,title) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET node=excluded.node, cwd=excluded.cwd, channel=excluded.channel, "
            "pid=excluded.pid, last_seen=excluded.last_seen, ended_at=NULL, "
            "conversation=CASE WHEN excluded.conversation='' THEN conversation ELSE excluded.conversation END, "
            "title=CASE WHEN excluded.conversation='' THEN title ELSE excluded.title END",
            (sid, "", node, cwd, 1 if b.get("channel") else 0, b.get("pid"), ts, ts, conv, b.get("title") or ""))
        if switched and old["agent"] and old["agent"] != agent:
            # resumed someone else's conversation: the name it showed before is free again
            conn.execute("UPDATE sessions SET agent='' WHERE id=?", (sid,))
        _touch_node(conn, ctx)
        out = {"session": sid, "node": node, "cwd": cwd, "agent": None, "suggest": []}
        if agent:
            try:
                out["agent"] = _bind(conn, agent, sid, node, cwd, b)
            except HubError as e:
                out["error"] = str(e)
        out["shared"] = shared.manifest(conn, (out["agent"] or {}).get("name"))
        if not out["agent"]:
            out["suggest"] = [r["name"] for r in conn.execute(
                "SELECT name FROM agents WHERE node=? AND workspace=? ORDER BY last_seen DESC", (node, cwd))]
    bus.bump()
    return 200, out


def _conversation_agent(conn, conv):
    """The agent whose conversation this is (sessions started before conversations were
    reported have their own id as the conversation)."""
    if not conv:
        return ""
    r = conn.execute("SELECT name FROM agents WHERE conversation=? ORDER BY last_seen DESC LIMIT 1", (conv,)).fetchone() \
        or conn.execute("SELECT agent AS name FROM sessions WHERE (conversation=? OR id=?) AND agent!='' "
                        "ORDER BY last_seen DESC LIMIT 1", (conv, conv)).fetchone()
    return r["name"] if r else ""


def api_session_meta(ctx, q, b, sid):
    """The channel reporting what its conversation is called (`/rename`, or Claude's own title)."""
    with db() as conn:
        s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HubError(404, "unknown session; open it first")
        title = (b.get("title") or "")[:200]
        conn.execute("UPDATE sessions SET title=? WHERE id=?", (title, sid))
        if s["agent"]:
            conn.execute("UPDATE agents SET title=? WHERE name=? AND conversation=?", (title, s["agent"], s["conversation"]))
    bus.bump()
    return 200, {"ok": True}


def _bind(conn, name, sid, node, cwd, b):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,47}$", name) or name == HUMAN:
        raise HubError(400, "name: letters, digits, - _ . (max 48), and not %r" % HUMAN)
    name = resolve(conn, name)
    holder = live_session(conn, name)
    conv = b.get("conversation") or ""
    if sid and holder and holder["id"] != sid:
        if not conv or conv != (holder["conversation"] or holder["id"]):
            raise HubError(409, "%s is in use by a live session on %s (%s). Pick another name."
                           % (name, holder["node"], holder["cwd"]))
        # The same conversation resumed in another terminal: this copy takes the name, and the
        # one left behind is closed by its node's hubd, before two copies write one transcript.
        conn.execute("UPDATE sessions SET agent='' WHERE id=?", (holder["id"],))
        if holder["pid"]:
            core.order(conn, holder["node"], "stop_session", name, {"session": holder["id"], "pid": holder["pid"]},
                       "taken over by %s:%s" % (node, cwd))
    ts = now()
    existing = conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()
    repo, branch = _git(b)
    if existing:
        conn.execute(
            "UPDATE agents SET node=?, workspace=?, repo=CASE WHEN ?='' THEN repo ELSE ? END, "
            "branch=CASE WHEN ?='' THEN branch ELSE ? END, last_session=?, last_seen=? WHERE name=?",
            (node, cwd, repo, repo, branch, branch, sid or existing["last_session"], ts, name))
    else:
        conn.execute(
            "INSERT INTO agents (name,kind,node,workspace,repo,branch,last_session,registered_at,last_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (name, b.get("kind") or "claude", node, cwd, repo, branch, sid or "", ts, ts))
    if existing and b.get("kind") and b["kind"] != existing["kind"]:
        conn.execute("UPDATE agents SET kind=? WHERE name=?", (b["kind"], name))
    if conv:
        conn.execute("UPDATE agents SET conversation=?, title=? WHERE name=?", (conv, b.get("title") or "", name))
    if b.get("description"):
        conn.execute("UPDATE agents SET description=? WHERE name=?", (b["description"], name))
    if b.get("project"):
        conn.execute("INSERT OR IGNORE INTO agent_scopes (agent,project,role) VALUES (?,?,?)",
                     (name, b["project"], b.get("role") or ""))
        conn.execute("INSERT OR IGNORE INTO projects (name,created_at) VALUES (?,?)", (b["project"], ts))
    if sid:
        # One session per name: whatever the name was bound to before is released.
        conn.execute("UPDATE sessions SET agent='' WHERE agent=? AND id!=?", (name, sid))
        conn.execute("UPDATE sessions SET agent=? WHERE id=?", (name, sid))
    # Undelivered work stays queued for the new session; drop wake orders it made moot.
        conn.execute("UPDATE orders SET state='done', result='session appeared', done_at=? "
                     "WHERE agent=? AND kind='wake' AND state='pending'", (ts, name))
    return core.agent_card(conn, conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone())


def api_register(ctx, q, b):
    """Claim a name. With a session id the session is bound to it; without one
    (a plain shell) only the agent's metadata is created or updated."""
    sid = b.get("session") or ""
    name = (b.get("name") or "").strip()
    if not name:
        raise HubError(400, "name required")
    with db() as conn:
        cwd = b.get("cwd", "")
        if sid:
            s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            cwd = cwd or (s["cwd"] if s else "")
            if not s:
                conn.execute("INSERT INTO sessions (id,node,cwd,started_at,last_seen) VALUES (?,?,?,?,?)",
                             (sid, ctx["node"], cwd, now(), now()))
        card = _bind(conn, name, sid or None, ctx["node"], cwd, b)
        manifest = shared.manifest(conn, card["name"])
    bus.bump()
    return 200, {"agent": card, "shared": manifest}


def api_session_pull(ctx, q, b, sid):
    """Long-poll: hand the session's agent everything queued for it.

    Delivery is acknowledged, not assumed: rows go out with `sent_at`, and only
    become `delivered_at` when the same session's next pull carries `ack=<ids>`.
    A session that dies holding an open poll never acks, so the maintenance
    loop puts its rows back in the queue (and wakes the agent again).
    `autoack=1` is for callers that consume immediately (the prompt hook).
    """
    import time
    wait = min(float(_q(q, "wait", "0") or 0), 55)
    acks = [int(x) for x in _q(q, "ack").split(",") if x.strip().isdigit()]
    autoack = bool(_q(q, "autoack"))
    v = bus.version()
    end = time.time() + wait
    first = True
    while True:
        with db() as conn:
            s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone() or conn.execute(
                # the prompt hook knows the conversation, which `/resume` moves away from the session id
                "SELECT * FROM sessions WHERE conversation=? AND ended_at IS NULL ORDER BY last_seen DESC LIMIT 1",
                (sid,)).fetchone()
            if not s:
                raise HubError(404, "unknown session; open it first")
            sid = s["id"]
            ts = now()
            if not first and s["ended_at"]:
                return 200, {"agent": s["agent"], "items": [], "ended": True}  # ended while we waited
            conn.execute("UPDATE sessions SET last_seen=?, ended_at=NULL WHERE id=?", (ts, sid))
            if first and acks:
                conn.execute("UPDATE deliveries SET delivered_at=? WHERE session=? AND delivered_at IS NULL AND id IN (%s)"
                             % ",".join("?" * len(acks)), [ts, sid] + acks)
            first = False
            items = []
            if s["agent"]:
                conn.execute("UPDATE agents SET last_seen=? WHERE name=?", (ts, s["agent"]))
                rows = conn.execute("SELECT * FROM deliveries WHERE agent=? AND delivered_at IS NULL AND sent_at IS NULL "
                                    "ORDER BY id LIMIT 20", (s["agent"],)).fetchall()
                for r in rows:
                    conn.execute("UPDATE deliveries SET sent_at=?, session=?, delivered_at=? WHERE id=?",
                                 (ts, sid, ts if autoack else None, r["id"]))
                    d = dict(r)
                    d["meta"] = jloads(d["meta"], {})
                    items.append(d)
        if items or time.time() >= end:
            if items:
                bus.bump()
            return 200, {"agent": s["agent"], "items": items}
        v = bus.wait(v, max(0.1, end - time.time()))


def _requeue(conn, where, args):
    """Put sent-but-unacknowledged deliveries back in the queue."""
    conn.execute("UPDATE deliveries SET sent_at=NULL, session='' WHERE delivered_at IS NULL AND sent_at IS NOT NULL "
                 "AND " + where, args)


def api_session_end(ctx, q, b, sid):
    with db() as conn:
        conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (now(), sid))
        _requeue(conn, "session=?", (sid,))
    bus.bump()
    return 200, {"ok": True}


def api_sessions(ctx, q, b):
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM sessions WHERE ended_at IS NULL AND last_seen > "
            "strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-1 day') ORDER BY last_seen DESC")]
    for r in rows:
        r["presence"] = presence(r["last_seen"])
    return 200, {"sessions": rows}


# ---------------------------------------------------------------- agents

def api_agents(ctx, q, b):
    with db() as conn:
        args, where = [], []
        if _q(q, "project"):
            where.append("name IN (SELECT agent FROM agent_scopes WHERE project=?)")
            args.append(_q(q, "project"))
        if _q(q, "node"):
            where.append("node=?")
            args.append(_q(q, "node"))
        rows = conn.execute("SELECT * FROM agents %s ORDER BY last_seen DESC" %
                            ("WHERE " + " AND ".join(where) if where else ""), args).fetchall()
        cards = [core.agent_card(conn, r) for r in rows]
    if _q(q, "live"):
        cards = [c for c in cards if c["presence"] == "live"]
    return 200, {"agents": cards}


def api_agent(ctx, q, b, name):
    with db() as conn:
        r = core.agent_row(conn, name)
        if not r:
            raise HubError(404, "no agent %r" % name)
        card = core.agent_card(conn, r)
        n = card["name"]
        card["tasks"] = [dict(x) for x in conn.execute(
            "SELECT id,title,state,project,created_by,owner,updated_at FROM tasks WHERE owner=? OR created_by=? "
            "ORDER BY updated_at DESC LIMIT 50", (n, n))]
        card["events"] = [dict(x) for x in conn.execute(
            "SELECT id,kind,title,project,task_id,ts FROM events WHERE agent=? ORDER BY id DESC LIMIT 30", (n,))]
        card["sessions"] = [dict(x) for x in conn.execute(
            "SELECT * FROM sessions WHERE agent=? OR id=? ORDER BY last_seen DESC LIMIT 10", (n, card["last_session"]))]
        card["aliases"] = [x["alias"] for x in conn.execute("SELECT alias FROM agent_aliases WHERE agent=?", (n,))]
    return 200, card


def api_agent_edit(ctx, q, b, name):
    """Dashboard/bot edits: description, wake policy, home, scopes, rename."""
    with db() as conn:
        r = core.agent_row(conn, name)
        if not r:
            raise HubError(404, "no agent %r" % name)
        name = r["name"]
        if "hidden" in b:
            conn.execute("UPDATE agents SET hidden=? WHERE name=?", (1 if b["hidden"] else 0, name))
        for k in ("description", "wake", "workspace", "node", "status"):
            if k in b:
                if k == "wake" and b[k] not in ("auto", "manual"):
                    raise HubError(400, "wake: auto | manual")
                conn.execute("UPDATE agents SET %s=? WHERE name=?" % k, (b[k], name))
        if "scopes" in b:
            conn.execute("DELETE FROM agent_scopes WHERE agent=?", (name,))
            for s in b["scopes"]:
                p, _, role = s.partition("/")
                if p:
                    conn.execute("INSERT OR IGNORE INTO agent_scopes VALUES (?,?,?)", (name, p, role))
                    conn.execute("INSERT OR IGNORE INTO projects (name,created_at) VALUES (?,?)", (p, now()))
        if b.get("rename") and b["rename"] != name:
            _rename(conn, name, b["rename"])
            name = b["rename"]
        card = core.agent_card(conn, conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone())
    bus.bump()
    return 200, card


RENAME_COLUMNS = (("events", "agent"), ("messages", "from_agent"), ("messages", "to_agent"),
                  ("tasks", "owner"), ("tasks", "created_by"), ("task_log", "agent"),
                  ("agent_scopes", "agent"), ("sessions", "agent"), ("deliveries", "agent"),
                  ("vault", "owner"), ("orders", "agent"), ("permissions", "agent"),
                  ("notes", "owner"), ("note_log", "agent"), ("vault_log", "agent"))


def _rename(conn, old, new):
    if conn.execute("SELECT 1 FROM agents WHERE name=?", (new,)).fetchone():
        raise HubError(409, "%s already exists" % new)
    conn.execute("UPDATE agents SET name=? WHERE name=?", (new, old))
    for t, c in RENAME_COLUMNS:
        conn.execute("UPDATE %s SET %s=? WHERE %s=?" % (t, c, c), (new, old))
    conn.execute("UPDATE agent_aliases SET agent=? WHERE agent=?", (new, old))
    conn.execute("INSERT OR REPLACE INTO agent_aliases VALUES (?,?,?)", (old, new, now()))


def api_agent_delete(ctx, q, b, name):
    with db() as conn:
        conn.execute("DELETE FROM agents WHERE name=?", (name,))
        conn.execute("DELETE FROM agent_scopes WHERE agent=?", (name,))
        conn.execute("DELETE FROM deliveries WHERE agent=? AND delivered_at IS NULL", (name,))
    bus.bump()
    return 200, {"ok": True}


def api_wake(ctx, q, b, name):
    with db() as conn:
        a = core.agent_row(conn, name)
        if not a:
            raise HubError(404, "no agent %r" % name)
        if not a["node"] or not a["workspace"]:
            raise HubError(400, "%s has no home (workspace@node) to wake in" % a["name"])
        oid = core.order(conn, a["node"], "wake", a["name"],
                         {"workspace": a["workspace"], "kind": a["kind"], "resume": a["conversation"] or a["last_session"],
                          "prompt": b.get("prompt", "")}, b.get("reason") or "manual wake")
    return 200, {"order": oid}


def api_stop(ctx, q, b, name):
    with db() as conn:
        a = core.agent_row(conn, name)
        if not a:
            raise HubError(404, "no agent %r" % name)
        oid = core.order(conn, a["node"], "stop", a["name"], {}, b.get("reason") or "manual stop")
    return 200, {"order": oid}


def _order_and_wait(node, kind, agent, args, reason, timeout=25):
    """Queue an order for a node's hubd and wait for its result (peek/keys are interactive)."""
    import time
    with db() as conn:
        n = conn.execute("SELECT hubd_seen, info FROM nodes WHERE name=?", (node,)).fetchone()
        if not n or presence(n["hubd_seen"]) != "live":
            raise HubError(503, "hubd on %s is not running" % node)
        have = jloads(n["info"], {}).get("hubd", "0")
        if kind in ("peek", "keys") and tuple(int(x) for x in re.findall(r"\d+", have)[:3]) < (1, 1, 0):
            raise HubError(409, i18n.t("err.hubd_old", node=node, have=have))
        oid = core.order(conn, node, kind, agent, args, reason)
    v = bus.version()
    end = time.time() + timeout
    while time.time() < end:
        with db() as conn:
            o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if o["state"] in ("done", "failed"):
            if o["state"] == "failed" and "not allowed" in o["result"]:
                raise HubError(400, o["result"])
            return {"state": o["state"], "screen": o["result"]}
        v = bus.wait(v, max(0.1, end - time.time()))
    raise HubError(504, "hubd on %s did not answer in %ds (older hubd? re-run the installer there)" % (node, timeout))


def api_peek(ctx, q, b, name):
    with db() as conn:
        a = core.agent_row(conn, name)
        if not a:
            raise HubError(404, "no agent %r" % name)
    return 200, _order_and_wait(a["node"], "peek", a["name"], {}, "peek")


def api_keys(ctx, q, b, name):
    with db() as conn:
        a = core.agent_row(conn, name)
        if not a:
            raise HubError(404, "no agent %r" % name)
    return 200, _order_and_wait(a["node"], "keys", a["name"],
                                {"text": b.get("text", ""), "keys": b.get("keys") or []}, "keys")


def api_scope(ctx, q, b, name):
    with db() as conn:
        n = resolve(conn, name)
        if b.get("remove"):
            p, _, role = b["remove"].partition("/")
            conn.execute("DELETE FROM agent_scopes WHERE agent=? AND project=? AND role=?", (n, p, role))
        for s in b.get("add", []):
            p, _, role = s.partition("/")
            conn.execute("INSERT OR IGNORE INTO agent_scopes VALUES (?,?,?)", (n, p, role))
            conn.execute("INSERT OR IGNORE INTO projects (name,created_at) VALUES (?,?)", (p, now()))
        out = scopes_of(conn, n)
    bus.bump()
    return 200, {"scopes": out}


# ---------------------------------------------------------------- nodes / orders

def _touch_node(conn, ctx, hubd=False, info=None):
    if not ctx.get("node"):
        return
    ts = now()
    conn.execute("INSERT INTO nodes (name,ip,os,owner,last_seen) VALUES (?,?,?,?,?) "
                 "ON CONFLICT(name) DO UPDATE SET ip=excluded.ip, os=excluded.os, owner=excluded.owner, "
                 "last_seen=excluded.last_seen",
                 (ctx["node"], ctx.get("ip", ""), ctx.get("os", ""), ctx.get("owner", ""), ts))
    if hubd:
        conn.execute("UPDATE nodes SET hubd_seen=?, info=? WHERE name=?",
                     (ts, json.dumps(info or {}), ctx["node"]))


def api_nodes(ctx, q, b):
    with db() as conn:
        out = []
        for r in conn.execute("SELECT * FROM nodes ORDER BY name"):
            n = dict(r)
            n["info"] = jloads(n["info"], {})
            n["hubd"] = presence(n["hubd_seen"])
            n["agents"] = [core.agent_card(conn, a) for a in conn.execute(
                "SELECT * FROM agents WHERE node=? ORDER BY last_seen DESC", (n["name"],))]
            out.append(n)
    return 200, {"nodes": out}


def api_node_orders(ctx, q, b):
    """hubd long-poll: heartbeat (with node info) + pick up orders for this node."""
    import time
    node = ctx["node"]
    wait = min(float(_q(q, "wait", "0") or 0), 55)
    acks = [int(x) for x in _q(q, "ack").split(",") if x.strip().isdigit()]
    if acks:
        with db() as conn:
            conn.execute("UPDATE orders SET acked_at=? WHERE node=? AND acked_at IS NULL AND id IN (%s)"
                         % ",".join("?" * len(acks)), [now(), node] + acks)
    v = bus.version()
    end = time.time() + wait
    while True:
        with db() as conn:
            _touch_node(conn, ctx, hubd=True, info=b)
            # sessions that hubd reports as running keep their agents reachable even mid-boot
            rows = conn.execute("SELECT * FROM orders WHERE node=? AND state='pending' ORDER BY id",
                                (node,)).fetchall()
            for r in rows:
                conn.execute("UPDATE orders SET state='taken', taken_at=? WHERE id=?", (now(), r["id"]))
            items = []
            for r in rows:
                d = dict(r)
                d["args"] = jloads(d["args"], {})
                items.append(d)
        if items or time.time() >= end:
            if items:
                bus.bump()
            return 200, {"node": node, "orders": items}
        v = bus.wait(v, max(0.1, end - time.time()))


def api_order_done(ctx, q, b, oid):
    with db() as conn:
        conn.execute("UPDATE orders SET state=?, result=?, done_at=?, acked_at=COALESCE(acked_at, ?) WHERE id=?",
                     (b.get("state", "done"), b.get("result", "")[:2000], now(), now(), oid))
        o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if o and o["kind"] == "stop" and b.get("state", "done") == "done":
            conn.execute("UPDATE sessions SET ended_at=? WHERE agent=? AND ended_at IS NULL", (now(), o["agent"]))
            _requeue(conn, "agent=?", (o["agent"],))
        if o and b.get("state") == "failed" and o["kind"] not in ("peek", "keys"):
            core.deliver(conn, HUMAN, "wake_failed", "Could not %s %s on %s: %s"
                         % (o["kind"], o["agent"], o["node"], b.get("result", "")[:500]))
    bus.bump()
    return 200, {"ok": True}


def api_orders(ctx, q, b):
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 50")]
    return 200, {"orders": rows}


# ---------------------------------------------------------------- tasks

def api_delegate(ctx, q, b):
    with db() as conn:
        t = core.delegate(conn, _by(ctx, b), b.get("to", ""), b.get("title", ""), b.get("goal", ""),
                          b.get("context", ""), b.get("acceptance", ""), b.get("project", ""),
                          b.get("parent_id") or None, int(b.get("priority") or 0), b.get("from_event"))
    return 200, t


def api_tasks(ctx, q, b):
    where, args = [], []
    mine = _q(q, "mine")
    if mine:
        with db() as conn:
            sc = scopes_of(conn, resolve(conn, mine))
        cond = ["owner=?", "created_by=?"]
        args += [mine, mine]
        for s in sc:
            cond.append("(owner='' AND role=? AND project=?)")
            args += [s["role"], s["project"]]
        where.append("(" + " OR ".join(cond) + ")")
    for key in ("project", "owner", "created_by", "state"):
        if _q(q, key):
            where.append("%s=?" % key)
            args.append(_q(q, key))
    if _q(q, "parent"):
        where.append("parent_id=?")
        args.append(int(_q(q, "parent")))
    if _q(q, "open"):
        where.append("state IN (%s)" % ",".join("?" * len(OPEN_STATES)))
        args += list(OPEN_STATES)
    if _q(q, "roots"):
        where.append("parent_id IS NULL")
    limit = int(_q(q, "limit", "200"))
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks %s ORDER BY updated_at DESC LIMIT ?" %
                            ("WHERE " + " AND ".join(where) if where else ""), args + [limit]).fetchall()
        out = []
        for r in rows:
            t = dict(r)
            t["children"] = conn.execute("SELECT COUNT(*) FROM tasks WHERE parent_id=?", (t["id"],)).fetchone()[0]
            t["log_count"] = conn.execute("SELECT COUNT(*) FROM task_log WHERE task_id=?", (t["id"],)).fetchone()[0]
            out.append(t)
    return 200, {"tasks": out}


def api_task(ctx, q, b, tid):
    with db() as conn:
        t = core.task_dict(conn, core.get_task(conn, int(tid)), full=True)
        t["events"] = [dict(r) for r in conn.execute(
            "SELECT id,agent,kind,title,body,ts FROM events WHERE task_id=? ORDER BY id", (int(tid),))]
    return 200, t


def api_task_update(ctx, q, b, tid):
    with db() as conn:
        tid = int(tid)
        by = _by(ctx, b)
        if b.get("assign"):
            return 200, core.assign(conn, tid, by, b["assign"])
        if b.get("state") in ("done", "dropped", "open"):
            return 200, core.close(conn, tid, by, b["state"], b.get("body", ""))
        if b.get("claim"):
            return 200, core.claim(conn, tid, by)
        if b.get("renotify"):
            return 200, core.renotify(conn, tid, by)
        if b.get("edit"):
            for k in ("title", "goal", "context", "acceptance", "project", "priority"):
                if k in b["edit"]:
                    conn.execute("UPDATE tasks SET %s=?, updated_at=? WHERE id=?" % k, (b["edit"][k], now(), tid))
            bus.bump()
            return 200, core.task_dict(conn, core.get_task(conn, tid), full=True)
        return 200, core.update(conn, tid, by, b.get("kind", "comment"), b.get("body", ""))


# ---------------------------------------------------------------- projects

def api_projects(ctx, q, b):
    with db() as conn:
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM projects UNION SELECT DISTINCT project FROM tasks WHERE project!='' "
            "UNION SELECT DISTINCT project FROM agent_scopes")]
        out = []
        for n in sorted(set(names)):
            p = conn.execute("SELECT * FROM projects WHERE name=?", (n,)).fetchone()
            d = dict(p) if p else {"name": n, "label": "", "description": "", "archived": 0}
            counts = {r["state"]: r["c"] for r in conn.execute(
                "SELECT state, COUNT(*) c FROM tasks WHERE project=? GROUP BY state", (n,))}
            d["tasks"] = counts
            d["open"] = sum(v for k, v in counts.items() if k in OPEN_STATES)
            d["agents"] = [dict(a) for a in conn.execute(
                "SELECT s.agent, s.role, a.last_seen, COALESCE(a.hidden, 0) AS hidden FROM agent_scopes s "
                "LEFT JOIN agents a ON a.name=s.agent "
                "WHERE s.project=? ORDER BY s.role, s.agent", (n,))]
            for a in d["agents"]:
                a["presence"] = "live" if live_session(conn, a["agent"]) else presence(a["last_seen"])
            last = conn.execute("SELECT MAX(updated_at) FROM tasks WHERE project=?", (n,)).fetchone()[0]
            last_e = conn.execute("SELECT MAX(ts) FROM events WHERE project=?", (n,)).fetchone()[0]
            d["last_activity"] = max(filter(None, [last, last_e, d.get("created_at")]), default=None)
            out.append(d)
    out.sort(key=lambda d: (d.get("archived", 0), -(d["open"]), -(len(d["agents"]))))
    return 200, {"projects": out}


def api_project_edit(ctx, q, b, name):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO projects (name,created_at) VALUES (?,?)", (name, now()))
        for k in ("label", "description", "archived"):
            if k in b:
                conn.execute("UPDATE projects SET %s=? WHERE name=?" % k, (b[k], name))
    bus.bump()
    return 200, {"ok": True}


def api_card_order(ctx, q, b):
    """Dashboard drag and drop: {"kind": "projects"|"agents", "names": [...]} in the new order."""
    kind, names = b.get("kind"), b.get("names") or []
    if kind not in ("projects", "agents") or not isinstance(names, list):
        raise HubError(400, "kind: projects | agents, names: [...]")
    with db() as conn:
        for i, n in enumerate(names):
            if kind == "projects":
                conn.execute("INSERT OR IGNORE INTO projects (name,created_at) VALUES (?,?)", (n, now()))
            conn.execute("UPDATE %s SET position=? WHERE name=?" % kind, (i, n))
    bus.bump()
    return 200, {"ok": True}


# ---------------------------------------------------------------- events / messages

def api_post_event(ctx, q, b):
    by = _by(ctx, b)
    kind = b.get("kind") or "note"
    if kind not in EVENT_KINDS:
        raise HubError(400, "kind must be one of %s" % ", ".join(EVENT_KINDS))
    title = (b.get("title") or "").strip()
    if not title:
        raise HubError(400, "title required")
    with db() as conn:
        project = b.get("project") or ""
        if not project and b.get("task_id"):
            r = conn.execute("SELECT project FROM tasks WHERE id=?", (b["task_id"],)).fetchone()
            project = r["project"] if r else ""
        if not project:
            sc = scopes_of(conn, by)
            project = sc[0]["project"] if sc else ""
        try:
            cur = conn.execute(
                "INSERT INTO events (agent,kind,title,body,tags,refs,project,task_id,idem,ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (by, kind, title, b.get("body", ""), json.dumps(b.get("tags") or []),
                 json.dumps(b.get("refs") or {}), project, b.get("task_id"), b.get("idem"), now()))
            eid = cur.lastrowid
        except Exception:
            r = conn.execute("SELECT id FROM events WHERE agent=? AND idem=?", (by, b.get("idem"))).fetchone()
            eid = r["id"] if r else None
        conn.execute("UPDATE agents SET last_seen=? WHERE name=?", (now(), by))
    bus.bump()
    out = {"id": eid}
    if SECRETISH.search(title + "\n" + b.get("body", "")):
        out["warning"] = "this looks like it contains a credential; use the vault instead"
    return 200, out


def api_events(ctx, q, b):
    where, args = [], []
    for key in ("agent", "kind", "project"):
        if _q(q, key):
            where.append("%s=?" % key)
            args.append(_q(q, key))
    if _q(q, "task"):
        where.append("task_id=?")
        args.append(int(_q(q, "task")))
    if _q(q, "before"):
        where.append("id<?")
        args.append(int(_q(q, "before")))
    limit = min(int(_q(q, "limit", "50")), 500)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM events %s ORDER BY id DESC LIMIT ?" % ("WHERE " + " AND ".join(where) if where else ""),
            args + [limit])]
    for r in rows:
        r["tags"] = jloads(r["tags"], [])
        r["refs"] = jloads(r["refs"], {})
    return 200, {"events": rows}


def api_search(ctx, q, b):
    term = _q(q, "q").strip()
    if not term:
        return 200, {"events": [], "tasks": [], "notes": []}
    like = "%" + term + "%"
    with db() as conn:
        try:
            ev = [dict(r) for r in conn.execute(
                "SELECT e.id,e.agent,e.kind,e.title,e.project,e.task_id,e.ts FROM events_fts f JOIN events e "
                "ON e.id=f.rowid WHERE events_fts MATCH ? ORDER BY rank LIMIT 30", (term,))]
        except Exception:
            ev = []
        if not ev:
            ev = [dict(r) for r in conn.execute(
                "SELECT id,agent,kind,title,project,task_id,ts FROM events WHERE title LIKE ? OR body LIKE ? "
                "ORDER BY id DESC LIMIT 30", (like, like))]
        tasks = [dict(r) for r in conn.execute(
            "SELECT id,title,state,project,owner,created_by,updated_at FROM tasks WHERE title LIKE ? OR goal LIKE ? "
            "OR context LIKE ? OR result LIKE ? OR id IN (SELECT task_id FROM task_log WHERE body LIKE ?) "
            "ORDER BY updated_at DESC LIMIT 30", (like, like, like, like, like))]
        notes = [dict(r) for r in conn.execute(
            "SELECT 'note' AS type, name, title, updated_at FROM notes WHERE name LIKE ? OR title LIKE ? OR body LIKE ? "
            "UNION ALL SELECT 'secret', name, note, updated_at FROM vault WHERE name LIKE ? OR note LIKE ? LIMIT 20",
            (like, like, like, like, like))]
    return 200, {"events": ev, "tasks": tasks, "notes": notes}


def api_send(ctx, q, b):
    with db() as conn:
        out = core.send(conn, _by(ctx, b), b.get("to", ""), b.get("body", ""),
                        b.get("reply_to"), b.get("task_id"))
    return 200, out


def api_inbox(ctx, q, b):
    """Undelivered deliveries, for clients without a channel (CLI, hooks)."""
    agent = _q(q, "agent")
    if not agent:
        raise HubError(400, "agent required")
    with db() as conn:
        agent = resolve(conn, agent)
        rows = conn.execute("SELECT * FROM deliveries WHERE agent=? AND delivered_at IS NULL AND sent_at IS NULL "
                            "ORDER BY id LIMIT 30", (agent,)).fetchall()
        if not _q(q, "peek"):
            for r in rows:
                conn.execute("UPDATE deliveries SET sent_at=?, delivered_at=?, session='inbox' WHERE id=?",
                             (now(), now(), r["id"]))
        conn.execute("UPDATE agents SET last_seen=? WHERE name=?", (now(), agent))
    items = []
    for r in rows:
        d = dict(r)
        d["meta"] = jloads(d["meta"], {})
        items.append(d)
    bus.bump()
    return 200, {"items": items}


def api_thread(ctx, q, b, thread):
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY id", (thread,))]
    return 200, {"messages": rows}


def api_messages(ctx, q, b):
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?",
                                              (int(_q(q, "limit", "100")),))]
    return 200, {"messages": rows}


def api_deliveries(ctx, q, b):
    agent = _q(q, "agent", HUMAN)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM deliveries WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, int(_q(q, "limit", "50"))))]
    return 200, {"items": rows}


# ---------------------------------------------------------------- permissions

def api_perm_request(ctx, q, b):
    with db() as conn:
        s = conn.execute("SELECT * FROM sessions WHERE id=?", (b.get("session", ""),)).fetchone()
        agent = (s["agent"] if s else "") or "(unnamed@%s)" % ctx["node"]
        cur = conn.execute(
            "INSERT INTO permissions (agent,session,request_id,tool,description,preview,created_at) VALUES (?,?,?,?,?,?,?)",
            (agent, b.get("session", ""), b.get("request_id", ""), b.get("tool", ""),
             b.get("description", ""), b.get("preview", ""), now()))
        pid = cur.lastrowid
        core.deliver(conn, HUMAN, "permission", "%s wants to use %s: %s" % (agent, b.get("tool"), b.get("description")),
                     meta={"perm_id": pid})
    return 200, {"id": pid}


def api_perm_decide(ctx, q, b, pid):
    behavior = b.get("behavior")
    if behavior not in ("allow", "deny"):
        raise HubError(400, "behavior: allow | deny")
    with db() as conn:
        p = conn.execute("SELECT * FROM permissions WHERE id=?", (int(pid),)).fetchone()
        if not p:
            raise HubError(404, "no permission request %s" % pid)
        if p["state"] != "pending":
            return 200, {"state": p["state"]}
        conn.execute("UPDATE permissions SET state=?, decided_by=?, decided_at=? WHERE id=?",
                     (behavior, _by(ctx, b), now(), p["id"]))
        if p["agent"] and not p["agent"].startswith("("):
            core.deliver(conn, p["agent"], "verdict", "", meta={"request_id": p["request_id"], "behavior": behavior},
                         wake=False)
    return 200, {"state": behavior}


def api_perm_resolved(ctx, q, b):
    """The session answered locally (or the request vanished): stop showing it."""
    with db() as conn:
        conn.execute("UPDATE permissions SET state='expired', decided_at=? WHERE session=? AND state='pending'"
                     " AND (?='' OR request_id=?)", (now(), b.get("session", ""), b.get("request_id", ""),
                                                     b.get("request_id", "")))
    bus.bump()
    return 200, {"ok": True}


# ---------------------------------------------------------------- skills / overview

def api_skills(ctx, q, b):
    return 200, marketplace.list_skills()


def api_skills_export(ctx, q, b):
    return 200, {"skills": marketplace.export_skills()}


def api_skill_publish(ctx, q, b):
    out = marketplace.publish_skill(b.get("name", ""), b.get("files") or {}, _by(ctx, b),
                                    b.get("plugin") or "team-skills")
    with db() as conn:
        conn.execute(
            "INSERT INTO events (agent,kind,title,body,tags,refs,project,ts) VALUES (?,?,?,?,?,?,?,?)",
            (_by(ctx, b), "artifact", "skill published: %s (%s v%s)" % (out["skill"], out["plugin"], out["version"]),
             "Every node picks it up right away (hubd updates plugins and skill dirs; it also checks every 10 min).",
             json.dumps(["skill"]), "{}", "", now()))
        for node in [r["name"] for r in conn.execute("SELECT name FROM nodes WHERE hubd_seen IS NOT NULL")]:
            core.order(conn, node, "update_plugins", "", {}, "skill %s published" % out["skill"])
    bus.bump()
    return 200, out


def api_skill_delete(ctx, q, b, name):
    out = marketplace.remove_skill(name, _by(ctx, b, q))
    with db() as conn:
        for node in [r["name"] for r in conn.execute("SELECT name FROM nodes WHERE hubd_seen IS NOT NULL")]:
            core.order(conn, node, "update_plugins", "", {}, "skill %s removed" % name)
    bus.bump()
    return 200, out


def api_overview(ctx, q, b):
    with db() as conn:
        att = core.attention(conn)
        counts = {r["state"]: r["c"] for r in conn.execute("SELECT state, COUNT(*) c FROM tasks GROUP BY state")}
        agents = [core.agent_card(conn, r) for r in conn.execute("SELECT * FROM agents ORDER BY last_seen DESC")]
        nodes = [dict(r) for r in conn.execute("SELECT name, hubd_seen, last_seen FROM nodes ORDER BY name")]
        for n in nodes:
            n["hubd"] = presence(n["hubd_seen"])
        recent = [dict(r) for r in conn.execute(
            "SELECT l.task_id, l.agent, l.kind, l.body, l.ts, t.title, t.project, t.state FROM task_log l "
            "JOIN tasks t ON t.id=l.task_id ORDER BY l.id DESC LIMIT 25")]
        tg = bool(setting(conn, "tg_chat"))
    from . import notify
    active = [a for a in agents if a["presence"] == "live"]
    return 200, {"attention": att, "task_counts": counts, "live": active,
                 "agent_total": len(agents), "nodes": nodes, "recent": recent, "telegram_paired": tg,
                 "notifiers": [n.name for n in notify.active()],
                 "version": bus.version()}


def api_telegram(ctx, q, b):
    from .notify import telegram
    from .store import set_setting
    with db() as conn:
        if b.get("unpair"):
            set_setting(conn, "tg_user", "")
            set_setting(conn, "tg_chat", "")
        out = {"enabled": bool(telegram.token()), "paired": bool(setting(conn, "tg_user")),
               "code": telegram.pair_code(conn)}
    if out["enabled"] and telegram._bot:
        try:
            out["bot"] = telegram._bot.username()
        except Exception:
            pass
    return 200, out


def api_session(ctx, q, b, sid):
    with db() as conn:
        r = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            raise HubError(404, "unknown session")
    return 200, dict(r)


def api_health(ctx, q, b):
    return 200, {"ok": True, "version": __import__("agenthub").__version__, "you": ctx or None}


def api_config(ctx, q, b):
    """What the dashboard needs before it has a token."""
    from . import notify
    return 200, {"lang": i18n.lang(), "version": __import__("agenthub").__version__, "identity": identity.mode(),
                 "authenticated": bool(ctx), "notifiers": notify.names() if ctx else []}


# ---------------------------------------------------------------- admin (admin token only; see server.py)

def api_admin_nodes(ctx, q, b):
    """GET: the node tokens issued. POST {name}: issue (or re-issue) a token for a node."""
    if b.get("name"):
        name = b["name"].strip().lower()
        if not re.match(r"^[a-z0-9][a-z0-9_.\-]{0,62}$", name):
            raise HubError(400, "node name: lowercase letters, digits, - _ .")
        return 200, {"name": name, "token": identity.create_node_token(name)}
    return 200, {"nodes": identity.node_tokens()}


def api_admin_node_delete(ctx, q, b, name):
    n = identity.revoke_node_token(name)
    if not n:
        raise HubError(404, "no token for node %r" % name)
    return 200, {"revoked": name}


ROUTES = [
    ("GET", r"^/healthz$", api_health),
    ("GET", r"^/v1/config$", api_config),
    ("GET", r"^/v1/admin/nodes$", api_admin_nodes),
    ("POST", r"^/v1/admin/nodes$", api_admin_nodes),
    ("DELETE", r"^/v1/admin/nodes/([^/]+)$", api_admin_node_delete),
    ("GET", r"^/v1/whoami$", api_health),
    ("GET", r"^/v1/overview$", api_overview),
    ("POST", r"^/v1/sessions$", api_session_open),
    ("GET", r"^/v1/sessions$", api_sessions),
    ("GET", r"^/v1/sessions/([^/]+)/pull$", api_session_pull),
    ("GET", r"^/v1/sessions/([^/]+)$", api_session),
    ("GET", r"^/v1/telegram$", api_telegram),
    ("POST", r"^/v1/telegram$", api_telegram),
    ("POST", r"^/v1/sessions/([^/]+)/end$", api_session_end),
    ("POST", r"^/v1/sessions/([^/]+)/meta$", api_session_meta),
    ("POST", r"^/v1/agents/register$", api_register),
    ("GET", r"^/v1/agents$", api_agents),
    ("GET", r"^/v1/agents/([^/]+)$", api_agent),
    ("POST", r"^/v1/agents/([^/]+)$", api_agent_edit),
    ("DELETE", r"^/v1/agents/([^/]+)$", api_agent_delete),
    ("POST", r"^/v1/agents/([^/]+)/wake$", api_wake),
    ("POST", r"^/v1/agents/([^/]+)/stop$", api_stop),
    ("POST", r"^/v1/agents/([^/]+)/scopes$", api_scope),
    ("POST", r"^/v1/agents/([^/]+)/peek$", api_peek),
    ("POST", r"^/v1/agents/([^/]+)/keys$", api_keys),
    ("GET", r"^/v1/nodes$", api_nodes),
    ("POST", r"^/v1/nodes/orders$", api_node_orders),
    ("GET", r"^/v1/orders$", api_orders),
    ("POST", r"^/v1/orders/(\d+)$", api_order_done),
    ("POST", r"^/v1/tasks$", api_delegate),
    ("GET", r"^/v1/tasks$", api_tasks),
    ("GET", r"^/v1/tasks/(\d+)$", api_task),
    ("POST", r"^/v1/tasks/(\d+)$", api_task_update),
    ("GET", r"^/v1/projects$", api_projects),
    ("POST", r"^/v1/projects/([^/]+)$", api_project_edit),
    ("POST", r"^/v1/order$", api_card_order),
    ("POST", r"^/v1/events$", api_post_event),
    ("GET", r"^/v1/events$", api_events),
    ("GET", r"^/v1/search$", api_search),
    ("POST", r"^/v1/messages$", api_send),
    ("GET", r"^/v1/messages$", api_messages),
    ("GET", r"^/v1/inbox$", api_inbox),
    ("GET", r"^/v1/threads/([^/]+)$", api_thread),
    ("GET", r"^/v1/deliveries$", api_deliveries),
    ("POST", r"^/v1/permissions$", api_perm_request),
    ("POST", r"^/v1/permissions/resolved$", api_perm_resolved),
    ("POST", r"^/v1/permissions/(\d+)$", api_perm_decide),
    ("GET", r"^/v1/skills$", api_skills),
    ("GET", r"^/v1/skills/export$", api_skills_export),
    ("POST", r"^/v1/skills$", api_skill_publish),
    ("DELETE", r"^/v1/skills/([^/]+)$", api_skill_delete),
]
ROUTES += shared.ROUTES
ROUTES = [(m, re.compile(p), f) for m, p, f in ROUTES]
