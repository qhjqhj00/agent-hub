"""Human-facing text in the hub's language (HUB_LANG: en | zh).

Only what a person reads: the chat bot, notifications, attention items, a few
errors. What agents read (task texts, tool replies) stays English: models
follow it best, whatever language the team speaks.
"""
import os

LANGS = ("en", "zh")

S = {
    "tg.help": {
        "en": """<b>agent-hub</b>
/status — what needs you
/agents — all agents (by node)
/projects — projects
/tasks [project] — open tasks
/task &lt;id&gt; — task details
/delegate &lt;agent|role@project&gt; &lt;title&gt;
  (goal / context on the next lines)
/tell &lt;agent&gt; &lt;message&gt; — push into an agent's session
/wake &lt;agent&gt; [prompt] · /stop &lt;agent&gt; · /peek &lt;agent&gt; (see its screen)
/done &lt;id&gt; · /drop &lt;id&gt; · /reopen &lt;id&gt;
<b>Reply</b> to a notification = answer in that task / message that agent""",
        "zh": """<b>agent-hub</b>
/status — 需要你处理的事
/agents — 所有 agent（按节点）
/projects — 项目
/tasks [project] — 进行中的任务
/task &lt;id&gt; — 任务详情
/delegate &lt;agent|role@project&gt; &lt;标题&gt;
  （换行后写目标/上下文）
/tell &lt;agent&gt; &lt;消息&gt; — 推送到 agent 会话
/wake &lt;agent&gt; [提示] · /stop &lt;agent&gt; · /peek &lt;agent&gt;（看屏幕）
/done &lt;id&gt; · /drop &lt;id&gt; · /reopen &lt;id&gt;
直接<b>回复</b>某条通知 = 在那个任务里回答 / 给那个 agent 发消息"""},
    "tg.perm": {"en": "🔐 <b>{agent}</b> wants to use <code>{tool}</code>\n{desc}\n<pre>{preview}</pre>",
                "zh": "🔐 <b>{agent}</b> 请求使用 <code>{tool}</code>\n{desc}\n<pre>{preview}</pre>"},
    "tg.allow": {"en": "✅ Allow", "zh": "✅ 允许"},
    "tg.deny": {"en": "⛔ Deny", "zh": "⛔ 拒绝"},
    "tg.accept": {"en": "✅ Accept", "zh": "✅ 验收"},
    "tg.send_back": {"en": "↩️ Send back (reply with what to change)", "zh": "↩️ 打回（回复本条说明）"},
    "tg.perm_allowed": {"en": "✅ Allowed permission request #{id}", "zh": "✅ 已允许 权限请求 #{id}"},
    "tg.perm_denied": {"en": "⛔ Denied permission request #{id}", "zh": "⛔ 已拒绝 权限请求 #{id}"},
    "tg.perm_already": {"en": "That request is already {state}", "zh": "该请求已是 {state}"},
    "tg.accepted": {"en": "✅ Task #{id} accepted", "zh": "✅ 任务 #{id} 已验收"},
    "tg.how_send_back": {"en": "Reply to that result message with what to change; it goes back to the agent.",
                         "zh": "回复那条结果消息，写下要改什么，就会打回给 agent。"},
    "tg.pair_hint": {"en": "Send /pair <code> — the code is on the hub dashboard (Guide tab).",
                     "zh": "发送 /pair <code> —— 配对码在 hub 看板的「使用说明」页。"},
    "tg.paired": {"en": "✅ Paired.\n\n", "zh": "✅ 已绑定。\n\n"},
    "tg.wrong_code": {"en": "wrong code", "zh": "配对码不对"},
    "tg.no_ref": {"en": "That message is not about a task or an agent. Use /tell or /delegate.",
                  "zh": "这条消息不对应任何任务或 agent。用 /tell 或 /delegate。"},
    "tg.wrote_task": {"en": "↪️ Added to task #{id}", "zh": "↪️ 已写入任务 #{id}"},
    "tg.sent_to": {"en": "↪️ Sent to {agent}", "zh": "↪️ 已发给 {agent}"},
    "tg.status_head": {"en": "<b>Live agents</b> {live} · <b>open tasks</b> {open}",
                       "zh": "<b>在线 agent</b> {live} · <b>进行中任务</b> {open}"},
    "tg.all_clear": {"en": "\nNothing needs you ✨", "zh": "\n没有需要你处理的事 ✨"},
    "tg.no_tasks": {"en": "No open tasks", "zh": "没有进行中的任务"},
    "tg.goal": {"en": "Goal", "zh": "目标"},
    "tg.usage_delegate": {"en": "Usage: /delegate &lt;agent|role@project&gt; &lt;title&gt;\n(goal on the next lines)",
                          "zh": "用法：/delegate &lt;agent|role@project&gt; &lt;标题&gt;\n（换行写目标）"},
    "tg.delegated": {"en": "📤 Task #{id} sent to {to}", "zh": "📤 任务 #{id} 已派给 {to}"},
    "tg.usage_tell": {"en": "Usage: /tell &lt;agent&gt; &lt;message&gt;", "zh": "用法：/tell &lt;agent&gt; &lt;消息&gt;"},
    "tg.told": {"en": "💬 Pushed to {to}", "zh": "💬 已推送给 {to}"},
    "tg.waking": {"en": "⏰ Asked {name}'s node to wake it", "zh": "⏰ 已让 {name} 的节点唤醒它"},
    "tg.screen": {"en": "🖥 <b>{name}</b>'s screen:\n<pre>{screen}</pre>", "zh": "🖥 <b>{name}</b> 的屏幕：\n<pre>{screen}</pre>"},
    "tg.stopping": {"en": "🛑 Asked the node to stop {name}", "zh": "🛑 已让节点停止 {name}"},
    "tg.closed": {"en": "Task #{id} → {state}", "zh": "任务 #{id} → {state}"},
    "tg.cmd.status": {"en": "what needs you", "zh": "需要你处理的事"},
    "tg.cmd.agents": {"en": "all agents", "zh": "所有 agent"},
    "tg.cmd.projects": {"en": "projects", "zh": "项目"},
    "tg.cmd.tasks": {"en": "open tasks", "zh": "进行中的任务"},
    "tg.cmd.task": {"en": "task details <id>", "zh": "任务详情 <id>"},
    "tg.cmd.delegate": {"en": "hand out a task", "zh": "派任务"},
    "tg.cmd.tell": {"en": "message an agent", "zh": "给 agent 发消息"},
    "tg.cmd.wake": {"en": "wake an agent", "zh": "唤醒 agent"},
    "tg.cmd.stop": {"en": "stop an agent", "zh": "停止 agent"},
    "tg.cmd.peek": {"en": "see an agent's screen", "zh": "看 agent 的屏幕"},
    "tg.cmd.done": {"en": "accept a task", "zh": "验收任务"},
    "tg.cmd.help": {"en": "help", "zh": "帮助"},
    "att.stalled": {"en": "Delivered to {owner}, but no response (no plan) for {ago}. Check its screen: it may be stuck on a dialog.",
                    "zh": "已送达 {owner}，但 {ago} 内没有回应（没发计划）。看看它的屏幕是不是卡在某个对话框上。"},
    "task.renotified": {"en": "(pushed again)", "zh": "（重新推送）"},
    "err.hubd_old": {"en": "hubd on {node} is {have}, too old to show the screen. Re-run the installer on {node}.",
                     "zh": "{node} 上的 hubd 是 {have}，太旧，不支持查看屏幕。在 {node} 上重新运行一次安装脚本。"},
}


def lang():
    v = (os.environ.get("HUB_LANG") or "en").lower()
    return "zh" if v.startswith("zh") else "en"


def t(key, **kw):
    s = S[key].get(lang()) or S[key]["en"]
    return s.format(**kw) if kw else s
