"""Agents without native push: pi, Gemini CLI, opencode, and anything you
describe in ~/.agent-hub/agents.json.

They all get push from the `agenthub run` wrapper. Gemini CLI and opencode
speak MCP, so they get the hub_* tools; pi has no MCP by design, so it uses
the `agenthub` command from its shell, as does any generic agent.
"""
import json
import os

from .base import Adapter, _short, mcp_command


class Pi(Adapter):
    kind = "pi"
    title = "pi coding agent"
    binary = "pi"
    tools = "cli"
    busy_markers = ("Working",)
    instructions_file = "~/.pi/agent/AGENTS.md"
    skills_dir = os.path.expanduser("~/.pi/agent/skills")
    resume_hint = "pi --continue"

    def argv(self, resume=None, prompt=None, extra=()):
        # pi resumes "the most recent session in this folder"; the folder is the agent's home
        cmd = ["pi"] + self.extra_args() + (["--continue"] if resume else []) + list(extra)
        return cmd + ([prompt] if prompt else [])

    def resumable(self, cwd, conversation):
        return (True, "") if conversation else (False, "no previous session")

    def find_conversation(self, pid, cwd, since):
        return "continue"  # pi picks the folder's latest session itself


def _env_key(key):
    """A non-default AGENTHUB_HOME has to reach the MCP server; agents do not pass our env on."""
    return {key: {"AGENTHUB_HOME": os.environ["AGENTHUB_HOME"]}} if os.environ.get("AGENTHUB_HOME") else {}


def _json_merge(path, update):
    path = os.path.expanduser(path)
    d = {}
    if os.path.isfile(path):
        with open(path) as fh:
            d = json.load(fh)
    before = json.dumps(d, sort_keys=True)
    update(d)
    if json.dumps(d, sort_keys=True) == before:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(d, fh, indent=2)
    return True


class Gemini(Adapter):
    kind = "gemini"
    title = "Gemini CLI"
    binary = "gemini"
    tools = "mcp"
    ready_markers = ("Type your message",)
    busy_markers = ("esc to cancel",)
    instructions_file = "~/.gemini/GEMINI.md"
    settings = "~/.gemini/settings.json"

    def argv(self, resume=None, prompt=None, extra=()):
        cmd = ["gemini"] + self.extra_args() + list(extra)
        return cmd + (["--prompt-interactive", prompt] if prompt else [])

    def setup(self, agenthub_cmd, **_):
        cmd = mcp_command(agenthub_cmd, self.kind)

        def upd(d):
            d.setdefault("mcpServers", {})["agent-hub"] = dict({"command": cmd[0], "args": cmd[1:], "trust": True},
                                                               **_env_key("env"))
        _json_merge(self.settings, upd)
        return ["gemini: MCP server agent-hub in %s" % self.settings] + Adapter.setup(self, agenthub_cmd)

    def teardown(self):
        _json_merge(self.settings, lambda d: d.get("mcpServers", {}).pop("agent-hub", None))
        return ["gemini: MCP server removed from %s" % self.settings] + Adapter.teardown(self)


class OpenCode(Adapter):
    kind = "opencode"
    title = "opencode"
    binary = "opencode"
    tools = "mcp"
    instructions_file = "~/.config/opencode/AGENTS.md"
    settings = "~/.config/opencode/opencode.json"

    def argv(self, resume=None, prompt=None, extra=()):
        cmd = ["opencode"] + self.extra_args() + (["--continue"] if resume else []) + list(extra)
        return cmd + (["--prompt", prompt] if prompt else [])

    def setup(self, agenthub_cmd, **_):
        cmd = mcp_command(agenthub_cmd, self.kind)

        def upd(d):
            d.setdefault("$schema", "https://opencode.ai/config.json")
            d.setdefault("mcp", {})["agent-hub"] = dict({"type": "local", "command": cmd, "enabled": True},
                                                        **_env_key("environment"))
        _json_merge(self.settings, upd)
        return ["opencode: MCP server agent-hub in %s" % self.settings] + Adapter.setup(self, agenthub_cmd)

    def teardown(self):
        _json_merge(self.settings, lambda d: d.get("mcp", {}).pop("agent-hub", None))
        return ["opencode: MCP server removed from %s" % self.settings] + Adapter.teardown(self)


class Custom(Adapter):
    """An agent described in ~/.agent-hub/agents.json:

        {"aider": {"command": ["aider", "--no-auto-commits"], "tools": "cli",
                   "ready": ["> "], "busy": [], "instructions_file": "~/.aider.conventions.md"}}

    Anything with a terminal UI works: the wrapper types hub events into it."""

    def __init__(self, kind, spec):
        self.kind = kind
        self.spec = spec
        self.title = spec.get("title") or kind
        self.command = spec.get("command") or [kind]
        if isinstance(self.command, str):
            import shlex
            self.command = shlex.split(self.command)
        self.binary = self.command[0]
        self.tools = spec.get("tools", "cli")
        self.ready_markers = tuple(spec.get("ready", ()))
        self.busy_markers = tuple(spec.get("busy", ()))
        self.instructions_file = spec.get("instructions_file")
        self.resume_args = spec.get("resume_args")  # e.g. ["--resume", "{conversation}"]

    def argv(self, resume=None, prompt=None, extra=()):
        cmd = list(self.command) + self.extra_args()
        if resume and self.resume_args:
            cmd += [a.replace("{conversation}", resume) for a in self.resume_args]
        return cmd + list(extra) + ([prompt] if prompt else [])

    def setup(self, agenthub_cmd, **_):
        if self.tools == "mcp" and self.spec.get("mcp_hint"):
            return ["%s: add this MCP server to its config: %s" % (self.kind, " ".join(mcp_command(agenthub_cmd, self.kind)))]
        return Adapter.setup(self, agenthub_cmd)


class Generic(Custom):
    """`agenthub run generic -- <any command>`: a one-off, nothing to configure."""

    def __init__(self):
        Custom.__init__(self, "generic", {"title": "Any terminal agent", "command": ["sh"]})

    def argv(self, resume=None, prompt=None, extra=()):
        return list(extra) or [os.environ.get("SHELL") or "sh"]

    def available(self):
        return True

    def version(self):
        return ""


__all__ = ["Pi", "Gemini", "OpenCode", "Custom", "Generic", "_short"]
