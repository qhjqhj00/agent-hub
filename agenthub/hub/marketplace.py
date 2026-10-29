"""The hub as a Claude Code plugin marketplace, and the team's skill store.

The hub serves a git repository at /marketplace.git (smart HTTP, read-only),
and every node with Claude Code adds it once (`agenthub setup claude` does):

    claude plugin marketplace add http://x:<token>@<hub>/marketplace.git

Two plugins live in it:
  agent-hub    the MCP server hookup, the collaboration skill, the hooks. Its
               source is agenthub/plugin/marketplace/plugins/agent-hub and is
               re-synced on every hub start.
  team-skills  skills any agent can publish (POST /v1/skills, `hub skill
               publish <dir>`). These live only in the work tree, so the
               work tree's git history *is* the skill history.

Each change bumps the plugin's version, which is what `claude plugin update`
compares; hubd runs that on every node after a publish. Agents other than
Claude Code get the same skills as files: hubd copies them into each agent's
skill directory (see agenthub/node/adapters).
"""
import json
import os
import re
import shutil
import subprocess
import threading

from .store import DATA_DIR, PKG

SRC = os.path.join(PKG, "plugin", "marketplace")
WORK = os.path.join(DATA_DIR, "marketplace-work")
BARE = os.path.join(DATA_DIR, "marketplace.git")
NAME = "agent-hub"
_lock = threading.Lock()


def _git(*args, cwd=WORK):
    env = dict(os.environ, GIT_AUTHOR_NAME="agent-hub", GIT_AUTHOR_EMAIL="hub@localhost",
               GIT_COMMITTER_NAME="agent-hub", GIT_COMMITTER_EMAIL="hub@localhost")
    return subprocess.run(["git"] + list(args), cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


def _tree_hash(path):
    """Content hash of a directory, to tell whether a re-sync changed anything."""
    import hashlib
    h = hashlib.sha1()
    for root, dirs, files in sorted(os.walk(path)):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, path).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def _bump(plugin):
    pj = os.path.join(WORK, "plugins", plugin, ".claude-plugin", "plugin.json")
    meta = json.load(open(pj))
    major, minor, patch = (meta.get("version") or "0.1.0").split("+")[0].split(".")[:3]
    meta["version"] = "%s.%s.%d" % (major, minor, int(patch) + 1)
    json.dump(meta, open(pj, "w"), indent=2)
    return meta["version"]


def _publish(msg):
    _git("add", "-A")
    if not _git("status", "--porcelain").strip():
        return False
    _git("commit", "-qm", msg)
    _git("push", "-q", BARE, "HEAD:main")
    _git("update-server-info", cwd=BARE)
    return True


def sync():
    """Bring the served marketplace up to date with the source tree."""
    with _lock:
        if not os.path.isdir(BARE):
            os.makedirs(BARE)
            _git("init", "-q", "--bare", "-b", "main", cwd=BARE)
        if not os.path.isdir(os.path.join(WORK, ".git")):
            os.makedirs(WORK, exist_ok=True)
            _git("init", "-q", "-b", "main")
        os.makedirs(os.path.join(WORK, ".claude-plugin"), exist_ok=True)
        shutil.copy(os.path.join(SRC, ".claude-plugin", "marketplace.json"),
                    os.path.join(WORK, ".claude-plugin", "marketplace.json"))
        state_p = os.path.join(DATA_DIR, "marketplace-state.json")
        state = json.load(open(state_p)) if os.path.isfile(state_p) else {}
        for plugin in sorted(os.listdir(os.path.join(SRC, "plugins"))):
            src, dst = os.path.join(SRC, "plugins", plugin), os.path.join(WORK, "plugins", plugin)
            if plugin == "team-skills" and os.path.isdir(dst):
                # published skills live only in the work tree; seed skills are added if missing
                added = False
                for sk in os.listdir(os.path.join(src, "skills")):
                    if not os.path.isdir(os.path.join(dst, "skills", sk)):
                        shutil.copytree(os.path.join(src, "skills", sk), os.path.join(dst, "skills", sk))
                        added = True
                if added:
                    _bump(plugin)
                continue
            h = _tree_hash(src)
            if state.get(plugin) == h and os.path.isdir(dst):
                continue
            served = None
            pj = os.path.join(dst, ".claude-plugin", "plugin.json")
            if os.path.isfile(pj):
                served = json.load(open(pj)).get("version")
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if served:
                meta = json.load(open(pj))
                if _vt(meta["version"]) <= _vt(served):
                    meta["version"] = served
                    json.dump(meta, open(pj, "w"), indent=2)
                    _bump(plugin)
            state[plugin] = h
        json.dump(state, open(state_p, "w"), indent=2)
        _publish("sync from source")


def _vt(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def list_skills():
    out = []
    root = os.path.join(WORK, "plugins")
    if not os.path.isdir(root):
        return {"skills": []}
    for plugin in sorted(os.listdir(root)):
        sk_root = os.path.join(root, plugin, "skills")
        ver = json.load(open(os.path.join(root, plugin, ".claude-plugin", "plugin.json"))).get("version")
        for sk in sorted(os.listdir(sk_root)) if os.path.isdir(sk_root) else []:
            md = os.path.join(sk_root, sk, "SKILL.md")
            desc = ""
            if os.path.isfile(md):
                m = re.search(r"^description:\s*(.+)$", open(md).read(), re.M)
                desc = m.group(1).strip() if m else ""
            out.append({"plugin": plugin, "version": ver, "skill": sk, "description": desc,
                        "files": sorted(os.path.relpath(os.path.join(r, f), os.path.join(sk_root, sk))
                                        for r, _, fs in os.walk(os.path.join(sk_root, sk)) for f in fs)})
    try:
        hist = _git("log", "--format=%h %ad %s", "--date=short", "-n", "15")
    except Exception:
        hist = ""
    return {"skills": out, "history": hist.splitlines()}


def publish_skill(name, files, by, plugin="team-skills"):
    if not re.match(r"^[a-z0-9][a-z0-9\-]{0,63}$", name or ""):
        raise ValueError("skill name: lowercase letters, digits, hyphens")
    if plugin != "team-skills":
        raise ValueError("only team-skills accepts published skills")
    if "SKILL.md" not in files:
        raise ValueError("a skill needs a SKILL.md")
    with _lock:
        dst = os.path.join(WORK, "plugins", plugin, "skills", name)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        for rel, content in files.items():
            rel = os.path.normpath(rel)
            if rel.startswith("..") or os.path.isabs(rel):
                raise ValueError("bad path %r" % rel)
            p = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(content)
        ver = _bump(plugin)
        _publish("skill %s published by %s" % (name, by))
    return {"plugin": plugin, "skill": name, "version": ver}


def remove_skill(name, by, plugin="team-skills"):
    with _lock:
        dst = os.path.join(WORK, "plugins", plugin, "skills", name)
        if not os.path.isdir(dst):
            raise ValueError("no skill %r" % name)
        shutil.rmtree(dst)
        ver = _bump(plugin)
        _publish("skill %s removed by %s" % (name, by))
    return {"plugin": plugin, "version": ver}


def export_skills(plugin="team-skills"):
    """{skill: {relative path: text}} -- for agents that read skill directories (Codex, pi)."""
    out = {}
    root = os.path.join(WORK, "plugins", plugin, "skills")
    for sk in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        files = {}
        for r, _, fs in os.walk(os.path.join(root, sk)):
            for f in fs:
                p = os.path.join(r, f)
                try:
                    with open(p) as fh:
                        files[os.path.relpath(p, os.path.join(root, sk))] = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
        if "SKILL.md" in files:
            out[sk] = files
    return out
