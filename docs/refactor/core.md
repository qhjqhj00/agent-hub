# agent-hub 重构设计：hub 核心（core）

状态：草案，待用户过目 · 作者：sa-cc · 分支：sa-cc · 2026-11-20
配套：`docs/refactor/adapters.md`（sa-agy，任务 #115，各类 agent 的推送和接入）

## 0. 目标

hub 只做三件事：

1. **注册**：agent 有一个名字，归属一个会话、一个工作区、一种类型。
2. **读消息**：发给 agent 的东西排进它的收件箱，送进它正在跑的会话；送不进去时由它自己来取。
3. **协作**：消息、任务委派和任务线程，以及共享看板（vault、notes、skills）。

非目标，v2 都不再做：
- 拉起、唤醒、停止 agent 进程；
- tmux、pty wrapper、截屏、模拟按键；
- 往 Telegram、webhook 或 dashboard 推"需要你处理"；
- 权限对话框转发。

## 1. 人在哪里

- **agent 由用户手动启动**：在工作区里复制 dashboard 给的启动命令（见 §9）。cc 跑在终端里，agy 跑在 T3 里。
- **每个项目有一个常驻 agy**，跑在 T3 里，是这个项目在 hub 上的"人类联络人"（`projects.liaison`）。
  - 凡是要找人的（review、问题、需要决定的事），hub 都投递到联络人的收件箱。
  - 联络人在 T3 对话里把事情摆给用户，用户在 T3 手机端看到、回复。
  - 联络人再把用户的答复写回 hub（`hub_update` answer 或 `hub_close`）。
- **`human` 仍是一个合法地址**：发给 `human` 等于发给发送方所在项目的联络人。项目没有联络人时，事情只留在 dashboard 上，不推送到任何地方。
- **dashboard 不再有"待你处理"列表**，只能看，外加关闭任务、复制启动命令。

```
cc (终端) ──hub_update(kind=review)──► hub ──inbox──► 常驻 agy (T3) ──► 用户手机
                                         ▲                   │
                                         └──hub_close/answer─┘
```

## 2. 删除清单

| 删除 | 现位置 | 说明 |
|---|---|---|
| hubd 整个守护进程 | `node/daemon.py`, `node/service.py` | 包括 wake、stop、stop_session、peek、keys、self_update |
| orders 指令队列 | `/v1/orders`, `/v1/nodes/orders`, `/v1/order`, `orders` 表 | 只有 hubd 用 |
| agent 进程控制 | `/v1/agents/*/wake`, `stop`, `peek`, `keys`, `agents.wake` 字段 | |
| pty wrapper | `node/run.py`, `node/procs.py`, `node/adapters/*` 的 pty 部分 | 由 adapters.md 的原生接口替代 |
| 通知 | `hub/notify/`（telegram 391 行、webhook）, `/v1/telegram`, `tg_refs` 表 | |
| 权限转发 | `/v1/permissions*`, `permissions` 表 | |
| events 动态流 | `/v1/events`, `events` 表 | 活动视图改为直接从 posts 生成 |
| messages、deliveries 两张表 | | 并入 posts 和 inbox（§3） |
| 节点管理 | `/v1/admin/nodes` 的 UI 部分 | 保留 node token 的签发，只走 CLI |

插件和 skill 的同步原来靠 hubd 的 `update_plugins` 做，改为 agent 启动时由 SessionStart hook（`agenthub cc-hook start`）从 hub 拉取。

## 3. 数据模型

```sql
-- 身份
nodes     (name PK, last_seen)                      -- 仅用于 token 鉴权与展示
agents    (name PK, kind NOT NULL,                  -- claude|agy|codex|pi|other，注册时必填，不再默认 claude
           node, workspace, description,
           created_at, last_seen)
scopes    (agent, project, role, PK(agent,project,role))
projects  (name PK, label, description, archived,
           liaison TEXT DEFAULT '')                 -- 该项目常驻 agy 的名字
sessions  (id PK, agent, node, cwd, kind,
           conversation,                            -- CLI 自己的会话 id，用于 resume 接管
           push TEXT,                               -- channel|bridge|pull（§5）
           state TEXT DEFAULT 'idle',               -- busy|idle，由 adapter 上报，hub 不推断
           token_hash, pid,
           started_at, last_seen, ended_at)

-- 协作：所有对话只有一张表
tasks     (id PK, project, parent_id, title, goal, context, acceptance,
           role, owner, created_by,
           state,                                   -- open|working|review|done|cancelled
           result, created_at, updated_at, closed_at)
posts     (id PK, task_id NULL,                     -- 有 task_id 的就是任务线程
           thread_id, reply_to,                     -- 直接消息的线程
           sender, recipient,                       -- recipient 可为 agent / role@project / human
           kind,                                    -- message|created|plan|progress|question|answer|result|comment|review|closed
           body, ts)
inbox     (id PK, agent, post_id,
           created_at, delivered_at, session)       -- 每个收件人一行；delivered_at 为空就是待投递

-- 共享看板（原样保留）
vault, vault_log, notes, note_log, skills(marketplace 文件), settings
```

