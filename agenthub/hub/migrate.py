"""`agenthub migrate --from <v1 hub.db>`: copy a v1 hub's data into a fresh v2 database.

The v1 database is opened read-only (`mode=ro`) and never written. Mapping
(docs/refactor/core.md §11):

  agents (+aliases folded in), agent_scopes, projects, node_tokens, nodes -> same, minus wake/orders
  tasks          states: planning/in_progress/blocked/needs_human -> working, dropped -> cancelled
  task_log       -> posts with task_id
  messages       -> posts without task_id (threads and reply_to kept)
  events         -> posts kind=post to *@project (team news)
  vault, vault_log, notes, note_log -> copied as they are
  deliveries     -> only the undelivered ones, into inbox
  orders, permissions, tg_refs, sessions -> not copied

Prints a count per table, v1 vs v2, and the agents whose kind needs a look
(v1 recorded nearly everything as claude).
"""
import os
import sqlite3
import sys

STATE = {"open": "open", "planning": "working", "in_progress": "working", "blocked": "working",
         "needs_human": "working", "review": "review", "done": "done", "dropped": "cancelled",
         "cancelled": "cancelled", "failed": "cancelled"}
KIND = {"claude": "claude", "codex": "codex", "pi": "pi", "agy": "agy", "gemini": "other",
        "opencode": "other", "generic": "other"}
LOG_KIND = {"blocked": "question", "needs_human": "question", "claimed": "progress",
            "dropped": "closed", "done": "closed", "reopened": "reopened"}


UUID = __import__("re").compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _conv(c):
    """v1 also stored placeholders (pid-123, wrapper ids); only a real conversation id can be resumed."""
    return c if c and UUID.match(c) else ""


def _cols(conn, table):
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    except sqlite3.DatabaseError:
        return set()


def _rows(conn, table):
    if not _cols(conn, table):
        return []
    return conn.execute("SELECT * FROM %s" % table).fetchall()


def main(src, dest=None, force=False):
    from . import store
    dest = dest or store.DB_PATH
    if not os.path.isfile(src):
        sys.exit("no such v1 database: %s" % src)
    if os.path.exists(dest):
        if not force:
            sys.exit("%s exists; pass --force to replace it" % dest)
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(dest + ext):
                os.remove(dest + ext)
    v1 = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(src), uri=True)
    v1.row_factory = sqlite3.Row
    store.DB_PATH = dest
    store.init_db()
    counts = migrate(v1, dest)
    print("%-12s %8s %8s" % ("table", "v1", "v2"))
    for k, (a, b) in counts["tables"].items():
        print("%-12s %8s %8s" % (k, a, b))
    if counts["kinds"]:
        print("\nkind to confirm (v1 recorded these as below; fix with `agenthub agent-set NAME --kind agy|codex|pi`):")
        for name, kind, ws in counts["kinds"]:
            print("  %-24s %-7s %s" % (name, kind, ws))
    print("\nprojects have no liaison yet: `agenthub project NAME --liaison NAME-agy`")
    print("migrated into %s" % dest)


