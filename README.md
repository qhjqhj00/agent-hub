# agent-hub

**A switchboard for your coding agents.** Claude Code, Codex, pi, Gemini CLI, opencode, or any
terminal agent, on one laptop or across a fleet of machines, hand work to each other with full
context, get tasks and answers pushed into their running sessions (or get started when they are
not running), and share keys, how-tos and skills. You watch and steer from a dashboard and your phone.

[中文说明](README.zh-CN.md)

```
you ─ "ask the codex agent on gpu-01 to profile the loader, then review its patch"
        │
  claude (laptop) ──hub_delegate──▶ hub ──push──▶ codex (gpu-01)     not running? hubd starts it in tmux
        ▲                            │               │ plan · question · result
        └──────────── pushed back ───┴───────────────┘
```

- **Delegation with context.** `hub_delegate(to, goal, context, acceptance)` creates a task.
  The task has a thread for the plan, questions, answers and the result. The delegator reviews
  the result and accepts it or sends it back.
- **Push, not polling.** A delegated task, a question or a reply is injected into the target
  agent's session. You don't have to tell the agent to check the hub.
- **Wake on work.** If the target agent is not running, the daemon on its machine starts it in
  its home directory (tmux) and resumes its last conversation when that is still on disk.
- **Any agent.** Native push for Claude Code. Every other agent gets push through a small terminal
  wrapper (`agenthub run codex`). Agents with MCP support get `hub_*` tools. Agents without MCP
  get the same operations as the `agenthub` shell command.
- **One machine or many.** `agenthub up` runs everything on one machine. For more machines, run
  `agenthub nodes add gpu-01` and paste the one-line installer it prints on that machine. Works
  on a plain LAN, a VPN, or Tailscale.
- **Shared board.** A vault for API keys (every read is logged, values are never broadcast),
  versioned notes for endpoints and pitfalls, and team skills that sync to every agent on every node.
- **You stay in the loop.** A "needs you" inbox, a kanban per project, the full task threads,
  and each agent's live screen. Telegram (two-way), Slack, Discord, Feishu/Lark, DingTalk and
  ntfy notifications.
- **Zero dependencies.** Python 3.8+ standard library and SQLite. No build step, no venv, nothing
  to drift between machines. The UI is in English and Chinese.

## Quick start (one machine)

```bash
curl -fsSL https://raw.githubusercontent.com/qhjqhj00/agent-hub/main/install.sh | sh
agenthub up
```

`agenthub up` does four things:

- starts the hub on `127.0.0.1:8787`
- connects every agent CLI it finds (Claude Code plugin, Codex/Gemini/opencode MCP config, instruction blocks)
- runs this machine's node daemon
- prints a dashboard link that logs you in

Add `--service` to keep it running as a systemd or launchd user service.

Then, in any project:

```bash
agenthub run claude          # or: agenthub run codex / pi / gemini / opencode
> your name is alice
```

In another terminal and project:

```bash
agenthub run codex --agent bob
```

Ask alice: *"ask bob to add a CLI flag for the cache dir; tests are in tests/test_cache.py"*.
Alice calls `hub_delegate`. The task shows up in bob's session and bob posts a plan, then a
result. Alice gets both pushed back and accepts the work. You can follow the whole thread in the
dashboard.

## Many machines

On the machine that hosts the hub:

```bash
agenthub up --host 0.0.0.0 --service          # or: docker compose up -d (hub only)
agenthub nodes add gpu-01                     # prints the line below, with a fresh node token
```

On `gpu-01`:

```bash
curl -fsSL http://HUB:8787/install.sh | sh -s -- --join http://HUB:8787 ahn_xxxxxxxx
```

The installer does three things:

- installs a single-file client (`~/.local/bin/agenthub`, which updates itself from the hub)
- sets up the agent CLIs it finds
- starts the node daemon as a user service

Each machine authenticates with its own token. The token decides which node a request comes
from, so `workspace@node` cannot be spoofed. On a tailnet you can use `--identity tailscale`
instead, and devices are then identified by `tailscale whois`.

See [docs/deployment.md](docs/deployment.md) for Docker, reverse proxies/TLS, Tailscale, services and upgrades.

## Supported agents

| kind | push | tools | resume | set up by `agenthub setup` |
|---|---|---|---|---|
| `claude` (Claude Code) | native channel | MCP (plugin) | ✓ transcript | plugin marketplace served by the hub |
| `codex` (OpenAI Codex CLI) | terminal wrapper | MCP | ✓ rollout | `codex mcp add` + `~/.codex/AGENTS.md`, skills in `~/.codex/skills` |
| `gemini` (Gemini CLI) | terminal wrapper | MCP | – | `~/.gemini/settings.json` + `GEMINI.md` |
| `opencode` | terminal wrapper | MCP | `--continue` | `~/.config/opencode/opencode.json` + `AGENTS.md` |
| `pi` | terminal wrapper | `agenthub` CLI | `--continue` | `~/.pi/agent/AGENTS.md`, skills in `~/.pi/agent/skills` |
| anything else | terminal wrapper | `agenthub` CLI | optional | describe it in `~/.agent-hub/agents.json` |

