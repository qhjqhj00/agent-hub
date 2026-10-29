"""The team commands: `agenthub delegate`, `tasks`, `update`, `vault`, ...

The same operations as the hub_* MCP tools, for shells, scripts, and agents
whose only tool is a shell. Identity: --agent > $HUB_AGENT > the `agenthub run`
wrapper this shell runs under > this Claude session's binding > nobody (you
act as the human).
"""
import os
import sys
import urllib.parse

from . import client


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def call(method, path, body=None, timeout=70):
    try:
        return client.call(method, path, body, timeout)
    except (client.HubRefused, client.HubUnavailable) as e:
        die(str(e))


def session():
    w = client.wrapper_info()
    if w:
        return w["session"], w
    return os.environ.get("CLAUDE_CODE_SESSION_ID", ""), None


def me(args, required=True):
    name = getattr(args, "agent", None) or os.environ.get("HUB_AGENT")
    if not name:
        sid, w = session()
        if w and w.get("agent"):
            name = w["agent"]
        elif sid:
            try:
                name = client.call("GET", "/v1/sessions/" + sid, timeout=10).get("agent") or ""
            except Exception:
                name = ""
    if not name and required:
        die("no identity: pass --agent NAME, set HUB_AGENT, or `agenthub register NAME` inside an agent session")
    return name or ""


def fmt_task(t):
    return "#%-4d %-11s %s  (%s -> %s)%s" % (t["id"], t["state"], t["title"], t["created_by"],
                                             t["owner"] or "%s@%s" % (t["role"], t["project"]),
                                             "  [%s]" % t["project"] if t.get("project") else "")


def c_whoami(a):
    n = me(a, required=False)
    if not n:
        print("(no identity -- acting as human)")
        return
    x = call("GET", "/v1/agents/" + n)
    print("%s  %s  presence=%s channel=%s pending=%d" % (x["name"], x["home"], x["presence"], x["channel"], x["pending"]))


def c_register(a):
    sid, w = session()
    body = {"name": a.name, "session": sid, "cwd": (w or {}).get("cwd") or os.getcwd(),
            "kind": (w or {}).get("kind") or os.environ.get("HUB_KIND") or ""}
    if a.project:
        body.update(project=a.project, role=a.role or "")
    if a.description:
        body["description"] = a.description
    x = call("POST", "/v1/agents/register", body)["agent"]
    print("registered %s (%s)" % (x["name"], x["home"]))


def c_agents(a):
    q = []
    if a.project:
        q.append("project=" + a.project)
    if a.node:
        q.append("node=" + a.node)
    if a.live:
        q.append("live=1")
    for x in call("GET", "/v1/agents?" + "&".join(q))["agents"]:
        print("%-24s %-8s %-5s %-45s %s%s" % (x["name"], x.get("kind", ""), x["presence"], x["home"][-45:],
                                         ",".join("%s/%s" % (s["project"], s["role"]) for s in x["scopes"]) or "-",
                                         "  #%d %s" % (x["current_task"]["id"], x["current_task"]["title"][:40])
                                         if x["current_task"] else ""))


def c_nodes(a):
    if a.action == "add":
        if not a.name:
            die("usage: agenthub nodes add NAME")
        out = call("POST", "/v1/admin/nodes", {"name": a.name})
        url = client.url()
        print("node token for %s (shown once):\n  %s\n\non %s run:\n  curl -fsSL %s/install.sh | sh -s -- --join %s %s"
              % (out["name"], out["token"], out["name"], url, url, out["token"]))
        return
    if a.action == "revoke":
        print(call("DELETE", "/v1/admin/nodes/" + (a.name or ""))["revoked"], "revoked")
        return
    if a.action == "tokens":
        for n in call("GET", "/v1/admin/nodes")["nodes"]:
            print("%-22s issued %s  last used %s" % (n["name"], n["created_at"][:16], (n["last_used"] or "never")[:16]))
        return
    for n in call("GET", "/v1/nodes")["nodes"]:
        ags = n["info"].get("agents") or ({"claude": n["info"]["claude"]} if n["info"].get("claude") else {})
        print("%-22s hubd=%-5s %-34s agents=%d tmux=%s" % (
            n["name"], n["hubd"], " ".join("%s:%s" % kv for kv in ags.items()) or "-", len(n["agents"]),
            ",".join(n["info"].get("tmux", []))))


