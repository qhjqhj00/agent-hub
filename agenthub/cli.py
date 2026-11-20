"""agenthub -- one command for the hub, the nodes, the agents, and the team.

  agenthub up                      the hub on this machine, with this machine as a node
  agenthub serve                   just the hub
  agenthub join URL TOKEN          make this machine a node of that hub
  agenthub setup [KIND...]         (re)install the hub integration into agent CLIs
  agenthub migrate --from V1.db    copy a v1 hub's data into a fresh v2 database
  agenthub delegate / tasks / ...  team commands (agenthub --help lists them)

The hub never starts agents: start each one yourself in its workspace (the dashboard
and `agenthub start NAME` print the command), then tell it its name.
"""
import argparse
import os
import sys
import threading
import time

from . import __version__, client


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------ hub

def _hub_env(a):
    if getattr(a, "identity", None):
        os.environ["HUB_IDENTITY"] = a.identity
    if getattr(a, "data", None):
        os.environ["HUB_DATA"] = os.path.abspath(os.path.expanduser(a.data))


def c_serve(a):
    _hub_env(a)
    from .hub import serve
    from .hub.store import init_db
    init_db()
    serve(a.host, a.port)


def c_up(a):
    """The hub, and this machine configured as its node, in one process."""
    _hub_env(a)
    os.environ.setdefault("HUB_IDENTITY", "token")
    from .hub import identity, serve
    from .hub.store import init_db
    init_db()
    ready = threading.Event()
    t = threading.Thread(target=serve, args=(a.host, a.port, ready), daemon=True)
    t.start()
    if not ready.wait(20):
        die("the hub did not start")
    local = "127.0.0.1" if a.host in ("0.0.0.0", "::", "") else a.host
    url = "http://%s:%d" % (local, ready.port)
    tok = identity.admin_token()
    client.save(url=url, token=tok)
    if not a.no_setup:
        _setup_agents(a.kinds, quiet=False)
    print("""
agent-hub %s is up.

  dashboard   %s/?token=%s
  start an agent in its workspace, then tell it its name ("你叫 alice");
  `agenthub start NAME` prints the command for an agent the hub already knows.
""" % (__version__, url, tok))
    try:
        while t.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print()


def c_token(a):
    _hub_env(a)
    from .hub import identity
    if not os.path.isfile(identity.ADMIN_FILE) and not os.environ.get("HUB_TOKEN"):
        die("no hub data here (%s); run this on the hub machine" % identity.ADMIN_FILE)
    print(identity.admin_token())


def c_migrate(a):
    _hub_env(a)
    from .hub import migrate
    migrate.main(a.src, a.dest, a.force)


# ------------------------------------------------------------------ node

def c_join(a):
    url = a.url.rstrip("/")
    client.save(url=url, token=a.token)
    try:
        me = client.call("GET", "/v1/whoami", timeout=10)
    except Exception as e:
        die("cannot use %s: %s" % (url, e))
    print("joined %s as node %s (%s)" % (url, me.get("node"), me.get("role")))
    if not a.no_setup:
        _setup_agents(a.kinds, quiet=False)


def _setup_agents(kinds, quiet=True, remove=False, workspace=None, agent_name=""):
    from .node import adapters, self_cmd
    allk = adapters.all_adapters()
    want = kinds or [k for k, ad in allk.items() if ad.available()]
    if not want and not quiet:
        print("no agent CLIs found on PATH (claude, agy, codex, pi); install one and run `agenthub setup`")
    for k in want:
        ad = adapters.get(k)
        if not ad.available() and k != "agy":
            print("%s: not installed, skipped" % k)
            continue
        try:
            lines = ad.teardown(workspace=workspace) if remove else ad.setup(
                self_cmd(), workspace=workspace, agent_name=agent_name
            )
        except Exception as e:
            lines = ["%s: setup failed: %s" % (k, e)]
        for line in lines:
            print("  " + line)


def c_setup(a):
    _setup_agents(a.kinds, quiet=False, remove=a.remove, workspace=getattr(a, "workspace", None),
                  agent_name=getattr(a, "agent_name", "") or "")


def c_t3_bridge(a):
    from .bridges import t3_bridge
    t3_bridge.main()


def c_codex_bridge(a):
    from .bridges import codex_bridge
    codex_bridge.main(endpoint=getattr(a, "endpoint", "") or "")


def c_agy_hook(a):
    from .bridges import agy_hook
    agy_hook.main([a.agent_name] if getattr(a, "agent_name", None) else [])


def c_mcp(a):
    from .node import mcp
    mcp.main(a.kind)


def c_cchook(a):
    from .node import cchooks
    cchooks.main(a.which)


