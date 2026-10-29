"""Storage: schema, connections, and the derived values (presence, relative
time) that the API, the bot and the dashboard all need.

SQLite is the source of truth. The FTS index is derived and rebuildable.
"""
import contextlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the agenthub package
DATA_DIR = os.path.expanduser(os.environ.get("HUB_DATA") or "~/.agent-hub/hub")
DB_PATH = os.environ.get("HUB_DB", os.path.join(DATA_DIR, "hub.db"))

# Presence. A session touches the hub on every channel pull (a long-poll that
# returns at least every ~50s), so "live" can be tight.
LIVE_AFTER = 120
IDLE_AFTER = 2 * 3600

HUMAN = "human"  # the byline for anything you do from Telegram or the dashboard

EVENT_KINDS = ("status", "finding", "decision", "artifact", "question", "handoff", "note")

# The task lifecycle. `review` means the assignee says it is finished and the
# delegator has not accepted it yet; `needs_human` is blocked on *you*.
TASK_STATES = ("open", "planning", "in_progress", "blocked", "needs_human",
               "review", "done", "dropped")
OPEN_STATES = ("open", "planning", "in_progress", "blocked", "needs_human", "review")

# What can be written into a task's thread, and the state each kind implies.
LOG_KINDS = {
    "plan": "in_progress",
    "progress": None,
    "question": None,
    "answer": None,
    "comment": None,
    "blocked": "blocked",
    "needs_human": "needs_human",
    "result": "review",
}

SECRETISH = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_\-]{30,}|xox[baprs]-[A-Za-z0-9\-]{10,}|\d{8,10}:AA[A-Za-z0-9_\-]{30,}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  name        TEXT PRIMARY KEY,
  ip          TEXT NOT NULL DEFAULT '',
  os          TEXT NOT NULL DEFAULT '',
  owner       TEXT NOT NULL DEFAULT '',
  info        TEXT NOT NULL DEFAULT '{}',
  hubd_seen   TEXT,
  last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS agents (
  name          TEXT PRIMARY KEY,
  kind          TEXT NOT NULL DEFAULT 'claude',
  node          TEXT NOT NULL DEFAULT '',
  workspace     TEXT NOT NULL DEFAULT '',
  repo          TEXT NOT NULL DEFAULT '',
  branch        TEXT NOT NULL DEFAULT '',
  description   TEXT NOT NULL DEFAULT '',
  capabilities  TEXT NOT NULL DEFAULT '[]',
  status        TEXT NOT NULL DEFAULT '',
  wake          TEXT NOT NULL DEFAULT 'auto',
  last_session  TEXT NOT NULL DEFAULT '',
  registered_at TEXT,
  last_seen     TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id         TEXT PRIMARY KEY,
  agent      TEXT NOT NULL DEFAULT '',
  node       TEXT NOT NULL DEFAULT '',
  cwd        TEXT NOT NULL DEFAULT '',
  channel    INTEGER NOT NULL DEFAULT 0,
  pid        INTEGER,
  started_at TEXT NOT NULL,
  last_seen  TEXT NOT NULL,
  ended_at   TEXT
);
CREATE INDEX IF NOT EXISTS sessions_agent ON sessions(agent, last_seen DESC);

CREATE TABLE IF NOT EXISTS agent_scopes (
  agent   TEXT NOT NULL,
  project TEXT NOT NULL,
  role    TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (agent, project, role)
);
CREATE INDEX IF NOT EXISTS scopes_project ON agent_scopes(project, role);

CREATE TABLE IF NOT EXISTS agent_aliases (
  alias TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  ts    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  name        TEXT PRIMARY KEY,
  label       TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

-- A task is a delegation: a goal, the context the delegator already has, and
-- what "done" means. Its thread (task_log) is where planning, questions and the
-- result live, so the whole collaboration can be read in one place.
CREATE TABLE IF NOT EXISTS tasks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  project    TEXT NOT NULL DEFAULT '',
  parent_id  INTEGER,
  title      TEXT NOT NULL,
  goal       TEXT NOT NULL DEFAULT '',
  context    TEXT NOT NULL DEFAULT '',
  acceptance TEXT NOT NULL DEFAULT '',
  role       TEXT NOT NULL DEFAULT '',
  owner      TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '',
  state      TEXT NOT NULL DEFAULT 'open',
  plan       TEXT NOT NULL DEFAULT '',
  result     TEXT NOT NULL DEFAULT '',
  priority   INTEGER NOT NULL DEFAULT 0,
  from_event INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at  TEXT
);
CREATE INDEX IF NOT EXISTS tasks_project ON tasks(project, state, id DESC);
CREATE INDEX IF NOT EXISTS tasks_owner   ON tasks(owner, state);
CREATE INDEX IF NOT EXISTS tasks_parent  ON tasks(parent_id);

