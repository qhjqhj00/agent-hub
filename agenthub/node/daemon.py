"""hubd -- the per-node daemon (`agenthub node`).

Long-polls the hub for orders addressed to this node and carries them out:

  wake            start `agenthub run <kind> --agent <name>` in the agent's workspace
                  inside tmux (session `hub-<name>`), resuming its last conversation
                  when that is still on disk, and click through known start-up dialogs
  stop            end that tmux session (and anything left running for the agent)
  stop_session    close one agent process: the copy left behind when its conversation
                  was resumed in another terminal
  update_plugins  refresh the Claude Code plugins and copy shared skills to other agents
  peek            return what the agent's tmux screen shows (diagnosing a stuck session)
  keys            type into that screen (answer a dialog nobody anticipated)
  self_update     replace this node's agenthub.pyz with the hub's (pyz installs only)

Every poll doubles as a heartbeat carrying what this node runs, which is what
the dashboard's Nodes view shows.
"""
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

from .. import __version__, client
from . import adapters, install_kind, procs, self_cmd, zip_path

UPDATE_EVERY = 600
# environment the agents need that tmux would not pass on
PASS_ENV = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "LANG", "LC_ALL")
BIN = os.path.expanduser("~/.local/bin")


def log(*a):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


def call(method, path, body=None, timeout=70):
    return client.call(method, path, body, timeout)


def sh(*args, timeout=30, check=False):
    env = dict(os.environ)
    env["PATH"] = BIN + os.pathsep + env.get("PATH", "")
    r = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, env=env)
    if check and r.returncode:
        raise RuntimeError("%s: %s" % (" ".join(args), (r.stderr or r.stdout).strip()[:500]))
    return r


def tmux_name(agent):
    return "hub-" + re.sub(r"[^A-Za-z0-9_-]", "_", agent)


def tmux_sessions():
    if not shutil.which("tmux"):
        return []
    r = sh("tmux", "ls", "-F", "#{session_name}")
    return [s for s in r.stdout.split() if s.startswith("hub-")] if r.returncode == 0 else []


INSTALLED = {}


def info():
    return {"hubd": __version__, "agents": INSTALLED, "claude": INSTALLED.get("claude", ""),
            "os": platform.system().lower(), "tmux": tmux_sessions(), "home": os.path.expanduser("~"),
            "install": install_kind(), "has_tmux": bool(shutil.which("tmux"))}


def tmux_new(name, ws, shell_cmd):
    """Start the session's tmux outside hubd's own cgroup when systemd is around, so that
    restarting (or upgrading) hubd does not take every woken agent down with it."""
    if not shutil.which("tmux"):
        raise RuntimeError("tmux is not installed on this node; hubd needs it to start agents")
    base = ["tmux", "new-session", "-d", "-s", name, "-x", "220", "-y", "50", "-c", ws, shell_cmd]
    if shutil.which("systemd-run") and os.environ.get("INVOCATION_ID"):
        r = sh("systemd-run", "--user", "--scope", "--quiet", "--collect", "--unit",
               "hub-tmux-%s-%d" % (re.sub(r"[^A-Za-z0-9_-]", "_", name), int(time.time())), *base)
        if r.returncode == 0:
            return
    sh(*base, check=True)


def pane(name):
    r = sh("tmux", "capture-pane", "-p", "-t", name)
    return r.stdout if r.returncode == 0 else ""


CURSORS = ("❯", "›", ">")


def pick(name, text, option):
    """Move the selection cursor onto the line containing `option` and press Enter."""
    lines = [l for l in text.splitlines() if l.strip()]
    tgt = next((i for i, l in enumerate(lines) if option in l), None)
    cur = None
    if tgt is not None:  # the cursor nearest the option: a dialog's, not the input prompt's
        marked = [i for i, l in enumerate(lines) if l.lstrip()[:1] in CURSORS]
        cur = min(marked, key=lambda i: abs(i - tgt)) if marked else None
    if cur is None or tgt is None:
        sh("tmux", "send-keys", "-t", name, "Enter")
        return
    key = "Down" if tgt > cur else "Up"
    for _ in range(abs(tgt - cur)):
        sh("tmux", "send-keys", "-t", name, key)
        time.sleep(0.15)
    sh("tmux", "send-keys", "-t", name, "Enter")


