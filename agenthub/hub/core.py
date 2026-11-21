"""Domain operations: sessions, the inbox, routing, tasks.

The shape of a collaboration:

    A: delegate(to=B, goal, context, acceptance)   -> task #n, post, inbox row for B
    B: plan                                        -> state=working, inbox row for A
    B: question / A: answer                        -> inbox rows both ways
    B: result                                      -> state=review, inbox row for A
    A: close(done) or comment (sends it back)      -> inbox row for B

`human` is never a mailbox of its own: whatever is addressed to the human goes to
the project's liaison (its resident agy in T3), where the human reads it on their phone.
The hub starts nothing; an inbox row waits until a running session of its agent takes it.
"""
import hashlib
import json
import re
import secrets
import time

from . import bus
from .store import (HUMAN, KINDS, LEASE, LIVE_AFTER, OPEN_STATES, PUSH, STALE_AFTER, TASK_STATES, UPDATE_KINDS,
                    age, ago_ts, jloads, live_session, now, presence, scopes_of)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,47}$")


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


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ agents

def agent_row(conn, name):
    return conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()


def start_command(kind, name, workspace, conversation="", project=""):
    """The command the human copies to start this agent. The adapters own the templates."""
    try:
        from ..node import adapters
        fn = getattr(adapters, "start_command", None)
        if fn:
            return fn(kind, name, workspace, conversation=conversation, project=project)
    except Exception:
        pass
    if kind == "claude":
        return ("cd %s && HUB_CHANNEL=1 HUB_AGENT=%s claude --dangerously-load-development-channels "
                "plugin:agent-hub@agent-hub%s" % (workspace or "~", name,
                                                  " --resume " + conversation if conversation else ""))
    return "cd %s  # start %s here, then tell it: 你叫 %s" % (workspace or "~", kind, name)


def liaison_of(conn, name):
    return [r["name"] for r in conn.execute("SELECT name FROM projects WHERE liaison=?", (name,))]


def agent_card(conn, r):
    s = live_session(conn, r["name"])
    cur = conn.execute("SELECT id, title, state FROM tasks WHERE owner=? AND state IN ('working','open','review') "
                       "ORDER BY state='working' DESC, updated_at DESC LIMIT 1", (r["name"],)).fetchone()
    scopes = scopes_of(conn, r["name"])
    stale = conn.execute("SELECT COUNT(*), MIN(created_at) FROM inbox WHERE agent=? AND delivered_at IS NULL "
                         "AND created_at < ?", (r["name"], ago_ts(STALE_AFTER))).fetchone()
    return {
        "name": r["name"], "kind": r["kind"], "node": r["node"], "workspace": r["workspace"],
        "description": r["description"], "archived": bool(r["hidden"]),
        "presence": presence(conn, r["name"], r["last_seen"]),
        "state": s["state"] if s else "", "push": s["push"] if s else "",
        "scopes": scopes, "liaison_of": liaison_of(conn, r["name"]),
        "current_task": dict(cur) if cur else None,
        "pending": conn.execute("SELECT COUNT(*) FROM inbox WHERE agent=? AND delivered_at IS NULL",
                                (r["name"],)).fetchone()[0],
        # queued and not delivered for a while: a bridge is down, or the session is not taking its items
        "stale_queued": stale[0], "oldest_queued_s": int(age(stale[1]) or 0) if stale[0] else 0,
        "start": start_command(r["kind"], r["name"], r["workspace"], r["conversation"],
                               scopes[0]["project"] if scopes else ""),
        "last_seen": r["last_seen"],
    }


