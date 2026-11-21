"""OpenAI Codex CLI adapter (v2).

Push:
- External bridge (`agenthub codex-bridge`) connects to the shared local
  `codex app-server` daemon via JSON-RPC 2.0, listens to `thread/status/changed`,
  and sends `turn/start` / `turn/steer`.
- Humans attach simultaneously via `codex --remote unix://~/.codex/app-server-control/app-server-control.sock`.

Tools:
- Native MCP (`codex mcp add agent-hub -- agenthub mcp --kind codex`).
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
from typing import Any

from .base import Adapter, mcp_command

CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")


class Codex(Adapter):
    kind = "codex"
    title = "OpenAI Codex CLI"
    binary = "codex"
    push = "bridge"
    tools = "mcp"

    def start_command(
        self,
        name: str,
        workspace: str,
        conversation: str = "",
        project: str = "",
    ) -> str:
        _ = name, project
        ws = shlex.quote(workspace) if workspace else "."
        sock = "unix://$HOME/.codex/app-server-control/app-server-control.sock"
        cmd = f"cd {ws} && codex --remote {sock}"
        if conversation:
            cmd += f" resume {shlex.quote(conversation)}"
        return cmd

    def find_conversation(self, pid: int, cwd: str, since: float) -> str:
        """Discover the Codex rollout UUID open by `pid` (Linux /proc) or newest in `cwd`."""
        fd_dir = f"/proc/{pid}/fd"
        if os.path.isdir(fd_dir):
            for pid_ in [pid] + _children(pid):
                try:
                    for f in os.listdir(f"/proc/{pid_}/fd"):
                        target = os.readlink(f"/proc/{pid_}/fd/{f}")
                        m = UUID.search(target)
                        if m and "rollout-" in target:
                            return m.group(1)
                except OSError:
                    continue
        best: tuple[float, str] | None = None
        for path in glob.glob(os.path.join(CODEX_HOME, "sessions", "*", "*", "*", "rollout-*.jsonl")):
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < since:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
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

    def setup(self, agenthub_cmd: list[str], **_: Any) -> list[str]:
        if not self.available():
            return ["codex not on PATH; skipped"]
        cmd = mcp_command(agenthub_cmd, "codex")
        subprocess.run(["codex", "mcp", "remove", "agent-hub"], capture_output=True, text=True, timeout=60)
        env = ["--env", f"AGENTHUB_HOME={os.environ['AGENTHUB_HOME']}"] if os.environ.get("AGENTHUB_HOME") else []
        r = subprocess.run(
            ["codex", "mcp", "add", "agent-hub"] + env + ["--"] + cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode == 0:
            return [f"codex: MCP server agent-hub -> {' '.join(cmd)}"]
        return [f"codex mcp add failed: {(r.stderr or r.stdout).strip()[-300:]}"]

    def teardown(self, **_: Any) -> list[str]:
        if not self.available():
            return []
        r = subprocess.run(["codex", "mcp", "remove", "agent-hub"], capture_output=True, text=True, timeout=60)
        return ["codex: MCP server agent-hub removed"] if r.returncode == 0 else []


def _children(pid: int) -> list[int]:
    try:
        with open(f"/proc/{pid}/task/{pid}/children", encoding="utf-8") as fh:
            return [int(x) for x in fh.read().split()]
    except (OSError, ValueError):
        return []
