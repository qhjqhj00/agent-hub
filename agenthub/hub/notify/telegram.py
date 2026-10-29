"""Telegram bot: the human's remote for the agent team.

Runs inside the hub process (long-polling, so no inbound port). It is the
`human` participant: everything delivered to `human` is sent here, and what you
type is written back as `human` -- delegations, answers, verdicts.

Only one Telegram user is obeyed: the one who paired with `/pair <code>`,
where the code is shown on the dashboard's settings panel.
"""
import html
import json
import os
import secrets
import threading
import time
import traceback
import urllib.parse
import urllib.request

from .. import bus, core
from ..core import HubError
from ..i18n import t
from ..store import DATA_DIR, HUMAN, OPEN_STATES, db, live_session, now, presence, set_setting, setting

TOKEN_FILE = os.environ.get("HUB_TG_TOKEN_FILE", os.path.join(DATA_DIR, "telegram.token"))
API = "https://api.telegram.org/bot%s/%s"
STATE_ICON = {"open": "⚪", "planning": "📝", "in_progress": "🔵", "blocked": "🟠", "needs_human": "🔴",
              "review": "🟣", "done": "✅", "dropped": "⚫"}
PRES_ICON = {"live": "🟢", "idle": "🟡", "gone": "⚫"}



def token():
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not tok and os.path.isfile(TOKEN_FILE):
        tok = open(TOKEN_FILE).read().strip()
        if "=" in tok:
            tok = tok.split("=", 1)[1].strip().strip('"')
    return tok