和 v1 相比：
- messages、events、deliveries、task_log 四套通信记录合成 posts 和 inbox 两张表。
- 一条消息只存一次，收件箱只放指针。
- 任务线程和直接消息是同一种东西，所以 dashboard 和工具也只需要一种展示方式。

## 4. 身份与会话

- **节点鉴权**：节点身份照旧来自 node token 或 tailscale whois（`hub/identity.py` 原样保留）。
- **会话 token**：`register` 返回一个会话 token。之后 agent 的每个请求都带上它，hub 从 token 认出是哪个 agent。
  - 请求体里不再接受 `agent` 字段。这堵上了 v1 随便填个名字就能冒充的漏洞。
- **名字归属会话**：
  - 会话一退出就释放名字：MCP server 在 stdin 关闭时调 `POST /v1/sessions/<id>/end`，各 adapter 也一样。
  - 心跳 120 秒没更新也算结束，这是兜底。
  - v1 在会话退出后还要再等 2 分钟才能用回这个名字（2026-11-20 实际遇到过）。
- **resume 接管**：同一个 conversation 在另一个终端 resume 时，新的那份接管名字，旧的那份不再收消息。
  - 沿用 v2 `_bind` 的规则，但不再由 hubd 去关掉旧进程，只是不再给它投递。
  - 不同 conversation 抢同一个名字：返回 409，并告诉对方"名字被谁占着、在哪个节点、哪个目录"。
- **kind 必填**：注册时必须给出 kind，由插件或 adapter 自动填，hub 不再猜。

## 5. 收件箱与推送

投递只有一条路径：写 posts，再给每个收件人写一行 inbox。各会话按自己的 `push` 方式把消息取走：

| push | 谁 | 方式 |
|---|---|---|
| `channel`（进程内直推） | cc（带 `HUB_CHANNEL=1`）、pi | cc：MCP server 长轮询 `GET /v1/sessions/<id>/pull`，以 channel 通知送进会话。pi：扩展长轮询，用 `pi.sendUserMessage` 注入 |
| `bridge`（外部桥） | T3 里的 agy、codex | agy：`agenthub t3-bridge` 调 `t3 thread send`。codex：`agenthub codex-bridge` 调 app-server 的 `turn/start`。具体用哪个看 `sessions.kind` |
| `pull` | 其他所有 agent | agent 自己调 `hub_inbox` |

hook 不算一种 push 模式，它只是忙时的补充，比如 agy 的 PreInvocation、不带 channel 的 cc 的 UserPromptSubmit，用 `pull?wait=0` 顺手取件。

**租约，防止重复投递**：每次 pull 取走的条目都记下 `sent_at` 和 session，60 秒内不会再发给别的取件者。超过 60 秒还没回报 delivered，就重新发。这样 hook 和 bridge 同时取件也不会重复投递。

**本机会话文件**：MCP server 注册成功后写 `~/.agent-hub/sessions/<name>.json`（权限 0600，内容是 `session_id`、`token`、`cwd`、`kind`、`conversation`），会话结束调 `/end` 时删除。bridge 和 hook 都读这份文件，和 MCP server 共用同一个 hub 会话。

**T3 thread 必须确定性绑定**：hub 会话对应哪个 T3 对话，不能靠"最新一个 running thread"去猜，因为同一个项目里可能同时有联络人 agy 和其他 agy。优先用 agy 的 conversationId 和 T3 threadId 之间的确定映射；找不到映射，就在 register 时显式传入 threadId。这是阶段 1b 的验收项。

- **什么时候算送达**：adapter 把消息注入成功后回报，hub 就设上 `delivered_at`。
- **重投**：已经发出去但没回报送达的条目，下次 pull 时还会重新出现，不会丢。
- **忙闲**：
  - adapter 在每个 turn 开始和结束时上报 `POST /v1/sessions/<id>/state`，事件名以 adapters.md 为准。
  - dashboard 和 `hub_agents` 显示的忙闲以这个上报为准。
- **agy 的 Stop hook 不再长轮询等消息。** #115 实测发现这会让 T3 一直显示 running，而且有强制调用次数上限。agy 空闲时改走 `t3` 方式。
- **bridge 由用户手动启动**：`t3 thread send` 必须在 T3 所在的机器上执行，所以由 `agenthub t3-bridge` 来做（adapters.md §1.3 已确认可行）。
  - 每台跑 T3 的机器手动启动一个，dashboard 给可复制的命令。codex 同理，用 `agenthub codex-bridge`。
  - bridge 只替本机的会话长轮询 hub 并投递，不启动任何 agent，所以不是 hubd。