def c_tasks(a):
    q = ["open=1"] if not a.all else []
    if a.mine:
        q.append("mine=" + me(a))
    if a.project:
        q.append("project=" + a.project)
    for t in call("GET", "/v1/tasks?" + "&".join(q))["tasks"]:
        print(fmt_task(t))


def c_task(a):
    t = call("GET", "/v1/tasks/%d" % a.id)
    print(fmt_task(t))
    for k in ("goal", "context", "acceptance", "plan", "result"):
        if t.get(k):
            print("\n%s:\n%s" % (k.upper(), t[k]))
    if t["log"]:
        print("\nTHREAD:")
        for e in t["log"]:
            print("  %s %-16s %-10s %s" % (e["ts"][5:16], e["agent"], e["kind"], e["body"].replace("\n", "\n" + " " * 42)))


def _text(v):
    return sys.stdin.read() if v == "-" else (v or "")


def c_delegate(a):
    t = call("POST", "/v1/tasks", {"agent": me(a, required=False), "to": a.to, "title": a.title,
                                   "goal": _text(a.goal), "context": _text(a.context), "acceptance": a.acceptance or "",
                                   "project": a.project or "", "parent_id": a.parent})
    print("task #%d -> %s" % (t["id"], a.to))


def c_update(a):
    t = call("POST", "/v1/tasks/%d" % a.id, {"agent": me(a, required=False), "kind": a.kind, "body": _text(a.body)})
    print("#%d now %s" % (t["id"], t["state"]))


def c_claim(a):
    call("POST", "/v1/tasks/%d" % a.id, {"agent": me(a), "claim": True})
    print("claimed #%d" % a.id)


def c_close(a):
    t = call("POST", "/v1/tasks/%d" % a.id, {"agent": me(a, required=False), "state": a.state, "body": a.note or ""})
    print("#%d now %s" % (t["id"], t["state"]))


def c_send(a):
    out = call("POST", "/v1/messages", {"agent": me(a, required=False), "to": a.to, "body": _text(a.body)})
    print("sent #%s to %s" % (out["id"], ", ".join(out["to"])))


def c_reply(a):
    out = call("POST", "/v1/messages", {"agent": me(a, required=False), "reply_to": a.id, "body": _text(a.body)})
    print("replied #%s" % out["id"])


def c_inbox(a):
    items = call("GET", "/v1/inbox?agent=%s%s" % (me(a), "&peek=1" if a.peek else ""))["items"]
    print("\n---\n".join(i["content"] for i in items) or "inbox empty")


def c_post(a):
    out = call("POST", "/v1/events", {"agent": me(a, required=False), "title": a.title, "body": _text(a.body),
                                      "kind": a.kind, "tags": a.tags.split(",") if a.tags else [],
                                      "project": a.project or "", "task_id": a.task})
    print("event %s%s" % (out.get("id"), "  WARNING: " + out["warning"] if out.get("warning") else ""))


def c_read(a):
    q = []
    for k in ("agent_name", "kind", "project"):
        v = getattr(a, k)
        if v:
            q.append("%s=%s" % ("agent" if k == "agent_name" else k, v))
    q.append("limit=%d" % a.limit)
    for e in reversed(call("GET", "/v1/events?" + "&".join(q))["events"]):
        print("%s %-18s %-8s %s" % (e["ts"][5:16], e["agent"], e["kind"], e["title"]))


def c_search(a):
    out = call("GET", "/v1/search?q=" + urllib.parse.quote(a.q))
    for t in out["tasks"]:
        print(fmt_task(t) if "created_by" in t else t)
    for e in out["events"]:
        print("event %-5d %-18s %s" % (e["id"], e["agent"], e["title"]))


