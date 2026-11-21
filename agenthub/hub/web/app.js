/* agent-hub dashboard: three views (Agents, Tasks, Board), hash routing, and one SSE
 * stream that only says "something changed" -- the current view re-fetches.
 * Mostly for looking. Writes: copy a start command, close / send back a task, set a
 * project's liaison, fix an agent's kind (the last three need the admin token). */
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KINDS = ["claude", "agy", "codex", "pi", "other"];
const STATE = { open: "待接", working: "进行中", review: "待验收", done: "完成", cancelled: "取消" };
let CFG = { admin: false };

async function api(path, body, method) {
  const r = await fetch(path, {
    method: method || (body ? "POST" : "GET"), credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(out.error || r.statusText), { status: r.status });
  return out;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.remove("on"), 1800);
}

async function act(fn, ok) {
  try { await fn(); if (ok) toast(ok); render(); } catch (e) { toast("失败：" + e.message); }
}

function ago(ts) {
  if (!ts) return "从未";
  const s = (Date.now() - new Date(ts)) / 1000;
  if (s < 60) return "刚刚";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  if (s < 86400) return Math.floor(s / 3600) + " 小时前";
  return Math.floor(s / 86400) + " 天前";
}

const short = p => (p || "").replace(/^\/home\/[^/]+/, "~").replace(/^\/Users\/[^/]+/, "~");
const st = s => `<span class="st ${s}">${STATE[s] || s}</span>`;
const avatar = n => {
  let h = 0;
  for (const c of n || "?") h = (h * 31 + c.charCodeAt(0)) % 360;
  return `<span class="av" style="background:hsl(${h} 45% 52%)">${esc((n || "?").replace(/^sa-/, "").slice(0, 2))}</span>`;
};

window.copyCmd = async (btn) => {
  const text = btn.parentElement.querySelector("code").textContent;
  try { await navigator.clipboard.writeText(text); } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("已复制启动命令");
};

/* ------------------------------------------------------------------ Agents */

