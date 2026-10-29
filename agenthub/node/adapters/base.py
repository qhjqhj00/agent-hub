"""What the hub needs to know about one kind of coding agent.

An adapter answers four questions for its CLI:

  start    the command line (`argv`), with the hub's identity and an optional
           conversation to resume
  push     how work reaches a running session: "channel" (the agent has a native
           way to inject events -- Claude Code) or "pty" (the `agenthub run`
           wrapper types the text into the agent's terminal when it is idle)
  tools    "mcp" (hub_* tools through `agenthub mcp`) or "cli" (the agent runs
           `agenthub ...` in its shell)
  screen   how to tell, from its terminal, that it is ready for input, busy, or
           sitting on a dialog -- for hubd's wake autopilot and for safe typing

and `setup()` installs the integration (MCP config, instructions block, skills).
"""
import os
import re
import shutil
import subprocess

from .. import instructions


class Adapter:
    kind = "generic"
    title = "Generic CLI"
    binary = None
    push = "pty"
    tools = "cli"
    # Substrings on the last lines of the screen. Ready: its input box is up. Blockers: a dialog or
    # confirmation is waiting, so typing would answer it. Busy: it is working.
    ready_markers = ()
    blockers = ("Enter to confirm", "Esc to cancel", "Press enter to continue", "(y/n)", "[y/N]", "[Y/n]")
    busy_markers = ()
    # Start-up dialogs hubd may answer on its own: (text that identifies it, option to pick).
    dialogs = ()
    instructions_file = None   # global instructions file the setup block goes into
    skills_dir = None          # where shared skills are copied, for agents that read SKILL.md dirs
    resume_hint = ""

    # ---------------------------------------------------------------- presence on this node
    def available(self):
        return bool(self.binary and shutil.which(self.binary))

    def version(self):
        if not self.available():
            return ""
        try:
            out = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=10)
            m = re.search(r"\d+\.\d+[\w.\-]*", out.stdout + out.stderr)
            return m.group(0) if m else "?"
        except Exception:
            return "?"

    # ---------------------------------------------------------------- start
    def argv(self, resume=None, prompt=None, extra=()):
        raise NotImplementedError

    def env(self, agent):
        return {}

    def extra_args(self):
        """User-chosen flags for every start: HUB_<KIND>_ARGS."""
        import shlex
        v = os.environ.get("HUB_%s_ARGS" % self.kind.upper().replace("-", "_"))
        return shlex.split(v) if v is not None else list(self.default_args)

    default_args = ()

    def resumable(self, cwd, conversation):
        """(ok, why): is it worth resuming this conversation here?"""
        return False, "resume not supported for %s" % self.kind

    def find_conversation(self, pid, cwd, since):
        """The id of the conversation the agent process `pid` is in, once it has one."""
        return ""

    # ---------------------------------------------------------------- screen
    def ready(self, screen):
        tail = "\n".join(screen.rstrip().splitlines()[-12:])
        if any(b in tail for b in self.blockers):
            return False
        if any(b in tail for b in self.busy_markers):
            return False
        # terminals drop trailing blanks, so "> " is matched as ">"
        return any(m.rstrip() in tail for m in self.ready_markers) if self.ready_markers else True

    # ---------------------------------------------------------------- setup
    def setup(self, agenthub_cmd):
        """Install the integration; returns lines describing what was done."""
        out = []
        if self.instructions_file:
            path = os.path.expanduser(self.instructions_file)
            changed = instructions.upsert(path, cli=(self.tools == "cli"))
            out.append("%s instructions block in %s" % ("wrote" if changed else "kept", _short(path)))
        return out

    def teardown(self):
        out = []
        if self.instructions_file:
            path = os.path.expanduser(self.instructions_file)
            if instructions.remove(path):
                out.append("removed instructions block from %s" % _short(path))
        return out

    def describe(self):
        return {"kind": self.kind, "title": self.title, "binary": self.binary, "push": self.push,
                "tools": self.tools, "available": self.available()}


def _short(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home) else p


def mcp_command(agenthub_cmd, kind):
    """The MCP server command line for an agent's config: [program, args...]."""
    return list(agenthub_cmd) + ["mcp", "--kind", kind]