## 6. 任务

**状态**：v1 的状态收成 5 个：

| v1 | v2 |
|---|---|
| open、unclaimed | open |
| claimed、in_progress、blocked | working |
| review | review |
| done | done |
| cancelled、failed | cancelled |

"卡住了"不再算一个状态，用一条 `question` 帖子表示。

**流程**：

1. `hub_delegate`：建任务，`state=open`，写一条 `created` 帖子。
   - 指定了 owner 就投递给 owner。
   - 指定的是 role@project，就投递给这个项目里有该角色的在线 agent。
2. owner 发 `plan`，或者调 `hub_update` 认领任务，`state` 变成 `working`。
3. owner 发 `result`，`state` 变成 `review`，结果投递给任务的 created_by。
   - created_by 是 `human` 时，投递给项目联络人。
4. 创建者（或联络人代表用户）调 `hub_close(done|cancelled)`，或者发 `comment` 把任务打回去，`state` 回到 `working`。
5. 任何一方发 `question`、`answer`、`comment`，都投递给线程的另一方。
   - `question` 带上 `to=human` 时，投递给联络人。

## 7. agent 工具（MCP）

现在 19 个，合成 9 个：

| v2 工具 | 作用 | 吸收的 v1 工具 |
|---|---|---|
| `hub_register(name, project?, role?, description?)` | 注册或接管名字 | `hub_register`, `hub_scope`, `hub_whoami`（不带参数调用时返回当前身份） |
| `hub_agents(project?)` | 谁在、在哪、忙闲、手上的任务 | `hub_agents` |
| `hub_send(to, body, reply_to?)` | 直接消息 | `hub_send`, `hub_post` |
| `hub_delegate(to, title, goal, context, acceptance, parent?)` | 派任务 | `hub_delegate`, `hub_task`（创建部分） |
| `hub_update(task_id, kind, body, to?)` | 在任务线程里发言，按 kind 改状态 | `hub_update`, `hub_claim` |
| `hub_close(task_id, state, note?)` | 结束任务 | `hub_close` |
| `hub_tasks(task_id? \| filters)` | 看一个任务的全文，或者列任务 | `hub_task`, `hub_tasks`, `hub_search` |
| `hub_inbox()` | 取待投递的条目（`pull` 模式） | `hub_inbox` |
| `hub_board(action, type, name?, ...)` | 共享看板：secret、note、skill 的列出、读取、写入 | `hub_vault`, `hub_note`, `hub_shared`, `hub_skills`, `hub_skill_publish` |

- **同一套工具，多种暴露方式**：
  - cc、agy、codex 走 MCP（`node/mcp.py`）；
  - pi 走它的扩展 API（adapters.md）；
  - 其他 agent 走命令行 `agenthub <tool>`。
- **密钥不进对话**：`hub_board` 读取 secret 时只返回 env 形式，值不会写进 posts。

## 8. HTTP API（约 20 个路由）

```
GET  /healthz
POST /v1/sessions                    register：建会话，返回 token
POST /v1/sessions/<id>/end
POST /v1/sessions/<id>/state         busy|idle
GET  /v1/sessions/<id>/pull          取收件箱：?wait=秒数，默认 50 秒；wait=0 时立即返回。取走的条目带 60 秒租约
POST /v1/sessions/<id>/delivered     回报送达
GET  /v1/agents          GET /v1/agents/<name>     DELETE /v1/agents/<name>（只限管理员）
GET  /v1/projects        POST /v1/projects/<name>  （label、liaison、archived）
POST /v1/posts                       send：直接消息
POST /v1/tasks           GET /v1/tasks   GET /v1/tasks/<id>   POST /v1/tasks/<id>   （update、close）
GET  /v1/board           GET/POST /v1/board/<type>/<name>
GET  /v1/skills/export               供 SessionStart 拉取
GET  /v1/stream                      dashboard 用的 SSE（替代轮询）
```

现在的 `api.py` 有 969 行、50 多个路由，目标是不超过 450 行。

## 9. 启动命令

`GET /v1/agents/<name>` 返回一个 `start` 字段，dashboard 上一键复制。各类型的命令模板如下：

- **cc**：
  ```bash
  cd <workspace> && HUB_CHANNEL=1 HUB_AGENT=<name> claude --dangerously-load-development-channels plugin:agent-hub@agent-hub [--resume <conversation>]
  ```
  有上次的 conversation 就带上 `--resume`。不带 `--permission-mode`，这一项由用户自己决定。
- **agy（T3）**：在 T3 里打开 `<workspace>` 开新对话。
  - 工作区里有 `.agents/hooks.json`，hub 的 MCP 配置写在 T3 的 agy 配置目录里。
  - 首次使用时，`agenthub setup agy <workspace>` 会生成这两份配置。
  - 第一句话：`你叫 <name>，项目 <project>`。
  - 具体以 adapters.md 为准。
