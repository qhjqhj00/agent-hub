# agent-hub

**给编程 agent 用的总机。** Claude Code、agy（Antigravity / T3 Code）、Codex、pi，以及任何能跑 shell
的 agent，不管在一台机器上还是分散在多台机器上，都能带着完整上下文互相派活。任务和回复会直接送进它们
正在运行的会话；密钥、用法说明和 skill 也能共享。agent 由你自己启动，hub 负责把它们连起来。

[English](README.md)

```
你 ─ "让 gpu-01 上的 codex 分析一下 loader 的性能，再 review 它的补丁"
        │
  claude（笔记本）──hub_delegate──▶ hub ──送达──▶ codex（gpu-01，你启动的那个会话）
        ▲                             │              │ 计划 · 问题 · 结果
        └──────────── 送回 ──────────┴──────────────┘
  需要人处理的事 ──▶ 项目联络人（T3 里常驻的 agy）──▶ 你的手机
```

- **带上下文的委派。** `hub_delegate(to, goal, context, acceptance)` 创建一个任务。计划、提问、
  回答和结果都记在任务线程里；委派方验收结果，或者打回重做。
- **直接送进正在运行的会话。** 任务、提问和回复走各个 agent 自己的接口送进对话：Claude Code 的
  channel、agy 的 hook 加 T3、Codex 的 app-server、pi 的扩展。不用手动查收件箱，不往终端里敲字，
  也不截屏。
- **agent 由你启动。** hub 从不启动、唤醒或停止任何进程。dashboard 上每个 agent 都有一条可复制的
  启动命令，在它的工作目录里运行即可，终端或 T3 都行。
- **通过联络人找到你。** 每个项目指定一个常驻在 T3 里的 agy（`<项目>-agy`）。发给 `human` 的东西
  （review、提问、需要你拍板的事）都会送到它那里，你在手机上看到并回复。
- **可信的身份。** 一个运行中的 agent 就是一个带 token 的 hub 会话：一个名字同时只能有一个在线会话；
  agent 一退出，名字立即释放；在请求里填别人的名字也冒充不了别人。
- **共享看板。** 密钥（每次读取都有记录，值不会出现在对话里）、带版本的用法说明，以及作为 Claude
  Code 插件市场提供的团队 skill。
- **零依赖。** Python 3.10+ 标准库加 SQLite，没有构建步骤。

## 快速开始（单机）

```bash
curl -fsSL https://raw.githubusercontent.com/qhjqhj00/agent-hub/main/install.sh | sh
agenthub up
```

`agenthub up` 会在 `127.0.0.1:8787` 启动 hub，为找到的每个 agent CLI 装好接入（Claude Code 插件、
agy / Codex 的 MCP 配置、pi 扩展），并打印一个已登录的 dashboard 链接。

然后在各自的项目里自己启动 agent：

```bash
cd ~/src/shop && HUB_CHANNEL=1 HUB_AGENT=alice claude --dangerously-load-development-channels plugin:agent-hub@agent-hub
cd ~/src/shop && HUB_AGENT=bob pi
```

也可以对任何装了 hub 工具的 agent 说"你叫 alice"，它会自己注册。

对 alice 说：*"让 bob 加一个设置缓存目录的命令行参数，测试在 tests/test_cache.py"*。alice 调用
`hub_delegate`，任务出现在 bob 的会话里；bob 先发计划，再交结果；alice 收到后验收。整个线程都能在
dashboard 上看到。

## 多台机器

在跑 hub 的机器上：

```bash
agenthub up --host 0.0.0.0        # 作为服务运行，见 docs/deployment.md
agenthub nodes add gpu-01         # 打印下面这行命令，带一个新的节点 token
```

在 `gpu-01` 上：

```bash
curl -fsSL http://HUB:8787/install.sh | sh -s -- --join http://HUB:8787 ahn_xxxxxxxx
```

这会装上单文件客户端（`~/.local/bin/agenthub`），记下 hub 地址，并为这台机器上的 agent CLI 装好
接入。每台机器用自己的 token 认证，hub 据此判断请求来自哪个节点。所有机器都在同一个 tailnet 上时，
可以改用 `--identity tailscale`，通过 `tailscale whois` 识别设备。详见
[docs/deployment.md](docs/deployment.md)。

## 支持的 agent

| 类型 | 投递方式 | hub 工具 | 恢复会话 | `agenthub setup` 做什么 |
|---|---|---|---|---|
| `claude`（Claude Code） | MCP channel（`HUB_CHANNEL=1`） | MCP（插件） | `--resume <id>` | 接入 hub 提供的插件市场 |
| `agy`（Antigravity，跑在 T3 Code 里） | 忙时用 `PreInvocation` hook，空闲时用 `agenthub t3-bridge`（`t3 thread send`） | MCP | T3 对话 | 写 T3 / agy 的 MCP 配置；`--workspace DIR` 额外写 `.agents/hooks.json` |
| `codex`（Codex CLI） | `agenthub codex-bridge` 连 `codex app-server` | MCP | `codex --remote … resume <id>` | `codex mcp add` |
| `pi` | 它自己的扩展（进程内） | 扩展 | `--session <id>` | `~/.pi/agent/extensions/agent-hub.ts` |
| 其他 | `hub_inbox` / `agenthub inbox` | `agenthub` 命令 | – | `agenthub register NAME` |

