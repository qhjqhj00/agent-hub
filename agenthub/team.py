"""The team commands: `agenthub delegate`, `tasks`, `update`, `board`, ...

The same operations as the hub_* MCP tools, for shells, scripts, agents whose only
tool is a shell, and the human on the hub machine.

Identity: `agenthub register NAME` opens a hub session and writes
~/.agent-hub/sessions/NAME.json; later commands act as that session when given
--agent NAME or $HUB_AGENT. Without one you act as the human, which needs the
admin token in ~/.agent-hub/config.json (the hub machine).
"""
import json
import os
import sys
import urllib.parse

from . import client

UPDATE_KINDS = ["plan", "progress", "question", "answer", "comment", "result"]


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def _name(a):
    return getattr(a, "agent", None) or os.environ.get("HUB_AGENT") or ""


def _register(name, kind=None, project="", role="", description=""):
    body = {"name": name, "kind": kind or os.environ.get("HUB_KIND") or "other", "cwd": os.getcwd(),
            "conversation": "cli:" + name, "push": "pull"}
    for k, v in (("project", project), ("role", role), ("description", description)):
        if v:
            body[k] = v
    out = client.call("POST", "/v1/sessions", body)
    sess = {"hub": client.url(), "session_id": out["session_id"], "token": out["token"], "name": name,
            "kind": body["kind"], "cwd": body["cwd"], "conversation": body["conversation"], "push": "pull", "meta": {}}
    client.write_session(name, sess)
    return out, sess


def call(a, method, path, body=None, timeout=70):
    """As the --agent / $HUB_AGENT session if there is one, else as the human (admin token)."""
    name = _name(a)
    try:
        if not name:
            return client.call(method, path, body, timeout)
        sess = client.read_session(name)
        if not sess:
            die("no session for %s here: run `agenthub register %s` first" % (name, name))
        try:
            return client.session_call(sess, method, path, body, timeout)
        except client.HubRefused as e:
            if e.code != 401 or not sess.get("conversation", "").startswith("cli:"):
                raise
            _, sess = _register(name, sess.get("kind"))  # a CLI session lapsed between commands: renew it
            return client.session_call(sess, method, path, body, timeout)
    except (client.HubRefused, client.HubUnavailable) as e:
        die(str(e))


def _text(v):
    """'-' reads the value from stdin."""
    return sys.stdin.read() if v == "-" else (v or "")


def fmt_task(t):
    return "#%-4d %-9s %s  (%s -> %s)%s" % (t["id"], t["state"], t["title"], t["created_by"],
                                           t["owner"] or "%s@%s" % (t["role"], t["project"]),
                                           "  [%s]" % t["project"] if t.get("project") else "")


def c_register(a):
    try:
        out, _ = _register(a.name, a.kind, a.project or "", a.role or "", a.description or "")
    except (client.HubRefused, client.HubUnavailable) as e:
        die(str(e))
    print("registered %s (session %s), %d queued; use --agent %s or HUB_AGENT=%s"
          % (a.name, out["session_id"], out["queued"], a.name, a.name))


def c_whoami(a):
    w = call(a, "GET", "/v1/whoami")
    if not w.get("agent"):
        print("(no agent session: acting as %s on node %s)" % (w.get("role"), w.get("node")))
        return
    c = w["agent"]
    print("%s  %s %s:%s  push=%s pending=%d" % (c["name"], c["kind"], c["node"], c["workspace"],
                                               w["session"]["push"], c["pending"]))


def c_agents(a):
    q = "?project=" + urllib.parse.quote(a.project) if a.project else ""
    for r in call(a, "GET", "/v1/agents" + q)["agents"]:
        if a.live and r["presence"] != "live":
            continue
        print("%-20s %-6s %-7s %-4s %s:%s  %s%s" % (
            r["name"], r["kind"], r["presence"], r["state"] or "-", r["node"], r["workspace"],
            ",".join("%s/%s" % (s["project"], s["role"]) for s in r["scopes"]) or "-",
            "  liaison:" + ",".join(r["liaison_of"]) if r["liaison_of"] else ""))


