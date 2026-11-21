"""Base adapter definition for agent-hub v2.

v2 removes PTY screen-scraping (`ready_markers`, `busy_markers`, `blockers`,
`dialogs`) and tmux auto-waking. Each adapter describes how to:

- detect the CLI (`available`, `version`)
- install / remove the hub integration (`setup`, `teardown`)
- generate the user's copy-pasteable `start_command` and `onboarding_prompt`
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from typing import Any


class Adapter:
    kind = "other"
    title = "Generic Agent"
    binary: str | None = None
    push = "pull"          # "channel" | "bridge" | "pull"
    tools = "cli"          # "mcp" | "extension" | "cli"

    def available(self) -> bool:
        return bool(self.binary and shutil.which(self.binary))

    def version(self) -> str:
        if not self.available() or not self.binary:
            return ""
        try:
            out = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=10)
            m = re.search(r"\d+\.\d+[\w.\-]*", (out.stdout or "") + (out.stderr or ""))
            return m.group(0) if m else "?"
        except Exception:
            return "?"

    def start_command(
        self,
        name: str,
        workspace: str,
        conversation: str = "",
        project: str = "",
    ) -> str:
        ws = shlex.quote(workspace) if workspace else "."
        bin_name = self.binary or self.kind
        return f"cd {ws} && HUB_AGENT={shlex.quote(name)} {bin_name}"

    def onboarding_prompt(
        self,
        name: str,
        project: str = "",
        role: str = "",
    ) -> str:
        args = [f'name="{name}"']
        if project:
            args.append(f'project="{project}"')
        if role:
            args.append(f'role="{role}"')
        reg = f"hub_register({', '.join(args)})"
        scope = ""
        if project and role:
            scope = f"，负责 {project} 项目的 {role}"
        elif project:
            scope = f"，项目 {project}"
        return (
            f"你是 {name}{scope}。请先调用 {reg} 注册身份并查看 hub_inbox。"
            "后续 agent-hub 的消息和任务会直接推入本会话，请统一调用 hub_update / hub_send / hub_close 回复 hub。"
        )

    def setup(self, agenthub_cmd: list[str], **kwargs: Any) -> list[str]:
        _ = agenthub_cmd, kwargs
        return [f"{self.kind}: nothing to set up"]

    def teardown(self, **kwargs: Any) -> list[str]:
        _ = kwargs
        return [f"{self.kind}: nothing to remove"]

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "binary": self.binary,
            "push": self.push,
            "tools": self.tools,
            "available": self.available(),
        }


def _short(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def mcp_command(agenthub_cmd: list[str], kind: str) -> list[str]:
    """The MCP server command line for an agent's config: [program, args...]."""
    return list(agenthub_cmd) + ["mcp", "--kind", kind]
