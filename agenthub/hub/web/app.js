/* agent-hub dashboard. Vanilla JS, hash routing, one SSE stream that says
 * "something changed" -- each view re-fetches what it shows. */
"use strict";

/* ------------------------------------------------------------ i18n */

const I18N = {
  en: {
    "nav.home": "Inbox", "nav.projects": "Projects", "nav.tasks": "Tasks", "nav.shared": "Shared", "nav.agents": "Agents",
    "nav.nodes": "Nodes", "nav.activity": "Activity", "nav.skills": "Skills", "nav.guide": "Guide",
    "top.search": "Search tasks / activity", "top.conn": "Live connection",
    "theme.toggle": "Toggle light / dark", "theme.toLight": "Switch to light", "theme.toDark": "Switch to dark",
    "lang.btn": "中", "lang.toggle": "切换到中文",

    "state.open": "Open", "state.planning": "Planning", "state.in_progress": "In progress", "state.blocked": "Blocked",
    "state.needs_human": "Needs you", "state.review": "In review", "state.done": "Done", "state.dropped": "Dropped",
    "kind.created": "created", "kind.claimed": "claimed", "kind.assigned": "assigned", "kind.plan": "plan", "kind.progress": "progress",
    "kind.question": "question", "kind.answer": "answer", "kind.comment": "comment", "kind.blocked": "blocked", "kind.needs_human": "needs you",
    "kind.result": "result", "kind.done": "accepted", "kind.dropped": "dropped", "kind.reopened": "reopened",

    "common.you": "👤 You", "common.cancel": "Cancel", "common.save": "Save", "common.saved": "Saved", "common.close": "Close",
    "common.delete": "Delete", "common.deleted": "Deleted", "common.edit": "Edit", "common.send": "Send", "common.sent": "Sent",
    "common.none": "None", "common.loading": "Loading…", "common.viewTask": "View task", "common.newTask": "+ New task",
    "common.allProjects": "All projects", "common.all": "All", "common.ago": "{t} ago", "common.last": "last {t}",
    "common.hide": "Hide", "common.show": "Show", "common.unhide": "Unhide", "common.noTasks": "No tasks",

    "auth.title": "Log in to agent-hub", "auth.token": "Hub token", "auth.go": "Log in",
    "auth.hint": "Run <code>agenthub token</code> on the hub machine to print it.",
    "auth.bad": "Wrong token", "auth.required": "Login required",

    "home.title": "Needs you", "home.sub": "Only what needs your hand: permission prompts, decisions, blockers, reviews, unclaimed tasks.",
    "home.allClear": "<b>✨ Nothing needs you</b>The agents are working; anything for you gets pushed here and to your notifier (Telegram, Slack, …)",
    "home.working": "In progress", "home.noWorking": "No tasks in progress", "home.recent": "Recent collaboration",
    "home.noRecent": "No collaboration yet", "home.live": "Live agents", "home.noLive": "No agents online", "home.nodes": "Nodes",
    "home.taskCounts": "Tasks", "home.notifier": "📱 Notifications not set up — see <a href=\"#/guide\">Guide</a>",

    "att.permission": "🔐 Permission", "att.needs_human": "🔴 Needs your decision", "att.blocked": "🟠 Blocked", "att.review": "🟣 Ready for review",
    "att.unclaimed": "⚪ Unclaimed", "att.unrouted": "⚠️ Can't deliver", "att.stalled": "⏳ Delivered, no response",
    "att.allow": "Allow", "att.deny": "Deny", "att.accept": "✅ Accept", "att.rejectView": "Send back / view",
    "att.answerPh": "Your answer — pushed to the agent and resumes the task", "att.answer": "Answer",
    "att.peek": "🖥 View screen", "att.renotify": "Re-push",
    "toast.renotified": "Re-pushed", "toast.allowed": "Allowed", "toast.denied": "Denied", "toast.taskState": "Task #{id} → {state}",
    "toast.answered": "Answered",

    "peek.title": "🖥 {name}'s screen", "peek.refresh": "Refresh",
    "peek.sub": "Current view of tmux session hub-{name} on its node. If it's stuck on a prompt, use the keys below to answer for it.",
    "peek.textPh": "Type text, Enter to send (presses Enter)", "peek.empty": "(empty)",

    "task.subtasks": "{n} subtasks",

    "projects.title": "Projects", "projects.sub": "One card per project: task progress, members and who's online. Click for the board. Drag cards to reorder; ✕ archives a project to the bottom.",
    "projects.empty": "No projects yet. Agents declare project/role with hub_scope, or set a project when delegating.",
    "projects.archive": "Archive (fold to bottom)", "projects.progress": "{open} open · {total} total", "projects.noTasks": "No tasks yet",
    "projects.archived": "Archived ({n})", "projects.openDesc": "{open} open · {desc}", "projects.unarchive": "Unarchive",
    "toast.archived": "Archived {n}", "toast.restored": "Restored {n}",

    "lane.open": "Open", "lane.working": "In progress", "lane.waiting": "Blocked / needs you", "lane.review": "In review", "lane.done": "Done",
    "project.noMembers": "No members have declared this project", "project.activity": "Project activity",
    "project.editTitle": "Edit project {name}", "project.label": "Display name", "project.desc": "Description", "project.archived": "Archived",

    "tasks.title": "Tasks", "tasks.sub": "Each task is a delegation: goal + context + acceptance. Plan, Q&A and result all live in its thread.",
    "tasks.filterOpen": "Unfinished", "tasks.filterAll": "All",

    "task.delegation": "Delegation", "task.time": "Time", "task.times": "created {c} ago · updated {u} ago",
    "task.accept": "✅ Accept", "task.markDone": "Mark done", "task.drop": "Drop", "task.reopen": "Reopen", "task.renotify": "Re-push",
    "task.peek": "🖥 View {name}'s screen", "task.reassign": "Reassign", "task.addSub": "+ Subtask",
    "task.goal": "Goal", "task.context": "Context", "task.acceptance": "Acceptance", "task.result": "Result",
    "task.thread": "Thread", "task.composerPh": "Write to the thread as you; pushed to {to}",
    "task.optComment": "Comment / send back", "task.optAnswer": "Answer a question", "task.plan": "Current plan",
    "task.children": "Subtasks", "task.events": "Related activity",
    "reassign.title": "Reassign task #{id}", "reassign.to": "To (agent name or role@project)", "reassign.go": "Reassign", "toast.reassigned": "Reassigned",

    "nt.titleSub": "New subtask of #{id}", "nt.title": "Delegate a task",
    "nt.sub": "You're the delegator; results come back to you (here + your notifier). The agent only sees what you write.",
    "nt.to": "To (agent name, or role@project)", "nt.toPh": "e.g. shop-api or backend@shop", "nt.ttl": "Title",
    "nt.goal": "Goal", "nt.goalPh": "What should be achieved", "nt.ctx": "Context", "nt.ctxPh": "Background, what's known, relevant files, constraints, how to test",
    "nt.acc": "Acceptance", "nt.accPh": "What counts as done", "nt.proj": "Project", "nt.go": "Delegate", "toast.delegated": "Delegated",

    "copy.elsewhere": "Copied (run on {node}). {name} is running in another terminal: this command takes it over and closes the old process",
    "copy.done": "Copied (run on {node}): {cmd}", "copy.convTitle": "Conversation name (/rename to change)", "copy.cmd": "⧉ command",

    "agents.hidden": "Hidden ({n})", "toast.hidden": "Hidden {n}", "toast.unhidden": "Unhidden {n}", "toast.hiddenN": "Hid {n}",
    "agents.staleConfirm": "Hide {n} agents with no activity for 7 days?\n\n{names}\n\nThis only folds them away; history and tasks are kept, and they reappear when they come back online.",
    "agents.hideStale": "Hide idle for {d} days ({n})",
    "agents.sub": "Grouped by node. Identity = name + home (workspace@node). When a task arrives and the agent is offline, hubd wakes it in its home with tmux (wake=auto). Drag cards to reorder; ✕ hides old agents: folded to the bottom of each group, history kept, shown again when back online.",
    "agents.nodeCount": "{live} online / {shown}", "agents.nodeHidden": " · {n} hidden",
    "agents.pushTitle": "Channel online, tasks are pushed live", "agents.hideTitle": "Hide (fold to bottom)", "agents.online": "Online",
    "agents.hiddenLive": " · hidden (shown while online)", "agents.screen": "Screen", "agents.message": "Message", "agents.wake": "Wake",
    "agents.empty": "No agents yet. On any node run <code>agenthub run claude</code> (or codex, pi, …), then tell it \"your name is xxx\".",
    "toast.wake": "Asked the node to wake {n}", "toast.stop": "Asked the node to stop {n}",
    "tell.title": "Message {n}", "tell.sub": "Pushed straight into its session (woken if offline). To get work done, delegate a task instead.",
    "tell.asTask": "Delegate as task",

    "agent.copyOpen": "⧉ Copy open command", "agent.screen": "🖥 Screen", "agent.delegate": "Delegate", "agent.stop": "Stop",
    "agent.home": "Home", "agent.session": "Session", "agent.open": "Open", "agent.runOn": "(run on {node})", "agent.repo": "Repo",
    "agent.roles": "Roles", "agent.desc": "Description", "agent.wake": "Wake",
    "agent.wakeAuto": "auto — woken automatically when a task arrives and it's offline", "agent.wakeManual": "manual — queued only, never woken automatically",
    "agent.status": "Status", "agent.liveSess": "online (session {id})", "agent.lastActive": "last active {t} ago", "agent.pending": " · {n} pending",
    "agent.aliases": "Former names", "agent.tasks": "Tasks", "agent.sessions": "Sessions",
    "agent.sessTitle": "session {id} · conversation {conv}", "agent.ended": "ended", "agent.events": "Posts",
    "ea.title": "Edit {name}", "ea.rename": "Rename (old name kept as an alias)", "ea.roles": "Roles (one project/role per line)",
    "ea.ws": "Home: workspace", "ea.node": "Home: node", "ea.wake": "Wake policy", "ea.delConfirm": "Delete {name}? History is kept.",

    "shared.title": "Shared", "shared.addSecret": "+ Secret", "shared.addNote": "+ How-to",
    "shared.sub": "Things every agent can use. Each session starts with this list (names and purpose only, no values); agents fetch with <code>hub_vault</code> / <code>hub_note</code> when needed.\n    Secret values leave the hub only when asked for by name, and every fetch is logged.",
    "shared.filterPh": "Filter by name / purpose / env var", "shared.secrets": "🔑 Secrets", "shared.notes": "📘 How-tos",
    "shared.noMatchSecret": "No matching secrets", "shared.noSecret": "No secrets yet. Click “+ Secret” to add common API keys; agents can also store them with hub_vault put.",
    "shared.noMatchNote": "No matching how-tos", "shared.noNote": "No how-tos yet. Good for: endpoints, model lists, how to call things and gotchas, team conventions.",
    "secret.noNote": "(no description)", "secret.copy": "Copy value", "secret.copyEnv": "Copy export", "secret.log": "Log",
    "secret.meta": "{owner} updated {t} ago · fetched {n} times", "secret.lastGet": " (last {agent} {t} ago)",
    "toast.copied": "Copied {n}", "toast.copiedEnv": "Copied export lines",
    "vlog.get": "get", "vlog.put": "put", "vlog.edit": "edit note", "vlog.delete": "delete", "vlog.title": "Access log for {n}",
    "se.edit": "Edit secret {n}", "se.new": "New secret", "se.name": "Name (agents fetch it by this, e.g. openai-relay)",
    "se.value": "Value", "se.valueKeep": "(empty = unchanged; filled = rotate, version +1)",
    "se.valuePh": "A single key as is; several fields as JSON, e.g. {\"base_url\": \"https://…/v1\", \"api_key\": \"sk-…\"}",
    "se.note": "Purpose / how to use (agents see the first line in the list)", "se.tags": "Tags (comma-separated, e.g. llm, data)",
    "se.env": "Env var mapping (one VAR=json_field per line; VAR= means the whole value)", "se.project": "Only for one project (empty = all agents)",
    "se.delConfirm": "Delete secret {n}? No agent will be able to fetch it any more.",
    "note.refs": "Secrets used", "note.meta": "{owner} · {t} ago", "note.full": "Full text / history", "note.history": "History",
    "note.histItem": "v{v} · {agent} · {t} ago",
    "ne.edit": "Edit how-to", "ne.new": "New how-to", "ne.name": "Name (e.g. relay-howto)", "ne.title": "Title (one line, shown in every agent's list)",
    "ne.body": "Content: endpoints, model lists, how to call, gotchas… (no secret values)", "ne.tags": "Tags (comma-separated)",
    "ne.refs": "Secrets used (comma-separated; available: {list})", "ne.noRefs": "none yet", "ne.delConfirm": "Delete how-to {n}?",

    "nodes.title": "Nodes", "nodes.sub": "Each machine runs one hubd: heartbeats, wake / stop / plugin updates. Add a machine: <code>{cmd}</code><br>Get a join token with <code>agenthub node add &lt;name&gt;</code> on the hub.",
    "nodes.noAgents": "No agents", "nodes.orders": "Recent orders",
    "feed.empty": "No activity",
    "activity.title": "Activity", "activity.sub": "Findings, decisions and outputs broadcast by the team (hub_post). Collapsed by default; click to read.",
    "skills.sub": "The hub is itself a Claude Code plugin marketplace: <code>{url}</code>.\n    Publish once and every node gets it: Claude Code through this marketplace, Codex and pi as skill folders. To publish: tell an agent \"publish the xx skill to the team\", or run <code>agenthub skill publish &lt;dir&gt;</code>.",
    "skills.files": "{n} files", "skills.history": "Publish history",
    "search.title": "Search: {q}", "search.tasks": "Tasks", "search.shared": "Shared", "search.secret": "🔑 Secret", "search.note": "📘 How-to", "search.events": "Activity",

    "tg.disabled": "Telegram bot not enabled: put the bot token in <code>telegram.token</code> in the hub's data dir (default <code>~/.agent-hub/hub/</code>) or set <code>TELEGRAM_BOT_TOKEN</code>, then restart the hub.",
    "tg.paired": "✅ Telegram paired{bot}.", "tg.unpair": "Unpair", "tg.unpairQ": "Unpair?", "tg.unpaired": "Unpaired",
    "tg.pair": "📱 In Telegram, open {bot} and send:", "tg.yourBot": "your bot", "tg.pairAfter": "Once paired, only your account can control it.",
  },
  zh: {
    "nav.home": "需要你", "nav.projects": "项目", "nav.tasks": "任务", "nav.shared": "共享", "nav.agents": "Agents",
    "nav.nodes": "节点", "nav.activity": "动态", "nav.skills": "Skills", "nav.guide": "使用说明",
    "top.search": "搜索任务 / 动态", "top.conn": "实时连接",
    "theme.toggle": "切换浅色 / 深色", "theme.toLight": "切换到浅色", "theme.toDark": "切换到深色",
    "lang.btn": "EN", "lang.toggle": "Switch to English",

    "state.open": "待接手", "state.planning": "规划中", "state.in_progress": "进行中", "state.blocked": "受阻",
    "state.needs_human": "等你决定", "state.review": "待验收", "state.done": "完成", "state.dropped": "放弃",
    "kind.created": "创建", "kind.claimed": "接手", "kind.assigned": "指派", "kind.plan": "计划", "kind.progress": "进展",
    "kind.question": "提问", "kind.answer": "回答", "kind.comment": "评论", "kind.blocked": "受阻", "kind.needs_human": "需要你",
    "kind.result": "结果", "kind.done": "验收", "kind.dropped": "放弃", "kind.reopened": "重开",

    "common.you": "👤 你", "common.cancel": "取消", "common.save": "保存", "common.saved": "已保存", "common.close": "关闭",
    "common.delete": "删除", "common.deleted": "已删除", "common.edit": "编辑", "common.send": "发送", "common.sent": "已发送",
    "common.none": "无", "common.loading": "加载中…", "common.viewTask": "查看任务", "common.newTask": "+ 新任务",
    "common.allProjects": "全部项目", "common.all": "全部", "common.ago": "{t} 前", "common.last": "上次 {t}",
    "common.hide": "隐藏", "common.show": "显示", "common.unhide": "取消隐藏", "common.noTasks": "没有任务",

    "auth.title": "登录 agent-hub", "auth.token": "Hub token", "auth.go": "登录",
    "auth.hint": "在 hub 所在机器上运行 <code>agenthub token</code> 查看。",
    "auth.bad": "token 不对", "auth.required": "需要登录",

    "home.title": "需要你处理", "home.sub": "只显示要你动手的事：权限审批、等你决定、受阻、待你验收、没人接的任务。",
    "home.allClear": "<b>✨ 没有需要你处理的事</b>agent 们在自己干活，有事会推到这里和你的通知渠道（Telegram、Slack…）",
    "home.working": "正在进行", "home.noWorking": "没有进行中的任务", "home.recent": "最近协作",
    "home.noRecent": "还没有协作记录", "home.live": "在线 agent", "home.noLive": "没有在线的 agent", "home.nodes": "节点",
    "home.taskCounts": "任务概况", "home.notifier": "📱 通知还没设置 — 见 <a href=\"#/guide\">使用说明</a>",

    "att.permission": "🔐 权限审批", "att.needs_human": "🔴 等你决定", "att.blocked": "🟠 受阻", "att.review": "🟣 待你验收",
    "att.unclaimed": "⚪ 没人接", "att.unrouted": "⚠️ 无法派送", "att.stalled": "⏳ 送达未响应",
    "att.allow": "允许", "att.deny": "拒绝", "att.accept": "✅ 验收", "att.rejectView": "打回 / 查看",
    "att.answerPh": "你的回答，会推给 agent 并恢复任务", "att.answer": "回答",
    "att.peek": "🖥 查看屏幕", "att.renotify": "重新推送",
    "toast.renotified": "已重新推送", "toast.allowed": "已允许", "toast.denied": "已拒绝", "toast.taskState": "任务 #{id} → {state}",
    "toast.answered": "已回答",

    "peek.title": "🖥 {name} 的屏幕", "peek.refresh": "刷新",
    "peek.sub": "节点上 tmux 会话 hub-{name} 的当前画面。卡在对话框上时，用下面的按键替它选择。",
    "peek.textPh": "输入文字，回车发送（会按 Enter）", "peek.empty": "(空)",

    "task.subtasks": "{n} 子任务",

    "projects.title": "项目", "projects.sub": "每个项目一张卡：任务进度、成员与在线状态。点进去是看板。拖动卡片调整顺序；✕ 把项目归档、折叠到底部。",
    "projects.empty": "还没有项目。agent 用 hub_scope 声明 project/role，或派任务时指定 project。",
    "projects.archive": "归档（折叠到底部）", "projects.progress": "{open} 进行中 · {total} 总计", "projects.noTasks": "暂无任务",
    "projects.archived": "已归档 {n} 个", "projects.openDesc": "{open} 进行中 · {desc}", "projects.unarchive": "取消归档",
    "toast.archived": "已归档 {n}", "toast.restored": "已恢复 {n}",

    "lane.open": "待接手", "lane.working": "进行中", "lane.waiting": "受阻 / 等你", "lane.review": "待验收", "lane.done": "完成",
    "project.noMembers": "没有成员声明这个项目", "project.activity": "项目动态",
    "project.editTitle": "编辑项目 {name}", "project.label": "显示名", "project.desc": "描述", "project.archived": "归档",

    "tasks.title": "任务", "tasks.sub": "每个任务是一次委托：目标 + 上下文 + 验收标准，计划、问答、结果都在它的线程里。",
    "tasks.filterOpen": "未完成", "tasks.filterAll": "全部",

    "task.delegation": "委托", "task.time": "时间", "task.times": "创建 {c} 前 · 更新 {u} 前",
    "task.accept": "✅ 验收", "task.markDone": "标记完成", "task.drop": "放弃", "task.reopen": "重新打开", "task.renotify": "重新推送",
    "task.peek": "🖥 看 {name} 的屏幕", "task.reassign": "改派", "task.addSub": "+ 子任务",
    "task.goal": "目标", "task.context": "上下文", "task.acceptance": "验收标准", "task.result": "结果",
    "task.thread": "线程", "task.composerPh": "以你的身份写进线程，推给 {to}",
    "task.optComment": "评论 / 打回修改", "task.optAnswer": "回答问题", "task.plan": "当前计划",
    "task.children": "子任务", "task.events": "相关动态",
    "reassign.title": "改派任务 #{id}", "reassign.to": "给谁（agent 名 或 role@project）", "reassign.go": "改派", "toast.reassigned": "已改派",

    "nt.titleSub": "新建 #{id} 的子任务", "nt.title": "派一个任务",
    "nt.sub": "你作为委托人，结果会推给你（这里 + 你的通知渠道）。接手的 agent 只能看到你写的内容。",
    "nt.to": "给谁（agent 名，或 role@project）", "nt.toPh": "例如 shop-api 或 backend@shop", "nt.ttl": "标题",
    "nt.goal": "目标", "nt.goalPh": "要做成什么", "nt.ctx": "上下文", "nt.ctxPh": "背景、已有结论、相关文件、约束、怎么测试",
    "nt.acc": "验收标准", "nt.accPh": "怎样算完成", "nt.proj": "项目", "nt.go": "派出", "toast.delegated": "已派出",

    "copy.elsewhere": "已复制（在 {node} 上运行）。{name} 正在另一个终端里运行：用这条命令打开会接管它，那边的旧进程会被自动关掉",
    "copy.done": "已复制（在 {node} 上运行）：{cmd}", "copy.convTitle": "会话名（/rename 可改）", "copy.cmd": "⧉ 命令",

    "agents.hidden": "已隐藏 {n} 个", "toast.hidden": "已隐藏 {n}", "toast.unhidden": "已取消隐藏 {n}", "toast.hiddenN": "已隐藏 {n} 个",
    "agents.staleConfirm": "隐藏 {n} 个 7 天没有活动的 agent？\n\n{names}\n\n只是折叠起来，历史和任务都保留；它们重新上线时会自动显示。",
    "agents.hideStale": "隐藏 {d} 天没动静的（{n}）",
    "agents.sub": "按节点分组。身份 = 名字 + 家（workspace@node）。有任务到达而它不在线时，hubd 会在它的家里用 tmux 把它唤醒（wake=auto）。拖动卡片调整顺序；老 agent 点 ✕ 隐藏：折叠到每组底部，历史保留，重新上线会自动显示。",
    "agents.nodeCount": "{live} 在线 / {shown}", "agents.nodeHidden": " · 隐藏 {n}",
    "agents.pushTitle": "channel 在线，任务会实时推送", "agents.hideTitle": "隐藏（折叠到底部）", "agents.online": "在线",
    "agents.hiddenLive": " · 已隐藏（在线时仍显示）", "agents.screen": "屏幕", "agents.message": "发消息", "agents.wake": "唤醒",
    "agents.empty": "还没有 agent。在任意节点运行 <code>agenthub run claude</code>（或 codex、pi…），然后对它说\"你叫 xxx\"。",
    "toast.wake": "已让节点唤醒 {n}", "toast.stop": "已让节点停止 {n}",
    "tell.title": "发消息给 {n}", "tell.sub": "会直接推进它的会话（不在线则唤醒）。要它干活请用\"派任务\"。",
    "tell.asTask": "改为派任务",

    "agent.copyOpen": "⧉ 复制打开命令", "agent.screen": "🖥 屏幕", "agent.delegate": "派任务", "agent.stop": "停止",
    "agent.home": "家", "agent.session": "会话", "agent.open": "打开", "agent.runOn": "（在 {node} 上运行）", "agent.repo": "仓库",
    "agent.roles": "角色", "agent.desc": "描述", "agent.wake": "唤醒",
    "agent.wakeAuto": "auto — 有任务且不在线时自动唤醒", "agent.wakeManual": "manual — 只排队，不自动唤醒",
    "agent.status": "状态", "agent.liveSess": "在线（会话 {id}）", "agent.lastActive": "上次活动 {t} 前", "agent.pending": " · 待推送 {n}",
    "agent.aliases": "曾用名", "agent.tasks": "任务", "agent.sessions": "会话",
    "agent.sessTitle": "会话 {id} · 对话 {conv}", "agent.ended": "结束", "agent.events": "发布的动态",
    "ea.title": "编辑 {name}", "ea.rename": "改名（旧名保留为别名）", "ea.roles": "角色（每行一个 project/role）",
    "ea.ws": "家：workspace", "ea.node": "家：node", "ea.wake": "唤醒策略", "ea.delConfirm": "删除 {name}？历史记录保留。",

    "shared.title": "共享", "shared.addSecret": "+ 密钥", "shared.addNote": "+ 配置说明",
    "shared.sub": "所有 agent 都能用的东西。每个会话开始时都会收到这份清单（只有名字和用途，没有值），需要时用 <code>hub_vault</code> / <code>hub_note</code> 取。\n    密钥的值只在被点名索取时离开 hub，每次取用都有记录。",
    "shared.filterPh": "筛选名字 / 用途 / 环境变量", "shared.secrets": "🔑 密钥", "shared.notes": "📘 配置说明",
    "shared.noMatchSecret": "没有匹配的密钥", "shared.noSecret": "还没有密钥。点「+ 密钥」添加常用的 API key；agent 也可以用 hub_vault put 存。",
    "shared.noMatchNote": "没有匹配的说明", "shared.noNote": "还没有说明。适合放：接口地址、模型列表、调用方式和坑、团队约定。",
    "secret.noNote": "（没有用途说明）", "secret.copy": "复制值", "secret.copyEnv": "复制 export", "secret.log": "记录",
    "secret.meta": "{owner} 更新于 {t} 前 · 取用 {n} 次", "secret.lastGet": "（最近 {agent} {t} 前）",
    "toast.copied": "已复制 {n}", "toast.copiedEnv": "已复制 export 行",
    "vlog.get": "取用", "vlog.put": "写入", "vlog.edit": "改说明", "vlog.delete": "删除", "vlog.title": "{n} 的访问记录",
    "se.edit": "编辑密钥 {n}", "se.new": "新密钥", "se.name": "名字（agent 用它取值，例如 openai-relay）",
    "se.value": "值", "se.valueKeep": "（留空 = 不修改；填了 = 轮换，版本号 +1）",
    "se.valuePh": "单个 key 直接填；多个字段用 JSON，例如 {\"base_url\": \"https://…/v1\", \"api_key\": \"sk-…\"}",
    "se.note": "用途 / 怎么用（agent 会在清单里看到第一行）", "se.tags": "标签（逗号分隔，例如 llm, 数据源）",
    "se.env": "环境变量映射（每行 VAR=JSON字段；VAR= 表示整个值）", "se.project": "只给某个项目（留空 = 所有 agent）",
    "se.delConfirm": "删除密钥 {n}？所有 agent 都将无法再取到它。",
    "note.refs": "用到的密钥", "note.meta": "{owner} · {t} 前", "note.full": "全文 / 历史", "note.history": "历史",
    "note.histItem": "v{v} · {agent} · {t} 前",
    "ne.edit": "编辑说明", "ne.new": "新配置说明", "ne.name": "名字（例如 relay-howto）", "ne.title": "标题（一行，会出现在每个 agent 的清单里）",
    "ne.body": "内容：接口地址、模型列表、调用方式、坑……（不要写密钥的值）", "ne.tags": "标签（逗号分隔）",
    "ne.refs": "用到的密钥（逗号分隔，可选：{list}）", "ne.noRefs": "暂无", "ne.delConfirm": "删除说明 {n}？",

    "nodes.title": "节点", "nodes.sub": "每台机器跑一个 hubd：上报心跳、执行唤醒 / 停止 / 更新插件。新机器接入：<code>{cmd}</code><br>join token 在 hub 上用 <code>agenthub node add &lt;名字&gt;</code> 生成。",
    "nodes.noAgents": "无 agent", "nodes.orders": "最近指令",
    "feed.empty": "没有动态",
    "activity.title": "动态", "activity.sub": "团队广播的发现、决定、产出（hub_post）。默认折叠，点开看正文。",
    "skills.sub": "hub 本身就是一个 Claude Code 插件市场：<code>{url}</code>。\n    发布一次，所有节点的 hubd 自动更新。发布：在 Claude 里说\"把 xx skill 发布到团队\"，或 <code>agenthub skill publish &lt;dir&gt;</code>。",
    "skills.files": "{n} 文件", "skills.history": "发布历史",
    "search.title": "搜索：{q}", "search.tasks": "任务", "search.shared": "共享", "search.secret": "🔑 密钥", "search.note": "📘 说明", "search.events": "动态",

    "tg.disabled": "Telegram bot 未启用：把 bot token 写进 hub 数据目录的 <code>telegram.token</code>（默认 <code>~/.agent-hub/hub/</code>），或设置环境变量 <code>TELEGRAM_BOT_TOKEN</code>，然后重启 hub。",
    "tg.paired": "✅ Telegram 已绑定{bot}。", "tg.unpair": "解除绑定", "tg.unpairQ": "解除绑定？", "tg.unpaired": "已解绑",
    "tg.pair": "📱 在 Telegram 里打开 {bot}，发送：", "tg.yourBot": "你的 bot", "tg.pairAfter": "绑定后只有你的账号能操作它。",
  },
};
let LANG = "en";
// t("key", {var}) -- vars are inserted as given (escape them yourself when the result goes into HTML)
function t(key, vars) {
  let s = I18N[LANG][key] ?? I18N.en[key] ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => vars[k] !== undefined ? vars[k] : m);
  return s;
}
const stateLabel = s => I18N[LANG]["state." + s] || s;
const kindLabel = k => I18N[LANG]["kind." + k] || k;

