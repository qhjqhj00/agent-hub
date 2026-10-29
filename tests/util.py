"""Shared test helpers: a real hub in a subprocess, with its own data dir and port."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTHUB = [sys.executable, os.path.join(ROOT, "bin", "agenthub")]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Hub:
    def __init__(self, identity="token", extra_env=None):
        self.tmp = tempfile.mkdtemp(prefix="agenthub-test-")
        self.port = free_port()
        self.url = "http://127.0.0.1:%d" % self.port
        self.env = dict(os.environ, HUB_DATA=os.path.join(self.tmp, "hub"), AGENTHUB_HOME=os.path.join(self.tmp, "home"),
                        HUB_IDENTITY=identity, HUB_URL=self.url, HOME=self.tmp)
        for k in ("CLAUDE_CODE_SESSION_ID", "HUB_SESSION", "HUB_AGENT", "HUB_WRAPPER_PID", "TELEGRAM_BOT_TOKEN",
                  "HUB_WEBHOOK_URL", "HUB_TOKEN"):
            self.env.pop(k, None)
        self.env.update(extra_env or {})
        self.proc = subprocess.Popen(AGENTHUB + ["serve", "--port", str(self.port)], env=self.env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        end = time.time() + 15
        while time.time() < end:
            try:
                urllib.request.urlopen(self.url + "/healthz", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("hub did not start: %s" % self.proc.stdout.read(2000))
        with open(os.path.join(self.tmp, "hub", "admin.token")) as fh:
            self.admin = fh.read().strip()
        # clients in this test talk to this hub as the admin
        os.makedirs(self.env["AGENTHUB_HOME"], exist_ok=True)
        with open(os.path.join(self.env["AGENTHUB_HOME"], "config.json"), "w") as fh:
            json.dump({"url": self.url, "token": self.admin}, fh)

    def call(self, method, path, body=None, token=None):
        """(status, json) -- never raises on HTTP errors."""
        req = urllib.request.Request(self.url + path, method=method)
        tok = self.admin if token is None else token
        if tok:
            req.add_header("Authorization", "Bearer " + tok)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data, timeout=30) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def cli(self, *args, **kw):
        return subprocess.run(AGENTHUB + list(args), env=dict(self.env, **kw.pop("env", {})), capture_output=True,
                              text=True, timeout=60, **kw)

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
