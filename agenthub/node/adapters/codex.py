"""OpenAI Codex CLI.

Tools come from `agenthub mcp --kind codex`, registered with `codex mcp add`.
Codex has no way to inject events into a running session, so push goes
through the `agenthub run codex` wrapper, which types into its terminal.
Conversations are its rollout files (~/.codex/sessions/**/rollout-*-<uuid>.jsonl),
resumed with `codex resume <uuid>`.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import time

from .base import Adapter, _short, mcp_command

CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")


class Codex(Adapter):
    kind = "codex"
    title = "OpenAI Codex CLI"
    binary = "codex"
    tools = "mcp"
    # the composer prompt; while working the status line says "esc to interrupt"
    ready_markers = ("› ",)
    busy_markers = ("esc to interrupt",)
    blockers = Adapter.blockers + ("Would you like to", "Yes, proceed", "Do you trust the contents",
                                   "Allow Codex to")
    dialogs = (
        ("Do you trust the contents of this directory", "Yes, continue"),
        ("Update available!", "Skip"),
    )
    # unattended agents should not stop for every command: sandboxed, asks only when it must
    default_args = ("--ask-for-approval", "on-request", "--sandbox", "workspace-write")
    instructions_file = os.path.join(CODEX_HOME, "AGENTS.md")
    skills_dir = os.path.join(CODEX_HOME, "skills")
    resume_hint = "codex resume <session uuid>"

    def argv(self, resume=None, prompt=None, extra=()):
        cmd = ["codex"]
        if resume:
            cmd += ["resume", resume]
        cmd += self.extra_args() + list(extra)
        if prompt:
            cmd.append(prompt)
        return cmd

    def _rollout(self, conv):
        hits = glob.glob(os.path.join(CODEX_HOME, "sessions", "*", "*", "*", "rollout-*%s.jsonl" % conv))
        return hits[0] if hits else None

    def resumable(self, cwd, conv):
        if not conv or not re.match(r"^[0-9a-f\-]{36}$", conv):
            return False, "no previous session"
        path = self._rollout(conv)
        if not path:
            return False, "previous session not on this node"
        if time.time() - os.stat(path).st_mtime > 7 * 86400:
            return False, "previous session older than 7 days"
        return True, ""

    def find_conversation(self, pid, cwd, since):
        # Linux: the rollout file codex keeps open
        fd_dir = "/proc/%d/fd" % pid
        if os.path.isdir(fd_dir):
            for pid_ in [pid] + _children(pid):
                try:
                    for f in os.listdir("/proc/%d/fd" % pid_):
                        target = os.readlink("/proc/%d/fd/%s" % (pid_, f))
                        m = UUID.search(target)
                        if m and "rollout-" in target:
                            return m.group(1)
                except OSError:
                    continue
        # elsewhere: the newest rollout started after us in this directory
        best = None
        for path in glob.glob(os.path.join(CODEX_HOME, "sessions", "*", "*", "*", "rollout-*.jsonl")):
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < since:
                continue
            try:
                with open(path) as fh:
                    meta = json.loads(fh.readline()).get("payload") or {}
            except (OSError, ValueError):
                continue
            if meta.get("cwd") and os.path.realpath(meta["cwd"]) != os.path.realpath(cwd):
                continue
            if not best or st.st_mtime > best[0]:
                best = (st.st_mtime, path)
        if best:
            m = UUID.search(best[1])
            return m.group(1) if m else ""
        return ""

    def setup(self, agenthub_cmd, **_):
        out = []
        if not self.available():
            return ["codex not on PATH; skipped"]
        cmd = mcp_command(agenthub_cmd, "codex")
        subprocess.run(["codex", "mcp", "remove", "agent-hub"], capture_output=True, text=True, timeout=60)
        env = ["--env", "AGENTHUB_HOME=%s" % os.environ["AGENTHUB_HOME"]] if os.environ.get("AGENTHUB_HOME") else []
        r = subprocess.run(["codex", "mcp", "add", "agent-hub"] + env + ["--"] + cmd, capture_output=True, text=True,
                           timeout=60)
        out.append("codex: MCP server agent-hub -> %s" % " ".join(cmd) if r.returncode == 0 else
                   "codex mcp add failed: %s" % (r.stderr or r.stdout).strip()[-300:])
        return out + Adapter.setup(self, agenthub_cmd)

    def teardown(self):
        out = []
        if self.available():
            r = subprocess.run(["codex", "mcp", "remove", "agent-hub"], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                out.append("codex: MCP server agent-hub removed")
        return out + Adapter.teardown(self)


def _children(pid):
    out = []
    try:
        with open("/proc/%d/task/%d/children" % (pid, pid)) as fh:
            out = [int(x) for x in fh.read().split()]
    except (OSError, ValueError):
        pass
    return out


__all__ = ["Codex", "shutil", "_short"]