class Bot:
    def __init__(self, tok):
        self.tok = tok
        self.offset = 0

    def call(self, method, _timeout=60, **params):
        data = urllib.parse.urlencode(
            {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(API % (self.tok, method), data)
        with urllib.request.urlopen(req, timeout=_timeout) as r:
            out = json.loads(r.read().decode())
        if not out.get("ok"):
            raise RuntimeError(out)
        return out["result"]

    def username(self):
        if not getattr(self, "_me", None):
            self._me = self.call("getMe", _timeout=10).get("username")
        return self._me

    def say(self, text, task_id=None, agent="", buttons=None, reply_to=None):
        with db() as conn:
            chat = setting(conn, "tg_chat")
        if not chat:
            return None
        markup = {"inline_keyboard": buttons} if buttons else None
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]
        msg = None
        for i, c in enumerate(chunks):
            try:
                msg = self.call("sendMessage", chat_id=chat, text=c, parse_mode="HTML",
                                disable_web_page_preview="true",
                                reply_markup=markup if i == len(chunks) - 1 else None,
                                reply_to_message_id=reply_to if i == 0 else None)
            except Exception:
                # bad HTML from agent text: fall back to plain
                msg = self.call("sendMessage", chat_id=chat, text=_strip(c),
                                reply_markup=markup if i == len(chunks) - 1 else None)
        if msg and (task_id or agent):
            with db() as conn:
                conn.execute("INSERT OR REPLACE INTO tg_refs VALUES (?,?,?,?)",
                             (msg["message_id"], task_id, agent, now()))
        return msg

    # ------------------------------------------------------------ outbound
    name = "telegram"

    def ready(self):
        with db() as conn:
            return bool(setting(conn, "tg_chat"))

    def send(self, d):
        self.notify(d)

    def notify(self, d):
        meta = json.loads(d["meta"] or "{}")
        kind, tid = d["kind"], d["task_id"]
        if kind == "permission":
            with db() as conn:
                p = conn.execute("SELECT * FROM permissions WHERE id=?", (meta.get("perm_id"),)).fetchone()
            if not p or p["state"] != "pending":
                return
            text = t("tg.perm", agent=_e(p["agent"]), tool=_e(p["tool"]), desc=_e(p["description"]),
                     preview=_e(p["preview"][:1500]))
            self.say(text, agent=p["agent"], buttons=[[
                {"text": t("tg.allow"), "callback_data": "perm:%d:allow" % p["id"]},
                {"text": t("tg.deny"), "callback_data": "perm:%d:deny" % p["id"]}]])
            return
        icon = {"result": "🟣", "question": "❓", "needs_human": "🔴", "blocked": "🟠", "plan": "📝",
                "message": "💬", "claimed": "🙋", "closed": "🏁", "wake_failed": "⚠️", "unrouted": "⚠️",
                "progress": "🔵", "task": "📥", "comment": "💬", "answer": "💬"}.get(kind, "•")
        body = d["content"]
        # agent-facing instructions are noise on a phone
        for cut in ("\n\nReview it.", "\n\nAnswer with hub_update", "\n\nReply with hub_send", "\n\nTask is back to"):
            body = body.split(cut)[0]
        body = body.replace("[agent-hub] ", "")
        buttons = None
        if kind == "result" and tid:
            buttons = [[{"text": t("tg.accept"), "callback_data": "task:%d:done" % tid},
                        {"text": t("tg.send_back"), "callback_data": "task:%d:noop" % tid}]]
        self.say("%s %s" % (icon, _e(body)), task_id=tid, agent=meta.get("from", ""), buttons=buttons)

    # ------------------------------------------------------------ inbound
    def inbound(self):
        while True:
            try:
                ups = self.call("getUpdates", _timeout=70, offset=self.offset, timeout=50,
                                allowed_updates=["message", "callback_query"])
                for u in ups:
                    self.offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except Exception:
                        traceback.print_exc()
            except Exception:
                traceback.print_exc()
                time.sleep(10)

    def handle(self, u):
        if "callback_query" in u:
            cq = u["callback_query"]
            if not self._allowed(cq["from"]["id"]):
                return
            self.call("answerCallbackQuery", callback_query_id=cq["id"])
            kind, ident, verb = (cq.get("data") or "::").split(":", 2)
            if kind == "perm":
                from .. import api
                code, out = api.api_perm_decide({"node": "telegram"}, {}, {"behavior": verb}, ident)
                self.say(t("tg.perm_allowed" if verb == "allow" else "tg.perm_denied", id=ident)
                         if out.get("state") == verb else t("tg.perm_already", state=out.get("state")))
            elif kind == "task" and verb == "done":
                with db() as conn:
                    core.close(conn, int(ident), HUMAN, "done")
                self.say(t("tg.accepted", id=ident))
            elif kind == "task" and verb == "noop":
                self.say(t("tg.how_send_back"))
            return
        m = u.get("message")
        if not m or "text" not in m:
            return
        text = m["text"].strip()
        uid = m["from"]["id"]
        if text.startswith("/pair"):
            return self.pair(m, text)
        if not self._allowed(uid):
            if text.startswith("/start"):
                self.call("sendMessage", chat_id=m["chat"]["id"],
                          text=t("tg.pair_hint"))
            return
        rt = m.get("reply_to_message")
        if rt and not text.startswith("/"):
            return self.reply(rt["message_id"], text)
        cmd, _, rest = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        fn = getattr(self, "cmd_" + cmd.lstrip("/"), None)
        if not fn:
            return self.say(t("tg.help"))
        try:
            fn(rest.strip())
        except HubError as e:
            self.say("⚠️ %s" % _e(str(e)))

    def _allowed(self, uid):
        with db() as conn:
            return setting(conn, "tg_user") == str(uid)

    def pair(self, m, text):
        code = text.partition(" ")[2].strip()
        with db() as conn:
            want = pair_code(conn)
            if setting(conn, "tg_user") and setting(conn, "tg_user") != str(m["from"]["id"]):
                return
            if code != want:
                self.call("sendMessage", chat_id=m["chat"]["id"], text=t("tg.wrong_code"))
                return
            # whatever piled up before pairing is history, not news
            conn.execute("UPDATE deliveries SET sent_at=?, delivered_at=?, session='pre-pair' "
                         "WHERE agent=? AND delivered_at IS NULL", (now(), now(), HUMAN))
            set_setting(conn, "tg_user", m["from"]["id"])
            set_setting(conn, "tg_chat", m["chat"]["id"])
            set_setting(conn, "tg_pair_code", secrets.token_hex(3))
        bus.bump()
        self.say(t("tg.paired") + t("tg.help"))

    def reply(self, msg_id, text):
        with db() as conn:
            ref = conn.execute("SELECT * FROM tg_refs WHERE msg_id=?", (msg_id,)).fetchone()
            if not ref:
                return self.say(t("tg.no_ref"))
            if ref["task_id"]:
                tk = core.get_task(conn, ref["task_id"])
                kind = "answer" if tk["state"] in ("needs_human", "blocked") else "comment"
                if tk["state"] in ("done", "dropped"):
                    core.close(conn, tk["id"], HUMAN, "open", text)
                    core.update(conn, tk["id"], HUMAN, "comment", text)
                else:
                    core.update(conn, tk["id"], HUMAN, kind, text)
                self.say(t("tg.wrote_task", id=tk["id"]))
            elif ref["agent"]:
                core.send(conn, HUMAN, ref["agent"], text)
                self.say(t("tg.sent_to", agent=_e(ref["agent"])))

    # ------------------------------------------------------------ commands
    def cmd_start(self, rest):
        self.say(t("tg.help"))

    cmd_help = cmd_start

    def cmd_status(self, rest):
        with db() as conn:
            items = core.attention(conn)
            live = conn.execute("SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL AND agent!='' AND "
                                "last_seen > strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-120 seconds')").fetchone()[0]
            counts = {r["state"]: r["c"] for r in conn.execute(
                "SELECT state, COUNT(*) c FROM tasks WHERE state IN (%s) GROUP BY state"
                % ",".join("?" * len(OPEN_STATES)), OPEN_STATES)}
        lines = [t("tg.status_head", live=live, open=sum(counts.values()))]
        if counts:
            lines.append(" ".join("%s%s %d" % (STATE_ICON.get(k, ""), k, v) for k, v in counts.items()))
        if not items:
            lines.append(t("tg.all_clear"))
        for it in items[:15]:
            lines.append("\n%s <b>%s</b> #%s %s\n%s" % (
                {"permission": "🔐", "needs_human": "🔴", "blocked": "🟠", "review": "🟣", "stalled": "⏳"}.get(it["type"], "⚠️"),
                it["type"], it["id"], _e(it["title"]), _e((it["body"] or "")[:200])))
        self.say("\n".join(lines))

    def cmd_agents(self, rest):
        with db() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY node, last_seen DESC").fetchall()
            out, node = [], None
            for r in rows:
                if r["node"] != node:
                    node = r["node"]
                    out.append("\n<b>%s</b>" % _e(node or "?"))
                s = live_session(conn, r["name"])
                pres = "live" if s else presence(r["last_seen"])
                cur = conn.execute("SELECT id,title FROM tasks WHERE owner=? AND state IN ('planning','in_progress',"
                                 "'blocked','needs_human') ORDER BY updated_at DESC LIMIT 1", (r["name"],)).fetchone()
                out.append("%s %s <i>%s</i>%s" % (PRES_ICON[pres], _e(r["name"]), _e(_short(r["workspace"])),
                                                  " — #%d %s" % (cur["id"], _e(cur["title"][:40])) if cur else ""))
        self.say("\n".join(out) or "no agents")

    def cmd_projects(self, rest):
        from .. import api
        _, d = api.api_projects({}, {}, {})
        lines = ["<b>%s</b> — %d open · %d agents" % (_e(p["name"]), p["open"], len(p["agents"]))
                 for p in d["projects"] if not p.get("archived")]
        self.say("\n".join(lines) or "no projects")

    def cmd_tasks(self, rest):
        with db() as conn:
            q = "SELECT * FROM tasks WHERE state IN (%s)" % ",".join("?" * len(OPEN_STATES))
            args = list(OPEN_STATES)
            if rest:
                q += " AND project=?"
                args.append(rest)
            rows = conn.execute(q + " ORDER BY updated_at DESC LIMIT 30", args).fetchall()
        lines = ["%s #%d %s → %s%s" % (STATE_ICON.get(r["state"], ""), r["id"], _e(r["title"][:60]),
                                         _e(r["owner"] or "%s@%s" % (r["role"], r["project"])),
                                         " [%s]" % _e(r["project"]) if r["project"] and not rest else "")
                 for r in rows]
        self.say("\n".join(lines) or t("tg.no_tasks"))

    def cmd_task(self, rest):
        with db() as conn:
            tk = core.task_dict(conn, core.get_task(conn, int(rest.lstrip("#"))), full=True)
        lines = ["%s <b>#%d %s</b>" % (STATE_ICON.get(tk["state"], ""), tk["id"], _e(tk["title"])),
                 "%s → %s · %s" % (_e(tk["created_by"]), _e(tk["owner"] or tk["role"]), tk["state"])]
        if tk["goal"]:
            lines.append("\n<b>%s</b>\n" % t("tg.goal") + "\n" + _e(tk["goal"][:600]))
        for e in tk["log"][-6:]:
            lines.append("\n<b>%s</b> %s:\n%s" % (_e(e["agent"]), e["kind"], _e(e["body"][:500])))
        self.say("\n".join(lines), task_id=tk["id"])

    def cmd_delegate(self, rest):
        to, _, body = rest.partition(" ")
        title, _, goal = body.strip().partition("\n")
        if not to or not title:
            return self.say(t("tg.usage_delegate"))
        with db() as conn:
            tk = core.delegate(conn, HUMAN, to, title.strip(), goal.strip())
        self.say(t("tg.delegated", id=tk["id"], to=_e(to)), task_id=tk["id"])

    def cmd_tell(self, rest):
        to, _, body = rest.partition(" ")
        if not body:
            return self.say(t("tg.usage_tell"))
        with db() as conn:
            core.send(conn, HUMAN, to, body)
        self.say(t("tg.told", to=_e(to)), agent=to)

    def cmd_wake(self, rest):
        from .. import api
        name, _, prompt = rest.partition(" ")
        api.api_wake({}, {}, {"prompt": prompt, "reason": "telegram"}, name)
        self.say(t("tg.waking", name=_e(name)))

    def cmd_peek(self, rest):
        from .. import api
        _, out = api.api_peek({}, {}, {}, rest.strip())
        tail = "\n".join(out["screen"].splitlines()[-30:])
        self.say(t("tg.screen", name=_e(rest), screen=_e(tail[-3500:])), agent=rest.strip())

    def cmd_stop(self, rest):
        from .. import api
        api.api_stop({}, {}, {"reason": "telegram"}, rest)
        self.say(t("tg.stopping", name=_e(rest)))

    def _close(self, rest, state):
        with db() as conn:
            core.close(conn, int(rest.lstrip("#")), HUMAN, state)
        self.say(t("tg.closed", id=rest, state=state))

    def cmd_done(self, rest):
        self._close(rest, "done")

    def cmd_drop(self, rest):
        self._close(rest, "dropped")

    def cmd_reopen(self, rest):
        self._close(rest, "open")


def pair_code(conn):
    code = setting(conn, "tg_pair_code")
    if not code:
        code = secrets.token_hex(3)
        set_setting(conn, "tg_pair_code", code)
    return code


def _e(s):
    return html.escape(s or "", quote=False)


def _strip(s):
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def _short(p):
    import re
    return re.sub(r"^(/home/[^/]+|/Users/[^/]+|/root)/", "~/", p or "")


_bot = None


def start():
    """The bot, if a token is configured: answers commands now, and is returned to the
    notifier so deliveries to the human reach Telegram."""
    global _bot
    tok = token()
    if not tok:
        return None
    _bot = Bot(tok)
    with db() as conn:
        code = pair_code(conn)
        paired = setting(conn, "tg_user")
    print("telegram: bot on%s" % (", paired" if paired else ", pair with /pair %s" % code), flush=True)
    try:
        _bot.call("setMyCommands", commands=[
            {"command": c, "description": t("tg.cmd." + c)} for c in (
                "status", "agents", "projects", "tasks", "task", "delegate", "tell", "wake", "stop", "peek",
                "done", "help")])
    except Exception:
        traceback.print_exc()
    threading.Thread(target=_bot.inbound, daemon=True).start()
    return _bot
