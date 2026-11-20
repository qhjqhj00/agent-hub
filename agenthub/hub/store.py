"""Storage: schema, connections, and small derived values (presence, relative time).

SQLite is the only source of truth. Every conversation -- direct messages, task
threads, team posts -- is a row in `posts`; `inbox` holds one pointer per
recipient that still has to be delivered. See docs/refactor/core.md §3.
"""
import contextlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the agenthub package
DATA_DIR = os.path.expanduser(os.environ.get("HUB_DATA") or "~/.agent-hub/hub")
DB_PATH = os.environ.get("HUB_DB", os.path.join(DATA_DIR, "hub.db"))

LIVE_AFTER = 120        # a session that has not touched the hub for this long is over
IDLE_AFTER = 2 * 3600   # an agent seen within this long shows as idle rather than offline
LEASE = 60              # a pulled inbox item is not handed out again for this long

HUMAN = "human"
KINDS = ("claude", "agy", "codex", "pi", "other")
PUSH = ("channel", "bridge", "pull")

TASK_STATES = ("open", "working", "review", "done", "cancelled")
OPEN_STATES = ("open", "working", "review")
# what can be written into a task thread -> the state it moves the task to (None = unchanged)
UPDATE_KINDS = {"plan": "working", "progress": "working", "question": None, "answer": None,
                "comment": None, "result": "review"}

SECRETISH = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_\-]{30,}|xox[baprs]-[A-Za-z0-9\-]{10,}|\d{8,10}:AA[A-Za-z0-9_\-]{30,}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS node_tokens (
  name TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, last_used TEXT);

CREATE TABLE IF NOT EXISTS nodes (name TEXT PRIMARY KEY, last_seen TEXT);

CREATE TABLE IF NOT EXISTS agents (
  name         TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,
  node         TEXT NOT NULL DEFAULT '',
  workspace    TEXT NOT NULL DEFAULT '',
  description  TEXT NOT NULL DEFAULT '',
  conversation TEXT NOT NULL DEFAULT '',   -- last conversation, for the start command's resume
  hidden       INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  last_seen    TEXT
);

CREATE TABLE IF NOT EXISTS scopes (
  agent TEXT NOT NULL, project TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (agent, project, role));
CREATE INDEX IF NOT EXISTS scopes_project ON scopes(project, role);

CREATE TABLE IF NOT EXISTS projects (
  name        TEXT PRIMARY KEY,
  label       TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  liaison     TEXT NOT NULL DEFAULT '',   -- the project's resident agy: where `human` is delivered
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id           TEXT PRIMARY KEY,
  agent        TEXT NOT NULL,
  node         TEXT NOT NULL DEFAULT '',
  cwd          TEXT NOT NULL DEFAULT '',
  kind         TEXT NOT NULL DEFAULT '',
  conversation TEXT NOT NULL DEFAULT '',
  push         TEXT NOT NULL DEFAULT 'pull',
  state        TEXT NOT NULL DEFAULT '',   -- busy|idle as reported by the adapter
  meta         TEXT NOT NULL DEFAULT '{}',
  token_hash   TEXT NOT NULL UNIQUE,
  pid          INTEGER,
  started_at   TEXT NOT NULL,
  last_seen    TEXT NOT NULL,
  ended_at     TEXT
);
CREATE INDEX IF NOT EXISTS sessions_agent ON sessions(agent, ended_at);

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
  result     TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at  TEXT
);
CREATE INDEX IF NOT EXISTS tasks_project ON tasks(project, state, id DESC);
CREATE INDEX IF NOT EXISTS tasks_owner   ON tasks(owner, state);

CREATE TABLE IF NOT EXISTS posts (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id   INTEGER,
  thread_id TEXT NOT NULL DEFAULT '',
  reply_to  INTEGER,
  sender    TEXT NOT NULL,
  recipient TEXT NOT NULL DEFAULT '',
  kind      TEXT NOT NULL,
  body      TEXT NOT NULL DEFAULT '',
  project   TEXT NOT NULL DEFAULT '',
  ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_task   ON posts(task_id, id);
CREATE INDEX IF NOT EXISTS posts_thread ON posts(thread_id, id);

CREATE TABLE IF NOT EXISTS inbox (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  agent        TEXT NOT NULL,
  post_id      INTEGER NOT NULL,
  kind         TEXT NOT NULL,
  task_id      INTEGER,
  text         TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  sent_at      TEXT,             -- lease start
  session      TEXT NOT NULL DEFAULT '',
  delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS inbox_pending ON inbox(agent, delivered_at, id);

CREATE TABLE IF NOT EXISTS vault (
  name TEXT PRIMARY KEY, value TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '',
  project TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]', env TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS vault_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes (
  name TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]', refs TEXT NOT NULL DEFAULT '[]', project TEXT NOT NULL DEFAULT '',
  owner TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS note_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
  agent TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS note_log_name ON note_log(name, id DESC);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ago_ts(secs):
    """The timestamp `secs` seconds ago, in now()'s format, so plain string comparison works."""
    return (datetime.now(timezone.utc) - timedelta(seconds=secs)).isoformat(timespec="seconds")


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


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)


def setting(conn, key, default=""):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def resolve(conn, name):
    """v2 has no aliases (the migration folds v1's into the agent rows); kept for callers."""
    return name


def scopes_of(conn, agent):
    return [dict(r) for r in conn.execute(
        "SELECT project, role FROM scopes WHERE agent=? ORDER BY project, role", (agent,))]


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


def live_session(conn, agent):
    """The agent's running session, or None."""
    r = conn.execute("SELECT * FROM sessions WHERE agent=? AND ended_at IS NULL "
                     "ORDER BY last_seen DESC LIMIT 1", (agent,)).fetchone()
    if r and (age(r["last_seen"]) if r["last_seen"] else 1e9) < LIVE_AFTER:
        return r
    return None


def presence(conn, agent, last_seen):
    if live_session(conn, agent):
        return "live"
    secs = age(last_seen)
    return "idle" if secs is not None and secs < IDLE_AFTER else "offline"
