"""Getting things to the human.

Anything delivered to `human` (a question, a result to review, a permission
request, a failed wake) goes out through every notifier that is set up:

  telegram   two-way: TELEGRAM_BOT_TOKEN (or data/telegram.token), then /pair
  webhook    one-way: HUB_WEBHOOK_URL -- Slack, Discord, Feishu/Lark, DingTalk,
             ntfy, or plain JSON; the format is guessed from the URL
             (override with HUB_WEBHOOK_FORMAT). Several URLs: comma-separated.

With no notifier, deliveries to the human stay queued and the dashboard's
"Needs you" page is the place to look.
"""
import threading
import time
import traceback

from .. import bus
from ..store import HUMAN, db, now
from . import telegram, webhook

_notifiers = []


def active():
    return [n for n in _notifiers if n.ready()]


def names():
    return [n.name for n in _notifiers]


def dispatch():
    v = -1
    while True:
        try:
            v = bus.wait(v, 30)
            targets = active()
            if not targets:
                continue
            with db() as conn:
                rows = conn.execute("SELECT * FROM deliveries WHERE agent=? AND delivered_at IS NULL ORDER BY id",
                                    (HUMAN,)).fetchall()
                for r in rows:
                    conn.execute("UPDATE deliveries SET sent_at=?, delivered_at=?, session=? WHERE id=?",
                                 (now(), now(), ",".join(n.name for n in targets), r["id"]))
            for r in rows:
                for n in targets:
                    try:
                        n.send(r)
                    except Exception:
                        traceback.print_exc()
        except Exception:
            traceback.print_exc()
            time.sleep(5)


def start():
    bot = telegram.start()
    if bot:
        _notifiers.append(bot)
    _notifiers.extend(webhook.from_env())
    if not _notifiers:
        print("notify: none configured (TELEGRAM_BOT_TOKEN, HUB_WEBHOOK_URL); the dashboard shows what needs you",
              flush=True)
    threading.Thread(target=dispatch, daemon=True).start()
