"""`agenthub mcp`: the hub's MCP server (stdio, stdlib only).

Claude Code starts it through the agent-hub plugin; agy and Codex through the config
`agenthub setup` writes. It

  * gives the agent the nine hub tools (docs/refactor/core.md §7)
  * registers the session (`POST /v1/sessions`) when the agent is given a name -- or at
    start when $HUB_AGENT names it -- and writes ~/.agent-hub/sessions/<name>.json so a
    bridge or hook serving the same agent acts as the same session
  * Claude Code with HUB_CHANNEL=1: long-polls the hub and pushes what arrives into the
    conversation as <channel source="agent-hub"> events (push=channel)
  * agy / Codex: push=bridge -- t3-bridge / codex-bridge deliver; this process only beats
  * anything else: push=pull -- the agent calls hub_inbox
  * ends the session when its agent exits (stdin closes), which frees the name at once --
    except an agy session bound to a T3 thread, which the next agy process in that thread adopts
"""
import json
import os
import re
import sys
import tempfile
import threading
import time

from .. import client
from . import instructions as guide_text


def _detect_kind():
    if os.environ.get("HUB_KIND"):
        return os.environ["HUB_KIND"]
    if os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude"
    if os.environ.get("ANTIGRAVITY_CONVERSATION_ID"):
        return "agy"
    return "other"


KIND = _detect_kind()
CWD = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
CHANNEL = False
LOG = os.path.join(client.HOME, "mcp.log")
AGENT_PID = os.getppid()

_out = threading.Lock()
_sess_lock = threading.RLock()
state = {"sess": None, "conv": "", "error": "", "ready": threading.Event(), "loop": None}


def configure(kind=None):
    global KIND, CHANNEL
    if kind:
        KIND = kind
    CHANNEL = KIND == "claude" and os.environ.get("HUB_CHANNEL") == "1"
    state["conv"] = (os.environ.get("CLAUDE_CODE_SESSION_ID") if KIND == "claude" else
                     os.environ.get("ANTIGRAVITY_CONVERSATION_ID") if KIND == "agy" else "") or ""


def push_mode():
    if CHANNEL:
        return "channel"
    return "bridge" if KIND in ("agy", "codex") else "pull"