def list_agents(conn, project="", archived=False):
    """archived=False: the active roster (what the dashboard and hub_agents show); True: only the archive;
    None: everyone. Archived agents keep their sessions, tasks and history, and are still addressable."""
    if project:
        rows = conn.execute("SELECT * FROM agents WHERE name IN (SELECT agent FROM scopes WHERE project=?) "
                            "ORDER BY name", (project,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM agents ORDER BY name").fetchall()
    return [agent_card(conn, r) for r in rows if archived is None or bool(r["hidden"]) == archived]


def add_scope(conn, agent, project, role=""):
    if not project:
        return
    conn.execute("INSERT OR IGNORE INTO projects (name, created_at) VALUES (?,?)", (project, now()))
    conn.execute("INSERT OR IGNORE INTO scopes (agent, project, role) VALUES (?,?,?)", (agent, project, role or ""))


# ------------------------------------------------------------------ sessions

def register(conn, ctx, b):
    name = (b.get("name") or "").strip()
    kind = (b.get("kind") or "").strip()
    if not NAME_RE.match(name) or name == HUMAN:
        raise HubError(400, "name: letters, digits, - _ . (max 48), and not %r" % HUMAN)
    if kind not in KINDS:
        raise HubError(400, "kind must be one of %s" % ", ".join(KINDS))
    push = b.get("push") or "pull"
    if push not in PUSH:
        raise HubError(400, "push must be one of %s" % ", ".join(PUSH))
    node = ctx.get("node") or ""
    conv = (b.get("conversation") or "").strip()
    holder = live_session(conn, name)
    ts = now()
    if holder:
        same_conv = bool(conv) and conv == holder["conversation"]
        same_proc = bool(b.get("pid")) and holder["pid"] == b.get("pid") and holder["node"] == node
        if not (same_conv or same_proc):
            raise HubError(409, "%s is in use by a live session on %s (%s). Pick another name, or end that one."
                           % (name, holder["node"], holder["cwd"]))
    # one name, one session: whatever this agent still has open is over now
    conn.execute("UPDATE sessions SET ended_at=? WHERE agent=? AND ended_at IS NULL", (ts, name))
    cwd = b.get("cwd") or ""
    if agent_row(conn, name):
        conn.execute("UPDATE agents SET kind=?, node=?, workspace=?, description=COALESCE(NULLIF(?,''),description), "
                     "conversation=COALESCE(NULLIF(?,''),conversation), last_seen=? WHERE name=?",
                     (kind, node, cwd, b.get("description") or "", conv, ts, name))
    else:
        conn.execute("INSERT INTO agents (name,kind,node,workspace,description,conversation,created_at,last_seen) "
                     "VALUES (?,?,?,?,?,?,?,?)", (name, kind, node, cwd, b.get("description") or "", conv, ts, ts))
    add_scope(conn, name, (b.get("project") or "").strip(), (b.get("role") or "").strip())
    if node:
        conn.execute("INSERT INTO nodes (name,last_seen) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET "
                     "last_seen=excluded.last_seen", (node, ts))
    sid = "s_" + secrets.token_hex(8)
    token = "hst_" + secrets.token_urlsafe(24)
    conn.execute("INSERT INTO sessions (id,agent,node,cwd,kind,conversation,push,meta,token_hash,pid,started_at,last_seen) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (sid, name, node, cwd, kind, conv, push, json.dumps(b.get("meta") or {}),
                  _hash(token), b.get("pid"), ts, ts))
    # leases held by the sessions just ended are void
    conn.execute("UPDATE inbox SET sent_at=NULL, session='' WHERE agent=? AND delivered_at IS NULL", (name,))
    bus.bump()
    return {"session_id": sid, "token": token, "agent": agent_card(conn, agent_row(conn, name)),
            "queued": _queued(conn, name)}


def session_for(conn, ctx, token):
    """The running session an X-Hub-Session token belongs to; touches its heartbeat."""
    if not token:
        return None
    s = conn.execute("SELECT * FROM sessions WHERE token_hash=?", (_hash(token),)).fetchone()
    if not s or s["ended_at"]:
        raise HubError(401, "session ended or unknown; register again (hub_register)")
    if ctx.get("role") == "node" and s["node"] and ctx.get("node") != s["node"]:
        raise HubError(401, "session belongs to another node")
    if (age(s["last_seen"]) or 0) >= LIVE_AFTER:
        conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (now(), s["id"]))
        raise HubError(401, "session expired (no heartbeat for %ds); register again" % LIVE_AFTER)
    ts = now()
    conn.execute("UPDATE sessions SET last_seen=? WHERE id=?", (ts, s["id"]))
    conn.execute("UPDATE agents SET last_seen=? WHERE name=?", (ts, s["agent"]))
    return s


