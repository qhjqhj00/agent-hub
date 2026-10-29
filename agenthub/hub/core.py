"""Domain operations shared by the HTTP API and the Telegram bot.

The shape of a collaboration:

    A: delegate(to=B, goal, context, acceptance)   -> task #n, delivery to B
    B: (woken if needed) reads it, logs a plan      -> delivery to A
    B: question / A: answer                         -> deliveries both ways
    B: result                                       -> state=review, delivery to A
    A: close(done) or comment (sends it back)       -> delivery to B

Every arrow is a row in `deliveries`. Whoever is on the other end gets it
pushed into their live session, or has one started for them (`orders`).
"""
import json
import uuid

from . import bus
from .i18n import t as tr
from .store import (HUMAN, LOG_KINDS, OPEN_STATES, TASK_STATES, ago, jloads, live_session,
                    now, presence, resolve, scopes_of)


class HubError(Exception):
    def __init__(self, code, msg):
        Exception.__init__(self, msg)
        self.code = code


def split_addr(addr):
    """'backend@shop' -> ('', 'backend', 'shop'); 'bob' -> ('bob', '', '')."""
    addr = (addr or "").strip()
    if "@" in addr:
        role, project = addr.split("@", 1)
        return "", role.strip(), project.strip()
    return addr, "", ""


def agent_row(conn, name):
    return conn.execute("SELECT * FROM agents WHERE name=?", (resolve(conn, name),)).fetchone()


def agent_card(conn, r):
    a = dict(r)
    a["capabilities"] = jloads(a.get("capabilities") or "[]", [])
    sess = live_session(conn, a["name"])
    # "live" means a session is pulling right now; a recent CLI call is only "idle"
    a["presence"] = "live" if sess else ("gone" if presence(a.get("last_seen")) == "gone" else "idle")
    a["session"] = dict(sess) if sess else None
    a["channel"] = bool(sess and sess["channel"])
    a["scopes"] = scopes_of(conn, a["name"])
    a["home"] = "%s@%s" % (a["workspace"] or "?", a["node"] or "?")
    t = conn.execute(
        "SELECT id,title,state FROM tasks WHERE owner=? AND state IN (%s) ORDER BY updated_at DESC LIMIT 1"
        % ",".join("?" * len(OPEN_STATES)), (a["name"],) + OPEN_STATES).fetchone()
    a["current_task"] = dict(t) if t else None
    a["pending"] = conn.execute(
        "SELECT COUNT(*) FROM deliveries WHERE agent=? AND delivered_at IS NULL",
        (a["name"],)).fetchone()[0]
    return a


def holders(conn, role, project):
    q = "SELECT DISTINCT agent FROM agent_scopes WHERE role=?"
    args = [role]
    if project:
        q += " AND project=?"
        args.append(project)
    return [r["agent"] for r in conn.execute(q, args)]


# ---------------------------------------------------------------- delivery

def deliver(conn, agent, kind, content, task_id=None, msg_id=None, meta=None, wake=True):
    """Queue something for `agent` to react to, and make sure someone will hear it."""
    agent = resolve(conn, agent)
    if not agent:
        return None
    m = {"kind": kind}
    if task_id:
        m["task_id"] = str(task_id)
    m.update({k: str(v) for k, v in (meta or {}).items() if v is not None})
    cur = conn.execute(
        "INSERT INTO deliveries (agent,kind,task_id,msg_id,content,meta,created_at) VALUES (?,?,?,?,?,?,?)",
        (agent, kind, task_id, msg_id, content, json.dumps(m), now()))
    if wake and agent != HUMAN:
        ensure_listening(conn, agent, "%s%s" % (kind, " #%s" % task_id if task_id else ""))
    bus.bump()
    return cur.lastrowid


