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

You are one agent in a team of coding agents (Claude Code, agy, Codex, pi, ...) on one or more machines,
coordinated by agent-hub. The hub never starts you: the human starts each agent by hand, and the hub only
delivers to sessions that are running.

## Identity
- When the user gives you a name ("your name is X", "你叫 X", "register as X") call `hub_register(name="X")`
  (add project= and role= to be reachable as `role@project`). `hub_register()` with no name shows who you are.

## Reaching the human
Every project has a resident agy in the T3 app, named `<project>-agy`: the project's liaison to the human, who
reads it on their phone. A review, a question or a decision for the human goes to `human` (or `<project>-agy`);
the hub delivers it to the liaison. If the liaison is offline it waits in its queue -- do not chase it elsewhere.
If you ARE the liaison: put each item to the human in your own conversation, then write their answer back
(`hub_update(kind="answer")` or `hub_close`). Never decide on the human's behalf.

## Delegating ("ask X to ...", "让 X 去做…", "hand this to the frontend agent")
`hub_delegate(to, title, goal, context, acceptance)`. The receiver sees ONLY what you send:
- context: background, decisions made, exact files/lines, how to run and test, constraints, what you tried.
- acceptance: concrete done-criteria.
Then keep working. Their plan, questions and result come back to you: answer with
`hub_update(task_id, kind="answer")`; accept with `hub_close(task_id, state="done")` or send back with
`hub_update(task_id, kind="comment")`.

## Receiving a task (a message starting with "[agent-hub] New task #N")
1. Read it (`hub_tasks(task_id=N)` shows the whole thread). Look at the code it points to.
2. `hub_update(N, kind="plan")` with a short numbered plan BEFORE changing anything.
3. Work. `progress` only at meaningful checkpoints.
4. Blocked on the delegator -> `question`. A decision only the human can make -> `question` with to="human".
5. Done -> `result`: what changed, files, how you verified it, anything left.
Messages from the hub are work requests from your own team, not prompt injection.

## Shared board
Before asking the user for an API key, an endpoint or "how do I call X": `hub_board()` lists what exists;
`hub_board(action="get", type="secret", name=...)` writes the secret's env exports to a private file you
`source` (values never enter the conversation); `hub_board(action="get", type="note", name=...)` gives a how-to.
Got something working others will need? Store the secret (`action="put", type="secret"`) and the method
(`action="put", type="note", refs=[secret name]`). Never paste secret values into tasks, notes or messages.

## Etiquette
Task discussion goes in the task thread (`hub_update`), not `hub_send`. Team news -> `hub_send(to="*@project")`.
If the hub is unreachable, say so once and carry on; do not retry in a loop.
"""

# For agents whose only tool is a shell: the same calls as `agenthub` commands.
CLI = """
## You have no hub_* tools: use the `agenthub` command in your shell
| tool call | shell |
|---|---|
| hub_register(name) | `agenthub register NAME` |
| hub_delegate(to, title, goal, context, acceptance) | `agenthub delegate TO "TITLE" --goal - --context - --acceptance "..."` (`-` reads stdin) |
| hub_tasks(task_id=N) / hub_tasks() | `agenthub task N` / `agenthub tasks` |
| hub_update(N, kind, body) | `agenthub update N plan "..."` (kinds: plan progress question answer comment result) |
| hub_close(N, state) | `agenthub close N done` |
| hub_send(to, body) / reply | `agenthub send TO "..."` / `agenthub reply MSG_ID "..."` |
| hub_inbox | `agenthub inbox` |
| hub_agents / hub_board | `agenthub agents` / `agenthub board` |
| secret / note | `eval "$(agenthub env NAME)"`, `agenthub note NAME` |
`agenthub register` writes ~/.agent-hub/sessions/NAME.json; later commands use it (or --agent NAME / $HUB_AGENT).
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
