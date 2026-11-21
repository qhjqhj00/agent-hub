/**
 * agent-hub v2 extension for pi coding agent (`~/.pi/agent/extensions/agent-hub.ts`).
 *
 * Capabilities:
 * - Registers all 9 v2 `hub_*` tools natively via `pi.registerTool()`.
 * - Runs an in-process background long-poll against `GET /v1/sessions/<id>/pull`
 *   and injects incoming messages directly into the live TUI via `pi.sendUserMessage()`.
 * - Reports `busy` / `idle` on `agent_start` / `agent_end`, updates `conversation`
 *   on `session_start` (`/resume`), and calls `/end` + removes the local 0600 session
 *   file on `session_shutdown`.
 *
 * Zero third-party dependencies: uses Node 22 built-in `fetch` and pi's built-in `typebox`.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

interface HubConfig {
  url?: string;
  token?: string;
}

interface SessionState {
  hub: string;
  session_id: string;
  token: string;
  name: string;
  kind: "pi";
  cwd: string;
  conversation: string;
  push: "channel";
  meta: Record<string, unknown>;
  pid: number;
}

function agenthubHome(): string {
  return process.env.AGENTHUB_HOME
    ? path.resolve(process.env.AGENTHUB_HOME.replace(/^~/, os.homedir()))
    : path.join(os.homedir(), ".agent-hub");
}

function loadConfig(): { url: string; token: string } {
  let cfg: HubConfig = {};
  try {
    const raw = fs.readFileSync(path.join(agenthubHome(), "config.json"), "utf8");
    cfg = JSON.parse(raw) as HubConfig;
  } catch {
    // ignore missing config
  }
  const url = (process.env.HUB_URL || cfg.url || "http://127.0.0.1:8787").replace(/\/+$/, "");
  const token = process.env.HUB_TOKEN || cfg.token || "";
  return { url, token };
}

function sessionFilePath(name: string): string {
  return path.join(agenthubHome(), "sessions", `${name}.json`);
}

function writeSessionFile(sess: SessionState): void {
  const dir = path.join(agenthubHome(), "sessions");
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const dest = sessionFilePath(sess.name);
  const tmp = `${dest}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(sess, null, 2), { mode: 0o600 });
  fs.chmodSync(tmp, 0o600);
  fs.renameSync(tmp, dest);
}

function removeSessionFile(name: string): void {
  try {
    fs.unlinkSync(sessionFilePath(name));
  } catch {
    // ignore
  }
}

function shellQuote(v: string): string {
  return `'${v.replace(/'/g, `'\\''`)}'`;
}

function writeSecretEnvFile(
  secretName: string,
  opts: { exports?: string[]; env?: Record<string, string>; value?: string },
): string {
  const dir = path.join(agenthubHome(), "tmp");
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const safe = secretName.replace(/[^A-Za-z0-9_.-]/g, "_");
  const file = path.join(dir, `secret-${safe}-${process.pid}-${Date.now()}.env`);
  const lines: string[] = [];
  if (Array.isArray(opts.exports) && opts.exports.length > 0) {
    for (const line of opts.exports) {
      lines.push(String(line));
    }
  } else if (opts.env && Object.keys(opts.env).length > 0) {
    for (const [k, v] of Object.entries(opts.env)) {
      lines.push(`export ${k}=${shellQuote(String(v))}`);
    }
  } else if (opts.value !== undefined) {
    const key = safe.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
    lines.push(`export ${key}=${shellQuote(String(opts.value))}`);
  }
  fs.writeFileSync(file, lines.join("\n") + "\n", { mode: 0o600 });
  fs.chmodSync(file, 0o600);
  return file;
}

export default function (pi: ExtensionAPI) {
  let session: SessionState | null = null;
  let lastCtx: ExtensionContext | null = null;
  let isBusy = false;
  let pollAbort: AbortController | null = null;
  let polling = false;
  let currentConversation = "";
  let currentSessionFile = "";
  let currentCwd = process.cwd();

  async function apiCall(
    method: string,
    apiPath: string,
    body?: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    const cfg = loadConfig();
    const base = session?.hub || cfg.url;
    const headers: Record<string, string> = {};
    if (cfg.token) headers["Authorization"] = `Bearer ${cfg.token}`;
    if (session?.token) headers["X-Hub-Session"] = session.token;
    if (body !== undefined) headers["Content-Type"] = "application/json";

    const resp = await fetch(`${base}${apiPath}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
    const text = await resp.text();
    let parsed: Record<string, unknown> = {};
    try {
      parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      parsed = { raw: text };
    }
    if (!resp.ok) {
      const errMsg = typeof parsed.error === "string" ? parsed.error : `HTTP ${resp.status}`;
      const err = new Error(`hub ${resp.status}: ${errMsg}`) as Error & { status?: number };
      err.status = resp.status;
      throw err;
    }
    return parsed;
  }

  function setStatus(text: string): void {
    try {
      if (lastCtx?.hasUI) {
        lastCtx.ui.setStatus("agent-hub", text);
      }
    } catch {
      // ignore in headless/rpc mode
    }
  }

  async function reportState(state: "busy" | "idle"): Promise<void> {
    if (!session) return;
    try {
      await apiCall("POST", `/v1/sessions/${encodeURIComponent(session.session_id)}/state`, { state });
    } catch {
      // non-fatal
    }
  }

  function stopPolling(): void {
    polling = false;
    if (pollAbort) {
      pollAbort.abort();
      pollAbort = null;
    }
  }

  function startPolling(): void {
    if (polling || !session) return;
    polling = true;
    const mySessionId = session.session_id;
    const waitSecs = Math.max(0, Math.min(55, Number(process.env.HUB_PI_POLL_WAIT || "50")));

    const loop = async () => {
      while (polling && session && session.session_id === mySessionId) {
        pollAbort = new AbortController();
        try {
          const res = await apiCall(
            "GET",
            `/v1/sessions/${encodeURIComponent(mySessionId)}/pull?wait=${waitSecs}`,
            undefined,
            pollAbort.signal,
          );
          const items = (Array.isArray(res.items) ? res.items : []) as Array<{ id?: number; text?: string }>;
          if (items.length > 0 && session && session.session_id === mySessionId) {
            const body = items.map((i) => String(i.text || "")).join("\n\n---\n\n");
            const msg = `[agent-hub] 以下是 hub 推送给你（${session.name}）的消息，请按其中要求处理；回复用 hub_* 工具。\n\n${body}`;
            if (isBusy) {
              pi.sendUserMessage(msg, { deliverAs: "followUp" });
            } else {
              pi.sendUserMessage(msg);
            }
            const ids = items.map((i) => i.id).filter((id): id is number => typeof id === "number");
            if (ids.length > 0) {
              await apiCall("POST", `/v1/sessions/${encodeURIComponent(mySessionId)}/delivered`, { ids });
            }
          }
        } catch (err: unknown) {
          const e = err as { name?: string; status?: number };
          if (e?.name === "AbortError") break;
          if (e?.status === 401 || e?.status === 404) {
            if (session && session.session_id === mySessionId) {
              removeSessionFile(session.name);
              session = null;
              setStatus("hub: disconnected");
            }
            break;
          }
          await new Promise((r) => setTimeout(r, 3000));
        }
      }
      polling = false;
    };
    void loop();
  }

  async function doRegister(params: {
    name: string;
    project?: string;
    role?: string;
    description?: string;
  }): Promise<Record<string, unknown>> {
    const cfg = loadConfig();
    const prevName = session?.name;
    const reqBody: Record<string, unknown> = {
      name: params.name,
      kind: "pi",
      cwd: currentCwd,
      project: params.project || "",
      role: params.role || "",
      description: params.description || "",
      conversation: currentConversation,
      pid: process.pid,
      push: "channel",
      meta: currentSessionFile ? { session_file: currentSessionFile } : {},
    };
    const res = await apiCall("POST", "/v1/sessions", reqBody);
    stopPolling();
    if (prevName && prevName !== params.name) {
      removeSessionFile(prevName);
    }
    session = {
      hub: cfg.url,
      session_id: String(res.session_id || ""),
      token: String(res.token || ""),
      name: params.name,
      kind: "pi",
      cwd: currentCwd,
      conversation: currentConversation,
      push: "channel",
      meta: currentSessionFile ? { session_file: currentSessionFile } : {},
      pid: process.pid,
    };
    writeSessionFile(session);
    setStatus(`hub: ${session.name}`);
    startPolling();
    return res;
  }

  async function shutdownSession(): Promise<void> {
    stopPolling();
    if (!session) return;
    const s = session;
    try {
      await apiCall("POST", `/v1/sessions/${encodeURIComponent(s.session_id)}/end`, {});
    } catch {
      // ignore network error on shutdown
    } finally {
      if (session === s) session = null;
      removeSessionFile(s.name);
    }
  }

  // ------------------------------------------------------------------ lifecycle events

  pi.on("session_start", async (ev, ctx) => {
    lastCtx = ctx;
    currentCwd = ctx.cwd || process.cwd();
    currentConversation = ctx.sessionManager.getSessionId() || "";
    currentSessionFile = ctx.sessionManager.getSessionFile() || "";

    if (session) {
      session.conversation = currentConversation;
      session.cwd = currentCwd;
      if (currentSessionFile) session.meta = { ...session.meta, session_file: currentSessionFile };
      writeSessionFile(session);
      try {
        await apiCall("POST", `/v1/sessions/${encodeURIComponent(session.session_id)}/meta`, {
          conversation: currentConversation,
          meta: session.meta,
        });
      } catch {
        // non-fatal
      }
    } else if (process.env.HUB_AGENT && ev.reason === "startup") {
      try {
        await doRegister({
          name: process.env.HUB_AGENT,
          project: process.env.HUB_PROJECT || "",
          role: process.env.HUB_ROLE || "",
        });
      } catch {
        setStatus("hub: offline");
      }
    }
  });

  pi.on("agent_start", async (_ev, ctx) => {
    lastCtx = ctx;
    isBusy = true;
    await reportState("busy");
  });

  pi.on("agent_end", async (_ev, ctx) => {
    lastCtx = ctx;
    // Note: in pi 0.74.2, ctx.isIdle() may still read false inside agent_end;
    // treat the agent_end event itself as the authoritative idle transition.
    isBusy = false;
    await reportState("idle");
  });

  pi.on("session_shutdown", async () => {
    await shutdownSession();
  });

  // ------------------------------------------------------------------ 9 hub_* tools (api.md §协作)

  function ok(data: unknown) {
    const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    return { content: [{ type: "text" as const, text }], details: {} };
  }

  pi.registerTool({
    name: "hub_register",
    label: "Hub Register",
    description: "Register or re-claim this agent's identity on agent-hub; call without name to inspect current identity.",
    parameters: Type.Object({
      name: Type.Optional(Type.String({ description: "Agent name (omit to check whoami)" })),
      project: Type.Optional(Type.String({ description: "Project scope" })),
      role: Type.Optional(Type.String({ description: "Role in project" })),
      description: Type.Optional(Type.String({ description: "One-line description" })),
    }),
    async execute(_id, params) {
      if (!params.name) {
        if (!session) return ok({ registered: false, hint: "Pass name to hub_register(name=...) first." });
        return ok(await apiCall("GET", "/v1/whoami"));
      }
      const res = await doRegister({
        name: params.name,
        project: params.project,
        role: params.role,
        description: params.description,
      });
      return ok({
        registered: params.name,
        session_id: res.session_id,
        queued: res.queued ?? 0,
        agent: res.agent,
      });
    },
  });

  pi.registerTool({
    name: "hub_agents",
    label: "Hub Agents",
    description: "List agents registered on agent-hub, their presence, busy/idle state, and current task.",
    parameters: Type.Object({
      project: Type.Optional(Type.String({ description: "Filter by project" })),
    }),
    async execute(_id, params) {
      const q = params.project ? `?project=${encodeURIComponent(params.project)}` : "";
      return ok(await apiCall("GET", `/v1/agents${q}`));
    },
  });

  pi.registerTool({
    name: "hub_send",
    label: "Hub Send",
    description: "Send a direct message to an agent, role@project, human, or *@project.",
    parameters: Type.Object({
      to: Type.String({ description: "Recipient: agent name, role@project, human, or *@project" }),
      body: Type.String({ description: "Message body" }),
      reply_to: Type.Optional(Type.Number({ description: "Optional post_id to reply to" })),
    }),
    async execute(_id, params) {
      const payload: Record<string, unknown> = { to: params.to, body: params.body };
      if (params.reply_to !== undefined) payload.reply_to = params.reply_to;
      return ok(await apiCall("POST", "/v1/posts", payload));
    },
  });

  pi.registerTool({
    name: "hub_delegate",
    label: "Hub Delegate",
    description: "Delegate a structured task to another agent or role@project.",
    parameters: Type.Object({
      to: Type.String({ description: "Target agent name or role@project" }),
      title: Type.String({ description: "Short task title" }),
      goal: Type.String({ description: "What must be achieved" }),
      context: Type.String({ description: "Complete context, files, constraints" }),
      acceptance: Type.String({ description: "Concrete done-criteria" }),
      project: Type.Optional(Type.String({ description: "Project name" })),
      parent_id: Type.Optional(Type.Number({ description: "Optional parent task_id" })),
    }),
    async execute(_id, params) {
      return ok(await apiCall("POST", "/v1/tasks", params as Record<string, unknown>));
    },
  });

  pi.registerTool({
    name: "hub_update",
    label: "Hub Update",
    description: "Post an entry into a task thread (plan, progress, question, answer, result, comment).",
    parameters: Type.Object({
      task_id: Type.Number({ description: "Task ID" }),
      kind: Type.Union([
        Type.Literal("plan"),
        Type.Literal("progress"),
        Type.Literal("question"),
        Type.Literal("answer"),
        Type.Literal("result"),
        Type.Literal("comment"),
      ]),
      body: Type.String({ description: "Update body" }),
      to: Type.Optional(Type.String({ description: "Optional explicit target (e.g. 'human')" })),
    }),
    async execute(_id, params) {
      const payload: Record<string, unknown> = { kind: params.kind, body: params.body };
      if (params.to) payload.to = params.to;
      return ok(await apiCall("POST", `/v1/tasks/${params.task_id}`, payload));
    },
  });

  pi.registerTool({
    name: "hub_close",
    label: "Hub Close",
    description: "Close or reopen a task (state: done | cancelled | open).",
    parameters: Type.Object({
      task_id: Type.Number({ description: "Task ID" }),
      state: Type.Union([Type.Literal("done"), Type.Literal("cancelled"), Type.Literal("open")]),
      note: Type.Optional(Type.String({ description: "Optional closing note" })),
    }),
    async execute(_id, params) {
      return ok(
        await apiCall("POST", `/v1/tasks/${params.task_id}`, {
          kind: "close",
          state: params.state,
          body: params.note || "",
        }),
      );
    },
  });

  pi.registerTool({
    name: "hub_tasks",
    label: "Hub Tasks",
    description: "Read one task in full (pass task_id) or list/search tasks with filters.",
    parameters: Type.Object({
      task_id: Type.Optional(Type.Number({ description: "Read this specific task and its full thread" })),
      project: Type.Optional(Type.String()),
      state: Type.Optional(Type.String()),
      owner: Type.Optional(Type.String()),
      q: Type.Optional(Type.String({ description: "Search query" })),
    }),
    async execute(_id, params) {
      if (params.task_id !== undefined) {
        return ok(await apiCall("GET", `/v1/tasks/${params.task_id}`));
      }
      const qs = new URLSearchParams();
      if (params.project) qs.set("project", params.project);
      if (params.state) qs.set("state", params.state);
      if (params.owner) qs.set("owner", params.owner);
      if (params.q) qs.set("q", params.q);
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      return ok(await apiCall("GET", `/v1/tasks${suffix}`));
    },
  });

  pi.registerTool({
    name: "hub_inbox",
    label: "Hub Inbox",
    description: "Consume all currently queued inbox items for this session.",
    parameters: Type.Object({}),
    async execute() {
      return ok(await apiCall("GET", "/v1/inbox"));
    },
  });

  pi.registerTool({
    name: "hub_board",
    label: "Hub Board",
    description: "Access the shared board: list/get/put secrets (written to 0600 env file, never echoed), notes, or skills.",
    parameters: Type.Object({
      action: Type.Union([Type.Literal("list"), Type.Literal("get"), Type.Literal("put"), Type.Literal("delete")]),
      type: Type.Optional(Type.Union([Type.Literal("secret"), Type.Literal("note"), Type.Literal("skill")])),
      name: Type.Optional(Type.String()),
      value: Type.Optional(Type.String()),
      title: Type.Optional(Type.String()),
      body: Type.Optional(Type.String()),
      note: Type.Optional(Type.String()),
      env: Type.Optional(Type.Record(Type.String(), Type.String())),
      tags: Type.Optional(Type.Array(Type.String())),
      refs: Type.Optional(Type.Array(Type.String())),
      project: Type.Optional(Type.String()),
    }),
    async execute(_id, params) {
      if (params.action === "list") {
        if (params.type === "skill") return ok(await apiCall("GET", "/v1/skills"));
        return ok(await apiCall("GET", "/v1/board"));
      }
      if (!params.name || !params.type) {
        return ok({ error: "name and type are required for get/put/delete" });
      }
      const enc = encodeURIComponent(params.name);
      if (params.type === "secret") {
        if (params.action === "get") {
          const data = await apiCall("GET", `/v1/board/secret/${enc}`);
          const exportsList = Array.isArray(data.exports) ? (data.exports as string[]) : undefined;
          const envMap = (data.env && typeof data.env === "object" ? data.env : {}) as Record<string, string>;
          const file = writeSecretEnvFile(params.name, {
            exports: exportsList,
            env: envMap,
            value: typeof data.value === "string" ? data.value : undefined,
          });
          return ok({
            name: params.name,
            note: data.note || "",
            env_keys: Object.keys(envMap),
            env_file: file,
            usage: `source ${shellQuote(file)}`,
          });
        }
        if (params.action === "put") {
          return ok(
            await apiCall("POST", `/v1/board/secret/${enc}`, {
              value: params.value || "",
              note: params.note,
              env: params.env,
              tags: params.tags,
              project: params.project,
            }),
          );
        }
      }
      if (params.type === "note") {
        if (params.action === "get") return ok(await apiCall("GET", `/v1/board/note/${enc}`));
        if (params.action === "put") {
          return ok(
            await apiCall("POST", `/v1/board/note/${enc}`, {
              title: params.title,
              body: params.body || "",
              tags: params.tags,
              refs: params.refs,
              project: params.project,
            }),
          );
        }
      }
      if (params.type === "skill" && params.action === "delete") {
        return ok(await apiCall("DELETE", `/v1/skills/${enc}`));
      }
      return ok({ error: `unsupported action=${params.action} type=${params.type}` });
    },
  });
}
