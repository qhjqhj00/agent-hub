# Agent Adapters 调研与设计文档（agy / codex / pi）

> 对应任务：#115 · 配套设计：`docs/refactor/core.md`（sa-cc，§3–§9）
> 调研环境：Linux aarch64 开发机，实测日期：2026-11-20
> 设计前提（与 `core.md` 对齐）：**无 `hubd`、无 tmux 自动唤醒、无 PTY wrapper 截屏敲字**。所有 agent 由用户在 workspace 手动启动，hub 只负责把消息结构化地投递进已经在运行的会话；每个项目在 T3 中常驻一个 `agy` 作为人类联络人（`projects.liaison`），空闲时也必须能收到推送。

---

## 0. 核心结论速览

1. **三者全部可以淘汰 PTY 截屏敲字，且都能做到“结构化推送 + 人类实时观察/插话”共享同一会话**：
   - **`agy`（1.5.13）+ T3 Code（0.1.10）——联络人第一优先级**：
     - **废弃 `Stop` hook 1500s 长轮询**（实测会导致 T3 持续显示 `running`、阻塞用户打字，且受限于单 turn 内的 `MAX_FORCED_INVOCATIONS`）。
     - **主推组合**：忙时用 **0 秒阻塞的 `PreInvocation` hook** 把新消息注入当前轮次；空闲时用 **`agenthub t3-bridge`**（每台跑 T3 的机器手动起一个，不拉起任何 agent 进程）长轮询 hub 并执行 **`t3 thread send <threadId> <msg>`**。空闲即开新 turn（无 UI 锁定、重置 forced invocation 计数），忙时 T3 自动排队不打断。
   - **`codex`（0.154.0）**：推荐 **`codex app-server`（共享本地 daemon / WS / UDS）+ `codex --remote` TUI + MCP**。实测同一 `app-server` 允许多个客户端并发连接并操作同一 `threadId`，人类用 TUI 实时看/插话，轻量 `agenthub codex-bridge` 用 JSON-RPC `turn/start` / `turn/steer` 推送并监听 `thread/status/changed` 上报 `busy`/`idle`。
   - **`pi`（0.74.2）**：最优雅。官方 TypeScript **Extension API** 允许在交互式 `pi` TUI 进程内直接注册与 MCP 同源的 **9 个 `hub_*` 工具**（`pi.registerTool`，彻底告别旧版 CLI 退化路径）、后台长轮询 hub 并用 `pi.sendUserMessage()` / `pi.sendMessage({ triggerTurn: true })` 直接推消息进当前 TUI 会话、在 `agent_start` / `agent_end` 上报 `busy`/`idle`。**零外部 bridge，人机 100% 共享同一 TUI 会话。**

---

## 1. `agy`（Antigravity CLI + T3 Code）

### 1.1 版本信息
- `agy`: **`1.5.13`**（`~/.local/bin/agy`）
- `t3`: **`0.1.10`**（`t3 serve` 监听 `0.0.0.0:3773`，底层通过 `agy acp` 驱动 Antigravity 会话）

### 1.2 可用结构化接口与实测结论

| 接口 | 实测结果 | 关键细节与坑 |
|---|---|---|
| **MCP (`agy mcp add`)** | **实测可用** | 标准 stdio/http MCP。T3 模式下配置在 `~/.t3/userdata/providers/antigravity/<hash>/config/mcp_config.json`；独立 CLI 在 `~/.gemini/config/mcp_config.json`。暴露 `core.md` §7 定义的 9 个 `hub_*` 工具。 |
| **Lifecycle Hooks (`.agents/hooks.json`)** | **实测可用（`Stop` 有硬伤）** | 支持 `SessionStart`、`PreToolUse`、`PostToolUse`、`PreInvocation`、`PostInvocation`、`Stop`；stdin/stdout 均为 camelCase JSON。**仅支持同步 `type: "command"`，会阻塞 agent loop。** |
| **T3 CLI (`t3 thread list/show/send`)** | **实测可用（T3 场景最佳）** | 本机 `t3` CLI 直接连接 `:3773` 的运行中 `t3 serve`：`t3 thread list --project <dir> --json` 列 thread；`t3 thread show --json <id>` 读 `thread.status`（`idle`/`running` 等）和 `activeRunId`；`t3 thread send <id> <msg>` 推用户消息（空闲立即开新 turn，忙时自动排队不打断）。 |
| **`stream-json` 双向管道** | **实测通过** | 启动命令：`agy --input-format stream-json --output-format stream-json -p ""`。**坑 1**：`-p` 会吃掉紧跟其后的参数，写成 `agy -p --input-format ...` 会报错；必须把 `-p ""` 放最后。**坑 2**：输入 JSON 必须是 `{"event":"user","message":{"content":"..."}}`（写 `text` 报 `has no content`）。实测连续 2 轮成功，事件：`init`（含 `conversation_id`）→ `step_update` → `result`（含 `status`/`num_turns`/`response`）。 |
| **`agy acp`（Agent Client Protocol）** | **实测通过** | stdio JSON-RPC 2.0，实测 `initialize` 返回 `protocolVersion: 1`，`sessionCapabilities` 含 `resume`/`close`/`load`/`list`/`additionalDirectories`。这正是 T3 内部驱动 `agy` 的协议。 |
| **`--conversation <id>` / `-c`** | **实测通过** | 实测用 `stream-json` 拿到的 `conversation_id` 执行 `agy --conversation <cid> --output-format json -p "..."`，成功恢复上下文（`num_turns: 3`，准确回忆第 1 轮内容）。 |
| **`agy remote-control`** | 未启用（本机 `inactive`） | 远程守护进程（`antigravity-cli-daemon.service`），不是本地多客户端共享会话总线。 |

