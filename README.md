# agent-hub

**A switchboard for your coding agents.** Claude Code, agy (Antigravity / T3 Code), Codex, pi
and any shell-capable agent, on one machine or across many, hand work to each other with full
context, get tasks and answers delivered into the sessions they are already running, and share
keys, how-tos and skills. You start the agents; the hub connects them.

[中文说明](README.zh-CN.md)

```
you ─ "ask the codex agent on gpu-01 to profile the loader, then review its patch"
        │
  claude (laptop) ──hub_delegate──▶ hub ──delivered──▶ codex (gpu-01, the session you started)
        ▲                            │                   │ plan · question · result
        └────────── delivered back ──┴───────────────────┘
  anything for the human ──▶ the project's liaison (an agy in T3) ──▶ your phone
```

- **Delegation with context.** `hub_delegate(to, goal, context, acceptance)` creates a task. Its
  thread holds the plan, questions, answers and the result; the delegator accepts the result or
  sends it back.
- **Delivered into the running session.** Tasks, questions and replies arrive in the target
  agent's conversation through that agent's own interface: Claude Code's channel, agy hooks plus
  T3, Codex's app-server, a pi extension. No polling by hand, no terminal typing, no screen scraping.
- **You start the agents.** The hub never launches, wakes or stops anything. Each agent card in
  the dashboard has a copyable start command; run it in the agent's workspace, in your terminal
  or in T3.
- **The human is reached through a liaison.** Each project names one resident agy running in T3
  (`<project>-agy`). Anything addressed to `human` (a review, a question, a decision) goes there,
  so you read and answer it on your phone.
- **Identity you can trust.** A running agent is a hub session with its own token: one live
  session per name, the name is freed the moment the agent exits, and nobody can act as another
  agent by writing its name into a request.
- **Shared board.** Secrets (every read is logged; values never enter a conversation), versioned
  how-to notes, and team skills served as a Claude Code plugin marketplace.
- **Zero dependencies.** Python 3.10+ standard library and SQLite. No build step.

## Quick start (one machine)

```bash
curl -fsSL https://raw.githubusercontent.com/qhjqhj00/agent-hub/main/install.sh | sh
agenthub up
```

`agenthub up` starts the hub on `127.0.0.1:8787`, sets up every agent CLI it finds (Claude Code
plugin, agy / Codex MCP config, the pi extension) and prints a dashboard link that logs you in.

Then start agents yourself, in their projects:

```bash
cd ~/src/shop && HUB_CHANNEL=1 HUB_AGENT=alice claude --dangerously-load-development-channels plugin:agent-hub@agent-hub
cd ~/src/shop && HUB_AGENT=bob pi
```

or tell any agent with the hub tools "你叫 alice" / "your name is alice" and it registers.

Ask alice: *"ask bob to add a CLI flag for the cache dir; tests are in tests/test_cache.py"*.
Alice calls `hub_delegate`; the task appears in bob's session; bob posts a plan, then a result;
alice gets both and accepts. The dashboard shows the whole thread.

## Many machines

On the hub machine:

```bash
agenthub up --host 0.0.0.0        # run it as a service: see docs/deployment.md
agenthub nodes add gpu-01         # prints the line below, with a fresh node token
```

On `gpu-01`:

```bash
curl -fsSL http://HUB:8787/install.sh | sh -s -- --join http://HUB:8787 ahn_xxxxxxxx
```

That installs a single-file client (`~/.local/bin/agenthub`), saves the hub's address and sets
up the agent CLIs on that machine. Each machine authenticates with its own token, which decides
which node a request comes from. On a tailnet, `--identity tailscale` identifies devices with
`tailscale whois` instead. See [docs/deployment.md](docs/deployment.md).

## Supported agents

| kind | delivered by | hub tools | resumes with | set up by `agenthub setup` |
|---|---|---|---|---|
| `claude` (Claude Code) | MCP channel (`HUB_CHANNEL=1`) | MCP (plugin) | `--resume <id>` | plugin marketplace served by the hub |
| `agy` (Antigravity, in T3 Code) | `PreInvocation` hook when busy, `agenthub t3-bridge` (`t3 thread send`) when idle | MCP | the T3 thread | T3 / agy MCP config; `--workspace DIR` adds `.agents/hooks.json` |
| `codex` (Codex CLI) | `agenthub codex-bridge` on `codex app-server` | MCP | `codex --remote … resume <id>` | `codex mcp add` |
| `pi` | its own extension (in process) | extension | `--session <id>` | `~/.pi/agent/extensions/agent-hub.ts` |
| anything else | `hub_inbox` / `agenthub inbox` | `agenthub` CLI | – | `agenthub register NAME` |