def log(*a):
    try:
        os.makedirs(client.HOME, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write("%s %s %s\n" % (time.strftime("%m-%d %H:%M:%S"), KIND, " ".join(str(x) for x in a)))
    except Exception:
        pass


def send(obj):
    with _out:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def notify(text, meta=None):
    if CHANNEL:
        send({"jsonrpc": "2.0", "method": "notifications/claude/channel",
              "params": {"content": text, "meta": meta or {}}})


# ------------------------------------------------------------------ session

def me():
    s = state["sess"]
    if not s:
        raise RuntimeError("this session has no hub identity yet -- call hub_register(name=...) first")
    return s


def hcall(method, path, body=None, timeout=20):
    """A request as this session; a session the hub has ended is re-registered once."""
    s = me()
    try:
        return client.session_call(s, method, path, body, timeout)
    except client.HubRefused as e:
        if e.code == 401 and register(s["name"], quiet=True):
            return client.session_call(state["sess"], method, path, body, timeout)
        raise RuntimeError(str(e))


def _t3_thread(conv):
    if KIND != "agy" or not conv:
        return ""
    try:
        from ..bridges.common import resolve_t3_thread
        return resolve_t3_thread(conv) or ""
    except Exception as e:
        log("resolve_t3_thread:", e)
        return ""


def register(name, project="", role="", description="", quiet=False):
    """POST /v1/sessions; on success write the session file and (re)start the push/beat loop."""
    with _sess_lock:
        old = state["sess"]
        if old and old["name"] != name:
            end_session(old)
        meta = {}
        t3 = _t3_thread(state["conv"])
        if t3:
            meta["t3_thread"] = t3
        body = {"name": name, "kind": KIND, "cwd": CWD, "conversation": state["conv"], "pid": AGENT_PID,
                "push": push_mode(), "meta": meta}
        for k, v in (("project", project), ("role", role), ("description", description)):
            if v:
                body[k] = v
        try:
            out = client.call("POST", "/v1/sessions", body, timeout=8)
        except (client.HubRefused, client.HubUnavailable) as e:
            state["error"] = str(e)
            log("register failed:", e)
            if quiet:
                state["sess"] = None
                notify("agent-hub: lost the hub session and could not register %s again: %s" % (name, e),
                       {"kind": "identity"})
                return None
            raise RuntimeError(str(e))
        sess = {"hub": client.url(), "session_id": out["session_id"], "token": out["token"], "name": name,
                "kind": KIND, "cwd": CWD, "conversation": state["conv"], "push": push_mode(), "meta": meta,
                "pid": AGENT_PID}
        client.write_session(name, sess)
        state.update(sess=sess, error="", card=out.get("agent") or {}, queued=out.get("queued", 0))
        log("registered", name, out["session_id"], "push=" + push_mode())
        _start_loop()
        return out


def end_session(s=None):
    s = s or state["sess"]
    if not s:
        return
    try:
        client.session_call(s, "POST", "/v1/sessions/%s/end" % s["session_id"], {}, timeout=3)
    except Exception:
        pass
    client.remove_session(s["name"], s["session_id"])
    if state["sess"] is s:
        state["sess"] = None


def _persistent(s):
    """An agy session bound to a T3 thread outlives this process: T3 reaps an idle `agy acp`
    after ~30 min but the thread lives on, and t3-bridge keeps the session (it ends it once the
    thread is deleted or archived). meta.t3_thread may have been written later by agy-hook."""
    if KIND != "agy" or not s:
        return False
    d = client.read_session(s["name"]) or {}
    return d.get("session_id") == s["session_id"] and bool((d.get("meta") or {}).get("t3_thread"))


def release():
    """This process is exiting (stdin closed or a signal): end the session unless it persists."""
    s = state["sess"]
    if _persistent(s):
        log("exit: keeping", s["name"], s["session_id"], "for its T3 thread")
        state["sess"] = None
        return
    end_session(s)


def adopt():
    """agy woken in a T3 thread starts a fresh MCP server: take over the session the last one
    left behind (same T3 thread / conversation, else the only bound session for this cwd)."""
    if KIND != "agy":
        return None
    conv = state["conv"]
    t3 = _t3_thread(conv)
    bound = [d for d in client.sessions() if d.get("kind") == "agy" and d.get("session_id") and d.get("token")
             and (d.get("meta") or {}).get("t3_thread")]
    hit = [d for d in bound if (t3 and d["meta"]["t3_thread"] == t3) or (conv and d.get("conversation") == conv)]
    if not hit and not t3:  # a thread we can name but nobody holds is a new conversation: register
        hit = [d for d in bound if d.get("cwd") == CWD]
    if len(hit) != 1:
        return None
    d = hit[0]
    try:
        client.session_call(d, "POST", "/v1/sessions/%s/beat" % d["session_id"], {}, timeout=8)
        if conv and d.get("conversation") != conv:
            client.session_call(d, "POST", "/v1/sessions/%s/meta" % d["session_id"], {"conversation": conv}, timeout=8)
            d["conversation"] = conv
    except Exception as e:
        log("adopt", d.get("name"), "failed:", e)
        return None
    d["pid"] = AGENT_PID
    client.write_session(d["name"], d)
    state.update(sess=d, error="")
    log("adopted", d["name"], d["session_id"])
    _start_loop()
    return d


def _start_loop():
    if state["loop"] and state["loop"].is_alive():
        return
    state["loop"] = threading.Thread(target=pull_loop if CHANNEL else beat_loop, daemon=True)
    state["loop"].start()


META_KEY = re.compile(r"^[A-Za-z0-9_]+$")


def pull_loop():
    """Channel push: long-poll, push each item into the conversation, report it delivered."""
    state["ready"].wait(30)
    time.sleep(1)  # let Claude Code finish registering the channel listener
    backoff = 2
    while True:
        s = state["sess"]
        if not s:
            time.sleep(2)
            continue
        try:
            out = hcall("GET", "/v1/sessions/%s/pull?wait=50" % s["session_id"], timeout=70)
            ids = []
            for it in out.get("items", []):
                meta = {"kind": it["kind"], "from": it.get("from") or ""}
                if it.get("task_id"):
                    meta["task_id"] = str(it["task_id"])
                if it["kind"] == "message":
                    meta["msg_id"] = str(it["post_id"])
                send({"jsonrpc": "2.0", "method": "notifications/claude/channel",
                      "params": {"content": it["text"], "meta": {k: v for k, v in meta.items() if META_KEY.match(k)}}})
                ids.append(it["id"])
            if ids:
                hcall("POST", "/v1/sessions/%s/delivered" % state["sess"]["session_id"], {"ids": ids})
                log("pushed", ids)
            backoff = 2
        except Exception as e:
            log("pull error:", e)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


def beat_loop():
    """No channel: keep the session alive; someone else (bridge, hook, hub_inbox) takes the items."""
    while True:
        time.sleep(40)
        s = state["sess"]
        if not s:
            continue
        try:
            hcall("POST", "/v1/sessions/%s/beat" % s["session_id"], {}, timeout=8)
        except Exception as e:
            log("beat error:", e)


def watch_conversation():
    """Claude Code: `/resume` switches conversations under one MCP server; the SessionStart
    hook drops the new id in RUN/<claude pid>.conv. Tell the hub, so the start command resumes it."""
    conv_file = os.path.join(client.RUN, "%d.conv" % AGENT_PID)
    seen = None
    while True:
        time.sleep(2)
        try:
            st = os.stat(conv_file)
            if (st.st_mtime, st.st_size) == seen:
                continue
            seen = (st.st_mtime, st.st_size)
            conv = json.load(open(conv_file)).get("conversation") or ""
            if conv and conv != state["conv"]:
                state["conv"] = conv
                if state["sess"]:
                    hcall("POST", "/v1/sessions/%s/meta" % state["sess"]["session_id"], {"conversation": conv})
                    state["sess"]["conversation"] = conv
                    client.write_session(state["sess"]["name"], state["sess"])
        except (OSError, ValueError):
            pass
        except Exception as e:
            log("watch error:", e)


def mark_running():
    """Let Claude Code's hooks (children of the same claude) find this server. Drop marks of dead claudes."""
    try:
        os.makedirs(client.RUN, exist_ok=True)
        for f in os.listdir(client.RUN):
            pid = f.split(".")[0]
            if pid.isdigit():
                try:
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    os.remove(os.path.join(client.RUN, f))
                except OSError:
                    pass
        with open(os.path.join(client.RUN, "%d.chan" % AGENT_PID), "w") as fh:
            fh.write(json.dumps({"kind": KIND, "channel": CHANNEL}))
    except OSError as e:
        log("run dir:", e)


def _set_chan_name(name):
    if KIND != "claude":
        return
    try:
        with open(os.path.join(client.RUN, "%d.chan" % AGENT_PID), "w") as fh:
            fh.write(json.dumps({"kind": KIND, "channel": CHANNEL, "name": name}))
    except OSError:
        pass


def instructions():
    s = state["sess"]
    if s:
        ident = "You are registered on the hub as agent **%s** (%s, node %s)." % (
            s["name"], CWD, (state.get("card") or {}).get("node", "?"))
    else:
        ident = ("You have no hub identity yet. When the user gives you a name (\"your name is X\", \"你叫 X\", "
                 "\"register as X\"), call hub_register(name=X) -- that is all it takes.")
        if state.get("error"):
            ident += " (Registering as $HUB_AGENT failed: %s)" % state["error"]
    push = {"channel": "Hub events are pushed into this session as <channel source=\"agent-hub\" kind=... from=...>.",
            "bridge": "Hub events are delivered into this conversation by the node's bridge, as messages starting "
                      "with \"[agent-hub]\".",
            "pull": "Hub events are NOT pushed to this session: check hub_inbox when the user mentions the hub, "
                    "and at the start of your work."}[push_mode()]
    return "%s\n%s\n\n%s" % (ident, push, guide_text.GUIDE)


# ------------------------------------------------------------------ tools

def S(**props):
    req = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"type": "object", "properties": props, "required": req}