def ensure_listening(conn, agent, reason):
    """If nobody is listening for `agent`, ask its home node to start a session."""
    if live_session(conn, agent):
        return None
    a = conn.execute("SELECT * FROM agents WHERE name=?", (agent,)).fetchone()
    if not a or a["wake"] != "auto" or not a["node"] or not a["workspace"]:
        return None
    pending = conn.execute(
        "SELECT id FROM orders WHERE agent=? AND kind='wake' AND state IN ('pending','taken') "
        "AND created_at > strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-5 minutes')", (agent,)).fetchone()
    if pending:
        return pending["id"]
    return order(conn, a["node"], "wake", agent,
                 {"workspace": a["workspace"], "kind": a["kind"], "resume": a["conversation"] or a["last_session"]},
                 reason)


def order(conn, node, kind, agent="", args=None, reason=""):
    cur = conn.execute(
        "INSERT INTO orders (node,kind,agent,args,reason,created_at) VALUES (?,?,?,?,?,?)",
        (node, kind, agent, json.dumps(args or {}), reason, now()))
    bus.bump()
    return cur.lastrowid


# ---------------------------------------------------------------- tasks

def task_dict(conn, r, full=False):
    t = dict(r)
    if full:
        t["log"] = [dict(x) for x in conn.execute(
            "SELECT * FROM task_log WHERE task_id=? ORDER BY id", (t["id"],))]
        t["children"] = [dict(x) for x in conn.execute(
            "SELECT id,title,state,owner,role,project FROM tasks WHERE parent_id=? ORDER BY id", (t["id"],))]
        if t["parent_id"]:
            p = conn.execute("SELECT id,title,state FROM tasks WHERE id=?", (t["parent_id"],)).fetchone()
            t["parent"] = dict(p) if p else None
    return t


def get_task(conn, tid):
    r = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not r:
        raise HubError(404, "no task #%s" % tid)
    return r


def log(conn, tid, agent, kind, body):
    conn.execute("INSERT INTO task_log (task_id,agent,kind,body,ts) VALUES (?,?,?,?,?)",
                 (tid, agent, kind, body, now()))


def _fmt_task(t, by):
    parts = ["[agent-hub] New task #%d from %s%s" % (
        t["id"], by, " (project %s)" % t["project"] if t["project"] else "")]
    parts.append("Title: %s" % t["title"])
    if t["goal"]:
        parts.append("Goal:\n%s" % t["goal"])
    if t["context"]:
        parts.append("Context from %s:\n%s" % (by, t["context"]))
    if t["acceptance"]:
        parts.append("Done when:\n%s" % t["acceptance"])
    if t["parent_id"]:
        parts.append("Part of task #%d." % t["parent_id"])
    parts.append(
        "Next: read the context, look at the code it points to, then post your plan with "
        "hub_update(task_id=%d, kind=\"plan\", body=...) before you start. Ask %s with "
        "kind=\"question\" if something blocks you. Finish with kind=\"result\" (what changed, "
        "files, anything left)." % (t["id"], by))
    return "\n\n".join(parts)


