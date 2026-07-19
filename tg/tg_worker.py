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
BASE = "http://127.0.0.1:8091"
SERVER = BASE + "/inbox"
STREET_URL = BASE + "/street"
CHAT_URL = BASE + "/chat"
STATE = Path.home() / ".local/share/warehouse/tg_offset"


def srv(path, payload=None):
    """Вызов склада: GET без payload, POST с ним."""
    req = urllib.request.Request(BASE + path)
    if payload is not None:
        req.data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# ─── 📱 Разбор инбокса из электрички ─────────────────────────────────────────
# Тот же конвейер, что в терминале (5 проходов, позиция живёт в boxes.triage_pass
# на сервере - начал в боте, продолжил на компе и наоборот). Кнопки = callback_data:
#   p|id|n  → ответ ведёт на проход n   m|id|shelf → отгрузка на полку
#   t|id    → выбор техпроекта          tc|id|i    → кандидат i из подсказки
#   tl|id|pg→ весь реестр, страница     tp|id|i    → проект i из реестра
#   z|id    → позже (в конец очереди)
TRI = {}  # chat_id -> {"ids": [box ids], "done": n} (сессия разбора, in-memory)

# порядок как в терминале (19.07): «техпроект?» в конце - спрашивается, когда
# уже ясно, что это дело и один конкретный шаг
PASSES = [
    ("Это дело?",
     [[("✅ Дело", "p|{id}|1"), ("🚫 Не дело", "p|{id}|2")],
      [("✔ уже сделано", "m|{id}|done"), ("🗑", "m|{id}|trash"), ("⤵ позже", "z|{id}")]]),
    ("Это один конкретный шаг?",
     [[("⚡ Один шаг", "p|{id}|3"), ("🧱 Проект", "m|{id}|pallet_step")],
      [("⤵ позже", "z|{id}")]]),
    ("Оставить как мысль?",
     [[("💭 Мысль", "m|{id}|mind"), ("🗑 Мусор", "m|{id}|trash")],
      [("⤵ позже", "z|{id}")]]),
    ("Это для технического проекта?",
     [[("🤖 Да, техпроект", "t|{id}"), ("🚫 Нет", "p|{id}|4")],
      [("✔ уже сделано", "m|{id}|done"), ("🗑", "m|{id}|trash"), ("⤵ позже", "z|{id}")]]),
    ("Займёшься на этой неделе?",
     [[("🎯 В фокус", "m|{id}|focus"), ("🗄 Не сейчас", "m|{id}|rack")],
      [("⤵ позже", "z|{id}")]]),
]


def kb(rows, box_id):
    return {"inline_keyboard": [
        [{"text": t, "callback_data": d.format(id=box_id)} for t, d in row]
        for row in rows]}


def tri_card(sess):
    """Текст + клавиатура текущей коробки (вопрос по её triage_pass)."""
    while sess["ids"]:
        try:
            boxes = {b["id"]: b for b in srv("/boxes?shelf=inbox")}
        except Exception:
            return "⚠ склад не отвечает", None
        b = boxes.get(sess["ids"][0])
        if b is None:              # разобрана с другого экрана - едем дальше
            sess["ids"].pop(0)
            continue
        p = max(0, min(len(PASSES) - 1, b.get("triage_pass") or 0))
        q, rows = PASSES[p]
        text = (f"📦 #{b['id']} · проход {p + 1}/{len(PASSES)} · осталось {len(sess['ids'])}\n"
                f"\n{b['raw_text']}\n\n❓ {q}")
        return text, kb(rows, b["id"])
    return f"Инбокс пуст. ✨ Разобрано за сессию: {sess['done']}", None


def tri_show(token, chat_id, msg_id=None):
    sess = TRI.get(chat_id)
    if not sess:
        return
    text, keyboard = tri_card(sess)
    params = {"chat_id": chat_id, "text": text}
    if keyboard:
        params["reply_markup"] = keyboard
    try:
        if msg_id:
            api(token, "editMessageText", message_id=msg_id, **params)
        else:
            api(token, "sendMessage", **params)
    except Exception as e:
        print(f"[tg_worker] tri_show: {e}", flush=True)


def tri_start(token, chat_id):
    try:
        ids = [b["id"] for b in srv("/boxes?shelf=inbox")]
    except Exception:
        api(token, "sendMessage", chat_id=chat_id, text="⚠ склад не отвечает")
        return
    TRI[chat_id] = {"ids": ids, "done": 0}
    tri_show(token, chat_id)