function agentCard(a, project) {
  const off = a.presence !== "live";
  const liaison = a.liaison_of.includes(project);
  const kindCtl = CFG.admin
    ? `<select class="btn" onchange="setKind('${esc(a.name)}', this.value)">${KINDS.map(k => `<option ${k === a.kind ? "selected" : ""}>${k}</option>`).join("")}</select>`
    : `<span class="chip">${esc(a.kind)}</span>`;
  return `<div class="card ${off ? "offline" : ""}">
    <div class="row"><span class="dot ${a.presence}"></span><span class="name">${esc(a.name)}</span>${kindCtl}
      ${a.presence === "live" && a.state ? `<span class="chip ${a.state}">${a.state === "busy" ? "忙" : "闲"}</span>` : ""}
      ${liaison ? `<span class="chip liaison">联络人</span>` : ""}
      ${a.stale_queued ? `<span class="chip stale" title="未送达超过 2 分钟">${a.stale_queued} 条滞留 ${secs(a.oldest_queued_s)}</span>`
        : a.pending ? `<span class="chip" title="待投递">${a.pending} 条待收</span>` : ""}
      <span class="grow"></span><span class="meta">${a.presence === "live" ? "在线" : ago(a.last_seen)}</span></div>
    <div class="meta">${esc(a.node)} · ${esc(short(a.workspace))} · ${esc(a.scopes.filter(s => s.project === project).map(s => s.role).filter(Boolean).join(", ") || "")}</div>
    ${a.description ? `<div class="desc">${esc(a.description)}</div>` : ""}
    ${a.current_task ? `<div class="now">${st(a.current_task.state)} <a href="#/task/${a.current_task.id}"><span class="id">#${a.current_task.id}</span> ${esc(a.current_task.title)}</a></div>` : ""}
    <div class="cmd"><code title="${esc(a.start)}">${esc(a.start)}</code><button class="btn" onclick="copyCmd(this)">复制</button>
      ${CFG.admin ? `<button class="btn" onclick="archive('${esc(a.name)}', ${!a.archived})">${a.archived ? "恢复" : "归档"}</button>` : ""}</div>
  </div>`;
}

window.archive = (name, on) => act(() => api("/v1/admin/agents/" + encodeURIComponent(name), { archived: on }),
  on ? `已归档 ${name}` : `已恢复 ${name}`);

window.setKind = (name, kind) => act(() => api("/v1/admin/agents/" + encodeURIComponent(name), { kind }), "已更新类型");
window.setLiaison = (project, liaison) => act(() => api("/v1/admin/projects/" + encodeURIComponent(project), { liaison }), "已设置联络人");

const secs = s => s < 90 ? s + " 秒" : s < 5400 ? Math.round(s / 60) + " 分钟" : Math.round(s / 3600) + " 小时";

/* What needs a person, first: results to review, questions waiting, deliveries stuck. Empty = no box. */
function attentionBox(att) {
  const rows = [];
  for (const a of att.stale) rows.push(`<div class="att stale"><span class="tag">投递滞留</span>
    <span class="grow"><b>${esc(a.name)}</b> 有 ${a.stale_queued} 条消息 ${secs(a.oldest_queued_s)} 没送进去
    <span class="meta">${a.presence === "live" ? (a.push === "bridge" ? "· 检查这台机器上的 bridge" : "· 会话在线但没取消息") : "· agent 不在线"} · ${esc(a.node)}</span></span></div>`);
  for (const t of att.questions) rows.push(`<a class="att question" href="#/task/${t.id}"><span class="tag">待答</span>
    <span class="grow"><span class="id">#${t.id}</span> ${esc(t.title)}<span class="meta"> · ${esc(t.asked_by)} 问${t.recipient === "human" ? "你" : ""}：${esc((t.body || "").split("\n")[0].slice(0, 80))}</span></span></a>`);
  for (const t of att.review) rows.push(`<a class="att review" href="#/task/${t.id}"><span class="tag">待验收</span>
    <span class="grow"><span class="id">#${t.id}</span> ${esc(t.title)}<span class="meta"> · ${esc(t.owner)} → ${esc(t.created_by)} · ${ago(t.updated_at)}</span></span></a>`);
  return rows.length ? `<section class="attention"><h2>需要处理 <span class="sub">${rows.length}</span></h2>${rows.join("")}</section>` : "";
}

/* Busy, carrying work, or with something queued: a full card. Everyone else: one quiet line. */
const needsCard = a => a.presence === "live" || a.pending || (a.current_task && a.current_task.state !== "open");

function agentRow(a, project) {
  return `<div class="arow"><span class="dot ${a.presence}"></span><span class="name">${esc(a.name)}</span>
    <span class="chip">${esc(a.kind)}</span>${a.liaison_of.includes(project) ? `<span class="chip liaison">联络人</span>` : ""}
    <span class="grow meta">${esc(short(a.workspace))}</span><span class="meta">${ago(a.last_seen)}</span>
    <span class="cmd"><code hidden>${esc(a.start)}</code><button class="btn" onclick="copyCmd(this)" title="${esc(a.start)}"><span class="long">复制启动命令</span><span class="short">复制</span></button></span>
    ${CFG.admin ? `<button class="btn" onclick="archive('${esc(a.name)}', true)">归档</button>` : ""}</div>`;
}