def delegate(conn, by, to, title, goal="", context="", acceptance="", project="",
             parent_id=None, priority=0, from_event=None):
    if not title:
        raise HubError(400, "title required")
    name, role, rproject = split_addr(to)
    project = project or rproject
    if parent_id and not project:
        p = conn.execute("SELECT project FROM tasks WHERE id=?", (parent_id,)).fetchone()
        project = p["project"] if p else ""
    owner = ""
    if name:
        owner = resolve(conn, name)
        if owner != HUMAN and not agent_row(conn, owner):
            raise HubError(404, "no agent named %r (hub_agents lists them)" % name)
    elif not role:
        raise HubError(400, "to: an agent name or role@project")
    if not project and by != HUMAN:
        sc = scopes_of(conn, by)
        project = sc[0]["project"] if sc else ""
    ts = now()
    cur = conn.execute(
        "INSERT INTO tasks (project,parent_id,title,goal,context,acceptance,role,owner,created_by,"
        "state,priority,from_event,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'open',?,?,?,?)",
        (project, parent_id, title, goal, context, acceptance, role, owner, by, priority,
         from_event, ts, ts))
    tid = cur.lastrowid
    if project:
        conn.execute("INSERT OR IGNORE INTO projects (name,created_at) VALUES (?,?)", (project, ts))
    log(conn, tid, by, "created", "→ %s" % (owner or "%s@%s" % (role, project)))
    t = get_task(conn, tid)
    text = _fmt_task(t, by)
    if owner:
        deliver(conn, owner, "task", text, tid, meta={"from": by})
    else:
        hs = [h for h in holders(conn, role, project) if h != by]
        live = [h for h in hs if live_session(conn, h)]
        if live:
            for h in live:
                deliver(conn, h, "task", text + "\n\n(addressed to role %s@%s -- hub_claim(%d) to take it)"
                        % (role, project, tid), tid, meta={"from": by, "role": role})
        elif hs:
            best = max(hs, key=lambda h: (agent_row(conn, h)["last_seen"] or ""))
            deliver(conn, best, "task", text + "\n\n(addressed to role %s@%s -- hub_claim(%d) to take it)"
                    % (role, project, tid), tid, meta={"from": by, "role": role})
        else:
            deliver(conn, HUMAN, "unrouted", "Task #%d %r is addressed to %s@%s, but nobody holds that role."
                    % (tid, title, role, project), tid)
    bus.bump()
    return task_dict(conn, get_task(conn, tid), full=True)


def _set_state(conn, tid, state):
    closed = now() if state in ("done", "dropped") else None
    conn.execute("UPDATE tasks SET state=?, updated_at=?, closed_at=? WHERE id=?",
                 (state, now(), closed, tid))


def _other_party(t, me):
    """Who should hear about something `me` wrote in task t."""
    out = set()
    if t["created_by"] and t["created_by"] != me:
        out.add(t["created_by"])
    if t["owner"] and t["owner"] != me:
        out.add(t["owner"])
    return out


def update(conn, tid, by, kind, body):
    if kind not in LOG_KINDS:
        raise HubError(400, "kind must be one of %s" % ", ".join(LOG_KINDS))
    t = get_task(conn, tid)
    if t["state"] in ("done", "dropped") and kind not in ("comment",):
        raise HubError(409, "task #%d is %s" % (tid, t["state"]))
    # Posting a plan on an unowned role task is taking it.
    if not t["owner"] and by != t["created_by"] and kind in ("plan", "progress", "result"):
        conn.execute("UPDATE tasks SET owner=? WHERE id=?", (by, tid))
        log(conn, tid, by, "claimed", "")
        t = get_task(conn, tid)
    log(conn, tid, by, kind, body)
    state = LOG_KINDS[kind]
    if kind == "plan":
        conn.execute("UPDATE tasks SET plan=? WHERE id=?", (body, tid))
    if kind == "result":
        conn.execute("UPDATE tasks SET result=? WHERE id=?", (body, tid))
    # The delegator answering or commenting un-blocks / sends back for rework.
    if by == t["created_by"] and kind in ("answer", "comment") and t["state"] in ("blocked", "needs_human", "review"):
        state = "in_progress"
    if by == HUMAN and t["state"] == "needs_human" and kind in ("answer", "comment"):
        state = "in_progress"
    if state:
        _set_state(conn, tid, state)
    else:
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), tid))
    t = get_task(conn, tid)
    head = "[agent-hub] Task #%d %r -- %s posted %s:" % (tid, t["title"], by, kind)
    tail = ""
    if kind == "result":
        tail = ("\n\nReview it. Accept with hub_close(task_id=%d, state=\"done\"), or send it back "
                "with hub_update(task_id=%d, kind=\"comment\", body=...)." % (tid, tid))
    elif kind == "question":
        tail = "\n\nAnswer with hub_update(task_id=%d, kind=\"answer\", body=...)." % tid
    elif by == t["created_by"] and state == "in_progress":
        tail = "\n\nTask is back to in_progress -- continue, then post a new result."
    for who in _other_party(t, by):
        deliver(conn, who, kind, "%s\n%s%s" % (head, body, tail), tid, meta={"from": by})
    if kind == "needs_human" and HUMAN not in _other_party(t, by):
        deliver(conn, HUMAN, kind, "%s\n%s" % (head, body), tid, meta={"from": by})
    bus.bump()
    return task_dict(conn, t, full=True)