def c_scope(a):
    out = call("POST", "/v1/agents/%s/scopes" % me(a), {"add": a.add or [], "remove": a.remove})
    print(", ".join("%s/%s" % (s["project"], s["role"]) for s in out["scopes"]) or "no scopes")


def c_shared(a):
    out = call("GET", "/v1/shared")
    print("SECRETS")
    for r in out["vault"]:
        print("  %-26s %-14s v%-3d %s%s" % (r["name"], ",".join(r["tags"]) or "-", r["version"],
                                           (r["note"].splitlines() or [""])[0][:70],
                                           "  env:" + ",".join(r["env"]) if r["env"] else ""))
    print("NOTES")
    for n in out["notes"]:
        print("  %-26s %-14s %s" % (n["name"], ",".join(n["tags"]) or "-", n["title"]))


c_vault = c_shared


def c_env(a):
    out = call("GET", "/v1/vault/%s/env?agent=%s%s" % (a.name, me(a, required=False),
                                                       "&format=dotenv" if a.dotenv else ""))
    print("\n".join(out["lines"]))


def c_note(a):
    if a.action == "list":
        return c_shared(a)
    if a.action == "get":
        n = call("GET", "/v1/notes/" + a.name)
        print("%s -- %s (v%d)%s\n\n%s" % (n["name"], n["title"], n["version"],
                                          "\nuses: " + ", ".join(n["refs"]) if n["refs"] else "", n["body"]))
        return
    body = {"agent": me(a, required=False)}
    if a.title:
        body["title"] = a.title
    if a.body is not None:
        body["body"] = _text(a.body)
    if a.tags is not None:
        body["tags"] = a.tags
    if a.refs is not None:
        body["refs"] = a.refs
    out = call("POST", "/v1/notes/" + a.name, body)
    print("%s v%d" % (out["name"], out["version"]))


def c_get(a):
    r = call("GET", "/v1/vault/%s?agent=%s" % (a.name, me(a, required=False)))
    print(r["note"] if a.note else r["value"], end="" if not a.note else "\n")


def c_put(a):
    body = {"agent": me(a, required=False), "value": _text(a.value)}
    for k in ("note", "project", "tags"):
        if getattr(a, k) is not None:
            body[k] = getattr(a, k)
    if a.env:
        body["env"] = "\n".join(a.env)
    out = call("POST", "/v1/vault/" + a.name, body)
    print("%s v%d" % (out["name"], out["version"]))


def c_wake(a):
    print(call("POST", "/v1/agents/%s/wake" % a.name, {"prompt": a.prompt or ""}))


def c_stop(a):
    print(call("POST", "/v1/agents/%s/stop" % a.name, {}))


def c_skill(a):
    if a.action == "list":
        out = call("GET", "/v1/skills")
        for s in out["skills"]:
            print("%-12s %-24s %s" % (s["plugin"], s["skill"], s["description"][:80]))
        return
    path = os.path.abspath(os.path.expanduser(a.path or "."))
    if not os.path.isfile(os.path.join(path, "SKILL.md")):
        die("%s has no SKILL.md" % path)
    files = {}
    for root, _, fs in os.walk(path):
        for f in fs:
            p = os.path.join(root, f)
            if "/." in p[len(path):] or os.path.getsize(p) > 512 * 1024:
                continue
            try:
                files[os.path.relpath(p, path)] = open(p).read()
            except UnicodeDecodeError:
                print("skip binary %s" % p, file=sys.stderr)
    out = call("POST", "/v1/skills", {"agent": me(a, required=False), "name": a.name or os.path.basename(path),
                                      "files": files})
    print("published %s -> %s v%s" % (out["skill"], out["plugin"], out["version"]))


def c_status(a):
    o = call("GET", "/v1/overview")
    print("live agents: %d/%d   tasks: %s" % (len(o["live"]), o["agent_total"],
                                              " ".join("%s=%d" % kv for kv in sorted(o["task_counts"].items()))))
    for it in o["attention"]:
        print("! %-11s #%-4s %s -- %s" % (it["type"], it["id"], it["title"], (it["body"] or "")[:80].replace("\n", " ")))




