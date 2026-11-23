"""The MCP server against a real hub: tools, registration, session file, channel push, ending on exit."""
import json
import os
import subprocess
import time
import unittest

from util import AGENTHUB, Hub


class MCP:
    def __init__(self, hub, env):
        self.p = subprocess.Popen(AGENTHUB + ["mcp"], env=dict(hub.env, **env), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.n = 0

    def rpc(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        while True:
            m = json.loads(self.p.stdout.readline())
            if m.get("id") == self.n:
                return m["result"]
            self.notes.append(m)

    notes = []

    def note(self, timeout=15):
        """The next notification (channel push)."""
        import select
        end = time.time() + timeout
        while time.time() < end:
            if MCP.notes:
                return MCP.notes.pop(0)
            r, _, _ = select.select([self.p.stdout], [], [], 0.5)
            if r:
                m = json.loads(self.p.stdout.readline())
                if "id" not in m:
                    return m
        return None

    def tool(self, _tool, **args):
        r = self.rpc("tools/call", {"name": _tool, "arguments": args})
        return r["content"][0]["text"], r["isError"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)


class MCPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = Hub()

    @classmethod
    def tearDownClass(cls):
        cls.hub.stop()

    def test_tools_register_and_session_file(self):
        m = MCP(self.hub, {"HUB_KIND": "codex"})
        init = m.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertIn("no hub identity", init["instructions"])
        names = [t["name"] for t in m.rpc("tools/list")["tools"]]
        self.assertEqual(len(names), 9, names)
        text, err = m.tool("hub_register", name="cx1", project="pp", role="be")
        self.assertFalse(err, text)
        path = os.path.join(self.hub.env["AGENTHUB_HOME"], "sessions", "cx1.json")
        sess = json.load(open(path))
        self.assertEqual((sess["kind"], sess["push"]), ("codex", "bridge"))
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        text, err = m.tool("hub_agents")
        self.assertIn("cx1", text)
        m.close()
        # stdin closed = the agent exited: session ended, file gone, name free right away
        self.assertFalse(os.path.exists(path))
        code, card = self.hub.call("GET", "/v1/agents/cx1")
        self.assertNotEqual(card["presence"], "live")

    def test_agy_session_bound_to_t3_thread_survives_exit_and_is_adopted(self):
        env = {"HUB_KIND": "agy", "ANTIGRAVITY_CONVERSATION_ID": "conv-t3a", "CLAUDE_PROJECT_DIR": "/w/t3a"}
        m = MCP(self.hub, env)
        m.rpc("initialize", {"protocolVersion": "2025-06-18"})
        text, err = m.tool("hub_register", name="t3a-agy")
        self.assertFalse(err, text)
        path = os.path.join(self.hub.env["AGENTHUB_HOME"], "sessions", "t3a-agy.json")
        sess = json.load(open(path))
        sess["meta"] = {"t3_thread": "thread-t3a"}  # what agy-hook writes after the first turn
        with open(path, "w") as fh:
            json.dump(sess, fh)
        m.close()  # T3 reaped the idle agy: stdin EOF
        self.assertTrue(os.path.exists(path))
        code, _ = self.hub.call("POST", "/v1/sessions/%s/beat" % sess["session_id"], {}, session=sess["token"])
        self.assertEqual(code, 200)
        # T3 wakes the thread: a new agy, a new MCP server, no hub_register needed
        m2 = MCP(self.hub, env)
        m2.rpc("initialize", {"protocolVersion": "2025-06-18"})
        text, err = m2.tool("hub_send", to="t3a-agy", body="still me")
        self.assertFalse(err, text)
        self.assertEqual(json.load(open(path))["session_id"], sess["session_id"])
        text, err = m2.tool("hub_register", name="t3a-agy")  # re-registering in place is not a 409
        self.assertFalse(err, text)
        m2.close()

    def test_agy_session_without_t3_thread_ends_on_exit(self):
        m = MCP(self.hub, {"HUB_KIND": "agy", "ANTIGRAVITY_CONVERSATION_ID": "conv-t3b", "CLAUDE_PROJECT_DIR": "/w/t3b"})
        m.rpc("initialize", {"protocolVersion": "2025-06-18"})
        text, err = m.tool("hub_register", name="t3b-agy")
        self.assertFalse(err, text)
        path = os.path.join(self.hub.env["AGENTHUB_HOME"], "sessions", "t3b-agy.json")
        self.assertTrue(os.path.exists(path))
        m.close()
        self.assertFalse(os.path.exists(path))
        code, card = self.hub.call("GET", "/v1/agents/t3b-agy")
        self.assertNotEqual(card["presence"], "live")

    def test_channel_push_and_delivered(self):
        m = MCP(self.hub, {"HUB_KIND": "claude", "HUB_CHANNEL": "1", "HUB_AGENT": "cc1"})
        init = m.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertIn("claude/channel", init["capabilities"].get("experimental", {}))
        self.assertIn("**cc1**", init["instructions"])
        m.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        m.p.stdin.flush()
        other = MCP(self.hub, {"HUB_KIND": "other"})
        other.rpc("initialize", {})
        other.tool("hub_register", name="sender1")
        text, err = other.tool("hub_send", to="cc1", body="ping from sender1")
        self.assertFalse(err, text)
        n = m.note()
        self.assertIsNotNone(n, "nothing pushed")
        self.assertEqual(n["method"], "notifications/claude/channel")
        self.assertIn("ping from sender1", n["params"]["content"])
        self.assertEqual(n["params"]["meta"]["from"], "sender1")
        time.sleep(1)
        code, card = self.hub.call("GET", "/v1/agents/cc1")
        self.assertEqual(card["pending"], 0)
        other.close()
        m.close()

    def test_secret_value_stays_out_of_the_conversation(self):
        self.hub.call("POST", "/v1/board/secret/tok", {"value": "zz-secret-zz", "env": {"TOK": ""}})
        m = MCP(self.hub, {"HUB_KIND": "other", "HUB_AGENT": "reader1"})
        m.rpc("initialize", {})
        text, err = m.tool("hub_board", action="get", type="secret", name="tok")
        self.assertFalse(err, text)
        self.assertNotIn("zz-secret-zz", text)
        path = text.split(" to ")[1].split(" ")[0]
        self.assertIn("zz-secret-zz", open(path).read())
        os.remove(path)
        m.close()

    def test_cli_team_commands(self):
        r = self.hub.cli("register", "sh1", "--kind", "pi", "--project", "cp")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.hub.cli("send", "sh1", "hello shell")  # as the human (admin token)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.hub.cli("inbox", env={"HUB_AGENT": "sh1"})
        self.assertIn("hello shell", r.stdout)
        r = self.hub.cli("start", "sh1")
        self.assertTrue(r.stdout.strip())


if __name__ == "__main__":
    unittest.main()