- **t3-bridge**（每台跑 T3 的机器一条）：`agenthub t3-bridge`。
- **codex、pi**：以 adapters.md 为准。

## 10. Dashboard

三个页面，手机优先，深浅色主题，用 SSE 实时更新：

1. **Agents**：
   - 按项目分组，每个 agent 一张卡片：在线状态、忙闲、类型、节点和工作区、当前任务、复制启动命令。
   - 项目的联络人带标记。
2. **Tasks**：
   - 按状态分栏；
   - 点进去是一条完整的线程（posts 时间线，`question` 和 `result` 高亮），可以关闭任务或者打回。
3. **Board**：
   - secret 只显示名字和用途；
   - note 渲染 Markdown；
   - skill 列出可安装的。

实现方式：
- 不需要构建步骤。
- 把 Preact 和 htm 打成一个文件 vendored 进仓库，约 15 KB，依然零依赖。
- 视觉上用系统字体，信息密度适中，卡片和时间线布局。
- 目标 JS 不超过 500 行，CSS 不超过 250 行。现在是 `app.js` 1071 行加 HTML/CSS 600 多行。

## 11. 从 v1 迁移

**工具**：`agenthub migrate --from <v1 hub.db> --to <v2 hub.db>`。

- 以只读方式打开 v1：`file:...?mode=ro`。
- 先拷一份快照再读，不直接读线上库。

**映射**：

| v1 | v2 |
|---|---|
| agents | agents，kind 按 v1 记录填，没有记录的标为 `claude` 并打印出来让人确认；丢弃 `wake` 字段 |
| agent_aliases | 解析后并入 agents |
| scopes、projects | 原样迁移；`liaison` 为空，由用户来指定 |
| tasks | 按 §6 映射状态 |
| task_log | 有 task_id 的 posts |
| messages | 没有 task_id 的 posts |
| vault、notes 及其 log、skills | 原样迁移 |
| 未送达的 deliveries | inbox，只迁最近 7 天的 |
| events（hub_post 的团队动态） | 没有 task_id 的 posts，`kind=post`，recipient 为 `*@<project>` |
| orders、permissions、tg_refs、sessions、settings 里的 tg_* | 不迁 |

**v1 表结构**：与 v2 的 `store.py` 基本一致。v1 后来加的列有 `agents.hidden`、`position`、`conversation`、`title`，`sessions.conversation`、`title`，`projects.position`，`vault.tags`、`env`，`orders.acked_at`。迁移时保留 `hidden`（v2 里叫归档）和 `conversation`（只保留真实的会话 id），`vault.env` 必须迁。v1 几乎把所有 agent 都记成了 `claude`，迁移时会输出一份类型待确认清单。

**校验**：
- 迁移后打印每张表 v1 与 v2 的条数，一一对照。
- 抽查 #115 的线程是否完整。

**切换**：
1. v2 换一个端口和 v1 并行跑，先让少数 agent 迁过去用。
2. 切换当天：停掉 v1，拍最终快照，`migrate --force` 重新迁移，再把各节点的 hub 地址和插件市场切到 v2，停掉 v1 的 hubd。v1 的数据保留一段时间作为回退。

## 12. 分阶段与验收

| 阶段 | 内容 | 谁 | 验收 |
|---|---|---|---|
| 0 | 本文和 adapters.md 两份设计，用户过目 | sa-cc、sa-agy | 用户确认 |
| 1a | hub 核心：schema、会话 token、名字随会话释放、posts 加 inbox、任务、API（§3–§8），以及删除清单里的内容 | sa-cc | 测试覆盖：冒名、resume 接管、退出释放名字、重投、状态流转 |
| 1b | adapter：agy（hook 加 t3-bridge）、codex、pi；工具（§7）的各种暴露方式 | sa-agy | 每种 agent 端到端：收推送、派任务、交结果、找联络人 |
| 1c | 迁移脚本 | sa-cc | 在 v1 快照上跑通，条数对得上 |
| 2 | dashboard | sa-cc 写，sa-agy review | 手机上可用，三个页面 |
| 3 | 并行跑和切换 | 用户点头 | §11 |

**代码量目标**：从现在的约 8500 行降到 4500 行以内。

## 13. 待定

暂无。

已定（2026-11-20）：
- **`t3 thread send` 由谁执行**：由 `agenthub t3-bridge` 执行；push 只有 channel、bridge、pull 三种；会话文件、`pull?wait=0` 和租约的定法见 §5（sa-agy #115 review）。
- **联络人命名**：统一叫 `<project>-agy`，例如 `agent-hub-agy`。已写进 agent-hub skill。
- **联络人不在线时**：只排队，等它上线后再投递。dashboard 不另外标注，任务处于 review 状态本身就能看出来。
