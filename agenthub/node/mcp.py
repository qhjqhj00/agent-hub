"""`agenthub mcp`: the hub's MCP server (stdio, stdlib only). Every MCP-capable
agent starts it -- Claude Code through the agent-hub plugin, Codex, Gemini CLI
and opencode through the config `agenthub setup` writes. It

  * gives the session its hub tools (register, delegate, update, send, ...)
  * Claude Code with the channel enabled: long-polls the hub and pushes anything
    addressed to this agent into the conversation as <channel source="agent-hub" ...>
    events, and relays permission prompts to the human
  * under `agenthub run` (any other agent): uses the wrapper's session; the wrapper
    does the pushing
  * otherwise: keeps the session present on the hub; queued items are fetched
    with hub_inbox (and shown by the Claude Code prompt hook)
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

from .. import client
from . import instructions as guide_text

KIND = os.environ.get("HUB_KIND") or ("claude" if os.environ.get("CLAUDE_CODE_SESSION_ID") else "generic")
WRAPPED = None
SESSION = os.environ.get("CLAUDE_CODE_SESSION_ID") or "%s-%s" % (KIND, uuid.uuid4().hex[:16])
CWD = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
CHANNEL = False
WANT_NAME = os.environ.get("HUB_AGENT", "")
LOG = os.path.join(client.HOME, "mcp.log")
# The SessionStart hook drops the conversation id here when `/resume` switches this claude to
# another conversation: SESSION (our env) stays the id claude started with.
RUN = client.RUN
CLAUDE_PID = os.getppid()
PROJECTS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")

_out = threading.Lock()
state = {"agent": None, "node": "", "suggest": [], "ready": threading.Event(), "conv": SESSION, "title": ""}


def configure(kind=None):
    """Work out which of the three modes we are in (see the module docstring)."""
    global KIND, WRAPPED, SESSION, CHANNEL, WANT_NAME, CWD
    if kind:
        KIND = kind
    WRAPPED = client.wrapper_info()
    if WRAPPED:
        SESSION = WRAPPED["session"]
        WANT_NAME = WANT_NAME or WRAPPED.get("agent") or ""
        CWD = WRAPPED.get("cwd") or CWD
        KIND = WRAPPED.get("kind") or KIND
    elif KIND != "claude" and not os.environ.get("CLAUDE_CODE_SESSION_ID"):
        SESSION = "%s-%s" % (KIND, uuid.uuid4().hex[:16])
    CHANNEL = KIND == "claude" and os.environ.get("HUB_CHANNEL") == "1" and not WRAPPED
    state["conv"] = SESSION if KIND == "claude" else ""


def log(*a):
    try:
        with open(LOG, "a") as fh:
            fh.write("%s %s %s\n" % (time.strftime("%m-%d %H:%M:%S"), SESSION[:12], " ".join(str(x) for x in a)))
    except Exception:
        pass


def call(method, path, body=None, timeout=20):
    try:
        return client.call(method, path, body, timeout)
    except client.HubRefused as e:
        raise RuntimeError(str(e))


def send(obj):
    with _out:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def git_info():
    def g(*a):
        try:
            return subprocess.run(["git", "-C", CWD] + list(a), capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            return ""
    return {"repo": g("config", "--get", "remote.origin.url") or g("rev-parse", "--show-toplevel"),
            "branch": g("rev-parse", "--abbrev-ref", "HEAD")}


def open_session(agent=None, source=""):
    body = {"session": SESSION, "cwd": CWD, "channel": CHANNEL or bool(WRAPPED), "pid": CLAUDE_PID,
            "agent": WANT_NAME if agent is None else agent, "kind": KIND,
            "conversation": state["conv"], "title": state["title"], "source": source}
    if WRAPPED:
        body.update(pid=WRAPPED.get("pid"), conversation="")
    body.update(git_info())
    try:
        out = call("POST", "/v1/sessions", body, timeout=6)
        state["agent"] = (out.get("agent") or {}).get("name")
        state["node"] = out.get("node", "")
        state["suggest"] = out.get("suggest", [])
        state["shared"] = out.get("shared", "")
        if out.get("error"):
            log("bind failed:", out["error"])
            state["bind_error"] = out["error"]
    except Exception as e:
        log("open failed:", e)
        state["offline"] = str(e)


def instructions():
    who = state.get("agent")
    if state.get("offline"):
        ident = "The hub is unreachable right now (%s); hub tools will fail until it is back." % state["offline"]
    elif who:
        ident = "You are registered as agent **%s** (%s@%s)." % (who, CWD, state["node"])
    else:
        ident = ("You have no hub identity yet. When the user gives you a name (\"your name is X\", \"你叫 X\", "
                 "\"register as X\"), call hub_register(name=X) -- that is all it takes.")
        if state["suggest"]:
            ident += (" Agents previously registered in this workspace: %s. If the user means one of them, "
                      "register under that name to inherit its tasks and history." % ", ".join(state["suggest"]))
        if state.get("bind_error"):
            ident += " (Auto-registration failed: %s)" % state["bind_error"]
    if CHANNEL:
        push = "Hub events are pushed into this session as <channel source=\"agent-hub\" kind=... task_id=... from=...>."
    elif WRAPPED:
        push = "Hub events are typed into this session as messages starting with \"[agent-hub]\"."
    else:
        push = ("This session was not started with `agenthub run %s`, so hub events are NOT pushed to you; "
                "check hub_inbox when the user mentions the hub." % KIND)
    shared = ("\n## On the shared board now\n" + state["shared"]) if state.get("shared") else ""
    return "%s\n%s\n\n%s%s" % (ident, push, guide_text.GUIDE, shared)


# ------------------------------------------------------------------ tools

def S(desc, **props):
    req = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"type": "object", "properties": props, "required": req, "description": desc}


def P(t, d, optional=False, **kw):
    out = {"type": t, "description": d}
    out.update(kw)
    if optional:
        out["optional"] = True
    return out


KINDS = ["plan", "progress", "question", "answer", "comment", "blocked", "needs_human", "result"]
TOOLS = [
    ("hub_register", "Register (or re-claim) this session's identity on the hub. Call when the user tells you your name.",
     S("", name=P("string", "agent name: letters, digits, - _ ."),
       description=P("string", "one line: what you work on", True),
       project=P("string", "project you serve", True), role=P("string", "your role in that project, e.g. backend", True))),
    ("hub_whoami", "Show this session's hub identity, home (workspace@node) and queued items.", S("")),
    ("hub_agents", "List agents: name, workspace@node, roles, presence, current task.",
     S("", project=P("string", "only this project", True), live_only=P("boolean", "only agents with a live session", True))),
    ("hub_delegate", "Hand a task to another agent (or role@project). It is pushed to them (their session is started if needed); their plan, questions and result come back to you.",
     S("", to=P("string", "agent name, or role@project"), title=P("string", "one line"),
       goal=P("string", "what should be achieved"),
       context=P("string", "everything they need: background, decisions, files:lines, constraints, how to test"),
       acceptance=P("string", "what done looks like", True), project=P("string", "project (defaults to yours)", True),
       parent_task=P("integer", "task id this is a subtask of", True))),
    ("hub_tasks", "List tasks. scope: mine (owned by me, created by me, or open for my roles) | project | all.",
     S("", scope=P("string", "mine | project | all", True, enum=["mine", "project", "all"]),
       project=P("string", "project name", True), include_closed=P("boolean", "include done/dropped", True))),
    ("hub_task", "Read a task in full: goal, context, plan, result, and its whole thread.", S("", task_id=P("integer", "task id"))),
    ("hub_update", "Write into a task's thread. kind: plan (before starting) | progress | question | answer | comment | blocked | needs_human (decision for the human) | result (finished: what changed, files, follow-ups).",
     S("", task_id=P("integer", "task id"), kind=P("string", "entry kind", enum=KINDS), body=P("string", "the text"))),
    ("hub_claim", "Take an unowned task (one addressed to a role you hold).", S("", task_id=P("integer", "task id"))),
    ("hub_close", "Close a task you delegated: done (accept the result) | dropped | open (reopen).",
     S("", task_id=P("integer", "task id"), state=P("string", "done | dropped | open", enum=["done", "dropped", "open"]),
       note=P("string", "optional note", True))),
    ("hub_send", "Direct message to an agent, role@project, or 'human'. Use reply_to to answer a message. For task discussion prefer hub_update.",
     S("", body=P("string", "message"), to=P("string", "recipient", True), reply_to=P("integer", "message id being answered", True))),
    ("hub_post", "Broadcast a finding/decision/artifact/note to the team feed (searchable by everyone).",
     S("", title=P("string", "one specific line"), body=P("string", "short body", True),
       kind=P("string", "finding | decision | artifact | note | status", True),
       tags=P("array", "tags", True, items={"type": "string"}), task_id=P("integer", "related task", True),
       project=P("string", "project", True))),
    ("hub_search", "Full-text search across tasks and the team feed.", S("", query=P("string", "search text"))),
    ("hub_inbox", "Fetch items queued for you that were not pushed (sessions without the channel).", S("")),
    ("hub_scope", "Declare which project/role pairs you serve (makes you reachable as role@project).",
     S("", add=P("array", "e.g. [\"myapp/backend\"]", True, items={"type": "string"}),
       remove=P("string", "project/role to drop", True))),
    ("hub_shared", "List the shared board: secrets (names + purpose, no values) and notes (endpoints, how-tos).",
     S("", tag=P("string", "only entries with this tag", True))),
    ("hub_vault", "Shared secrets. action: get (value) | env (shell export lines for its env mapping; pipe to a .env or eval) | put (create/rotate) | list. Values never go in posts/notes/tasks.",
     S("", action=P("string", "get | env | put | list", enum=["get", "env", "put", "list"]), name=P("string", "entry name", True),
       value=P("string", "value (put); JSON object if it has several fields", True),
       note=P("string", "what it is for / how to use it (put)", True),
       tags=P("array", "e.g. [\"llm\"] (put)", True, items={"type": "string"}),
       env=P("object", "env var -> JSON key in the value, \"\" for the whole value, e.g. {\"OPENAI_API_KEY\": \"api_key\"} (put)", True),
       project=P("string", "limit to a project; empty = everyone (put)", True))),
    ("hub_note", "Shared non-secret notes: endpoints, model lists, conventions, how to use a service. action: get | put | list. put edits in place (history is kept).",
     S("", action=P("string", "get | put | list", enum=["get", "put", "list"]), name=P("string", "note name, e.g. relay-howto", True),
       title=P("string", "one line (put)", True), body=P("string", "the note (put)", True),
       tags=P("array", "tags (put)", True, items={"type": "string"}),
       refs=P("array", "vault names this note relies on (put)", True, items={"type": "string"}),
       project=P("string", "limit to a project; empty = everyone (put)", True))),
    ("hub_skill_publish", "Publish a local skill directory (with SKILL.md) to the team marketplace so every node gets it.",
     S("", path=P("string", "path to the skill directory"))),
    ("hub_skills", "List skills available in the team marketplace.", S("")),
]


def me():
    if not state.get("agent"):
        raise RuntimeError("this session has no hub identity yet -- call hub_register(name=...) first")
    return state["agent"]


def fmt_task(t, full=False):
    head = "#%d [%s] %s  (%s -> %s%s)" % (t["id"], t["state"], t["title"], t["created_by"],
                                         t["owner"] or "%s@%s" % (t["role"], t["project"]),
                                         ", project %s" % t["project"] if t.get("project") else "")
    if not full:
        return head
    parts = [head]
    for k in ("goal", "context", "acceptance", "plan", "result"):
        if t.get(k):
            parts.append("%s:\n%s" % (k.upper(), t[k]))
    if t.get("parent"):
        parts.append("PARENT: #%d %s [%s]" % (t["parent"]["id"], t["parent"]["title"], t["parent"]["state"]))
    if t.get("children"):
        parts.append("SUBTASKS:\n" + "\n".join("  #%d [%s] %s -> %s" % (c["id"], c["state"], c["title"], c["owner"] or c["role"])
                                               for c in t["children"]))
    if t.get("log"):
        parts.append("THREAD:\n" + "\n".join("  %s %s %s: %s" % (e["ts"][5:16], e["agent"], e["kind"], e["body"])
                                             for e in t["log"]))
    return "\n\n".join(parts)


def run_tool(name, a):
    if name == "hub_register":
        body = {"name": a["name"], "session": SESSION, "cwd": CWD, "conversation": state["conv"],
                "title": state["title"], "kind": KIND}
        body.update(git_info())
        for k in ("description", "project", "role"):
            if a.get(k):
                body[k] = a[k]
        res = call("POST", "/v1/agents/register", body)
        out = res["agent"]
        state["shared"] = res.get("shared", "")
        state["agent"] = out["name"]
        state.pop("bind_error", None)
        msg = "Registered as %s (%s). Scopes: %s. %d queued item(s)%s." % (
            out["name"], out["home"], ", ".join("%s/%s" % (s["project"], s["role"]) for s in out["scopes"]) or "none",
            out["pending"], " will be pushed now" if (CHANNEL or WRAPPED) and out["pending"] else "")
        if not CHANNEL and not WRAPPED:
            msg += " (Not started with `agenthub run %s`: tasks are not pushed live; use hub_inbox.)" % KIND
        if state.get("shared"):
            msg += "\n\nShared board you can use:\n" + state["shared"]
        return msg
    if name == "hub_whoami":
        if not state.get("agent"):
            return "No identity. Suggested from this workspace: %s" % (", ".join(state["suggest"]) or "none")
        a2 = call("GET", "/v1/agents/" + state["agent"])
        return json.dumps({k: a2.get(k) for k in ("name", "home", "repo", "branch", "description", "scopes",
                                                   "presence", "channel", "current_task", "pending", "wake")},
                          ensure_ascii=False, indent=1) + "\nsession: %s, conversation: %s, channel push: %s" % (
                              SESSION, state["conv"], CHANNEL)
    if name == "hub_agents":
        q = "?project=%s" % a["project"] if a.get("project") else ""
        rows = call("GET", "/v1/agents" + q)["agents"]
        # hidden = retired by the human; still addressable, just not offered
        rows = [r for r in rows if not r.get("hidden") or r["presence"] == "live"]
        if a.get("live_only"):
            rows = [r for r in rows if r["presence"] == "live"]
        return "\n".join("%-22s %-5s %s  roles=%s%s%s" % (
            r["name"], r["presence"], r["home"], ",".join("%s/%s" % (s["project"], s["role"]) for s in r["scopes"]) or "-",
            "  now: #%d %s" % (r["current_task"]["id"], r["current_task"]["title"][:50]) if r["current_task"] else "",
            "  -- " + r["description"] if r.get("description") else "") for r in rows) or "no agents"
    if name == "hub_delegate":
        t = call("POST", "/v1/tasks", {"agent": me(), "to": a["to"], "title": a["title"], "goal": a.get("goal", ""),
                                       "context": a.get("context", ""), "acceptance": a.get("acceptance", ""),
                                       "project": a.get("project", ""), "parent_id": a.get("parent_task")})
        return "Delegated task #%d to %s. Its plan, questions and result will be pushed to you; keep working." % (
            t["id"], a["to"])
    if name == "hub_tasks":
        scope = a.get("scope") or "mine"
        q = []
        if scope == "mine":
            q.append("mine=" + me())
        if a.get("project"):
            q.append("project=" + a["project"])
        if not a.get("include_closed"):
            q.append("open=1")
        rows = call("GET", "/v1/tasks?" + "&".join(q))["tasks"]
        return "\n".join(fmt_task(t) for t in rows) or "no tasks"
    if name == "hub_task":
        return fmt_task(call("GET", "/v1/tasks/%d" % int(a["task_id"])), full=True)
    if name == "hub_update":
        t = call("POST", "/v1/tasks/%d" % int(a["task_id"]), {"agent": me(), "kind": a["kind"], "body": a["body"]})
        return "Posted %s to #%d (state now %s)." % (a["kind"], t["id"], t["state"])
    if name == "hub_claim":
        t = call("POST", "/v1/tasks/%d" % int(a["task_id"]), {"agent": me(), "claim": True})
        return "You own #%d now. Post a plan with hub_update(kind=plan)." % t["id"]
    if name == "hub_close":
        t = call("POST", "/v1/tasks/%d" % int(a["task_id"]), {"agent": me(), "state": a["state"], "body": a.get("note", "")})
        return "#%d is %s." % (t["id"], t["state"])
    if name == "hub_send":
        out = call("POST", "/v1/messages", {"agent": me(), "to": a.get("to", ""), "body": a["body"],
                                            "reply_to": a.get("reply_to")})
        return "Sent (message %s) to %s." % (out.get("id"), ", ".join(out.get("to", [])))
    if name == "hub_post":
        out = call("POST", "/v1/events", {"agent": me(), "title": a["title"], "body": a.get("body", ""),
                                          "kind": a.get("kind") or "note", "tags": a.get("tags") or [],
                                          "task_id": a.get("task_id"), "project": a.get("project", "")})
        return "Posted event %s.%s" % (out.get("id"), " WARNING: " + out["warning"] if out.get("warning") else "")
    if name == "hub_search":
        import urllib.parse
        out = call("GET", "/v1/search?q=" + urllib.parse.quote(a["query"]))
        lines = ["task #%d [%s] %s" % (t["id"], t["state"], t["title"]) for t in out["tasks"]]
        lines += ["event %d %s/%s: %s" % (e["id"], e["agent"], e["kind"], e["title"]) for e in out["events"]]
        return "\n".join(lines) or "nothing found"
    if name == "hub_inbox":
        items = call("GET", "/v1/inbox?agent=" + me())["items"]
        return "\n\n".join(i["content"] for i in items) or "inbox empty"
    if name == "hub_scope":
        out = call("POST", "/v1/agents/%s/scopes" % me(), {"add": a.get("add") or [], "remove": a.get("remove")})
        return "Scopes: " + (", ".join("%s/%s" % (s["project"], s["role"]) for s in out["scopes"]) or "none")
    if name == "hub_shared":
        out = call("GET", "/v1/shared")
        tag = (a.get("tag") or "").lower()
        lines = ["%-26s %-16s v%-3d %s%s" % (v["name"], ",".join(v["tags"]) or "-", v["version"], v["note"].splitlines()[0][:90] if v["note"] else "",
                                            "  env: " + ",".join(v["env"]) if v["env"] else "")
                 for v in out["vault"] if not tag or tag in v["tags"]]
        notes = ["%-26s %-16s %s%s" % (n["name"], ",".join(n["tags"]) or "-", n["title"],
                                       "  (uses " + ",".join(n["refs"]) + ")" if n["refs"] else "")
                 for n in out["notes"] if not tag or tag in n["tags"]]
        return "SECRETS (hub_vault get/env):\n" + ("\n".join(lines) or "  none") + \
            "\n\nNOTES (hub_note get):\n" + ("\n".join(notes) or "  none")
    if name == "hub_vault":
        act = a["action"]
        who = state.get("agent") or ""
        if act == "list":
            return run_tool("hub_shared", {})
        if act == "get":
            return call("GET", "/v1/vault/%s?agent=%s" % (a["name"], who))["value"]
        if act == "env":
            return "\n".join(call("GET", "/v1/vault/%s/env?agent=%s" % (a["name"], who))["lines"])
        body = {"agent": me()}
        for k in ("value", "note", "tags", "env", "project"):
            if a.get(k) is not None:
                body[k] = a[k]
        out = call("POST", "/v1/vault/" + a["name"], body)
        return "Stored %s v%d%s." % (out["name"], out["version"], "" if out["value_changed"] else " (metadata only)")
    if name == "hub_note":
        act = a["action"]
        if act == "list":
            return run_tool("hub_shared", {})
        if act == "get":
            n = call("GET", "/v1/notes/" + a["name"])
            return "%s -- %s (v%d, %s)%s\n\n%s" % (n["name"], n["title"], n["version"], n["owner"],
                                                  "\nuses secrets: " + ", ".join(n["refs"]) if n["refs"] else "", n["body"])
        body = {"agent": me()}
        for k in ("title", "body", "tags", "refs", "project"):
            if a.get(k) is not None:
                body[k] = a[k]
        out = call("POST", "/v1/notes/" + a["name"], body)
        return "Saved note %s v%d." % (out["name"], out["version"])
    if name == "hub_skill_publish":
        path = os.path.abspath(os.path.expanduser(a["path"]))
        files = {}
        for root, _, fs in os.walk(path):
            for f in fs:
                p = os.path.join(root, f)
                if os.path.getsize(p) > 512 * 1024:
                    continue
                try:
                    files[os.path.relpath(p, path)] = open(p).read()
                except UnicodeDecodeError:
                    continue
        out = call("POST", "/v1/skills", {"agent": state.get("agent") or "", "name": os.path.basename(path.rstrip("/")),
                                          "files": files})
        return "Published %s to %s v%s; every node's hubd will update its plugins." % (out["skill"], out["plugin"], out["version"])
    if name == "hub_skills":
        out = call("GET", "/v1/skills")
        return "\n".join("%s/%s: %s" % (s["plugin"], s["skill"], s["description"][:100]) for s in out["skills"])
    raise RuntimeError("unknown tool " + name)


# ------------------------------------------------------------------ push loop

META_KEY = re.compile(r"^[A-Za-z0-9_]+$")


def pull_loop():
    state["ready"].wait(30)
    time.sleep(2)  # let Claude Code finish registering the channel listener
    backoff = 2
    acks = []
    while True:
        try:
            out = call("GET", "/v1/sessions/%s/pull?wait=50&ack=%s" % (SESSION, ",".join(acks)), timeout=70)
            acks = []
            backoff = 2
            if out.get("agent") and out["agent"] != state.get("agent"):
                state["agent"] = out["agent"]  # renamed or bound elsewhere (dashboard)
            for it in out.get("items", []):
                meta = it.get("meta") or {}
                if it["kind"] == "verdict":
                    send({"jsonrpc": "2.0", "method": "notifications/claude/channel/permission",
                          "params": {"request_id": meta.get("request_id"), "behavior": meta.get("behavior")}})
                    continue
                send({"jsonrpc": "2.0", "method": "notifications/claude/channel",
                      "params": {"content": it["content"],
                                 "meta": {k: str(v) for k, v in meta.items() if META_KEY.match(k)}}})
                log("pushed", it["kind"], it.get("task_id"))
            acks = [str(it["id"]) for it in out.get("items", [])]
        except Exception as e:
            msg = str(e)
            log("pull error:", msg)
            if "unknown session" in msg:
                open_session()
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


def heartbeat_loop():
    """Sessions without a channel still show as present; nothing is consumed."""
    while True:
        time.sleep(60)
        try:
            call("POST", "/v1/sessions", {"session": SESSION, "cwd": CWD, "channel": False, "pid": os.getppid(),
                                          "kind": KIND})
        except Exception:
            pass


# ------------------------------------------------------------------ conversation watch

class Transcript:
    """Follows a conversation's .jsonl for its name: `/rename` (custom-title, written once)
    beats Claude's own ai-title (re-written as the conversation goes)."""

    def __init__(self, conv):
        self.conv, self.path, self.pos, self.custom, self.ai = conv, None, 0, "", ""

    def title(self):
        if not self.path:
            import glob
            hits = glob.glob(os.path.join(PROJECTS, "*", self.conv + ".jsonl"))
            if not hits:
                return ""
            self.path = hits[0]
        try:
            size = os.path.getsize(self.path)
            if size < self.pos:
                self.pos, self.custom, self.ai = 0, "", ""
            with open(self.path, "rb") as fh:
                while size > self.pos:  # in chunks: a long conversation is 100MB+
                    fh.seek(self.pos)
                    data = fh.read(min(size - self.pos, 8 << 20))
                    end = data.rfind(b"\n") + 1  # a line still being written waits for the next look
                    if not end:
                        break
                    self.pos += end
                    for line in data[:end].splitlines():
                        if b'"custom-title"' in line or b'"ai-title"' in line:
                            try:
                                d = json.loads(line)
                            except ValueError:
                                continue
                            if d.get("type") == "custom-title":
                                self.custom = d.get("customTitle") or ""
                            elif d.get("type") == "ai-title":
                                self.ai = d.get("aiTitle") or ""
        except OSError:
            pass
        return (self.custom or self.ai).strip()


