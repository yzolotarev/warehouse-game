#!/usr/bin/env python3
"""TG-вход склада: поллит личного бота, кладёт сообщения в инбокс (source=phone).

Токен: ~/.config/warehouse/tg_token (создать бота у @BotFather, положить токен в файл).
Без session-файлов - чистый Bot API long-poll.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

TOKEN_FILE = Path.home() / ".config/warehouse/tg_token"
CHAT_FILE = Path.home() / ".config/warehouse/tg_chat"   # его читает сервер для закрепа «🚶 Улица»
SERVER = "http://127.0.0.1:8091/inbox"
STREET_URL = "http://127.0.0.1:8091/street"
STATE = Path.home() / ".local/share/warehouse/tg_offset"


def api(token, method, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.load(r)


def to_inbox(text):
    req = urllib.request.Request(SERVER, data=json.dumps(
        {"text": text, "source": "phone"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)["id"]


def street_reply():
    """Актуальный список «🚶 Улица» по запросу (сервер сам держит и закреп)."""
    with urllib.request.urlopen(STREET_URL, timeout=5) as r:
        rows = json.load(r)
    if not rows:
        return "🚶 Улица: пусто. Гуляй налегке ✨"
    icons = {"focus": "🎯", "inbox": "📥", "rack": "🗄", "waiting": "⏳", "pallet_step": "🧱"}
    return f"🚶 Улица — {len(rows)}:\n" + "\n".join(
        f"{icons.get(b['shelf'], '•')} {b['raw_text']}" for b in rows)


def main():
    if not TOKEN_FILE.exists():
        sys.exit(f"Нет токена: {TOKEN_FILE}. Создай бота у @BotFather и положи токен туда.")
    token = TOKEN_FILE.read_text().strip()
    offset = int(STATE.read_text()) if STATE.exists() else 0
    STATE.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            upd = api(token, "getUpdates", offset=offset, timeout=60,
                      allowed_updates=["message"])
            for u in upd.get("result", []):
                offset = u["update_id"] + 1
                STATE.write_text(str(offset))
                msg = u.get("message") or {}
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                chat_id = msg["chat"]["id"]
                # сервер использует chat_id для закрепа «🚶 Улица»
                CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
                CHAT_FILE.write_text(str(chat_id))
                low = text.lower().lstrip("/")
                if low in ("start", "help"):
                    api(token, "sendMessage", chat_id=chat_id, text=(
                        "📦 Склад на связи. Любое сообщение = коробка в инбокс.\n"
                        "/улица - список уличных задач (он же висит в закрепе)"))
                    continue
                if low in ("улица", "street", "ул"):
                    api(token, "sendMessage", chat_id=chat_id, text=street_reply())
                    continue
                box_id = to_inbox(text)
                api(token, "sendMessage", chat_id=chat_id,
                    text=f"📦 #{box_id} в инбоксе")
        except Exception as e:
            print(f"[tg_worker] {e}", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