def own(s, sid):
    if not s or s["id"] != sid:
        raise HubError(403, "that is not your session")


def end_session(conn, s):
    conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (now(), s["id"]))
    conn.execute("UPDATE inbox SET sent_at=NULL, session='' WHERE session=? AND delivered_at IS NULL", (s["id"],))
    bus.bump()


def set_state(conn, s, state):
    if state not in ("busy", "idle"):
        raise HubError(400, "state: busy | idle")
    conn.execute("UPDATE sessions SET state=? WHERE id=?", (state, s["id"]))
    bus.bump()


def set_meta(conn, s, b):
    if b.get("push") and b["push"] not in PUSH:
        raise HubError(400, "push must be one of %s" % ", ".join(PUSH))
    meta = jloads(s["meta"], {})
    meta.update(b.get("meta") or {})
    conv = b.get("conversation") or s["conversation"]
    conn.execute("UPDATE sessions SET conversation=?, push=?, meta=? WHERE id=?",
                 (conv, b.get("push") or s["push"], json.dumps(meta), s["id"]))
    if b.get("conversation"):
        conn.execute("UPDATE agents SET conversation=? WHERE name=?", (conv, s["agent"]))
    bus.bump()


def session_dict(s):
    d = {k: s[k] for k in s.keys() if k != "token_hash"}
    d["meta"] = jloads(s["meta"], {})
    return d


def _queued(conn, agent):
    return conn.execute("SELECT COUNT(*) FROM inbox WHERE agent=? AND delivered_at IS NULL", (agent,)).fetchone()[0]


def take(conn, s, limit=20):
    """Lease what is waiting for this session's agent (LEASE seconds)."""
    rows = conn.execute(
        "SELECT i.*, p.sender FROM inbox i JOIN posts p ON p.id=i.post_id WHERE i.agent=? AND i.delivered_at IS NULL "
        "AND (i.sent_at IS NULL OR i.sent_at < ?) ORDER BY i.id LIMIT ?",
        (s["agent"], ago_ts(LEASE), limit)).fetchall()
    ts = now()
    for r in rows:
        conn.execute("UPDATE inbox SET sent_at=?, session=? WHERE id=?", (ts, s["id"], r["id"]))
    return [{"id": r["id"], "post_id": r["post_id"], "kind": r["kind"], "task_id": r["task_id"],
             "from": r["sender"], "ts": r["created_at"], "text": r["text"]} for r in rows]


def pull(dbf, ctx, token, sid, wait):
    """Long-poll: hold the request until something is waiting or `wait` seconds pass."""
    wait = max(0, min(55, int(wait)))
    deadline = time.time() + wait
    while True:
        v = bus.version()
        with dbf() as conn:
            s = session_for(conn, ctx, token)
            own(s, sid)
            items = take(conn, s)
        if items or time.time() >= deadline:
            return {"items": items}
        bus.wait(v, min(25, max(0.1, deadline - time.time())))


def delivered(conn, s, ids):
    ids = [int(i) for i in ids or []]
    if ids:
        conn.execute("UPDATE inbox SET delivered_at=? WHERE agent=? AND delivered_at IS NULL AND id IN (%s)"
                     % ",".join("?" * len(ids)), [now(), s["agent"]] + ids)
        bus.bump()


def inbox(conn, s):
    items = take(conn, s, limit=50)
    delivered(conn, s, [i["id"] for i in items])
    return {"items": items}


