"""agenthub -- one command for the hub, the nodes, the agents, and the team.

  agenthub up                      hub + this machine as a node, in one go (single machine)
  agenthub serve                   just the hub
  agenthub join URL TOKEN          make this machine a node of that hub
  agenthub node                    the node daemon (hubd), usually run as a service
  agenthub run claude|codex|pi|... start a coding agent connected to the hub
  agenthub setup [KIND...]         (re)install the hub integration into agent CLIs
  agenthub delegate / tasks / ...  team commands (agenthub --help lists them)
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
    if getattr(a, "lang", None):
        os.environ["HUB_LANG"] = a.lang


def c_serve(a):
    _hub_env(a)
    from .hub import serve
    from .hub.store import init_db
    init_db()
    serve(a.host, a.port)


def _start_hub_thread(host, port):
    from .hub import serve
    from .hub.store import init_db
    init_db()
    ready = threading.Event()
    t = threading.Thread(target=serve, args=(host, port, ready), daemon=True)
    t.start()
    if not ready.wait(20):
        die("the hub did not start")
    return ready.port, t


def c_up(a):
    """Single machine (or the first machine of many): the hub and this node, one process."""
    _hub_env(a)
    os.environ.setdefault("HUB_IDENTITY", "token")
    from .hub import identity
    local = "127.0.0.1" if a.host in ("0.0.0.0", "::", "") else a.host
    url = "http://%s:%d" % (local, a.port)
    if a.service:
        from .node import service
        args = ["--host", a.host, "--port", str(a.port)]
        env = {k: os.environ[k] for k in ("HUB_IDENTITY", "HUB_DATA", "HUB_LANG", "HUB_URL", "TELEGRAM_BOT_TOKEN",
                                          "HUB_WEBHOOK_URL") if os.environ.get(k)}
        print(service.install("hub", args, env))
        tok = identity.admin_token()
        client.save(url=url, token=tok)
        _wait_hub(url)
        _setup_agents(a.kinds, quiet=False)
        print(service.install("node"))
        _banner(url, tok, a.host)
        return
    port, hub_thread = _start_hub_thread(a.host, a.port)
    url = "http://%s:%d" % (local, port)
    tok = identity.admin_token()
    client.save(url=url, token=tok)
    if not a.no_setup:
        _setup_agents(a.kinds, quiet=False)
    from .node import daemon
    threading.Thread(target=daemon.main, daemon=True).start()
    _banner(url, tok, a.host)
    try:
        while hub_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print()


def _wait_hub(url, secs=20):
    end = time.time() + secs
    while time.time() < end:
        try:
            client.call("GET", "/healthz", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    die("hub at %s did not come up; see ~/.agent-hub/agent-hub.log" % url)


def _banner(url, tok, host):
    print("""
agent-hub %s is up.

  dashboard   %s/?token=%s
  start agents in any project directory:
      agenthub run claude      (or: codex, pi, gemini, opencode)
  then tell the agent its name: "your name is alice"