def P(t, d, optional=False, **kw):
    out = {"type": t, "description": d}
    out.update(kw)
    if optional:
        out["optional"] = True
    return out


UPDATE_KINDS = ["plan", "progress", "question", "answer", "comment", "result"]
TOOLS = [
    ("hub_register", "Register this session on the hub under a name (call when the user tells you your name). "
                     "Without a name: show who you are and what is queued.",
     S(name=P("string", "agent name: letters, digits, - _ .", True),
       project=P("string", "project you serve", True), role=P("string", "your role in it, e.g. backend", True),
       description=P("string", "one line: what you work on", True))),
    ("hub_agents", "List agents: name, kind, node:workspace, roles, presence, busy/idle, current task.",
     S(project=P("string", "only this project", True), live_only=P("boolean", "only agents running now", True))),
    ("hub_send", "Direct message to an agent, role@project, *@project (team post) or 'human' (goes to the "
                 "project's liaison). reply_to answers a message. Task discussion belongs in hub_update.",
     S(body=P("string", "message"), to=P("string", "recipient (optional with reply_to)", True),
       reply_to=P("integer", "msg_id being answered", True))),
    ("hub_delegate", "Hand a task to another agent (or role@project). It is delivered to them when they run; "
                     "their plan, questions and result come back to you.",
     S(to=P("string", "agent name, or role@project"), title=P("string", "one line"),
       goal=P("string", "what should be achieved"),
       context=P("string", "everything they need: background, decisions, files:lines, constraints, how to test"),
       acceptance=P("string", "what done looks like", True), project=P("string", "project (defaults to yours)", True),
       parent_task=P("integer", "task id this is a subtask of", True))),
    ("hub_update", "Write into a task's thread. kind: plan (before starting) | progress | question | answer | "
                   "comment | result (finished: what changed, files, follow-ups). to=\"human\" also sends it to "
                   "the human (via the project's liaison) -- for decisions only they can make.",
     S(task_id=P("integer", "task id"), kind=P("string", "entry kind", enum=UPDATE_KINDS),
       body=P("string", "the text"), to=P("string", "extra recipient, e.g. human", True))),
    ("hub_close", "Close a task: done (accept the result) | cancelled | open (reopen).",
     S(task_id=P("integer", "task id"), state=P("string", "done | cancelled | open", enum=["done", "cancelled", "open"]),
       note=P("string", "optional note", True))),
    ("hub_tasks", "With task_id: the task in full (goal, context, result, whole thread). Without: list tasks "
                  "(default: active ones you own or created).",
     S(task_id=P("integer", "task id", True), scope=P("string", "mine | project | all", True, enum=["mine", "project", "all"]),
       project=P("string", "project", True), state=P("string", "e.g. active, review, done", True),
       query=P("string", "text search", True))),
    ("hub_inbox", "Fetch what is queued for you (sessions without push).", S()),
    ("hub_board", "The shared board. action=list: secrets (names + purpose, never values), notes, skills. "
                  "get secret: writes its env exports to a private file and tells you what to `source`. "
                  "get note: the how-to. put secret / put note: store or update. publish skill: share a local "
                  "skill directory (path) with every agent.",
     S(action=P("string", "list | get | put | publish", True, enum=["list", "get", "put", "publish"]),
       type=P("string", "secret | note | skill", True, enum=["secret", "note", "skill"]),
       name=P("string", "entry name", True), tag=P("string", "list: only entries with this tag", True),
       value=P("string", "put secret: the value (JSON object if it has several fields)", True),
       env=P("object", "put secret: env var -> JSON key in the value (\"\" = whole value)", True),
       note=P("string", "put secret: what it is for", True), title=P("string", "put note: one line", True),
       body=P("string", "put note: the note", True),
       tags=P("array", "put: tags", True, items={"type": "string"}),
       refs=P("array", "put note: secret names it relies on", True, items={"type": "string"}),
       project=P("string", "put: limit to a project; empty = everyone", True),
       path=P("string", "publish skill: path to the skill directory (with SKILL.md)", True))),
]


