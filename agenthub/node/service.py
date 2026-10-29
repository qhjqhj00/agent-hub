"""Run the hub or the node daemon as a user service: systemd on Linux, launchd on macOS."""
import os
import platform
import shlex
import subprocess

from .. import client
from . import self_cmd

UNITS = {"hub": ("agent-hub", "agent-hub (hub server)"), "node": ("agent-hubd", "agent-hub node daemon (hubd)")}


def _env_lines(env):
    return "".join("Environment=%s=%s\n" % (k, v) for k, v in sorted(env.items()))


def install(which, args=(), env=None):
    name, desc = UNITS[which]
    cmd = self_cmd() + [which if which == "node" else "serve"] + list(args)
    env = dict(env or {})
    env.setdefault("PATH", os.environ.get("PATH", ""))
    log = os.path.join(client.HOME, "%s.log" % name)
    os.makedirs(client.HOME, exist_ok=True)
    if platform.system() == "Darwin":
        label = "dev.agent-hub.%s" % name
        pl = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % label)
        os.makedirs(os.path.dirname(pl), exist_ok=True)
        prog = "".join("<string>%s</string>" % _x(c) for c in cmd)
        envs = "".join("<key>%s</key><string>%s</string>" % (_x(k), _x(v)) for k, v in env.items())
        with open(pl, "w") as fh:
            fh.write("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>%s</string>
<key>ProgramArguments</key><array>%s</array>
<key>EnvironmentVariables</key><dict>%s</dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>%s</string><key>StandardErrorPath</key><string>%s</string>
</dict></plist>
""" % (label, prog, envs, log, log))
        subprocess.run(["launchctl", "unload", pl], capture_output=True)
        subprocess.run(["launchctl", "load", pl], check=True)
        return "launchd: %s (log %s)" % (label, log)
    unit = os.path.expanduser("~/.config/systemd/user/%s.service" % name)
    os.makedirs(os.path.dirname(unit), exist_ok=True)
    with open(unit, "w") as fh:
        fh.write("""[Unit]
Description=%s
After=network-online.target

[Service]
ExecStart=%s
%sRestart=always
RestartSec=5
%s
[Install]
WantedBy=default.target
""" % (desc, " ".join(shlex.quote(c) for c in cmd), _env_lines(env),
            # restarting hubd must not kill the agents' tmux sessions it started
            "KillMode=process\n" if which == "node" else ""))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", name], capture_output=True)
    subprocess.run(["systemctl", "--user", "restart", name], check=True)
    note = ""
    r = subprocess.run(["loginctl", "enable-linger", os.environ.get("USER", "")], capture_output=True)
    if r.returncode:
        note = " (run `sudo loginctl enable-linger $USER` so it keeps running after you log out)"
    return "systemd: %s (systemctl --user status %s)%s" % (name, name, note)


def uninstall(which):
    name, _ = UNITS[which]
    if platform.system() == "Darwin":
        pl = os.path.expanduser("~/Library/LaunchAgents/dev.agent-hub.%s.plist" % name)
        subprocess.run(["launchctl", "unload", pl], capture_output=True)
        if os.path.exists(pl):
            os.remove(pl)
        return "removed %s" % pl
    subprocess.run(["systemctl", "--user", "disable", "--now", name], capture_output=True)
    unit = os.path.expanduser("~/.config/systemd/user/%s.service" % name)
    if os.path.exists(unit):
        os.remove(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return "removed %s" % unit


def _x(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
