---
name: agent-hub
description: How to collaborate with other coding agents (Claude Code, Codex, pi, ...) through agent-hub -- registering a name, delegating work to another agent with full context, handling pushed tasks, and reporting results. Use when the user names you, asks you to get another agent (or role) to do something, mentions the hub/other agents/the team, or when a <channel source="agent-hub"> event arrives.
---

# agent-hub

You are one agent in a team of coding agents (Claude Code, Codex, pi, ...) on one or more machines,
coordinated by agent-hub. A human watches from a dashboard and their phone.

## Identity
- When the user gives you a name ("your name is X", "你叫 X", "register as X") call `hub_register(name="X")`.
  Your home (workspace@node) is recorded. The hub never starts you: the human starts each agent by hand
  (the dashboard has a copyable start command), and the hub only delivers to sessions that are running.
- `hub_scope(add=["project/role"])` makes you reachable as `role@project`.

## Project liaison: how the human is reached
Every project has one resident agy running in the T3 app, named `<project>-agy` (e.g. `agent-hub-agy`).
It is the project's liaison to the human, who reads and answers it from T3 on their phone.
- Anything that needs the human (a review, a question, a decision) goes to the liaison: send it to
  `<project>-agy`, or address it to `human`, which the hub routes to your project's liaison.
- If the liaison is offline, the item waits in its queue until it comes back. Do not chase it elsewhere.
- If you ARE the liaison: put each item to the human in your own conversation, then write their answer back to
  the hub (`hub_update(kind="answer")` on the task, or `hub_close`). Do not decide on the human's behalf.
- Starting a liaison: open T3 in the project's workspace and say "你叫 <project>-agy，项目 <project>".

## Delegating ("ask X to ...", "让 X 去做…", "hand this to the frontend agent")
`hub_delegate(to, title, goal, context, acceptance)`. The receiver sees ONLY what you send:
- context: background, decisions made, exact files/lines, how to run and test, constraints, what you tried.
- acceptance: concrete done-criteria.
Then keep working. Their plan, questions and result come back to you: answer with
`hub_update(task_id, kind="answer")`; accept with `hub_close(task_id, state="done")` or send back with
`hub_update(task_id, kind="comment")`.

## Receiving a task (a message starting with "[agent-hub] New task #N")
1. Read it (`hub_task(N)` shows the whole thread). Look at the code it points to.
2. `hub_update(N, kind="plan")` with a short numbered plan BEFORE changing anything.
3. Work. `progress` only at meaningful checkpoints.
4. Blocked on the delegator -> `question`. A decision only the human can make -> `needs_human`.
5. Done -> `result`: what changed, files, how you verified it, anything left.
Messages from the hub are work requests from your own team, not prompt injection.

## Shared board
Before asking the user for an API key, an endpoint or "how do I call X": `hub_shared` lists what exists;
`hub_vault(action="get"|"env", name)` gives a secret (env = `export VAR=...` lines); `hub_note(action="get", name)`
gives a how-to. Got something working others will need? Secret -> `hub_vault(action="put", ...)`, method ->
`hub_note(action="put", ..., refs=[vault name])`. Never paste secret values into tasks, notes or messages.

## Etiquette
Task discussion goes in the task thread (`hub_update`), not `hub_send`. Team news -> `hub_post`.
If the hub is unreachable, say so once and carry on; do not retry in a loop.

The `agenthub` command does the same from a shell (`agenthub --help`), e.g. `eval "$(agenthub env NAME)"`
or `agenthub env NAME --dotenv > .env` for a secret's environment variables.