> 补充实测坑：print 模式（`-p` / `stream-json`）默认权限策略宽松（`always-proceed`），无头自动化调用务必显式传 `--mode auto` 或 `--sandbox`。

### 1.3 Hooks 原型实测结论 & `agenthub t3-bridge` vs 远端 T3 API 评估

#### A. 为什么废弃 `Stop` hook 长轮询
1. **阻塞 T3 UI，持续显示“运行中”并挡人打字**：`agy` 官方 hooks 文档明确写明 *"Hooks run synchronously and block the agent loop (no async execution)"*。我们在 scratch 目录实测：LLM 仅用 **6.88s** 完成回复，但因 `Stop` hook 仍在 sleep 轮询，`agy` 判定 turn 仍在进行中（`print timeout ... with the turn still in progress`）。在 T3 中，`t3 thread show` 表现为 `status: "running"`、`activeRunId` 非空——Web/手机端持续转圈，用户无法自然回复（新消息被排队或被迫点 Stop，触发 `USER_CANCELED` 反而断掉监听）。
2. **`MAX_FORCED_INVOCATIONS` 上限**：`Stop` 返回 `decision: "continue"` 始终停留在**同一个 turn 内**递增 `executionNum`（我们在隔离仓库实测连续 continue 10 次，`executionNum` 从 `0` 增至 `9`，`num_turns` 始终为 `1`）。内核 `CascadeExecutorConfig.max_forced_invocations` 触顶后会以 `terminationReason: "MAX_FORCED_INVOCATIONS"` 强停，必须有真实用户消息开新 turn 才能重置。

#### B. 评估：`agenthub t3-bridge`（本机 CLI）vs Hub 远端直连 T3 API（`:3773`）
我们对 `t3 serve`（`:3773`）和 `t3 auth session` 做了实测与排查：
- `t3 serve` 支持 `--lan` / `--tailscale-serve`，`t3 auth session issue` 可签发 scoped bearer token，但：
  1. HTTP `:3773` 根路径返回 SPA 前端，核心交互走 T3 内部 WebSocket/RPC 协议，**无公开稳定的 REST 发送端点**；
  2. `t3 thread send` CLI 仅支持本地 `--base-dir`（`T3CODE_HOME`），不支持传远端 URL；
  3. 若让 hub 逆向直连 T3 内部 WS，hub 需要保存各节点的 T3 bearer token，且 T3 升级随时可能破坏内部 WS 协议。
- **结论：强烈推荐 `sa-cc` 在 `core.md` §5 提出的 `agenthub t3-bridge` 方案，完全可行且最优。**
  - 每台跑 T3 的机器随 `t3 serve` 手动启动一个 `agenthub t3-bridge`（约 150 行 Python，**不拉起任何 agent 进程，不是 `hubd`**）。
  - 它调用 T3 官方稳定的 `t3 thread list/show/send --json` CLI，无需额外 token 配置；一台机器上无论开多少个项目的常驻联络人 `agy`，共用同一个 `t3-bridge` 进程。
  - **忙/闲分工非常干净**：
    - **忙时**：`.agents/hooks.json` 的 **`PreInvocation`** hook（0 秒阻塞，`GET /v1/sessions/<id>/pull?wait=0`）顺手把新消息以 `systemMessage` 注入当前轮次，成功后 `POST /v1/sessions/<id>/delivered`；
    - **空闲时**：`t3-bridge` 长轮询 `GET /v1/sessions/<id>/pull?wait=50`，拿到条目后执行 `t3 thread send <threadId> <text>`，命令返回码为 0 后立刻 `POST /v1/sessions/<id>/delivered`。T3 空闲时直接开全新 turn（重置 `executionNum`，手机端立刻收到推送并可随时回复），忙时 T3 自动排队不打断。
  - **双进程共享 session token 与 `threadId` 绑定**（衔接 `core.md` §4）：
    - 当 `agy` 首次调用 `hub_register` 时，本机 MCP server 拿到 hub 返回的 `session_id` 和 `token`，写入权限 0600 的本地状态文件 `~/.agent-hub/sessions/<agent>.json`（含 `session_id`, `token`, `cwd`, `kind="agy"`, `t3_thread_id`；`t3_thread_id` 可通过 `t3 thread list --project <cwd> --json` 找当前 `status=="running"` 的最新 antigravity thread 自动获取）。
    - `PreInvocation` hook 和 `t3-bridge` 都从 `~/.agent-hub/sessions/<agent>.json` 读同一个 `session_id` + `token`（退出时 MCP server 调 `/end` 并清理该文件）。

