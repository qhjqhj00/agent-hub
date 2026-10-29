"""In-process change notification.

Every write that someone might be waiting on calls `bump()`. Long-polls
(channel pulls, hubd orders, the dashboard's SSE stream) block in `wait()`
until the counter moves or they time out, then re-read the database. The
counter carries no payload on purpose: SQLite stays the only source of truth.
"""
import threading

_cond = threading.Condition()
_version = 0


def bump():
    global _version
    with _cond:
        _version += 1
        _cond.notify_all()


def version():
    return _version


def wait(since, timeout):
    """Block until the version differs from `since` or `timeout` seconds pass."""
    with _cond:
        if _version != since:
            return _version
        _cond.wait(timeout)
        return _version
