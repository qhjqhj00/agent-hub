# Contributing

Thanks for helping. A few ground rules keep agent-hub easy to deploy:

- **Standard library only**, Python 3.10+. No runtime dependencies, no build step. The dashboard
  is vanilla JS.
- **SQLite is the source of truth.** If you add state, add a table or column (append to
  `ADDED_COLUMNS` in `hub/store.py` so existing databases upgrade in place).
- **The hub never starts agents.** No tmux, no pty wrappers, no screen scraping. Delivery goes
  through each agent's own interface (MCP channel, hooks, extensions, app-server, T3); see
  [docs/agents.md](docs/agents.md).
- **Agents read English.** Text that agents see (task texts, tool replies, instructions) stays
  English. The dashboard is in Chinese for now.
- **The wire protocol is [docs/refactor/api.md](docs/refactor/api.md).** Change it there first.

## Running

```bash
./bin/agenthub up --port 8788 --data /tmp/hub-dev --no-setup
cd tests && python3 -m unittest -v
node --check agenthub/hub/web/app.js
```

Tests start real hubs on free ports with throwaway data directories. They never touch your
`~/.agent-hub` or your agent CLIs' configs. Keep it that way: a test must not start a real
agent CLI.

## Pull requests

Keep them focused, describe the behaviour change, and add or adjust a test. For anything that
changes the wire protocol (`/v1` API, delivery semantics), open an issue first.
