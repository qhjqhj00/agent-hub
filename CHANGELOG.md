# Changelog

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