def c_start(a):
    print(call(a, "GET", "/v1/agents/" + urllib.parse.quote(a.name))["start"])


def c_tasks(a):
    q = {"state": a.state or "active"}
    for k in ("project", "owner"):
        if getattr(a, k):
            q[k] = getattr(a, k)
    if a.q:
        q["q"] = a.q
    for t in call(a, "GET", "/v1/tasks?" + urllib.parse.urlencode(q))["tasks"]:
        print(fmt_task(t))


def c_task(a):
    out = call(a, "GET", "/v1/tasks/%d" % a.id)
    t = out["task"]
    print(fmt_task(t))
    for k in ("goal", "context", "acceptance", "result"):
        if t.get(k):
            print("\n%s:\n%s" % (k.upper(), t[k]))
    print("\nTHREAD:")
    for p in out["posts"]:
        print("  %s %s %s: %s" % (p["ts"][5:16], p["sender"], p["kind"], p["body"]))


def c_delegate(a):
    t = call(a, "POST", "/v1/tasks", {"to": a.to, "title": a.title, "goal": _text(a.goal),
                                      "context": _text(a.context), "acceptance": _text(a.acceptance),
                                      "project": a.project or "", "parent_id": a.parent})["task"]
    print("task #%d -> %s" % (t["id"], a.to))


def c_update(a):
    t = call(a, "POST", "/v1/tasks/%d" % a.id, {"kind": a.kind, "body": _text(a.body), "to": a.to or ""})["task"]
    print("#%d %s" % (t["id"], t["state"]))


def c_close(a):
    t = call(a, "POST", "/v1/tasks/%d" % a.id, {"kind": "close", "state": a.state, "body": a.note or ""})["task"]
    print("#%d %s" % (t["id"], t["state"]))


def c_send(a):
    out = call(a, "POST", "/v1/posts", {"to": a.to, "body": _text(a.body)})
    print("msg_id=%d -> %s" % (out["post_id"], ", ".join(out["delivered_to"]) or "(saved, nobody to deliver to)"))


def c_reply(a):
    out = call(a, "POST", "/v1/posts", {"reply_to": a.id, "body": _text(a.body)})
    print("msg_id=%d -> %s" % (out["post_id"], ", ".join(out["delivered_to"])))


def c_inbox(a):
    items = call(a, "GET", "/v1/inbox")["items"]
    print("\n\n---\n\n".join(i["text"] for i in items) or "inbox empty")


def c_board(a):
    out = call(a, "GET", "/v1/board")
    print("SECRETS")
    for v in out["secrets"]:
        print("  %-26s %s%s" % (v["name"], (v["note"] or "").splitlines()[0][:90] if v["note"] else "",
                                "  (env: %s)" % ",".join(v["env_keys"]) if v["env_keys"] else ""))
    print("NOTES")
    for n in out["notes"]:
        print("  %-26s %s" % (n["name"], n["title"]))


def c_env(a):
    fmt = "dotenv" if a.dotenv else "export"
    print("\n".join(call(a, "GET", "/v1/board/secret/%s/env?format=%s" % (a.name, fmt))["lines"]))


def c_note(a):
    if a.body is None and a.title is None:
        n = call(a, "GET", "/v1/board/note/" + a.name)
        print("%s -- %s (v%d)\n\n%s" % (n["name"], n["title"], n["version"], n["body"]))
        return
    body = {}
    if a.title is not None:
        body["title"] = a.title
    if a.body is not None:
        body["body"] = _text(a.body)
    out = call(a, "POST", "/v1/board/note/" + a.name, body)
    print("note %s v%d" % (out["name"], out["version"]))


def c_put(a):
    body = {"value": _text(a.value)}
    if a.note:
        body["note"] = a.note
    if a.env:
        body["env"] = a.env
    out = call(a, "POST", "/v1/board/secret/" + a.name, body)
    print("secret %s v%d" % (out["name"], out["version"]))


# ---------------------------------------------------------------- the human's admin commands

