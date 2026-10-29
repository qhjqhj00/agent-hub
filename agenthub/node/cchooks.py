"""Claude Code plugin hooks (`agenthub cc-hook start|prompt`).

  start   SessionStart. `/resume` inside a running claude switches it to another
          conversation, but the channel server keeps the session id claude started
          with. Hand it the conversation id (it finds the file by its claude's pid).
  prompt  UserPromptSubmit. Sessions started with `agenthub run claude` get hub items
          pushed; a plain `claude` would never hear about them, so on each prompt we
          hand over whatever is queued. Best effort: a slow or absent hub never blocks."""
import json
import os
import subprocess
import sys

from .. import client

RUN = client.RUN


def ppid(pid):
    try:
        return int(open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        try:
            return int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True,
                                      text=True, timeout=3).stdout.strip() or 0)
        except Exception:
            return 0


def start():
    d = json.load(sys.stdin)
    if not d.get("session_id"):
        return
    pid = os.getppid()
    for _ in range(4):  # claude -> (shell) -> this hook
        if pid <= 1:
            return
        if os.path.exists(os.path.join(RUN, "%d.chan" % pid)):
            tmp = os.path.join(RUN, "%d.conv.tmp" % pid)
            with open(tmp, "w") as fh:
                json.dump({"conversation": d["session_id"], "source": d.get("source") or "",
                           "transcript": d.get("transcript_path") or ""}, fh)
            os.replace(tmp, os.path.join(RUN, "%d.conv" % pid))
            return
        pid = ppid(pid)


def prompt():
    if os.environ.get("HUB_CHANNEL") == "1" or os.environ.get("HUB_SESSION"):
        return  # pushed already: by the channel, or typed in by the `agenthub run` wrapper
    sid = json.load(sys.stdin).get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    items = client.call("GET", "/v1/sessions/%s/pull?wait=0&autoack=1" % sid, timeout=5).get("items", [])
    items = [i for i in items if i.get("kind") != "verdict"]
    if items:
        print("agent-hub: %d queued item(s) for you (start with `agenthub run claude` to get them pushed live):"
              % len(items))
        for i in items:
            print("---\n" + i["content"])


def main(which):
    try:
        {"start": start, "prompt": prompt}.get(which, prompt)()
    except Exception:
        pass
    sys.exit(0)