def agent_listening(agent):
    try:
        a = call("GET", "/v1/agents/" + agent, timeout=10)
        return a.get("presence") == "live" and a.get("channel")
    except Exception:
        return False


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


def wake(o):
    agent, args = o["agent"], o.get("args") or {}
    ad = adapters.get(args.get("kind") or "claude")
    ws = os.path.expanduser(args.get("workspace") or "~")
    name = tmux_name(agent)
    if not os.path.isdir(ws):
        raise RuntimeError("workspace %s does not exist on this node" % ws)
    if not ad.available():
        raise RuntimeError("%s (%s) is not installed on this node" % (ad.title, ad.binary))
    if name in tmux_sessions():
        if agent_listening(agent) and ad.ready(pane(name)):
            return "already running in tmux %s" % name
        # there but unusable (stuck loading, sitting on a dialog, or the agent gone): replace it
        log("  %s exists but is not ready; replacing it with a fresh session" % name)
        sh("tmux", "kill-session", "-t", name)
        args = dict(args, fresh=True)
        o = dict(o, args=args)
    orphans = procs.with_env("HUB_AGENT", agent)
    if orphans:
        log("  killing headless processes for %s: %s" % (agent, orphans))
        procs.kill(orphans)
    cmd = self_cmd() + ["run", ad.kind, "--agent", agent]
    resumed, why = ad.resumable(ws, args.get("resume"))
    if resumed and not args.get("fresh"):
        cmd += ["--resume", args["resume"]]
    else:
        resumed = False
    if args.get("prompt"):
        cmd += ["--prompt", args["prompt"]]
    parts = [BIN] + [x for x in os.environ.get("PATH", "").split(os.pathsep) if x and x != BIN]
    env_path = os.pathsep.join(dict.fromkeys(parts))
    # tmux starts the command with the tmux server's environment, not ours: carry over what matters
    keep = {k: v for k, v in os.environ.items() if k.startswith(("HUB_", "AGENTHUB_")) or k in PASS_ENV}
    keep.pop("HUB_SESSION", None), keep.pop("HUB_WRAPPER_PID", None), keep.pop("HUB_AGENT", None)
    keep["PATH"] = env_path
    # through a 0600 file, not the command line: some of it is secret and `ps` shows command lines
    os.makedirs(client.RUN, exist_ok=True)
    envf = os.path.join(client.RUN, "wake-%s.env" % re.sub(r"[^A-Za-z0-9_.-]", "_", agent))
    fd = os.open(envf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("".join("export %s=%s\n" % (k, _q(v)) for k, v in sorted(keep.items())))
    shell_cmd = ". %s; %s; echo; echo '[hubd] agent exited'; sleep 600" % (_q(envf), " ".join(_q(c) for c in cmd))
    tmux_new(name, ws, shell_cmd)
    try:
        res = autopilot(name, agent, ad, started=True, resumed=resumed)
    except NotReady:
        if not resumed:
            raise
        # the resumed session never became usable: start over without it
        log("  resume of %s did not become ready; starting fresh" % agent)
        sh("tmux", "kill-session", "-t", name)
        procs.kill(procs.with_env("HUB_AGENT", agent))
        return wake(dict(o, args=dict(args, fresh=True))) + " (resume was too slow; started fresh)"
    return res + ("" if resumed or args.get("fresh") or not why else " (fresh: %s)" % why)


class NotReady(RuntimeError):
    pass


def autopilot(name, agent, ad, started, resumed=False):
    deadline = time.time() + (90 if resumed else 120)
    picked = []

    def answer_dialogs(text):
        for marker, option in ad.dialogs:
            if marker in text and option in text:
                pick(name, text, option)
                picked.append(marker.split()[0].lower())
                time.sleep(2)
                return True
        return False

    while time.time() < deadline:
        time.sleep(1.5)
        text = pane(name)
        if answer_dialogs(text):
            continue
        if "[hubd] agent exited" in text:
            raise RuntimeError("%s exited during start-up:\n%s" % (ad.kind, "\n".join(text.strip().splitlines()[-12:])))
        if agent_listening(agent) and ad.ready(text):
            # Some dialogs (e.g. on --resume) come up after the hub connection; a session sitting
            # on one never acts on what is pushed to it. Look again before declaring success.
            time.sleep(6)
            tail = "\n".join(pane(name).strip().splitlines()[-18:])
            while answer_dialogs(tail):
                tail = "\n".join(pane(name).strip().splitlines()[-18:])
            if any(b in tail for b in ad.blockers):
                raise RuntimeError("session is up but waiting on a dialog hubd does not know; answer it from the "
                                   "dashboard (Screen) or `tmux attach -t %s`:\n%s" % (name, tail))
            return "%s in tmux %s%s%s" % ("started" if started else "recovered", name,
                                          " (resumed)" if resumed else "",
                                          " · dialogs: " + ",".join(picked) if picked else "")
    raise NotReady("session did not become ready (no input box) in time; screen:\n" +
                   "\n".join(pane(name).strip().splitlines()[-15:]))


def stop(o):
    name = tmux_name(o["agent"])
    done = []
    if name in tmux_sessions():
        sh("tmux", "send-keys", "-t", name, "C-c")
        time.sleep(0.5)
        sh("tmux", "send-keys", "-t", name, "C-c")
        time.sleep(2)
        sh("tmux", "kill-session", "-t", name)
        done.append("tmux %s" % name)
    orphans = procs.with_env("HUB_AGENT", o["agent"])
    if orphans:
        procs.kill(orphans)
        done.append("headless %s" % ",".join(map(str, orphans)))
    return "stopped " + " + ".join(done) if done else "not running"


def session_pid_ok(pid, session):
    """Is `pid` the agent process that runs hub session `session`? It (or a child, like its MCP
    server) carries the session id in its environment; a reused pid will not."""
    wants = [("CLAUDE_CODE_SESSION_ID=" + session).encode(), ("HUB_SESSION=" + session).encode()]
    for p in [pid] + [c for c in procs.all_pids() if procs.parent(c) == pid]:
        env = procs.environ(p) or []
        if any(w in env for w in wants):
            return True
    return False


def stop_session(o):
    args = o.get("args") or {}
    pid, session = int(args.get("pid") or 0), args.get("session") or ""
    if not pid or not procs.alive(pid):
        return "already gone"
    if not session or not session_pid_ok(pid, session):
        return "pid %d is not session %s any more; left alone" % (pid, session[:8])
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.5)
        if not procs.alive(pid):
            return "closed %d (session %s)" % (pid, session[:8])
    os.kill(pid, signal.SIGKILL)
    return "killed %d (session %s): it ignored SIGTERM" % (pid, session[:8])