def notify(text):
    if CHANNEL:
        send({"jsonrpc": "2.0", "method": "notifications/claude/channel",
              "params": {"content": text, "meta": {"kind": "identity"}}})


def switch_conversation(conv, source):
    """`/resume` (or /clear) moved this claude to another conversation: tell the hub, which
    hands us the agent that conversation belongs to -- taking it over from a copy still
    running in another terminal."""
    before = state.get("agent")
    state["conv"], state["title"] = conv, ""
    state["transcript"] = Transcript(conv)  # its title is read by the watch loop
    state.pop("bind_error", None)
    open_session(agent="", source=source)
    log("conversation", conv[:8], source, "->", state.get("agent"), state.get("bind_error") or "")
    if source != "resume":
        return
    if state.get("agent") and state["agent"] != before:
        notify("agent-hub: this session resumed the conversation of agent %s and is now registered on the hub "
               "as %s. If a copy of this conversation was still running in another terminal, its node is "
               "closing it." % (state["agent"], state["agent"]))
    elif state.get("bind_error"):
        notify("agent-hub: resumed a conversation but could not take its hub identity: %s" % state["bind_error"])


def watch_loop():
    conv_file = os.path.join(RUN, "%d.conv" % CLAUDE_PID)
    seen, n, sent = None, -1, None
    while True:
        time.sleep(2)
        n += 1
        try:
            st = os.stat(conv_file)
            if (st.st_mtime, st.st_size) != seen:
                seen = (st.st_mtime, st.st_size)
                d = json.load(open(conv_file))
                if d.get("conversation") and d["conversation"] != state["conv"]:
                    switch_conversation(d["conversation"], d.get("source") or "")
                    n, sent = 0, None
        except (OSError, ValueError):
            pass
        except Exception as e:
            log("watch error:", e)
        if n % 15 == 0:  # at start, after a switch, then every ~30s: has the conversation been (re)named?
            try:
                title = state["transcript"].title()
                if title and title != sent:
                    state["title"] = title
                    call("POST", "/v1/sessions/%s/meta" % SESSION, {"title": title}, timeout=6)
                    sent = title
            except Exception as e:
                log("title:", e)


