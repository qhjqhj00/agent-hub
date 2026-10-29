"""The coding agents agent-hub knows how to start, reach and set up.

Built in: claude, codex, pi, gemini, opencode, generic. More can be described
in ~/.agent-hub/agents.json without writing code (see others.Custom).
"""
import json
import os

from .base import Adapter
from .claude import Claude
from .codex import Codex
from .others import Custom, Gemini, Generic, OpenCode, Pi

BUILTIN = (Claude, Codex, Pi, Gemini, OpenCode)
CUSTOM_FILE = os.path.join(os.path.expanduser(os.environ.get("AGENTHUB_HOME") or "~/.agent-hub"), "agents.json")


def custom():
    try:
        with open(CUSTOM_FILE) as fh:
            return {k: Custom(k, v) for k, v in json.load(fh).items()}
    except (OSError, ValueError):
        return {}


def all_adapters():
    out = {c.kind: c() for c in BUILTIN}
    out.update(custom())
    out["generic"] = Generic()
    return out


def get(kind):
    a = all_adapters().get(kind or "claude")
    if not a:
        raise SystemExit("unknown agent kind %r; known: %s (add your own in %s)"
                         % (kind, ", ".join(sorted(all_adapters())), CUSTOM_FILE))
    return a


def installed():
    """{kind: version} for the agent CLIs on this node."""
    return {k: a.version() for k, a in all_adapters().items() if k != "generic" and a.available()}


__all__ = ["Adapter", "all_adapters", "get", "installed"]