# ------------------------------------------------------------------ routing

def liaison(conn, project):
    if not project:
        return ""
    r = conn.execute("SELECT liaison FROM projects WHERE name=?", (project,)).fetchone()
    return r["liaison"] if r else ""


def home_project(conn, agent):
    sc = scopes_of(conn, agent)
    return sc[0]["project"] if sc else ""


def holders(conn, role, project):
    return [r["agent"] for r in conn.execute("SELECT agent FROM scopes WHERE project=? AND role=?", (project, role))]


def recipients(conn, addr, by, project=""):
    """Agent names an address reaches. `human` -> the project's liaison; `*@p` -> p's members."""
    name, role, rproject = split_addr(addr)
    if name == HUMAN:
        li = liaison(conn, project or home_project(conn, by))
        return [li] if li and li != by else []
    if name:
        if not agent_row(conn, name):
            raise HubError(404, "no agent named %r (hub_agents lists them)" % name)
        return [name]
    if not rproject:
        raise HubError(400, "address: an agent name, role@project, *@project or human")
    if role == "*":
        return [r["agent"] for r in conn.execute(
            "SELECT DISTINCT agent FROM scopes WHERE project=? AND agent!=? ORDER BY agent", (rproject, by))]
    hs = [h for h in holders(conn, role, rproject) if h != by]
    live = [h for h in hs if live_session(conn, h)]
    if live:
        return live
    if hs:
        return [max(hs, key=lambda h: agent_row(conn, h)["last_seen"] or "")]
    return []


def add_post(conn, sender, kind, body, recipient="", task_id=None, project="", thread_id="", reply_to=None):
    pid = conn.execute("INSERT INTO posts (task_id,thread_id,reply_to,sender,recipient,kind,body,project,ts) "
                       "VALUES (?,?,?,?,?,?,?,?,?)",
                       (task_id, thread_id, reply_to, sender, recipient, kind, body, project, now())).lastrowid
    if not thread_id and task_id is None:
        conn.execute("UPDATE posts SET thread_id=? WHERE id=?", ("m%d" % pid, pid))
    return pid


def enqueue(conn, agent, post_id, kind, text, task_id=None):
    conn.execute("INSERT INTO inbox (agent,post_id,kind,task_id,text,created_at) VALUES (?,?,?,?,?,?)",
                 (agent, post_id, kind, task_id, text, now()))


def send(conn, by, to, body, reply_to=None):
    if not (body or "").strip():
        raise HubError(400, "body required")
    thread = ""
    if reply_to:
        p = conn.execute("SELECT * FROM posts WHERE id=?", (int(reply_to),)).fetchone()
        if not p:
            raise HubError(404, "no message %s" % reply_to)
        thread = p["thread_id"]
        to = to or p["sender"]
    if not to:
        raise HubError(400, "to required")
    _, role, rproject = split_addr(to)
    project = rproject or home_project(conn, by)
    who = recipients(conn, to, by, project)
    kind = "post" if role == "*" else "message"
    pid = add_post(conn, by, kind, body, to, None, project, thread, int(reply_to) if reply_to else None)
    head = "[agent-hub] %s from %s%s (msg_id=%d):" % (
        "Team post" if kind == "post" else "Message", by,
        " to %s" % to if (who and to != who[0]) else "", pid)
    tail = "" if kind == "post" else "\n\nReply with hub_send(reply_to=%d, body=...) if a reply is owed." % pid
    for w in who:
        enqueue(conn, w, pid, kind, "%s\n%s%s" % (head, body, tail))
    bus.bump()
    thread_id = conn.execute("SELECT thread_id FROM posts WHERE id=?", (pid,)).fetchone()["thread_id"]
    return {"post_id": pid, "thread_id": thread_id, "delivered_to": who}


def thread(conn, thread_id):
    return [dict(r) for r in conn.execute("SELECT * FROM posts WHERE thread_id=? ORDER BY id", (thread_id,))]