def c_project(a):
    body = {}
    for k in ("liaison", "label", "description"):
        if getattr(a, k) is not None:
            body[k] = getattr(a, k)
    if a.archive:
        body["archived"] = True
    print(json.dumps(call(a, "POST", "/v1/admin/projects/" + a.name, body), ensure_ascii=False, indent=1))


def c_agent_set(a):
    body = {}
    if a.kind:
        body["kind"] = a.kind
    if a.archive or a.restore:
        body["archived"] = bool(a.archive)
    if a.scope:
        body["add_scopes"] = a.scope
    c = call(a, "POST", "/v1/admin/agents/" + a.name, body)
    print("%s kind=%s archived=%s scopes=%s" % (c["name"], c["kind"], c["archived"],
                                             ",".join("%s/%s" % (s["project"], s["role"]) for s in c["scopes"])))


def c_nodes(a):
    if a.action == "add":
        out = call(a, "POST", "/v1/admin/nodes", {"name": a.name})
        u = client.url()
        print("node %s token: %s\non that machine:\n  curl -fsSL %s/install.sh | sh -s -- --join %s %s"
              % (out["name"], out["token"], u, u, out["token"]))
    elif a.action == "revoke":
        print(call(a, "DELETE", "/v1/admin/nodes/" + a.name))
    else:
        for n in call(a, "GET", "/v1/admin/nodes")["nodes"]:
            print("%-20s created %s  last used %s" % (n["name"], n.get("created_at"), n.get("last_used")))


def register(add):
    add("register", c_register, (["name"],), (["--kind"],), (["--project"],), (["--role"],), (["--description"],),
        help="open a hub session as NAME (for shell-only agents)")
    add("whoami", c_whoami, help="this shell's identity")
    add("agents", c_agents, (["--project"],), (["--live"], {"action": "store_true"}), help="list agents")
    add("start", c_start, (["name"],), help="print the command that starts an agent")
    add("tasks", c_tasks, (["--project"],), (["--owner"],), (["--state"],), (["--q"],), help="list tasks")
    add("task", c_task, (["id"], {"type": int}), help="a task and its thread")
    add("delegate", c_delegate, (["to"],), (["title"],), (["--goal"],), (["--context"],), (["--acceptance"],),
        (["--project"],), (["--parent"], {"type": int}), help="hand a task to an agent (- reads stdin)")
    add("update", c_update, (["id"], {"type": int}), (["kind"], {"choices": UPDATE_KINDS}), (["body"],), (["--to"],),
        help="write into a task thread")
    add("close", c_close, (["id"], {"type": int}), (["state"], {"choices": ["done", "cancelled", "open"]}),
        (["--note"],), help="close or reopen a task")
    add("send", c_send, (["to"],), (["body"],), help="direct message (to an agent, role@project, *@project, human)")
    add("reply", c_reply, (["id"], {"type": int}), (["body"],), help="answer a message")
    add("inbox", c_inbox, help="items queued for you")
    add("board", c_board, help="the shared board: secrets (no values) and notes")
    add("env", c_env, (["name"],), (["--dotenv"], {"action": "store_true"}),
        help="a secret as shell exports: eval \"$(agenthub env NAME)\"")
    add("note", c_note, (["name"],), (["--title"],), (["--body"],), help="read (or with --body, write) a note")
    add("put", c_put, (["name"],), (["value"],), (["--note"],), (["--env"], {"type": json.loads}),
        help="store a secret (- reads stdin)")
    add("project", c_project, (["name"],), (["--liaison"],), (["--label"],), (["--description"],),
        (["--archive"], {"action": "store_true"}), help="(admin) set a project's liaison, label, ...")
    add("agent-set", c_agent_set, (["name"],), (["--kind"],), (["--archive"], {"action": "store_true"}),
        (["--restore"], {"action": "store_true"}), (["--scope"], {"action": "append", "help": "project/role"}),
        help="(admin) fix an agent's kind, archive / restore it, add a scope")
    add("nodes", c_nodes, (["action"], {"nargs": "?", "choices": ["list", "add", "revoke"], "default": "list"}),
        (["name"], {"nargs": "?"}), help="(admin) node tokens")
