"""Agent adapters registry for agent-hub v2.

Built-in kinds: claude, agy, codex, pi (plus fallback `other`).
Hub imports `start_command(kind, name, workspace, conversation="", project="")`
directly from this package to populate `card.start`.
"""
from __future__ import annotations

from .agy import Agy
from .base import Adapter
from .claude import Claude
from .codex import Codex
from .pi import Pi

BUILTIN = (Claude, Agy, Codex, Pi)


def all_adapters() -> dict[str, Adapter]:
    return {cls.kind: cls() for cls in BUILTIN}


def get(kind: str | None) -> Adapter:
    adapters = all_adapters()
    if kind in adapters:
        return adapters[kind]
    if not kind:
        return adapters["claude"]
    other = Adapter()
    other.kind = kind
    other.title = kind
    other.binary = kind
    return other


def installed() -> dict[str, str]:
    """{kind: version} for the built-in agent CLIs available on this node."""
    return {k: a.version() for k, a in all_adapters().items() if a.available()}


def start_command(
    kind: str,
    name: str,
    workspace: str,
    conversation: str = "",
    project: str = "",
) -> str:
    """Return a copy-pasteable launch command for `kind` (used by hub `card.start`)."""
    return get(kind).start_command(
        name=name,
        workspace=workspace,
        conversation=conversation,
        project=project,
    )


def onboarding_prompt(
    kind: str,
    name: str,
    project: str = "",
    role: str = "",
) -> str:
    """Return the first-turn onboarding prompt to paste into a newly launched agent."""
    return get(kind).onboarding_prompt(name=name, project=project, role=role)


__all__ = [
    "Adapter",
    "Agy",
    "Claude",
    "Codex",
    "Pi",
    "all_adapters",
    "get",
    "installed",
    "onboarding_prompt",
    "start_command",
]