# ------------------------------------------------------------------ tasks

def get_task(conn, tid):
    t = conn.execute("SELECT * FROM tasks WHERE id=?", (int(tid),)).fetchone()
    if not t:
        raise HubError(404, "no task #%s" % tid)
    return t


def task_dict(conn, t, full=False):
    d = dict(t)
    if full:
        d["posts"] = [dict(r) for r in conn.execute("SELECT * FROM posts WHERE task_id=? ORDER BY id", (t["id"],))]
        d["children"] = [dict(r) for r in conn.execute(
            "SELECT id, title, state, owner FROM tasks WHERE parent_id=? ORDER BY id", (t["id"],))]
        d["liaison"] = liaison(conn, t["project"])
    return d


def list_tasks(conn, q):
    sql, args = "SELECT * FROM tasks WHERE 1=1", []
    for key in ("project", "owner", "created_by"):
        if q.get(key):
            sql += " AND %s=?" % key
            args.append(q[key])
    if q.get("state"):
        states = [s for s in q["state"].split(",") if s]
        if states == ["active"]:
            states = list(OPEN_STATES)
        sql += " AND state IN (%s)" % ",".join("?" * len(states))
        args += states
    if q.get("q"):
        sql += " AND (title LIKE ? OR goal LIKE ? OR result LIKE ?)"
        args += ["%" + q["q"] + "%"] * 3
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(int(q.get("limit") or 100))
    return [dict(r) for r in conn.execute(sql, args)]


def _fmt_task(t, by):
    parts = ["[agent-hub] New task #%d from %s%s" % (t["id"], by, " (project %s)" % t["project"] if t["project"] else "")]
    parts.append("Title: %s" % t["title"])
    if t["goal"]:
        parts.append("Goal:\n%s" % t["goal"])
    if t["context"]:
        parts.append("Context from %s:\n%s" % (by, t["context"]))
    if t["acceptance"]:
        parts.append("Done when:\n%s" % t["acceptance"])
    if t["parent_id"]:
        parts.append("Part of task #%d." % t["parent_id"])
    parts.append("Next: read the context, look at the code it points to, then post your plan with "
                 "hub_update(task_id=%d, kind=\"plan\", body=...) before you start. Ask %s with kind=\"question\" "
                 "if something blocks you; a decision only the human can make goes to them with "
                 "kind=\"question\", to=\"human\". Finish with kind=\"result\" (what changed, files, anything left)."
                 % (t["id"], by))
    return "\n\n".join(parts)


def _party(conn, t, who):
    """The agent that hears for `who` in task t: the human is heard by the project's liaison."""
    return liaison(conn, t["project"]) if who == HUMAN else who


def _others(conn, t, me):
    out = []
    for w in (t["created_by"], t["owner"]):
        a = _party(conn, t, w) if w else ""
        if a and a != me and a not in out:
            out.append(a)
    return out


def _liaison_note(conn, t, w, to=""):
    li = liaison(conn, t["project"])
    if w == li and (to == HUMAN or HUMAN in (t["created_by"], t["owner"])):
        p = t["project"] or "the project"
        return ("\n\n(You are %s's liaison: put this to the human in this conversation, then write their "
                "answer back with hub_update / hub_close. Project rules for the liaison, if any, are in the "
                "shared-board note liaison-%s: hub_board(action=\"get\", type=\"note\", name=\"liaison-%s\").)"
                % (p, t["project"] or "PROJECT", t["project"] or "PROJECT"))
    return ""