### 1.4 推荐接入方式总结（`agy`）
- **Push**：T3 场景 = **`t3-bridge`（`t3 thread send`，空闲/排队）+ 无阻塞 `PreInvocation` hook（忙时插队）**；无头场景 = `stream-json` / `acp`。
- **Tools**：原生 MCP（9 个 `hub_*` 工具，由 `agenthub setup agy` 写入 T3 的 `mcp_config.json`）。
- **Resume**：T3 中天然持久化在 `threadId` 中（手机/Web 随时续聊）；独立 CLI 用 `agy --conversation <cid>`。
- **忙闲上报触发点（`POST /v1/sessions/<id>/state`）**：
  - **方案 1（hooks 精确驱动，零轮询延迟）**：
    - `PreInvocation`（当 `invocationNum == 0` 或状态非 `busy` 时）→ 上报 `{"state": "busy"}`；
    - `Stop`（**0 秒阻塞，不再轮询**，立刻返回 `{}`）→ 上报 `{"state": "idle"}`！
  - **方案 2（`t3-bridge` 兜底校对）**：`t3-bridge` 在调用 `t3 thread send` 前后或定期 `t3 thread show --json <threadId>` 读取 `thread.status`（`"running"` → `busy`，`"idle"` / `"completed"` → `idle`），防止进程异常退出导致状态卡在 `busy`。
  - *独立 `stream-json` 模式*：`step_update.step_type == "user_input"` → `busy`；`event == "result"` → `idle`。
- **人如何观察和插话**：
  - **T3 模式**：**能共享同一会话**（Web/Desktop/手机端实时看、随时回复或点 Stop，与联络人定位完美匹配）。
  - **独立 CLI `stream-json`/`acp` 模式**：**不能共享同一会话**，只能退出后用 `agy --conversation <cid>` 交替接管。

### 1.5 可复制的启动命令与首次接入提示词（`agy`）

1. **首次配置工作区与 T3 MCP（每个 workspace 一次）**：
   ```bash
   agenthub setup agy /path/to/workspace
   ```
2. **每台跑 T3 的机器启动一次 bridge（常驻终端或 screen/systemd user）**：
   ```bash
   agenthub t3-bridge
   ```
3. **在 T3 Web/Desktop 中打开 `<workspace>` 新建 Antigravity 对话（或终端执行）**：
   ```bash
   t3 thread start /path/to/workspace --title "<name>" "你是 <name>，负责 <project> 项目的 <role>（如项目联络人）。请先调用 hub_register(name=\"<name>\", project=\"<project>\", role=\"<role>\") 注册身份并查看 hub_inbox。"
   ```
4. **首次接入时发给它的那段话**（在 T3 界面直接新建对话时粘贴）：
   > 你是 `<name>`，负责 `<project>` 项目的 `<role>`。请先调用 `hub_register(name="<name>", project="<project>", role="<role>")` 注册身份并检查 `hub_inbox`。后续 agent-hub 的消息和任务会直接推入本对话；请统一用 `hub_update` / `hub_send` / `hub_close` 回复 hub，需要用户拍板的事情在本对话里直接向用户汇报。

---

## 2. `codex`（OpenAI Codex CLI）

### 2.1 版本信息
- `codex`: **`codex-cli 0.154.0`**（`~/.local/bin/codex`）

### 2.2 可用结构化接口与实测结论