def claim(conn, tid, by):
    t = get_task(conn, tid)
    if t["owner"] and t["owner"] != by:
        raise HubError(409, "task #%d is owned by %s" % (tid, t["owner"]))
    conn.execute("UPDATE tasks SET owner=?, updated_at=? WHERE id=?", (by, now(), tid))
    log(conn, tid, by, "claimed", "")
    if t["created_by"] and t["created_by"] != by:
        deliver(conn, t["created_by"], "claimed", "[agent-hub] %s took task #%d %r."
                % (by, tid, t["title"]), tid, meta={"from": by}, wake=False)
    bus.bump()
    return task_dict(conn, get_task(conn, tid), full=True)


def close(conn, tid, by, state, note=""):
    if state not in ("done", "dropped", "open"):
        raise HubError(400, "state: done | dropped | open")
    t = get_task(conn, tid)
    _set_state(conn, tid, state)
    if state == "open":
        conn.execute("UPDATE tasks SET closed_at=NULL WHERE id=?", (tid,))
    log(conn, tid, by, state if state != "open" else "reopened", note)
    for who in _other_party(t, by):
        deliver(conn, who, "closed", "[agent-hub] Task #%d %r marked %s by %s.%s"
                % (tid, t["title"], state, by, ("\n" + note) if note else ""), tid,
                meta={"from": by}, wake=(state == "open"))
    bus.bump()
    return task_dict(conn, get_task(conn, tid), full=True)


def assign(conn, tid, by, to):
    """Re-point a task at a different agent or role (dashboard / bot)."""
    t = get_task(conn, tid)
    name, role, project = split_addr(to)
    owner = resolve(conn, name) if name else ""
    conn.execute("UPDATE tasks SET owner=?, role=?, project=CASE WHEN ?='' THEN project ELSE ? END, "
                 "updated_at=? WHERE id=?", (owner, role, project, project, now(), tid))
    log(conn, tid, by, "assigned", "→ %s" % to)
    t = get_task(conn, tid)
    if owner:
        deliver(conn, owner, "task", _fmt_task(t, t["created_by"] or by), tid, meta={"from": by})
    bus.bump()
    return task_dict(conn, t, full=True)


def renotify(conn, tid, by):
    """Push the task to its owner again (it was delivered, but nothing happened)."""
    t = get_task(conn, tid)
    if not t["owner"]:
        raise HubError(400, "task #%d has no owner to remind" % tid)
    log(conn, tid, by, "comment", tr("task.renotified"))
    deliver(conn, t["owner"], "task", "[agent-hub] Reminder -- this task was sent to you earlier and has had no "
            "response yet.\n\n" + _fmt_task(t, t["created_by"] or by), tid, meta={"from": by})
    bus.bump()
    return task_dict(conn, get_task(conn, tid), full=True)


# ---------------------------------------------------------------- messages

