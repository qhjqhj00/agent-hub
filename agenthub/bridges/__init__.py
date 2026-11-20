"""External control-plane bridges and hooks for agent-hub v2.

No process spawning or PTY wrapping: bridges only deliver messages into
sessions already started by the user.
"""
from .common import (
    SessionAuthError,
    SessionHTTPClient,
    list_session_files,
    read_session_file,
    remove_session_file,
    resolve_t3_thread,
    session_file,
    sessions_dir,
    update_session_file,
    write_session_file,
)

__all__ = [
    "SessionAuthError",
    "SessionHTTPClient",
    "list_session_files",
    "read_session_file",
    "remove_session_file",
    "resolve_t3_thread",
    "session_file",
    "sessions_dir",
    "update_session_file",
    "write_session_file",
]