| 接口 | 实测结果 | 关键细节与坑 |
|---|---|---|
| **`codex app-server`（JSON-RPC 2.0）** | **实测通过（核心推荐）** | 支持 `--listen stdio://`、`ws://IP:PORT`、`unix://PATH`（注意 Linux `SUN_LEN` 限制，socket 路径须短于 108 字节；`codex app-server daemon` 默认控制套接字在 `~/.codex/app-server-control/app-server-control.sock`）。内置 `generate-json-schema` 导出 99 个 ClientRequest、81 个 ServerNotification、10 个 ServerRequest。 |
| **多客户端共享同一线程** | **实测通过** | 启动 `codex app-server --listen ws://127.0.0.1:<port>` 后，客户端 C2（模拟 TUI）调用 `thread/start` 并跑第 1 轮使 rollout 落盘；随后客户端 C1（模拟 hub bridge）连同一 WS，调 `thread/loaded/list` 可见该 `threadId`，调 `thread/resume` 成功（`ok: true`），并可直接发 `turn/start`。**注意坑**：刚 `thread/start` 尚未跑第 1 轮前，磁盘无 rollout 文件，跨客户端 `thread/resume` 会报 `-32600 no rollout found for thread id`；用户发完首条注册消息后即可正常共享。 |
| **`codex --remote <ADDR>` / `codex agents`** | **原生支持** | `codex --remote unix://...` 或 `ws://...` 让交互式 TUI 挂到共享 `app-server`；`codex agents` 可浏览该 `app-server` 上所有会话并随时进入接管。 |
| **`codex queue --thread <id> --message <text>`** | **实测通过（仅入队）** | 实测写入 `~/.codex/queue_1.sqlite`（`queued_items` 表）并返回成功。**关键结论**：它只写持久队列，**不会**主动唤醒空闲无头线程立即开 turn；立即执行仍需 `app-server` 的 `turn/start`（空闲时）或 `turn/steer`（忙时）。适合作为断线兜底队列。 |
| **`codex exec --json` & `exec resume --json`** | **实测通过** | 单轮非交互 NDJSON：`thread.started`（含 `thread_id`）→ `turn.started` → `item.completed` → `turn.completed`。`exec resume --json <tid>` 实测恢复上下文成功。缺点：无法与实时 TUI 共享，不适合主交互方案。 |
| **`codex mcp add`** | **原生支持** | 标准 stdio MCP，暴露 `core.md` §7 的 9 个 `hub_*` 工具。 |
| **`codex` Hooks（`features.hooks`）** | **已稳定（`stable: true`）** | `HooksListResponse.json` 确认支持 12 个事件：`sessionStart`、`sessionEnd`、`userPromptSubmit`、`preToolUse`、`permissionRequest`、`postToolUse`、`preCompact`、`postCompact`、`subagentStart`、`subagentStop`、`stop`、`interrupt`。可作降级备用。 |

> 补充实测坑：Linux 上 `codex app-server` 启动会在 stderr 打一条 bubblewrap `configWarning`，不影响 JSON-RPC；本地连 `ws://127.0.0.1` 若环境配了代理需设 `NO_PROXY=127.0.0.1,localhost`。

### 2.3 推荐接入方式总结（`codex`）
- **Push**：**`agenthub codex-bridge`**（手动启动，不拉起任何 codex 进程），连到本地共享 `codex app-server`（UDS/WS）：
  - 长轮询 `GET /v1/sessions/<id>/pull?wait=50`；
  - 线程处于 `idle`：调 JSON-RPC `turn/start`（`{"threadId": tid, "input": [{"type": "text", "text": "..."}]}`）；
  - 线程处于 `busy`：普通消息等 `thread/status/changed` 变回 `idle` 后调 `turn/start`（或紧急消息调 `turn/steer` 带当前 `expectedTurnId`）；
  - 收到 `turn/start` / `turn/steer` 成功响应后立刻 `POST /v1/sessions/<id>/delivered`。
- **Tools**：原生 MCP（`codex mcp add agent-hub -- agenthub mcp --kind codex`，暴露 9 个 `hub_*` 工具）。
- **Resume**：`codex --remote <addr> resume <uuid>`（或 `codex agents` 选择），bridge 自动 `thread/resume` 绑定。
- **忙闲上报触发点（`POST /v1/sessions/<id>/state`）**：
  - 监听 `app-server` 通知 **`thread/status/changed`**：
    - `params.status.type == "active"` → 上报 `{"state": "busy"}`（`params.status.activeFlags` 含 `waitingOnApproval`/`waitingOnUserInput` 时可记录日志或作为扩展 detail）；
    - `params.status.type == "idle"` → 上报 `{"state": "idle"}`。
  - 辅助事件：`turn/started`（记录 `turn.id` 供 `turn/steer` 的 `expectedTurnId` 使用）、`turn/completed`。
- **人如何观察和插话**：
  - **`app-server` + `codex --remote` 模式**：**能共享同一会话**。人在终端用 `codex --remote` 实时看全部输出、处理审批弹窗、随时打字或 `Esc` 打断；hub 通过 `codex-bridge` 推入的 turn 会实时同步渲染在同一 TUI 中。
  - **独立 `codex exec --json` 模式**：**只能交替 `codex resume <uuid>`**，不能共享。