def send(conn, by, to, body, reply_to=None, task_id=None):
    if task_id:
        return update(conn, int(task_id), by, "comment", body)
    thread = None
    if reply_to:
        orig = conn.execute("SELECT * FROM messages WHERE id=?", (reply_to,)).fetchone()
        if not orig:
            raise HubError(404, "no message %s" % reply_to)
        to = to or orig["from_agent"]
        thread = orig["thread_id"]
        conn.execute("UPDATE messages SET state='answered' WHERE id=?", (reply_to,))
    if not to:
        raise HubError(400, "to required")
    name, role, project = split_addr(to)
    targets = [resolve(conn, name)] if name else holders(conn, role, project)
    if name and name != HUMAN and not agent_row(conn, name):
        raise HubError(404, "no agent named %r" % name)
    if not targets:
        raise HubError(404, "nobody holds %s" % to)
    thread = thread or uuid.uuid4().hex[:10]
    cur = conn.execute(
        "INSERT INTO messages (thread_id,from_agent,to_agent,to_role,to_project,body,reply_to,ts) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (thread, by, name or "", role, project, body, reply_to, now()))
    mid = cur.lastrowid
    text = "[agent-hub] Message from %s%s:\n%s\n\nReply with hub_send(reply_to=%d, body=...) if a reply is owed." % (
        by, " (to %s)" % to if role else "", body, mid)
    for t in targets:
        if t != by:
            deliver(conn, t, "message", text, msg_id=mid, meta={"from": by, "msg_id": mid})
    bus.bump()
    return {"id": mid, "thread_id": thread, "to": targets}


# ---------------------------------------------------------------- attention

def attention(conn):
    """What needs the human, most urgent first. Empty is the goal."""
    items = []
    for r in conn.execute("SELECT * FROM permissions WHERE state='pending' ORDER BY id DESC"):
        items.append({"type": "permission", "id": r["id"], "agent": r["agent"],
                      "title": "%s wants %s" % (r["agent"], r["tool"]),
                      "body": r["description"], "preview": r["preview"], "ts": r["created_at"]})
    for r in conn.execute("SELECT * FROM tasks WHERE state IN ('needs_human','blocked') ORDER BY updated_at DESC"):
        last = conn.execute("SELECT body FROM task_log WHERE task_id=? ORDER BY id DESC LIMIT 1",
                            (r["id"],)).fetchone()
        items.append({"type": r["state"], "id": r["id"], "agent": r["owner"], "title": r["title"],
                      "body": last["body"] if last else "", "ts": r["updated_at"], "project": r["project"]})
    for r in conn.execute("SELECT * FROM tasks WHERE state='review' AND created_by=? ORDER BY updated_at DESC",
                          (HUMAN,)):
        items.append({"type": "review", "id": r["id"], "agent": r["owner"], "title": r["title"],
                      "body": r["result"], "ts": r["updated_at"], "project": r["project"]})
    for r in conn.execute("SELECT * FROM tasks WHERE state='open' AND owner='' AND "
                          "created_at < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-30 minutes') AND "
                          "created_at > strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-3 days') ORDER BY id DESC"):
        items.append({"type": "unclaimed", "id": r["id"], "agent": "", "title": r["title"],
                      "body": "addressed to %s@%s, nobody took it" % (r["role"], r["project"]),
                      "ts": r["created_at"], "project": r["project"]})
    # delivered to its owner, but no plan / reply for 10 minutes: probably stuck on something
    for r in conn.execute(
            "SELECT t.* FROM tasks t WHERE t.state='open' AND t.owner!='' AND "
            "t.updated_at < strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-10 minutes') AND "
            "t.updated_at > strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-3 days') AND EXISTS ("
            "SELECT 1 FROM deliveries d WHERE d.task_id=t.id AND d.agent=t.owner AND d.kind='task' "
            "AND d.delivered_at IS NOT NULL) ORDER BY t.updated_at DESC"):
        items.append({"type": "stalled", "id": r["id"], "agent": r["owner"], "title": r["title"],
                      "body": tr("att.stalled", owner=r["owner"], ago=ago(r["updated_at"])),
                      "ts": r["updated_at"], "project": r["project"]})
    for r in conn.execute("SELECT * FROM deliveries WHERE agent=? AND kind='unrouted' "
                          "AND created_at > strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-1 day') ORDER BY id DESC", (HUMAN,)):
        items.append({"type": "unrouted", "id": r["task_id"], "agent": "", "title": "unroutable task",
                      "body": r["content"], "ts": r["created_at"]})
    return items


def task_states():
    return TASK_STATES
