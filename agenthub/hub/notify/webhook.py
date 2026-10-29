"""One-way notifications to a chat webhook.

The payload shape is picked from the URL: Slack, Discord, Feishu/Lark,
DingTalk and ntfy each want their own; anything else gets plain JSON
({"kind", "task_id", "from", "text", "url"}). HUB_WEBHOOK_FORMAT forces one.
"""
import json
import os
import urllib.request

FORMATS = ("slack", "discord", "feishu", "dingtalk", "ntfy", "json")
ICON = {"result": "🟣", "question": "❓", "needs_human": "🔴", "blocked": "🟠", "message": "💬",
        "permission": "🔐", "wake_failed": "⚠️", "unrouted": "⚠️", "task": "📥"}
# agent-facing instructions at the end of a delivery are noise for a person
CUTS = ("\n\nReview it.", "\n\nAnswer with hub_update", "\n\nReply with hub_send", "\n\nTask is back to")


def guess(url):
    u = url.lower()
    for host, fmt in (("hooks.slack.com", "slack"), ("discord.com/api/webhooks", "discord"),
                      ("discordapp.com/api/webhooks", "discord"), ("open.feishu.cn", "feishu"),
                      ("open.larksuite.com", "feishu"), ("oapi.dingtalk.com", "dingtalk"), ("ntfy", "ntfy")):
        if host in u:
            return fmt
    return "json"


def text_of(d):
    body = d["content"]
    for cut in CUTS:
        body = body.split(cut)[0]
    return "%s %s" % (ICON.get(d["kind"], "•"), body.replace("[agent-hub] ", ""))


class Webhook:
    def __init__(self, url, fmt=None):
        self.url, self.fmt = url, fmt or guess(url)
        self.name = "webhook:" + self.fmt
        if self.fmt not in FORMATS:
            raise SystemExit("HUB_WEBHOOK_FORMAT must be one of %s" % ", ".join(FORMATS))

    def ready(self):
        return True

    def payload(self, d):
        text = text_of(d)
        link = os.environ.get("HUB_URL", "").rstrip("/")
        if link and d["task_id"]:
            link += "/#/task/%d" % d["task_id"]
            text += "\n" + link
        meta = json.loads(d["meta"] or "{}")
        if self.fmt == "slack":
            return {"text": text}
        if self.fmt == "discord":
            return {"content": text[:1990]}
        if self.fmt == "feishu":
            return {"msg_type": "text", "content": {"text": text}}
        if self.fmt == "dingtalk":
            return {"msgtype": "text", "text": {"content": text}}
        if self.fmt == "ntfy":
            return text
        return {"kind": d["kind"], "task_id": d["task_id"], "from": meta.get("from", ""), "text": text,
                "url": link or None}

    def send(self, d):
        body = self.payload(d)
        if isinstance(body, str):
            req = urllib.request.Request(self.url, body.encode(), {"Title": "agent-hub: %s" % d["kind"]})
        else:
            req = urllib.request.Request(self.url, json.dumps(body, ensure_ascii=False).encode(),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()


def from_env():
    urls = [u.strip() for u in os.environ.get("HUB_WEBHOOK_URL", "").split(",") if u.strip()]
    fmt = os.environ.get("HUB_WEBHOOK_FORMAT") or None
    out = [Webhook(u, fmt) for u in urls]
    for w in out:
        print("notify: %s" % w.name, flush=True)
    return out
