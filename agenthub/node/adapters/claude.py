"""Claude Code adapter (v2).

Push is in-process (`channel` via MCP `claude/channel` when HUB_CHANNEL=1,
otherwise `pull`). No PTY wrapper or screen markers.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any

from .base import Adapter, _short

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


class Claude(Adapter):
    kind = "claude"
    title = "Claude Code"
    binary = "claude"
    push = "channel"
    tools = "mcp"

    def start_command(
        self,
        name: str,
        workspace: str,
        conversation: str = "",
        project: str = "",
    ) -> str:
        _ = project
        ws = shlex.quote(workspace) if workspace else "."
        cmd = (
            f"cd {ws} && HUB_CHANNEL=1 HUB_AGENT={shlex.quote(name)} "
            "claude --dangerously-load-development-channels plugin:agent-hub@agent-hub"
        )
        if conversation:
            cmd += f" --resume {shlex.quote(conversation)}"
        return cmd

    def setup(self, agenthub_cmd: list[str], hub_url: str | None = None, token: str | None = None, **_: Any) -> list[str]:
        """Add the hub's plugin marketplace and install the agent-hub + team-skills plugins."""
        _ = agenthub_cmd
        from ... import client

        out: list[str] = []
        if not self.available():
            return ["claude not on PATH; skipped"]
        hub_url = hub_url or client.url()
        token = token if token is not None else client.token()
        mkt = hub_url.rstrip("/") + "/marketplace.git"
        if token:
            scheme, _, rest = mkt.partition("://")
            mkt = f"{scheme}://agenthub:{token}@{rest}"

        def run(*a: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["claude"] + list(a), capture_output=True, text=True, timeout=180)

        have = run("plugin", "marketplace", "list").stdout
        if "agent-hub" in have:
            run("plugin", "marketplace", "remove", "agent-hub")
        r = run("plugin", "marketplace", "add", mkt)
        if r.returncode:
            return out + [f"claude plugin marketplace add failed: {(r.stderr or r.stdout).strip()[-300:]}"]
        out.append(f"claude: marketplace agent-hub -> {hub_url}/marketplace.git")
        for p in ("agent-hub@agent-hub", "team-skills@agent-hub"):
            r = run("plugin", "install", p)
            if r.returncode:
                r = run("plugin", "update", p)
            out.append(f"claude: plugin {p} {'ok' if r.returncode == 0 else (r.stderr or r.stdout).strip()[-200:]}")
        cfg_path = os.path.join(CONFIG_DIR, "settings.json")
        try:
            d = json.load(open(cfg_path, encoding="utf-8")) if os.path.isfile(cfg_path) else {}
            d.setdefault("extraKnownMarketplaces", {}).setdefault("agent-hub", {})["autoUpdate"] = True
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=2)
            out.append(f"claude: marketplace auto-update on ({_short(cfg_path)})")
        except (OSError, ValueError) as e:
            out.append(f"claude: could not update {_short(cfg_path)}: {e}")
        return out

    def teardown(self, **_: Any) -> list[str]:
        if not self.available():
            return []
        r = subprocess.run(["claude", "plugin", "marketplace", "remove", "agent-hub"], capture_output=True, text=True, timeout=60)
        return ["claude: marketplace agent-hub removed" if r.returncode == 0 else "claude: nothing to remove"]
