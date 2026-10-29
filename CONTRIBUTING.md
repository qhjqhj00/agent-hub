# Contributing

Thanks for helping. A few ground rules keep agent-hub easy to deploy:

- **Standard library only**, Python 3.8+. No runtime dependencies, no build step. The dashboard
  is vanilla JS.
- **SQLite is the source of truth.** If you add state, add a table or column (append to
  `ADDED_COLUMNS` in `hub/store.py` so existing databases upgrade in place).
- **Agents read English.** Text that agents see (task texts, tool replies, instructions) stays
  English. Text for people goes through `hub/i18n.py` (server) or `I18N` in `web/app.js`
  (dashboard), in both `en` and `zh`.
- **New agent CLIs** are adapters. See [docs/agents.md](docs/agents.md). Many need no code at
  all (`~/.agent-hub/agents.json`).

## Running

```bash
./bin/agenthub up --port 8788 --data /tmp/hub-dev --no-setup
python3 -m unittest discover -s tests -v
node --check agenthub/hub/web/app.js
```

Tests start real hubs on free ports with throwaway data directories. They never touch your
`~/.agent-hub` or your agent CLIs' configs. Keep it that way: a test must not start a real
agent CLI.

## Pull requests

Keep them focused, describe the behaviour change, and add or adjust a test. For anything that
changes the wire protocol (`/v1` API, delivery semantics), open an issue first.