def delegate(conn, by, to, title, goal="", context="", acceptance="", project="", parent_id=None):
    if not (title or "").strip():
        raise HubError(400, "title required")
    name, role, rproject = split_addr(to)
    project = project or rproject
    if parent_id and not project:
        project = get_task(conn, parent_id)["project"]
    project = project or home_project(conn, by)
    owner = ""
    if name:
        if name != HUMAN and not agent_row(conn, name):
            raise HubError(404, "no agent named %r (hub_agents lists them)" % name)
        owner = name
    elif not role:
        raise HubError(400, "to: an agent name or role@project")
    ts = now()
    tid = conn.execute(
        "INSERT INTO tasks (project,parent_id,title,goal,context,acceptance,role,owner,created_by,state,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,'open',?,?)",
        (project, parent_id, title, goal, context, acceptance, role, owner, by, ts, ts)).lastrowid
    if project:
        conn.execute("INSERT OR IGNORE INTO projects (name, created_at) VALUES (?,?)", (project, ts))
    t = get_task(conn, tid)
    target = owner or "%s@%s" % (role, project)
    pid = add_post(conn, by, "task", "→ %s" % target, target, tid, project)
    text = _fmt_task(t, by)
    if owner:
        a = _party(conn, t, owner)
        who = [a] if a else []
    else:
        who = recipients(conn, "%s@%s" % (role, project), by, project)
        text += "\n\n(addressed to role %s@%s -- posting a plan takes it)" % (role, project)
    for w in who:
        enqueue(conn, w, pid, "task", text + _liaison_note(conn, t, w), tid)
    bus.bump()
    return task_dict(conn, get_task(conn, tid), full=True)


def _set_state(conn, tid, state):
    closed = now() if state in ("done", "cancelled") else None
    conn.execute("UPDATE tasks SET state=?, updated_at=?, closed_at=? WHERE id=?", (state, now(), closed, tid))


def update(conn, tid, by, kind, body, to=""):
    if kind not in UPDATE_KINDS:
        raise HubError(400, "kind must be one of %s (closing is hub_close)" % ", ".join(UPDATE_KINDS))
    t = get_task(conn, tid)
    tid = t["id"]
    if t["state"] in ("done", "cancelled") and kind != "comment":
        raise HubError(409, "task #%d is %s" % (tid, t["state"]))
    if not t["owner"] and by != t["created_by"] and kind in ("plan", "progress", "result"):
        conn.execute("UPDATE tasks SET owner=? WHERE id=?", (by, tid))
        t = get_task(conn, tid)
    state = UPDATE_KINDS[kind]
    if kind == "result":
        conn.execute("UPDATE tasks SET result=? WHERE id=?", (body, tid))
    creator_side = by in (t["created_by"], _party(conn, t, t["created_by"]))
    if creator_side and kind in ("plan", "progress"):
        state = None  # the delegator writing a plan does not start the task
    if creator_side and kind in ("answer", "comment") and t["state"] == "review":
        state = "working"  # sent back for rework
    if state and state != t["state"]:
        _set_state(conn, tid, state)
    else:
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), tid))
    t = get_task(conn, tid)
    pid = add_post(conn, by, kind, body, to or "", tid, t["project"])
    head = "[agent-hub] Task #%d %r -- %s posted %s:" % (tid, t["title"], by, kind)
    tail = ""
    if kind == "result":
        tail = ("\n\nReview it. Accept with hub_close(task_id=%d, state=\"done\"), or send it back with "
                "hub_update(task_id=%d, kind=\"comment\", body=...)." % (tid, tid))
    elif kind == "question":
        tail = "\n\nAnswer with hub_update(task_id=%d, kind=\"answer\", body=...)." % tid
    elif creator_side and state == "working" and kind in ("answer", "comment"):
        tail = "\n\nTask is back to working -- continue, then post a new result."
    who = _others(conn, t, by)
    if to:
        for w in recipients(conn, to, by, t["project"]):
            if w not in who and w != by:
                who.append(w)
    li = liaison(conn, t["project"])
    for w in who:
        # The liaison stands in for the human; the human's phone should not ring for every progress note.
        # Progress stays in the thread; plans, questions, answers, results and anything sent to="human" go through.
        if (kind == "progress" and w == li and to != HUMAN and w not in (t["created_by"], t["owner"])):
            continue
        enqueue(conn, w, pid, kind, "%s\n%s%s%s" % (head, body, tail, _liaison_note(conn, t, w, to)), tid)
    bus.bump()
    return task_dict(conn, t, full=True)


