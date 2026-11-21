"""pi coding agent adapter (v2).

Push & Tools:
- In-process TypeScript Extension (`agenthub/node/pi/agent-hub.ts`), installed
  into `~/.pi/agent/extensions/agent-hub.ts` by `agenthub setup pi`.
- Registers all 9 v2 `hub_*` tools via `pi.registerTool()`, runs a background
  long-poll loop against `GET /v1/sessions/<id>/pull`, injects messages directly
  into the live interactive TUI via `pi.sendUserMessage()`, and reports
  `busy`/`idle`/`end` on `agent_start`/`agent_end`/`session_shutdown`.
"""
from __future__ import annotations

import os
import shlex
import shutil
from typing import Any

from .base import Adapter, _short

EXT_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pi", "agent-hub.ts")


def pi_extension_dest() -> str:
    base = os.path.expanduser(os.environ.get("PI_CODING_AGENT_DIR") or "~/.pi/agent")
    return os.path.join(base, "extensions", "agent-hub.ts")


class Pi(Adapter):
    kind = "pi"
    title = "pi coding agent"
    binary = "pi"
    push = "channel"
    tools = "extension"

    def start_command(
        self,
        name: str,
        workspace: str,
        conversation: str = "",
        project: str = "",
    ) -> str:
        _ = project
        ws = shlex.quote(workspace) if workspace else "."
        cmd = f"cd {ws} && HUB_AGENT={shlex.quote(name)} pi"
        if conversation:
            cmd += f" --session {shlex.quote(conversation)}"
        return cmd

    def setup(self, agenthub_cmd: list[str], **_: Any) -> list[str]:
        _ = agenthub_cmd
        import pkgutil
        try:  # works from a source tree and from the agenthub.pyz zipapp alike
            data = pkgutil.get_data("agenthub.node", "pi/agent-hub.ts")
        except OSError:
            data = None
        if not data:
            return [f"pi: extension source missing at {EXT_SRC}"]
        dest = pi_extension_dest()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        return [f"pi: installed extension -> {_short(dest)}"]

    def teardown(self, **_: Any) -> list[str]:
        dest = pi_extension_dest()
        if os.path.isfile(dest):
            os.unlink(dest)
            return [f"pi: removed extension {_short(dest)}"]
        return []