const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const STATES = ["open", "planning", "in_progress", "blocked", "needs_human", "review", "done", "dropped"];
const OPEN = ["open", "planning", "in_progress", "blocked", "needs_human", "review"];

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function rich(s) {
  // escape, then light touch: `code`, bare urls, file:line refs stay readable
  return esc(s).replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
}
function ago(ts) {
  if (!ts) return "—";
  const s = (Date.now() - Date.parse(ts)) / 1000;
  if (s < 60) return Math.max(0, Math.floor(s)) + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 172800) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
}
const short = p => (p || "").replace(/^\/home\/[^/]+\//, "~/").replace(/^\/Users\/[^/]+\//, "~/").replace(/^\/root\//, "~/");
const st = s => `<span class="st ${s}">${stateLabel(s)}</span>`;
const pd = p => `<span class="pd ${p}" title="${p}"></span>`;
const alink = n => n ? (n === "human" ? `<span class="chip">${t("common.you")}</span>` : `<a href="#/agent/${encodeURIComponent(n)}">${esc(n)}</a>`) : "";
const target = t_ => t_.owner ? alink(t_.owner) : `<span class="chip role">${esc(t_.role || "?")}@${esc(t_.project || "?")}</span>`;
const kchip = a => a.kind ? `<span class="chip kind">${esc(a.kind)}</span>` : "";   // which agent CLI: claude, codex, pi, …

/* ------------------------------------------------------------ auth */

let authOpen = false;   // one login modal at a time, however many calls get a 401
function showLogin() {
  if (authOpen) return;
  modal(`<h3>${t("auth.title")}</h3><div class="muted" style="font-size:12px">${t("auth.hint")}</div>
    <label>${t("auth.token")}</label><input id="lg-t" type="password" autocomplete="current-password">
    <div id="lg-e" style="color:var(--s-needs_human);font-size:13px;margin-top:6px"></div>
    <div class="row" style="margin-top:14px"><span class="spacer"></span><button class="primary" id="lg-go">${t("auth.go")}</button></div>`, box => {
    const submit = async () => {
      const token = $("#lg-t", box).value.trim();
      if (!token) return;
      $("#lg-e", box).textContent = "";
      try {
        const r = await fetch("/v1/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
        if (r.ok) return location.reload();
        const d = await r.json().catch(() => ({}));
        $("#lg-e", box).textContent = "⚠️ " + (d.error || (r.status === 401 || r.status === 403 ? t("auth.bad") : r.statusText));
      } catch (e) { $("#lg-e", box).textContent = "⚠️ " + e.message; }
    };
    $("#lg-go", box).onclick = submit;
    $("#lg-t", box).onkeydown = e => { if (e.key === "Enter") submit(); };
    $("#lg-t", box).focus();
  });
  authOpen = true;
}

async function api(path, body, method) {
  const opt = body !== undefined ? { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  if (r.status === 401) { showLogin(); throw new Error(t("auth.required")); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}
function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.add("hidden"), 2600);
}
async function act(fn, ok) {
  try { await fn(); if (ok) toast(ok); render(); } catch (e) { toast("⚠️ " + e.message); }
}
function modal(html, onMount) {
  authOpen = false;
  $("#modal-box").innerHTML = html; $("#modal").classList.remove("hidden");
  if (onMount) onMount($("#modal-box"));
}
function closeModal() { authOpen = false; $("#modal").classList.add("hidden"); $("#modal-box").innerHTML = ""; }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

/* ------------------------------------------------------------ views */

const views = {};

views.home = async () => {
  const o = await api("/v1/overview");
  const tasks = (await api("/v1/tasks?open=1&limit=40")).tasks;
  const att = o.attention;
  const working = tasks.filter(x => ["planning", "in_progress"].includes(x.state));
  let h = `<h1>${t("home.title")}</h1><div class="sub">${t("home.sub")}</div>`;
  if (!att.length) h += `<div class="card allclear">${t("home.allClear")}</div>`;
  else h += `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(380px,1fr))">${att.map(attCard).join("")}</div>`;
  h += `<div class="cols"><div><h2>${t("home.working")} <span class="count">${working.length}</span></h2>`;
  h += working.length ? `<div class="list">${working.map(taskRow).join("")}</div>` : `<div class="empty">${t("home.noWorking")}</div>`;
  h += `<h2>${t("home.recent")}</h2><div class="list">${o.recent.map(r => `
      <div class="li link" onclick="go('#/task/${r.task_id}')">
        <span class="id">#${r.task_id}</span>
        <span class="chip">${esc(kindLabel(r.kind))}</span>
        <span class="title"><b>${esc(r.agent)}</b> · ${esc(r.title)} <span class="muted">${esc((r.body || "").slice(0, 90))}</span></span>
        <span class="meta">${ago(r.ts)}</span></div>`).join("") || `<div class="li muted">${t("home.noRecent")}</div>`}</div></div>`;
  h += `<div><h2>${t("home.live")} <span class="count">${o.live.length}/${o.agent_total}</span></h2><div class="list">`;
  h += o.live.map(a => `<div class="li link" onclick="go('#/agent/${encodeURIComponent(a.name)}')">${pd("live")}
      <span class="title"><b>${esc(a.name)}</b> <span class="muted">${esc(short(a.workspace))}@${esc(a.node)}</span></span>
      <span class="meta">${a.current_task ? "#" + a.current_task.id : ""}</span></div>`).join("") || `<div class="li muted">${t("home.noLive")}</div>`;
  h += `</div><h2>${t("home.nodes")}</h2><div class="row">${o.nodes.map(n => `<span class="chip">${pd(n.hubd)} ${esc(n.name)}</span>`).join("")}</div>`;
  h += `<h2>${t("home.taskCounts")}</h2><div class="row">${STATES.filter(s => o.task_counts[s]).map(s => `<a href="#/tasks?state=${s}">${st(s)} ${o.task_counts[s]}</a>`).join(" ")}</div>`;
  if (!o.telegram_paired && !(o.notifiers || []).length) h += `<div class="card" style="margin-top:16px">${t("home.notifier")}</div>`;
  h += `</div></div>`;
  return h;
};

function attCard(it) {
  const head = ["permission", "needs_human", "blocked", "review", "unclaimed", "unrouted", "stalled"].includes(it.type) ? t("att." + it.type) : it.type;
  let actions = "";
  if (it.type === "permission")
    actions = `<button class="primary" onclick="perm(${it.id},'allow')">${t("att.allow")}</button> <button class="danger" onclick="perm(${it.id},'deny')">${t("att.deny")}</button>`;
  else if (it.type === "review")
    actions = `<button class="primary" onclick="closeTask(${it.id},'done')">${t("att.accept")}</button> <button onclick="go('#/task/${it.id}')">${t("att.rejectView")}</button>`;
  else if (it.type === "needs_human" || it.type === "blocked")
    actions = `<textarea id="ans-${it.id}" placeholder="${t("att.answerPh")}"></textarea>
      <div class="row" style="margin-top:6px"><button class="primary" onclick="answer(${it.id})">${t("att.answer")}</button>
      <button onclick="go('#/task/${it.id}')">${t("common.viewTask")}</button></div>`;
  else if (it.type === "stalled")
    actions = `<button class="primary" onclick="peekAgent('${esc(it.agent)}')">${t("att.peek")}</button>
      <button onclick="renotify(${it.id})">${t("att.renotify")}</button> <button onclick="go('#/task/${it.id}')">${t("common.viewTask")}</button>`;
  else if (it.id) actions = `<button onclick="go('#/task/${it.id}')">${t("common.viewTask")}</button>`;
  return `<div class="card att ${it.type}">
    <div class="row"><b>${head}</b>${it.id && it.type !== "permission" ? `<a class="id" href="#/task/${it.id}">#${it.id}</a>` : ""}
      <span class="spacer"></span><span class="muted">${alink(it.agent)} · ${ago(it.ts)}</span></div>
    <div style="margin-top:4px;font-weight:500">${esc(it.title)}</div>
    ${it.body ? `<div class="body"><pre>${rich(it.body)}</pre></div>` : ""}
    ${it.preview ? `<div class="preview"><pre>${esc(it.preview)}</pre></div>` : ""}
    <div>${actions}</div></div>`;
}
window.renotify = id => act(() => api(`/v1/tasks/${id}`, { renotify: true }), t("toast.renotified"));

/* the agent's tmux screen on its node, via hubd: for sessions stuck on something nobody anticipated */
window.peekAgent = name => {
  const keyBtn = (k, label) => `<button class="small" data-k="${k}">${label}</button>`;
  modal(`<div class="row"><h3 style="margin:0">${t("peek.title", { name: esc(name) })}</h3><span class="spacer"></span><button class="small" id="pk-r">${t("peek.refresh")}</button></div>
    <div class="muted" style="font-size:12px;margin:4px 0 8px">${t("peek.sub", { name: esc(name) })}</div>
    <pre id="pk-s" class="mono" style="background:var(--panel2);padding:10px;border-radius:8px;max-height:52vh;overflow:auto;font-size:11.5px;line-height:1.35">${t("common.loading")}</pre>
    <div class="row" style="margin-top:8px">${keyBtn("Up", "↑")}${keyBtn("Down", "↓")}${keyBtn("Enter", "Enter")}${keyBtn("Escape", "Esc")}${keyBtn("Tab", "Tab")}${keyBtn("C-c", "Ctrl-C")}</div>
    <div class="row" style="margin-top:8px"><input id="pk-t" placeholder="${t("peek.textPh")}" style="flex:1"><button class="primary" id="pk-go">${t("common.send")}</button></div>
    <div class="row" style="margin-top:10px"><span class="spacer"></span><button onclick="closeModal()">${t("common.close")}</button></div>`, box => {
    const show = d => { const el = $("#pk-s", box); el.textContent = d.screen || t("peek.empty"); el.scrollTop = el.scrollHeight; };
    const load = () => { $("#pk-s", box).textContent = t("common.loading"); api(`/v1/agents/${encodeURIComponent(name)}/peek`, {}).then(show).catch(e => $("#pk-s", box).textContent = "⚠️ " + e.message); };
    const send = body => api(`/v1/agents/${encodeURIComponent(name)}/keys`, body).then(show).catch(e => toast("⚠️ " + e.message));
    $("#pk-r", box).onclick = load;
    box.querySelectorAll("[data-k]").forEach(b => b.onclick = () => send({ keys: [b.dataset.k] }));
    const sendText = () => { const v = $("#pk-t", box).value; if (v) { $("#pk-t", box).value = ""; send({ text: v, keys: ["Enter"] }); } };
    $("#pk-go", box).onclick = sendText;
    $("#pk-t", box).onkeydown = e => { if (e.key === "Enter") sendText(); };
    load();
  });
};

window.perm = (id, b) => act(() => api(`/v1/permissions/${id}`, { behavior: b }), b === "allow" ? t("toast.allowed") : t("toast.denied"));
window.closeTask = (id, s) => act(() => api(`/v1/tasks/${id}`, { state: s }), t("toast.taskState", { id, state: stateLabel(s) }));
window.answer = id => { const v = $("#ans-" + id).value.trim(); if (v) act(() => api(`/v1/tasks/${id}`, { kind: "answer", body: v }), t("toast.answered")); };

function taskRow(x) {
  return `<div class="li link" onclick="go('#/task/${x.id}')">
    <span class="id">#${x.id}</span>${st(x.state)}
    <span class="title">${x.parent_id ? `<span class="muted">↳ </span>` : ""}${esc(x.title)}</span>
    <span class="meta">${x.project ? `<span class="chip">${esc(x.project)}</span> ` : ""}${esc(x.created_by)} → ${x.owner ? esc(x.owner) : esc((x.role || "?") + "@" + (x.project || "?"))}${x.children ? ` · ${t("task.subtasks", { n: x.children })}` : ""} · ${ago(x.updated_at)}</span></div>`;
}

views.projects = async () => {
  const ps = (await api("/v1/projects")).projects.sort(byPos);
  let h = `<div class="row"><h1>${t("projects.title")}</h1><span class="spacer"></span><button onclick="newTask()">${t("common.newTask")}</button></div>
    <div class="sub">${t("projects.sub")}</div>`;
  if (!ps.length) return h + `<div class="empty">${t("projects.empty")}</div>`;
  const shown = ps.filter(p => !p.archived), folded = ps.filter(p => p.archived);
  h += `<div class="grid" data-sortlist="projects">` + shown.map(p => {
    const total = Object.values(p.tasks).reduce((a, b) => a + b, 0);
    const bar = STATES.filter(s => p.tasks[s]).map(s => `<span style="width:${100 * p.tasks[s] / total}%;background:var(--s-${s})"></span>`).join("");
    return `<div class="card link pcard" draggable="true" data-key="${esc(p.name)}" onclick="go('#/project/${encodeURIComponent(p.name)}')">
      <div class="row"><h3>${esc(p.label || p.name)}</h3><span class="spacer"></span><span class="muted">${ago(p.last_activity)}</span>
        <button class="icon-btn" title="${t("projects.archive")}" onclick="event.stopPropagation();hideProject('${esc(p.name)}', true)">✕</button></div>
      ${p.description ? `<div class="muted">${esc(p.description)}</div>` : ""}
      ${total ? `<div class="bar">${bar}</div><div class="muted" style="font-size:12px">${t("projects.progress", { open: p.open, total })}</div>` : `<div class="muted" style="font-size:12px;margin:8px 0">${t("projects.noTasks")}</div>`}
      <div class="row" style="margin-top:8px">${(p.agents = p.agents.filter(a => !(a.hidden && a.presence !== "live"))).slice(0, 8).map(a => `<span class="chip">${pd(a.presence)}${esc(a.agent)}${a.role ? ` <span class="muted">${esc(a.role)}</span>` : ""}</span>`).join("")}${p.agents.length > 8 ? `<span class="muted">+${p.agents.length - 8}</span>` : ""}</div>
    </div>`;
  }).join("") + `</div>`;
  if (folded.length) h += `<details class="folded" ${openFolds.has("projects") ? "open" : ""} ontoggle="fold('projects', this)">
    <summary>${t("projects.archived", { n: folded.length })}</summary><div class="list" style="margin-top:8px">${folded.map(p => `
    <div class="li"><a href="#/project/${encodeURIComponent(p.name)}">${esc(p.label || p.name)}</a>
      <span class="title muted" style="font-size:12px">${t("projects.openDesc", { open: p.open, desc: esc(p.description || "") })}</span>
      <span class="meta">${ago(p.last_activity)}</span>
      <button class="small" onclick="hideProject('${esc(p.name)}', false)">${t("projects.unarchive")}</button></div>`).join("")}</div></details>`;
  return h;
};
window.hideProject = (n, hide) => act(() => api("/v1/projects/" + encodeURIComponent(n), { archived: hide ? 1 : 0 }), hide ? t("toast.archived", { n }) : t("toast.restored", { n }));

views.project = async name => {
  const [ps, ts, ev] = await Promise.all([api("/v1/projects"), api(`/v1/tasks?project=${encodeURIComponent(name)}&limit=300`),
    api(`/v1/events?project=${encodeURIComponent(name)}&limit=15`)]);
  const p = ps.projects.find(x => x.name === name) || { name, agents: [], description: "" };
  const tasks = ts.tasks;
  const lanes = [[t("lane.open"), ["open"]], [t("lane.working"), ["planning", "in_progress"]], [t("lane.waiting"), ["blocked", "needs_human"]],
    [t("lane.review"), ["review"]], [t("lane.done"), ["done", "dropped"]]];
  let h = `<div class="crumb"><a href="#/projects">${t("projects.title")}</a> /</div>
    <div class="row"><h1>${esc(p.label || name)}</h1><span class="spacer"></span>
    <button onclick="editProject('${esc(name)}')">${t("common.edit")}</button><button class="primary" onclick="newTask({project:'${esc(name)}'})">${t("common.newTask")}</button></div>
    <div class="sub">${esc(p.description || "")}</div>
    <div class="row" style="margin-bottom:16px">${p.agents.filter(a => !(a.hidden && a.presence !== "live")).map(a => `<a class="chip" href="#/agent/${encodeURIComponent(a.agent)}">${pd(a.presence)}${esc(a.agent)} <span class="muted">${esc(a.role || "")}</span></a>`).join("") || `<span class="muted">${t("project.noMembers")}</span>`}</div>
    <div class="board">`;
  for (const [title, ss] of lanes) {
    let items = tasks.filter(x => ss.includes(x.state));
    if (ss.includes("done")) items = items.slice(0, 12);
    h += `<div class="lane"><h4><span>${title}</span><span>${items.length}</span></h4>${items.map(x => `
      <div class="tcard" onclick="go('#/task/${x.id}')"><div class="m"><span class="id">#${x.id}</span>${ss.length > 1 ? st(x.state) : ""}${x.parent_id ? `<span>↳ #${x.parent_id}</span>` : ""}</div>
      <div class="t">${esc(x.title)}</div>
      <div class="m">${esc(x.created_by)} → ${x.owner ? esc(x.owner) : `<span class="chip role">${esc(x.role)}</span>`}<span class="spacer"></span>${ago(x.updated_at)}</div></div>`).join("")}</div>`;
  }
  h += `</div><h2>${t("project.activity")}</h2>` + feedList(ev.events);
  return h;
};

window.editProject = name => {
  api("/v1/projects").then(d => {
    const p = d.projects.find(x => x.name === name) || {};
    modal(`<h3>${t("project.editTitle", { name: esc(name) })}</h3><label>${t("project.label")}</label><input id="pl" value="${esc(p.label || "")}">
      <label>${t("project.desc")}</label><textarea id="pd">${esc(p.description || "")}</textarea>
      <label><input type="checkbox" id="pa" style="width:auto" ${p.archived ? "checked" : ""}> ${t("project.archived")}</label>
      <div class="row" style="margin-top:14px"><span class="spacer"></span><button onclick="closeModal()">${t("common.cancel")}</button>
      <button class="primary" id="ps">${t("common.save")}</button></div>`, box => {
      $("#ps", box).onclick = () => act(() => api(`/v1/projects/${encodeURIComponent(name)}`,
        { label: $("#pl").value, description: $("#pd").value, archived: $("#pa").checked ? 1 : 0 }).then(closeModal), t("common.saved"));
    });
  });
};

views.tasks = async (_, params) => {
  const state = params.get("state") || "open";
  const project = params.get("project") || "";
  const q = new URLSearchParams({ limit: 300 });
  if (state === "open") q.set("open", 1); else if (state !== "all") q.set("state", state);
  if (project) q.set("project", project);
  const [ts, ps] = await Promise.all([api("/v1/tasks?" + q), api("/v1/projects")]);
  const setp = (k, v) => { const n = new URLSearchParams(params); v ? n.set(k, v) : n.delete(k); return "#/tasks?" + n; };
  let h = `<div class="row"><h1>${t("tasks.title")}</h1><span class="spacer"></span><button class="primary" onclick="newTask()">${t("common.newTask")}</button></div>
    <div class="sub">${t("tasks.sub")}</div>
    <div class="filters">${["open", ...STATES, "all"].map(s => `<button class="${s === state ? "on" : ""}" onclick="go('${setp("state", s)}')">${s === "open" ? t("tasks.filterOpen") : s === "all" ? t("tasks.filterAll") : stateLabel(s)}</button>`).join("")}
    <select onchange="go(this.value)"><option value="${setp("project", "")}">${t("common.allProjects")}</option>${ps.projects.map(p => `<option value="${setp("project", p.name)}" ${p.name === project ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>`;
  h += ts.tasks.length ? `<div class="list">${ts.tasks.map(taskRow).join("")}</div>` : `<div class="empty">${t("common.noTasks")}</div>`;
  return h;
};

views.task = async id => {
  const x = await api("/v1/tasks/" + id);
  const isOpen = OPEN.includes(x.state);
  let h = `<div class="crumb"><a href="#/tasks">${t("tasks.title")}</a> / ${x.project ? `<a href="#/project/${encodeURIComponent(x.project)}">${esc(x.project)}</a> / ` : ""}${x.parent ? `<a href="#/task/${x.parent.id}">#${x.parent.id} ${esc(x.parent.title)}</a> / ` : ""}</div>
    <div class="task-head"><h1><span class="id" style="font-size:18px">#${x.id}</span> ${esc(x.title)}</h1>${st(x.state)}</div>
    <div class="kv"><span class="k">${t("task.delegation")}</span><span>${alink(x.created_by)} → ${target(x)}</span>
    <span class="k">${t("task.time")}</span><span>${t("task.times", { c: ago(x.created_at), u: ago(x.updated_at) })}</span></div>
    <div class="row">${x.state === "review" ? `<button class="primary" onclick="closeTask(${x.id},'done')">${t("task.accept")}</button>` : ""}
      ${isOpen ? `<button onclick="closeTask(${x.id},'done')">${t("task.markDone")}</button><button onclick="closeTask(${x.id},'dropped')">${t("task.drop")}</button>` : `<button onclick="closeTask(${x.id},'open')">${t("task.reopen")}</button>`}
      ${x.owner && x.state === "open" ? `<button onclick="renotify(${x.id})">${t("task.renotify")}</button><button onclick="peekAgent('${esc(x.owner)}')">${t("task.peek", { name: esc(x.owner) })}</button>` : ""}
      <button onclick="reassign(${x.id})">${t("task.reassign")}</button><button onclick="newTask({parent_id:${x.id},project:'${esc(x.project)}'})">${t("task.addSub")}</button></div>`;
  const sec = (label, body, cls = "") => body ? `<div class="section"><div class="label">${label}</div><div class="content ${cls}"><pre>${rich(body)}</pre></div></div>` : "";
  h += `<div class="cols"><div>`;
  h += sec(t("task.goal"), x.goal) + sec(t("task.context"), x.context) + sec(t("task.acceptance"), x.acceptance) + sec(t("task.result"), x.result, "result");
  h += `<h2>${t("task.thread")} <span class="count">${x.log.length}</span></h2><div class="timeline">${x.log.map(e => `
    <div class="ev ${e.kind}"><span class="ic"></span><span class="who">${e.agent === "human" ? t("common.you") : esc(e.agent)}</span><span class="kind">${esc(kindLabel(e.kind))}${e.kind === "created" || e.kind === "assigned" ? " " + esc(e.body) : ""}</span><span class="when">${ago(e.ts)}</span>
    <div class="txt"><pre>${rich(e.body)}</pre></div></div>`).join("")}</div>`;
  h += `<div class="composer"><textarea id="cm" placeholder="${t("task.composerPh", { to: esc(x.owner || x.role) })}"></textarea>
    <div class="row" style="margin-top:8px"><select id="ck" style="width:auto"><option value="comment">${t("task.optComment")}</option><option value="answer">${t("task.optAnswer")}</option></select>
    <span class="spacer"></span><button class="primary" onclick="comment(${x.id})">${t("common.send")}</button></div></div>`;
  h += `</div><div>`;
  if (x.plan) h += sec(t("task.plan"), x.plan);
  h += `<h2>${t("task.children")} <span class="count">${x.children.length}</span></h2>${x.children.length ? `<div class="list">${x.children.map(c => `<div class="li link" onclick="go('#/task/${c.id}')"><span class="id">#${c.id}</span>${st(c.state)}<span class="title">${esc(c.title)}</span><span class="meta">${esc(c.owner || c.role)}</span></div>`).join("")}</div>` : `<div class="muted">${t("common.none")}</div>`}`;
  if (x.events.length) h += `<h2>${t("task.events")}</h2>` + feedList(x.events);
  h += `</div></div>`;
  return h;
};
window.comment = id => { const v = $("#cm").value.trim(); if (v) act(() => api(`/v1/tasks/${id}`, { kind: $("#ck").value, body: v }), t("common.sent")); };
window.reassign = id => {
  modal(`<h3>${t("reassign.title", { id })}</h3><label>${t("reassign.to")}</label><input id="ra" list="agent-list">${agentDatalist()}
    <div class="row" style="margin-top:14px"><span class="spacer"></span><button onclick="closeModal()">${t("common.cancel")}</button><button class="primary" id="rs">${t("reassign.go")}</button></div>`,
    box => { $("#rs", box).onclick = () => act(() => api(`/v1/tasks/${id}`, { assign: $("#ra").value.trim() }).then(closeModal), t("toast.reassigned")); });
};

let agentCache = [];
function agentDatalist() { return `<datalist id="agent-list">${agentCache.map(a => `<option value="${esc(a.name)}">${esc(a.home)}</option>`).join("")}</datalist>`; }
window.newTask = (pre = {}) => {
  modal(`<h3>${pre.parent_id ? t("nt.titleSub", { id: pre.parent_id }) : t("nt.title")}</h3>
    <div class="muted" style="font-size:12px">${t("nt.sub")}</div>
    <label>${t("nt.to")}</label><input id="nt-to" list="agent-list" value="${esc(pre.to || "")}" placeholder="${t("nt.toPh")}">${agentDatalist()}
    <label>${t("nt.ttl")}</label><input id="nt-title">
    <label>${t("nt.goal")}</label><textarea id="nt-goal" placeholder="${t("nt.goalPh")}"></textarea>
    <label>${t("nt.ctx")}</label><textarea id="nt-ctx" placeholder="${t("nt.ctxPh")}"></textarea>
    <label>${t("nt.acc")}</label><input id="nt-acc" placeholder="${t("nt.accPh")}">
    <label>${t("nt.proj")}</label><input id="nt-proj" value="${esc(pre.project || "")}">
    <div class="row" style="margin-top:14px"><span class="spacer"></span><button onclick="closeModal()">${t("common.cancel")}</button><button class="primary" id="nt-go">${t("nt.go")}</button></div>`,
    box => {
      $("#nt-go", box).onclick = () => act(async () => {
        const r = await api("/v1/tasks", { to: $("#nt-to").value.trim(), title: $("#nt-title").value.trim(), goal: $("#nt-goal").value,
          context: $("#nt-ctx").value, acceptance: $("#nt-acc").value, project: $("#nt-proj").value.trim(), parent_id: pre.parent_id || null });
        closeModal(); go("#/task/" + r.id);
      }, t("toast.delegated"));
    });
};

/* the shell command that opens this agent: attach to its tmux if hubd is running it there,
 * otherwise start it in its home workspace under its own name (resuming its last session) */
let tmuxByNode = {};
async function loadTmux() {
  try {
    const ns = (await api("/v1/nodes")).nodes;
    tmuxByNode = Object.fromEntries(ns.map(n => [n.name, new Set((n.info || {}).tmux || [])]));
  } catch (e) { /* keep the last known */ }
}
const sq = s => "'" + String(s).replace(/'/g, "'\\''") + "'";
// the agent's conversation, resumed in its home; on a session that is still live elsewhere this takes it over
function resumeCmd(a) {
  const conv = a.conversation || (a.last_session && !/^(cli-|pid-)/.test(a.last_session) ? a.last_session : "");
  const kind = a.kind || "claude";
  return `cd ${sq(a.workspace || "~")} && agenthub run ${/^[\w.-]+$/.test(kind) ? kind : sq(kind)} --agent ${sq(a.name)}${conv ? ` --resume ${conv}` : ""}`;
}
function openCmd(a) {
  const tmux = "hub-" + a.name.replace(/[^A-Za-z0-9_-]/g, "_");
  if ((tmuxByNode[a.node] || new Set()).has(tmux)) return `tmux attach -t ${tmux}`;
  return resumeCmd(a);
}
window.copyOpen = (name, kind) => {
  const a = agentCache.find(x => x.name === name) || window._agentDetail;
  if (!a) return;
  const cmd = kind === "resume" ? resumeCmd(a) : openCmd(a);
  const elsewhere = a.presence === "live" && !cmd.startsWith("tmux attach");
  copyText(cmd).then(() => toast(elsewhere ? t("copy.elsewhere", { node: a.node, name: a.name }) : t("copy.done", { node: a.node, cmd })));
};
const convTitle = a => a.title ? `<div class="ctitle" title="${t("copy.convTitle")}">💬 ${esc(a.title)}</div>` : "";
const copyBtn = (a, kind) => kind === "resume"
  ? `<button class="small copy-cmd" title="${esc(resumeCmd(a))}" onclick="copyOpen('${esc(a.name)}','resume')">⧉ resume</button>`
  : `<button class="small copy-cmd" title="${esc(openCmd(a))}" onclick="copyOpen('${esc(a.name)}')">${t("copy.cmd")}</button>`;

const isFolded = a => a.hidden && a.presence !== "live";   // hidden, unless it came back to life
const openFolds = new Set();                                 // which "hidden" groups the user expanded
window.fold = (key, el) => { el.open ? openFolds.add(key) : openFolds.delete(key); };
window.hideAgent = (n, hide) => act(() => api("/v1/agents/" + encodeURIComponent(n), { hidden: hide }), hide ? t("toast.hidden", { n }) : t("toast.unhidden", { n }));
window.hideStale = names => {
  if (!confirm(t("agents.staleConfirm", { n: names.length, names: names.join(", ") }))) return;
  act(() => Promise.all(names.map(n => api("/v1/agents/" + encodeURIComponent(n), { hidden: true }))), t("toast.hiddenN", { n: names.length }));
};
const STALE_DAYS = 7;
const isStale = a => a.presence !== "live" && (!a.last_seen || Date.now() - Date.parse(a.last_seen) > STALE_DAYS * 86400e3);

function foldedList(key, list, kind) {
  if (!list.length) return "";
  return `<details class="folded" ${openFolds.has(key) ? "open" : ""} ontoggle="fold('${esc(key)}', this)">
    <summary>${t("agents.hidden", { n: list.length })}</summary><div class="list" style="margin-top:8px">${list.map(a => `
    <div class="li">${pd(a.presence)}<a href="#/agent/${encodeURIComponent(a.name)}">${esc(a.name)}</a>${kchip(a)}
      <span class="title muted" style="font-size:12px">${esc(short(a.workspace))}</span>
      <span class="meta">${t("common.last", { t: ago(a.last_seen) })}</span>${copyBtn(a, kind)}
      <button class="small" onclick="hideAgent('${esc(a.name)}', false)">${t("common.unhide")}</button></div>`).join("")}</div></details>`;
}

views.agents = async () => {
  const [as] = await Promise.all([api("/v1/agents").then(d => d.agents), loadTmux()]);
  agentCache = as;
  const byNode = {};
  as.forEach(a => (byNode[a.node || "?"] = byNode[a.node || "?"] || []).push(a));
  const stale = as.filter(a => !a.hidden && isStale(a)).map(a => a.name);
  let h = `<div class="row"><h1>${t("nav.agents")}</h1><span class="spacer"></span>
    ${stale.length ? `<button onclick='hideStale(${esc(JSON.stringify(stale))})'>${t("agents.hideStale", { d: STALE_DAYS, n: stale.length })}</button>` : ""}</div>
    <div class="sub">${t("agents.sub")}</div>`;
  for (const node of Object.keys(byNode).sort()) {
    const all = byNode[node];
    const shown = all.filter(a => !isFolded(a)).sort(byPos), folded = all.filter(isFolded);
    h += `<div class="node-group"><h2>🖥 ${esc(node)} <span class="count">${t("agents.nodeCount", { live: all.filter(a => a.presence === "live").length, shown: shown.length })}${folded.length ? t("agents.nodeHidden", { n: folded.length }) : ""}</span></h2>`;
    if (shown.length) h += `<div class="grid" data-sortlist="agents">` + shown.map(a => `<div class="card acard" draggable="true" data-key="${esc(a.name)}">
      <div class="name">${pd(a.presence)}<a href="#/agent/${encodeURIComponent(a.name)}">${esc(a.name)}</a>${kchip(a)}${a.channel ? `<span class="chip" title="${t("agents.pushTitle")}">⚡ push</span>` : ""}${a.pending ? `<span class="badge">${a.pending}</span>` : ""}
        <span class="spacer"></span><button class="icon-btn" title="${t("agents.hideTitle")}" onclick="hideAgent('${esc(a.name)}', true)">✕</button></div>
      <div class="home">${esc(short(a.workspace) || "?")}</div>${convTitle(a)}
      ${a.description ? `<div class="muted" style="font-size:13px">${esc(a.description)}</div>` : ""}
      <div class="row" style="margin-top:6px">${a.scopes.map(s => `<span class="chip role">${esc(s.role || "·")}@${esc(s.project)}</span>`).join("")}</div>
      ${a.current_task ? `<div class="now">${st(a.current_task.state)} <a href="#/task/${a.current_task.id}">#${a.current_task.id} ${esc(a.current_task.title)}</a></div>` : ""}
      <div class="toggle" style="margin-top:10px">${a.presence === "live" ? t("agents.online") : t("common.last", { t: ago(a.last_seen) })} · wake=${a.wake}${a.hidden ? t("agents.hiddenLive") : ""}</div>
      <div class="row" style="margin-top:6px;gap:6px">${copyBtn(a)}<span class="spacer"></span>
      ${a.presence === "live" ? `<button class="small" onclick="peekAgent('${esc(a.name)}')">${t("agents.screen")}</button><button class="small" onclick="tell('${esc(a.name)}')">${t("agents.message")}</button>` : `<button class="small" onclick="wake('${esc(a.name)}')">${t("agents.wake")}</button>`}</div></div>`).join("") + `</div>`;
    h += foldedList("agents:" + node, folded) + `</div>`;
  }
  return as.length ? h : h + `<div class="empty">${t("agents.empty")}</div>`;
};
window.wake = n => act(() => api(`/v1/agents/${encodeURIComponent(n)}/wake`, { reason: "dashboard" }), t("toast.wake", { n }));
window.stopAgent = n => act(() => api(`/v1/agents/${encodeURIComponent(n)}/stop`, {}), t("toast.stop", { n }));
window.tell = n => {
  modal(`<h3>${t("tell.title", { n: esc(n) })}</h3><div class="muted" style="font-size:12px">${t("tell.sub")}</div>
    <textarea id="tm" style="margin-top:10px"></textarea><div class="row" style="margin-top:10px"><span class="spacer"></span>
    <button onclick="closeModal()">${t("common.cancel")}</button><button onclick="closeModal();newTask({to:'${esc(n)}'})">${t("tell.asTask")}</button><button class="primary" id="ts">${t("common.send")}</button></div>`,
    box => { $("#ts", box).onclick = () => act(() => api("/v1/messages", { to: n, body: $("#tm").value }).then(closeModal), t("common.sent")); });
};

views.agent = async name => {
  const [a] = await Promise.all([api("/v1/agents/" + encodeURIComponent(name)), loadTmux()]);
  window._agentDetail = a;
  let h = `<div class="crumb"><a href="#/agents">${t("nav.agents")}</a> / ${esc(a.node)}</div>
    <div class="row"><h1>${pd(a.presence)} ${esc(a.name)}</h1>${kchip(a)}${a.channel ? `<span class="chip">⚡ push</span>` : ""}<span class="spacer"></span>
    <button title="${esc(openCmd(a))}" onclick="copyOpen('${esc(a.name)}')">${t("agent.copyOpen")}</button>
    <button onclick="peekAgent('${esc(a.name)}')">${t("agent.screen")}</button><button onclick="tell('${esc(a.name)}')">${t("agents.message")}</button><button onclick="newTask({to:'${esc(a.name)}'})">${t("agent.delegate")}</button>
    ${a.presence === "live" ? `<button class="danger" onclick="stopAgent('${esc(a.name)}')">${t("agent.stop")}</button>` : `<button class="primary" onclick="wake('${esc(a.name)}')">${t("agents.wake")}</button>`}
    <button onclick="hideAgent('${esc(a.name)}', ${a.hidden ? "false" : "true"})">${a.hidden ? t("common.unhide") : t("common.hide")}</button>
    <button onclick="editAgent('${esc(a.name)}')">${t("common.edit")}</button></div>
    <div class="kv"><span class="k">${t("agent.home")}</span><span><code>${esc(a.workspace)}</code> @ <b>${esc(a.node)}</b></span>
    <span class="k">${t("agent.session")}</span><span>${a.title ? `<b>${esc(a.title)}</b> ` : ""}<span class="muted">${esc(a.conversation || a.last_session || "—")}</span></span>
    <span class="k">${t("agent.open")}</span><span><code>${esc(openCmd(a))}</code> <span class="muted">${t("agent.runOn", { node: esc(a.node) })}</span></span>
    ${openCmd(a) !== resumeCmd(a) ? `<span class="k">Resume</span><span><code>${esc(resumeCmd(a))}</code> <button class="small" onclick="copyOpen('${esc(a.name)}','resume')">⧉</button></span>` : ""}
    <span class="k">${t("agent.repo")}</span><span>${esc(a.repo || "—")} ${a.branch ? `<span class="chip">${esc(a.branch)}</span>` : ""}</span>
    <span class="k">${t("agent.roles")}</span><span>${a.scopes.map(s => `<span class="chip role">${esc(s.role || "·")}@${esc(s.project)}</span>`).join(" ") || "—"}</span>
    <span class="k">${t("agent.desc")}</span><span>${esc(a.description || "—")}</span>
    <span class="k">${t("agent.wake")}</span><span>${a.wake === "auto" ? t("agent.wakeAuto") : t("agent.wakeManual")}</span>
    <span class="k">${t("agent.status")}</span><span>${a.presence === "live" ? t("agent.liveSess", { id: esc((a.session || {}).id || "").slice(0, 8) }) : t("agent.lastActive", { t: ago(a.last_seen) })}${t("agent.pending", { n: a.pending })}</span>
    ${a.aliases.length ? `<span class="k">${t("agent.aliases")}</span><span>${a.aliases.map(esc).join(", ")}</span>` : ""}</div>`;
  h += `<div class="cols"><div><h2>${t("agent.tasks")} <span class="count">${a.tasks.length}</span></h2>${a.tasks.length ? `<div class="list">${a.tasks.map(x => `
    <div class="li link" onclick="go('#/task/${x.id}')"><span class="id">#${x.id}</span>${st(x.state)}<span class="title">${esc(x.title)}</span>
    <span class="meta">${x.owner === a.name ? "← " + esc(x.created_by) : "→ " + esc(x.owner || "?")} · ${ago(x.updated_at)}</span></div>`).join("")}</div>` : `<div class="empty">${t("common.noTasks")}</div>`}</div>
    <div><h2>${t("agent.sessions")}</h2><div class="list">${a.sessions.map(s => `<div class="li"><span class="id" title="${esc(s.conversation ? t("agent.sessTitle", { id: s.id, conv: s.conversation }) : s.id)}">${esc((s.conversation || s.id).slice(0, 8))}</span><span class="title">${s.title ? esc(s.title) + " · " : ""}<span class="muted">${esc(short(s.cwd))}@${esc(s.node)}</span></span><span class="meta">${s.channel ? "⚡ " : ""}${s.ended_at ? t("agent.ended") : ""} ${ago(s.last_seen)}</span></div>`).join("") || `<div class="li muted">—</div>`}</div>
    <h2>${t("agent.events")}</h2>${feedList(a.events)}</div></div>`;
  return h;
};
window.editAgent = name => {
  api("/v1/agents/" + encodeURIComponent(name)).then(a => modal(`<h3>${t("ea.title", { name: esc(name) })}</h3>
    <label>${t("ea.rename")}</label><input id="ea-n" value="${esc(a.name)}">
    <label>${t("agent.desc")}</label><input id="ea-d" value="${esc(a.description || "")}">
    <label>${t("ea.roles")}</label><textarea id="ea-s">${a.scopes.map(s => s.project + "/" + s.role).join("\n")}</textarea>
    <label>${t("ea.ws")}</label><input id="ea-w" value="${esc(a.workspace)}"><label>${t("ea.node")}</label><input id="ea-node" value="${esc(a.node)}">
    <label>${t("ea.wake")}</label><select id="ea-wk"><option value="auto" ${a.wake === "auto" ? "selected" : ""}>auto</option><option value="manual" ${a.wake === "manual" ? "selected" : ""}>manual</option></select>
    <div class="row" style="margin-top:14px"><button class="danger" id="ea-del">${t("common.delete")}</button><span class="spacer"></span><button onclick="closeModal()">${t("common.cancel")}</button><button class="primary" id="ea-go">${t("common.save")}</button></div>`,
    box => {
      $("#ea-go", box).onclick = () => act(async () => {
        const nn = $("#ea-n").value.trim();
        await api("/v1/agents/" + encodeURIComponent(name), { description: $("#ea-d").value, workspace: $("#ea-w").value.trim(), node: $("#ea-node").value.trim(),
          wake: $("#ea-wk").value, scopes: $("#ea-s").value.split("\n").map(s => s.trim()).filter(Boolean), rename: nn !== name ? nn : undefined });
        closeModal(); if (nn !== name) go("#/agent/" + encodeURIComponent(nn));
      }, t("common.saved"));
      $("#ea-del", box).onclick = () => { if (confirm(t("ea.delConfirm", { name }))) act(() => api("/v1/agents/" + encodeURIComponent(name), {}, "DELETE").then(() => { closeModal(); go("#/agents"); }), t("common.deleted")); };
    }));
};

/* ------------------------------------------------------------ shared board */

const revealed = {};           // name -> value, only for this page view
let sharedFilter = { q: "", tag: "" };

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  // plain http on the tailnet is not a secure context: fall back to a hidden textarea
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); } finally { ta.remove(); }
  return Promise.resolve();
}

views.shared = async () => {
  const d = await api("/v1/shared");
  const f = sharedFilter;
  const match = x => (!f.tag || x.tags.includes(f.tag)) &&
    (!f.q || [x.name, x.note, x.title, x.body, (x.tags || []).join(" "), Object.keys(x.env || {}).join(" ")]
      .join(" ").toLowerCase().includes(f.q.toLowerCase()));
  const vault = d.vault.filter(match), notes = d.notes.filter(match);
  let h = `<div class="row"><h1>${t("shared.title")}</h1><span class="spacer"></span>
    <button onclick="editSecret()">${t("shared.addSecret")}</button><button class="primary" onclick="editNote()">${t("shared.addNote")}</button></div>
    <div class="sub">${t("shared.sub")}</div>
    <div class="filters"><input id="sh-q" placeholder="${t("shared.filterPh")}" value="${esc(f.q)}" style="width:240px">
    <button class="${!f.tag ? "on" : ""}" onclick="shTag('')">${t("common.all")}</button>
    ${d.tags.map(x => `<button class="${f.tag === x ? "on" : ""}" onclick="shTag('${esc(x)}')">${esc(x)}</button>`).join("")}</div>`;

  h += `<h2>${t("shared.secrets")} <span class="count">${vault.length}</span></h2>`;
  h += vault.length ? `<div class="grid">${vault.map(secretCard).join("")}</div>` :
    `<div class="empty">${d.vault.length ? t("shared.noMatchSecret") : t("shared.noSecret")}</div>`;
  h += `<h2>${t("shared.notes")} <span class="count">${notes.length}</span></h2>`;
  h += notes.length ? `<div class="grid">${notes.map(noteCard).join("")}</div>` :
    `<div class="empty">${d.notes.length ? t("shared.noMatchNote") : t("shared.noNote")}</div>`;
  return h;
};
window.shTag = x => { sharedFilter.tag = x; render(); };
document.addEventListener("input", e => {
  if (e.target.id !== "sh-q") return;
  sharedFilter.q = e.target.value;
  clearTimeout(window._shq);
  window._shq = setTimeout(async () => {
    const pos = e.target.selectionStart;
    app.innerHTML = await views.shared();
    const el = $("#sh-q"); el.focus(); el.setSelectionRange(pos, pos);
  }, 150);
});

function secretCard(v) {
  const env = Object.keys(v.env || {});
  const shown = revealed[v.name] !== undefined;
  return `<div class="card secret" id="sec-${esc(v.name)}">
    <div class="row"><span class="sname">${esc(v.name)}</span>${v.project ? `<span class="chip role">${esc(v.project)}</span>` : ""}
      <span class="spacer"></span><span class="muted" style="font-size:12px">v${v.version}</span></div>
    <div class="row" style="margin:4px 0">${v.tags.map(x => `<span class="chip">${esc(x)}</span>`).join("")}</div>
    ${v.note ? `<div class="snote"><pre>${rich(v.note)}</pre></div>` : `<div class="muted" style="font-size:13px">${t("secret.noNote")}</div>`}
    ${env.length ? `<div class="row" style="margin-top:6px">${env.map(e => `<code class="envv">${esc(e)}</code>`).join("")}</div>` : ""}
    ${shown ? `<div class="sval"><pre class="mono">${esc(revealed[v.name])}</pre></div>` : `<div class="sval masked">••••••••••••</div>`}
    <div class="row" style="margin-top:8px">
      <button class="small" onclick="reveal('${esc(v.name)}')">${shown ? t("common.hide") : t("common.show")}</button>
      <button class="small" onclick="copySecret('${esc(v.name)}')">${t("secret.copy")}</button>
      ${env.length ? `<button class="small" onclick="copyEnv('${esc(v.name)}')">${t("secret.copyEnv")}</button>` : ""}
      <span class="spacer"></span>
      <button class="small" onclick="secretLog('${esc(v.name)}')">${t("secret.log")}</button>
      <button class="small" onclick="editSecret('${esc(v.name)}')">${t("common.edit")}</button></div>
    <div class="muted" style="font-size:12px;margin-top:6px">${t("secret.meta", { owner: esc(v.owner || "?"), t: ago(v.updated_at), n: v.gets })}${v.last_get ? t("secret.lastGet", { agent: esc(v.last_get.agent), t: ago(v.last_get.ts) }) : ""}</div>
  </div>`;
}
window.reveal = async n => {
  if (revealed[n] !== undefined) { delete revealed[n]; return render(); }
  try { revealed[n] = (await api("/v1/vault/" + encodeURIComponent(n))).value; render(); } catch (e) { toast("⚠️ " + e.message); }
};
window.copySecret = async n => {
  try { const v = (await api("/v1/vault/" + encodeURIComponent(n))).value; await copyText(v); toast(t("toast.copied", { n })); } catch (e) { toast("⚠️ " + e.message); }
};
window.copyEnv = async n => {
  try { const d = await api(`/v1/vault/${encodeURIComponent(n)}/env`); await copyText(d.lines.join("\n")); toast(t("toast.copiedEnv")); } catch (e) { toast("⚠️ " + e.message); }
};
window.secretLog = async n => {
  const d = await api(`/v1/vault/${encodeURIComponent(n)}/log`);
  const label = a => ["get", "put", "edit", "delete"].includes(a) ? t("vlog." + a) : esc(a);
  modal(`<h3>${t("vlog.title", { n: esc(n) })}</h3><div class="list">${d.log.map(l => `<div class="li"><span class="chip">${label(l.action)}</span>
    <span class="title">${esc(l.agent)}</span><span class="meta">${esc(l.ts.replace("T", " ").slice(0, 16))} · ${t("common.ago", { t: ago(l.ts) })}</span></div>`).join("") || `<div class="li muted">${t("common.none")}</div>`}</div>
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button onclick="closeModal()">${t("common.close")}</button></div>`);
};

window.editSecret = async name => {
  let v = { name: "", note: "", tags: [], env: {}, project: "" };
  if (name) v = (await api("/v1/shared")).vault.find(x => x.name === name) || v;
  const envText = Object.entries(v.env || {}).map(([k, x]) => `${k}=${x}`).join("\n");
  modal(`<h3>${name ? t("se.edit", { n: esc(name) }) : t("se.new")}</h3>
    <label>${t("se.name")}</label><input id="se-n" value="${esc(v.name)}" ${name ? "disabled" : ""}>
    <label>${t("se.value")} ${name ? t("se.valueKeep") : ""}</label>
    <textarea id="se-v" class="mono" placeholder="${esc(t("se.valuePh"))}"></textarea>
    <label>${t("se.note")}</label><textarea id="se-note">${esc(v.note)}</textarea>
    <label>${t("se.tags")}</label><input id="se-t" value="${esc(v.tags.join(", "))}">
    <label>${t("se.env")}</label>
    <textarea id="se-env" class="mono" placeholder="OPENAI_API_KEY=api_key&#10;OPENAI_BASE_URL=base_url" style="min-height:56px">${esc(envText)}</textarea>
    <label>${t("se.project")}</label><input id="se-p" value="${esc(v.project)}">
    <div class="row" style="margin-top:14px">${name ? `<button class="danger" id="se-del">${t("common.delete")}</button>` : ""}<span class="spacer"></span>
    <button onclick="closeModal()">${t("common.cancel")}</button><button class="primary" id="se-go">${t("common.save")}</button></div>`, box => {
    $("#se-go", box).onclick = () => act(async () => {
      const n = name || $("#se-n").value.trim();
      const body = { note: $("#se-note").value, tags: $("#se-t").value, env: $("#se-env").value, project: $("#se-p").value.trim() };
      if ($("#se-v").value) body.value = $("#se-v").value;
      await api("/v1/vault/" + encodeURIComponent(n), body);
      delete revealed[n]; closeModal();
    }, t("common.saved"));
    if (name) $("#se-del", box).onclick = () => { if (confirm(t("se.delConfirm", { n: name }))) act(() => api("/v1/vault/" + encodeURIComponent(name), {}, "DELETE").then(closeModal), t("common.deleted")); };
  });
};

function noteCard(n) {
  return `<div class="card note">
    <div class="row"><b>${esc(n.title || n.name)}</b>${n.project ? `<span class="chip role">${esc(n.project)}</span>` : ""}<span class="spacer"></span>
      <span class="muted" style="font-size:12px">v${n.version}</span></div>
    <div class="row" style="margin:4px 0"><code class="muted" style="font-size:12px">${esc(n.name)}</code>${n.tags.map(x => `<span class="chip">${esc(x)}</span>`).join("")}</div>
    <div class="nbody"><pre>${rich(n.body)}</pre></div>
    ${n.refs.length ? `<div class="row" style="margin-top:6px"><span class="muted" style="font-size:12px">${t("note.refs")}</span>${n.refs.map(r => `<a class="chip" href="javascript:void(0)" onclick="document.getElementById('sec-${esc(r)}')?.scrollIntoView({behavior:'smooth',block:'center'})">🔑 ${esc(r)}</a>`).join("")}</div>` : ""}
    <div class="row" style="margin-top:8px"><span class="muted" style="font-size:12px">${t("note.meta", { owner: esc(n.owner || "?"), t: ago(n.updated_at) })}</span><span class="spacer"></span>
      <button class="small" onclick="viewNote('${esc(n.name)}')">${t("note.full")}</button><button class="small" onclick="editNote('${esc(n.name)}')">${t("common.edit")}</button></div>
  </div>`;
}
window.viewNote = async name => {
  const n = await api("/v1/notes/" + encodeURIComponent(name));
  modal(`<h3>${esc(n.title || n.name)}</h3><div class="muted" style="font-size:12px">${esc(n.name)} · v${n.version} · ${esc(n.owner)}</div>
    <div class="section"><div class="content"><pre>${rich(n.body)}</pre></div></div>
    <h2>${t("note.history")}</h2><div class="list">${n.history.map(x => `<details class="li" style="display:block"><summary>${t("note.histItem", { v: x.version, agent: esc(x.agent), t: ago(x.ts) })}</summary><pre style="margin-top:6px">${rich(x.body)}</pre></details>`).join("")}</div>
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button onclick="closeModal()">${t("common.close")}</button><button class="primary" onclick="closeModal();editNote('${esc(n.name)}')">${t("common.edit")}</button></div>`);
};
window.editNote = async name => {
  let n = { name: "", title: "", body: "", tags: [], refs: [], project: "" };
  if (name) n = await api("/v1/notes/" + encodeURIComponent(name));
  const secrets = (await api("/v1/vault")).vault.map(v => v.name);
  modal(`<h3>${name ? t("ne.edit") : t("ne.new")}</h3>
    <label>${t("ne.name")}</label><input id="ne-n" value="${esc(n.name)}" ${name ? "disabled" : ""}>
    <label>${t("ne.title")}</label><input id="ne-t" value="${esc(n.title)}">
    <label>${t("ne.body")}</label><textarea id="ne-b" style="min-height:180px">${esc(n.body)}</textarea>
    <label>${t("ne.tags")}</label><input id="ne-tags" value="${esc(n.tags.join(", "))}">
    <label>${t("ne.refs", { list: secrets.map(esc).join(", ") || t("ne.noRefs") })}</label><input id="ne-r" value="${esc(n.refs.join(", "))}">
    <label>${t("se.project")}</label><input id="ne-p" value="${esc(n.project)}">
    <div class="row" style="margin-top:14px">${name ? `<button class="danger" id="ne-del">${t("common.delete")}</button>` : ""}<span class="spacer"></span>
    <button onclick="closeModal()">${t("common.cancel")}</button><button class="primary" id="ne-go">${t("common.save")}</button></div>`, box => {
    $("#ne-go", box).onclick = () => act(async () => {
      const nn = name || $("#ne-n").value.trim();
      await api("/v1/notes/" + encodeURIComponent(nn), { title: $("#ne-t").value, body: $("#ne-b").value, tags: $("#ne-tags").value,
        refs: $("#ne-r").value, project: $("#ne-p").value.trim() });
      closeModal();
    }, t("common.saved"));
    if (name) $("#ne-del", box).onclick = () => { if (confirm(t("ne.delConfirm", { n: name }))) act(() => api("/v1/notes/" + encodeURIComponent(name), {}, "DELETE").then(closeModal), t("common.deleted")); };
  });
};

// installed agent CLIs and their versions ({"claude": "2.1.3", "codex": "0.125.0"}); older hubds only report claude
function nodeAgents(info) {
  const ag = info.agents && typeof info.agents === "object" ? Object.entries(info.agents) : [];
  return ag.length ? ag.map(([k, v]) => `${esc(k)} ${esc(v || "?")}`).join(" · ") : `claude ${esc(info.claude || "?")}`;
}

views.nodes = async () => {
  const [ns, os] = await Promise.all([api("/v1/nodes"), api("/v1/orders")]);
  tmuxByNode = Object.fromEntries(ns.nodes.map(n => [n.name, new Set((n.info || {}).tmux || [])]));
  ns.nodes.forEach(n => n.agents.forEach(a => { if (!agentCache.find(x => x.name === a.name)) agentCache.push(a); }));
  const join = esc(`curl -fsSL ${location.origin}/install.sh | sh -s -- --join ${location.origin} <join-token>`);
  let h = `<h1>${t("nodes.title")}</h1><div class="sub">${t("nodes.sub", { cmd: join })}</div><div class="grid">`;
  h += ns.nodes.map(n => { const info = n.info || {}; return `<div class="card"><div class="row"><h3>🖥 ${esc(n.name)}</h3><span class="spacer"></span>${pd(n.hubd)}<span class="muted">hubd ${n.hubd}</span></div>
    <div class="muted" style="font-size:12px">${esc(n.ip)} · ${esc(n.os)} · ${nodeAgents(info)} · hubd ${esc(info.hubd || "—")}</div>
    <div style="margin-top:8px">${n.agents.filter(a => !isFolded(a)).map(a => `<div class="row node-agent" style="gap:6px">${pd(a.presence)}<a href="#/agent/${encodeURIComponent(a.name)}">${esc(a.name)}</a>${kchip(a)}<span class="muted" style="font-size:12px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(short(a.workspace))}</span>${copyBtn(a, "resume")}</div>`).join("") || `<span class="muted">${t("nodes.noAgents")}</span>`}</div>
    ${foldedList("nodes:" + n.name, n.agents.filter(isFolded), "resume")}
    ${(info.tmux || []).length ? `<div style="margin-top:8px" class="muted">tmux: ${info.tmux.map(esc).join(", ")}</div>` : ""}</div>`; }).join("");
  h += `</div><h2>${t("nodes.orders")}</h2><div class="list">${os.orders.slice(0, 25).map(o => `<div class="li"><span class="id">${o.id}</span><span class="chip">${esc(o.kind)}</span>
    <span class="title">${esc(o.agent)} @ ${esc(o.node)} <span class="muted">${esc(o.reason)} ${o.result ? "· " + esc(o.result.slice(0, 120)) : ""}</span></span>
    <span class="meta">${esc(o.state)} · ${ago(o.created_at)}</span></div>`).join("") || `<div class="li muted">—</div>`}</div>`;
  return h;
};

function feedList(events) {
  if (!events || !events.length) return `<div class="empty">${t("feed.empty")}</div>`;
  return `<div class="list feed">${events.map(e => `<div class="li"><span class="kind-badge">${esc(e.kind)}</span>
    <div class="title" style="white-space:normal"><details><summary><b>${esc(e.agent || "")}</b> ${esc(e.title)}</summary>
    ${e.body ? `<div class="ebody muted"><pre>${rich(e.body)}</pre></div>` : ""}</details></div>
    <span class="meta">${e.project ? esc(e.project) + " · " : ""}${ago(e.ts)}</span></div>`).join("")}</div>`;
}

views.activity = async (_, params) => {
  const q = new URLSearchParams({ limit: 80 });
  ["project", "kind", "agent"].forEach(k => params.get(k) && q.set(k, params.get(k)));
  const [ev, ps] = await Promise.all([api("/v1/events?" + q), api("/v1/projects")]);
  const setp = (k, v) => { const n = new URLSearchParams(params); v ? n.set(k, v) : n.delete(k); return "#/activity?" + n; };
  return `<h1>${t("activity.title")}</h1><div class="sub">${t("activity.sub")}</div>
    <div class="filters">${["", "finding", "decision", "artifact", "status", "note", "handoff", "question"].map(k => `<button class="${(params.get("kind") || "") === k ? "on" : ""}" onclick="go('${setp("kind", k)}')">${k || t("common.all")}</button>`).join("")}
    <select onchange="go(this.value)"><option value="${setp("project", "")}">${t("common.allProjects")}</option>${ps.projects.map(p => `<option value="${setp("project", p.name)}" ${p.name === params.get("project") ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>` + feedList(ev.events);
};

views.skills = async () => {
  const s = await api("/v1/skills");
  return `<h1>${t("nav.skills")}</h1><div class="sub">${t("skills.sub", { url: esc(location.origin) + "/marketplace.git" })}</div>
    <div class="list">${s.skills.map(k => `<div class="li"><span class="chip">${esc(k.plugin)}</span><span class="title" style="white-space:normal"><b>${esc(k.skill)}</b> <span class="muted">${esc(k.description)}</span></span><span class="meta">v${esc(k.version)} · ${t("skills.files", { n: k.files.length })}</span></div>`).join("")}</div>
    <h2>${t("skills.history")}</h2><div class="list">${(s.history || []).map(l => `<div class="li"><code>${esc(l)}</code></div>`).join("")}</div>`;
};

views.search = async (_, params) => {
  const term = params.get("q") || "";
  const r = await api("/v1/search?q=" + encodeURIComponent(term));
  return `<h1>${t("search.title", { q: esc(term) })}</h1><h2>${t("search.tasks")} <span class="count">${r.tasks.length}</span></h2>
    ${r.tasks.length ? `<div class="list">${r.tasks.map(x => taskRow(Object.assign({ children: 0 }, x))).join("")}</div>` : `<div class="empty">${t("common.none")}</div>`}
    ${(r.notes || []).length ? `<h2>${t("search.shared")} <span class="count">${r.notes.length}</span></h2><div class="list">${r.notes.map(n => `<div class="li link" onclick="go('#/shared')"><span class="chip">${n.type === "secret" ? t("search.secret") : t("search.note")}</span><span class="title"><b>${esc(n.name)}</b> <span class="muted">${esc(n.title || "")}</span></span></div>`).join("")}</div>` : ""}
    <h2>${t("search.events")} <span class="count">${r.events.length}</span></h2>${feedList(r.events)}`;
};

// guide.<lang>.html, falling back to the single-language guide.html of older hubs
async function fetchGuide() {
  for (const p of [`/static/guide.${LANG}.html`, "/static/guide.html"]) {
    const r = await fetch(p);
    if (r.status === 401) { showLogin(); throw new Error(t("auth.required")); }
    if (r.ok) return r.text();
    if (r.status !== 404) throw new Error(r.statusText);
  }
  throw new Error("guide not found");
}
views.guide = async () => {
  const [html, tg] = await Promise.all([fetchGuide(), api("/v1/telegram")]);
  const bot = tg.bot ? `<a href="https://t.me/${esc(tg.bot)}" target="_blank">@${esc(tg.bot)}</a>` : t("tg.yourBot");
  const tgBox = !tg.enabled ? `<div class="callout">${t("tg.disabled")}</div>`
    : tg.paired ? `<div class="callout">${t("tg.paired", { bot: tg.bot ? (LANG === "zh" ? `（@${esc(tg.bot)}）` : ` (@${esc(tg.bot)})`) : "" })}<button class="small" onclick="if(confirm(t('tg.unpairQ')))act(()=>api('/v1/telegram',{unpair:true}),t('tg.unpaired'))">${t("tg.unpair")}</button></div>`
    : `<div class="callout">${t("tg.pair", { bot })}<pre>/pair ${esc(tg.code)}</pre>${t("tg.pairAfter")}</div>`;
  return `<div class="guide">` + html.replaceAll("{{HUB}}", esc(location.origin)).replace("{{TELEGRAM}}", tgBox) + `</div>`;
};

/* ------------------------------------------------------------ card order (drag and drop) */

// placed cards (position set by a drag) first, in their order; the rest keep the server's order after them
const byPos = (a, b) => (a.position ?? Infinity) - (b.position ?? Infinity) || 0;
let dragEl = null;
document.addEventListener("dragstart", e => {
  const el = e.target.closest && e.target.closest("[data-sortlist] > [draggable]");
  if (!el) return;
  dragEl = el; el.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", el.dataset.key);
});
document.addEventListener("dragover", e => {
  if (!dragEl) return;
  const over = e.target.closest && e.target.closest("[data-sortlist] > [draggable]");
  if (!over || over.parentNode !== dragEl.parentNode) return;   // only within the same list (agents: same node)
  e.preventDefault();
  if (over === dragEl) return;
  const r = over.getBoundingClientRect();
  // grid wraps: compare on the axis the cards are laid out along in that row
  const after = Math.abs(r.top - dragEl.getBoundingClientRect().top) < r.height / 2
    ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2;
  over.parentNode.insertBefore(dragEl, after ? over.nextSibling : over);
});
document.addEventListener("drop", e => { if (dragEl) e.preventDefault(); });
document.addEventListener("dragend", () => {
  if (!dragEl) return;
  const list = dragEl.parentNode, names = [...list.children].map(c => c.dataset.key);
  dragEl.classList.remove("dragging"); dragEl = null;
  api("/v1/order", { kind: list.dataset.sortlist, names }).catch(e => { toast("⚠️ " + e.message); render(); });
  if (pending) { pending = false; setTimeout(render, 200); }
});

/* ------------------------------------------------------------ router */

window.go = h => { if (location.hash === h) render(); else location.hash = h; };
let rendering = false, pending = false;
async function render() {
  if (rendering) { pending = true; return; }
  // do not wipe a half-written answer
  const ae = document.activeElement;
  if (ae && (ae.tagName === "TEXTAREA" || ae.tagName === "INPUT") && ae.value && ae.id !== "search") { pending = true; return; }
  if (!$("#modal").classList.contains("hidden")) { pending = true; return; }
  if (dragEl) { pending = true; return; }   // a re-render mid-drag would drop the card being dragged
  rendering = true;
  const [path, qs] = (location.hash.slice(1) || "/").split("?");
  const params = new URLSearchParams(qs || "");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const name = parts[0] || "home";
  const fn = views[name] || views.home;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on",
    a.dataset.v === name || (name === "project" && a.dataset.v === "projects") || (name === "task" && a.dataset.v === "tasks") || (name === "agent" && a.dataset.v === "agents")));
  try {
    const y = window.scrollY;
    const html = await fn(parts[1], params);
    app.innerHTML = html;
    if (render.lastPath === location.hash) window.scrollTo(0, y); else window.scrollTo(0, 0);
    render.lastPath = location.hash;
  } catch (e) {
    app.innerHTML = `<div class="empty">⚠️ ${esc(e.message)}</div>`;
  }
  rendering = false;
  if (pending) { pending = false; setTimeout(render, 300); }
}
window.addEventListener("hashchange", render);
document.addEventListener("focusout", () => { if (pending) { pending = false; setTimeout(render, 200); } });
const _close = closeModal;
window.closeModal = () => { _close(); if (pending) { pending = false; render(); } };
$("#search").addEventListener("keydown", e => { if (e.key === "Enter" && e.target.value.trim()) go("#/search?q=" + encodeURIComponent(e.target.value.trim())); });

async function badge() {
  try { const o = await api("/v1/overview"); $("#att-count").textContent = o.attention.length || ""; } catch (e) { /* offline */ }
}
function stream() {
  const es = new EventSource("/v1/stream");
  let tm;
  es.addEventListener("change", () => { clearTimeout(tm); tm = setTimeout(() => { render(); badge(); }, 250); });
  es.onopen = () => $("#conn").classList.add("on");
  es.onerror = () => {
    $("#conn").classList.remove("on");
    // an HTTP error (e.g. 401) closes the stream for good; probe through api() so a logged-out page gets the (single) login modal
    if (es.readyState === EventSource.CLOSED) badge();
  };
}
/* light / dark toggle: defaults to the system, remembers an explicit choice */
const darkMQ = window.matchMedia("(prefers-color-scheme: dark)");
const isDark = () => (document.documentElement.dataset.theme || (darkMQ.matches ? "dark" : "light")) === "dark";
function paintThemeBtn() { $("#theme").textContent = isDark() ? "☀️" : "🌙"; $("#theme").title = isDark() ? t("theme.toLight") : t("theme.toDark"); }
$("#theme").addEventListener("click", () => {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("hub-theme", next); } catch (e) { /* private mode */ }
  paintThemeBtn();
});
darkMQ.addEventListener("change", paintThemeBtn);

/* language: an explicit choice (localStorage), else the hub's default (/v1/config), else the browser */
function storedLang() { try { const l = localStorage.getItem("hub-lang"); return I18N[l] ? l : ""; } catch (e) { return ""; } }
const browserLang = () => /^zh/i.test(navigator.language || "") ? "zh" : "en";
async function pickLang() {
  const s = storedLang();
  if (s) return s;
  try { const r = await fetch("/v1/config"); if (r.ok) { const c = await r.json(); if (I18N[c.lang]) return c.lang; } } catch (e) { /* old hub */ }
  return browserLang();
}
function applyLang() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach(el => el.textContent = t(el.dataset.i18n));
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => el.placeholder = t(el.dataset.i18nPlaceholder));
  document.querySelectorAll("[data-i18n-title]").forEach(el => el.title = t(el.dataset.i18nTitle));
  $("#lang").textContent = t("lang.btn");
  $("#lang").title = t("lang.toggle");
  paintThemeBtn();
}
$("#lang").addEventListener("click", () => {
  LANG = LANG === "en" ? "zh" : "en";
  try { localStorage.setItem("hub-lang", LANG); } catch (e) { /* private mode */ }
  applyLang(); render();
});

LANG = storedLang() || browserLang(); applyLang();   // first paint, before /v1/config answers
pickLang().then(l => {
  if (l !== LANG) { LANG = l; applyLang(); }
  api("/v1/agents").then(d => agentCache = d.agents).catch(() => {});
  render(); badge();
  if (!/nosse/.test(location.search)) stream();
});