async function viewAgents() {
  const o = await api("/v1/overview");
  const projects = o.projects.filter(p => p.agents.length || Object.values(p.tasks).some(Boolean));
  const liveIn = p => p.agents.filter(x => x.presence === "live").length;
  projects.sort((a, b) => liveIn(b) - liveIn(a) || a.name.localeCompare(b.name));
  const live = projects.reduce((n, p) => n + liveIn(p), 0);
  let h = `<h1>Agents <span class="sub">${live} 个在线</span><span class="grow"></span>${o.archived ? `<a class="sub" href="#/archived">已归档 ${o.archived} →</a>` : ""}</h1>`;
  h += attentionBox(o.attention);
  const section = (title, agents, project, extra) => {
    agents = [...agents].sort((a, b) => (b.presence === "live") - (a.presence === "live") || a.name.localeCompare(b.name));
    const cards = agents.filter(needsCard), quiet = agents.filter(a => !needsCard(a));
    let x = `<h2>${title}<span class="grow"></span>${extra || ""}</h2>`;
    if (cards.length) x += `<div class="grid">${cards.map(a => agentCard(a, project)).join("")}</div>`;
    if (quiet.length) x += `<div class="quiet">${quiet.map(a => agentRow(a, project)).join("")}</div>`;
    return x;
  };
  for (const p of projects) {
    const active = p.tasks.open + p.tasks.working + p.tasks.review;
    const liaisonCtl = CFG.admin
      ? `<select class="btn" onchange="setLiaison('${esc(p.name)}', this.value)"><option value="">（无联络人）</option>${p.agents.map(a => `<option ${a.name === p.liaison ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>`
      : (p.liaison ? `<span class="chip liaison">联络人 ${esc(p.liaison)}</span>` : `<span class="chip">无联络人</span>`);
    h += section(`${esc(p.label || p.name)} <a class="sub" href="#/tasks?project=${encodeURIComponent(p.name)}">${active ? active + " 个进行中的任务" : "没有进行中的任务"}</a>`,
      p.agents, p.name, liaisonCtl);
  }
  if (o.unassigned.length) h += section("未加入项目", o.unassigned, "");
  return h;
}

async function viewArchived(q) {
  const f = (q.get("q") || "").toLowerCase();
  const { agents } = await api("/v1/agents?archived=1");
  const rows = agents.filter(a => !f || (a.name + " " + a.workspace + " " + a.scopes.map(s => s.project).join(" ")).toLowerCase().includes(f))
    .sort((a, b) => (b.presence === "live") - (a.presence === "live") || (b.last_seen || "").localeCompare(a.last_seen || ""));
  const live = rows.filter(a => a.presence === "live").length;
  return `<h1>已归档 <span class="sub">${agents.length} 个 · 不在 Agents 页显示，会话、任务和历史都保留，消息照常投递</span></h1>
    <div class="filters"><input class="btn" placeholder="按名字 / 目录 / 项目筛选" value="${esc(q.get("q") || "")}"
      onkeydown="if(event.key==='Enter')location.hash='#/archived?q='+encodeURIComponent(this.value)"><a class="btn" href="#/">← 返回</a></div>
    ${live ? `<p class="meta">其中 ${live} 个现在在线（归档的 agent 重新注册不会自动恢复，要手动点“恢复”）。</p>` : ""}
    <div class="grid">${rows.map(a => agentCard(a, (a.scopes[0] || {}).project || "")).join("") || `<div class="empty">没有</div>`}</div>`;
}

/* ------------------------------------------------------------------ Tasks */

function taskItem(t) {
  return `<a class="task" href="#/task/${t.id}"><div class="row"><span class="id">#${t.id}</span>${st(t.state)}<span class="grow"></span><span class="meta">${ago(t.updated_at)}</span></div>
    <div class="title">${esc(t.title)}</div>
    <div class="meta">${esc(t.created_by)} → ${esc(t.owner || t.role + "@" + t.project)}${t.project ? " · " + esc(t.project) : ""}</div></a>`;
}

async function viewTasks(q) {
  const project = q.get("project") || "";
  const qs = s => `/v1/tasks?state=${s}${project ? "&project=" + encodeURIComponent(project) : ""}&limit=60`;
  const [act_, done] = await Promise.all([api(qs("open,working,review")), api(qs("done,cancelled"))]);
  const projects = (await api("/v1/overview")).projects.map(p => p.name);
  const col = (title, rows) => `<div class="col"><h2>${title} <span class="sub">${rows.length}</span></h2>${rows.map(taskItem).join("") || `<div class="meta">—</div>`}</div>`;
  const by = s => act_.tasks.filter(t => t.state === s);
  return `<h1>任务</h1>
    <div class="filters"><select class="btn" onchange="location.hash='#/tasks'+(this.value?'?project='+encodeURIComponent(this.value):'')">
      <option value="">全部项目</option>${projects.map(p => `<option ${p === project ? "selected" : ""}>${esc(p)}</option>`).join("")}</select></div>
    <div class="cols">${col("待验收", by("review"))}${col("进行中", by("working"))}${col("待接", by("open"))}${col("最近完成", done.tasks.slice(0, 20))}</div>`;
}

window.closeTask = (id, state) => act(() => api(`/v1/admin/tasks/${id}`, { kind: "close", state, body: $("#reply")?.value || "" }),
  state === "done" ? "已验收" : "已更新");
window.sendBack = id => act(() => {
  const body = $("#reply").value.trim();
  if (!body) throw new Error("写一下要改什么");
  return api(`/v1/admin/tasks/${id}`, { kind: "comment", body });
}, "已打回");

async function viewTask(id) {
  const { task: t, posts } = await api("/v1/tasks/" + id);
  const kv = [["项目", t.project], ["委派", `${t.created_by} → ${t.owner || t.role + "@" + t.project}`], ["目标", t.goal],
    ["背景", t.context], ["验收标准", t.acceptance]].filter(x => x[1]);
  const small = new Set(["task", "closed", "reopened", "created"]);
  let h = `<h1><span class="id">#${t.id}</span> ${esc(t.title)} ${st(t.state)}</h1>
    <dl class="kv">${kv.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}
    ${t.parent_id ? `<dt>上级</dt><dd><a href="#/task/${t.parent_id}">#${t.parent_id}</a></dd>` : ""}
    ${t.children.length ? `<dt>子任务</dt><dd>${t.children.map(c => `<a href="#/task/${c.id}">#${c.id} ${esc(c.title)}</a> ${st(c.state)}`).join("<br>")}</dd>` : ""}</dl>`;
  if (CFG.admin && ["open", "working", "review"].includes(t.state)) {
    h += `<div class="actions" style="flex-direction:column"><textarea id="reply" placeholder="备注 / 打回时写要改什么"></textarea>
      <div class="actions" style="margin:0">${t.state === "review" ? `<button class="btn primary" onclick="closeTask(${t.id},'done')">验收通过</button><button class="btn" onclick="sendBack(${t.id})">打回</button>` : ""}
      <button class="btn" onclick="closeTask(${t.id},'cancelled')">取消任务</button></div></div>`;
  }
  h += `<h2>线程 <span class="sub">${posts.length}</span></h2><div class="thread">${posts.map(p => small.has(p.kind)
    ? `<div class="post small"><span></span><div class="body">${esc(p.sender)} · ${esc(p.kind)} ${esc(p.body)} · ${ago(p.ts)}</div></div>`
    : `<div class="post ${esc(p.kind)}">${avatar(p.sender)}<div class="body"><div class="row"><b>${esc(p.sender)}</b><span class="chip">${esc(p.kind)}</span>${p.recipient ? `<span class="meta">→ ${esc(p.recipient)}</span>` : ""}<span class="grow"></span><span class="meta">${ago(p.ts)}</span></div><pre>${esc(p.body)}</pre></div></div>`).join("")}</div>`;
  return h;
}

/* ------------------------------------------------------------------ Board */

async function viewBoard() {
  const b = await api("/v1/board");
  const skills = (b.skills && b.skills.skills) || [];
  return `<h1>看板</h1>
    <h2>密钥 <span class="sub">${b.secrets.length} · 只显示名字和用途</span></h2>
    <div class="card list">${b.secrets.map(s => `<div class="item"><span class="mono">${esc(s.name)}</span>
      <span class="grow meta" title="${esc(s.note)}">${esc((s.note || "").split("\n")[0])}</span>
      ${s.tags.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>`).join("") || `<div class="empty">无</div>`}</div>
    <h2>说明 <span class="sub">${b.notes.length}</span></h2>
    <div class="card list">${b.notes.map(n => `<a class="item" href="#/note/${encodeURIComponent(n.name)}"><span class="mono">${esc(n.name)}</span>
      <span class="grow meta">${esc(n.title)}</span>${n.tags.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</a>`).join("") || `<div class="empty">无</div>`}</div>
    <h2>技能 <span class="sub">${skills.length}</span></h2>
    <div class="card list">${skills.map(s => `<div class="item"><span class="mono">${esc(s.skill)}</span><span class="grow meta">${esc(s.description)}</span></div>`).join("") || `<div class="empty">无</div>`}</div>`;
}

async function viewNote(name) {
  const n = await api("/v1/board/note/" + encodeURIComponent(name));
  return `<h1>${esc(n.title || n.name)} <span class="sub">${esc(n.name)} · v${n.version} · ${esc(n.owner)} · ${ago(n.updated_at)}</span></h1>
    ${n.refs.length ? `<p class="meta">用到的密钥：${n.refs.map(esc).join(", ")}</p>` : ""}<div class="note-body">${esc(n.body)}</div>`;
}

/* ------------------------------------------------------------------ shell */

function loginView(msg) {
  return `<div class="card login"><h1>登录</h1><p class="meta">${esc(msg || "")} 在 hub 所在机器上运行 <code>agenthub token</code> 拿到管理员 token。</p>
    <input class="btn" id="tok" type="password" placeholder="token" onkeydown="if(event.key==='Enter')login()"><button class="btn primary" onclick="login()">登录</button></div>`;
}
window.login = () => act(async () => { await api("/v1/login", { token: $("#tok").value.trim() }); CFG = await api("/v1/config"); location.hash = "#/"; });

let seq = 0;
async function render() {
  const [path, query] = (location.hash.slice(1) || "/").split("?");
  const q = new URLSearchParams(query || "");
  const parts = path.split("/").filter(Boolean);
  const v = parts[0] || "agents";
  document.querySelectorAll(".top nav a").forEach(a => a.classList.toggle("on", a.dataset.v === (v === "task" ? "tasks" : v === "note" ? "board" : v === "archived" ? "agents" : v)));
  const my = ++seq;
  let html;
  try {
    html = v === "tasks" ? await viewTasks(q) : v === "task" ? await viewTask(parts[1]) : v === "board" ? await viewBoard()
      : v === "note" ? await viewNote(decodeURIComponent(parts[1])) : v === "login" ? loginView()
      : v === "archived" ? await viewArchived(q) : await viewAgents();
    if (!CFG.admin && v !== "login") html += `<p class="meta" style="margin-top:40px">只读模式。<a href="#/login" style="color:var(--accent)">用管理员 token 登录</a>后可以验收任务、设置联络人。</p>`;
  } catch (e) {
    html = e.status === 401 ? loginView("需要登录。") : `<div class="empty">出错了：${esc(e.message)}</div>`;
  }
  if (my !== seq) return;
  const app = $("#app"), y = scrollY, typing = document.activeElement && document.activeElement.id === "reply";
  if (typing) return; // do not wipe what someone is writing; the next change will refresh
  app.innerHTML = html;
  scrollTo(0, y);
}

function live() {
  const es = new EventSource("/v1/stream");
  let t;
  es.addEventListener("change", () => { clearTimeout(t); t = setTimeout(render, 300); });
  es.onopen = () => $("#conn").classList.add("ok");
  es.onerror = () => $("#conn").classList.remove("ok");
}

window.addEventListener("hashchange", () => { scrollTo(0, 0); render(); });
(async () => {
  try { CFG = await api("/v1/config"); } catch (e) {}
  render();
  if (CFG.authenticated) live();
})();