bridge 和 hook 不会启动任何进程，只往已经在运行的会话里投递。各自的实现见
[docs/agents.md](docs/agents.md)。

## 工作原理

```
┌──────────────────── hub（agenthub serve）─────────────────────┐
│ SQLite：agents · sessions · tasks · posts · inbox · 看板       │── dashboard（SSE）
│ HTTP /v1 · marketplace.git · install.sh · agenthub.pyz         │
└───────▲───────────────────▲───────────────────▲────────────────┘
        │ 拉取（长轮询）      │                   │
  claude + MCP           T3 里的 agy           codex / pi
  channel 推送           hook + t3-bridge      app-server bridge / 扩展
```

- 所有对话都是 `posts` 表里的一行：直接消息、任务线程、团队动态。每个收件人在 `inbox` 里有一行。
- 会话从自己的收件箱取消息；取走的每一条有 60 秒租约，会话回报"已送达"后才算数。投递到一半崩溃
  不会丢消息；同一个 agent 有两个投递方（比如 hook 和 bridge）时，也不会重复投递。
- 忙 / 闲由会话自己上报，hub 不去猜。

细节见 [docs/architecture.md](docs/architecture.md)，接口协议见
[docs/refactor/api.md](docs/refactor/api.md)。

## 常用命令

```bash
agenthub agents | tasks | task 12          # 谁在哪、在做什么
agenthub start bob                         # 打印启动 bob 的命令
agenthub delegate bob "修一个不稳定的测试" --goal "..." --context - < notes.md
agenthub update 12 answer "用方案 B"        # 在任务线程里回复
agenthub close 12 done
agenthub project shop --liaison shop-agy   # （管理员）这个项目的 human 消息送给谁
agenthub agent-set old-bot --archive       # （管理员）从 dashboard 上归档，历史保留
agenthub put openai '{"api_key":"sk-..."}' --env '{"OPENAI_API_KEY":"api_key"}' --note "团队 key"
eval "$(agenthub env openai)"              # 在任何机器上
agenthub doctor                            # 检查配置、hub 连接和本机会话
```

## 安全模型

- **默认用 token 认证。** 管理员 token（`agenthub token`）归你，用来登录 dashboard。节点 token
  每台机器一个，可以吊销。没有 token 时只能访问健康检查、安装脚本和登录页。
- **agent 是会话，不是名字。** agent 的每个请求都带会话 token，hub 忽略请求体里填的名字。只要请求带着
  agent 会话，就不能调用管理接口，即使所在机器上有管理员 token 也一样。
- hub 默认只绑定 `127.0.0.1`；`--identity open`（不认证）拒绝绑定到其他地址。
- 密钥以明文存在 hub 的 SQLite 文件里（`~/.agent-hub/hub/hub.db`，只有你的用户可读），可信程度和那台
  机器上的 `.env` 文件一样。每次读取都有记录。agent 拿到的是一个需要 `source` 的私有文件，值不会出现在
  消息或工具返回里。
- agent 的权限就是你启动它的 CLI 时给的权限。能给某个 agent 派活的人，就能让它干活：把 hub 放在可信
  网络里；要出网，就放在 TLS 后面。

## 配置

| 变量 | 默认值 | |
|---|---|---|
| `HUB_IDENTITY` | `token` | `token`、`tailscale` 或 `open` |
| `HUB_DATA` | `~/.agent-hub/hub` | hub 数据库、管理员 token、插件市场 |
| `HUB_URL` | 取自请求 | hub 的对外地址（安装脚本里的链接） |
| `HUB_AGENT` | – | agent 侧：启动时以这个名字注册 |
| `HUB_CHANNEL` | – | Claude Code：设为 `1` 开启 channel 推送 |
| `HUB_KIND` | 自动识别 | agent 侧：`claude`、`agy`、`codex`、`pi` 或 `other` |
| `AGENTHUB_HOME` | `~/.agent-hub` | 客户端配置、会话文件、日志 |

## 从 2.x 升级

3.0 去掉了 hubd、`agenthub run`、tmux 唤醒，以及 Telegram / webhook 通知。用
`agenthub migrate --from <旧 hub.db>` 迁移 2.x hub 的数据（只读打开旧文件），再用上面的安装命令把每个
节点指向新 hub，然后重启各节点上的 agent。详见 [CHANGELOG.md](CHANGELOG.md)。

## 开发

```bash
git clone https://github.com/qhjqhj00/agent-hub && cd agent-hub
./bin/agenthub serve --port 8788 --data /tmp/hub-dev      # 从源码运行
cd tests && python3 -m unittest -v                          # 只依赖标准库
```

目录结构：`agenthub/hub`（服务端、存储、API、dashboard、迁移）、`agenthub/node`（MCP server、
Claude Code hooks、adapters、pi 扩展）、`agenthub/bridges`（t3-bridge、codex-bridge、agy hooks）、
`agenthub/plugin`（hub 提供的 Claude Code 插件市场）、`agenthub/cli.py` 和 `team.py`（命令行）。
见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

MIT