# ------------------------------------------------------------------ skills and plugins

SYNCED = os.path.join(client.HOME, "skills-synced.json")


def sync_skills():
    """Copy the team's shared skills into every installed agent that reads skill directories
    (Claude Code gets them through its plugin instead)."""
    targets = [a for a in adapters.all_adapters().values() if a.skills_dir and a.available()]
    if not targets:
        return "no agents with skill directories"
    skills = call("GET", "/v1/skills/export", timeout=60).get("skills", {})
    try:
        before = json.load(open(SYNCED))
    except (OSError, ValueError):
        before = {}
    after, out = {}, []
    for ad in targets:
        root = ad.skills_dir
        for name, files in skills.items():
            dst = os.path.join(root, name)
            if os.path.isdir(dst) and name not in before.get(ad.kind, []):
                continue  # a skill of the same name the user made: not ours to overwrite
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            for rel, content in files.items():
                p = os.path.join(dst, os.path.normpath(rel))
                if not p.startswith(dst + os.sep):
                    continue
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as fh:
                    fh.write(content)
            after.setdefault(ad.kind, []).append(name)
        for gone in set(before.get(ad.kind, [])) - set(skills):
            shutil.rmtree(os.path.join(root, gone), ignore_errors=True)
        out.append("%s: %d skill(s)" % (ad.kind, len(after.get(ad.kind, []))))
    os.makedirs(client.HOME, exist_ok=True)
    json.dump(after, open(SYNCED, "w"))
    return ", ".join(out)


def update_plugins(o=None):
    out = []
    cl = adapters.get("claude")
    if cl.available():
        out += cl.update_plugins()
    try:
        out.append(sync_skills())
    except Exception as e:
        out.append("skills: %s" % e)
    return "\n".join(out)