def fmt_task(t, posts=None):
    head = "#%d [%s] %s  (%s -> %s%s)" % (t["id"], t["state"], t["title"], t["created_by"],
                                         t["owner"] or "%s@%s" % (t["role"], t["project"]),
                                         ", project %s" % t["project"] if t.get("project") else "")
    if posts is None:
        return head
    parts = [head]
    for k in ("goal", "context", "acceptance", "result"):
        if t.get(k):
            parts.append("%s:\n%s" % (k.upper(), t[k]))
    if t.get("children"):
        parts.append("SUBTASKS:\n" + "\n".join("  #%d [%s] %s -> %s" % (c["id"], c["state"], c["title"], c["owner"])
                                               for c in t["children"]))
    if posts:
        parts.append("THREAD:\n" + "\n".join("  %s %s %s: %s" % (p["ts"][5:16], p["sender"], p["kind"], p["body"])
                                             for p in posts))
    return "\n\n".join(parts)


def _secret_file(name, d):
    fd, path = tempfile.mkstemp(prefix="hub-%s-" % name, suffix=".env")
    with os.fdopen(fd, "w") as fh:
        if d.get("exports"):
            fh.write("\n".join(d["exports"]) + "\n")
        else:
            var = re.sub(r"[^A-Za-z0-9_]", "_", name).upper()
            import shlex
            fh.write("export %s=%s\n" % (var, shlex.quote(d["value"])))
    os.chmod(path, 0o600)
    return path


