"""Unit and fake-hub end-to-end tests for v2 adapters, bridges, hooks, and pi extension."""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import shutil
import socket
import socketserver
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # run from tests/ like the others

from agenthub.bridges import (
    agy_hook,
    codex_bridge,
    common,
    t3_bridge,
)
from agenthub.node import adapters


# ------------------------------------------------------------------ Fake v2 Hub (per api.md)

class FakeV2Hub:
    """In-process HTTP server implementing the v2 contract in docs/refactor/api.md."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, str] = {}  # token -> session_id
        self.inbox: list[dict[str, Any]] = []
        self.next_inbox_id = 1
        self.next_sid = 1
        self.events: list[tuple[str, Any]] = []
        self.wake = threading.Condition(self.lock)
        self.secrets: dict[str, dict[str, Any]] = {
            "demo-key": {
                "name": "demo-key",
                "value": "SUPER_SECRET_VALUE",
                "exports": ["export DEMO_KEY='SUPER_SECRET_VALUE'"],
                "env": {"DEMO_KEY": "value"},
                "note": "test secret",
            },
        }
        self.notes: dict[str, dict[str, Any]] = {}
        self.tasks: dict[int, dict[str, Any]] = {}
        self.next_task_id = 100

        hub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def _send(self, code: int, obj: dict[str, Any]) -> None:
                raw = json.dumps(obj).encode("utf-8")
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _read_json(self) -> dict[str, Any]:
                n = int(self.headers.get("Content-Length") or "0")
                return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

            def _auth_session(self) -> dict[str, Any] | None:
                tok = self.headers.get("X-Hub-Session") or ""
                with hub.lock:
                    sid = hub.tokens.get(tok)
                    if not sid:
                        return None
                    s = hub.sessions.get(sid)
                    if not s or s.get("ended"):
                        return None
                    return s

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                p = parsed.path
                body = self._read_json()

                if p == "/v1/sessions":
                    with hub.lock:
                        sid = f"s_{hub.next_sid}"
                        hub.next_sid += 1
                        tok = f"hst_{sid}"
                        sess = {
                            "session_id": sid,
                            "token": tok,
                            "name": body["name"],
                            "kind": body.get("kind", "other"),
                            "cwd": body.get("cwd", ""),
                            "conversation": body.get("conversation", ""),
                            "push": body.get("push", "pull"),
                            "meta": dict(body.get("meta") or {}),
                            "state": "idle",
                            "ended": False,
                        }
                        hub.sessions[sid] = sess
                        hub.tokens[tok] = sid
                        hub.events.append(("register", sess))
                        queued = sum(1 for i in hub.inbox if i["agent"] == sess["name"] and not i["delivered"])
                    self._send(200, {"session_id": sid, "token": tok, "agent": {"name": sess["name"], "kind": sess["kind"]}, "queued": queued})
                    return

                sess = self._auth_session()
                if not sess:
                    self._send(401, {"error": "invalid or ended session"})
                    return

                parts = p.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["v1", "sessions"]:
                    sid, action = parts[2], parts[3]
                    if sid != sess["session_id"]:
                        self._send(401, {"error": "session mismatch"})
                        return
                    with hub.lock:
                        if action == "state":
                            sess["state"] = body.get("state", "idle")
                            hub.events.append(("state", (sess["name"], sess["state"])))
                            self._send(200, {"ok": True})
                        elif action == "delivered":
                            ids = set(body.get("ids") or [])
                            for it in hub.inbox:
                                if it["id"] in ids and it["agent"] == sess["name"]:
                                    it["delivered"] = True
                            hub.events.append(("delivered", (sess["name"], sorted(ids))))
                            self._send(200, {"ok": True})
                        elif action == "meta":
                            if "conversation" in body:
                                sess["conversation"] = body["conversation"]
                            if "push" in body:
                                sess["push"] = body["push"]
                            if isinstance(body.get("meta"), dict):
                                sess["meta"].update(body["meta"])
                            hub.events.append(("meta", (sess["name"], body)))
                            self._send(200, {"ok": True})
                        elif action == "beat":
                            q = sum(1 for i in hub.inbox if i["agent"] == sess["name"] and not i["delivered"])
                            self._send(200, {"ok": True, "queued": q})
                        elif action == "end":
                            sess["ended"] = True
                            hub.events.append(("end", sess["name"]))
                            hub.wake.notify_all()
                            self._send(200, {"ok": True})
                        else:
                            self._send(404, {"error": "unknown session action"})
                    return

                if p == "/v1/posts":
                    hub.events.append(("post", body))
                    self._send(200, {"post_id": 1, "thread_id": "th_1"})
                    return

                if p == "/v1/tasks":
                    with hub.lock:
                        tid = hub.next_task_id
                        hub.next_task_id += 1
                        task = {"id": tid, "state": "open", **body}
                        hub.tasks[tid] = task
                    self._send(200, {"task": task})
                    return

                if len(parts) == 3 and parts[:2] == ["v1", "tasks"]:
                    tid = int(parts[2])
                    hub.events.append(("task_update", (tid, body)))
                    self._send(200, {"ok": True, "task_id": tid})
                    return

                if len(parts) == 4 and parts[:3] == ["v1", "board", "note"]:
                    name = urllib.parse.unquote(parts[3])
                    hub.notes[name] = {"name": name, "title": body.get("title", name), "body": body.get("body", ""), "version": 1}
                    self._send(200, {"ok": True})
                    return

                self._send(404, {"error": f"not found: {p}"})

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                p = parsed.path
                qs = urllib.parse.parse_qs(parsed.query)
                sess = self._auth_session()
                if not sess:
                    self._send(401, {"error": "invalid or ended session"})
                    return

                parts = p.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "pull":
                    sid = parts[2]
                    if sid != sess["session_id"]:
                        self._send(401, {"error": "session mismatch"})
                        return
                    wait = max(0.0, min(55.0, float((qs.get("wait") or ["0"])[0])))
                    deadline = time.time() + wait
                    with hub.wake:
                        while True:
                            now = time.time()
                            ready = [
                                i for i in hub.inbox
                                if i["agent"] == sess["name"]
                                and not i["delivered"]
                                and (i["lease_until"] <= now)
                            ]
                            if ready or sess.get("ended") or now >= deadline:
                                for i in ready:
                                    i["lease_until"] = now + 60.0
                                items = [
                                    {"id": i["id"], "post_id": i["id"], "kind": "message", "task_id": None, "from": "sa-cc", "ts": "", "text": i["text"]}
                                    for i in ready
                                ]
                                break
                            hub.wake.wait(timeout=min(0.2, max(0.01, deadline - now)))
                    self._send(200, {"items": items})
                    return

                if p == "/v1/whoami":
                    self._send(200, {"agent": sess["name"], "session_id": sess["session_id"], "kind": sess["kind"]})
                    return
                if p == "/v1/agents":
                    with hub.lock:
                        cards = [{"name": s["name"], "kind": s["kind"], "state": s["state"]} for s in hub.sessions.values() if not s["ended"]]
                    self._send(200, {"agents": cards})
                    return
                if p == "/v1/inbox":
                    with hub.lock:
                        ready = [i for i in hub.inbox if i["agent"] == sess["name"] and not i["delivered"]]
                        for i in ready:
                            i["delivered"] = True
                    self._send(200, {"items": [{"id": i["id"], "text": i["text"]} for i in ready]})
                    return
                if p == "/v1/tasks":
                    self._send(200, {"tasks": list(hub.tasks.values())})
                    return
                if len(parts) == 3 and parts[:2] == ["v1", "tasks"]:
                    tid = int(parts[2])
                    self._send(200, {"task": hub.tasks.get(tid, {"id": tid}), "posts": []})
                    return
                if p == "/v1/board":
                    self._send(200, {"secrets": [{"name": k} for k in hub.secrets], "notes": list(hub.notes.values()), "skills": []})
                    return
                if len(parts) == 4 and parts[:3] == ["v1", "board", "secret"]:
                    name = urllib.parse.unquote(parts[3])
                    self._send(200, hub.secrets.get(name, {}))
                    return
                if len(parts) == 4 and parts[:3] == ["v1", "board", "note"]:
                    name = urllib.parse.unquote(parts[3])
                    self._send(200, hub.notes.get(name, {}))
                    return

                self._send(404, {"error": f"not found: {p}"})

        class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = ThreadedServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def enqueue(self, agent: str, text: str) -> int:
        with self.wake:
            iid = self.next_inbox_id
            self.next_inbox_id += 1
            self.inbox.append({"id": iid, "agent": agent, "text": text, "delivered": False, "lease_until": 0.0})
            self.wake.notify_all()
            return iid

    def register_direct(self, name: str, kind: str, cwd: str, push: str, conversation: str = "", meta: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            sid = f"s_{self.next_sid}"
            self.next_sid += 1
            tok = f"hst_{sid}"
            sess = {
                "hub": self.url,
                "session_id": sid,
                "token": tok,
                "name": name,
                "kind": kind,
                "cwd": cwd,
                "conversation": conversation,
                "push": push,
                "meta": dict(meta or {}),
                "state": "idle",
                "ended": False,
                "pid": os.getpid(),
            }
            self.sessions[sid] = sess
            self.tokens[tok] = sid
            return dict(sess)

    def end_session(self, sid: str) -> None:
        with self.wake:
            if sid in self.sessions:
                self.sessions[sid]["ended"] = True
                self.wake.notify_all()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ------------------------------------------------------------------ Minimal mock WS Codex app-server

class MockCodexAppServer:
    """Minimal RFC 6455 WebSocket server mimicking codex app-server JSON-RPC 2.0."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}"
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @staticmethod
    def _ws_send(conn: socket.socket, text: str) -> None:
        payload = text.encode("utf-8")
        hdr = bytearray([0x81])
        n = len(payload)
        if n < 126:
            hdr.append(n)
        elif n < 65536:
            hdr.append(126)
            hdr.extend(struct.pack("!H", n))
        else:
            hdr.append(127)
            hdr.extend(struct.pack("!Q", n))
        conn.sendall(bytes(hdr) + payload)

    @staticmethod
    def _ws_recv(conn: socket.socket) -> str | None:
        def exact(k: int) -> bytes:
            buf = bytearray()
            while len(buf) < k:
                chunk = conn.recv(k - len(buf))
                if not chunk:
                    raise EOFError()
                buf.extend(chunk)
            return bytes(buf)

        try:
            b1, b2 = exact(2)
            opcode = b1 & 0x0F
            if opcode == 0x8:
                return None
            masked = bool(b2 & 0x80)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", exact(8))[0]
            mask = exact(4) if masked else b""
            data = exact(n)
            if masked:
                data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
            return data.decode("utf-8")
        except (EOFError, OSError):
            return None

    def _serve(self) -> None:
        self.sock.settimeout(0.5)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            hdr = b""
            while b"\r\n\r\n" not in hdr:
                c = conn.recv(4096)
                if not c:
                    return
                hdr += c
            key = ""
            for line in hdr.decode("ascii", "ignore").splitlines():
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            acc = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            conn.sendall(
                f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {acc}\r\n\r\n".encode()
            )
            while not self._stop:
                raw = self._ws_recv(conn)
                if raw is None:
                    break
                msg = json.loads(raw)
                self.received.append(msg)
                meth = msg.get("method")
                rid = msg.get("id")
                if meth == "initialize":
                    self._ws_send(conn, json.dumps({"id": rid, "result": {"userAgent": "mock"}}))
                elif meth == "thread/resume":
                    tid = msg["params"]["threadId"]
                    self._ws_send(conn, json.dumps({"id": rid, "result": {"thread": {"id": tid}}}))
                elif meth == "turn/start":
                    tid = msg["params"]["threadId"]
                    self._ws_send(conn, json.dumps({"id": rid, "result": {"turn": {"id": "turn_1", "status": "inProgress"}}}))
                    self._ws_send(conn, json.dumps({"method": "thread/status/changed", "params": {"threadId": tid, "status": {"type": "active", "activeFlags": []}}}))
                    self._ws_send(conn, json.dumps({"method": "turn/started", "params": {"threadId": tid, "turn": {"id": "turn_1"}}}))
                    time.sleep(0.05)
                    self._ws_send(conn, json.dumps({"method": "thread/status/changed", "params": {"threadId": tid, "status": {"type": "idle"}}}))
                    self._ws_send(conn, json.dumps({"method": "turn/completed", "params": {"threadId": tid, "turn": {"id": "turn_1"}}}))
        finally:
            conn.close()

    def close(self) -> None:
        self._stop = True
        self.sock.close()


