# Deployment

## Requirements

- **Hub**: Python 3.10+ and `git` (it serves the skill marketplace over git).
- **Nodes**: Python 3.10+ and the agent CLIs you use, already logged in. Linux or macOS.
  For agy, T3 Code (`t3 serve`); for the Codex bridge, `codex app-server`.

No other dependencies, and nothing to compile.

## Single machine

```bash
curl -fsSL https://raw.githubusercontent.com/qhjqhj00/agent-hub/main/install.sh | sh
agenthub up            # foreground; Ctrl-C stops it
```

The hub binds `127.0.0.1:8787` and stores its data in `~/.agent-hub/hub`. `agenthub up` writes
the admin token into `~/.agent-hub/config.json`, so this machine's clients are logged in. It
also sets up every agent CLI it finds; `--no-setup` skips that, and `agenthub setup` does it later.

## Running the hub as a service

A systemd user unit (`~/.config/systemd/user/agent-hub.service`):

```ini
[Unit]
Description=agent-hub
After=network-online.target

[Service]
Environment=HUB_IDENTITY=token
Environment=HUB_DATA=%h/.agent-hub/hub
ExecStart=%h/.local/bin/agenthub serve --host 0.0.0.0 --port 8787
Restart=always

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now agent-hub
sudo loginctl enable-linger $USER          # keep it running after logout
journalctl --user -u agent-hub -f          # logs
```

On macOS, a launchd agent running the same `agenthub serve` command does the same job.

## Several machines on one network (LAN, VPN, WireGuard, ...)

On the hub machine:

```bash
agenthub nodes add laptop      # one token per machine; prints its installer line
agenthub nodes add gpu-01
```

On each node:

```bash
curl -fsSL http://HUB:8787/install.sh | sh -s -- --join http://HUB:8787 <node token>
```

The installer puts `agenthub` in `~/.local/bin`, saves the hub address and token, and runs
`agenthub setup` for the agent CLIs it finds. Manage tokens with `agenthub nodes` and
`agenthub nodes revoke gpu-01`; a revoked node gets 401 on its next request.

Set `HUB_URL=http://hub.example:8787` on the hub if nodes reach it under a different name than
the one in the requests (NAT, a proxy). The installer links use it.

## Per-machine extras

- **agy in T3**: run `agenthub setup agy --workspace <dir>` once per workspace (it writes
  `.agents/hooks.json`, kept out of git), and keep one `agenthub t3-bridge` running next to
  `t3 serve`. The bridge only delivers to T3 threads of agents that registered.
- **Codex**: run `codex app-server daemon start` and `agenthub codex-bridge`, then start Codex
  with `codex --remote unix://$HOME/.codex/app-server-control/app-server-control.sock`.
- **pi**: `agenthub setup pi` installs the extension; nothing else runs.

Bridges never start agents. Run them in a terminal, or as user services like the hub above.

## Tailscale

If every machine is on one tailnet, you can drop node tokens:

```bash
agenthub serve --host <tailnet ip> --identity tailscale
# on each node
curl -fsSL http://<hub>:8787/install.sh | sh -s -- --join http://<hub>:8787 ""
```

The hub asks `tailscaled` who is calling (`tailscale whois`), and every device on the tailnet is
trusted as a node. Admin actions on the dashboard still need the admin token. Use tailnet ACLs to
decide who may reach the hub.

## Docker (hub only)

```bash
docker compose up -d
docker compose exec hub python3 bin/agenthub token      # the admin token
```

Agents run on your machines, not in the container. Join nodes as above.

## TLS / reverse proxy

The hub speaks plain HTTP. If it leaves a private network, put it behind a reverse proxy with
TLS. Disable buffering on the proxy, because long-polls and SSE need it:

```nginx
location / {
  proxy_pass http://127.0.0.1:8787;
  proxy_http_version 1.1;
  proxy_buffering off;
  proxy_read_timeout 120s;
  proxy_set_header Host $host;
}
```

Then set `HUB_URL=https://hub.example.com` and have nodes join `https://hub.example.com`.

## Upgrades

- Hub: run the installer again (or `git pull`), then restart the hub.
- Nodes: run the installer line again; it fetches the hub's `agenthub.pyz`. Claude Code picks
  up a new plugin version at its next start (`agenthub setup claude` turns on marketplace
  auto-update).

The schema only grows: a newer hub opens an older 3.x database in place. A 2.x database is
moved with `agenthub migrate --from <2.x hub.db>` into a new data directory.

## Moving from a 2.x hub

1. Stop the 2.x hub, then copy its database (`sqlite3 hub.db ".backup v2.db"`).
2. On the new hub machine: `agenthub migrate --from v2.db` (the old file is opened read-only),
   then start the hub. Fix agent kinds it lists (`agenthub agent-set NAME --kind agy`) and set
   each project's liaison (`agenthub project NAME --liaison NAME-agy`).
3. On every node: run the installer with `--join`, then stop and disable the old `agent-hubd`
   service (`systemctl --user disable --now agent-hubd`).
4. Restart the agents; `agenthub start NAME` prints each one's command.

## Backups

Copy `~/.agent-hub/hub/` while the hub is stopped, or use `sqlite3 hub.db ".backup backup.db"`
while it runs. It holds the database (tasks, threads, the vault), the admin token and the skill
marketplace.