def close(conn, tid, by, state, note=""):
    if state not in ("done", "cancelled", "open"):
        raise HubError(400, "state: done | cancelled | open")
    t = get_task(conn, tid)
    tid = t["id"]
    _set_state(conn, tid, state if state != "open" else ("working" if t["owner"] else "open"))
    pid = add_post(conn, by, "closed" if state != "open" else "reopened", note or state, "", tid, t["project"])
    for w in _others(conn, t, by):
        enqueue(conn, w, pid, "closed", "[agent-hub] Task #%d %r marked %s by %s.%s"
                % (tid, t["title"], state, by, ("\n" + note) if note else ""), tid)
    bus.bump()
    return task_dict(conn, get_task(conn, tid), full=True)


# ------------------------------------------------------------------ dashboard + upkeep

def overview(conn):
    projects = []
    for p in conn.execute("SELECT * FROM projects WHERE archived=0 ORDER BY name").fetchall():
        counts = {s: 0 for s in TASK_STATES}
        for r in conn.execute("SELECT state, COUNT(*) n FROM tasks WHERE project=? GROUP BY state", (p["name"],)):
            counts[r["state"]] = r["n"]
        projects.append(dict(p, agents=list_agents(conn, p["name"]), tasks=counts))
    loose = [a for a in list_agents(conn) if not a["scopes"]]
    archived = conn.execute("SELECT COUNT(*) FROM agents WHERE hidden=1").fetchone()[0]
    return {"projects": projects, "unassigned": loose, "archived": archived, "attention": attention(conn),
            "version": bus.version()}


def attention(conn):
    """What needs a person now: results to review, questions nobody answered, deliveries stuck in a queue."""
    live = "(SELECT name FROM projects WHERE archived=0)"
    review = [dict(r) for r in conn.execute(
        "SELECT id, title, project, owner, created_by, updated_at FROM tasks WHERE state='review' AND "
        "(project='' OR project IN %s) ORDER BY updated_at" % live)]
    questions = [dict(r) for r in conn.execute(
        "SELECT t.id, t.title, t.project, t.owner, t.created_by, p.sender AS asked_by, p.recipient, p.body, p.ts "
        "FROM tasks t JOIN posts p ON p.id = (SELECT MAX(id) FROM posts WHERE task_id=t.id) "
        "WHERE t.state IN ('open','working') AND p.kind='question' AND (t.project='' OR t.project IN %s) "
        "ORDER BY p.ts" % live)]
    stale = []
    for r in conn.execute("SELECT DISTINCT i.agent FROM inbox i JOIN agents a ON a.name=i.agent WHERE a.hidden=0 AND "
                          "i.delivered_at IS NULL AND i.created_at < ?", (ago_ts(STALE_AFTER),)).fetchall():
        c = agent_card(conn, agent_row(conn, r["agent"]))
        stale.append({k: c[k] for k in ("name", "kind", "node", "presence", "push", "stale_queued", "oldest_queued_s")})
    stale.sort(key=lambda x: -x["oldest_queued_s"])
    return {"review": review, "questions": questions, "stale": stale}


def maintain(conn):
    """Every ~30s: end sessions whose heartbeat stopped, so their names free up and their leases lapse."""
    stale = [r["id"] for r in conn.execute("SELECT id FROM sessions WHERE ended_at IS NULL AND last_seen < ?",
                                           (ago_ts(LIVE_AFTER),))]
    for sid in stale:
        conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (now(), sid))
        conn.execute("UPDATE inbox SET sent_at=NULL, session='' WHERE session=? AND delivered_at IS NULL", (sid,))
    if stale:
        bus.bump()
    return len(stale)
