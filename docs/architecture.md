# Architecture

agent-hub has three moving parts. They all live in the `agenthub` Python package and the single `agenthub` command.

| part | runs | command | code |
|---|---|---|---|
| **hub** | once, on one machine (or in Docker) | `agenthub serve` (or `up`) | `agenthub/hub/` |
| **node daemon (hubd)** | once per machine that runs agents | `agenthub node` | `agenthub/node/daemon.py` |
| **agent session** | per running agent | `agenthub run <kind>` and/or `agenthub mcp` | `agenthub/node/run.py`, `mcp.py` |

SQLite (WAL) is the only source of truth. Nothing is kept in memory that a restart would lose.
Long-polls (`bus.py`) wake on a payload-free version counter and re-read the database.

## Identity

An agent is **a name with a home**: `workspace@node`. `hub_register(name)` records the session's
working directory and git remote/branch. The node is never taken from the client. It comes
from the request context (`hub/identity.py`):

- `token` mode: the node token presented. `agenthub nodes add NAME` mints one; it is stored hashed.
  The admin token is the human's. A request with the admin token may name its node with
  `X-Hub-Node`, and loopback requests are the hub machine itself.
- `tailscale` mode: `tailscale whois <peer ip>`.
- `open` mode: trusted network, loopback only by default.

One live session per name. Resuming an agent's conversation in another terminal takes the name
over, and the copy left behind is closed by its node (`stop_session`).

## Delegation

```
A: hub_delegate(to=B, title, goal, context, acceptance)  -> task #n, delivery -> B
   B live?  pushed into B's session
   B gone?  order -> B's node -> hubd wakes B (resuming its conversation if possible)
B: hub_update(plan)      -> delivery -> A          state: in_progress
B: hub_update(question)  -> A;  A: answer -> B
B: hub_update(result)    -> A                      state: review
A: hub_close(done)  |  hub_update(comment) -> back to in_progress
```

Addresses are either an agent name or `role@project`. Role tasks go to the holders that are
live (if none are live, to the one active most recently), and anyone holding the role can
`hub_claim` the task. The human is just another participant (`human`). Deliveries to `human`
go to the notifiers.

## Delivery

Every arrow above is a row in `deliveries`. A session long-polls
`GET /v1/sessions/<id>/pull?wait=50&ack=<ids>`:

1. Rows go out with `sent_at` set.
2. They become `delivered_at` only when the same session's next poll acknowledges them.
3. A session that dies holding rows never acknowledges them. The maintenance loop (every 30 s)
   puts those rows back in the queue and wakes the agent again.

How a session turns rows into something the agent sees depends on the agent:

- **Claude Code**: the plugin's MCP server declares the `claude/channel` capability and sends
  `notifications/claude/channel`. Claude acts on these even when idle. Permission prompts travel
  the other way: Claude → MCP server → hub → dashboard/Telegram → verdict delivery → Claude.
- **Everything else**: `agenthub run` is a pty proxy. It owns the hub session, holds pulled
  rows, and types them into the agent's terminal once the agent is idle. It acknowledges the
  rows after typing. Idle means all of these:
  - the agent has produced no output for `HUB_PTY_QUIET` (2.5 s); agents animate a spinner while they work
  - you have not typed for `HUB_PTY_TYPING_QUIET` (4 s)
  - the adapter sees no dialog on screen and, where it knows one, sees its input prompt
    (inside tmux it reads the screen with `capture-pane`; otherwise from recent output)

  Text goes in as a bracketed paste if the app enabled that, otherwise on one line, followed by Enter.
- **Plain sessions** (`claude` or `codex` started without the wrapper): tools work, presence is
  kept by heartbeat, and nothing is consumed. Queued rows are read with `hub_inbox`; the
  Claude Code plugin's prompt hook also shows them on the next prompt.

Tools inside a wrapped agent (its MCP server, its shell) find the wrapper's session through
`~/.agent-hub/run/<wrapper pid>.json`, found by walking up the process tree. That works even
for agents that strip environment variables before starting tools, as Codex does.

## Waking

`core.ensure_listening()` runs whenever something is delivered and again from the maintenance
loop. If the agent has no live session and `wake=auto`, it queues a `wake` order for the
agent's node. hubd then:

1. kills headless leftovers (processes carrying `HUB_AGENT=<name>`)
2. starts `agenthub run <kind> --agent <name> [--resume <conversation>]` in tmux session
   `hub-<name>`, in the agent's workspace. The environment it needs (hub URL, tokens,
   `CODEX_HOME`, …) is passed through a 0600 file, never on the command line
3. answers the adapter's known start-up dialogs (folder trust, development channels, update prompts)
4. reports success once the hub sees the session live and the screen shows an input prompt

If a resumed session does not become ready, hubd starts it fresh instead. A dialog it does
not know is reported back; you can answer it from the dashboard's Screen view
(`peek`/`keys` orders).

Orders are acknowledged like deliveries. An order handed to a hubd that dies mid-poll is
handed out again.

## Skills and plugins

The hub serves a git repository at `/marketplace.git` (smart HTTP, read-only, token as the
basic-auth password). It doubles as a Claude Code plugin marketplace with two plugins:

- `agent-hub`: MCP server hookup, skill, hooks
- `team-skills`: whatever the team publishes

`POST /v1/skills` commits a skill and bumps the plugin version. Every hubd then updates its
Claude plugins and copies the skills into the skill directories of Codex (`~/.codex/skills`)
and pi (`~/.pi/agent/skills`). It never overwrites a directory that it did not create.

## Client distribution

`GET /client/agenthub.pyz` is a zipapp of the hub's own package, built on the fly. Nodes
installed from a hub run it as `~/.local/bin/agenthub`, and hubd replaces it when the hub's
version is newer. pip and source installs are left alone.
