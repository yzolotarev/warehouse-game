#!/usr/bin/env python3
"""Склад: сервер-демон (часть 1 - фундамент).

Владеет БД коробок. Единственный источник истины.
API: POST /inbox, GET /boxes, POST /move, GET /health
"""
import difflib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

CALL_AT = os.environ.get("WAREHOUSE_CALL", "21:30")     # зов
TRUCK_AT = os.environ.get("WAREHOUSE_TRUCK", "22:00")    # фура + стопор-чек
MORNING_AT = os.environ.get("WAREHOUSE_MORNING", "09:30")  # утро: напомнить ⭐

DB_PATH = Path(os.environ.get("WAREHOUSE_DB",
                              Path.home() / ".local/share/warehouse/warehouse.db"))
PORT = int(os.environ.get("WAREHOUSE_PORT", "8091"))
# папка проектов в Obsidian: паллета может быть привязана к md-файлу/папке (контекст проекта)
OBSIDIAN_PROJECTS = Path(os.environ.get(
    "WAREHOUSE_OBSIDIAN", Path.home() / "Obsidian/9ListsHybrid/2nd brain/1. Проекты"))

SHELVES = {"inbox", "focus", "rack", "pallet_step", "waiting", "done", "trash", "mind", "archived"}
TRIAGE_POINTS = 1     # за разобранную коробку
RITUAL_POINTS = 10    # за инбокс, разобранный до нуля
DONE_POINTS = 3       # за сделанную задачу
SERIES_POINTS = 2     # за каждый следующий шаг той же паллеты за день (батчинг)
REVIEW_POINTS = 25    # за недельную пересборку
REST_POINTS = 2       # за преднамеренный отдых (кофейня, не чаще раза в 30 мин)
STALE_FOCUS_DAYS = 3    # фокус без движения → пыль
STALE_PALLET_DAYS = 30  # паллета без движения → заморозка

SCHEMA = """
CREATE TABLE IF NOT EXISTS boxes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_text TEXT NOT NULL,
  born_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  source TEXT NOT NULL DEFAULT 'pc',
  shelf TEXT NOT NULL DEFAULT 'inbox',
  context TEXT,
  grp TEXT,
  pallet_id INTEGER,
  reward INTEGER,
  glow_timer TEXT,
  goal TEXT,
  starred INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS moves(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  box_id INTEGER NOT NULL REFERENCES boxes(id),
  at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  from_shelf TEXT,
  to_shelf TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pallets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  done_criteria TEXT,
  plan TEXT,
  frozen INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS racks(
  context TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS points(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  amount INTEGER NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flags(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  kind TEXT NOT NULL,
  payload TEXT
);
"""


def log_event(conn, kind, **payload):
    """Тотальный журнал: каждое действие системы и человека - строка датасета."""
    conn.execute("INSERT INTO events(kind, payload) VALUES(?,?)",
                 (kind, json.dumps(payload, ensure_ascii=False)))


def award(conn, amount, reason):
    conn.execute("INSERT INTO points(amount, reason) VALUES(?,?)", (amount, reason))


