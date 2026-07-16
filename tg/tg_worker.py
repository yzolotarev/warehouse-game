#!/usr/bin/env python3
"""TG-вход склада: поллит личного бота, кладёт сообщения в инбокс (source=phone).

Токен: ~/.config/warehouse/tg_token (создать бота у @BotFather, положить токен в файл).
Без session-файлов - чистый Bot API long-poll.

Сторож-чат: сообщение с «?» в начале или reply на реплику бота → разговор.
Мозг живёт на сервере (POST /chat: LLM + история дня, общая с веб-виджетом);
воркер - чистый транспорт. Сервер лежит → «сторож спит», захват не страдает.
"""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

TOKEN_FILE = Path.home() / ".config/warehouse/tg_token"
CHAT_FILE = Path.home() / ".config/warehouse/tg_chat"   # его читает сервер для закрепа «🚶 Улица»
SERVER = "http://127.0.0.1:8091/inbox"
STREET_URL = "http://127.0.0.1:8091/street"
CHAT_URL = "http://127.0.0.1:8091/chat"
STATE = Path.home() / ".local/share/warehouse/tg_offset"


def storozh_answer(token, chat_id, question):
    try:
        api(token, "sendChatAction", chat_id=chat_id, action="typing")
    except Exception:
        pass
    answer = None
    try:
        req = urllib.request.Request(
            CHAT_URL, data=json.dumps({"text": question}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=150) as r:
            answer = json.load(r).get("answer")
    except Exception as e:
        print(f"[tg_worker] chat: {e}", flush=True)
    api(token, "sendMessage", chat_id=chat_id, text=answer or (
        "🌙 Сторож спит (мозг недоступен). Склад работает: "
        "сообщение без «?» уйдёт во входящие."))


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
    # белый список: хозяин = первый написавший (его id уже лежит в CHAT_FILE)
    allowed = int(CHAT_FILE.read_text().strip()) if CHAT_FILE.exists() else None
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
                if allowed is None:
                    # bootstrap: первый написавший становится хозяином,
                    # сервер использует этот chat_id для закрепа «🚶 Улица»
                    CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
                    CHAT_FILE.write_text(str(chat_id))
                    allowed = chat_id
                elif chat_id != allowed:
                    print(f"[tg_worker] чужой chat_id {chat_id} - игнор", flush=True)
                    continue
                low = text.lower().lstrip("/")
                if low in ("start", "help"):
                    api(token, "sendMessage", chat_id=chat_id, text=(
                        "📦 Склад на связи. Любое сообщение = коробка в инбокс.\n"
                        "?вопрос - поболтать со сторожем (о жизни и делах); "
                        "ответ (reply) на его реплику продолжает разговор\n"
                        "/улица - список уличных задач (он же висит в закрепе)"))
                    continue
                reply_from = (msg.get("reply_to_message") or {}).get("from") or {}
                if text.startswith("?") or reply_from.get("is_bot"):
                    q = text.lstrip("?").strip() or "привет"
                    threading.Thread(target=storozh_answer,
                                     args=(token, chat_id, q), daemon=True).start()
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
