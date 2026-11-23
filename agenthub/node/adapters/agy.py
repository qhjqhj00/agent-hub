"""Antigravity (agy) adapter for v2 (T3 Code + standalone CLI).

Push:
- Idle / cross-turn (T3): `agenthub t3-bridge` calls `t3 thread send <t3_thread>`.
- Mid-turn (busy): `<workspace>/.agents/hooks.json` runs `PreInvocation` (0s `pull?wait=0`)
  and `Stop` (0s non-blocking `state=idle`).

Tools:
- Native MCP (`agenthub mcp --kind agy`), written into T3's Antigravity provider
  `mcp_config.json` (when present) and `~/.gemini/config/mcp_config.json`.
"""
from __future__ import annotations

import glob
import json
import os
import shlex
import subprocess
from typing import Any

from .base import Adapter, _short, mcp_command


def _t3_config_dirs() -> list[str]:
    t3_home = os.path.expanduser(os.environ.get("T3CODE_HOME") or "~/.t3")
    pattern = os.path.join(t3_home, "userdata", "providers", "antigravity", "*", "config")
    return [d for d in sorted(glob.glob(pattern)) if os.path.isdir(d)]


def _t3_mcp_configs() -> list[str]:
    return [os.path.join(d, "mcp_config.json") for d in _t3_config_dirs()]


def _t3_hook_configs() -> list[str]:
    return [os.path.join(d, "hooks.json") for d in _t3_config_dirs()]


def _cli_mcp_config() -> str:
    return os.path.expanduser("~/.gemini/config/mcp_config.json")


def _upsert_mcp_config(path: str, cmd: list[str]) -> bool:
    d: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {}
    before = json.dumps(d, sort_keys=True)
    entry: dict[str, Any] = {"command": cmd[0], "args": cmd[1:]}
    if os.environ.get("AGENTHUB_HOME"):
        entry["env"] = {"AGENTHUB_HOME": os.environ["AGENTHUB_HOME"]}
    d.setdefault("mcpServers", {})["agent-hub"] = entry
    if json.dumps(d, sort_keys=True) == before:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
    return True


