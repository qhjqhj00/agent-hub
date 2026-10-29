"""What agents are told about the hub.

One text, three ways in: the MCP server's `instructions` (Claude Code, Codex,
Gemini CLI, opencode), the Claude Code plugin skill, and a marked block in the
agent's global instructions file (AGENTS.md, GEMINI.md) written by
`agenthub setup`. Agents without MCP (pi, anything generic) get the same
text with the tool calls spelled as `agenthub` shell commands.
"""

START = "<!-- agent-hub:start -->"
END = "<!-- agent-hub:end -->"

GUIDE = """# agent-hub

You are one agent in a team of coding agents (Claude Code, Codex, pi, ...) on one or more machines,
coordinated by agent-hub. A human watches from a dashboard and their phone.

## Identity
- When the user gives you a name ("your name is X", "你叫 X", "register as X") call `hub_register(name="X")`.
  Your home (workspace@node) is recorded; the hub restarts you there when work arrives and you are not running.
- `hub_scope(add=["project/role"])` makes you reachable as `role@project`.

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
"""

# For agents whose only tool is a shell: the same calls as `agenthub` commands.
CLI = """
## You have no hub_* tools: use the `agenthub` command in your shell
| tool call | shell |
|---|---|
| hub_register(name) | `agenthub register NAME` |
| hub_delegate(to, title, goal, context, acceptance) | `agenthub delegate TO "TITLE" --goal - --context - --acceptance "..."` (`-` reads stdin) |
| hub_task(N) / hub_tasks | `agenthub task N` / `agenthub tasks --mine` |
| hub_update(N, kind, body) | `agenthub update N plan "..."` (kinds: plan progress question answer comment blocked needs_human result) |
| hub_close(N, state) | `agenthub close N done` |
| hub_send(to, body) / reply | `agenthub send TO "..."` / `agenthub reply MSG_ID "..."` |
| hub_inbox | `agenthub inbox` |
| hub_agents / hub_shared | `agenthub agents` / `agenthub shared` |
| hub_vault get/env, hub_note get | `agenthub get NAME`, `eval "$(agenthub env NAME)"`, `agenthub note get NAME` |
Your identity is picked up automatically when you were started with `agenthub run`.
"""


def guide(cli=False):
    return GUIDE + (CLI if cli else "")


def block(cli=False):
    """The marked block for an agent's global instructions file."""
    return "%s\n%s\n%s\n" % (START, guide(cli).strip(), END)


def upsert(path, cli=False):
    """Write (or refresh) the block in an instructions file; returns True when the file changed."""
    import os
    old = ""
    if os.path.isfile(path):
        with open(path) as fh:
            old = fh.read()
    new_block = block(cli)
    if START in old and END in old:
        a, b = old.index(START), old.index(END) + len(END)
        new = old[:a] + new_block.rstrip("\n") + old[b:]
    else:
        new = (old.rstrip("\n") + "\n\n" if old.strip() else "") + new_block
    if new == old:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(new)
    return True


def remove(path):
    import os
    if not os.path.isfile(path):
        return False
    with open(path) as fh:
        old = fh.read()
    if START not in old:
        return False
    a, b = old.index(START), old.index(END) + len(END)
    with open(path, "w") as fh:
        fh.write((old[:a].rstrip("\n") + "\n" + old[b:].lstrip("\n")).strip("\n") + "\n")
    return True
