"""Codex bridge (`agenthub codex-bridge`):

Runs manually alongside a shared local `codex app-server` (daemon UDS or WS).
Never spawns or wakes any Codex agent process.

Watches `~/.agent-hub/sessions/*.json` for `kind == "codex"` and `push == "bridge"`,
connects to `codex app-server` via JSON-RPC 2.0, resumes the session's `conversation`
(Codex `threadId`), listens to `thread/status/changed` to report `busy` / `idle`,
and delivers hub inbox items via `turn/start` (waiting for `idle` if busy, or
`turn/steer` when requested), then calls `POST /v1/sessions/<id>/delivered`.

Pure Python stdlib (includes a minimal RFC 6455 WebSocket client for `ws://` and
supports `unix://` via `codex app-server proxy` or raw socket).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
from typing import Any, Callable

from .. import client
from .common import (
    SessionAuthError,
    SessionHTTPClient,
    list_session_files,
    remove_session_file,
)

DEFAULT_CODEX_SOCKET = os.path.join(
    os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex"),
    "app-server-control",
    "app-server-control.sock",
)


def _log(*args: Any) -> None:
    print("%s [codex-bridge] %s" % (time.strftime("%H:%M:%S"), " ".join(str(x) for x in args)), flush=True)


# ------------------------------------------------------------------ minimal stdlib WS / JSON-RPC transport

class _StdlibWebSocket:
    """Minimal RFC 6455 text-frame WebSocket client over TCP (stdlib only)."""

    def __init__(self, url: str, timeout: float = 10.0):
        u = urllib.parse.urlparse(url)
        if u.scheme != "ws":
            raise ValueError("only ws:// is supported by _StdlibWebSocket, got %r" % (url,))
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(None)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(req)
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake closed")
            hdr += chunk
        head, rest = hdr.split(b"\r\n\r\n", 1)
        first = head.split(b"\r\n", 1)[0]
        if b" 101 " not in first:
            raise ConnectionError("WebSocket handshake failed: %r" % (first,))
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode("ascii")
        if expected.encode("ascii") not in head:
            raise ConnectionError("WebSocket Sec-WebSocket-Accept mismatch")
        self._buf = bytearray(rest)
        self._wlock = threading.Lock()

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("WebSocket closed")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        with self._wlock:
            self.sock.sendall(bytes(header) + masked)

    def _send_pong(self, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytearray([0x8A, 0x80 | len(payload)])
        header.extend(mask)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        with self._wlock:
            self.sock.sendall(bytes(header) + masked)

    def recv_text(self) -> str | None:
        chunks: list[bytes] = []
        while True:
            try:
                b1, b2 = self._read_exact(2)
            except (EOFError, OSError):
                return None
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if masked:
                payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping
                self._send_pong(payload)
                continue
            if opcode == 0xA:  # pong
                continue
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class CodexRPCClient:
    """JSON-RPC 2.0 client for `codex app-server` over `ws://` or `codex app-server proxy`."""

    def __init__(self, endpoint: str = "", *, codex_bin: str = "codex"):
        self.endpoint = endpoint or os.environ.get("CODEX_APP_SERVER_URL") or ("unix://" + DEFAULT_CODEX_SOCKET)
        self.codex_bin = codex_bin
        self._ws: _StdlibWebSocket | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._wlock = threading.Lock()
        self._req_id = 0
        self._pending: dict[int, tuple[threading.Event, list[dict[str, Any]]]] = {}
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._closed = False
        self._reader: threading.Thread | None = None

    def connect(self) -> None:
        if self.endpoint.startswith("ws://"):
            self._ws = _StdlibWebSocket(self.endpoint)
        elif self.endpoint.startswith("unix://"):
            # `codex app-server proxy` bridges stdio JSONL to the running local daemon control socket.
            self._proc = subprocess.Popen(
                [self.codex_bin, "app-server", "proxy"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        else:
            raise ValueError("unsupported codex app-server endpoint: %r" % (self.endpoint,))

        self._reader = threading.Thread(target=self._read_loop, name="codex-rpc-reader", daemon=True)
        self._reader.start()

        self.request("initialize", {"clientInfo": {"name": "agent-hub-codex-bridge", "version": "2.0"}})
        self.notify("initialized", {})

    def add_listener(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def _write(self, obj: dict[str, Any]) -> None:
        raw = json.dumps(obj)
        with self._wlock:
            if self._ws is not None:
                self._ws.send_text(raw)
            elif self._proc is not None and self._proc.stdin is not None:
                self._proc.stdin.write(raw + "\n")
                self._proc.stdin.flush()

    def _read_one(self) -> dict[str, Any] | None:
        if self._ws is not None:
            raw = self._ws.recv_text()
            return json.loads(raw) if raw else None
        if self._proc is not None and self._proc.stdout is not None:
            line = self._proc.stdout.readline()
            return json.loads(line) if line else None
        return None

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                msg = self._read_one()
            except Exception:
                break
            if msg is None:
                break
            if "id" in msg and ("result" in msg or "error" in msg):
                rid = msg["id"]
                slot = self._pending.pop(rid, None)
                if slot is not None:
                    ev, box = slot
                    box.append(msg)
                    ev.set()
            else:
                for fn in list(self._listeners):
                    try:
                        fn(msg)
                    except Exception:
                        pass
        self._closed = True
        for ev, _ in list(self._pending.values()):
            ev.set()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
        with self._wlock:
            self._req_id += 1
            rid = self._req_id
        ev = threading.Event()
        box: list[dict[str, Any]] = []
        self._pending[rid] = (ev, box)
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not ev.wait(timeout):
            self._pending.pop(rid, None)
            raise TimeoutError("codex RPC %s timed out" % method)
        if not box:
            raise ConnectionError("codex RPC connection closed during %s" % method)
        resp = box[0]
        if "error" in resp and resp["error"]:
            raise RuntimeError("codex RPC %s error: %s" % (method, resp["error"]))
        return resp.get("result") or {}

    def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            self._ws.close()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


# ------------------------------------------------------------------ per-session Codex worker

def _render_batch(agent: str, items: list[dict[str, Any]]) -> str:
    body = "\n\n---\n\n".join(str(i.get("text") or i.get("content") or "") for i in items)
    return "[agent-hub] 以下是 hub 推送给你（%s）的消息，请按其中要求处理；回复用 hub_* 工具。\n\n%s" % (agent, body)


class CodexSessionWorker:
    """Delivers hub inbox items into a single Codex app-server thread."""

    def __init__(
        self,
        sess: dict[str, Any],
        rpc: CodexRPCClient,
        *,
        pull_wait: int = 50,
    ):
        self.sess = dict(sess)
        self.name = sess["name"]
        self.session_id = sess["session_id"]
        self.rpc = rpc
        self.pull_wait = pull_wait
        self.http = SessionHTTPClient.from_session(sess)
        self.stop_event = threading.Event()
        self.idle_event = threading.Event()
        self.idle_event.set()
        self._state = "idle"
        self._active_turn_id = ""
        self._resumed = False
        self._thread: threading.Thread | None = None
        self._fail_count = 0
        self.rpc.add_listener(self._on_rpc_event)

    def _backoff_seconds(self) -> float:
        """Exponential backoff: 2s -> 4s -> 8s -> 16s -> 32s -> 60s."""
        return float(min(60, 2 << max(0, self._fail_count - 1)))

    @property
    def thread_id(self) -> str:
        return (self.sess.get("conversation") or "").strip()

    def _on_rpc_event(self, msg: dict[str, Any]) -> None:
        method = msg.get("method") or ""
        params = msg.get("params") or {}
        if params.get("threadId") and self.thread_id and params["threadId"] != self.thread_id:
            return
        if method == "thread/status/changed":
            st_type = ((params.get("status") or {}).get("type") or "").strip()
            new_state = "busy" if st_type == "active" else ("idle" if st_type == "idle" else "")
            if new_state and new_state != self._state:
                self._state = new_state
                if new_state == "idle":
                    self.idle_event.set()
                else:
                    self.idle_event.clear()
                try:
                    self.http.state(new_state)
                except Exception as e:
                    _log(self.name, "state report error:", e)
        elif method == "turn/started":
            self._active_turn_id = (params.get("turn") or {}).get("id") or ""
            self.idle_event.clear()
            if self._state != "busy":
                self._state = "busy"
                try:
                    self.http.state("busy")
                except Exception:
                    pass
        elif method == "turn/completed":
            self._active_turn_id = ""
            self.idle_event.set()
            if self._state != "idle":
                self._state = "idle"
                try:
                    self.http.state("idle")
                except Exception:
                    pass

    def ensure_resumed(self) -> bool:
        tid = self.thread_id
        if not tid:
            return False
        if self._resumed:
            return True
        try:
            self.rpc.request("thread/resume", {"threadId": tid})
            self._resumed = True
            return True
        except Exception as e:
            _log(self.name, "thread/resume %s deferred:" % tid, e)
            return False

    def deliver_text(self, text: str, *, steer: bool = False) -> None:
        tid = self.thread_id
        if not tid:
            raise RuntimeError("missing codex thread_id (conversation)")
        if not self.ensure_resumed():
            raise RuntimeError("codex thread %s not yet resumable" % tid)

        if self._state == "busy":
            if steer and self._active_turn_id:
                self.rpc.request(
                    "turn/steer",
                    {
                        "threadId": tid,
                        "expectedTurnId": self._active_turn_id,
                        "input": [{"type": "text", "text": text}],
                    },
                )
                return
            # Wait briefly for idle before starting a new turn
            self.idle_event.wait(timeout=30.0)

        self.rpc.request(
            "turn/start",
            {
                "threadId": tid,
                "input": [{"type": "text", "text": text}],
            },
        )

    def poll_once(self) -> bool:
        if not self.thread_id:
            return True
        if not self.ensure_resumed():
            return True
        try:
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
        try:
            self.deliver_text(text)
            self._fail_count = 0
            ids = [i["id"] for i in items if "id" in i]
            self.http.delivered(ids)
            _log(self.name, "delivered", ids, "-> codex thread", self.thread_id)
        except SessionAuthError:
            remove_session_file(self.name)
            return False
        except Exception as e:
            self._fail_count += 1
            delay = self._backoff_seconds()
            _log(self.name, "deliver failed (#%d, retry in %.0fs, keeping undelivered):" % (self._fail_count, delay), e)
            self.stop_event.wait(delay)
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="codex-bridge-%s" % self.name, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        _log("watching", self.name, "session=%s" % self.session_id, "thread=%s" % (self.thread_id or "(pending)"))
        while not self.stop_event.is_set():
            if not self.poll_once():
                break
            if not self.thread_id or not self._resumed:
                self.stop_event.wait(2.0)

    def stop(self) -> None:
        self.stop_event.set()
        self.idle_event.set()


class CodexBridge:
    """Watches ~/.agent-hub/sessions/*.json for kind=codex push=bridge."""

    def __init__(self, endpoint: str = "", *, scan_interval: float = 2.0, pull_wait: int = 50):
        self.endpoint = endpoint
        self.scan_interval = scan_interval
        self.pull_wait = pull_wait
        self.rpc = CodexRPCClient(endpoint)
        self.workers: dict[str, CodexSessionWorker] = {}
        self.stop_event = threading.Event()

    def reconcile(self) -> None:
        active = {
            s["name"]: s
            for s in list_session_files(kind="codex", push="bridge")
        }
        for name in list(self.workers):
            w = self.workers[name]
            cur = active.get(name)
            if not cur or cur.get("session_id") != w.session_id or not (w._thread and w._thread.is_alive()):
                w.stop()
                self.workers.pop(name, None)
            else:
                w.sess = dict(cur)
        for name, sess in active.items():
            if name not in self.workers:
                w = CodexSessionWorker(sess, self.rpc, pull_wait=self.pull_wait)
                self.workers[name] = w
                w.start()

    def run(self) -> None:
        _log("connecting to codex app-server at", self.rpc.endpoint)
        self.rpc.connect()
        _log("connected; watching ~/.agent-hub/sessions/*.json for kind=codex push=bridge")
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
        self.rpc.close()


def main(endpoint: str = "") -> None:
    bridge = CodexBridge(endpoint=endpoint)
    bridge.run()


if __name__ == "__main__":
    main()
