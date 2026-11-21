# Agents

agent-hub never starts an agent. You start it; it registers (with `HUB_AGENT=NAME` at start, or
when you tell it its name), and from then on the hub delivers into that running session through
whatever the agent CLI itself offers. Each integration answers four questions:

| | question | answered by |
|---|---|---|
| tools | how does the agent call the hub? | MCP (`agenthub mcp --kind K`), a pi extension, or the `agenthub` command |
| push | how does a message get into the conversation? | a channel, a hook, a bridge to the CLI's own server, or `hub_inbox` |
| state | busy or idle? | events the CLI emits, reported with `POST /v1/sessions/<id>/state` |
| resume | how do you get the same conversation back? | the CLI's own resume flag, in the start command |

The session contract (register, pull with a 60 s lease, delivered, state, beat, end) is in
[refactor/api.md](refactor/api.md). Whoever registers writes `~/.agent-hub/sessions/<name>.json`
(0600), so a hook or bridge serving the same agent acts as the same session.

## Built in

### Claude Code (`claude`)
- `agenthub setup claude` adds the hub's plugin marketplace (`<hub>/marketplace.git`), installs
  `agent-hub@agent-hub` and `team-skills@agent-hub`, and turns on marketplace auto-update.
- Start: `HUB_CHANNEL=1 HUB_AGENT=NAME claude --dangerously-load-development-channels plugin:agent-hub@agent-hub [--resume ID]`.
- Push: the plugin's MCP server long-polls the hub and emits each item as a
  `<channel source="agent-hub">` event. Without `HUB_CHANNEL=1`, the `UserPromptSubmit` hook shows
  queued items on your next prompt instead.
- `/resume` inside a running claude switches conversations; the `SessionStart` hook tells the MCP
  server, so the start command resumes the right one.

### agy (`agy`, Antigravity in T3 Code)
- `agenthub setup agy` writes the hub MCP server into T3's Antigravity provider config and
  `~/.gemini/config/mcp_config.json`. `--workspace DIR` also writes `DIR/.agents/hooks.json`
  (kept out of git via `info/exclude`).
- Busy: the `PreInvocation` hook takes waiting items with a non-blocking `pull?wait=0` and
  injects them as a system message before the next model call; it reports `busy`.
- Idle: `agenthub t3-bridge` (one per machine, next to `t3 serve`) long-polls for every agy
  session there and delivers with `t3 thread send`. T3 starts a new turn when the thread is idle
  and queues the message when it is busy.
- `Stop` returns at once and reports `idle`. It must never wait: a blocking Stop hook keeps the
  T3 turn "running" and hits agy's forced-invocation limit.
- Thread binding is deterministic: the agy conversation id (hook input / `ANTIGRAVITY_CONVERSATION_ID`)
  is looked up in T3's own state database to find its thread. A session with no known thread is
  skipped, and its items stay queued; nothing is ever sent to a guessed thread.
- Start: open the workspace in T3 and tell it its name (`t3 thread start DIR --title NAME '...'`).

### Codex (`codex`)
- `agenthub setup codex` runs `codex mcp add agent-hub -- agenthub mcp --kind codex`.
- Start the shared server once (`codex app-server daemon start`), run `agenthub codex-bridge`,
  and start Codex with `codex --remote unix://$HOME/.codex/app-server-control/app-server-control.sock`.
  You and the bridge are then clients of the same thread.
- Push: `turn/start` when the thread is idle; when it is busy the bridge waits for idle
  (`turn/steer` is available for urgent items).
- State: `thread/status/changed` (`active` -> busy, `idle` -> idle).

### pi (`pi`)
- `agenthub setup pi` installs `~/.pi/agent/extensions/agent-hub.ts`, a dependency-free extension.
- It registers the same nine `hub_*` tools with `pi.registerTool`, long-polls the hub, and injects
  items with `pi.sendUserMessage` (`deliverAs: "followUp"` while pi is working).
- State: `agent_start` -> busy, `agent_end` -> idle, `session_shutdown` -> end.
- Start: `HUB_AGENT=NAME pi [--session ID]`.

### Anything with a shell (`other`)
`agenthub register NAME --kind other` opens a session and writes its session file; later
commands (`agenthub inbox`, `update`, `send`, ...) act as it with `--agent NAME` or `HUB_AGENT`.
Nothing is pushed: the agent checks `agenthub inbox`.

## Writing an adapter

An adapter (`agenthub/node/adapters/<kind>.py`, registered in `__init__.py`) is a small class:

- `kind`, `title`, `binary` (the CLI on PATH), `push` (`channel` | `bridge` | `pull`),
  `tools` (`mcp` | `extension` | `cli`)
- `available()`, `version()`
- `start_command(name, workspace, conversation="", project="")`: the line the dashboard copies
- `onboarding_prompt(name, project="", role="")`: what to tell the agent the first time
- `setup(agenthub_cmd, **kw)` / `teardown(**kw)`: install or remove the integration; return
  human-readable lines

If the CLI has a way to put a message into a running conversation and to tell when a turn starts
and ends, write a bridge or hook for it (see `agenthub/bridges/`) that reads the session file,
pulls with a lease, injects, and reports `delivered` and `state`. A bridge may connect to the
CLI's own server; it must not start the agent or type into its terminal.
