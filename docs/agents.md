# Agents

An adapter (`agenthub/node/adapters/`) tells agent-hub how to work with one coding-agent CLI:

- `argv(resume, prompt, extra)`: how to start it
- `push`: `"channel"` (native) or `"pty"` (the `agenthub run` wrapper types messages in)
- `tools`: `"mcp"` (`agenthub mcp --kind <kind>`) or `"cli"` (`agenthub ...` in its shell)
- `ready_markers` / `busy_markers` / `blockers`: screen text meaning "input box is up",
  "working" and "a dialog is waiting"
- `dialogs`: start-up prompts hubd may answer on its own
- `resumable()` / `find_conversation()`: keep the agent's memory across wakes
- `setup()` / `teardown()`: install or remove the integration; `instructions_file` gets a
  marked agent-hub block, and `skills_dir` receives team skills

## Built in

### Claude Code (`claude`)
- `agenthub setup claude` adds the hub's marketplace (`http://agenthub:<token>@hub/marketplace.git`)
  and installs `agent-hub@agent-hub` and `team-skills@agent-hub`.
- `agenthub run claude` = `claude --permission-mode auto --dangerously-load-development-channels plugin:agent-hub@agent-hub`.
  Custom channels are a research-preview feature, which is why that flag is needed; hubd confirms
  its warning dialog when it wakes an agent. `HUB_CLAUDE_PUSH=pty` uses the wrapper instead.
- Conversations are transcripts under `~/.claude/projects`. The plugin's SessionStart hook
  reports `/resume` switches, so the name follows the conversation.

### Codex (`codex`)
- `agenthub setup codex` runs `codex mcp add agent-hub -- agenthub mcp --kind codex` and writes
  the instructions block into `~/.codex/AGENTS.md`.
- Started with `--ask-for-approval on-request --sandbox workspace-write` (`HUB_CODEX_ARGS` overrides).
- The conversation is the rollout file that Codex keeps open (Linux), or the newest rollout
  started in that directory. It is resumed with `codex resume <uuid>`.

### Gemini CLI (`gemini`), opencode (`opencode`)
MCP server in `~/.gemini/settings.json` / `~/.config/opencode/opencode.json`, and an
instructions block in `~/.gemini/GEMINI.md` / `~/.config/opencode/AGENTS.md`.

### pi (`pi`)
No MCP, by design. The instructions block in `~/.pi/agent/AGENTS.md` maps every `hub_*` call
to an `agenthub` shell command, and the wrapper's typed messages end with a reminder of that.
Resume is `pi --continue` (the folder's latest session).

## Your own agent, no code

```json
// ~/.agent-hub/agents.json
{
  "aider": {
    "title": "aider",
    "command": ["aider", "--no-auto-commits"],
    "tools": "cli",
    "ready": ["> "],
    "busy": [],
    "instructions_file": "~/.aider.conventions.md",
    "resume_args": null
  }
}
```

`agenthub kinds` lists it, `agenthub run aider --agent carol` starts it, and hubd can wake it.
`agenthub run generic -- <command>` is the same thing for a one-off.

Tips for markers: run the agent in tmux and `tmux capture-pane -p`. Use text that appears only
when it is waiting for you (a prompt glyph, a footer hint) as `ready`, and text that appears
only while it works ("esc to interrupt") as `busy`. With no markers, the wrapper relies on output
going quiet.

## Writing a built-in adapter

Subclass `Adapter` in `agenthub/node/adapters/`, add it to `BUILTIN` in `__init__.py`, and add
a test for its `ready()` logic and its config writing (see `tests/test_node.py`). Keep it to
the standard library.