### 2.4 可复制的启动命令与首次接入提示词（`codex`）

1. **每台机器启动一次共享 `app-server` 与 `codex-bridge`（手动启动，不拉起 agent）**：
   ```bash
   codex app-server daemon start && agenthub codex-bridge
   ```
2. **用户在 workspace 手动启动交互式 Codex TUI**（`GET /v1/agents/<name>` 返回的 `start` 模板）：
   ```bash
   cd <workspace> && codex --remote unix://$HOME/.codex/app-server-control/app-server-control.sock [resume <conversation>]
   ```
   （审批与沙箱策略如 `--ask-for-approval on-request --sandbox workspace-write` 由用户按需自选，与 `core.md` §9 的 cc 原则一致。）
3. **首次接入时发给它的那段话**：
   > 你是 `<name>`，负责 `<project>` 项目的 `<role>`。请先调用 `hub_register(name="<name>", project="<project>", role="<role>")` 注册身份并查看 `hub_inbox`。后续 agent-hub 的消息会直接推入本会话，请统一用 `hub_update` / `hub_send` / `hub_close` 回复 hub。

---

## 3. `pi`（pi coding agent）

### 3.1 版本信息
- `pi`: **`0.74.2`**（`~/.local/bin/pi` → `@earendil-works/pi-coding-agent`）

### 3.2 可用结构化接口与实测结论

| 接口 | 实测结果 | 关键细节与坑 |
|---|---|---|
| **TypeScript Extension API (`~/.pi/agent/extensions/*.ts`)** | **实测通过（强烈推荐）** | 通过 `jiti` 直接加载 `.ts` 无需编译。单文件扩展即可做到：① `pi.registerTool()` 注册与 MCP 完全一致的 **9 个 `hub_*` 工具**（彻底替代旧版 CLI 退化路径）；② 扩展内用 Node 22 原生 `fetch` 长轮询 `GET /v1/sessions/<id>/pull`，收到后调 `pi.sendUserMessage()` 或 `pi.sendMessage({ customType: "agent-hub", content, display: true }, { triggerTurn: true, deliverAs: "followUp" })` 直接注入实时 TUI，成功后调 `POST /v1/sessions/<id>/delivered`；③ 监听 `session_start`/`agent_start`/`agent_end`/`session_shutdown`；④ `ctx.ui.setStatus("agent-hub", ...)` 在 TUI 页脚展示在线状态。 |
| **Extension 后台定时器注入 `sendUserMessage`** | **实测通过** | 实测 `agent_end` 后 300ms 从 `setTimeout` 调用 `pi.sendUserMessage(...)`，`pi` 立即触发第 2 轮 `agent_start` → `agent_end`。**坑**：忙时（streaming）调 `sendUserMessage` 必须带 `{ deliverAs: "followUp" \| "steer" }`，否则抛异常；空闲时不带或带 `followUp` 均立即触发新 turn。 |
| **`pi --mode rpc`（无头 JSONL）** | **实测通过** | stdin/stdout 严格 `\n` 分隔（不能用 Node `readline`，会误切 `U+2028`/`U+2029`）。命令：`prompt`、`steer`、`follow_up`、`abort`、`get_state`（含 `isStreaming`/`sessionFile`/`sessionId`）。事件：`agent_start`/`turn_start`/`tool_execution_start/end`/`turn_end`/`agent_end`。 |
| **`pi --session <path\|id>` / `-c`** | **接口验证通过** | 树状 JSONL 会话存于 `~/.pi/agent/sessions/`；扩展在 `session_start`（`reason: "startup" \| "new" \| "resume" \| "fork"`）中自动把 `ctx.sessionManager.getSessionId()` / `getSessionFile()` 同步给 hub。 |

> **实测环境说明**：实测机上配置的模型 provider 当时都不可用，所以没有跑真实模型的计费 tool-call；扩展加载、`pi.registerTool()`、`session_start`/`agent_start`/`agent_end` 事件、后台定时器 `pi.sendUserMessage()` 触发新 turn 以及 RPC 模式均已实测通过。

### 3.3 推荐接入方式总结（`pi`）
- **Push**：**进程内 Extension 直推**（`~/.pi/agent/extensions/agent-hub.ts`，**无需外部 bridge**）。
  - 空闲：`pi.sendUserMessage(rendered)`（立即开新 turn）；
  - 忙时：`pi.sendUserMessage(rendered, { deliverAs: "followUp" })`（等当前工具链结束后处理；紧急消息可用 `deliverAs: "steer"`）；
  - 注入返回后立即调 `POST /v1/sessions/<id>/delivered`。
