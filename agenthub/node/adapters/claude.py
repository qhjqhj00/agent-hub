"""Claude Code.

Push is native: the agent-hub plugin's MCP server declares the `claude/channel`
capability and injects hub events straight into the session, idle or not, and
relays permission prompts to the human. Custom channels need
`--dangerously-load-development-channels` while Claude Code has them in
research preview; set HUB_CLAUDE_PUSH=pty to use the terminal wrapper instead.
"""
import json
import os
import re
import subprocess
import time

from .base import Adapter, _short

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
RESUME_MAX_MB = 8          # bigger transcripts take minutes to load, and the agent sits deaf meanwhile
RESUME_MAX_AGE = 7 * 86400


class Claude(Adapter):
    kind = "claude"
    title = "Claude Code"
    binary = "claude"
    tools = "mcp"
    # the status line under the input box is only drawn once the session takes input
    ready_markers = ("⏵⏵", "? for shortcuts", "auto mode on", "accept edits on", "plan mode on",
                     "bypass permissions on", "manual mode on")
    busy_markers = ("esc to interrupt",)
    dialogs = (
        ("Do you trust the files in this folder", "Yes"),
        ("Is this a project you created or one you trust", "Yes, I trust this folder"),
        ("Loading development channels", "I am using this for local development"),
        ("New MCP server found", "Use this MCP server"),
    )
    default_args = ("--permission-mode", "auto")
    resume_hint = "--resume <conversation id>"

    @property
    def push(self):
        return "pty" if os.environ.get("HUB_CLAUDE_PUSH") == "pty" else "channel"

    def argv(self, resume=None, prompt=None, extra=()):
        if "HUB_CLAUDE_ARGS" not in os.environ and os.environ.get("HUB_PERMISSION_MODE"):
            args = ["--permission-mode", os.environ["HUB_PERMISSION_MODE"]]  # v1 name
        else:
            args = self.extra_args()
        cmd = ["claude"] + args
        if self.push == "channel":
            cmd += ["--dangerously-load-development-channels", "plugin:agent-hub@agent-hub"]
        if resume:
            cmd += ["--resume", resume]
        cmd += list(extra)
        if prompt:
            cmd.append(prompt)
        return cmd

    def env(self, agent):
        return {"HUB_CHANNEL": "1" if self.push == "channel" else "0"}

    def resumable(self, cwd, sid):
        if not sid or sid.startswith(("cli-", "pid-")) or not re.match(r"^[0-9a-f\-]{36}$", sid):
            return False, "no previous session"
        proj = re.sub(r"[^A-Za-z0-9]", "-", cwd)
        path = os.path.join(CONFIG_DIR, "projects", proj, sid + ".jsonl")
        if not os.path.isfile(path):
            return False, "previous transcript not on this node"
        st = os.stat(path)
        if st.st_size > RESUME_MAX_MB * 1024 * 1024:
            return False, "previous session too large to resume (%d MB)" % (st.st_size // 1048576)
        if time.time() - st.st_mtime > RESUME_MAX_AGE:
            return False, "previous session older than 7 days"
        return True, ""

    # Claude reports its conversation to the MCP server through the plugin's SessionStart hook.

    def setup(self, agenthub_cmd, hub_url=None, token=None):
        """Add the hub's plugin marketplace and install the agent-hub + team-skills plugins."""
        from ... import client
        out = []
        if not self.available():
            return ["claude not on PATH; skipped"]
        hub_url = hub_url or client.url()
        token = token if token is not None else client.token()
        # the marketplace is a git repo on the hub; git sends the token as the basic-auth password
        mkt = hub_url.rstrip("/") + "/marketplace.git"
        if token:
            scheme, _, rest = mkt.partition("://")
            mkt = "%s://agenthub:%s@%s" % (scheme, token, rest)

        def run(*a):
            return subprocess.run(["claude"] + list(a), capture_output=True, text=True, timeout=180)

        have = run("plugin", "marketplace", "list").stdout
        if "agent-hub" in have:
            run("plugin", "marketplace", "remove", "agent-hub")
        r = run("plugin", "marketplace", "add", mkt)
        if r.returncode:
            return out + ["claude plugin marketplace add failed: %s" % (r.stderr or r.stdout).strip()[-300:]]
        out.append("claude: marketplace agent-hub -> %s/marketplace.git" % hub_url)
        for p in ("agent-hub@agent-hub", "team-skills@agent-hub"):
            r = run("plugin", "install", p)
            if r.returncode:
                r = run("plugin", "update", p)
            out.append("claude: plugin %s %s" % (p, "ok" if r.returncode == 0 else (r.stderr or r.stdout).strip()[-200:]))
        p = os.path.join(CONFIG_DIR, "settings.json")
        try:
            d = json.load(open(p)) if os.path.isfile(p) else {}
            d.setdefault("extraKnownMarketplaces", {}).setdefault("agent-hub", {})["autoUpdate"] = True
            json.dump(d, open(p, "w"), indent=2)
            out.append("claude: marketplace auto-update on (%s)" % _short(p))
        except (OSError, ValueError) as e:
            out.append("claude: could not update %s: %s" % (_short(p), e))
        return out

    def update_plugins(self):
        out = []
        for a in (("plugin", "marketplace", "update", "agent-hub"), ("plugin", "update", "agent-hub@agent-hub"),
                  ("plugin", "update", "team-skills@agent-hub")):
            r = subprocess.run(["claude"] + list(a), capture_output=True, text=True, timeout=180)
            out.append("%s: %s" % (" ".join(a[1:]), (r.stdout + r.stderr).strip()[-200:]))
        return out

    def teardown(self):
        if not self.available():
            return []
        r = subprocess.run(["claude", "plugin", "marketplace", "remove", "agent-hub"], capture_output=True,
                           text=True, timeout=60)
        return ["claude: marketplace agent-hub removed" if r.returncode == 0 else "claude: nothing to remove"]
