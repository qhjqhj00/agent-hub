# Architecture

Two moving parts, both in the `agenthub` package and the single `agenthub` command:

| part | runs | command | code |
|---|---|---|---|
| **hub** | once, on one machine (or in Docker) | `agenthub serve` (or `up`) | `agenthub/hub/` |
| **agent side** | inside or next to each running agent | `agenthub mcp`, hooks, `t3-bridge`, `codex-bridge`, the pi extension | `agenthub/node/`, `agenthub/bridges/` |

There is no node daemon. The hub never starts, wakes or stops an agent, and nothing types into a
terminal. SQLite (WAL) is the only source of truth; long-polls (`bus.py`) wake on a payload-free
version counter and re-read the database.

The design and its reasons are in [refactor/core.md](refactor/core.md) and
[refactor/adapters.md](refactor/adapters.md); the wire protocol is [refactor/api.md](refactor/api.md).

## Identity

- **Nodes** come from the request context, never from the client (`hub/identity.py`): the node
  token presented (`token` mode, one hashed token per machine), `tailscale whois` (`tailscale`
  mode), or trust (`open` mode, loopback only by default). The admin token is the human's.
- **Agents** are sessions. `POST /v1/sessions` (name, kind, cwd, conversation, push) returns a
  session id and token; every later request carries `X-Hub-Session`, and the hub knows the agent
  from that alone. Names in request bodies are ignored.
- One live session per name. A second registration under a live name is refused (409), unless it
  is the same conversation (resumed in another terminal) or the same process, which take it over.
  `/end` (sent when the agent exits) frees the name at once; a session with no heartbeat for
  120 s is ended by the hub.
- Admin routes need the admin token and no session: an agent never acts as the human.

## Conversations and delivery

```
posts  (id, task_id?, thread_id, reply_to, sender, recipient, kind, body, project, ts)
inbox  (id, agent, post_id, kind, task_id, text, sent_at, session, delivered_at)
```

- Every message, task-thread entry and team post is one row in `posts`. Each recipient gets an
  `inbox` row with the text rendered for it.
- A session takes its items with `GET /v1/sessions/<id>/pull?wait=0..55`. Each item taken is
  leased to that session for 60 s; it counts once the session posts `/delivered`. An unconfirmed
  lease expires and the item is handed out again, so a crash loses nothing and two deliverers for
  one agent never double-deliver. Leases of an ended session are released at once.
- Addresses: an agent name; `role@project` (live holders of the role, else the one seen most
  recently); `*@project` (everyone in it); `human` (the project's liaison, `projects.liaison`; with
  no liaison the post is kept and delivered to nobody).
- Sessions report `busy` / `idle`; the dashboard shows what they report.

## Tasks

```
A: hub_delegate(to=B, title, goal, context, acceptance)  -> task #n (open), inbox -> B
B: hub_update(plan | progress)  -> A                      working (an unowned role task is taken)
B: hub_update(question[, to=human]) -> A [+ liaison];  A: answer -> B
B: hub_update(result)           -> A                      review
A: hub_close(done | cancelled)  |  hub_update(comment) -> B, back to working
```

A task created by the human, or a question to `human`, is delivered to the project's liaison with
a note telling it to put the item to the human and write the answer back.

## Agents and the dashboard

`agents` keeps each agent's kind, node, workspace and last conversation, from which the adapter
builds its start command. Archived agents (`agents.hidden`) are left out of the dashboard and
`hub_agents` but keep their sessions, tasks and history and still receive what is sent to them;
registering again does not un-archive.

The dashboard (`hub/web/`, vanilla JS) has three views: Agents (by project, presence, busy/idle,
current task, start command), Tasks (by state, with full threads) and the Board. One SSE stream
(`/v1/stream`) says "something changed"; the view re-fetches. Writing actions need the admin token.

## Skills and plugins

The hub serves a Claude Code plugin marketplace over git (`/marketplace.git`, read-only):
`agent-hub` (the MCP server, hooks and the skill agents read) and `team-skills` (skills published
with `hub_board(action="publish")`). Each change bumps the plugin version; Claude Code with
marketplace auto-update picks it up at its next start.

## Client distribution

`GET /client/agenthub.pyz` is a zipapp of the hub's own copy of the package; `/install.sh`
downloads it to `~/.local/bin/agenthub` and joins the node. Re-running the installer upgrades it.