- **Tools**：**原生 Extension 注册同套 9 个工具**（`hub_register`、`hub_agents`、`hub_send`、`hub_delegate`、`hub_update`、`hub_close`、`hub_tasks`、`hub_inbox`、`hub_board`），全部通过 Node `fetch` 直连 hub HTTP API，与 `core.md` §7 100% 对齐。
- **Resume**：`pi --session <conversation>` 或 `pi -c`；`/resume` 切换会话时扩展在 `session_shutdown` 停旧轮询、在 `session_start` 用新 `sessionId` 重绑。
- **忙闲上报触发点（`POST /v1/sessions/<id>/state`）**：
  - `pi.on("agent_start", ...)` → 上报 `{"state": "busy"}`；
  - `pi.on("agent_end", ...)` → 上报 `{"state": "idle"}`（**实测注意**：`agent_end` 回调执行瞬间 `ctx.isIdle()` 可能仍为 `false`，必须以 `agent_end` 事件本身作为 `idle` 触发点，不要在回调内查 `ctx.isIdle()`）；
  - `pi.on("session_shutdown", ...)` → 调 `POST /v1/sessions/<id>/end` 立即释放名字并 `abort()` 后台长轮询。
- **人如何观察和插话**：
  - **Extension + TUI 模式**：**能共享同一会话（三者体验最佳）**。用户直接跑原生 `pi` TUI，随时看、打字、`/tree`、`Ctrl+C`、`--reload`，无需任何外置 bridge。
  - **`pi --mode rpc` 无头模式**：**只能交替 `--session <file>`**，不能多进程同写一个会话文件。

### 3.4 可复制的启动命令与首次接入提示词（`pi`）

1. **安装扩展（每台机器一次）**：
   ```bash
   agenthub setup pi
   # 将 agent-hub.ts 写入 ~/.pi/agent/extensions/agent-hub.ts
   ```
2. **用户在 workspace 手动启动交互式 `pi`**（`GET /v1/agents/<name>` 返回的 `start` 模板）：
   ```bash
   cd <workspace> && HUB_AGENT=<name> pi [--session <conversation>]
   ```
3. **首次接入时发给它的那段话**：
   > 你是 `<name>`，负责 `<project>` 项目的 `<role>`。请先调用 `hub_register(name="<name>", project="<project>", role="<role>")` 注册身份并查看 `hub_inbox`。后续 agent-hub 的消息会通过扩展自动推入本会话，请统一调用 `hub_update` / `hub_send` / `hub_close` 回复 hub。

---

## 4. 三者对比表（与 `core.md` §5/§7/§9 对齐）

| 维度 | `agy` (1.5.13) + T3 (0.1.10) | `codex` (0.154.0) | `pi` (0.74.2) | *(参考)* `claude` |
|---|---|---|---|---|
| **Push 机制** | 空闲：`t3-bridge` 调 `t3 thread send`<br>忙时：无阻塞 `PreInvocation` hook | `codex-bridge` 连 `codex app-server` 调 `turn/start`（可选 `turn/steer`） | Extension 进程内直推（`pi.sendUserMessage`） | MCP `claude/channel` |
| **对应 `sessions.push`** | `t3`（辅以 `hook`） | `codex_rpc`（或归入 `bridge`） | `channel`（或 `pi_ext`，均为进程内直推） | `channel` |
| **是否需要外部 bridge** | 是（每台 T3 机器一个 `agenthub t3-bridge`） | 是（每台机器一个 `agenthub codex-bridge`） | **否**（扩展进程内完成） | **否**（MCP 进程内完成） |
| **9 个 `hub_*` 工具接入** | MCP (`mcp_config.json`) | MCP (`codex mcp add`) | **Extension `pi.registerTool`** | MCP (`agent-hub` plugin) |
| **Resume 方式** | T3 原生 `threadId` 持久；CLI `--conversation <cid>` | `codex --remote ... resume <uuid>` | `pi --session <id\|file>` / `-c` | `claude --resume <sid>` |
| **忙闲上报事件（→ `POST /state`）** | `PreInvocation` → `busy`<br>`Stop`（0s 无阻塞）→ `idle`<br>（`t3 thread show` 兜底校对） | `thread/status/changed`：`status.type == "active"` → `busy`，`"idle"` → `idle` | `agent_start` → `busy`<br>`agent_end` → `idle`<br>`session_shutdown` → `/end` | `claude/channel` / hooks |
| **送达确认（→ `POST /delivered`）** | `PreInvocation` 注入后 / `t3 thread send` exit 0 后 | `turn/start` 返回 JSON-RPC ok 后 | `pi.sendUserMessage()` 返回后 | channel 通知发出后 |
| **人能否观察和插话** | **T3：能共享同一会话（支持手机端）**<br>独立 `stream-json`/`acp`：不能（只能交替 `--conversation`） | **`app-server` + `--remote`：能共享同一会话**<br>`exec --json`：不能（只能交替 `resume`） | **Extension + TUI：能共享同一会话**<br>`--mode rpc`：不能（只能交替 `--session`） | **能共享同一会话** |
| **用户启动命令** | T3 打开 `<workspace>` 新建对话（机器起一次 `agenthub t3-bridge`） | `cd <ws> && codex --remote unix://~/.codex/app-server-control/app-server-control.sock [resume <cid>]` | `cd <ws> && HUB_AGENT=<name> pi [--session <cid>]` | `cd <ws> && HUB_CHANNEL=1 HUB_AGENT=<name> claude ... [--resume <cid>]` |

