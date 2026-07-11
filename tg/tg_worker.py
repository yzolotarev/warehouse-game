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
SERVER = "http://127.0.0.1:8091/inbox"
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
                box_id = to_inbox(text)
                api(token, "sendMessage", chat_id=msg["chat"]["id"],
                    text=f"📦 #{box_id} в инбоксе")
        except Exception as e:
            print(f"[tg_worker] {e}", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
