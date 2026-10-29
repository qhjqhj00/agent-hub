"""`agenthub run <kind>`: start a coding agent connected to the hub.

For Claude Code (native channel push) this is just the right command line.
For every other agent it is a thin terminal wrapper:

    you <-> [ agenthub run: pty proxy + hub listener ] <-> codex / pi / gemini / ...

The wrapper owns a hub session for the agent, long-polls it, and when a task,
question or reply arrives it waits until the agent is idle (no output for a
moment, nobody typing, no dialog on screen) and types the text in, exactly as
if you had pasted it and pressed Enter. Delivery is acknowledged only after
the text went in, so nothing is lost when the agent is busy or exits.

The agent's MCP server and shell tools find this session through a run file
(~/.agent-hub/run/<wrapper pid>.json), since not every agent passes
environment variables on to the processes it starts.
"""
import errno
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

from .. import client
from . import adapters, procs

QUIET = float(os.environ.get("HUB_PTY_QUIET", "2.5"))        # seconds of no output = idle
TYPING_QUIET = float(os.environ.get("HUB_PTY_TYPING_QUIET", "4"))   # seconds since you last typed
MAX_PASTE = 6000
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-Z\\-_]|[\x00-\x08\x0b-\x1f]")


def log(*a):
    try:
        with open(os.path.join(client.HOME, "run.log"), "a") as fh:
            fh.write("%s [%d] %s\n" % (time.strftime("%m-%d %H:%M:%S"), os.getpid(), " ".join(str(x) for x in a)))
    except OSError:
        pass


# ------------------------------------------------------------------ the terminal

class Pty:
    """Run argv on a pseudo-terminal, copying it to ours, and let another thread type into it."""

    def __init__(self, argv, env, cwd=None):
        self.argv, self.env, self.cwd = argv, env, cwd
        self.fd = None
        self.pid = None
        self.last_out = time.time()
        self.last_in = 0.0
        self.tail = ""             # recent output, escape codes stripped
        self.bracketed = False     # the app asked for bracketed paste
        self._wlock = threading.Lock()

    def _winsize(self):
        import fcntl
        import termios
        if os.isatty(0):
            try:
                ws = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8)
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, ws)
            except OSError:
                pass

    def _seen(self, data):
        self.last_out = time.time()
        text = data.decode("utf-8", "replace")
        on, off = text.rfind("\x1b[?2004h"), text.rfind("\x1b[?2004l")
        if on > off:
            self.bracketed = True
        elif off > on:
            self.bracketed = False
        self.tail = (self.tail + ANSI.sub("", text))[-6000:]

    def screen(self):
        """What the agent shows: tmux's view of our pane when we have one, else recent output."""
        pane = os.environ.get("TMUX_PANE")
        if pane and shutil.which("tmux"):
            r = subprocess.run(["tmux", "capture-pane", "-p", "-t", pane], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return r.stdout
        return self.tail

    def idle(self):
        t = time.time()
        return t - self.last_out >= QUIET and t - self.last_in >= TYPING_QUIET

    def type(self, text, submit="\r"):
        with self._wlock:
            if self.bracketed:
                self._write(b"\x1b[200~" + text.encode() + b"\x1b[201~")
            else:
                self._write(text.replace("\r", "").replace("\n", " ").encode())
            time.sleep(0.5)  # TUIs treat an Enter glued to a paste as part of it
            self._write(submit.encode())

    def _write(self, data):
        while data:
            n = os.write(self.fd, data)
            data = data[n:]

    def run(self):
        import pty
        import termios
        import tty
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                if self.cwd:
                    os.chdir(self.cwd)
                os.execvpe(self.argv[0], self.argv, self.env)
            except Exception as e:
                sys.stderr.write("agenthub run: cannot start %s: %s\n" % (self.argv[0], e))
            os._exit(127)
        self._winsize()
        old = None
        if os.isatty(0):
            old = termios.tcgetattr(0)
            tty.setraw(0)
            signal.signal(signal.SIGWINCH, lambda *_: self._winsize())
        stdin_open = True
        try:
            while True:
                rl = [self.fd] + ([0] if stdin_open else [])
                try:
                    r, _, _ = select.select(rl, [], [], 0.5)
                except InterruptedError:
                    continue
                if self.fd in r:
                    try:
                        data = os.read(self.fd, 65536)
                    except OSError as e:
                        if e.errno in (errno.EIO, errno.EBADF):
                            break
                        raise
                    if not data:
                        break
                    os.write(1, data)
                    self._seen(data)
                if 0 in r:
                    data = os.read(0, 4096)
                    if not data:
                        stdin_open = False
                    else:
                        self.last_in = time.time()
                        with self._wlock:
                            self._write(data)
        finally:
            if old is not None:
                termios.tcsetattr(0, termios.TCSAFLUSH, old)
        _, status = os.waitpid(self.pid, 0)
        return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)


# ------------------------------------------------------------------ the hub side