def peek(o):
    name = tmux_name(o["agent"])
    if name not in tmux_sessions():
        orphans = procs.with_env("HUB_AGENT", o["agent"])
        if orphans:
            return ("(%s is not in tmux, but its processes %s are running without a terminal -- left over from a "
                    "killed tmux. It receives pushes but cannot work. Stop it, then wake it.)"
                    % (name, ",".join(map(str, orphans))))
        return "(%s is not running in tmux on this node)" % name
    r = sh("tmux", "capture-pane", "-p", "-J", "-S", "-60", "-t", name)
    lines = [l.rstrip() for l in r.stdout.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)[-8000:]


TMUX_KEYS = {"Enter", "Escape", "Up", "Down", "Left", "Right", "Tab", "BTab", "C-c", "Space", "BSpace"}


def keys(o):
    name = tmux_name(o["agent"])
    if name not in tmux_sessions():
        raise RuntimeError("%s is not running in tmux on this node" % name)
    args = o.get("args") or {}
    if args.get("text"):
        sh("tmux", "send-keys", "-t", name, "-l", args["text"])
        time.sleep(0.3)
    for k in args.get("keys") or []:
        if k not in TMUX_KEYS:
            raise RuntimeError("key %r not allowed" % k)
        sh("tmux", "send-keys", "-t", name, k)
        time.sleep(0.25)
    time.sleep(1.5)
    return peek(o)


RESTART = False


def self_update(o=None):
    """Keep a pyz install in step with the hub; hubd restarts itself if it changed."""
    global RESTART
    path = zip_path()
    if not path:
        return "installed with %s; update it the same way" % install_kind()
    data = client.fetch("/client/agenthub.pyz")
    with open(path, "rb") as fh:
        old = fh.read()
    if not data or data == old:
        return "up to date"
    tmp = path + ".new"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    RESTART = True
    return "updated %s" % path


def restart():
    log("agenthub changed; restarting hubd")
    os.execv(sys.executable, [sys.executable, zip_path() or sys.argv[0], "node"])


HANDLERS = {"wake": wake, "stop": stop, "stop_session": stop_session, "update_plugins": update_plugins,
            "peek": peek, "keys": keys, "self_update": self_update}


def run_order(o):
    try:
        log("order", o["id"], o["kind"], o.get("agent"), o.get("reason"))
        fn = HANDLERS.get(o["kind"])
        if not fn:
            raise RuntimeError("this hubd (%s) does not know order %r" % (__version__, o["kind"]))
        res = fn(o)
        call("POST", "/v1/orders/%d" % o["id"], {"state": "done", "result": res}, timeout=15)
        log("  done:", res if o["kind"] != "peek" else "(screen)")
    except Exception as e:
        log("  failed:", e)
        try:
            call("POST", "/v1/orders/%d" % o["id"], {"state": "failed", "result": str(e)}, timeout=15)
        except Exception:
            pass
    if RESTART:
        restart()


def periodic():
    while True:
        try:
            INSTALLED.update(adapters.installed())
            if install_kind() == "pyz":
                self_update()
                if RESTART:
                    restart()
            update_plugins()
        except Exception:
            traceback.print_exc()
        time.sleep(UPDATE_EVERY)


def main():
    INSTALLED.update(adapters.installed())
    log("hubd %s starting; hub=%s agents=%s" % (__version__, client.url(),
                                                ", ".join("%s %s" % kv for kv in INSTALLED.items()) or "none"))
    if not shutil.which("tmux"):
        log("warning: tmux not found; this node cannot wake agents (install tmux)")
    threading.Thread(target=periodic, daemon=True).start()
    backoff = 2
    acks = []
    while True:
        try:
            # ack what the previous poll delivered: an order handed to a poll that never comes
            # back (hubd restarted mid-poll) stays unacked and the hub hands it out again
            out = call("POST", "/v1/nodes/orders?wait=50&ack=%s" % ",".join(acks), info())
            backoff = 2
            acks = [str(o["id"]) for o in out.get("orders", [])]
            for o in out.get("orders", []):
                threading.Thread(target=run_order, args=(o,), daemon=True).start()
        except client.HubRefused as e:
            log("hub refused:", e)
            time.sleep(min(backoff * 5, 120))
            backoff = min(backoff * 2, 60)
        except Exception as e:
            log("poll error:", e)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