def mark_running():
    """Let the SessionStart hook (run by the same claude) find us. Also drop marks of dead claudes."""
    try:
        os.makedirs(RUN, exist_ok=True)
        for f in os.listdir(RUN):
            pid = f.split(".")[0]
            if pid.isdigit():
                try:
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    os.remove(os.path.join(RUN, f))
                except OSError:
                    pass
        with open(os.path.join(RUN, "%d.chan" % CLAUDE_PID), "w") as fh:
            fh.write(SESSION)
    except OSError as e:
        log("run dir:", e)


# ------------------------------------------------------------------ MCP loop

def end_session(*_):
    if not WRAPPED:
        try:
            call("POST", "/v1/sessions/%s/end" % SESSION, {}, timeout=3)
        except Exception:
            pass
    os._exit(0)


def main(kind=None):
    import signal
    configure(kind)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, end_session)
    if KIND == "claude":
        mark_running()
    state["transcript"] = Transcript(SESSION)
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        method, mid = m.get("method"), m.get("id")
        try:
            if method == "initialize":
                open_session()
                caps = {"tools": {}}
                if CHANNEL:
                    caps["experimental"] = {"claude/channel": {}, "claude/channel/permission": {}}
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": m["params"].get("protocolVersion", "2025-06-18"),
                    "capabilities": caps, "serverInfo": {"name": "agent-hub", "version": __import__("agenthub").__version__},
                    "instructions": instructions()}})
            elif method == "notifications/initialized":
                state["ready"].set()
                if not WRAPPED:  # under the wrapper, it holds the session
                    threading.Thread(target=pull_loop if CHANNEL else heartbeat_loop, daemon=True).start()
                if KIND == "claude":
                    threading.Thread(target=watch_loop, daemon=True).start()
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                    {"name": n, "description": d, "inputSchema": {k: v for k, v in s.items() if k != "description"}}
                    for n, d, s in TOOLS]}})
            elif method == "tools/call":
                name, args = m["params"]["name"], m["params"].get("arguments") or {}
                try:
                    text, err = run_tool(name, args), False
                except Exception as e:
                    text, err = "error: %s" % e, True
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}],
                                                               "isError": err}})
            elif method == "notifications/claude/channel/permission_request":
                p = m.get("params") or {}
                try:
                    call("POST", "/v1/permissions", {"session": SESSION, "request_id": p.get("request_id"),
                                                     "tool": p.get("tool_name"), "description": p.get("description"),
                                                     "preview": p.get("input_preview")})
                except Exception as e:
                    log("permission relay failed:", e)
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            elif mid is not None:
                if method in ("resources/list", "prompts/list", "resources/templates/list"):
                    key = method.split("/")[0] if "templates" not in method else "resourceTemplates"
                    send({"jsonrpc": "2.0", "id": mid, "result": {key: []}})
                else:
                    send({"jsonrpc": "2.0", "id": mid, "result": {}})
        except Exception as e:
            log("error handling", method, e)
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})
    if not WRAPPED:
        try:
            call("POST", "/v1/sessions/%s/end" % SESSION, {}, timeout=3)
        except Exception:
            pass