def get_flag(conn, key, default=""):
    row = conn.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_flag(conn, key, value):
    conn.execute("INSERT INTO flags(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(boxes)")}
        if "starred" not in cols:
            conn.execute("ALTER TABLE boxes ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
        if "ai_suggest" not in cols:
            conn.execute("ALTER TABLE boxes ADD COLUMN ai_suggest TEXT")
        if "triage_pass" not in cols:
            # позиция в конвейере триажа (0-4) - переживает reload/undo, чтобы
            # промежуточные ответы ("Это дело?" и т.п.) не терялись при перезагрузке
            conn.execute("ALTER TABLE boxes ADD COLUMN triage_pass INTEGER NOT NULL DEFAULT 0")
        if "street" not in cols:
            # уличная задача (забрать заказ, купить, отнести) - попадает в
            # закреп «🚶 Улица» в Telegram; ставится автоматом, руками не вводится
            conn.execute("ALTER TABLE boxes ADD COLUMN street INTEGER NOT NULL DEFAULT 0")
        if "street_manual" not in cols:
            # человек руками выбрал контекст улицы - его решение сильнее LLM:
            # пересборка стеллажа не смеет перекинуть такую коробку в другой контекст
            conn.execute("ALTER TABLE boxes ADD COLUMN street_manual INTEGER NOT NULL DEFAULT 0")
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(pallets)")}
        if "note_path" not in pcols:
            conn.execute("ALTER TABLE pallets ADD COLUMN note_path TEXT")


app = FastAPI(title="warehouse")
app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Chrome --app охотно кэширует страницы - обновления UI не должны прятаться."""
    resp = await call_next(request)
    ct = resp.headers.get("content-type", "")
    if "html" in ct or "css" in ct or "javascript" in ct:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/style.css")
def style_css():
    return FileResponse(Path(__file__).parent / "style.css", media_type="text/css")


@app.get("/theme.js")
def theme_js():
    return FileResponse(Path(__file__).parent / "theme.js", media_type="text/javascript")


class InboxItem(BaseModel):
    text: str
    source: str = "pc"


class Move(BaseModel):
    id: int
    to: str
    context: str | None = None
    grp: str | None = None
    wait_mode: str = "grow"       # grow (1-3-7-14) | shrink (14-7-3-1) | every
    wait_days: int | None = None  # для every: каждые N дней


# мета-бэклог: идеи ПРО САМ склад/инструменты для его правки - не мешают
# основному потоку триажа, батчатся отдельным хвостом (см. startTriage в terminal.html)
META_KEYWORDS = ("склад", "инбокс", "паллет", "триаж", "claude", "openclaude", "mcp",
                  "graphify", "gemini", "web2api", "датасет", "dashboard", "mindmap")


def _is_meta(text):
    t = text.lower()
    return any(k in t for k in META_KEYWORDS)


def _find_similar_trashed(conn, text, threshold=0.62):
    """Антидубликат: локальный fuzzy-match без LLM/эмбеддингов - не блокирует
    захват, только мягкая подсказка "ты это уже выкидывал"."""
    t = text.lower().strip()
    rows = conn.execute(
        "SELECT id, raw_text FROM boxes WHERE shelf='trash' "
        "AND born_at >= datetime('now','-30 days') ORDER BY born_at DESC LIMIT 200").fetchall()
    best = None
    for r in rows:
        ratio = difflib.SequenceMatcher(None, t, r["raw_text"].lower().strip()).ratio()
        if ratio >= threshold and (not best or ratio > best[1]):
            best = (r, ratio)
    return best[0] if best else None


AI_SUGGEST_VOCAB = {"ШАГ": "step", "ПРОЕКТ": "project", "МЫСЛЬ": "thought", "МУСОР": "trash"}

# уличные задачи: regex-страховка (мгновенно, без LLM) + LLM уточняет при классификации
STREET_RE = re.compile(
    r"забрать|заказ|доставк|купить|магазин|аптек|почт[аеуы]|\bмфц\b|отнести|"
    r"вернуть заказ|сходить|съездить|вынести|банкомат|шиномонтаж|парикмахер|барбер",
    re.IGNORECASE)


def _ai_classify_box(box_id, text):
    """Авто-триаж: фоновая LLM-предподготовка (единственное место, где комфорт
    склада реально тратит web2api). Fire-and-forget - при любой ошибке/недоступности
    бэкенда просто не проставляет подсказку, триаж работает как обычно."""
    try:
        r = subprocess.run(
            ["llm-brains", "--backend", "web2api", "--max-tokens", "20",
             "--system",
             "Классифицируешь короткие заметки личного таск-трекера. "
             "Ответь СТРОГО двумя словами через пробел, без пояснений. "
             "Первое слово: "
             "ШАГ (конкретное дело, можно сделать за один присест), "
             "ПРОЕКТ (дело из нескольких шагов или пока неясно как), "
             "МЫСЛЬ (не дело - наблюдение, ссылка, идея на будущее), "
             "МУСОР (случайный шум). "
             "Второе слово: УЛИЦА (задача требует физически выйти из дома: "
             "забрать, купить, отнести, сходить) или ДОМА.",
             text],
            capture_output=True, text=True, timeout=200)  # web2api - веб-скрейпинг, не API: измерено 30-60с+ на реальных промптах
        if r.returncode != 0:
            print(f"[ai_classify] #{box_id} llm-brains rc={r.returncode} stderr={r.stderr[:300]!r}")
            return
        answer = r.stdout.strip().upper()
        kind = next((v for k, v in AI_SUGGEST_VOCAB.items() if k in answer), None)
        if not kind:
            print(f"[ai_classify] #{box_id} unparsed answer={r.stdout[:200]!r}")
            return
        street = 1 if "УЛИЦА" in answer else 0
        with db() as conn:
            # street только повышается (regex при захвате мог уже поставить 1)
            conn.execute("UPDATE boxes SET ai_suggest=?, street=MAX(street,?) "
                         "WHERE id=? AND shelf='inbox'", (kind, street, box_id))
            if street:
                set_flag(conn, "street_dirty",
                         datetime.now().isoformat(timespec="seconds"))
        print(f"[ai_classify] #{box_id} -> {kind}{' УЛИЦА' if street else ''}")
    except Exception as e:
        print(f"[ai_classify] #{box_id} exception: {e!r}")


# ─── Автоконтекстуализация стеллажа (перенос «сообщающихся сосудов» из Отложки) ──
# Человек контексты не вводит вообще: LLM периодически пересобирает ВЕСЬ стеллаж
# в 2-6 эмерджентных контекстов под фактическое содержимое. Модель отдаёт только
# структуру (ID → контекст), тексты коробок не трогаются; каждый класс ошибок LLM
# гасится детерминированной страховкой (пропущенная коробка сохраняет старый контекст).
RECONTEXT_DEBOUNCE_S = 120   # тишина после последнего приезда на стеллаж перед прогоном
RECONTEXT_MIN_BOXES = 2
_recontext_running = threading.Lock()
STREET_CTX = "🚶 Улица"   # канонический контекст: из него собирается TG-закреп для улицы


def _recontext_prompt(texts):
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    return (
        "Ты - система группировки отложенных задач. Верни ТОЛЬКО JSON строго по схеме:\n"
        '{"contexts": [{"title": "Название контекста с эмодзи", "ids": [1, 2]}]}\n\n'
        "ПРАВИЛА:\n"
        f"1. Каждый ID от 1 до {len(texts)} - ровно один раз.\n"
        "2. Создай 2-6 контекстов по смыслу задач. Если задача не вписывается - "
        "создай для неё новый контекст, НЕ сваливай в «Разное».\n"
        "3. title - короткий (2-4 слова), один эмодзи в начале, без Markdown.\n"
        "4. Похожие задачи (один инструмент/тема/место) - в один контекст.\n"
        f"5. ВАЖНО: задачи, требующие выйти из дома (забрать заказ, купить, отнести, "
        f"сходить/съездить куда-то физически) - в контекст ровно с названием «{STREET_CTX}».\n"
        "6. Никаких пояснений, только JSON.\n\n"
        f"ЗАДАЧИ:\n{lines}"
    )


def _extract_json(raw):
    """LLM может обернуть JSON в ```-заборы или прозу - берём первый {...} блок."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"нет JSON в ответе: {raw[:200]!r}")
    return json.loads(m.group())


def _recontextualize_rack():
    """Полный прогон: стеллаж → LLM → новые контексты. Фон, 30-120с на web2api."""
    if not _recontext_running.acquire(blocking=False):
        return  # уже идёт
    try:
        with db() as conn:
            set_flag(conn, "recontext_dirty", "")
            rows = conn.execute(
                "SELECT id, raw_text, street_manual FROM boxes "
                "WHERE shelf='rack' ORDER BY id").fetchall()
        if len(rows) < RECONTEXT_MIN_BOXES:
            return
        ids = [r["id"] for r in rows]
        texts = [r["raw_text"] for r in rows]
        r = subprocess.run(
            ["llm-brains", "--max-tokens", "1500", _recontext_prompt(texts)],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"llm-brains rc={r.returncode}: {r.stderr[:200]}")
        data = _extract_json(r.stdout)
        # страховки: только int-ID в диапазоне, каждый один раз, чистый title
        seen, assign = set(), {}   # assign: box_id → контекст
        for ctx in data.get("contexts", []):
            if not isinstance(ctx, dict):
                continue
            title = re.sub(r"[*_`#\[\]{}]", "", str(ctx.get("title", ""))).strip()
            if not title:
                continue
            for raw_i in (ctx.get("ids") or ctx.get("task_ids") or []):
                try:
                    i = int(raw_i)
                except (TypeError, ValueError):
                    continue
                if 1 <= i <= len(ids) and i not in seen:
                    seen.add(i)
                    assign[ids[i - 1]] = title
        if not assign:
            raise ValueError("LLM не вернула ни одного валидного контекста")
        # keyword-override (страховка как в отложке): уличные по ключевым словам
        # или по ручному выбору человека едут в канонический контекст,
        # даже если LLM решила иначе
        by_id = {r["id"]: r["raw_text"] for r in rows}
        manual = {r["id"] for r in rows if r["street_manual"]}
        for box_id in list(assign):
            if box_id in manual or STREET_RE.search(by_id.get(box_id, "")):
                assign[box_id] = STREET_CTX
        with db() as conn:
            for box_id, title in assign.items():
                # коробка могла уехать со стеллажа, пока LLM думала - не трогаем;
                # street на стеллаже следует за контекстом (свежайшее суждение)
                conn.execute("UPDATE boxes SET context=?, street=? "
                             "WHERE id=? AND shelf='rack'",
                             (title, 1 if title == STREET_CTX else 0, box_id))
                conn.execute("INSERT OR IGNORE INTO racks(context) VALUES(?)", (title,))
            set_flag(conn, "street_dirty", datetime.now().isoformat(timespec="seconds"))
            # адаптивность: контексты умирают вместе с последней коробкой
            conn.execute("DELETE FROM racks WHERE context NOT IN "
                         "(SELECT DISTINCT context FROM boxes WHERE shelf='rack' "
                         "AND context IS NOT NULL)")
            titles = sorted(set(assign.values()))
            set_flag(conn, "recontext_state", json.dumps(
                {"at": datetime.now().isoformat(timespec="seconds"), "ok": True,
                 "boxes": len(assign), "contexts": titles, "fails": 0}, ensure_ascii=False))
            log_event(conn, "recontext", boxes=len(assign), contexts=titles,
                      skipped=len(ids) - len(assign))
        notify("🗄 Стеллаж пересобран", " · ".join(titles))
    except Exception as e:
        with db() as conn:
            try:
                fails = json.loads(get_flag(conn, "recontext_state") or "{}").get("fails", 0) + 1
            except ValueError:
                fails = 1
            set_flag(conn, "recontext_state", json.dumps(
                {"at": datetime.now().isoformat(timespec="seconds"), "ok": False,
                 "error": str(e)[:200], "fails": fails}, ensure_ascii=False))
            if fails < 3:
                # ретрай через дебаунс-паузу; после 3 фейлов подряд затихаем
                # до следующего приезда коробки или ручной кнопки 🤖
                set_flag(conn, "recontext_dirty",
                         datetime.now().isoformat(timespec="seconds"))
            log_event(conn, "recontext_fail", error=str(e)[:300], fails=fails)
    finally:
        _recontext_running.release()


@app.post("/recontext")
def recontext_now():
    """Ручной запуск пересборки контекстов стеллажа (идёт в фоне)."""
    if not _recontext_running.locked():
        threading.Thread(target=_recontextualize_rack, daemon=True).start()
    return {"ok": True, "running": True}


@app.get("/recontext_status")
def recontext_status():
    with db() as conn:
        dirty = get_flag(conn, "recontext_dirty")
        state = get_flag(conn, "recontext_state")
    return {"running": _recontext_running.locked(), "dirty": bool(dirty),
            "last": json.loads(state) if state else None}


# ─── Список «🚶 Улица» → закреп в Telegram ────────────────────────────────────
# Уличные задачи должны быть видны С ТЕЛЕФОНА вне дома: бот держит в личном чате
# ОДНО закреплённое сообщение и молча редактирует его при каждом изменении списка.
# chat_id пишет tg_worker при первом сообщении боту.
TG_TOKEN_FILE = Path.home() / ".config/warehouse/tg_token"
TG_CHAT_FILE = Path.home() / ".config/warehouse/tg_chat"
STREET_SHELVES = ("focus", "inbox", "rack", "waiting", "pallet_step")
_street_syncing = threading.Lock()


def _street_rows(conn):
    marks = ",".join("?" * len(STREET_SHELVES))
    return conn.execute(
        f"SELECT * FROM boxes WHERE street=1 AND shelf IN ({marks}) "
        "ORDER BY CASE shelf WHEN 'focus' THEN 0 WHEN 'inbox' THEN 1 "
        "WHEN 'waiting' THEN 2 WHEN 'pallet_step' THEN 3 ELSE 4 END, id",
        STREET_SHELVES).fetchall()


@app.get("/street")
def street_list():
    with db() as conn:
        return [dict(r) for r in _street_rows(conn)]


class Rename(BaseModel):
    id: int
    text: str


@app.post("/rename")
def rename_box(r: Rename):
    """Задачи мутируют по ходу жизни («чекни авито» → «забрать заказ самому») -
    название должно уметь меняться, история остаётся в events."""
    text = r.text.strip()
    if not text:
        raise HTTPException(400, "empty text")
    with db() as conn:
        row = conn.execute("SELECT raw_text, street FROM boxes WHERE id=?",
                           (r.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such box")
        # новое название может сделать задачу уличной; вниз автоматом не снимаем
        street = row["street"] or (1 if STREET_RE.search(text) else 0)
        conn.execute("UPDATE boxes SET raw_text=?, street=? WHERE id=?",
                     (text, street, r.id))
        if street or row["street"]:
            set_flag(conn, "street_dirty", datetime.now().isoformat(timespec="seconds"))
        log_event(conn, "rename", id=r.id, old=row["raw_text"], new=text)
    return {"ok": True, "street": street}


class StreetMark(BaseModel):
    id: int
    street: int = 1


@app.post("/street_mark")
def street_mark(s: StreetMark):
    """Ручной выбор контекста улицы (или снятие). Ручное решение сильнее LLM."""
    val = 1 if s.street else 0
    with db() as conn:
        row = conn.execute("SELECT shelf FROM boxes WHERE id=?", (s.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such box")
        conn.execute("UPDATE boxes SET street=?, street_manual=? WHERE id=?",
                     (val, val, s.id))
        if row["shelf"] == "rack":
            if val:
                conn.execute("UPDATE boxes SET context=? WHERE id=?", (STREET_CTX, s.id))
                conn.execute("INSERT OR IGNORE INTO racks(context) VALUES(?)", (STREET_CTX,))
            else:
                # сняли улицу на стеллаже - контекст переопределит ближайшая пересборка
                conn.execute("UPDATE boxes SET context=NULL WHERE id=?", (s.id,))
                set_flag(conn, "recontext_dirty",
                         datetime.now().isoformat(timespec="seconds"))
        set_flag(conn, "street_dirty", datetime.now().isoformat(timespec="seconds"))
        log_event(conn, "street_mark", id=s.id, street=val, manual=True)
    return {"ok": True, "street": val}


def _tg_api(method, **params):
    token = TG_TOKEN_FILE.read_text().strip()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def street_text(rows):
    if not rows:
        return "🚶 Улица: пусто. Гуляй налегке ✨"
    icons = {"focus": "🎯", "inbox": "📥", "rack": "🗄", "waiting": "⏳", "pallet_step": "🧱"}
    lines = [f"🚶 Улица — {len(rows)}:"]
    for r in rows:
        lines.append(f"{icons.get(r['shelf'], '•')} {r['raw_text']}")
    lines.append(f"\nобновлено {datetime.now().strftime('%d.%m %H:%M')}")
    return "\n".join(lines)


def _sync_street_tg():
    """Обновить закреп «🚶 Улица» в TG. Редактирование - тихое, без уведомлений."""
    if not (TG_TOKEN_FILE.exists() and TG_CHAT_FILE.exists()):
        return  # бот ещё не подключён: флаг подождёт, синк догонит после настройки
    if not _street_syncing.acquire(blocking=False):
        return
    try:
        with db() as conn:
            set_flag(conn, "street_dirty", "")
            rows = _street_rows(conn)
            msg_id = get_flag(conn, "tg_street_msg")
        text = street_text(rows)
        chat = TG_CHAT_FILE.read_text().strip()
        if msg_id:
            try:
                _tg_api("editMessageText", chat_id=chat, message_id=int(msg_id), text=text)
                return
            except urllib.error.HTTPError as e:
                if "message is not modified" in e.read().decode(errors="replace"):
                    return  # список не изменился - это не ошибка
                # закреп удалён/недоступен - перевыставляем новым сообщением ниже
        r = _tg_api("sendMessage", chat_id=chat, text=text, disable_notification=True)
        new_id = r["result"]["message_id"]
        _tg_api("pinChatMessage", chat_id=chat, message_id=new_id, disable_notification=True)
        with db() as conn:
            set_flag(conn, "tg_street_msg", str(new_id))
            log_event(conn, "street_pin", msg_id=new_id, boxes=len(rows))
    except Exception as e:
        with db() as conn:
            log_event(conn, "street_tg_fail", error=str(e)[:200])
    finally:
        _street_syncing.release()


@app.post("/inbox")
def add_inbox(item: InboxItem):
    text = item.text.strip()
    if not text:
        raise HTTPException(400, "empty text")
    with db() as conn:
        similar = _find_similar_trashed(conn, text)
        street = 1 if STREET_RE.search(text) else 0
        cur = conn.execute(
            "INSERT INTO boxes(raw_text, source, grp, street) VALUES(?, ?, ?, ?)",
            (text, item.source, "meta" if _is_meta(text) else None, street))
        box_id = cur.lastrowid
        if street:
            set_flag(conn, "street_dirty", datetime.now().isoformat(timespec="seconds"))
        conn.execute(
            "INSERT INTO moves(box_id, from_shelf, to_shelf) VALUES(?, NULL, 'inbox')",
            (box_id,))
        log_event(conn, "inbox_add", id=box_id, source=item.source, text=text)
    threading.Thread(target=_ai_classify_box, args=(box_id, text), daemon=True).start()
    return {"id": box_id,
            "similar_trashed": {"id": similar["id"], "text": similar["raw_text"]} if similar else None}


class TriagePassUpdate(BaseModel):
    id: int
    p: int


@app.post("/triage_pass")
def triage_pass(t: TriagePassUpdate):
    """Персист позиции товара в конвейере триажа - без этого reload посреди
    разбора откатывает все промежуточные ответы ("Это дело?" и т.д.), потому
    что route() двигает только in-memory состояние браузера, а на сервер
    уходит лишь финальная отгрузка."""
    with db() as conn:
        conn.execute("UPDATE boxes SET triage_pass=? WHERE id=? AND shelf='inbox'",
                     (max(0, min(4, t.p)), t.id))
    return {"ok": True}


@app.get("/boxes")
def list_boxes(shelf: str = "inbox", limit: int = 200):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM boxes WHERE shelf=? ORDER BY born_at DESC LIMIT ?",
            (shelf, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # петля: сколько раз коробку откатывали обратно в инбокс после решения -
            # сигнал для эскалации вопроса в триаже (не даём молча гонять по кругу)
            d["loop_count"] = conn.execute(
                "SELECT COUNT(*) c FROM moves WHERE box_id=? AND to_shelf='inbox' AND from_shelf IS NOT NULL",
                (r["id"],)).fetchone()["c"]
            out.append(d)
    return out


@app.get("/inbox_dwell")
def inbox_dwell():
    """Сколько минут самый старый нетронутый инбокс-объект ждёт решения -
    для dwell-triggered peek (напоминание по факту застоя, не по расписанию)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT b.id, COALESCE("
            "  (SELECT MAX(at) FROM moves WHERE box_id=b.id AND to_shelf='inbox'), b.born_at"
            ") AS entered FROM boxes b WHERE b.shelf='inbox'").fetchall()
    if not rows:
        return {"oldest_minutes": 0, "count": 0}
    now = datetime.now()
    ages = [(now - datetime.fromisoformat(r["entered"])).total_seconds() / 60 for r in rows]
    return {"oldest_minutes": round(max(ages)), "count": len(rows)}


@app.post("/move")
def move_box(m: Move):
    if m.to not in SHELVES:
        raise HTTPException(400, f"unknown shelf: {m.to}")
    with db() as conn:
        row = conn.execute("SELECT shelf, pallet_id, street FROM boxes WHERE id=?", (m.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such box")
        # Физический стопор: пока инбокс не разобран, двигаются только коробки
        # ИЗ инбокса (разбор) и НА инбокс (откат решения)
        if get_flag(conn, "blocked") and row["shelf"] != "inbox" and m.to != "inbox":
            raise HTTPException(423, "склад стоит: сначала разбери инбокс")
        conn.execute(
            "UPDATE boxes SET shelf=?, context=COALESCE(?, context), grp=COALESCE(?, grp) WHERE id=?",
            (m.to, m.context, m.grp, m.id))
        if m.to == "inbox":
            # общий откат в инбокс = начать разбор заново, конвейерная позиция не при делах
            # (специфический undo() в терминале сам восстановит нужный проход отдельным вызовом)
            conn.execute("UPDATE boxes SET triage_pass=0 WHERE id=?", (m.id,))
        if m.to != "focus":
            # звезда живёт только в фокусе: ушла коробка — погасла звезда
            conn.execute("UPDATE boxes SET starred=0 WHERE id=?", (m.id,))
        conn.execute(
            "INSERT INTO moves(box_id, from_shelf, to_shelf) VALUES(?, ?, ?)",
            (m.id, row["shelf"], m.to))
        if m.context:
            conn.execute("INSERT OR IGNORE INTO racks(context) VALUES(?)", (m.context,))
        if m.to == "rack":
            # приезд на стеллаж → отложенный автопрогон контекстуализации
            # (дебаунс в планировщике: ждём затишья, чтобы сгруппировать пачкой)
            set_flag(conn, "recontext_dirty", datetime.now().isoformat(timespec="seconds"))
        if row["street"]:
            # уличная коробка сменила полку (сделана/выкинута/в фокус) → освежить TG-закреп
            set_flag(conn, "street_dirty", datetime.now().isoformat(timespec="seconds"))
        if m.to == "waiting":
            if m.wait_mode == "every":
                sched = {"seq": [max(1, m.wait_days or 3)], "i": 0, "mode": "every"}
            elif m.wait_mode == "shrink":
                sched = {"seq": [14, 7, 3, 1], "i": 0, "mode": "shrink"}
            else:
                sched = {"seq": [1, 3, 7, 14], "i": 0, "mode": "grow"}
            conn.execute(
                "UPDATE boxes SET glow_timer=? WHERE id=?",
                (json.dumps({**sched, "next": _add_days(sched["seq"][0])}), m.id))
        pts = 0
        if row["shelf"] == "inbox" and m.to != "inbox":
            pts += TRIAGE_POINTS
            award(conn, TRIAGE_POINTS, f"триаж #{m.id}")
            left = conn.execute(
                "SELECT COUNT(*) c FROM boxes WHERE shelf='inbox'").fetchone()["c"]
            if left == 0:
                set_flag(conn, "blocked", "")
                if get_flag(conn, "last_ritual") != date.today().isoformat():
                    pts += RITUAL_POINTS
                    award(conn, RITUAL_POINTS, "инбокс разобран до нуля")
                    set_flag(conn, "last_ritual", date.today().isoformat())
        elif row["shelf"] != "inbox" and m.to == "inbox":
            # откат решения: очко триажа возвращается в кассу
            pts -= TRIAGE_POINTS
            award(conn, -TRIAGE_POINTS, f"откат на приёмку #{m.id}")
        if m.to == "done":
            pts += DONE_POINTS
            award(conn, DONE_POINTS, f"сделано #{m.id}")
            if row["pallet_id"]:
                prev = conn.execute(
                    "SELECT COUNT(*) c FROM moves mv JOIN boxes b ON b.id=mv.box_id "
                    "WHERE mv.to_shelf='done' AND date(mv.at)=date('now','localtime') "
                    "AND b.pallet_id=? AND mv.box_id!=?",
                    (row["pallet_id"], m.id)).fetchone()["c"]
                if prev:
                    pts += SERIES_POINTS
                    award(conn, SERIES_POINTS, f"серия паллеты #{row['pallet_id']}")
        log_event(conn, "move", id=m.id, frm=row["shelf"], to=m.to,
                  context=m.context, points=pts)
    return {"ok": True, "points": pts}


def _add_days(n):
    from datetime import timedelta
    return (date.today() + timedelta(days=n)).isoformat()


@app.get("/glow")
def glow():
    """Загоревшиеся товары ожидания: пора чекнуть."""
    today = date.today().isoformat()
    out = []
    with db() as conn:
        for r in conn.execute("SELECT * FROM boxes WHERE shelf='waiting'"):
            t = json.loads(r["glow_timer"] or "{}")
            if t.get("next") and t["next"] <= today:
                out.append(dict(r))
    return out


class WaitCheck(BaseModel):
    id: int
    action: str  # done | wait | delete


@app.post("/wait_check")
def wait_check(w: WaitCheck):
    with db() as conn:
        row = conn.execute("SELECT * FROM boxes WHERE id=? AND shelf='waiting'",
                           (w.id,)).fetchone()
        if not row:
            raise HTTPException(404, "не в ожидании")
        if w.action == "wait":
            t = json.loads(row["glow_timer"] or '{"seq":[1,3,7,14],"i":0}')
            t["i"] = min(t["i"] + 1, len(t["seq"]) - 1)
            t["next"] = _add_days(t["seq"][t["i"]])
            conn.execute("UPDATE boxes SET glow_timer=? WHERE id=?",
                         (json.dumps(t), w.id))
            log_event(conn, "wait_more", id=w.id, next=t["next"])
            return {"ok": True, "next": t["next"]}
    target = {"done": "done", "delete": "trash"}.get(w.action)
    if not target:
        raise HTTPException(400, "action: done|wait|delete")
    return move_box(Move(id=w.id, to=target))


class PalletNew(BaseModel):
    title: str
    done_criteria: str = ""
    from_box: int | None = None   # мутный товар, из которого родилась
    first_step: str = ""          # одношаговый шаг → сразу в фокус


def _check_blocked(conn):
    if get_flag(conn, "blocked"):
        raise HTTPException(423, "склад стоит: сначала разбери инбокс")


@app.post("/pallet")
def pallet_new(p: PalletNew):
    with db() as conn:
        _check_blocked(conn)
        cur = conn.execute(
            "INSERT INTO pallets(title, done_criteria) VALUES(?,?)",
            (p.title.strip(), p.done_criteria.strip()))
        pid = cur.lastrowid
        # проект приехал указателем из Obsidian → сразу привязываем его заметку
        note = _obsidian_match(p.title) if "obsidian" in p.title.lower() else None
        if p.from_box:
            conn.execute("UPDATE boxes SET pallet_id=? WHERE id=?", (pid, p.from_box))
            if not note:
                src = conn.execute("SELECT raw_text FROM boxes WHERE id=?",
                                   (p.from_box,)).fetchone()
                if src and "obsidian" in src["raw_text"].lower():
                    note = _obsidian_match(src["raw_text"])
        if note:
            conn.execute("UPDATE pallets SET note_path=? WHERE id=?", (note, pid))
        step_id = None
        if p.first_step.strip():
            c2 = conn.execute(
                "INSERT INTO boxes(raw_text, source, shelf, pallet_id) "
                "VALUES(?, 'pallet', 'focus', ?)", (p.first_step.strip(), pid))
            step_id = c2.lastrowid
            conn.execute("INSERT INTO moves(box_id, from_shelf, to_shelf) "
                         "VALUES(?, NULL, 'focus')", (step_id,))
        log_event(conn, "pallet_new", pid=pid, from_box=p.from_box,
                  title=p.title.strip(), first_step=bool(p.first_step.strip()))
    return {"pallet_id": pid, "step_id": step_id}


def _norm(s):
    s = s.lower().replace("ё", "е").replace("—", "-").replace("_", " ")
    return " ".join("".join(ch for ch in s if ch.isalnum() or ch.isspace()).split())


def _obsidian_match(text):
    """Лучший кандидат в папке проектов под текст коробки/название."""
    if not OBSIDIAN_PROJECTS.is_dir():
        return None
    t = _norm(text)
    best, best_score = None, 0.0
    for p in OBSIDIAN_PROJECTS.iterdir():
        if p.name.startswith(".") or p.name == "Архив проектов":
            continue
        if not (p.is_dir() or p.suffix == ".md"):
            continue
        stem = _norm(p.stem if p.is_file() else p.name)
        if not stem:
            continue
        if stem in t or t in stem:
            score = 1.0
        else:
            a, b = set(stem.split()), set(t.split())
            score = len(a & b) / max(1, min(len(a), len(b)))
        if score > best_score:
            best, best_score = p, score
    return str(best) if best and best_score >= 0.5 else None


def _note_file(note_path):
    """Файл для чтения/дозаписи: папка проекта → Склад-заметки.md внутри неё."""
    p = Path(note_path)
    return p / "Склад-заметки.md" if p.is_dir() else p


def _check_note_path(path):
    p = Path(path).resolve()
    if not str(p).startswith(str(OBSIDIAN_PROJECTS.resolve())):
        raise HTTPException(400, "путь вне папки проектов Obsidian")
    return p


@app.get("/obsidian_files")
def obsidian_files():
    if not OBSIDIAN_PROJECTS.is_dir():
        return []
    out = []
    for p in sorted(OBSIDIAN_PROJECTS.iterdir()):
        if p.name.startswith(".") or not (p.is_dir() or p.suffix == ".md"):
            continue
        out.append({"name": p.stem if p.is_file() else p.name + "/",
                    "path": str(p), "dir": p.is_dir()})
    return out


class NoteAttach(BaseModel):
    id: int
    path: str


@app.post("/pallet/note_attach")
def pallet_note_attach(n: NoteAttach):
    p = _check_note_path(n.path)
    with db() as conn:
        conn.execute("UPDATE pallets SET note_path=? WHERE id=?", (str(p), n.id))
        log_event(conn, "note_attach", pid=n.id, path=str(p))
    return {"ok": True}


@app.get("/pallet_note")
def pallet_note(id: int):
    with db() as conn:
        row = conn.execute("SELECT note_path FROM pallets WHERE id=?", (id,)).fetchone()
    if not row or not row["note_path"]:
        return {"path": None, "content": "", "files": [], "hash": None}
    p = Path(row["note_path"])
    files = [f.name for f in sorted(p.iterdir())
             if not f.name.startswith(".")] if p.is_dir() else []
    nf = _note_file(row["note_path"])
    content = nf.read_text(errors="replace") if nf.is_file() else ""
    import hashlib
    return {"path": row["note_path"], "content": content, "files": files,
            "hash": hashlib.sha256(content.encode()).hexdigest()}


class NoteSave(BaseModel):
    id: int
    content: str
    base_hash: str | None = None


@app.post("/pallet/note_save")
def pallet_note_save(n: NoteSave):
    """Правка заметки прямо со склада. Предохранители:
    бэкап перед записью + optimistic lock (409, если файл менялся в Obsidian)."""
    import hashlib
    import shutil
    with db() as conn:
        row = conn.execute("SELECT title, note_path FROM pallets WHERE id=?",
                           (n.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such pallet")
        note_path = row["note_path"]
        if not note_path:
            safe = "".join(ch for ch in row["title"] if ch not in '/\\:*?"<>|')[:80].strip()
            p = OBSIDIAN_PROJECTS / f"{safe or 'проект-' + str(n.id)}.md"
            note_path = str(p)
            conn.execute("UPDATE pallets SET note_path=? WHERE id=?", (note_path, n.id))
        nf = _note_file(note_path)
        cur = nf.read_text(errors="replace") if nf.is_file() else ""
        if n.base_hash and hashlib.sha256(cur.encode()).hexdigest() != n.base_hash:
            raise HTTPException(409, "файл изменился в Obsidian - открой заметку заново")
        if nf.is_file() and cur.strip():
            bdir = DB_PATH.parent / "note_backups"
            bdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(nf, bdir / f"{datetime.now():%Y%m%d-%H%M%S}-{nf.name}")
        nf.parent.mkdir(parents=True, exist_ok=True)
        nf.write_text(n.content)
        log_event(conn, "note_save", pid=n.id, chars=len(n.content))
    return {"ok": True, "path": note_path,
            "hash": hashlib.sha256(n.content.encode()).hexdigest()}


class NoteAppend(BaseModel):
    id: int
    text: str


@app.post("/pallet/note_append")
def pallet_note_append(n: NoteAppend):
    """Write-only стрелка в Obsidian: дописать контекст к проекту."""
    text = n.text.strip()
    if not text:
        raise HTTPException(400, "пусто")
    with db() as conn:
        row = conn.execute("SELECT title, note_path FROM pallets WHERE id=?",
                           (n.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such pallet")
        note_path = row["note_path"]
        if not note_path:  # заметки ещё нет - создаём файл по названию проекта
            safe = "".join(ch for ch in row["title"] if ch not in '/\\:*?"<>|')[:80].strip()
            p = OBSIDIAN_PROJECTS / f"{safe or 'проект-' + str(n.id)}.md"
            if not p.exists():
                p.write_text(f"# {row['title']}\n")
            note_path = str(p)
            conn.execute("UPDATE pallets SET note_path=? WHERE id=?", (note_path, n.id))
        nf = _note_file(note_path)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(nf, "a") as f:
            f.write(f"\n- **склад {stamp}:** {text}\n")
        log_event(conn, "note_append", pid=n.id, chars=len(text))
    return {"ok": True, "path": note_path}


class PalletStep(BaseModel):
    pallet_id: int
    text: str


@app.post("/pallet/step")
def pallet_step(s: PalletStep):
    with db() as conn:
        _check_blocked(conn)
        cur = conn.execute(
            "INSERT INTO boxes(raw_text, source, shelf, pallet_id) "
            "VALUES(?, 'pallet', 'focus', ?)", (s.text.strip(), s.pallet_id))
        conn.execute("INSERT INTO moves(box_id, from_shelf, to_shelf) "
                     "VALUES(?, NULL, 'focus')", (cur.lastrowid,))
        log_event(conn, "pallet_step", pid=s.pallet_id, step_id=cur.lastrowid)
    return {"step_id": cur.lastrowid}


def _pallet_last_move(conn, p):
    row = conn.execute(
        "SELECT MAX(mv.at) m FROM moves mv JOIN boxes b ON b.id=mv.box_id "
        "WHERE b.pallet_id=?", (p["id"],)).fetchone()
    return (row["m"] or p["created_at"])[:10]


@app.get("/pallets")
def pallets_list():
    today = date.today()
    with db() as conn:
        pallets = [dict(r) for r in conn.execute(
            "SELECT * FROM pallets WHERE frozen=0 ORDER BY created_at DESC")]
        frozen = [dict(r) for r in conn.execute(
            "SELECT * FROM pallets WHERE frozen=1 ORDER BY created_at DESC")]
        for p in pallets + frozen:
            p["steps"] = [dict(r) for r in conn.execute(
                "SELECT * FROM boxes WHERE pallet_id=? ORDER BY id", (p["id"],))]
            p["idle_days"] = (today - date.fromisoformat(_pallet_last_move(conn, p))).days
            p["done_today"] = conn.execute(
                "SELECT COUNT(*) c FROM moves mv JOIN boxes b ON b.id=mv.box_id "
                "WHERE mv.to_shelf='done' AND date(mv.at)=date('now','localtime') "
                "AND b.pallet_id=?", (p["id"],)).fetchone()["c"]
        raw = [dict(r) for r in conn.execute(
            "SELECT * FROM boxes WHERE shelf='pallet_step' AND pallet_id IS NULL")]
    return {"pallets": pallets, "frozen": frozen, "unformed": raw,
            "series_points": SERIES_POINTS}


class PalletBump(BaseModel):
    id: int


@app.post("/pallet/bump")
def pallet_bump(b: PalletBump):
    """📌 На первое место: список сортируется по created_at DESC - освежаем дату."""
    with db() as conn:
        row = conn.execute("SELECT id FROM pallets WHERE id=?", (b.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such pallet")
        conn.execute("UPDATE pallets SET created_at=datetime('now','localtime') WHERE id=?",
                     (b.id,))
        log_event(conn, "pallet_bump", pid=b.id)
    return {"ok": True}


class PalletFreeze(BaseModel):
    id: int
    frozen: bool


@app.post("/pallet/freeze")
def pallet_freeze(f: PalletFreeze):
    with db() as conn:
        conn.execute("UPDATE pallets SET frozen=? WHERE id=?", (int(f.frozen), f.id))
        log_event(conn, "pallet_freeze", pid=f.id, frozen=f.frozen)
    return {"ok": True}


class PalletDelete(BaseModel):
    id: int


@app.post("/pallet/delete")
def pallet_delete(d: PalletDelete):
    """Проект не нужен: живые шаги → мусор, сделанные остаются в истории."""
    with db() as conn:
        _check_blocked(conn)
        row = conn.execute("SELECT id FROM pallets WHERE id=?", (d.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such pallet")
        live = conn.execute(
            "SELECT id, shelf FROM boxes WHERE pallet_id=? "
            "AND shelf NOT IN ('done','archived','trash')", (d.id,)).fetchall()
        for b in live:
            conn.execute("UPDATE boxes SET shelf='trash' WHERE id=?", (b["id"],))
            conn.execute("INSERT INTO moves(box_id, from_shelf, to_shelf) "
                         "VALUES(?, ?, 'trash')", (b["id"], b["shelf"]))
        conn.execute("DELETE FROM pallets WHERE id=?", (d.id,))
        log_event(conn, "pallet_delete", pid=d.id, trashed_steps=len(live))
    return {"ok": True, "trashed_steps": len(live)}


class Star(BaseModel):
    id: int
    on: bool


@app.post("/star")
def star(s: Star):
    """⭐ frontloading (канон Санга): вечером отметить 1-2 главных на завтра.

    Утром решений ноль - звезда уже горит. Максимум 2 (узкая окрестность внимания).
    """
    with db() as conn:
        row = conn.execute("SELECT shelf FROM boxes WHERE id=?", (s.id,)).fetchone()
        if not row or row["shelf"] != "focus":
            raise HTTPException(400, "звезда ставится только на товар в фокусе")
        if s.on:
            n = conn.execute(
                "SELECT COUNT(*) c FROM boxes WHERE shelf='focus' AND starred=1 AND id!=?",
                (s.id,)).fetchone()["c"]
            if n >= 2:
                raise HTTPException(409, "уже 2 ⭐ - больше окрестность внимания не вместит")
        conn.execute("UPDATE boxes SET starred=? WHERE id=?", (int(s.on), s.id))
        log_event(conn, "star", id=s.id, on=s.on)
    return {"ok": True, "starred": s.on}


@app.get("/focus")
def focus_list():
    """Фокус с ПДВ: награда видна ДО действия + возраст без движения. ⭐ первыми."""
    today = date.today()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM boxes WHERE shelf='focus' ORDER BY starred DESC, id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            last = conn.execute("SELECT MAX(at) m FROM moves WHERE box_id=?",
                                (r["id"],)).fetchone()["m"]
            d["idle_days"] = (today - date.fromisoformat(last[:10])).days if last else 0
            d["reward"] = DONE_POINTS
            d["series"] = 0
            d["pallet_title"] = None
            if r["pallet_id"]:
                p = conn.execute("SELECT title FROM pallets WHERE id=?",
                                 (r["pallet_id"],)).fetchone()
                d["pallet_title"] = p["title"] if p else None
                done_today = conn.execute(
                    "SELECT COUNT(*) c FROM moves mv JOIN boxes b ON b.id=mv.box_id "
                    "WHERE mv.to_shelf='done' AND date(mv.at)=date('now','localtime') "
                    "AND b.pallet_id=?", (r["pallet_id"],)).fetchone()["c"]
                if done_today:
                    d["series"] = SERIES_POINTS
            out.append(d)
    return {"items": out, "stale_days": STALE_FOCUS_DAYS}


@app.get("/pallets_page")
def pallets_page():
    return FileResponse(Path(__file__).parent / "pallets.html")


@app.get("/mirror")
def mirror():
    """Зеркало дня: лента коробок, рождённых сегодня + очки за сегодня."""
    today = date.today().isoformat()
    with db() as conn:
        boxes = [dict(r) for r in conn.execute(
            "SELECT * FROM boxes WHERE date(born_at)=? ORDER BY born_at", (today,))]
        pts = [dict(r) for r in conn.execute(
            "SELECT at, amount, reason FROM points WHERE date(at)=? ORDER BY at", (today,))]
        total = sum(p["amount"] for p in pts)
    return {"date": today, "boxes": boxes, "points": pts, "points_total": total}


def _review_due(conn):
    last = get_flag(conn, "last_week_review")
    if last:
        return (date.today() - date.fromisoformat(last)).days >= 7
    return date.today().weekday() == 6  # первая пересборка — в воскресенье


@app.get("/state")
def state():
    with db() as conn:
        counts = dict(conn.execute(
            "SELECT shelf, COUNT(*) FROM boxes GROUP BY shelf").fetchall())
        blocked = get_flag(conn, "blocked")
        total = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM points").fetchone()["s"]
        review_due = _review_due(conn)
        stars = [{"id": r["id"], "text": r["raw_text"]} for r in conn.execute(
            "SELECT id, raw_text FROM boxes WHERE shelf='focus' AND starred=1 ORDER BY id")]
        rest_until = get_flag(conn, "rest_until")
    return {"counts": counts, "blocked": bool(blocked), "blocked_since": blocked or None,
            "points_total": total, "call_at": CALL_AT, "truck_at": TRUCK_AT,
            "review_due": review_due, "stars": stars, "rest_until": rest_until or None}


def _pick_now(st, glow_items):
    """Порт pickNow() из hub.html: одна рекомендация вместо стены задач (constrained front door)."""
    c = st["counts"] or {}
    stars = st["stars"] or []
    hour = datetime.now().hour
    if st["blocked"]:
        return {"act": "⛔ Разблокировать склад: разобрать входящие",
                "why": "физика мира: пока входящие не разобраны, всё стоит", "url": "/terminal"}
    if c.get("inbox"):
        return {"act": f"📥 Разобрать входящие ({c['inbox']})",
                "why": "один вопрос на экран, вопросы сами разложат всё по полкам", "url": "/terminal"}
    if glow_items:
        return {"act": f"✨ Проверить ожидание ({len(glow_items)})",
                "why": "коробки загорелись: пора чекнуть, дождался ли", "url": "/terminal#glow"}
    if st["review_due"]:
        return {"act": "🧰 Недельная пересборка · +25 ⭐",
                "why": "столп №2: без неё система разваливается за две недели", "url": "/review"}
    if hour >= 20 and not stars and c.get("focus"):
        return {"act": "⭐ Отметить звезду на завтра",
                "why": "две минуты: выбери главное — утром думать не придётся", "url": "/focus_page"}
    if stars:
        return {"act": "★ Делать звезду: " + stars[0]["text"][:60],
                "why": "решение принято ещё вчера — просто начни", "url": "/focus_page"}
    if c.get("focus"):
        return {"act": f"🎯 Работать фокус ({c['focus']})",
                "why": "одна задача на экране — её и делай", "url": "/focus_page"}
    return {"act": "☕ Всё разобрано — отдых тоже работа",
            "why": "можно закинуть что-то во входящие или просто выдохнуть",
            "url": "/focus_page", "calm": True}


@app.get("/peek")
def peek():
    """Ambient peek: та же рекомендация, что в hub.html, без открытия окна (для tray/notify-send)."""
    st = state()
    now = _pick_now(st, glow())
    return {"now": now, "points_total": st["points_total"]}


@app.get("/review_data")
def review_data():
    """Итоги недели + очереди для пересборки: стеллажи, паллеты, заморозка."""
    with db() as conn:
        week_pts = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM points "
            "WHERE date(at) >= date('now','localtime','-6 days')").fetchone()["s"]
        shipped = conn.execute(
            "SELECT COUNT(DISTINCT box_id) c FROM moves WHERE to_shelf='done' "
            "AND date(at) >= date('now','localtime','-6 days')").fetchone()["c"]
        born = conn.execute(
            "SELECT COUNT(*) c FROM boxes "
            "WHERE date(born_at) >= date('now','localtime','-6 days')").fetchone()["c"]
        racks = [dict(r) for r in conn.execute(
            "SELECT * FROM boxes WHERE shelf='rack' ORDER BY context, id")]
        pallets = [dict(r) for r in conn.execute(
            "SELECT * FROM pallets WHERE frozen=0 ORDER BY created_at")]
        for p in pallets:
            p["steps"] = [dict(r) for r in conn.execute(
                "SELECT * FROM boxes WHERE pallet_id=? ORDER BY id", (p["id"],))]
            p["idle_days"] = (date.today()
                              - date.fromisoformat(_pallet_last_move(conn, p))).days
        frozen = [dict(r) for r in conn.execute(
            "SELECT * FROM pallets WHERE frozen=1 ORDER BY created_at")]
    return {"week_points": week_pts, "shipped": shipped, "born": born,
            "racks": racks, "pallets": pallets, "frozen": frozen,
            "reward": REVIEW_POINTS}


@app.post("/review_finish")
def review_finish():
    with db() as conn:
        _check_blocked(conn)  # пересборка — тоже мыслительная работа: при стопоре стоит
        today = date.today().isoformat()
        if get_flag(conn, "last_week_review") == today:
            return {"ok": True, "points": 0}  # уже пересобран сегодня
        award(conn, REVIEW_POINTS, "недельная пересборка")
        set_flag(conn, "last_week_review", today)
        log_event(conn, "review_finish", points=REVIEW_POINTS)
    return {"ok": True, "points": REVIEW_POINTS}


@app.get("/terminal")
def terminal_page():
    return FileResponse(Path(__file__).parent / "terminal.html")


@app.get("/")
def world_page():
    return FileResponse(Path(__file__).parent / "world.html")


@app.get("/hub")
def hub_page():
    return FileResponse(Path(__file__).parent / "hub.html")


@app.get("/focus_page")
def focus_page():
    return FileResponse(Path(__file__).parent / "focus.html")


@app.get("/proto")
def proto_page():
    return FileResponse(Path(__file__).parent.parent / "proto/warehouse-proto.html")


@app.get("/capture")
def capture_page():
    return FileResponse(Path(__file__).parent / "capture.html")


@app.get("/racks_page")
def racks_page():
    return FileResponse(Path(__file__).parent / "racks.html")


@app.get("/mind_page")
def mind_page():
    return FileResponse(Path(__file__).parent / "mind.html")


class RestStart(BaseModel):
    minutes: int = 5


@app.post("/rest_start")
def rest_start(r: RestStart):
    """Кофейня: преднамеренный отдых. Главное - встать из-за стола."""
    mins = max(1, min(r.minutes, 60))
    until = datetime.now() + timedelta(minutes=mins)
    with db() as conn:
        set_flag(conn, "rest_until", until.isoformat(timespec="seconds"))
        log_event(conn, "rest_start", minutes=mins)
    return {"ok": True, "until": until.isoformat(timespec="seconds")}


@app.post("/rest_finish")
def rest_finish():
    """Отдых дожит до конца → очки. Кулдаун 30 мин против кликер-фарма."""
    now = datetime.now()
    with db() as conn:
        until = get_flag(conn, "rest_until")
        if not until or now < datetime.fromisoformat(until):
            return {"ok": False, "points": 0}
        set_flag(conn, "rest_until", "")
        last = get_flag(conn, "last_rest_award")
        if last and (now - datetime.fromisoformat(last)).total_seconds() < 1800:
            return {"ok": True, "points": 0}
        award(conn, REST_POINTS, "отдых в кофейне")
        set_flag(conn, "last_rest_award", now.isoformat(timespec="seconds"))
        log_event(conn, "rest_finish", points=REST_POINTS)
    return {"ok": True, "points": REST_POINTS}


@app.get("/done_page")
def done_page():
    return FileResponse(Path(__file__).parent / "done.html")


@app.get("/review")
def review_page():
    return FileResponse(Path(__file__).parent / "review.html")


@app.get("/gemini_scene")
def gemini_scene_page():
    return FileResponse(Path(__file__).parent.parent / "proto/gemini-scene.html")


class ClientEvent(BaseModel):
    kind: str
    payload: dict = {}


@app.post("/event")
def client_event(e: ClientEvent):
    """События из UI (ответы триажа и т.п.) - в тот же журнал, kind с префиксом ui_."""
    kind = e.kind.strip()
    if not kind:
        raise HTTPException(400, "empty kind")
    with db() as conn:
        log_event(conn, f"ui_{kind}", **e.payload)
    return {"ok": True}


@app.get("/events")
def events_list(limit: int = 200, kind: str | None = None):
    with db() as conn:
        if kind:
            rows = conn.execute("SELECT * FROM events WHERE kind=? ORDER BY id DESC LIMIT ?",
                                (kind, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?",
                                (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/health")
def health():
    with db() as conn:
        counts = dict(conn.execute(
            "SELECT shelf, COUNT(*) FROM boxes GROUP BY shelf").fetchall())
    return {"ok": True, "db": str(DB_PATH), "counts": counts}


def notify(title, body, urgent=False):
    cmd = ["notify-send", title, body]
    if urgent:
        cmd[1:1] = ["-u", "critical"]
    env = dict(os.environ,
               DISPLAY=os.environ.get("DISPLAY", ":0"),
               DBUS_SESSION_BUS_ADDRESS=os.environ.get(
                   "DBUS_SESSION_BUS_ADDRESS",
                   f"unix:path=/run/user/{os.getuid()}/bus"))
    subprocess.run(cmd, env=env, timeout=5, check=False)


def scheduler():
    """Зов в CALL_AT; фура + стопор-чек в TRUCK_AT. Тикает раз в 30 сек."""
    fired = set()
    while True:
        now = datetime.now()
        hm = now.strftime("%H:%M")
        today = date.today().isoformat()
        # автоконтекстуализация: пачка коробок приехала на стеллаж и настало затишье
        dirty = get_flag_standalone("recontext_dirty")
        if dirty and not _recontext_running.locked():
            try:
                quiet = (now - datetime.fromisoformat(dirty)).total_seconds()
            except ValueError:
                quiet = RECONTEXT_DEBOUNCE_S
            if quiet >= RECONTEXT_DEBOUNCE_S:
                threading.Thread(target=_recontextualize_rack, daemon=True).start()
        # TG-закреп «🚶 Улица»: редактирование дешёвое, дебаунс не нужен -
        # обновляем на ближайшем тике после любого изменения уличного списка
        if (TG_TOKEN_FILE.exists() and TG_CHAT_FILE.exists()
                and get_flag_standalone("street_dirty") and not _street_syncing.locked()):
            threading.Thread(target=_sync_street_tg, daemon=True).start()
        if hm == MORNING_AT and ("morning", today) not in fired:
            fired.add(("morning", today))
            with db() as conn:
                stars = [r["raw_text"] for r in conn.execute(
                    "SELECT raw_text FROM boxes WHERE shelf='focus' AND starred=1")]
            if stars:  # утром решений ноль: звезда выбрана с вечера
                notify("⭐ Твоя звезда на сегодня", " · ".join(stars))
                with db() as conn:
                    log_event(conn, "sched_morning", stars=len(stars))
        if hm == CALL_AT and ("call", today) not in fired:
            fired.add(("call", today))
            with db() as conn:
                n = conn.execute("SELECT COUNT(*) c FROM boxes WHERE shelf='inbox'").fetchone()["c"]
                has_star = conn.execute(
                    "SELECT COUNT(*) c FROM boxes WHERE shelf='focus' AND starred=1").fetchone()["c"]
            if n:
                notify("📦 Склад зовёт", f"В инбоксе {n} коробок. Вечерний разбор: http://127.0.0.1:{PORT}/terminal")
            elif not has_star:
                # scripted action для худшего дня: 2 минуты - отметить 1 ⭐ на завтра
                notify("⭐ Две минуты", f"Отметь звезду на завтра: http://127.0.0.1:{PORT}/focus_page")
        if hm == CALL_AT and ("review", today) not in fired:
            fired.add(("review", today))
            with db() as conn:
                due = _review_due(conn)
            if due:
                notify("🧰 Недельная пересборка", f"Столп №2: пересобери склад. http://127.0.0.1:{PORT}/review")
        # страховочная автоконтекстуализация: пользователь (или событие-триггер)
        # за день так и не пересобрал стеллаж - вечером делаем сами, но только
        # если есть зачем: бесконтекстные коробки, свежие приезды или прошлый фейл
        if hm == TRUCK_AT and ("recontext", today) not in fired:
            fired.add(("recontext", today))
            with db() as conn:
                n_rack = conn.execute(
                    "SELECT COUNT(*) c FROM boxes WHERE shelf='rack'").fetchone()["c"]
                null_ctx = conn.execute(
                    "SELECT COUNT(*) c FROM boxes WHERE shelf='rack' "
                    "AND context IS NULL").fetchone()["c"]
                last_arrival = conn.execute(
                    "SELECT MAX(at) m FROM moves WHERE to_shelf='rack'").fetchone()["m"]
                try:
                    st = json.loads(get_flag(conn, "recontext_state") or "{}")
                except ValueError:
                    st = {}
            need = (null_ctx > 0 or not st.get("ok")
                    or (last_arrival or "").replace(" ", "T") > st.get("at", ""))
            if n_rack >= RECONTEXT_MIN_BOXES and need and not _recontext_running.locked():
                with db() as conn:
                    log_event(conn, "recontext_nightly", rack=n_rack, null_ctx=null_ctx)
                threading.Thread(target=_recontextualize_rack, daemon=True).start()
        if hm == TRUCK_AT and ("truck", today) not in fired:
            fired.add(("truck", today))
            with db() as conn:
                rows = conn.execute("SELECT id FROM boxes WHERE shelf='done'").fetchall()
                for r in rows:
                    conn.execute("UPDATE boxes SET shelf='archived' WHERE id=?", (r["id"],))
                    conn.execute("INSERT INTO moves(box_id, from_shelf, to_shelf) "
                                 "VALUES(?, 'done', 'archived')", (r["id"],))
                n_inbox = conn.execute("SELECT COUNT(*) c FROM boxes WHERE shelf='inbox'").fetchone()["c"]
                if n_inbox and get_flag(conn, "last_ritual") != today:
                    set_flag(conn, "blocked", today)
                log_event(conn, "sched_truck", archived=len(rows), inbox_left=n_inbox,
                          stopper=bool(n_inbox and get_flag(conn, "last_ritual") != today))
            frozen_now = []
            with db() as conn:
                for p in conn.execute("SELECT * FROM pallets WHERE frozen=0").fetchall():
                    idle = (date.today()
                            - date.fromisoformat(_pallet_last_move(conn, p))).days
                    if idle >= STALE_PALLET_DAYS:
                        conn.execute("UPDATE pallets SET frozen=1 WHERE id=?", (p["id"],))
                        log_event(conn, "sched_autofreeze", pid=p["id"], idle=idle)
                        frozen_now.append(p["title"])
            if rows:
                notify("🚚 Фура уехала", f"Увезла {len(rows)} посылок.")
            if frozen_now:
                notify("🧊 Заморозка", "Месяц без движения: " + ", ".join(frozen_now))
            if n_inbox and get_flag_standalone("last_ritual") != today:
                notify("⛔ Склад встал", f"Инбокс не разобран ({n_inbox}). Работа стоит до разбора.", urgent=True)
        if now.hour == 0 and now.minute == 0:
            fired = {k for k in fired if k[1] == today}
        time.sleep(30)


def get_flag_standalone(key):
    with db() as conn:
        return get_flag(conn, key)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=scheduler, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
