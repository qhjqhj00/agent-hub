"""Claude Code plugin hooks (`agenthub cc-hook start|prompt`).

  start   SessionStart. `/resume` inside a running claude switches it to another
          conversation under the same MCP server. Hand that server the new conversation
          id (it finds the file by its claude's pid).
  prompt  UserPromptSubmit. A claude started without HUB_CHANNEL=1 gets nothing pushed,
          so on each prompt show whatever is queued for it. Best effort: a slow or
          absent hub never blocks the prompt.
"""
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


def _claude_pid():
    """The claude this hook runs under: the nearest ancestor with a .chan mark."""
    pid = os.getppid()
    for _ in range(4):  # claude -> (shell) -> this hook
        if pid <= 1:
            return 0
        if os.path.exists(os.path.join(RUN, "%d.chan" % pid)):
            return pid
        pid = ppid(pid)
    return 0


def start():
    d = json.load(sys.stdin)
    pid = _claude_pid()
    if not d.get("session_id") or not pid:
        return
    tmp = os.path.join(RUN, "%d.conv.tmp" % pid)
    with open(tmp, "w") as fh:
        json.dump({"conversation": d["session_id"], "source": d.get("source") or "",
                   "transcript": d.get("transcript_path") or ""}, fh)
    os.replace(tmp, os.path.join(RUN, "%d.conv" % pid))


def prompt():
    if os.environ.get("HUB_CHANNEL") == "1":
        return  # the channel pushes already
    pid = _claude_pid()
    if not pid:
        return
    try:
        name = json.load(open(os.path.join(RUN, "%d.chan" % pid))).get("name")
    except (OSError, ValueError):
        return
    sess = client.read_session(name) if name else None
    if not sess:
        return
    items = client.session_call(sess, "GET", "/v1/sessions/%s/pull?wait=0" % sess["session_id"], timeout=5)["items"]
    if items:
        print("agent-hub: %d queued item(s) for you (start claude with HUB_CHANNEL=1 to get them pushed live):"
              % len(items))
        for i in items:
            print("---\n" + i["text"])
        client.session_call(sess, "POST", "/v1/sessions/%s/delivered" % sess["session_id"],
                            {"ids": [i["id"] for i in items]}, timeout=5)


def main(which):
    try:
        {"start": start, "prompt": prompt}.get(which, prompt)()
    except Exception:
        pass
    sys.exit(0)