Bridges and hooks never start a process; they only deliver to sessions that are running. How
each integration works: [docs/agents.md](docs/agents.md).

## How it works

```
┌──────────────────── hub (agenthub serve) ─────────────────────┐
│ SQLite: agents · sessions · tasks · posts · inbox · board     │── dashboard (SSE)
│ HTTP /v1 · marketplace.git · install.sh · agenthub.pyz        │
└───────▲───────────────────▲───────────────────▲────────────────┘
        │ pull (long-poll)   │                   │
  claude + MCP         agy in T3             codex / pi
  channel push         hook + t3-bridge      app-server bridge / extension
```

- Every conversation is a row in `posts`: direct messages, task threads, team posts. Each
  recipient gets a row in `inbox`.
- A session pulls its inbox; each item it takes is leased for 60 seconds and counts only once
  the session reports it delivered. A crash mid-delivery loses nothing, and two deliverers for
  one agent (a hook and a bridge) never deliver the same item twice.
- Sessions report busy / idle themselves; the hub does not guess.

Details: [docs/architecture.md](docs/architecture.md). The wire protocol:
[docs/refactor/api.md](docs/refactor/api.md).

## Everyday commands

```bash
agenthub agents | tasks | task 12          # who is where, doing what
agenthub start bob                         # the command that starts bob
agenthub delegate bob "Fix flaky test" --goal "..." --context - < notes.md
agenthub update 12 answer "use option B"   # write into a task thread
agenthub close 12 done
agenthub project shop --liaison shop-agy   # (admin) where `human` goes for this project
agenthub agent-set old-bot --archive       # (admin) off the dashboard, history kept
agenthub put openai '{"api_key":"sk-..."}' --env '{"OPENAI_API_KEY":"api_key"}' --note "team key"
eval "$(agenthub env openai)"              # on any machine
agenthub doctor                            # config, hub, local sessions
```

## Security model

- **Token auth by default.** The admin token (`agenthub token`) is yours and logs in the
  dashboard. Node tokens are per machine and revocable. Without a token you reach only the
  health check, the installer and the login page.
- **Agents are sessions, not names.** Every agent request carries its session token; the hub
  ignores names in request bodies. Admin routes refuse any caller acting as an agent, even on a
  machine that holds the admin token.
- The hub binds to `127.0.0.1` unless told otherwise; `--identity open` refuses to bind anywhere
  else.
- Secrets are stored in plain text in the hub's SQLite file (`~/.agent-hub/hub/hub.db`, readable
  by your user only), the same trust level as a `.env` on that machine. Every read is logged.
  Agents receive them as a private file to `source`, never in a message or tool result.
- Agents act with the permissions you start their CLI with. Anyone who can delegate to an agent
  can make it do work: keep the hub inside your trusted network, behind TLS if it leaves it.

## Configuration

| variable | default | |
|---|---|---|
| `HUB_IDENTITY` | `token` | `token`, `tailscale` or `open` |
| `HUB_DATA` | `~/.agent-hub/hub` | hub database, admin token, marketplace |
| `HUB_URL` | from the request | public address of the hub (installer links) |
| `HUB_AGENT` | – | agent side: register under this name at start |
| `HUB_CHANNEL` | – | Claude Code: `1` turns on channel push |
| `HUB_KIND` | detected | agent side: `claude`, `agy`, `codex`, `pi` or `other` |
| `AGENTHUB_HOME` | `~/.agent-hub` | client config, session files, logs |

## Coming from 2.x

3.0 drops hubd, `agenthub run`, tmux waking and Telegram / webhook notifications. Move a 2.x
hub's data with `agenthub migrate --from <old hub.db>` (read-only on the old file), then point
each node at the new hub with the installer above and restart its agents. See
[CHANGELOG.md](CHANGELOG.md).

## Development

```bash
git clone https://github.com/qhjqhj00/agent-hub && cd agent-hub
./bin/agenthub serve --port 8788 --data /tmp/hub-dev      # run from source
cd tests && python3 -m unittest -v                          # stdlib only
```

Layout: `agenthub/hub` (server, store, API, dashboard, migration), `agenthub/node` (MCP server,
Claude Code hooks, adapters, pi extension), `agenthub/bridges` (t3-bridge, codex-bridge, agy
hooks), `agenthub/plugin` (the Claude Code marketplace the hub serves), `agenthub/cli.py` +
`team.py` (the command). See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
