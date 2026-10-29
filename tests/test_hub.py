import threading
import time
import unittest

from util import Hub


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = Hub()

    @classmethod
    def tearDownClass(cls):
        cls.hub.stop()

    def test_public_and_private_paths(self):
        h = self.hub
        self.assertEqual(h.call("GET", "/healthz", token="")[0], 200)
        code, cfg = h.call("GET", "/v1/config", token="")
        self.assertEqual(code, 200)
        self.assertFalse(cfg["authenticated"])
        for path in ("/v1/vault", "/v1/agents", "/v1/tasks", "/v1/shared", "/marketplace.git/info/refs"):
            self.assertEqual(h.call("GET", path, token="")[0], 401, path)
        self.assertEqual(h.call("GET", "/v1/vault", token="wrong")[0], 401)
        self.assertEqual(h.call("GET", "/v1/vault")[0], 200)

    def test_node_tokens_name_the_node(self):
        h = self.hub
        code, out = h.call("POST", "/v1/admin/nodes", {"name": "gpu-01"})
        self.assertEqual(code, 200)
        tok = out["token"]
        code, me = h.call("GET", "/v1/whoami", token=tok)
        self.assertEqual((code, me["you"]["node"], me["you"]["role"]), (200, "gpu-01", "node"))
        # a node token cannot mint more tokens
        self.assertEqual(h.call("POST", "/v1/admin/nodes", {"name": "evil"}, token=tok)[0], 403)
        # whatever it claims, a node is the node its token names
        code, out = h.call("POST", "/v1/agents/register", {"name": "gpu-agent", "cwd": "/w"}, token=tok)
        self.assertEqual(out["agent"]["node"], "gpu-01")
        self.assertEqual(h.call("DELETE", "/v1/admin/nodes/gpu-01")[0], 200)
        self.assertEqual(h.call("GET", "/v1/whoami", token=tok)[0], 401)

    def test_login_sets_cookie(self):
        import urllib.request
        import json
        req = urllib.request.Request(self.hub.url + "/v1/login", json.dumps({"token": self.hub.admin}).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            self.assertIn("agenthub_token=", r.headers["Set-Cookie"])
            self.assertIn("HttpOnly", r.headers["Set-Cookie"])


class DelegationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = Hub()

    @classmethod
    def tearDownClass(cls):
        cls.hub.stop()

    def open(self, sid, name, kind="codex"):
        code, out = self.hub.call("POST", "/v1/sessions", {"session": sid, "cwd": "/w/" + name, "channel": True,
                                                            "agent": name, "kind": kind})
        self.assertEqual(code, 200, out)
        return out

    def test_task_round_trip_with_acknowledged_delivery(self):
        h = self.hub
        self.open("s-alice", "alice")
        self.open("s-bob", "bob", kind="pi")
        code, t = h.call("POST", "/v1/tasks", {"agent": "alice", "to": "bob", "title": "Add a test",
                                               "goal": "g", "context": "c", "acceptance": "a"})
        self.assertEqual(code, 200)
        tid = t["id"]
        code, out = h.call("GET", "/v1/sessions/s-bob/pull?wait=0")
        items = out["items"]
        self.assertEqual([i["kind"] for i in items], ["task"])
        self.assertIn("New task #%d from alice" % tid, items[0]["content"])
        # not acknowledged yet: still pending, not handed out twice
        self.assertEqual(h.call("GET", "/v1/sessions/s-bob/pull?wait=0")[1]["items"], [])
        h.call("GET", "/v1/sessions/s-bob/pull?wait=0&ack=%d" % items[0]["id"])
        self.assertEqual(h.call("GET", "/v1/agents/bob")[1]["pending"], 0)
        self.assertEqual(h.call("GET", "/v1/agents/bob")[1]["kind"], "pi")
        for kind, state in (("plan", "in_progress"), ("result", "review")):
            code, t = h.call("POST", "/v1/tasks/%d" % tid, {"agent": "bob", "kind": kind, "body": kind})
            self.assertEqual(t["state"], state)
        kinds = [i["kind"] for i in h.call("GET", "/v1/sessions/s-alice/pull?wait=0")[1]["items"]]
        self.assertEqual(kinds, ["plan", "result"])
        code, t = h.call("POST", "/v1/tasks/%d" % tid, {"agent": "alice", "state": "done"})
        self.assertEqual(t["state"], "done")

    def test_role_address_and_unrouted(self):
        h = self.hub
        self.open("s-carol", "carol")
        h.call("POST", "/v1/agents/carol/scopes", {"add": ["shop/frontend"]})
        code, t = h.call("POST", "/v1/tasks", {"agent": "alice", "to": "frontend@shop", "title": "Fix CSS"})
        self.assertEqual(t["role"], "frontend")
        items = h.call("GET", "/v1/sessions/s-carol/pull?wait=0")[1]["items"]
        self.assertTrue(any("hub_claim(%d)" % t["id"] in i["content"] for i in items))
        code, t = h.call("POST", "/v1/tasks", {"agent": "alice", "to": "nobody@shop", "title": "x"})
        self.assertEqual(code, 200)
        self.assertTrue(any(i["type"] == "unrouted" for i in h.call("GET", "/v1/overview")[1]["attention"]))

    def test_session_end_is_not_undone_by_a_waiting_poll(self):
        h = self.hub
        self.open("s-dave", "dave")
        done = {}

        def poll():
            done["out"] = h.call("GET", "/v1/sessions/s-dave/pull?wait=10")
        th = threading.Thread(target=poll)
        th.start()
        time.sleep(0.5)
        h.call("POST", "/v1/sessions/s-dave/end", {})
        th.join(12)
        self.assertEqual(h.call("GET", "/v1/agents/dave")[1]["presence"] != "live", True)

    def test_vault_and_notes(self):
        h = self.hub
        code, out = h.call("POST", "/v1/vault/openai", {"agent": "alice", "value": '{"api_key": "sk-test"}',
                                                         "note": "OpenAI key", "env": {"OPENAI_API_KEY": "api_key"}})
        self.assertEqual(code, 200, out)
        code, out = h.call("GET", "/v1/vault/openai/env?agent=bob")
        self.assertEqual(out["lines"], ["export OPENAI_API_KEY=sk-test"])
        shared = h.call("GET", "/v1/shared")[1]
        self.assertNotIn("sk-test", str(shared))
        code, out = h.call("POST", "/v1/notes/openai-howto", {"agent": "alice", "title": "How", "body": "use it",
                                                               "refs": ["openai"]})
        self.assertEqual(code, 200, out)


class OpenModeTest(unittest.TestCase):
    def test_open_mode_refuses_a_public_bind(self):
        import subprocess
        from util import AGENTHUB, free_port
        import os
        import tempfile
        env = dict(os.environ, HUB_DATA=tempfile.mkdtemp(), HUB_IDENTITY="open")
        env.pop("HUB_I_KNOW_ITS_OPEN", None)
        r = subprocess.run(AGENTHUB + ["serve", "--host", "0.0.0.0", "--port", str(free_port())], env=env,
                           capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
