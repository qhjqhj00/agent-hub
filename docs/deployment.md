# Deployment

## Requirements

- **Hub**: Python 3.8+ and `git` (it serves the skill marketplace over git).
- **Nodes**: Python 3.8+, `tmux` (to start agents when work arrives), and the agent CLIs you
  use, already logged in. Linux or macOS.

No other dependencies, and nothing to compile.

## Single machine

```bash
curl -fsSL https://raw.githubusercontent.com/qhjqhj00/agent-hub/main/install.sh | sh
agenthub up            # foreground; Ctrl-C stops it
agenthub up --service  # or: as systemd (Linux) / launchd (macOS) user services
```

The hub binds `127.0.0.1:8787` and stores its data in `~/.agent-hub/hub`. `agenthub up` writes
the admin token into `~/.agent-hub/config.json`, so this machine's clients are logged in. It
also sets up every agent CLI it finds; `--no-setup` skips that, and `agenthub setup` does it later.

## Several machines on one network (LAN, VPN, WireGuard, ...)

On the hub machine:

```bash
agenthub up --host 0.0.0.0 --service
agenthub nodes add laptop      # one token per machine; prints its installer line
agenthub nodes add gpu-01
```

On each node:

```bash
curl -fsSL http://HUB:8787/install.sh | sh -s -- --join http://HUB:8787 <node token>
```

Manage tokens with `agenthub nodes tokens` and `agenthub nodes revoke gpu-01`. A revoked node
gets 401 on its next request.

Set `HUB_URL=http://hub.example:8787` on the hub if it is reached under a different name than
the one in the requests (behind NAT, or a proxy). Installer and notification links use it.

## Tailscale

If every machine is on one tailnet, you can drop node tokens:

```bash
agenthub up --host <tailnet ip> --identity tailscale --service
# on each node
curl -fsSL http://<hub>:8787/install.sh | sh -s -- --join http://<hub>:8787 ""
```

The hub then asks `tailscaled` who is calling (`tailscale whois`), and every device on the
tailnet is trusted. The dashboard still needs the admin token unless you open it from a tailnet
device. Use tailnet ACLs to decide who may reach the hub.

## Docker (hub only)

```bash
docker compose up -d
docker compose exec hub python3 bin/agenthub token      # the admin token
```

Nodes cannot run in a container in any useful way, because hubd starts agents in tmux on its
own host. Join them as above.

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

## Notifications

- **Telegram** (two-way: answer questions, accept results, approve permission prompts):
  1. Create a bot with @BotFather.
  2. Set `TELEGRAM_BOT_TOKEN` (or write it to `~/.agent-hub/hub/telegram.token`) and restart the hub.
  3. Send the bot `/pair <code>`; the code is on the dashboard's Guide page.
- **Webhooks** (one-way): set `HUB_WEBHOOK_URL`, comma-separated for several. Slack, Discord,
  Feishu/Lark, DingTalk and ntfy URLs are recognised; anything else gets JSON
  `{kind, task_id, from, text, url}`. `HUB_WEBHOOK_FORMAT` forces a format.

## Services and logs

| | Linux | macOS |
|---|---|---|
| hub | `systemctl --user status agent-hub` | `launchctl list dev.agent-hub.agent-hub` |
| node | `systemctl --user status agent-hubd` | `launchctl list dev.agent-hub.agent-hubd` |
| logs | `journalctl --user -u agent-hubd`, `~/.agent-hub/*.log` | `~/.agent-hub/agent-hubd.log` |

On Linux, run `sudo loginctl enable-linger $USER` so services survive logout. Restarting hubd
does not kill the agents it started: they run in their own tmux scopes.

`agenthub doctor` checks the config, the hub connection and your token, tmux, and the agent CLIs.

## Upgrades

- Hub from source or the GitHub installer: run the installer again (or `git pull`), then restart
  the hub service.
- Nodes installed from the hub: nothing to do. hubd fetches the hub's `agenthub.pyz` every 10
  minutes and restarts itself.
- pip installs (`pipx install git+https://github.com/qhjqhj00/agent-hub`): `pipx upgrade agent-hub-cli`.

The database schema only grows. A newer hub opens an older database in place.

## Backups

Copy `~/.agent-hub/hub/` while the hub is stopped, or use
`sqlite3 hub.db ".backup backup.db"` while it runs. It holds the database (tasks, vault),
the admin token and the skill marketplace.