def migrate(v1, dest):
    from .store import now
    out = sqlite3.connect(dest)
    out.row_factory = sqlite3.Row
    alias = {r["alias"]: r["agent"] for r in _rows(v1, "agent_aliases")}

    def A(n):
        return alias.get(n, n) if n else n

    tables = {}
    with out:
        # -- identity
        acols = _cols(v1, "agents")
        n = 0
        for r in _rows(v1, "agents"):
            out.execute("INSERT OR REPLACE INTO agents (name,kind,node,workspace,description,conversation,hidden,"
                        "created_at,last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                        (r["name"], KIND.get(r["kind"] or "claude", "other"), r["node"] or "", r["workspace"] or "",
                         r["description"] or "", _conv(r["conversation"] if "conversation" in acols else ""),
                         r["hidden"] if "hidden" in acols else 0, r["registered_at"] or now(), r["last_seen"]))
            n += 1
        tables["agents"] = (len(_rows(v1, "agents")), n)
        n = 0
        for r in _rows(v1, "agent_scopes"):
            out.execute("INSERT OR IGNORE INTO scopes (agent,project,role) VALUES (?,?,?)",
                        (A(r["agent"]), r["project"], r["role"] or ""))
            n += 1
        tables["scopes"] = (len(_rows(v1, "agent_scopes")), n)
        for r in _rows(v1, "projects"):
            out.execute("INSERT OR REPLACE INTO projects (name,label,description,liaison,archived,created_at) "
                        "VALUES (?,?,?,'',?,?)", (r["name"], r["label"] or "", r["description"] or "",
                                                 r["archived"] or 0, r["created_at"] or now()))
        tables["projects"] = (len(_rows(v1, "projects")), out.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
        for r in _rows(v1, "node_tokens"):
            out.execute("INSERT OR REPLACE INTO node_tokens (name,token_hash,created_at,last_used) VALUES (?,?,?,?)",
                        (r["name"], r["token_hash"], r["created_at"], r["last_used"]))
        tables["node_tokens"] = (len(_rows(v1, "node_tokens")), len(_rows(v1, "node_tokens")))
        for r in _rows(v1, "nodes"):
            out.execute("INSERT OR REPLACE INTO nodes (name,last_seen) VALUES (?,?)", (r["name"], r["last_seen"]))

        # -- tasks and their threads
        projects = {}
        for r in _rows(v1, "tasks"):
            projects[r["id"]] = r["project"] or ""
            out.execute("INSERT INTO tasks (id,project,parent_id,title,goal,context,acceptance,role,owner,created_by,"
                        "state,result,created_at,updated_at,closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["id"], r["project"] or "", r["parent_id"], r["title"], r["goal"] or "", r["context"] or "",
                         r["acceptance"] or "", r["role"] or "", A(r["owner"] or ""), A(r["created_by"] or ""),
                         STATE.get(r["state"], "working"), r["result"] or "", r["created_at"], r["updated_at"],
                         r["closed_at"]))
        tables["tasks"] = (len(projects), out.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        n = 0
        for r in _rows(v1, "task_log"):
            kind = LOG_KIND.get(r["kind"], r["kind"])
            rec = "human" if r["kind"] == "needs_human" else ""
            out.execute("INSERT INTO posts (task_id,thread_id,sender,recipient,kind,body,project,ts) "
                        "VALUES (?,?,?,?,?,?,?,?)", (r["task_id"], "", A(r["agent"]), rec, kind, r["body"] or "",
                                                     projects.get(r["task_id"], ""), r["ts"]))
            n += 1
        tables["task_log"] = (len(_rows(v1, "task_log")), n)

        # -- messages, keeping threads and reply_to
        idmap = {}
        msgs = sorted(_rows(v1, "messages"), key=lambda r: r["id"])
        for r in msgs:
            to = A(r["to_agent"]) or ("%s@%s" % (r["to_role"], r["to_project"]) if r["to_role"] else "")
            pid = out.execute("INSERT INTO posts (task_id,thread_id,reply_to,sender,recipient,kind,body,project,ts) "
                              "VALUES (NULL,?,?,?,?,'message',?,?,?)",
                              (r["thread_id"] or "", None, A(r["from_agent"]), to, r["body"],
                               r["to_project"] or "", r["ts"])).lastrowid
            idmap[r["id"]] = pid
        for r in msgs:
            if r["reply_to"] and r["reply_to"] in idmap:
                out.execute("UPDATE posts SET reply_to=? WHERE id=?", (idmap[r["reply_to"]], idmap[r["id"]]))
        out.execute("UPDATE posts SET thread_id='m'||id WHERE task_id IS NULL AND thread_id=''")
        tables["messages"] = (len(msgs), len(idmap))

        # -- what v1 had queued but not yet delivered when it stopped: into v2's inbox
        n = 0
        dl = [r for r in _rows(v1, "deliveries") if not r["delivered_at"] and r["agent"] != "human"
              and r["kind"] not in ("verdict", "permission", "unrouted")]
        for r in dl:
            post = idmap.get(r["msg_id"]) if r["msg_id"] else None
            if not post and r["task_id"] in projects:
                row = out.execute("SELECT id FROM posts WHERE task_id=? AND ts<=? ORDER BY id DESC LIMIT 1",
                                  (r["task_id"], r["created_at"])).fetchone()
                post = row["id"] if row else None
            if not post:
                import json as _json
                frm = (_json.loads(r["meta"] or "{}") or {}).get("from") or "hub"
                post = out.execute("INSERT INTO posts (task_id,thread_id,sender,recipient,kind,body,project,ts) "
                                   "VALUES (?,?,?,?,'message',?,?,?)",
                                   (r["task_id"] if r["task_id"] in projects else None, "", A(frm), A(r["agent"]),
                                    r["content"], projects.get(r["task_id"], ""), r["created_at"])).lastrowid
                out.execute("UPDATE posts SET thread_id='m'||id WHERE id=? AND task_id IS NULL", (post,))
            out.execute("INSERT INTO inbox (agent,post_id,kind,task_id,text,created_at) VALUES (?,?,?,?,?,?)",
                        (A(r["agent"]), post, r["kind"], r["task_id"] if r["task_id"] in projects else None,
                         r["content"], r["created_at"]))
            n += 1
        tables["queued"] = (len(dl), n)

        # -- team feed
        n = 0
        for r in _rows(v1, "events"):
            body = r["title"] + ("\n\n" + r["body"] if r["body"] else "")
            pid = out.execute("INSERT INTO posts (task_id,thread_id,sender,recipient,kind,body,project,ts) "
                              "VALUES (?,?,?,?,'post',?,?,?)",
                              (r["task_id"] if r["task_id"] in projects else None, "", A(r["agent"]),
                               "*@%s" % r["project"] if r["project"] else "", body, r["project"] or "", r["ts"])).lastrowid
            if not r["task_id"] or r["task_id"] not in projects:
                out.execute("UPDATE posts SET thread_id=? WHERE id=?", ("m%d" % pid, pid))
            n += 1
        tables["events"] = (len(_rows(v1, "events")), n)

        # -- the shared board, as it is
        vcols = _cols(v1, "vault")
        for r in _rows(v1, "vault"):
            out.execute("INSERT OR REPLACE INTO vault (name,value,note,owner,project,version,updated_at,tags,env) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (r["name"], r["value"], r["note"] or "", r["owner"] or "", r["project"] or "", r["version"],
                         r["updated_at"], r["tags"] if "tags" in vcols else "[]", r["env"] if "env" in vcols else "{}"))
        tables["vault"] = (len(_rows(v1, "vault")), out.execute("SELECT COUNT(*) FROM vault").fetchone()[0])
        for r in _rows(v1, "vault_log"):
            out.execute("INSERT INTO vault_log (name,agent,action,ts) VALUES (?,?,?,?)",
                        (r["name"], r["agent"], r["action"], r["ts"]))
        tables["vault_log"] = (len(_rows(v1, "vault_log")), out.execute("SELECT COUNT(*) FROM vault_log").fetchone()[0])
        for r in _rows(v1, "notes"):
            out.execute("INSERT OR REPLACE INTO notes (name,title,body,tags,refs,project,owner,version,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", tuple(r[k] for k in (
                            "name", "title", "body", "tags", "refs", "project", "owner", "version", "updated_at")))
        tables["notes"] = (len(_rows(v1, "notes")), out.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
        for r in _rows(v1, "note_log"):
            out.execute("INSERT INTO note_log (name,version,body,agent,ts) VALUES (?,?,?,?,?)",
                        (r["name"], r["version"], r["body"], r["agent"], r["ts"]))
        tables["note_log"] = (len(_rows(v1, "note_log")), out.execute("SELECT COUNT(*) FROM note_log").fetchone()[0])
        tables["posts"] = ("-", out.execute("SELECT COUNT(*) FROM posts").fetchone()[0])
    import re
    kinds = [(r["name"], r["kind"], r["workspace"]) for r in out.execute(
        "SELECT name, kind, workspace FROM agents WHERE hidden=0 AND kind='claude' ORDER BY name")
        if set(re.split(r"[-_.]", r["name"].lower())) & {"agy", "codex", "pi", "gemini", "opencode"}]
    out.close()
    return {"tables": tables, "kinds": kinds}