def tech_kb(box_id, text):
    """Клавиатура выбора техпроекта: угаданные кандидаты + вход в полный реестр."""
    try:
        cands = [c for c in srv("/tech_route_suggest",
                                {"id": box_id, "text": text})["candidates"]
                 if c.get("confidence") != "none"][:3]
    except Exception:
        cands = []
    rows = [[{"text": f"📁 {c['name']}" + (" ✓" if c["confidence"] == "high" else ""),
              "callback_data": f"tc|{box_id}|{i}"}] for i, c in enumerate(cands)]
    rows.append([{"text": "📋 Весь список", "callback_data": f"tl|{box_id}|0"},
                 {"text": "← назад", "callback_data": f"p|{box_id}|3"}])
    return {"inline_keyboard": rows}


def tech_list_kb(box_id, page):
    projects = srv("/tech_projects")["projects"]
    per = 8
    chunk = projects[page * per:(page + 1) * per]
    rows = [[{"text": f"📁 {p['name']}", "callback_data": f"tp|{box_id}|{page * per + i}"}]
            for i, p in enumerate(chunk)]
    nav = []
    if page > 0:
        nav.append({"text": "‹", "callback_data": f"tl|{box_id}|{page - 1}"})
    nav.append({"text": "← назад", "callback_data": f"p|{box_id}|3"})
    if (page + 1) * per < len(projects):
        nav.append({"text": "›", "callback_data": f"tl|{box_id}|{page + 1}"})
    rows.append(nav)
    return {"inline_keyboard": rows}


def tri_callback(token, cq):
    """Кнопка конвейера. Решения едут в те же ручки, что жмёт терминал."""
    chat_id = cq["message"]["chat"]["id"]
    msg_id = cq["message"]["message_id"]
    sess = TRI.get(chat_id)
    try:
        api(token, "answerCallbackQuery", callback_query_id=cq["id"])
    except Exception:
        pass
    if not sess:
        return
    parts = (cq.get("data") or "").split("|")
    op = parts[0]
    box_id = int(parts[1]) if len(parts) > 1 else 0

    def cur_text():
        try:
            for b in srv("/boxes?shelf=inbox"):
                if b["id"] == box_id:
                    return b["raw_text"]
        except Exception:
            pass
        return ""

    try:
        if op == "p":      # переход на проход n (персист на сервере)
            srv("/triage_pass", {"id": box_id, "p": int(parts[2])})
            tri_show(token, chat_id, msg_id)
        elif op == "m":    # отгрузка на полку - коробка разобрана
            srv("/move", {"id": box_id, "to": parts[2]})
            if box_id in sess["ids"]:
                sess["ids"].remove(box_id)
            sess["done"] += 1
            tri_show(token, chat_id, msg_id)
        elif op == "z":    # позже - в конец очереди
            if box_id in sess["ids"]:
                sess["ids"].remove(box_id)
                sess["ids"].append(box_id)
            tri_show(token, chat_id, msg_id)
        elif op == "t":    # меню техпроектов
            api(token, "editMessageText", chat_id=chat_id, message_id=msg_id,
                text=f"📡 #{box_id} - в какой проект?\n\n{cur_text()}",
                reply_markup=tech_kb(box_id, cur_text()))
        elif op == "tl":   # полный реестр, страница
            api(token, "editMessageText", chat_id=chat_id, message_id=msg_id,
                text=f"📡 #{box_id} - выбери проект:",
                reply_markup=tech_list_kb(box_id, int(parts[2])))
        elif op in ("tc", "tp"):  # выбран проект - отправляем
            text = cur_text()
            if op == "tc":
                cands = [c for c in srv("/tech_route_suggest",
                                        {"id": box_id, "text": text})["candidates"]
                         if c.get("confidence") != "none"]
                proj = cands[int(parts[2])]
            else:
                proj = srv("/tech_projects")["projects"][int(parts[2])]
            srv("/tech_route_confirm", {"id": box_id, "text": text,
                                        "project_name": proj["name"],
                                        "project_path": proj["path"]})
            if box_id in sess["ids"]:
                sess["ids"].remove(box_id)
            sess["done"] += 1
            tri_show(token, chat_id, msg_id)
    except Exception as e:
        print(f"[tg_worker] tri_callback: {e}", flush=True)
        try:
            api(token, "sendMessage", chat_id=chat_id,
                text=f"⚠ не получилось ({e}) - карточка ниже свежая")
        except Exception:
            pass
        tri_show(token, chat_id)


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
                      allowed_updates=["message", "callback_query"])
            for u in upd.get("result", []):
                offset = u["update_id"] + 1
                STATE.write_text(str(offset))
                cq = u.get("callback_query")
                if cq:
                    if allowed is not None and cq["message"]["chat"]["id"] != allowed:
                        continue
                    tri_callback(token, cq)
                    continue
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
                        "/разбор - разобрать инбокс кнопками (для электрички)\n"
                        "?вопрос - поболтать со сторожем (о жизни и делах); "
                        "ответ (reply) на его реплику продолжает разговор\n"
                        "/улица - список уличных задач (он же висит в закрепе)"))
                    continue
                if low in ("разбор", "razbor", "triage", "р", "p", "r"):
                    tri_start(token, chat_id)
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
