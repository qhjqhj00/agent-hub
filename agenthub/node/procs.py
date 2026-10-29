"""Processes on this node. /proc on Linux, `ps` elsewhere (macOS)."""
import os
import signal
import subprocess
import time

HAVE_PROC = os.path.isdir("/proc/self")


def parent(pid):
    if HAVE_PROC:
        try:
            with open("/proc/%d/stat" % pid) as fh:
                return int(fh.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            return 0
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=3)
        return int(out.stdout.strip() or 0)
    except Exception:
        return 0


def ancestors(pid, limit=12):
    out = []
    pid = parent(pid)
    while pid > 1 and len(out) < limit:
        out.append(pid)
        pid = parent(pid)
    return out


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def environ(pid):
    """The environment a process started with, or None when it cannot be read."""
    if HAVE_PROC:
        try:
            with open("/proc/%d/environ" % pid, "rb") as fh:
                return fh.read().split(b"\0")
        except OSError:
            return None
    try:
        out = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(pid)], capture_output=True, timeout=3)
        return out.stdout.split()
    except Exception:
        return None


def all_pids():
    if HAVE_PROC:
        return [int(p) for p in os.listdir("/proc") if p.isdigit()]
    try:
        out = subprocess.run(["ps", "-U", str(os.getuid()), "-o", "pid="], capture_output=True, text=True, timeout=5)
        return [int(x) for x in out.stdout.split()]
    except Exception:
        return []


def with_env(key, value):
    """Our processes whose environment carries key=value (everything started for one agent)."""
    want = ("%s=%s" % (key, value)).encode()
    me, uid, out = os.getpid(), os.getuid(), []
    for pid in all_pids():
        if pid == me:
            continue
        if HAVE_PROC:
            try:
                if os.stat("/proc/%d" % pid).st_uid != uid:
                    continue
            except OSError:
                continue
        env = environ(pid)
        if env and want in env:
            out.append(pid)
    return out


def kill(pids, grace=3):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        left = []
        for p in pids:
            try:
                os.kill(p, sig)
                left.append(p)
            except OSError:
                pass
        if not left:
            return
        time.sleep(grace)
        pids = [p for p in left if alive(p)]
