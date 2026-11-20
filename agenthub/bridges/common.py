"""Shared helpers for v2 bridges and hooks:

- Local session files (`~/.agent-hub/sessions/<name>.json`, 0600)
- Deterministic T3 thread resolution (`resolve_t3_thread`)
- v2 session HTTP client (`X-Hub-Session` + optional node `Authorization`)
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import client


class SessionAuthError(RuntimeError):
    """Raised when the hub rejects a session (401/404: ended, taken over, or invalid)."""

    def __init__(self, code: int, msg: str):
        super().__init__("session %s: %s" % (code, msg))
        self.code = code


# ------------------------------------------------------------------ local session files

def agenthub_home() -> str:
    return os.path.expanduser(os.environ.get("AGENTHUB_HOME") or "~/.agent-hub")


def sessions_dir() -> str:
    return os.path.join(agenthub_home(), "sessions")


def session_file(name: str) -> str:
    return os.path.join(sessions_dir(), "%s.json" % name)


def read_session_file(name: str) -> dict[str, Any] | None:
    path = session_file(name)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_session_file(data: dict[str, Any]) -> str:
    """Atomically write ~/.agent-hub/sessions/<name>.json with 0600 permissions."""
    name = data.get("name")
    if not name:
        raise ValueError("session data missing 'name'")
    d = sessions_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = session_file(name)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def update_session_file(name: str, **updates: Any) -> dict[str, Any] | None:
    """Merge updates (including nested 'meta') into an existing session file."""
    data = read_session_file(name)
    if data is None:
        return None
    for k, v in updates.items():
        if v is None:
            continue
        if k == "meta" and isinstance(v, dict):
            meta = dict(data.get("meta") or {})
            meta.update(v)
            data["meta"] = meta
        else:
            data[k] = v
    write_session_file(data)
    return data


def remove_session_file(name: str) -> bool:
    try:
        os.unlink(session_file(name))
        return True
    except OSError:
        return False


def list_session_files(
    *,
    kind: str | None = None,
    push: str | None = None,
) -> list[dict[str, Any]]:
    """Return all valid session dicts under sessions_dir(), optionally filtered."""
    d = sessions_dir()
    if not os.path.isdir(d):
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("session_id") or not data.get("token"):
            continue
        if kind and data.get("kind") != kind:
            continue
        if push and data.get("push") != push:
            continue
        out.append(data)
    return out


# ------------------------------------------------------------------ deterministic T3 thread lookup

def t3_state_db() -> str:
    home = os.path.expanduser(os.environ.get("T3CODE_HOME") or "~/.t3")
    return os.path.join(home, "userdata", "statev2.sqlite")


def resolve_t3_thread(conversation_id: str, db_path: str | None = None) -> str:
    """Deterministically resolve an Antigravity conversation_id to its T3 thread_id.

    Reads T3's local SQLite projection (`orchestration_v2_projection_provider_threads`)
    in read-only mode and matches `nativeThreadRef.nativeId == conversation_id`.
    Returns "" when no deterministic mapping exists (never guesses).
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return ""
    path = db_path or t3_state_db()
    if not os.path.isfile(path):
        return ""
    conn = None
    try:
        uri = "file:%s?mode=ro" % urllib.parse.quote(path)
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        cur = conn.cursor()
        # Inspect columns defensively in case column naming evolves slightly across T3 builds.
        cur.execute("PRAGMA table_info(orchestration_v2_projection_provider_threads)")
        cols = [row[1] for row in cur.fetchall()]
        if not cols:
            return ""
        # Identify the JSON payload column (typically the last column or named *json*/*payload*/*data*).
        json_cols = [c for c in cols if any(k in c.lower() for k in ("json", "payload", "data", "snapshot", "record"))]
        if not json_cols:
            json_cols = [cols[-1]]
        tid_col = "thread_id" if "thread_id" in cols else None

        for jcol in json_cols:
            select_cols = ("%s, %s" % (tid_col, jcol)) if tid_col else jcol
            cur.execute(
                "SELECT %s FROM orchestration_v2_projection_provider_threads WHERE %s LIKE ?" % (select_cols, jcol),
                ("%%%s%%" % cid,),
            )
            for row in cur.fetchall():
                row_tid = row[0] if tid_col else ""
                raw_json = row[1] if tid_col else row[0]
                if not isinstance(raw_json, str):
                    continue
                try:
                    obj = json.loads(raw_json)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                ref = obj.get("nativeThreadRef") or {}
                if not isinstance(ref, dict):
                    continue
                if ref.get("nativeId") != cid:
                    continue
                driver = ref.get("driver") or obj.get("driver") or ""
                if driver and driver != "antigravity":
                    continue
                app_tid = obj.get("appThreadId") or row_tid or ""
                if app_tid:
                    return str(app_tid)
        return ""
    except sqlite3.Error:
        return ""
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