def c_doctor(a):
    from .node import install_kind, self_cmd
    ok = True
    print("agenthub %s (%s: %s)" % (__version__, install_kind(), " ".join(self_cmd())))
    print("config   %s -> %s, token %s" % (client.CFG, client.url(), "set" if client.token() else "missing"))
    try:
        me = client.call("GET", "/v1/whoami", timeout=5)
        print("hub      ok, you are node %s (%s)" % (me.get("node"), me.get("role")))
    except Exception as e:
        ok = False
        print("hub      PROBLEM: %s" % e)
    for s in client.sessions():
        print("session  %-20s %-6s push=%-7s %s" % (s["name"], s.get("kind"), s.get("push"), s.get("session_id")))
    sys.exit(0 if ok else 1)


# ------------------------------------------------------------------ parser

def build():
    from . import team
    ap = argparse.ArgumentParser(prog="agenthub", description="agent-hub: coordinate coding agents across machines",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Start here: `agenthub up` on one machine.")
    ap.add_argument("--version", action="version", version="agenthub " + __version__)
    ap.add_argument("--agent", help="act as this agent's session (team commands)")
    sp = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    def add(name, fn, *args, **kw):
        p = sp.add_parser(name, help=kw.pop("help", None), description=kw.pop("desc", None))
        for spec in args:
            flags, opts = spec[0], (spec[1] if len(spec) > 1 else {})
            p.add_argument(*flags, **opts)
        p.set_defaults(fn=fn)
        return p

    hubopts = ((["--identity"], {"choices": ["token", "tailscale", "open"],
                                 "help": "how callers are identified (default: token)"}),
               (["--data"], {"help": "data directory (default ~/.agent-hub/hub)"}))
    port = int(os.environ.get("HUB_PORT", 8787))
    add("up", c_up, (["--host"], {"default": os.environ.get("HUB_HOST", "127.0.0.1"),
                                  "help": "bind address (0.0.0.0 to let other machines join)"}),
        (["--port"], {"type": int, "default": port}), *hubopts,
        (["--no-setup"], {"action": "store_true", "help": "do not touch agent CLI configs"}),
        (["kinds"], {"nargs": "*", "help": "agents to set up (default: all installed)"}),
        help="run the hub, with this machine as a node (start here)")
    add("serve", c_serve, (["--host"], {"default": os.environ.get("HUB_HOST", "127.0.0.1")}),
        (["--port"], {"type": int, "default": port}), *hubopts, help="run only the hub server")
    add("token", c_token, (["--data"],), help="print the hub's admin token (on the hub machine)")
    add("migrate", c_migrate, (["--from"], {"dest": "src", "required": True, "help": "the v1 hub.db (opened read-only)"}),
        (["--to"], {"dest": "dest", "help": "the v2 database to create (default: this hub's)"}),
        (["--force"], {"action": "store_true", "help": "replace an existing v2 database"}), (["--data"],),
        help="copy a v1 hub's data into a new v2 database")
    add("join", c_join, (["url"],), (["token"],), (["--no-setup"], {"action": "store_true"}), (["kinds"], {"nargs": "*"}),
        help="connect this machine to a hub as a node")
    add("setup", c_setup, (["kinds"], {"nargs": "*", "help": "claude agy codex pi (default: installed)"}),
        (["--workspace"], {"help": "workspace path (writes .agents/hooks.json for agy)"}),
        (["--agent-name"], {"help": "optional fixed agent name for agy hooks"}),
        (["--remove"], {"action": "store_true"}), help="install the hub integration into agent CLIs")
    add("t3-bridge", c_t3_bridge, help="deliver hub messages to running T3 Code (agy) threads")
    add("codex-bridge", c_codex_bridge,
        (["--endpoint"], {"help": "codex app-server endpoint (unix://... or ws://...)"}),
        help="deliver hub messages to running Codex app-server threads")
    add("agy-hook", c_agy_hook, (["agent_name"], {"nargs": "?"}), help=argparse.SUPPRESS)
    add("doctor", c_doctor, help="check config, hub connection and local sessions")
    add("mcp", c_mcp, (["--kind"],), help=argparse.SUPPRESS)
    add("cc-hook", c_cchook, (["which"], {"choices": ["start", "prompt"]}), help=argparse.SUPPRESS)
    team.register(add)
    sp._choices_actions = [x for x in sp._choices_actions if x.help != argparse.SUPPRESS]
    return ap


def main(argv=None):
    ap = build()
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not getattr(a, "fn", None):
        ap.print_help()
        return
    try:
        a.fn(a)
    except (client.HubRefused, client.HubUnavailable) as e:
        die(str(e))
    except KeyboardInterrupt:
        sys.exit(130)