def run_tool(name, a):
    if name == "hub_register":
        if not a.get("name"):
            s = state["sess"]
            if not s:
                return "No hub identity yet. %s" % (state.get("error") or "Call hub_register(name=...).")
            w = hcall("GET", "/v1/whoami")
            card = w["agent"]
            return "You are %s (%s, %s:%s), push=%s, session %s. Scopes: %s. %d queued." % (
                card["name"], card["kind"], card["node"], card["workspace"], s["push"], s["session_id"],
                ", ".join("%s/%s" % (x["project"], x["role"]) for x in card["scopes"]) or "none", card["pending"])
        out = register(a["name"], a.get("project", ""), a.get("role", ""), a.get("description", ""))
        _set_chan_name(a["name"])
        card = out["agent"]
        msg = "Registered as %s (%s@%s). Scopes: %s. %d queued item(s)%s." % (
            card["name"], CWD, card["node"],
            ", ".join("%s/%s" % (x["project"], x["role"]) for x in card["scopes"]) or "none", out["queued"],
            {"channel": " -- they are being pushed now", "bridge": " -- the bridge delivers them",
             "pull": " -- fetch them with hub_inbox"}[push_mode()] if out["queued"] else "")
        if card.get("liaison_of"):
            msg += "\nYou are the liaison of: %s -- what is addressed to the human comes to you." % ", ".join(card["liaison_of"])
        return msg
    if name == "hub_agents":
        q = "?project=%s" % a["project"] if a.get("project") else ""
        rows = hcall("GET", "/v1/agents" + q)["agents"]
        if a.get("live_only"):
            rows = [r for r in rows if r["presence"] == "live"]
        return "\n".join("%-20s %-6s %-7s %-4s %s:%s  roles=%s%s%s%s" % (
            r["name"], r["kind"], r["presence"], r["state"] or "-", r["node"], r["workspace"],
            ",".join("%s/%s" % (s["project"], s["role"]) for s in r["scopes"]) or "-",
            "  liaison:" + ",".join(r["liaison_of"]) if r["liaison_of"] else "",
            "  now: #%d %s" % (r["current_task"]["id"], r["current_task"]["title"][:50]) if r["current_task"] else "",
            "  -- " + r["description"] if r.get("description") else "") for r in rows) or "no agents"
    if name == "hub_send":
        out = hcall("POST", "/v1/posts", {"to": a.get("to", ""), "body": a["body"], "reply_to": a.get("reply_to")})
        who = ", ".join(out["delivered_to"]) or "nobody yet (saved; e.g. the project has no liaison)"
        return "Sent (msg_id=%d) to %s." % (out["post_id"], who)
    if name == "hub_delegate":
        t = hcall("POST", "/v1/tasks", {"to": a["to"], "title": a["title"], "goal": a.get("goal", ""),
                                        "context": a.get("context", ""), "acceptance": a.get("acceptance", ""),
                                        "project": a.get("project", ""), "parent_id": a.get("parent_task")})["task"]
        return "Delegated task #%d to %s. Its plan, questions and result will come back to you; keep working." % (
            t["id"], a["to"])
    if name == "hub_update":
        t = hcall("POST", "/v1/tasks/%d" % int(a["task_id"]),
                  {"kind": a["kind"], "body": a["body"], "to": a.get("to", "")})["task"]
        return "Posted %s to #%d (state now %s)." % (a["kind"], t["id"], t["state"])
    if name == "hub_close":
        t = hcall("POST", "/v1/tasks/%d" % int(a["task_id"]),
                  {"kind": "close", "state": a["state"], "body": a.get("note", "")})["task"]
        return "#%d is %s." % (t["id"], t["state"])
    if name == "hub_tasks":
        if a.get("task_id"):
            out = hcall("GET", "/v1/tasks/%d" % int(a["task_id"]))
            return fmt_task(out["task"], out["posts"])
        import urllib.parse
        scope = a.get("scope") or "mine"
        q = {"state": a.get("state") or "active"}
        if a.get("project"):
            q["project"] = a["project"]
        if a.get("query"):
            q["q"] = a["query"]
        rows = []
        if scope == "mine":
            for key in ("owner", "created_by"):
                rows += hcall("GET", "/v1/tasks?" + urllib.parse.urlencode(dict(q, **{key: me()["name"]})))["tasks"]
            seen = set()
            rows = [t for t in rows if not (t["id"] in seen or seen.add(t["id"]))]
        else:
            rows = hcall("GET", "/v1/tasks?" + urllib.parse.urlencode(q))["tasks"]
        return "\n".join(fmt_task(t) for t in rows) or "no tasks"
    if name == "hub_inbox":
        items = hcall("GET", "/v1/inbox")["items"]
        return "\n\n---\n\n".join(i["text"] for i in items) or "inbox empty"
    if name == "hub_board":
        return board(a)
    raise RuntimeError("unknown tool " + name)


