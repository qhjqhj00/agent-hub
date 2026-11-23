"""T3 Code bridge (`agenthub t3-bridge`):

Runs manually alongside `t3 serve` on a host. Never spawns or wakes any agent
process. Instead, it watches `~/.agent-hub/sessions/*.json` for sessions with
`kind == "agy"` and `push == "bridge"`, long-polls `GET /v1/sessions/<id>/pull`,
and delivers messages via `t3 thread send <t3_thread> <text>`.

Deterministic binding rule:
- Only delivers when `meta.t3_thread` is present (or deterministically resolved
  from `conversation` via `resolve_t3_thread()`).
- Sessions without a deterministic `t3_thread` (e.g. standalone `agy` CLI outside
  T3) are skipped cleanly — their inbox items are NEVER marked delivered.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from .. import client
from .common import (
    SessionAuthError,
    SessionHTTPClient,
    list_session_files,
    remove_session_file,
    resolve_t3_thread,
    update_session_file,
)


def _log(*args: Any) -> None:
    print("%s [t3-bridge] %s" % (time.strftime("%H:%M:%S"), " ".join(str(x) for x in args)), flush=True)


def default_t3_send(thread_id: str, text: str, *, t3_bin: str = "t3", timeout: float = 30.0) -> tuple[bool, str]:
    """Send a user message into an existing T3 thread using the official CLI."""
    cmd = [t3_bin, "thread", "send", thread_id, "-"]
    try:
        r = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, (r.stderr or r.stdout).strip()[:300]
    except Exception as e:
        return False, str(e)


def default_t3_status(thread_id: str, *, t3_bin: str = "t3", timeout: float = 10.0) -> str | None:
    """Query `t3 thread show --json <thread_id>` and map to 'busy' | 'idle' | 'gone' | None.

    Returns 'gone' ONLY when T3 explicitly reports the thread is deleted/missing or archived.
    Transient failures (timeouts, server restarting) return None so the session stays alive.
    """
    try:
        r = subprocess.run(
            [t3_bin, "thread", "show", "--json", "--messages", "0", thread_id],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            err = ((r.stderr or "") + (r.stdout or "")).lower()
            if any(k in err for k in ("not found", "no thread", "does not exist", "unknown thread")):
                return "gone"
            return None
        data = json.loads(r.stdout or "{}")
        thread = data.get("thread") or {}
        if thread.get("archived"):
            return "gone"
        st = thread.get("status") or ""
        if st in ("running", "starting", "preparing"):
            return "busy"
        if st in ("idle", "completed", "waiting", "interrupted", "failed", "cancelled"):
            return "idle"
        return None
    except Exception:
        return None


def _render_batch(agent: str, items: list[dict[str, Any]]) -> str:
    body = "\n\n---\n\n".join(str(i.get("text") or i.get("content") or "") for i in items)
    return "[agent-hub] 以下是 hub 推送给你（%s）的消息，请按其中要求处理；回复用 hub_* 工具。\n\n%s" % (agent, body)


class T3SessionWorker:
    """Long-poll worker for a single T3 agy session."""

    def __init__(
        self,
        sess: dict[str, Any],
        *,
        send_fn: Callable[[str, str], tuple[bool, str]] = default_t3_send,
        status_fn: Callable[[str], str | None] = default_t3_status,
        pull_wait: int = 50,
    ):
        self.sess = dict(sess)
        self.name = sess["name"]
        self.session_id = sess["session_id"]
        self.send_fn = send_fn
        self.status_fn = status_fn
        self.pull_wait = pull_wait
        self.http = SessionHTTPClient.from_session(sess)
        self.stop_event = threading.Event()
        self._last_state: str | None = None
        self._thread: threading.Thread | None = None
        self._fail_count = 0

    def _backoff_seconds(self) -> float:
        """Exponential backoff: 2s -> 4s -> 8s -> 16s -> 32s -> 60s."""
        return float(min(60, 2 << max(0, self._fail_count - 1)))

    def get_t3_thread(self) -> str:
        meta = self.sess.get("meta") or {}
        tid = (meta.get("t3_thread") or "").strip()
        if tid:
            return tid
        cid = (self.sess.get("conversation") or "").strip()
        if cid:
            tid = resolve_t3_thread(cid)
            if tid:
                self.sess.setdefault("meta", {})["t3_thread"] = tid
                update_session_file(self.name, meta={"t3_thread": tid})
                try:
                    self.http.meta(meta={"t3_thread": tid})
                except Exception:
                    pass
                return tid
        return ""

    def sync_state(self, thread_id: str) -> bool:
        """Sync T3 thread status to hub. Returns False if the thread is gone (deleted/archived)."""
        st = self.status_fn(thread_id)
        if st == "gone":
            _log(self.name, "t3 thread %s deleted/archived, ending hub session" % thread_id)
            try:
                self.http.end()
            except Exception:
                pass
            remove_session_file(self.name)
            return False
        if st and st != self._last_state:
            try:
                self.http.state(st)
                self._last_state = st
            except SessionAuthError:
                raise
            except Exception as e:
                _log(self.name, "state sync error:", e)
        return True

    def poll_once(self) -> bool:
        """Execute one pull + deliver cycle. Returns False when worker should exit."""
        tid = self.get_t3_thread()
        if not tid:
            # No deterministic T3 thread binding (e.g., standalone agy CLI): skip without pulling/delivering.
            return True
        try:
            if not self.sync_state(tid):
                return False
            items = self.http.pull(wait=self.pull_wait)
        except SessionAuthError as e:
            _log(self.name, "session ended/rejected (%s), stopping worker" % e)
            remove_session_file(self.name)
            return False
        except (client.HubUnavailable, client.HubRefused) as e:
            self._fail_count += 1
            delay = self._backoff_seconds()
            _log(self.name, "hub pull error (#%d, retry in %.0fs):" % (self._fail_count, delay), e)
            self.stop_event.wait(delay)
            return True

        if not items:
            self._fail_count = 0
            return True

        text = _render_batch(self.name, items)
        ok, info = self.send_fn(tid, text)
        if ok:
            self._fail_count = 0
            ids = [i["id"] for i in items if "id" in i]
            try:
                self.http.delivered(ids)
                self._last_state = "busy"
                self.http.state("busy")
            except SessionAuthError:
                remove_session_file(self.name)
                return False
            except Exception as e:
                _log(self.name, "delivered/state error:", e)
            _log(self.name, "delivered", ids, "-> t3 thread", tid)
        else:
            # Never mark delivered when t3 thread send fails; items re-appear after 60s lease.
            self._fail_count += 1
            delay = self._backoff_seconds()
            _log(self.name, "t3 thread send failed (#%d, retry in %.0fs, keeping undelivered):" % (self._fail_count, delay), info)
            self.stop_event.wait(delay)
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="t3-bridge-%s" % self.name, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        _log("watching", self.name, "session=%s" % self.session_id, "t3_thread=%s" % (self.get_t3_thread() or "(pending)"))
        while not self.stop_event.is_set():
            if not self.poll_once():
                break
            if not self.get_t3_thread():
                self.stop_event.wait(2.0)

    def stop(self) -> None:
        self.stop_event.set()


class T3Bridge:
    """Watches ~/.agent-hub/sessions/*.json and manages per-session T3SessionWorkers."""

    def __init__(
        self,
        *,
        send_fn: Callable[[str, str], tuple[bool, str]] = default_t3_send,
        status_fn: Callable[[str], str | None] = default_t3_status,
        scan_interval: float = 2.0,
        pull_wait: int = 50,
    ):
        self.send_fn = send_fn
        self.status_fn = status_fn
        self.scan_interval = scan_interval
        self.pull_wait = pull_wait
        self.workers: dict[str, T3SessionWorker] = {}
        self.stop_event = threading.Event()

    def reconcile(self) -> None:
        active = {
            s["name"]: s
            for s in list_session_files(kind="agy", push="bridge")
        }
        # Stop removed or replaced sessions
        for name in list(self.workers):
            w = self.workers[name]
            cur = active.get(name)
            if not cur or cur.get("session_id") != w.session_id or not (w._thread and w._thread.is_alive()):
                w.stop()
                self.workers.pop(name, None)
            else:
                # Refresh metadata (e.g. if PreInvocation hook just populated meta.t3_thread)
                w.sess = dict(cur)

        # Start new workers
        for name, sess in active.items():
            if name not in self.workers:
                w = T3SessionWorker(
                    sess,
                    send_fn=self.send_fn,
                    status_fn=self.status_fn,
                    pull_wait=self.pull_wait,
                )
                self.workers[name] = w
                w.start()

    def run(self) -> None:
        _log("started (watching ~/.agent-hub/sessions/*.json for kind=agy push=bridge)")
        try:
            while not self.stop_event.is_set():
                self.reconcile()
                self.stop_event.wait(self.scan_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        for w in list(self.workers.values()):
            w.stop()
        self.workers.clear()


def main(argv: list[str] | None = None) -> None:
    _ = argv
    bridge = T3Bridge()
    bridge.run()


if __name__ == "__main__":
    main()
