"""Everything that runs on a node: the daemon (hubd), the agent wrapper, the MCP
server, and the adapters that know each coding agent."""
import os
import shutil
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def zip_path():
    """The .pyz this code runs from, or None."""
    loader = globals().get("__loader__")
    if type(loader).__name__ == "zipimporter":
        return getattr(loader, "archive", None)
    return None


def install_kind():
    """How this copy was installed: pyz (downloaded from a hub, self-updating), pip, or source."""
    if zip_path():
        return "pyz"
    return "pip" if "site-packages" in PKG or "dist-packages" in PKG else "source"


def self_cmd():
    """argv prefix that runs this same agenthub (for MCP configs, tmux, services)."""
    z = zip_path()
    if z:
        return [z] if os.access(z, os.X_OK) else [sys.executable, z]
    script = os.path.join(os.path.dirname(PKG), "bin", "agenthub")
    if install_kind() == "source" and os.path.isfile(script):
        return [sys.executable, script]
    exe = shutil.which("agenthub")
    return [exe] if exe else [sys.executable, "-m", "agenthub"]