class Listener(threading.Thread):
    """Holds the agent's hub session: pulls what is queued for it and types it in."""

    daemon = True

    def __init__(self, session, agent, adapter, term, cwd):
        threading.Thread.__init__(self)
        self.session, self.agent, self.ad, self.term, self.cwd = session, agent, adapter, term, cwd
        self.conv = ""
        self.started = time.time()
        self.stop = threading.Event()

    def open(self):
        body = {"session": self.session, "cwd": self.cwd, "channel": True, "pid": os.getpid(),
                "agent": self.agent or "", "kind": self.ad.kind, "conversation": self.conv, "source": ""}
        body.update(git_info(self.cwd))
        out = client.call("POST", "/v1/sessions", body, timeout=10)
        if out.get("error"):
            log("bind failed:", out["error"])
        self.agent = (out.get("agent") or {}).get("name") or self.agent
        return out

    def footer(self):
        if self.ad.tools == "cli":
            return ("\n\n(Use the `agenthub` command in your shell for hub calls, e.g. `agenthub update <task> plan "
                    "\"...\"`, `agenthub task <id>`; `agenthub --help` lists them.)")
        return ""

    def deliver(self, items):
        """Type the items in once the agent can take them. False if we gave up waiting."""
        text = "\n\n---\n\n".join(it["content"] for it in items)
        if len(text) > MAX_PASTE:
            ids = ", ".join("#%s" % it["task_id"] for it in items if it.get("task_id"))
            text = ("[agent-hub] %d new item(s) for you%s -- too long to paste. Read them with hub_inbox "
                    "(or `agenthub inbox`)." % (len(items), " (tasks %s)" % ids if ids else ""))
        text += self.footer()
        deadline = time.time() + 45  # keep the long-poll alive: pull again, items come back
        while time.time() < deadline and not self.stop.is_set():
            if self.term.idle() and self.ad.ready(self.term.screen()):
                self.term.type(text)
                log("typed", ",".join(str(it["id"]) for it in items))
                return True
            time.sleep(0.5)
        return False

    def find_conversation(self):
        if self.conv or time.time() - self.started > 600 or not self.term.pid:
            return
        conv = self.ad.find_conversation(self.term.pid, self.cwd, self.started - 5)
        if conv:
            self.conv = conv
            try:
                self.open()
            except Exception as e:
                log("report conversation:", e)

    def run(self):
        backoff, acks, held = 2, [], {}
        while not self.stop.is_set():
            try:
                if not self.agent:
                    out = self.open()   # until named: keep the session fresh, the agent registers itself
                out = client.call("GET", "/v1/sessions/%s/pull?wait=40&ack=%s" % (self.session, ",".join(acks)),
                                  timeout=60)
                acks, backoff = [], 2
                if out.get("agent"):
                    self.agent = out["agent"]
                for it in out.get("items", []):
                    held[it["id"]] = it
                todo = [it for _, it in sorted(held.items()) if it["kind"] != "verdict"]
                acks += [str(i) for i, it in held.items() if it["kind"] == "verdict"]  # no dialog to answer here
                if todo and self.deliver(todo):
                    acks += [str(it["id"]) for it in todo]
                    held.clear()
                elif not todo:
                    held.clear()
                self.find_conversation()
            except client.HubRefused as e:
                log("pull:", e)
                if e.code == 404:
                    try:
                        self.open()
                    except Exception:
                        pass
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception as e:
                log("pull:", e)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def end(self):
        self.stop.set()
        try:
            client.call("POST", "/v1/sessions/%s/end" % self.session, {}, timeout=3)
        except Exception:
            pass


def git_info(cwd):
    def g(*a):
        try:
            return subprocess.run(["git", "-C", cwd] + list(a), capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            return ""
    return {"repo": g("config", "--get", "remote.origin.url") or g("rev-parse", "--show-toplevel"),
            "branch": g("rev-parse", "--abbrev-ref", "HEAD")}


# ------------------------------------------------------------------ entry

def main(kind, agent="", resume="", prompt="", extra=(), fresh=False):
    ad = adapters.get(kind)
    cwd = os.getcwd()
    if resume and not fresh:
        ok, why = ad.resumable(cwd, resume)
        if not ok:
            sys.stderr.write("agenthub run: not resuming (%s)\n" % why)
            resume = ""
    argv = ad.argv(resume=resume or None, prompt=prompt or None, extra=extra)
    env = dict(os.environ, HUB_KIND=ad.kind, **ad.env(agent))
    if agent:
        env["HUB_AGENT"] = agent
    if ad.push == "channel":
        # the agent's own MCP server holds the session and pushes into it
        os.execvpe(argv[0], argv, env)
    session = "%s-%s" % (ad.kind, uuid.uuid4().hex[:16])
    env.update(HUB_SESSION=session, HUB_WRAPPER_PID=str(os.getpid()))
    os.makedirs(client.RUN, exist_ok=True)
    rf = client.run_file(os.getpid())
    with open(rf, "w") as fh:
        json.dump({"session": session, "agent": agent, "kind": ad.kind, "pid": os.getpid(), "cwd": cwd,
                   "started": time.time()}, fh)
    term = Pty(argv, env)
    lst = Listener(session, agent, ad, term, cwd)
    try:
        lst.open()
    except Exception as e:
        sys.stderr.write("agenthub run: hub not reachable (%s); starting %s without it\n" % (e, ad.kind))
    lst.start()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: (term.pid and os.kill(term.pid, signal.SIGTERM)))
    try:
        code = term.run()
    finally:
        lst.end()
        try:
            os.remove(rf)
        except OSError:
            pass
        for f in os.listdir(client.RUN):  # marks of wrappers that died without cleaning up
            p = f.split(".")[0]
            if f.endswith(".json") and p.isdigit() and not procs.alive(int(p)):
                try:
                    os.remove(os.path.join(client.RUN, f))
                except OSError:
                    pass
    return code