# ------------------------------------------------------------------ Tests

class AdaptersV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agenthub_v2_test_")
        self._orig_env = {k: os.environ.get(k) for k in ("AGENTHUB_HOME", "T3CODE_HOME", "PI_CODING_AGENT_DIR", "HUB_URL", "HUB_TOKEN")}
        os.environ["AGENTHUB_HOME"] = os.path.join(self.tmp, "agenthub")
        os.environ["T3CODE_HOME"] = os.path.join(self.tmp, "t3")
        os.environ["PI_CODING_AGENT_DIR"] = os.path.join(self.tmp, "pi_agent")
        self.hub = FakeV2Hub()
        os.environ["HUB_URL"] = self.hub.url
        os.environ["HUB_TOKEN"] = ""

    def tearDown(self) -> None:
        self.hub.close()
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 1. start_command & onboarding_prompt & setup
    def test_start_commands_and_onboarding_prompts(self) -> None:
        for kind in ("claude", "agy", "codex", "pi", "custom_cli"):
            cmd = adapters.start_command(kind, "sa-test", "/work/proj", conversation="conv-123", project="agent-hub")
            self.assertIn("/work/proj", cmd)
            prompt = adapters.onboarding_prompt(kind, "sa-test", project="agent-hub", role="adapters")
            self.assertIn('hub_register(name="sa-test", project="agent-hub", role="adapters")', prompt)

        self.assertIn("--resume conv-123", adapters.start_command("claude", "sa-cc", "/ws", conversation="conv-123"))
        self.assertIn("t3 thread start", adapters.start_command("agy", "sa-agy", "/ws", project="agent-hub"))
        self.assertIn("codex --remote", adapters.start_command("codex", "sa-cx", "/ws", conversation="uuid-1"))
        self.assertIn("resume uuid-1", adapters.start_command("codex", "sa-cx", "/ws", conversation="uuid-1"))
        self.assertIn("HUB_AGENT=sa-pi pi --session sess-9", adapters.start_command("pi", "sa-pi", "/ws", conversation="sess-9"))

        liaison_prompt = adapters.onboarding_prompt("agy", "agent-hub-agy", project="agent-hub", role="liaison", liaison=True)
        self.assertIn("人类联络人", liaison_prompt)
        self.assertIn("liaison-agent-hub", liaison_prompt)

    def test_bridge_exponential_backoff(self) -> None:
        sess = self.hub.register_direct("backoff-agy", "agy", "/ws", "bridge", meta={"t3_thread": "t3-1"})
        w = t3_bridge.T3SessionWorker(sess, send_fn=lambda tid, txt: (False, "down"), status_fn=lambda tid: "idle", pull_wait=0)
        seq = []
        for _ in range(7):
            w._fail_count += 1
            seq.append(w._backoff_seconds())
        self.assertEqual(seq, [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0])

    def test_setup_agy_and_pi(self) -> None:
        # Prepare fake T3 antigravity config dir + git workspace
        t3_cfg_dir = os.path.join(os.environ["T3CODE_HOME"], "userdata", "providers", "antigravity", "abc123", "config")
        os.makedirs(t3_cfg_dir, exist_ok=True)
        ws = os.path.join(self.tmp, "repo")
        os.makedirs(ws, exist_ok=True)
        subprocess.run(["git", "-C", ws, "init", "-q"], check=True)

        out = adapters.get("agy").setup(["agenthub"], workspace=ws)
        self.assertTrue(any("hooks.json" in line for line in out))
        with open(os.path.join(ws, ".agents", "hooks.json"), encoding="utf-8") as fh:
            hooks = json.load(fh)
        self.assertIn("PreInvocation", hooks["agent-hub"])
        self.assertIn("Stop", hooks["agent-hub"])
        self.assertLessEqual(hooks["agent-hub"]["Stop"][0]["timeout"], 15)
        with open(os.path.join(t3_cfg_dir, "mcp_config.json"), encoding="utf-8") as fh:
            t3_mcp = json.load(fh)
        self.assertEqual(t3_mcp["mcpServers"]["agent-hub"]["args"], ["mcp", "--kind", "agy"])
        with open(os.path.join(ws, ".git", "info", "exclude"), encoding="utf-8") as fh:
            exclude = fh.read()
        self.assertIn(".agents/hooks.json", exclude)

        pi_out = adapters.get("pi").setup(["agenthub"])
        self.assertTrue(any("agent-hub.ts" in line for line in pi_out))
        self.assertTrue(os.path.isfile(os.path.join(os.environ["PI_CODING_AGENT_DIR"], "extensions", "agent-hub.ts")))

    # 2. Deterministic resolve_t3_thread (multi-thread same project, no guessing)
    def test_resolve_t3_thread_deterministic(self) -> None:
        db_dir = os.path.join(os.environ["T3CODE_HOME"], "userdata")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "statev2.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE orchestration_v2_projection_provider_threads ("
            "id TEXT PRIMARY KEY, thread_id TEXT, payload_json TEXT)"
        )
        rows = [
            ("p1", "t3-liaison", json.dumps({"appThreadId": "t3-liaison", "driver": "antigravity", "nativeThreadRef": {"driver": "antigravity", "nativeId": "cid-liaison", "strength": "strong"}})),
            ("p2", "t3-worker", json.dumps({"appThreadId": "t3-worker", "driver": "antigravity", "nativeThreadRef": {"driver": "antigravity", "nativeId": "cid-worker", "strength": "strong"}})),
        ]
        conn.executemany("INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?, ?)", rows)
        conn.commit()
        conn.close()

        self.assertEqual(common.resolve_t3_thread("cid-liaison"), "t3-liaison")
        self.assertEqual(common.resolve_t3_thread("cid-worker"), "t3-worker")
        self.assertEqual(common.resolve_t3_thread("cid-unknown"), "")
        self.assertEqual(common.resolve_t3_thread(""), "")

    # 3. agy_hook.py (PreInvocation busy + pull?wait=0 + delivered + meta.t3_thread; Stop 0s idle)
    def test_agy_hook_preinvocation_and_zero_sec_stop(self) -> None:
        # Prepare statev2.sqlite mapping
        db_dir = os.path.join(os.environ["T3CODE_HOME"], "userdata")
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(os.path.join(db_dir, "statev2.sqlite"))
        conn.execute("CREATE TABLE orchestration_v2_projection_provider_threads (id TEXT, thread_id TEXT, payload_json TEXT)")
        conn.execute(
            "INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?, ?)",
            ("p1", "t3-thread-42", json.dumps({"appThreadId": "t3-thread-42", "nativeThreadRef": {"driver": "antigravity", "nativeId": "cid-42", "strength": "strong"}})),
        )
        conn.execute(
            "INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?, ?)",
            ("p2", "t3-thread-99", json.dumps({"appThreadId": "t3-thread-99", "nativeThreadRef": {"driver": "antigravity", "nativeId": "cid-99", "strength": "strong"}})),
        )
        conn.commit()
        conn.close()

        sess = self.hub.register_direct("sa-agy", "agy", "/ws", "bridge")
        path = common.write_session_file(sess)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

        iid = self.hub.enqueue("sa-agy", "Task #116 update")
        out = agy_hook.handle_event({"hookEventName": "PreInvocation", "conversationId": "cid-42", "cwd": "/ws"}, agent_arg="sa-agy")
        self.assertIn("injectSteps", out)
        self.assertIn("Task #116 update", out["injectSteps"][0]["systemMessage"]["systemMessage"])

        # Verify deterministic meta.t3_thread synced to local file and hub
        updated = common.read_session_file("sa-agy")
        self.assertEqual((updated or {}).get("meta", {}).get("t3_thread"), "t3-thread-42")
        self.assertEqual(self.hub.sessions[sess["session_id"]]["meta"].get("t3_thread"), "t3-thread-42")
        self.assertIn(("state", ("sa-agy", "busy")), self.hub.events)
        self.assertIn(("delivered", ("sa-agy", [iid])), self.hub.events)

        # Verify Stop hook is strictly 0s non-blocking and reports idle
        t0 = time.time()
        stop_out = agy_hook.handle_event({"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL"}, agent_arg="sa-agy")
        elapsed = time.time() - t0
        self.assertEqual(stop_out, {})
        self.assertLess(elapsed, 1.0)
        self.assertIn(("state", ("sa-agy", "idle")), self.hub.events)

        # Verify Stop hook also binds meta.t3_thread when hub_register happened mid-turn (after PreInvocation)
        sess2 = self.hub.register_direct("brain-agy", "agy", "/ws-brain", "bridge")
        common.write_session_file(sess2)
        stop_out2 = agy_hook.handle_event(
            {"hookEventName": "Stop", "conversationId": "cid-99", "workspacePaths": ["/ws-brain"], "terminationReason": "NO_TOOL_CALL"},
            agent_arg="",
        )
        self.assertEqual(stop_out2, {})
        updated2 = common.read_session_file("brain-agy")
        self.assertEqual((updated2 or {}).get("meta", {}).get("t3_thread"), "t3-thread-99")
        self.assertEqual(self.hub.sessions[sess2["session_id"]]["meta"].get("t3_thread"), "t3-thread-99")

    # 4. t3_bridge.py (delivers with t3_thread; skips without t3_thread and NEVER marks delivered)
    def test_t3_bridge_delivery_and_skip_without_thread(self) -> None:
        # Case A: session with deterministic meta.t3_thread
        sess_a = self.hub.register_direct("liaison-agy", "agy", "/ws", "bridge", meta={"t3_thread": "t3-99"})
        common.write_session_file(sess_a)
        iid_a = self.hub.enqueue("liaison-agy", "Please review PR #7")

        sent: list[tuple[str, str]] = []
        w_a = t3_bridge.T3SessionWorker(
            sess_a,
            send_fn=lambda tid, txt: (sent.append((tid, txt)) or True, "ok"),
            status_fn=lambda tid: "idle",
            pull_wait=0,
        )
        self.assertTrue(w_a.poll_once())
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "t3-99")
        self.assertIn("Please review PR #7", sent[0][1])
        self.assertIn(("delivered", ("liaison-agy", [iid_a])), self.hub.events)

        # Case B: standalone agy CLI with NO t3_thread -> skipped, item stays undelivered!
        sess_b = self.hub.register_direct("cli-agy", "agy", "/ws", "bridge", meta={})
        common.write_session_file(sess_b)
        iid_b = self.hub.enqueue("cli-agy", "Keep in inbox")

        w_b = t3_bridge.T3SessionWorker(
            sess_b,
            send_fn=lambda tid, txt: (True, "ok"),
            status_fn=lambda tid: "idle",
            pull_wait=0,
        )
        self.assertTrue(w_b.poll_once())
        item_b = [i for i in self.hub.inbox if i["id"] == iid_b][0]
        self.assertFalse(item_b["delivered"])
        self.assertEqual(item_b["lease_until"], 0.0)

    # 5. codex_bridge.py (real WS JSON-RPC handshake + turn/start + busy/idle state reporting)
    def test_codex_bridge_ws_delivery_and_state(self) -> None:
        mock_srv = MockCodexAppServer()
        rpc = codex_bridge.CodexRPCClient(endpoint=mock_srv.url)
        try:
            rpc.connect()
            sess = self.hub.register_direct("sa-codex", "codex", "/ws", "bridge", conversation="cx-thread-1")
            common.write_session_file(sess)
            iid = self.hub.enqueue("sa-codex", "Run unit tests")

            w = codex_bridge.CodexSessionWorker(sess, rpc, pull_wait=0)
            self.assertTrue(w.poll_once())
            time.sleep(0.2)

            methods = [m.get("method") for m in mock_srv.received]
            self.assertIn("initialize", methods)
            self.assertIn("thread/resume", methods)
            self.assertIn("turn/start", methods)
            self.assertIn(("delivered", ("sa-codex", [iid])), self.hub.events)
            self.assertIn(("state", ("sa-codex", "busy")), self.hub.events)
            self.assertIn(("state", ("sa-codex", "idle")), self.hub.events)
        finally:
            rpc.close()
            mock_srv.close()

    # 6. pi extension (agent-hub.ts): 9 tools + secret 0600 temp file + live pull push + busy/idle/end
    def test_pi_extension_tools_and_push(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        pi_pkg = os.environ.get("PI_PKG") or os.path.expanduser(
            "~/.npm-global/lib/node_modules/@earendil-works/pi-coding-agent")
        if not os.path.isdir(os.path.join(pi_pkg, "node_modules", "jiti")):
            self.skipTest("pi not installed (set PI_PKG to its package dir)")
        ext_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agenthub", "node", "pi", "agent-hub.ts")

        # Run a small Node harness (using pi's installed jiti/typebox or ts strip in node 22)
        harness = r"""
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

// Load extension via pi's jiti so TypeScript works identically to `pi` runtime.
const piBase = process.env.PI_PKG || (process.env.HOME + "/.npm-global/lib/node_modules/@earendil-works/pi-coding-agent");
const jitiMod = require(path.join(piBase, "node_modules", "jiti"));
const createJiti = jitiMod.createJiti || jitiMod;
const typeboxPath = require.resolve("typebox", { paths: [piBase, path.join(piBase, "node_modules", "@earendil-works", "pi-ai")] });
const jiti = createJiti(process.env.EXT_PATH, {
  alias: {
    "typebox": typeboxPath,
  },
});

(async () => {
  const mod = await jiti.import(process.env.EXT_PATH);
  const factory = mod.default || mod;
  const handlers = {};
  const tools = {};
  const injected = [];

  const mockPi = {
    on(name, fn) { (handlers[name] ||= []).push(fn); },
    registerTool(def) { tools[def.name] = def; },
    sendUserMessage(content, opts) { injected.push({ content, opts }); },
  };

  factory(mockPi);
  const toolNames = Object.keys(tools).sort();
  console.log("TOOLS=" + JSON.stringify(toolNames));

  const ctx = {
    cwd: process.cwd(),
    hasUI: false,
    sessionManager: {
      getSessionId: () => "pi-sess-77",
      getSessionFile: () => "/tmp/pi-sess-77.jsonl",
    },
  };
  for (const fn of handlers.session_start || []) await fn({ reason: "startup" }, ctx);

  // Test hub_register
  const reg = await tools.hub_register.execute("c1", { name: "sa-pi", project: "agent-hub", role: "adapters" });
  console.log("REG=" + JSON.stringify(JSON.parse(reg.content[0].text)));

  // Test hub_board secret get -> must NOT contain secret value, must return 0600 env file
  const sec = await tools.hub_board.execute("c2", { action: "get", type: "secret", name: "demo-key" });
  console.log("SEC=" + JSON.stringify(JSON.parse(sec.content[0].text)));

  // Test agent_start / agent_end -> busy / idle
  for (const fn of handlers.agent_start || []) await fn({}, ctx);
  for (const fn of handlers.agent_end || []) await fn({}, ctx);

  // Wait briefly for background pull loop to pick up enqueued message
  for (let i = 0; i < 40 && injected.length === 0; i++) {
    await new Promise(r => setTimeout(r, 100));
  }
  console.log("INJECTED=" + JSON.stringify(injected));

  // Shutdown -> /end + delete session file
  for (const fn of handlers.session_shutdown || []) await fn({}, ctx);
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
"""
        env = dict(os.environ)
        env["EXT_PATH"] = ext_path
        env["HUB_PI_POLL_WAIT"] = "1"
        for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            env.pop(k, None)

        # Enqueue a message for sa-pi right after it registers
        def feeder() -> None:
            for _ in range(50):
                if any(s["name"] == "sa-pi" for s in self.hub.sessions.values()):
                    self.hub.enqueue("sa-pi", "Hello pi from hub!")
                    return
                time.sleep(0.05)

        threading.Thread(target=feeder, daemon=True).start()
        r = subprocess.run([node, "-e", harness], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=f"stderr={r.stderr}\nstdout={r.stdout}")

        out_lines = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in r.stdout.splitlines() if "=" in line}
        tool_names = json.loads(out_lines["TOOLS"])
        self.assertEqual(
            tool_names,
            ["hub_agents", "hub_board", "hub_close", "hub_delegate", "hub_inbox", "hub_register", "hub_send", "hub_tasks", "hub_update"],
        )
        # Secret must NOT leak into tool output!
        self.assertNotIn("SUPER_SECRET_VALUE", out_lines["SEC"])
        sec_obj = json.loads(out_lines["SEC"])
        self.assertTrue(os.path.isfile(sec_obj["env_file"]))
        self.assertEqual(os.stat(sec_obj["env_file"]).st_mode & 0o777, 0o600)

        # Verify background pull injected message & reported busy/idle/delivered/end
        injected = json.loads(out_lines["INJECTED"])
        self.assertEqual(len(injected), 1)
        self.assertIn("Hello pi from hub!", injected[0]["content"])
        self.assertIn(("state", ("sa-pi", "busy")), self.hub.events)
        self.assertIn(("state", ("sa-pi", "idle")), self.hub.events)
        self.assertTrue(any(ev[0] == "delivered" and ev[1][0] == "sa-pi" for ev in self.hub.events))
        self.assertIn(("end", "sa-pi"), self.hub.events)
        # Session file cleaned up on shutdown
        self.assertIsNone(common.read_session_file("sa-pi"))


if __name__ == "__main__":
    unittest.main()