# ------------------------------------------------------------------ v2 session HTTP client

class SessionHTTPClient:
    """Thin stdlib HTTP client for a single v2 hub session (`api.md`)."""

    def __init__(
        self,
        hub_url: str,
        session_id: str,
        session_token: str,
        *,
        node_token: str | None = None,
    ):
        self.hub_url = hub_url.rstrip("/")
        self.session_id = session_id
        self.session_token = session_token
        self.node_token = client.token() if node_token is None else node_token

    @classmethod
    def from_session(cls, sess: dict[str, Any]) -> "SessionHTTPClient":
        return cls(
            hub_url=sess.get("hub") or client.url(),
            session_id=sess["session_id"],
            session_token=sess["token"],
        )

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
        req = urllib.request.Request(self.hub_url + path, method=method)
        if self.node_token:
            req.add_header("Authorization", "Bearer " + self.node_token)
        req.add_header("X-Hub-Session", self.session_token)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        # Bypass proxies for loopback, LAN, and Tailscale (100.64.0.0/10) hub URLs
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if _bypass_proxy(self.hub_url) else urllib.request.build_opener()
        try:
            with opener.open(req, data, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error") or e.reason
            except Exception:
                msg = str(e.reason)
            if e.code in (401, 404):
                raise SessionAuthError(e.code, msg)
            raise client.HubRefused(e.code, msg)
        except (urllib.error.URLError, OSError) as e:
            raise client.HubUnavailable("hub unreachable at %s (%s)" % (self.hub_url, getattr(e, "reason", e)))

    def pull(self, wait: int = 50) -> list[dict[str, Any]]:
        wait = max(0, min(55, int(wait)))
        res = self._request(
            "GET",
            "/v1/sessions/%s/pull?wait=%d" % (urllib.parse.quote(self.session_id), wait),
            timeout=wait + 15.0,
        )
        return list(res.get("items") or [])

    def delivered(self, ids: list[int]) -> dict[str, Any]:
        if not ids:
            return {"ok": True}
        return self._request(
            "POST",
            "/v1/sessions/%s/delivered" % urllib.parse.quote(self.session_id),
            {"ids": list(ids)},
        )

    def state(self, value: str) -> dict[str, Any]:
        if value not in ("busy", "idle"):
            raise ValueError("state must be 'busy' or 'idle', got %r" % (value,))
        return self._request(
            "POST",
            "/v1/sessions/%s/state" % urllib.parse.quote(self.session_id),
            {"state": value},
        )

    def meta(
        self,
        *,
        conversation: str | None = None,
        push: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if conversation is not None:
            body["conversation"] = conversation
        if push is not None:
            body["push"] = push
        if meta is not None:
            body["meta"] = meta
        return self._request(
            "POST",
            "/v1/sessions/%s/meta" % urllib.parse.quote(self.session_id),
            body,
        )

    def beat(self) -> dict[str, Any]:
        return self._request("POST", "/v1/sessions/%s/beat" % urllib.parse.quote(self.session_id), {})

    def end(self) -> dict[str, Any]:
        return self._request("POST", "/v1/sessions/%s/end" % urllib.parse.quote(self.session_id), {})


def _bypass_proxy(url: str) -> bool:
    try:
        import ipaddress
        host = urllib.parse.urlparse(url).hostname or ""
        if host in ("localhost", "::1") or host.endswith(".ts.net"):
            return True
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or (ip in ipaddress.ip_network("100.64.0.0/10"))
    except Exception:
        return False