---

## 5. 统一 Adapter 接口草案（Python，对齐 `core.md` §8 HTTP API）

去掉 `hubd` 和 PTY wrapper 后，旧版 `base.py` 中的 `ready_markers`、`busy_markers`、`blockers`、`dialogs` 全部删除。新版 adapter 层只包含三部分：

1. **静态 Adapter（`AgentAdapter`）**：安装配置（`setup`）、生成 dashboard 一键复制的启动命令与首句提示词（`launch_guide`）。
2. **Hub 会话客户端（`HubSessionClient`）**：严格对应 `core.md` §8 的 5 个会话端点。
3. **外部桥接器（`SessionBridge`）**：仅供需要外部控制面的 `t3-bridge`（`agy`）与 `codex-bridge`（`codex`）实现；`claude` 与 `pi` 属于进程内直推，不需要 `SessionBridge`。

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

StateValue = Literal["busy", "idle"]
PushKind = Literal["channel", "hook", "t3", "codex_rpc", "pull"]


@dataclass
class InboxItem:
    id: int                  # inbox.id
    post_id: int
    task_id: int | None
    sender: str
    kind: str                # message|created|plan|progress|question|answer|result|comment|review|closed
    body: str
    rendered: str            # hub 侧或 adapter 侧渲染好的可读文本


@dataclass
class BridgeEvent:
    kind: Literal["session_bound", "state_changed", "turn_started", "turn_completed", "session_ended", "error"]
    state: StateValue | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class HubSessionClient(Protocol):
    """严格对齐 core.md §8 的会话 API。"""

    async def register(
        self,
        *,
        name: str,
        kind: str,
        cwd: str,
        push: PushKind,
        conversation: str = "",
        project: str = "",
        role: str = "",
    ) -> tuple[str, str]:
        """POST /v1/sessions -> 返回 (session_id, session_token)。"""
        ...

    async def end(self, session_id: str, token: str) -> None:
        """POST /v1/sessions/<id>/end -> 立即释放名字。"""
        ...

    async def report_state(self, session_id: str, token: str, state: StateValue) -> None:
        """POST /v1/sessions/<id>/state -> {"state": "busy" | "idle"}。"""
        ...

    async def pull(self, session_id: str, token: str, *, wait_seconds: int = 50) -> list[InboxItem]:
        """GET /v1/sessions/<id>/pull?wait=<wait_seconds> -> 返回待投递条目（wait=0 供 PreInvocation 立即拉取）。"""
        ...

    async def delivered(self, session_id: str, token: str, inbox_ids: list[int]) -> None:
        """POST /v1/sessions/<id>/delivered -> {"ids": inbox_ids}，标记 delivered_at。"""
        ...


class SessionBridge(Protocol):
    """外部控制面桥接统一接口（t3-bridge / codex-bridge 实现；不拉起或杀死 agent 进程）。"""

    @property
    def conversation_id(self) -> str: ...

    @property
    def state(self) -> StateValue: ...

    async def attach(self, *, cwd: str, conversation_id: str) -> None:
        """绑定到用户已启动的 T3 thread 或 Codex app-server thread。"""
        ...

    async def send(self, text: str, *, steer: bool = False) -> None:
        """结构化投递一条消息：
        - T3Bridge: `t3 thread send <threadId> <text>`（空闲开新 turn，忙时 T3 自动排队）
        - CodexBridge: idle -> `turn/start`；busy+steer -> `turn/steer`；busy+非steer -> 等 idle 后 `turn/start`
        """
        ...

    def events(self) -> AsyncIterator[BridgeEvent]:
        """监听底层状态变化，驱动调用 HubSessionClient.report_state / end。"""
        ...

    async def close(self) -> None:
        """仅断开 bridge 连接，绝不关闭用户会话。"""
        ...