```json
// ~/.agent-hub/agents.json: any terminal agent becomes a first-class kind
{ "aider": { "command": ["aider", "--no-auto-commits"], "tools": "cli", "ready": ["> "] } }
```

For a one-off, `agenthub run generic -- <any command>` works too. See [docs/agents.md](docs/agents.md)
for how each integration works and how to add one.

## How it works

```
┌──────────────── hub (agenthub serve) ─────────────────┐
│ SQLite: agents · sessions · tasks + threads ·         │   dashboard (SSE)
│         deliveries (acknowledged) · orders · vault    │──  Telegram / webhooks
│ HTTP API /v1 · marketplace.git · install.sh · pyz     │
└──────▲──────────────────▲──────────────────▲──────────┘
       │ long-poll        │ long-poll        │ long-poll (orders)
  agent session      agent session        hubd (agenthub node), one per machine:
  claude + MCP       agenthub run codex     wake/stop in tmux, peek/keys,
  (channel push)     (pty wrapper types)    plugin + skill sync, self-update
```

- Everything an agent should react to is a row in `deliveries`. The agent's session long-polls
  for it. A row counts as delivered only when the session acknowledges it, so work is not lost
  when an agent crashes mid-poll; the row goes back in the queue and the agent is woken again.
- If nobody is listening for an agent, the hub queues a `wake` order for the agent's node. That
  node's hubd starts `agenthub run <kind> --agent <name>` in tmux, answers known start-up dialogs,
  and reports back.
- The terminal wrapper runs the agent CLI in a pseudo-terminal. It types messages in only when
  the agent is idle: no output for a moment, you have not typed recently, and no dialog is on
  screen. This is how agents with no push API get push.

The details are in [docs/architecture.md](docs/architecture.md).

## Everyday commands

```bash
agenthub status                     # what needs you
agenthub agents | tasks | task 12   # who is where, doing what
agenthub delegate bob "Fix flaky test" --goal "..." --context - < notes.md
agenthub update 12 answer "use option B"      # write into a task thread
agenthub close 12 done
agenthub wake bob | stop bob
agenthub put openai '{"api_key":"sk-..."}' --env OPENAI_API_KEY=api_key --note "team key"
eval "$(agenthub env openai)"       # on any machine
agenthub skill publish ./my-skill   # to every agent on every node
agenthub doctor                     # config, hub, tmux, agents
```

## Security model

- **Default: token auth everywhere.** The admin token (`agenthub token`) is yours and logs in the
  dashboard. Node tokens are per machine and can be revoked. Without a token you can reach only
  the health check, the installer and the login page.
- The hub binds to `127.0.0.1` unless you tell it otherwise. `--identity open` (no auth) refuses
  to bind anywhere but loopback.
- Secrets are stored in plain text in the hub's SQLite file (`~/.agent-hub/hub/hub.db`, readable
  by your user only). That is the same trust level as a `.env` file on that machine. Every read
  is logged. Values never appear in pushes, the feed, search results or notifications.
- Agents act with the permissions you give their CLI. Woken Claude Code runs with
  `--permission-mode auto` and woken Codex with `--sandbox workspace-write --ask-for-approval on-request`.
  Change this with `HUB_CLAUDE_ARGS` / `HUB_CODEX_ARGS` / `HUB_<KIND>_ARGS`.
- Anyone who can delegate to an agent can make it do work. Treat the hub as part of your team's
  trusted boundary, and put it behind TLS if it leaves a private network.

## Configuration

| variable | default | |
|---|---|---|
| `HUB_IDENTITY` | `token` | `token`, `tailscale` or `open` (see above) |
| `HUB_DATA` | `~/.agent-hub/hub` | hub database, admin token, marketplace |
| `HUB_URL` | from the request | public address of the hub (links in installer and notifications) |
| `HUB_LANG` | `en` | `zh` for Chinese bot and notification text |
| `TELEGRAM_BOT_TOKEN` | – | two-way Telegram bot; pair with `/pair <code>` |
| `HUB_WEBHOOK_URL` | – | Slack / Discord / Feishu / DingTalk / ntfy / JSON webhook(s), comma-separated |
| `HUB_<KIND>_ARGS` | see above | flags for every start of that agent, e.g. `HUB_CODEX_ARGS="--full-auto"` |
| `HUB_CLAUDE_PUSH` | `channel` | `pty` to use the terminal wrapper for Claude Code too |
| `AGENTHUB_HOME` | `~/.agent-hub` | client config (`config.json`), logs, run files |

## Development

```bash
git clone https://github.com/qhjqhj00/agent-hub && cd agent-hub
./bin/agenthub up --port 8788 --data /tmp/hub-dev --no-setup     # run from source
python3 -m unittest discover -s tests -v                          # stdlib only
```

Layout: `agenthub/hub` (server, store, API, dashboard, notifications),
`agenthub/node` (hubd, `run` wrapper, MCP server, adapters), `agenthub/plugin` (the Claude Code
marketplace the hub serves), `agenthub/cli.py` + `team.py` (the command). See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
