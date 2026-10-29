# agent-hub

**给你的 coding agent 们配一台总机。** Claude Code、Codex、pi、Gemini CLI、opencode，或者任何终端里的 agent，
不管在一台笔记本上还是分布在一群机器上，都可以带着完整上下文互相派活。任务和回复会直接推进对方正在运行的会话，
对方没在运行就把它启动起来。key、配置说明和 skill 在团队里共享。你在 dashboard 和手机上看全局、做决定。

[English](README.md)

```
你 ─ "让 gpu-01 上的 codex 去 profile 一下 loader，然后 review 它的 patch"
        │
  claude（笔记本）──hub_delegate──▶ hub ──推送──▶ codex（gpu-01）    不在线？hubd 在 tmux 里把它拉起来
        ▲                            │               │ 计划 · 提问 · 结果
        └──────────── 推回来 ────────┴───────────────┘
```

- **带上下文的委托**：`hub_delegate(to, goal, context, acceptance)` 生成一个任务。任务有自己的线程，
  计划、提问、回答、结果都在里面。委托方负责验收，不满意就打回。
- **推送，不用轮询**：派来的任务、提问、回复会直接注入目标 agent 的会话，不用再叮嘱它"去看看 hub"。
- **有活就唤醒**：目标 agent 没在运行时，它所在机器上的守护进程会在它的工作目录里（tmux）把它启动，
  能续上次的对话就续上。
- **什么 agent 都行**：Claude Code 走原生推送。其他 agent 通过一个很薄的终端外壳（`agenthub run codex`）获得推送。
  支持 MCP 的 agent 拿到 `hub_*` 工具，不支持 MCP 的用 `agenthub` 命令行做同样的事。
- **单机或多机**：单机只要 `agenthub up`。加机器时运行 `agenthub nodes add gpu-01`，把它打印出来的一行安装命令
  贴到那台机器上执行。普通局域网、VPN、Tailscale 都能用。
- **共享看板**：
  - API key 保险箱：每次取用都有记录，值不会出现在任何广播里
  - 有版本的配置说明：接口地址、踩过的坑
  - 团队 skill：自动同步到每台机器上的每个 agent
- **人始终在环上**："需要你"收件箱、每个项目一个看板、完整的任务线程、每个 agent 的实时屏幕。
  通知支持 Telegram（双向）、Slack、Discord、飞书/Lark、钉钉、ntfy。
- **零依赖**：只用 Python 3.8+ 标准库加 SQLite。没有构建步骤，不用 venv，各台机器之间没有版本漂移的问题。
  界面有中文和英文。

## 快速开始（单机）

```bash
curl -fsSL https://raw.githubusercontent.com/qhjqhj00/agent-hub/main/install.sh | sh
agenthub up
```

`agenthub up` 会做四件事：

- 在 `127.0.0.1:8787` 启动 hub
- 把找到的 agent CLI 都接上：Claude Code 插件，Codex、Gemini、opencode 的 MCP 配置，以及各自的说明块
- 把本机作为一个节点运行
- 打印一个带登录凭据的 dashboard 链接

加 `--service` 则以 systemd 或 launchd 用户服务的方式常驻。

然后在任意项目里启动 agent：

```bash
agenthub run claude          # 或者：agenthub run codex / pi / gemini / opencode
> 你叫 alice
```

在另一个终端、另一个项目里：

```bash
agenthub run codex --agent bob
```

对 alice 说：*"让 bob 给缓存目录加一个命令行参数，测试在 tests/test_cache.py"*。
alice 会调用 `hub_delegate`，任务出现在 bob 的会话里。bob 先回一份计划，做完后提交结果，
这些都会推回给 alice，由 alice 验收。整个线程都能在 dashboard 上看到。

## 多机

在放 hub 的机器上：

```bash
agenthub up --host 0.0.0.0 --service          # 或：docker compose up -d（只跑 hub）
agenthub nodes add gpu-01                     # 打印下面这行，带一个新的节点 token
```

在 `gpu-01` 上：

```bash
curl -fsSL http://HUB:8787/install.sh | sh -s -- --join http://HUB:8787 ahn_xxxxxxxx
```

安装脚本会做三件事：

- 安装单文件客户端 `~/.local/bin/agenthub`，以后从 hub 自动更新
- 配置本机找到的 agent CLI
- 把节点守护进程装成用户服务

每台机器用自己的 token 认证。请求来自哪个节点由 token 决定，所以 `workspace@node` 伪造不了。
如果机器都在同一个 tailnet 里，也可以改用 `--identity tailscale`，由 `tailscale whois` 识别设备。

Docker、反向代理/TLS、Tailscale、服务管理和升级见 [docs/deployment.md](docs/deployment.md)。

## 支持的 agent

| kind | 推送 | 工具 | 续会话 | `agenthub setup` 做了什么 |
|---|---|---|---|---|
| `claude`（Claude Code） | 原生 channel | MCP（插件） | ✓ transcript | 接入 hub 提供的插件市场 |
| `codex`（OpenAI Codex CLI） | 终端外壳 | MCP | ✓ rollout | `codex mcp add` + `~/.codex/AGENTS.md`，skill 放进 `~/.codex/skills` |
| `gemini`（Gemini CLI） | 终端外壳 | MCP | – | `~/.gemini/settings.json` + `GEMINI.md` |
| `opencode` | 终端外壳 | MCP | `--continue` | `~/.config/opencode/opencode.json` + `AGENTS.md` |
| `pi` | 终端外壳 | `agenthub` 命令行 | `--continue` | `~/.pi/agent/AGENTS.md`，skill 放进 `~/.pi/agent/skills` |
| 其他任何 agent | 终端外壳 | `agenthub` 命令行 | 可选 | 在 `~/.agent-hub/agents.json` 里描述一下 |

