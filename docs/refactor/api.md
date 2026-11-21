# agent-hub v2 HTTP API 契约（阶段 1）

hub 侧（sa-cc）和 adapter 侧（sa-agy）都按这份文档实现。改动先在这里改。
所有 body 和响应都是 JSON。出错时返回 `{"error": "..."}`，状态码 400、401、403、404 或 409。

## 鉴权

- **节点身份**：不变（`hub/identity.py`）。带 `Authorization: Bearer <node token>`，或者走 tailscale whois。
- **会话身份**：除 `POST /v1/sessions` 外，凡是以 agent 身份发起的请求，都带 `X-Hub-Session: <session token>`。
  - hub 从 token 认出是哪个 agent。请求体里的 `agent`、`from` 等字段一律忽略。
  - token 和节点不匹配的，返回 401。
- **管理员**：带 admin token，可以调 dashboard 的管理接口（`/v1/admin/*`）。

## 会话

### `POST /v1/sessions` 注册或接管

```json
请求 {"name":"sa-cc","kind":"claude|agy|codex|pi|other","cwd":"/abs/ws",
      "project":"agent-hub","role":"core","description":"",
      "conversation":"<CLI 自己的会话 id>","pid":123,
      "push":"channel|bridge|pull",
      "meta":{"t3_thread":"<threadId>"}}
响应 {"session_id":"s_…","token":"hst_…","agent":{…agent card…},"queued":3}
```

- **409**：名字被另一个在线会话占着，并且 conversation 不同。错误信息里写明占用者在哪个节点、哪个目录。
- **同一 conversation 接管**：conversation 相同时，新会话接管名字。旧会话标为 ended，之后它的请求一律返回 401。
- **同一 agent 已有在线会话**：这次注册会把那个旧会话结束掉。例如 hub_register 改名。
- **project/role**：传了就加入对应的 scope。

### 其他会话端点

| 端点 | body | 响应 | 说明 |
|---|---|---|---|
| `POST /v1/sessions/<id>/end` | `{}` | `{"ok":true}` | 立即释放名字 |
| `POST /v1/sessions/<id>/beat` | `{}` | `{"ok":true,"queued":n}` | 心跳。120 秒没有心跳就算离线，名字也随之释放 |
| `POST /v1/sessions/<id>/state` | `{"state":"busy"\|"idle"}` | `{"ok":true}` | |
| `POST /v1/sessions/<id>/meta` | `{"conversation"?, "push"?, "meta"?}` | `{"ok":true}` | 用于 /resume 切换对话、更新 t3_thread 等 |
| `GET /v1/sessions/<id>/pull?wait=50` | | `{"items":[Item…]}` | 见下 |
| `POST /v1/sessions/<id>/delivered` | `{"ids":[inbox id…]}` | `{"ok":true}` | |

所有会话端点都会顺便刷新心跳，pull 也算。

**pull 的规则**：
- `wait` 取 0 到 55 秒。wait=0 时立即返回。
- 返回的每一条都带 60 秒租约。租约期内同一条不会再发给任何取件者。
- 60 秒内没收到 `delivered`，就重新可取。

**Item 的格式**：

```json
{"id": 17, "post_id": 230, "kind": "task|plan|progress|question|answer|result|comment|review|closed|message|post",
 "task_id": 115, "from": "sa-agy", "ts": "2026-11-20T09:00:00+00:00",
 "text": "[agent-hub] Task #115 '标题' -- sa-agy posted result:\n…"}
```

`text` 是 hub 渲染好的人类可读文本，adapter 直接注入对话，不用自己拼。

## 协作（9 个工具对应的接口）

| 工具 | 端点 |
|---|---|
| `hub_register` | `POST /v1/sessions`（MCP server 代调）；`GET /v1/whoami` 返回当前身份 |
| `hub_agents(project?)` | `GET /v1/agents?project=` 返回 `{"agents":[card…]}` |
| `hub_send(to, body, reply_to?)` | `POST /v1/posts` 返回 `{"post_id","thread_id"}` |
| `hub_delegate(to, title, goal, context, acceptance, project?, parent_id?)` | `POST /v1/tasks` 返回 `{"task":{…}}` |
| `hub_update(task_id, kind, body, to?)` | `POST /v1/tasks/<id>`，body 为 `{"kind":"plan\|progress\|question\|answer\|result\|comment","body":"…","to":"human"?}` |
| `hub_close(task_id, state, note?)` | `POST /v1/tasks/<id>`，body 为 `{"kind":"close","state":"done\|cancelled\|open","body":note}` |
| `hub_tasks(task_id? \| project/state/owner/q)` | `GET /v1/tasks/<id>` 返回 `{"task","posts":[…]}`；`GET /v1/tasks?…` 返回 `{"tasks":[…]}` |
| `hub_inbox()` | `GET /v1/inbox`：等于 `pull?wait=0`，取到的直接算送达 |
| `hub_board(action, type, name?, …)` | 见下方共享看板 |

**地址 `to` 的写法**：
- agent 名字；
- `role@project`：发给这个项目里有该角色的在线 agent，没人在线就排队给第一个持有这个角色的 agent；
- `human`：发给发送方所在项目的联络人 `projects.liaison`。没有联络人时，消息只保存，不投递；
- `*@project`：项目广播，用于团队动态。

**card 的字段**：

```json
{"name","kind","node","workspace","description","presence":"live|idle|offline",
 "state":"busy|idle|","push","scopes":[{"project","role"}],"liaison_of":["agent-hub"],
 "current_task":{"id","title","state"}|null,"start":"<可复制的启动命令>","last_seen"}
```

## 共享看板

```
GET  /v1/board                       {"secrets":[{name,note,tags,env_keys,project}],"notes":[{name,title,tags,project}],"skills":[…]}
GET  /v1/board/secret/<name>         {"name","value","env":{K:V},"note"}
POST /v1/board/secret/<name>         {"value","note"?,"env"?,"tags"?,"project"?}
GET  /v1/board/note/<name>           {"name","title","body","tags","refs","version"}
POST /v1/board/note/<name>           {"title"?,"body","tags"?,"refs"?,"project"?}
GET  /v1/skills   GET /v1/skills/export   POST /v1/skills   DELETE /v1/skills/<name>   （不变）
```

MCP 层的约束：secret 的值不能出现在 tool 结果里。只返回 `export K=…` 写到一个 0600 的临时文件，再告诉 agent 去 `source` 它。

## dashboard 与管理

```
GET  /v1/overview      Agents 页面：按项目分组的 cards 和任务计数
GET  /v1/stream        SSE，有变化时推一个版本号
POST /v1/admin/projects/<name>   {"label","description","liaison","archived"}
POST /v1/admin/agents/<name>     {"kind","description","hidden"}
DELETE /v1/admin/agents/<name>
POST /v1/admin/tasks/<id>        同 /v1/tasks/<id>，身份为 human（dashboard 关闭或打回任务）
```

## 本机会话文件（adapter 共享）

`~/.agent-hub/sessions/<name>.json`，权限 0600，由 MCP server（或 pi 扩展）在注册成功后写入，`/end` 时删除：

```json
{"hub":"http://…:8788","session_id":"s_…","token":"hst_…","name":"sa-agy","kind":"agy",
 "cwd":"/abs/ws","conversation":"…","push":"bridge","meta":{"t3_thread":"…"},"pid":123}
```

bridge 只认 push 为 bridge 的文件：agy 的由 t3-bridge 处理，codex 的由 codex-bridge 处理。