KINDS = ["plan", "progress", "question", "answer", "comment", "blocked", "needs_human", "result"]


def register(add_):
    import argparse

    def add(*a, **kw):
        p = add_(*a, **kw)
        p.add_argument("--agent", default=argparse.SUPPRESS, help="act as this agent")
        return p

    add("whoami", c_whoami, help="this shell's identity")
    add("status", c_status, help="what needs attention")
    add("register", c_register, (["name"],), (["--project"],), (["--role"],), (["--description"],), help="claim a name")
    add("agents", c_agents, (["--project"],), (["--node"],), (["--live"], {"action": "store_true"}), help="list agents")
    add("nodes", c_nodes, (["action"], {"nargs": "?", "choices": ["list", "add", "revoke", "tokens"], "default": "list"}),
        (["name"], {"nargs": "?"}), help="list nodes; add NAME issues a join token (admin)")
    add("tasks", c_tasks, (["--mine"], {"action": "store_true"}), (["--project"],), (["--all"], {"action": "store_true"}),
        help="list tasks")
    add("task", c_task, (["id"], {"type": int}), help="a task and its thread")
    add("delegate", c_delegate, (["to"],), (["title"],), (["--goal"],), (["--context"],), (["--acceptance"],),
        (["--project"],), (["--parent"], {"type": int}), help="hand a task to an agent or role@project")
    add("update", c_update, (["id"], {"type": int}), (["kind"], {"choices": KINDS}),
        (["body"],), help="write into a task thread ('-' reads stdin)")
    add("claim", c_claim, (["id"], {"type": int}), help="take an unowned task")
    add("close", c_close, (["id"], {"type": int}), (["state"], {"choices": ["done", "dropped", "open"]}), (["--note"],),
        help="accept (done), drop or reopen a task")
    add("send", c_send, (["to"],), (["body"],), help="direct message")
    add("reply", c_reply, (["id"], {"type": int}), (["body"],), help="answer a message")
    add("inbox", c_inbox, (["--peek"], {"action": "store_true"}), help="items queued for you")
    add("post", c_post, (["title"],), (["--body"],), (["--kind"], {"default": "note"}), (["--tags"],), (["--project"],),
        (["--task"], {"type": int}), help="post to the team feed")
    add("read", c_read, (["agent_name"], {"nargs": "?"}), (["--kind"],), (["--project"],),
        (["--limit"], {"type": int, "default": 30}), help="read the team feed")
    add("search", c_search, (["q"],), help="search tasks and the feed")
    add("scope", c_scope, (["--add"], {"action": "append"}), (["--remove"],), help="project/role pairs you serve")
    add("shared", c_shared, help="the shared board: secrets (no values) and notes")
    add("vault", c_vault, help="same as shared")
    add("env", c_env, (["name"],), (["--dotenv"], {"action": "store_true"}),
        help="export lines for a secret's env mapping: eval \"$(agenthub env NAME)\"")
    add("note", c_note, (["action"], {"choices": ["get", "put", "list"]}), (["name"], {"nargs": "?"}), (["--title"],),
        (["--body"],), (["--tags"],), (["--refs"],), help="shared how-to notes ('-' body reads stdin)")
    add("get", c_get, (["name"],), (["--note"], {"action": "store_true"}), help="a secret's value")
    add("put", c_put, (["name"],), (["value"],), (["--note"],), (["--project"],), (["--tags"],),
        (["--env"], {"action": "append", "help": "VAR=json_key (repeatable); VAR= for the whole value"}),
        help="store/rotate a secret ('-' reads stdin)")
    add("wake", c_wake, (["name"],), (["--prompt"],), help="start an agent on its node")
    add("stop", c_stop, (["name"],), help="stop an agent")
    add("skill", c_skill, (["action"], {"choices": ["publish", "list"]}), (["path"], {"nargs": "?"}), (["--name"],),
        help="publish a skill dir to every node / list skills")
