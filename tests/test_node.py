import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from util import AGENTHUB, ROOT, Hub

sys.path.insert(0, ROOT)


def read(p):
    with open(p) as fh:
        return fh.read()

FAKE = r'''
import os, sys
log = open(sys.argv[1], "a")
while True:
    sys.stdout.write("\n> "); sys.stdout.flush()
    try:
        line = input()
    except EOFError:
        break
    log.write(line + "\n"); log.flush()
    if line.strip() == "quit":
        break
'''


@unittest.skipUnless(os.name == "posix", "needs a pty")
class WrapperTest(unittest.TestCase):
    """`agenthub run` types hub events into an agent's terminal, and the agent's tools find
    the session through the wrapper's run file."""

    @classmethod
    def setUpClass(cls):
        cls.hub = Hub(extra_env={"HUB_PTY_QUIET": "0.5", "HUB_PTY_TYPING_QUIET": "0"})
        cls.fake = os.path.join(cls.hub.tmp, "fake.py")
        with open(cls.fake, "w") as fh:
            fh.write(FAKE)

    @classmethod
    def tearDownClass(cls):
        cls.hub.stop()

    def test_push_is_typed_in_and_identity_found(self):
        log = os.path.join(self.hub.tmp, "typed.log")
        proc = subprocess.Popen(AGENTHUB + ["run", "generic", "--agent", "erin", "--", sys.executable, self.fake, log],
                                env=self.hub.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, cwd=self.hub.tmp)
        try:
            for _ in range(50):
                if self.hub.call("GET", "/v1/agents/erin")[1].get("presence") == "live":
                    break
                time.sleep(0.2)
            self.assertEqual(self.hub.call("GET", "/v1/agents/erin")[1]["kind"], "generic")
            self.hub.call("POST", "/v1/messages", {"agent": "alice", "to": "erin", "body": "line one\nline two"})
            for _ in range(60):
                if os.path.exists(log) and "line one" in read(log):
                    break
                time.sleep(0.25)
            typed = read(log)
            self.assertIn("Message from alice", typed)
            self.assertIn("line one line two", typed)  # no bracketed paste: newlines become spaces
            # delivered = acknowledged after typing
            for _ in range(40):
                if self.hub.call("GET", "/v1/agents/erin")[1]["pending"] == 0:
                    break
                time.sleep(0.25)
            self.assertEqual(self.hub.call("GET", "/v1/agents/erin")[1]["pending"], 0)
        finally:
            proc.terminate()
            proc.wait(10)

    def test_tools_inside_the_wrapper_know_who_they_are(self):
        script = os.path.join(self.hub.tmp, "who.sh")
        out = os.path.join(self.hub.tmp, "who.out")
        with open(script, "w") as fh:
            # an agent that strips the environment before running tools (as Codex does)
            fh.write("env -u HUB_SESSION -u HUB_AGENT -u HUB_WRAPPER_PID %s whoami > %s 2>&1\n"
                     % (" ".join(AGENTHUB), out))
        r = subprocess.run(AGENTHUB + ["run", "generic", "--agent", "fred", "--", "sh", script], env=self.hub.env,
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=60, cwd=self.hub.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(read(out).startswith("fred "), read(out))


class InstructionsTest(unittest.TestCase):
    def test_upsert_is_idempotent_and_removable(self):
        from agenthub.node import instructions
        d = tempfile.mkdtemp()
        p = os.path.join(d, "AGENTS.md")
        with open(p, "w") as fh:
            fh.write("# My rules\n\nBe nice.\n")
        self.assertTrue(instructions.upsert(p))
        self.assertFalse(instructions.upsert(p))
        text = read(p)
        self.assertIn("Be nice.", text)
        self.assertEqual(text.count(instructions.START), 1)
        self.assertTrue(instructions.upsert(p, cli=True))  # switching to the CLI flavour rewrites the block
        self.assertIn("agenthub update", read(p))
        self.assertTrue(instructions.remove(p))
        self.assertEqual(read(p).strip(), "# My rules\n\nBe nice.")


class AdapterTest(unittest.TestCase):
    def test_json_config_merge_keeps_user_settings(self):
        from agenthub.node.adapters import others
        home = tempfile.mkdtemp()
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            os.makedirs(os.path.join(home, ".gemini"))
            with open(os.path.join(home, ".gemini", "settings.json"), "w") as fh:
                json.dump({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}, fh)
            others.Gemini().setup(["/bin/agenthub"])
            d = json.loads(read(os.path.join(home, ".gemini", "settings.json")))
            self.assertEqual(d["theme"], "dark")
            self.assertEqual(d["mcpServers"]["agent-hub"]["args"], ["mcp", "--kind", "gemini"])
            self.assertIn("other", d["mcpServers"])
            others.Gemini().teardown()
            self.assertNotIn("agent-hub", json.loads(read(os.path.join(home, ".gemini", "settings.json")))["mcpServers"])
        finally:
            os.environ["HOME"] = old

    def test_ready_detection(self):
        from agenthub.node.adapters import get
        claude, codex = get("claude"), get("codex")
        self.assertTrue(claude.ready("foo\n❯ \n  ⏵⏵ auto mode on (shift+tab to cycle)\n"))
        self.assertFalse(claude.ready("Do you want to proceed?\n❯ 1. Yes\nEsc to cancel\n"))
        self.assertTrue(codex.ready("› Write tests for @filename\n  gpt-5.5 default · ~/x\n"))
        self.assertFalse(codex.ready("• Working (3s • esc to interrupt)\n› \n"))
        self.assertFalse(codex.ready("Would you like to run the following command?\n› 1. Yes, proceed (y)\n"))

    def test_custom_kinds_from_agents_json(self):
        home = tempfile.mkdtemp()
        with open(os.path.join(home, "agents.json"), "w") as fh:
            json.dump({"aider": {"command": "aider --no-auto-commits", "ready": ["> "]}}, fh)
        r = subprocess.run(AGENTHUB + ["kinds"], env=dict(os.environ, AGENTHUB_HOME=home), capture_output=True,
                           text=True, timeout=30)
        self.assertIn("aider", r.stdout)

    def test_claude_argv(self):
        from agenthub.node.adapters import get
        os.environ.pop("HUB_CLAUDE_PUSH", None)
        argv = get("claude").argv(resume="abc", prompt="hi")
        self.assertIn("--dangerously-load-development-channels", argv)
        self.assertEqual(argv[-3:], ["--resume", "abc", "hi"])


class BundleTest(unittest.TestCase):
    def test_pyz_runs(self):
        from agenthub.hub import bundle
        d = tempfile.mkdtemp()
        p = os.path.join(d, "agenthub.pyz")
        with open(p, "wb") as fh:
            fh.write(bundle.pyz())
        r = subprocess.run([sys.executable, p, "--version"], capture_output=True, text=True, timeout=30)
        self.assertIn("agenthub", r.stdout)
        r = subprocess.run([sys.executable, p, "kinds"], capture_output=True, text=True, timeout=30)
        self.assertIn("codex", r.stdout)


if __name__ == "__main__":
    unittest.main()