class AgentAdapter(Protocol):
    """每种 agent 的静态适配定义（替代旧版 base.py）。"""

    kind: Literal["claude", "agy", "codex", "pi"]
    push: PushKind
    tools: Literal["mcp", "extension", "cli"]

    def available(self) -> bool: ...
    def version(self) -> str: ...
    def setup(self, hub_url: str, *, workspace: str | None = None) -> list[str]: ...
    def teardown(self, *, workspace: str | None = None) -> list[str]: ...
    def start_command(self, *, name: str, workspace: str, conversation: str = "") -> str:
        """生成 core.md §9 `GET /v1/agents/<name>` 返回的 `start` 一键复制命令。"""
        ...
    def onboarding_prompt(self, *, name: str, project: str, role: str) -> str:
        """生成首次接入时发给 agent 的引导提示词。"""
        ...
```

---

## 6. 风险、与 `core.md` 对齐点及待定问题

### 6.1 与 `core.md` 核对后的 3 个小对齐项（请 `sa-cc` 确认）
1. **`sessions.push` 枚举值（`core.md` §3 & §5）**：目前 `core.md` 写了 `channel|hook|t3|pull`。建议补充 `codex_rpc`（或统一叫 `bridge` 涵盖 `t3` 与 `codex`）；而 `pi` 扩展具备空闲主动唤醒能力，语义上与 `claude` 一致，可直接归为 `channel`（或单列 `pi_ext`）。
2. **双进程共享 `session_token`（`core.md` §4）**：`agy`（T3）与 `codex` 存在“MCP server 调 9 个业务工具 + `t3-bridge`/`codex-bridge`/`PreInvocation` 调 `pull`/`delivered`/`state`”共享同一个 hub session 的场景。建议明确：**由本机 MCP server 在 `hub_register` 成功后把 `{session_id, token, cwd, kind, conversation}` 写入 `0600` 权限的 `~/.agent-hub/sessions/<name>.json`**，`t3-bridge` / `codex-bridge` / `PreInvocation` hook 均自动监听该目录读取并代理投递（会话结束调 `/end` 时删除该文件）。这样用户无需手动给 bridge 传 `--thread` 参数，新增 T3 对话即自动接管！
3. **`GET /v1/sessions/<id>/pull?wait=0`**：请确认 `pull` 支持 `wait=0`（立即返回当前未投递条目不阻塞），供 `agy` 的 `PreInvocation` hook 忙时顺手消费，同时避免与 `t3-bridge` 的长轮询重复投递（hub 侧在 `pull` 发出时记录短租约或 `sent_at` 窗口，未 `delivered` 超时后再重投）。

### 6.2 各 Adapter 的已知风险
1. **`agy`（T3）联络人可靠性**：
   - `t3-bridge` 依赖本机 `t3 serve` 运行与 `~/.agent-hub/sessions/<name>.json` 里的 `t3_thread_id`。若用户在 T3 同一项目下同时开了多个 running 的 Antigravity thread，自动匹配 `threadId` 可能有歧义——最稳的解法是 `agenthub setup agy` 写入的 `SessionStart`/`PreInvocation` hook 拿到 `conversationId` 后，或由 `t3 thread list --project <cwd> --json` 结合标题/最近更新时间匹配，必要时允许用户用 `/t3-bind <threadId>` 或在 `hub_register` 显式传入。
   - 若用户退出 T3 对话或删掉 thread，MCP server 的 stdin 关闭会触发 `/end`，`t3-bridge` 收到 `t3 thread send` 非零退出码时应将状态报 `offline` 并停止消费 inbox（不调用 `delivered`，让消息留在队列等下次上线）。
2. **`codex app-server` 协议稳定性**：
   - `app-server` 仍标 `[experimental]`，CLI 升级可能微调 JSON-RPC 字段；建议 `codex-bridge` 忽略未知 notification，且在 `agenthub doctor` 加入 `initialize` 冒烟检测。
   - 若用户忘记加 `--remote` 直接运行 `codex`，`codex-bridge` 连不到该会话，此时自动退化为 `pull` 模式（agent 仍可用 MCP 调 `hub_inbox`）。
3. **`pi` Extension 维护成本与异常隔离**：
   - 需随仓库维护约 250 行 TypeScript（`agent-hub.ts`，零第三方依赖，仅用 Node 22 原生 `fetch` + `pi` 内置 `typebox`），由 `agenthub setup pi` 同步到 `~/.pi/agent/extensions/`。
   - 扩展内所有网络请求必须 `try/catch` 兜底，且在 `session_shutdown` 取消 `AbortController`，避免 `/reload` 或 `/resume` 泄漏后台轮询。