def board(a):
    act, typ, nm = a.get("action") or "list", a.get("type") or "", a.get("name") or ""
    if act == "list":
        out = hcall("GET", "/v1/board")
        tag = (a.get("tag") or "").lower()
        sec = ["  %-26s %-14s %s%s" % (v["name"], ",".join(v["tags"]) or "-", (v["note"] or "").splitlines()[0][:90] if v["note"] else "",
                                      "  (env: %s)" % ",".join(v["env_keys"]) if v["env_keys"] else "")
               for v in out["secrets"] if not tag or tag in v["tags"]]
        notes = ["  %-26s %-14s %s%s" % (n["name"], ",".join(n["tags"]) or "-", n["title"],
                                        "  (uses %s)" % ",".join(n["refs"]) if n["refs"] else "")
                 for n in out["notes"] if not tag or tag in n["tags"]]
        skills = ["  %s/%s: %s" % (s.get("plugin"), s.get("skill"), (s.get("description") or "")[:90])
                  for s in (out.get("skills") or {}).get("skills", [])] if isinstance(out.get("skills"), dict) else []
        return ("SECRETS (get -> a file to source):\n%s\n\nNOTES:\n%s%s" % (
            "\n".join(sec) or "  none", "\n".join(notes) or "  none",
            "\n\nSKILLS:\n" + "\n".join(skills) if skills else ""))
    if act == "get" and typ == "secret":
        d = hcall("GET", "/v1/board/secret/" + nm)
        path = _secret_file(nm, d)
        return "Wrote %s's env exports to %s (0600). Run `source %s` in the shell that needs it; do not print it." % (
            nm, path, path)
    if act == "get" and typ == "note":
        n = hcall("GET", "/v1/board/note/" + nm)
        return "%s -- %s (v%d, %s)%s\n\n%s" % (n["name"], n["title"], n["version"], n["owner"],
                                              "\nuses secrets: " + ", ".join(n["refs"]) if n["refs"] else "", n["body"])
    if act == "put" and typ in ("secret", "note"):
        keys = ("value", "note", "tags", "env", "project") if typ == "secret" else ("title", "body", "tags", "refs", "project")
        out = hcall("POST", "/v1/board/%s/%s" % (typ, nm), {k: a[k] for k in keys if a.get(k) is not None})
        return "Stored %s %s v%d." % (typ, out["name"], out["version"])
    if act == "publish":
        path = os.path.abspath(os.path.expanduser(a.get("path") or ""))
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
        out = hcall("POST", "/v1/skills", {"name": os.path.basename(path.rstrip("/")), "files": files})
        return "Published %s to %s v%s; agents get it on their next start." % (out["skill"], out["plugin"], out["version"])
    raise RuntimeError("hub_board: action=list | get type=secret|note | put type=secret|note | publish path=...")