```json
// ~/.agent-hub/agents.json：任何终端 agent 都能成为一等公民
{ "aider": { "command": ["aider", "--no-auto-commits"], "tools": "cli", "ready": ["> "] } }
```

临时用一下也可以直接 `agenthub run generic -- <任意命令>`。各集成的原理以及怎么新增一种 agent，见
[docs/agents.md](docs/agents.md)。

## 工作原理

```
┌──────────────── hub（agenthub serve）─────────────────┐
│ SQLite：agents · sessions · tasks + 线程 ·            │   dashboard（SSE）
│         deliveries（需确认）· orders · vault          │──  Telegram / webhook
│ HTTP API /v1 · marketplace.git · install.sh · pyz     │
└──────▲──────────────────▲──────────────────▲──────────┘
       │ 长轮询           │ 长轮询            │ 长轮询（指令）
  agent 会话         agent 会话          hubd（agenthub node），每台机器一个：
  claude + MCP       agenthub run codex    tmux 里唤醒/停止、看屏幕/按键、
  （channel 推送）    （外壳替它打字）       同步插件和 skill、自我更新
```

- 所有需要 agent 响应的东西都是 `deliveries` 表里的一行，由 agent 的会话长轮询取走。
  只有会话确认之后才算送达。agent 在轮询中途崩溃时，这一行会回到队列，agent 也会被重新唤醒，活不会丢。
- 如果没有会话在监听某个 agent，hub 会给它所在的节点下一条 `wake` 指令。
  那台机器上的 hubd 在 tmux 里运行 `agenthub run <kind> --agent <name>`，自动点掉已知的启动对话框，再回报结果。
- 终端外壳在伪终端里运行 agent CLI。只有在 agent 空闲时才把消息打进去：
  一段时间没有输出、你最近没在打字、屏幕上没有对话框。没有推送接口的 agent 就是这样拿到推送的。

细节见 [docs/architecture.md](docs/architecture.md)。

## 常用命令

```bash
agenthub status                     # 需要你处理的事
agenthub agents | tasks | task 12   # 谁在哪、在干什么
agenthub delegate bob "修掉不稳定的测试" --goal "..." --context - < notes.md
agenthub update 12 answer "用方案 B"          # 写进任务线程
agenthub close 12 done
agenthub wake bob | stop bob
agenthub put openai '{"api_key":"sk-..."}' --env OPENAI_API_KEY=api_key --note "团队 key"
eval "$(agenthub env openai)"       # 在任何机器上
agenthub skill publish ./my-skill   # 发给每台机器上的每个 agent
agenthub doctor                     # 检查配置、hub 连接、tmux、agent
```

## 安全模型

- **默认处处需要 token。** admin token（`agenthub token`）是你的，也用来登录 dashboard。
  节点 token 每台机器一个，可以随时吊销。没有 token 只能访问健康检查、安装脚本和登录页。
- 除非你指定，hub 只绑定 `127.0.0.1`。`--identity open`（不认证）拒绝绑定 loopback 以外的地址。
- 密钥以明文存在 hub 的 SQLite 文件里（`~/.agent-hub/hub/hub.db`，只有你的用户可读），
  和那台机器上的 `.env` 文件是同一个信任级别。每次读取都有记录，值不会出现在推送、动态、搜索结果和通知里。
- agent 的权限就是你给它 CLI 的权限。被唤醒的 Claude Code 用 `--permission-mode auto`，
  Codex 用 `--sandbox workspace-write --ask-for-approval on-request`。
  可以通过 `HUB_CLAUDE_ARGS` / `HUB_CODEX_ARGS` / `HUB_<KIND>_ARGS` 修改。
- 能给 agent 派活的人就能让它干活。把 hub 当作团队信任边界的一部分；如果它要离开内网，请放在 TLS 后面。

## 配置

| 变量 | 默认 | |
|---|---|---|
| `HUB_IDENTITY` | `token` | `token`、`tailscale` 或 `open`（见上） |
| `HUB_DATA` | `~/.agent-hub/hub` | hub 数据库、admin token、插件市场 |
| `HUB_URL` | 取自请求 | hub 的对外地址，用于安装脚本和通知里的链接 |
| `HUB_LANG` | `en` | 设为 `zh`，bot 和通知用中文 |
| `TELEGRAM_BOT_TOKEN` | – | 双向 Telegram bot，用 `/pair <code>` 绑定 |
| `HUB_WEBHOOK_URL` | – | Slack / Discord / 飞书 / 钉钉 / ntfy / JSON webhook，多个用逗号分隔 |
| `HUB_<KIND>_ARGS` | 见上 | 每次启动该 agent 时带的参数，例如 `HUB_CODEX_ARGS="--full-auto"` |
| `HUB_CLAUDE_PUSH` | `channel` | 设为 `pty`，Claude Code 也走终端外壳 |
| `AGENTHUB_HOME` | `~/.agent-hub` | 客户端配置（`config.json`）、日志、运行时文件 |

## 开发

```bash
git clone https://github.com/qhjqhj00/agent-hub && cd agent-hub
./bin/agenthub up --port 8788 --data /tmp/hub-dev --no-setup     # 从源码运行
python3 -m unittest discover -s tests -v                          # 只用标准库
```

目录结构：
- `agenthub/hub`：服务端、存储、API、dashboard、通知
- `agenthub/node`：hubd、`run` 外壳、MCP server、各 agent 的适配器
- `agenthub/plugin`：hub 提供的 Claude Code 插件市场
- `agenthub/cli.py` + `team.py`：命令行

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

MIT