CREATE TABLE IF NOT EXISTS task_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  agent   TEXT NOT NULL,
  kind    TEXT NOT NULL,
  body    TEXT NOT NULL DEFAULT '',
  ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_log_task ON task_log(task_id, id);

CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  agent   TEXT NOT NULL,
  kind    TEXT NOT NULL,
  title   TEXT NOT NULL,
  body    TEXT NOT NULL DEFAULT '',
  tags    TEXT NOT NULL DEFAULT '[]',
  refs    TEXT NOT NULL DEFAULT '{}',
  project TEXT NOT NULL DEFAULT '',
  task_id INTEGER,
  idem    TEXT,
  ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_agent   ON events(agent, id DESC);
CREATE INDEX IF NOT EXISTS events_project ON events(project, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS events_idem ON events(agent, idem) WHERE idem IS NOT NULL;

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
  USING fts5(title, body, content='events', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
  INSERT INTO events_fts(events_fts, rowid, title, body) VALUES('delete', old.id, old.title, old.body);
END;

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id  TEXT NOT NULL,
  from_agent TEXT NOT NULL,
  to_agent   TEXT NOT NULL,
  to_role    TEXT NOT NULL DEFAULT '',
  to_project TEXT NOT NULL DEFAULT '',
  body       TEXT NOT NULL,
  state      TEXT NOT NULL DEFAULT 'pending',
  reply_to   INTEGER,
  read_by    TEXT NOT NULL DEFAULT '',
  ts         TEXT NOT NULL,
  read_at    TEXT
);
CREATE INDEX IF NOT EXISTS msg_inbox  ON messages(to_agent, state, id);
CREATE INDEX IF NOT EXISTS msg_thread ON messages(thread_id, id);

-- The push queue. Anything an agent should *react to* lands here; the channel
-- server in that agent's session long-polls it and injects each row into the
-- conversation. If no session is listening, the hub asks the agent's node to
-- start one (wake_orders).
CREATE TABLE IF NOT EXISTS deliveries (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  agent        TEXT NOT NULL,
  kind         TEXT NOT NULL,
  task_id      INTEGER,
  msg_id       INTEGER,
  content      TEXT NOT NULL,
  meta         TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL,
  sent_at      TEXT,
  delivered_at TEXT,
  session      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS deliveries_pending ON deliveries(agent, delivered_at, id);

CREATE TABLE IF NOT EXISTS orders (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  node       TEXT NOT NULL,
  kind       TEXT NOT NULL,
  agent      TEXT NOT NULL DEFAULT '',
  args       TEXT NOT NULL DEFAULT '{}',
  reason     TEXT NOT NULL DEFAULT '',
  state      TEXT NOT NULL DEFAULT 'pending',
  result     TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  taken_at   TEXT,
  done_at    TEXT
);
CREATE INDEX IF NOT EXISTS orders_node ON orders(node, state, id);

CREATE TABLE IF NOT EXISTS permissions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  agent       TEXT NOT NULL,
  session     TEXT NOT NULL,
  request_id  TEXT NOT NULL,
  tool        TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  preview     TEXT NOT NULL DEFAULT '',
  state       TEXT NOT NULL DEFAULT 'pending',
  decided_by  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  decided_at  TEXT
);
CREATE INDEX IF NOT EXISTS permissions_state ON permissions(state, id DESC);

CREATE TABLE IF NOT EXISTS vault (
  name       TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  note       TEXT NOT NULL DEFAULT '',
  owner      TEXT NOT NULL DEFAULT '',
  project    TEXT NOT NULL DEFAULT '',
  version    INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vault_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  name   TEXT NOT NULL,
  agent  TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  ts     TEXT NOT NULL
);

-- Non-secret shared knowledge: endpoints, model lists, conventions, how-tos.
-- Unlike events these are living documents -- edited in place, history kept.
CREATE TABLE IF NOT EXISTS notes (
  name       TEXT PRIMARY KEY,
  title      TEXT NOT NULL DEFAULT '',
  body       TEXT NOT NULL DEFAULT '',
  tags       TEXT NOT NULL DEFAULT '[]',
  refs       TEXT NOT NULL DEFAULT '[]',
  project    TEXT NOT NULL DEFAULT '',
  owner      TEXT NOT NULL DEFAULT '',
  version    INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS note_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  name    TEXT NOT NULL,
  version INTEGER NOT NULL,
  body    TEXT NOT NULL,
  agent   TEXT NOT NULL DEFAULT '',
  ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS note_log_name ON note_log(name, id DESC);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Machines allowed to join. A node presents its token; the hub knows which node
-- is calling from the token alone, so `workspace@node` cannot be forged.
CREATE TABLE IF NOT EXISTS node_tokens (
  name       TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  last_used  TEXT
);

-- Telegram message id -> what it was about, so replying to a bot message in
-- Telegram lands in the right task thread.
CREATE TABLE IF NOT EXISTS tg_refs (
  msg_id  INTEGER PRIMARY KEY,
  task_id INTEGER,
  agent   TEXT NOT NULL DEFAULT '',
  ts      TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextlib.contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# Columns added after a database was first created.
ADDED_COLUMNS = (
    ("deliveries", "sent_at", "TEXT"),
    # vault: grouping, and which env vars a value maps to ({"OPENAI_API_KEY": "api_key"} -- the
    # right side is a key into a JSON value, "" for the whole value)
    ("vault", "tags", "TEXT NOT NULL DEFAULT '[]'"),
    ("vault", "env", "TEXT NOT NULL DEFAULT '{}'"),
    # orders are acknowledged by the next poll; a poll whose hubd died never acks (see maintenance)
    ("orders", "acked_at", "TEXT"),
    # retired agents folded away on the dashboard and left out of hub_agents; history stays
    ("agents", "hidden", "INTEGER NOT NULL DEFAULT 0"),
    # dashboard card order, set by drag and drop; NULL = not placed yet (sorts after the placed ones)
    ("agents", "position", "INTEGER"),
    ("projects", "position", "INTEGER"),
    # the Claude conversation a session is showing. A session id is fixed when claude starts;
    # `/resume` inside it switches to another conversation (transcript) under the same id.
    # An agent's identity follows its conversation, so resuming it elsewhere takes the name over.
    ("sessions", "conversation", "TEXT NOT NULL DEFAULT ''"),
    ("sessions", "title", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "conversation", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "title", "TEXT NOT NULL DEFAULT ''"),
)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        for table, col, decl in ADDED_COLUMNS:
            if col not in {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))


def setting(conn, key, default=""):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def resolve(conn, name):
    """A retired name still points at the agent that carries it now."""
    if not name:
        return name
    r = conn.execute("SELECT agent FROM agent_aliases WHERE alias=?", (name,)).fetchone()
    return r["agent"] if r else name


def scopes_of(conn, agent):
    return [dict(r) for r in conn.execute(
        "SELECT project, role FROM agent_scopes WHERE agent=? ORDER BY project, role", (agent,))]


def jloads(s, default):
    try:
        return json.loads(s)
    except Exception:
        return default


def age(ts):
    if not ts:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return None


def presence(last_seen):
    secs = age(last_seen)
    if secs is None:
        return "gone"
    if secs < LIVE_AFTER:
        return "live"
    return "idle" if secs < IDLE_AFTER else "gone"


def ago(ts):
    secs = age(ts)
    if secs is None:
        return ts or "never"
    for cutoff, div, unit in ((90, 1, "s"), (5400, 60, "m"), (172800, 3600, "h")):
        if secs < cutoff:
            return "%d%s" % (secs // div, unit)
    return "%dd" % (secs // 86400)


def live_session(conn, agent):
    """The agent's newest session that is still pulling, or None."""
    r = conn.execute(
        "SELECT * FROM sessions WHERE agent=? AND ended_at IS NULL "
        "ORDER BY last_seen DESC LIMIT 1", (agent,)).fetchone()
    if r and presence(r["last_seen"]) == "live":
        return r
    return None