# ------------------------------------------------------------------ MCP loop

def _stop(*_):
    release()
    os._exit(0)


def main(kind=None):
    import signal
    configure(kind)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _stop)
    if KIND == "claude":
        mark_running()
        threading.Thread(target=watch_conversation, daemon=True).start()
    want = os.environ.get("HUB_AGENT", "")
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        method, mid = m.get("method"), m.get("id")
        try:
            if method == "initialize":
                if not state["sess"]:
                    try:
                        adopt()
                    except Exception as e:
                        log("adopt error:", e)
                if want and not state["sess"]:
                    try:
                        register(want)
                        _set_chan_name(want)
                    except Exception:
                        pass
                caps = {"tools": {}}
                if CHANNEL:
                    caps["experimental"] = {"claude/channel": {}}
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": m["params"].get("protocolVersion", "2025-06-18"),
                    "capabilities": caps, "serverInfo": {"name": "agent-hub", "version": __import__("agenthub").__version__},
                    "instructions": instructions()}})
            elif method == "notifications/initialized":
                state["ready"].set()
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                    {"name": n, "description": d, "inputSchema": s} for n, d, s in TOOLS]}})
            elif method == "tools/call":
                name, args = m["params"]["name"], m["params"].get("arguments") or {}
                try:
                    text, err = run_tool(name, args), False
                except Exception as e:
                    text, err = "error: %s" % e, True
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}],
                                                               "isError": err}})
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
    release()  # the agent exited: free the name now, not in two minutes
