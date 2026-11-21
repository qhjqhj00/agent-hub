# Changelog

## 3.0.0

The hub stops managing agent processes and delivers through each agent's own interface.
Design notes: [docs/refactor/](docs/refactor/).

**Breaking**

- Removed hubd (`agenthub node`) and everything it did: waking agents in tmux, stop, peek,
  keys, the `orders` queue, plugin sync and self-update.
- Removed `agenthub run` (the pty wrapper) and the `agents.json` custom kinds. Agents are
  started by hand; `agenthub start NAME` and the dashboard print the command.
- Removed Telegram and webhook notifications, the "needs you" inbox and the permission relay.
  Anything for the human goes to the project's liaison (an agy in T3, `projects.liaison`).
- Agent identity is a session token (`X-Hub-Session`). `agent` / `from` fields in request bodies
  are ignored. `kind` is required when registering.
- The HTTP API changed; see [docs/refactor/api.md](docs/refactor/api.md). The MCP tools are now
  nine: `hub_register`, `hub_agents`, `hub_send`, `hub_delegate`, `hub_update`, `hub_close`,
  `hub_tasks`, `hub_inbox`, `hub_board`.
- Task states: `open`, `working`, `review`, `done`, `cancelled`.
- Gemini CLI and opencode adapters are gone for now.
- Python 3.10+.

**New**

- One live session per name; the name is freed when the agent exits, and resuming the same
  conversation elsewhere takes it over.
- `posts` + `inbox` replace messages, events, deliveries and task_log. Pulls take a 60 s lease and
  count once reported delivered.
- agy: `PreInvocation` / `Stop` hooks and `agenthub t3-bridge` (via `t3 thread send`), bound to
  the right T3 thread deterministically.
- Codex: `agenthub codex-bridge` on `codex app-server`; you watch and type with `codex --remote`.
- pi: an in-process TypeScript extension with the nine tools and push.
- Busy / idle reported by each session.
- Archive agents (off the dashboard and `hub_agents`, sessions and history kept), restore by hand.
- A new dashboard: Agents with copyable start commands, Tasks with full threads, the Board.
- `agenthub migrate --from <2.x hub.db>`: copies a 2.x hub's data (read-only on the old file),
  including messages that were still waiting to be delivered.

**Known gaps**

- Team skills reach Claude Code through the plugin marketplace; other agents do not get them
  automatically yet.
- The dashboard is in Chinese only.

## 2.0.0

First open-source release.

- **Any coding agent**: adapters for Claude Code, Codex, pi, Gemini CLI and opencode, and for
  any terminal agent through `~/.agent-hub/agents.json` or `agenthub run generic`.
- **`agenthub run`**: a terminal wrapper that types hub events into agents without a push API,
  with acknowledged delivery.
- **`agenthub mcp`**: one MCP server for every MCP-capable agent; Claude Code keeps native
  channel push and permission relay.
- **One command**: `agenthub` replaces `hub`, `hubd` and `cc`. It covers `up`, `serve`,
  `join`, `node`, `setup`, `run`, `doctor`, `nodes add`, plus every team command.
- **Auth**: token identity by default (admin token, plus a revocable token per node), dashboard
  login, `tailscale` and `open` modes. Every API route now requires authentication.
- **Deployment**: one-line installers (from GitHub, or served by the hub with a single-file
  self-updating client), systemd/launchd services, Docker image for the hub.
- **Notifications**: Telegram plus Slack, Discord, Feishu/Lark, DingTalk, ntfy and JSON webhooks.
- **i18n**: dashboard, bot and notifications in English and Chinese.
- **Skills**: team skills also sync into Codex and pi skill directories.