""" % (__version__, url, tok))
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("  add another machine:  agenthub nodes add <name>   (prints its one-line installer)\n")


def c_token(a):
    _hub_env(a)
    from .hub import identity
    if not os.path.isfile(identity.ADMIN_FILE) and not os.environ.get("HUB_TOKEN"):
        die("no hub data here (%s); run this on the hub machine" % identity.ADMIN_FILE)
    print(identity.admin_token())


# ------------------------------------------------------------------ node

def c_node(a):
    from .node import daemon
    daemon.main()


def c_join(a):
    url = a.url.rstrip("/")
    client.save(url=url, token=a.token)
    try:
        me = client.call("GET", "/v1/whoami", timeout=10)
    except Exception as e:
        die("cannot use %s: %s" % (url, e))
    who = (me.get("you") or {})
    print("joined %s as node %s (%s)" % (url, who.get("node"), who.get("role")))
    if not a.no_setup:
        _setup_agents(a.kinds, quiet=False)
    if not a.no_service:
        from .node import service
        print(service.install("node"))
    else:
        print("run the node daemon with: agenthub node")


def _setup_agents(kinds, quiet=True, remove=False):
    from .node import adapters, self_cmd
    allk = adapters.all_adapters()
    want = kinds or [k for k, ad in allk.items() if k != "generic" and ad.available()]
    if not want and not quiet:
        print("no agent CLIs found on PATH (claude, codex, pi, gemini, opencode); install one and run `agenthub setup`")
    for k in want:
        ad = adapters.get(k)
        if not ad.available():
            print("%s: not installed, skipped" % k)
            continue
        try:
            lines = ad.teardown() if remove else ad.setup(self_cmd())
        except Exception as e:
            lines = ["%s: setup failed: %s" % (k, e)]
        for line in lines:
            print("  " + line)


def c_setup(a):
    _setup_agents(a.kinds, quiet=False, remove=a.remove)


def c_run(a):
    from .node import run
    sys.exit(run.main(a.kind, a.agent_name or "", a.resume or "", a.prompt or "", a.extra, a.fresh))


def c_mcp(a):
    from .node import mcp
    mcp.main(a.kind)


def c_cchook(a):
    from .node import cchooks
    cchooks.main(a.which)


def c_service(a):
    from .node import service
    if a.action == "install":
        print(service.install(a.which))
    else:
        print(service.uninstall(a.which))


def c_agents_kinds(a):
    from .node import adapters
    for k, ad in adapters.all_adapters().items():
        d = ad.describe()
        print("%-10s %-24s push=%-7s tools=%-4s %s" % (k, d["title"], d["push"], d["tools"],
                                                        ("installed " + ad.version()) if d["available"] else "-"))


def c_doctor(a):
    import shutil
    from .node import adapters, install_kind, self_cmd
    ok = True
    print("agenthub %s (%s: %s)" % (__version__, install_kind(), " ".join(self_cmd())))
    print("config   %s -> %s, token %s" % (client.CFG, client.url(), "set" if client.token() else "missing"))
    try:
        me = client.call("GET", "/v1/whoami", timeout=5)
        print("hub      ok, version %s, you are node %s (%s)" % (me.get("version"), (me.get("you") or {}).get("node"),
                                                                 (me.get("you") or {}).get("role")))
    except Exception as e:
        ok = False
        print("hub      PROBLEM: %s" % e)
    print("tmux     %s" % ("ok" if shutil.which("tmux") else "missing (needed to wake agents)"))
    for k, ad in adapters.all_adapters().items():
        if k != "generic" and ad.available():
            print("agent    %-9s %s" % (k, ad.version()))
    sys.exit(0 if ok else 1)


# ------------------------------------------------------------------ parser

def build():
    from . import team
    ap = argparse.ArgumentParser(prog="agenthub", description="agent-hub: coordinate coding agents across machines",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Start here: `agenthub up` on one machine, then `agenthub run claude` in a project.")
    ap.add_argument("--version", action="version", version="agenthub " + __version__)
    ap.add_argument("--agent", help="act as this agent (team commands)")
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
               (["--data"], {"help": "data directory (default ~/.agent-hub/hub)"}),
               (["--lang"], {"choices": ["en", "zh"], "help": "language for people (bot, notifications)"}))
    port = int(os.environ.get("HUB_PORT", 8787))
    add("up", c_up, (["--host"], {"default": os.environ.get("HUB_HOST", "127.0.0.1"),
                                  "help": "bind address (0.0.0.0 to let other machines join)"}),
        (["--port"], {"type": int, "default": port}), *hubopts,
        (["--service"], {"action": "store_true", "help": "install hub + node daemon as user services instead"}),
        (["--no-setup"], {"action": "store_true", "help": "do not touch agent CLI configs"}),
        (["kinds"], {"nargs": "*", "help": "agents to set up (default: all installed)"}),
        help="run the hub and this machine as a node (start here)")
    add("serve", c_serve, (["--host"], {"default": os.environ.get("HUB_HOST", "127.0.0.1")}),
        (["--port"], {"type": int, "default": port}), *hubopts, help="run only the hub server")
    add("token", c_token, (["--data"],), help="print the hub's admin token (on the hub machine)")
    add("join", c_join, (["url"],), (["token"],), (["--no-service"], {"action": "store_true"}),
        (["--no-setup"], {"action": "store_true"}), (["kinds"], {"nargs": "*"}),
        help="connect this machine to a hub as a node")
    add("node", c_node, help="run the node daemon (hubd)")
    add("setup", c_setup, (["kinds"], {"nargs": "*", "help": "claude codex pi gemini opencode (default: installed)"}),
        (["--remove"], {"action": "store_true"}), help="install the hub integration into agent CLIs")
    add("kinds", c_agents_kinds, help="agent kinds this node knows")
    add("service", c_service, (["action"], {"choices": ["install", "uninstall"]}),
        (["which"], {"choices": ["hub", "node"]}), help="manage the systemd/launchd user service")
    add("doctor", c_doctor, help="check config, hub connection and agent CLIs")
    p = add("run", c_run, (["kind"], {"help": "claude, codex, pi, gemini, opencode, generic, or one from agents.json"}),
            (["--agent"], {"dest": "agent_name", "help": "start as this agent name"}),
            (["--resume"], {"help": "conversation to resume"}), (["--prompt"],), (["--fresh"], {"action": "store_true"}),
            help="start a coding agent connected to the hub (arguments after -- go to the agent CLI)")
    p.usage = "agenthub run KIND [--agent NAME] [--resume ID] [-- agent args...]"
    add("mcp", c_mcp, (["--kind"],), help=argparse.SUPPRESS)
    add("cc-hook", c_cchook, (["which"], {"choices": ["start", "prompt"]}), help=argparse.SUPPRESS)
    team.register(add)
    sp._choices_actions = [x for x in sp._choices_actions if x.help != argparse.SUPPRESS]
    return ap


def main(argv=None):
    ap = build()
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if argv[:1] == ["run"] and "--" in argv:  # `agenthub run KIND [opts] -- agent args`
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    a.extra = extra
    if not getattr(a, "fn", None):
        ap.print_help()
        return
    try:
        a.fn(a)
    except (client.HubRefused, client.HubUnavailable) as e:
        die(str(e))
    except KeyboardInterrupt:
        sys.exit(130)