def _remove_mcp_config(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return False
    if "agent-hub" not in (d.get("mcpServers") or {}):
        return False
    d["mcpServers"].pop("agent-hub", None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
    return True


def _ensure_git_exclude(workspace: str, rel_entry: str) -> None:
    """Keep `.agents/hooks.json` untracked via git info/exclude when workspace is a git repo."""
    try:
        r = subprocess.run(
            ["git", "-C", workspace, "rev-parse", "--git-path", "info/exclude"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return
        exclude_path = r.stdout.strip()
        if not os.path.isabs(exclude_path):
            exclude_path = os.path.join(workspace, exclude_path)
        existing = ""
        if os.path.isfile(exclude_path):
            with open(exclude_path, encoding="utf-8") as fh:
                existing = fh.read()
        if rel_entry in existing.splitlines():
            return
        os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
        with open(exclude_path, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(rel_entry + "\n")
    except Exception:
        pass


def _write_hooks_file(hooks_path: str, agenthub_cmd: list[str], agent_name: str = "") -> str:
    hook_argv = list(agenthub_cmd) + ["agy-hook"] + ([agent_name] if agent_name else [])
    hook_cmd = " ".join(shlex.quote(p) for p in hook_argv)
    data: dict[str, Any] = {}
    if os.path.isfile(hooks_path):
        try:
            with open(hooks_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
    data["agent-hub"] = {
        "PreInvocation": [{"type": "command", "command": hook_cmd, "timeout": 15}],
        "Stop": [{"type": "command", "command": hook_cmd, "timeout": 10}],
    }
    os.makedirs(os.path.dirname(hooks_path), exist_ok=True)
    with open(hooks_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return hooks_path


def write_workspace_hooks(workspace: str, agenthub_cmd: list[str], agent_name: str = "") -> str:
    """Write `<workspace>/.agents/hooks.json` with non-blocking PreInvocation and 0s Stop hooks."""
    ws = os.path.abspath(os.path.expanduser(workspace))
    hooks_path = os.path.join(ws, ".agents", "hooks.json")
    _write_hooks_file(hooks_path, agenthub_cmd, agent_name=agent_name)
    _ensure_git_exclude(ws, ".agents/hooks.json")
    return hooks_path


class Agy(Adapter):
    kind = "agy"
    title = "Antigravity (T3 / CLI)"
    binary = "agy"
    push = "bridge"
    tools = "mcp"

    def start_command(
        self,
        name: str,
        workspace: str,
        conversation: str = "",
        project: str = "",
    ) -> str:
        ws = shlex.quote(workspace) if workspace else "."
        prompt = self.onboarding_prompt(name=name, project=project)
        # In T3 Code, starting a thread directly binds the workspace and opens it in Web/Mobile.
        _ = conversation
        return f"t3 thread start {ws} --title {shlex.quote(name)} {shlex.quote(prompt)}"

    def onboarding_prompt(
        self,
        name: str,
        project: str = "",
        role: str = "",
        liaison: bool = False,
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
        base = (
            f"你是 {name}{scope}。请先调用 {reg} 注册身份并查看 hub_inbox。"
            "后续 agent-hub 的消息和任务会直接推入本对话；请统一用 hub_update / hub_send / hub_close 回复 hub。"
        )
        if not liaison:
            return base + "需要用户拍板的事情在本对话里直接向用户汇报。"
        extra_note = f"（如共享看板存在 note `liaison-{project}`，请一并读取并遵守。）" if project else ""
        return (
            base
            + "你同时是本项目的【人类联络人】，请严格遵守省注意力原则："
            "1) 普通进度更新不主动打扰用户，只在有待验收结果（review）、需要拍板的问题（question）或明确卡住时向用户汇报；"
            "2) 每次只向用户提出一个具体问题，并附上简短选项或你的建议；"
            "3) 收到用户答复后，立刻用 hub_update(kind=\"answer\"/\"comment\") 或 hub_close 写回 hub。"
            + extra_note
        )

    def setup(
        self,
        agenthub_cmd: list[str],
        workspace: str | None = None,
        agent_name: str = "",
        **_: Any,
    ) -> list[str]:
        out: list[str] = []
        cmd = mcp_command(agenthub_cmd, "agy")
        targets = _t3_mcp_configs()
        targets.append(_cli_mcp_config())
        for path in targets:
            changed = _upsert_mcp_config(path, cmd)
            out.append(f"agy: {'wrote' if changed else 'kept'} MCP agent-hub in {_short(path)}")
        # Install global T3 hooks.json so non-git workspaces (e.g. ~/workspace) also run agy-hook.
        for hpath in _t3_hook_configs():
            _write_hooks_file(hpath, agenthub_cmd)
            out.append(f"agy: wrote global T3 hooks in {_short(hpath)}")
        if workspace:
            hp = write_workspace_hooks(workspace, agenthub_cmd, agent_name=agent_name)
            out.append(f"agy: wrote non-blocking PreInvocation/Stop hooks in {_short(hp)}")
        return out

    def teardown(self, workspace: str | None = None, **_: Any) -> list[str]:
        out: list[str] = []
        for path in _t3_mcp_configs() + [_cli_mcp_config()]:
            if _remove_mcp_config(path):
                out.append(f"agy: removed MCP agent-hub from {_short(path)}")
        if workspace:
            hp = os.path.join(os.path.abspath(os.path.expanduser(workspace)), ".agents", "hooks.json")
            if os.path.isfile(hp):
                try:
                    with open(hp, encoding="utf-8") as fh:
                        d = json.load(fh)
                    if "agent-hub" in d:
                        d.pop("agent-hub", None)
                        with open(hp, "w", encoding="utf-8") as fh:
                            json.dump(d, fh, indent=2)
                        out.append(f"agy: removed agent-hub hook from {_short(hp)}")
                except (OSError, ValueError):
                    pass
        return out
