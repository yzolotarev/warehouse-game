#!/usr/bin/env python3
"""Склад: сервер-демон (часть 1 - фундамент).

Владеет БД коробок. Единственный источник истины.
API: POST /inbox, GET /boxes, POST /move, GET /health
"""
import difflib
import json
import os
import random
import re
import shutil
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
from fastapi.responses import FileResponse, PlainTextResponse
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
# полка «Мысли» дублируется заметками сюда: мысль с разбора сразу становится заметкой
OBSIDIAN_MIND = Path(os.environ.get(
    "WAREHOUSE_OBSIDIAN_MIND", Path.home() / "Obsidian/9ListsHybrid/2nd brain"))

SHELVES = {"inbox", "focus", "rack", "pallet_step", "waiting", "done", "trash", "mind", "archived"}
TRIAGE_POINTS = 1     # за разобранную коробку
RITUAL_POINTS = 10    # за инбокс, разобранный до нуля
DONE_POINTS = 3       # за сделанную задачу
SERIES_POINTS = 2     # за каждый следующий шаг той же паллеты за день (батчинг)
REVIEW_POINTS = 25    # за общую пересборку
REVIEW_DAYS = 3        # каждые N дней
RACK_REVIEW_POINTS = 8  # за полную ревизию стеллажа (не чаще раза в день)
REST_POINTS = 2       # за преднамеренный отдых (кофейня, не чаще раза в 30 мин)
STALE_FOCUS_DAYS = 3    # фокус без движения → пыль
STALE_PALLET_DAYS = 30  # паллета без движения → заморозка
DAY_START_H = int(os.environ.get("WAREHOUSE_DAY_START", "3"))
SQL_TODAY = f"date('now','localtime','-{DAY_START_H} hours')"
TIMER_MAX_S = 4 * 3600
TIMER_AFK_STOP_S = 15 * 60


def game_today():
    """Игровой «сегодня»: сдвиг границы на DAY_START_H часов ночи."""
    return (datetime.now() - timedelta(hours=DAY_START_H)).date()


# ─── 🎬 Кредит перекусов (earned screen time) ─────────────────────────────────
# Петля ПДВ без единого решения человека: карточка фокуса показывает награду
# ДО действия, ✅ начисляет минуты, ActivityWatch сам списывает за просмотром.
# Канон ресёрча (NLM «Earned screen time»): при нуле - трение/видимость, НЕ блок
# (реактивность); изредка кубик-бонус (вариативность против выгорания новизны);
# творческие паллеты вне экономики (overjustification: не награждать интересное).
YT_PER_DONE = 15          # минут перекуса за сделанный шаг
YT_BONUS = 25             # кубик: изредка вместо обычных
YT_BONUS_CHANCE = 6       # 1 из N начислений - бонусное
YT_CAP = 90               # потолок накоплений (копить бесконечно = экономика мертва)
YT_NUDGE_COOLDOWN = 300   # сек между нуджами «кредит кончился»
AW_URL = os.environ.get("WAREHOUSE_AW", "http://localhost:5600/api/0")
YT_TITLE_RE = re.compile(r"youtube|twitch", re.IGNORECASE)

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
CREATE TABLE IF NOT EXISTS yt_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  delta REAL NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timer_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  box_id INTEGER NOT NULL REFERENCES boxes(id),
  started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  stopped_at TEXT,
  elapsed_seconds INTEGER,
  note TEXT
);
CREATE TABLE IF NOT EXISTS chest(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  cost INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  bought_at TEXT
);
"""


def log_event(conn, kind, **payload):
    """Тотальный журнал: каждое действие системы и человека - строка датасета."""
    conn.execute("INSERT INTO events(kind, payload) VALUES(?,?)",
                 (kind, json.dumps(payload, ensure_ascii=False)))


def award(conn, amount, reason):
    conn.execute("INSERT INTO points(amount, reason) VALUES(?,?)", (amount, reason))


def yt_balance(conn):
    return conn.execute(
        "SELECT COALESCE(SUM(delta),0) s FROM yt_ledger").fetchone()["s"]


def yt_earn(conn, box_id, pallet_id):
    """Начислить перекус-кредит за сделанный шаг. Творческие паллеты - мимо кассы.

    Идемпотентна: повторный done той же коробки минут не даёт.

    Возвращает (минуты, бонус?) - (0, False) если не положено или потолок."""
    already = conn.execute(
        "SELECT 1 FROM yt_ledger WHERE reason LIKE ?", (f"шаг #{box_id}%",)).fetchone()
    if already:
        return 0, False
    if pallet_id:
        p = conn.execute("SELECT creative FROM pallets WHERE id=?",
                         (pallet_id,)).fetchone()
        if p and p["creative"]:
            return 0, False
    bonus = random.randrange(YT_BONUS_CHANCE) == 0
    mins = YT_BONUS if bonus else YT_PER_DONE
    add = min(mins, max(0, YT_CAP - yt_balance(conn)))
    if add <= 0:
        return 0, False
    conn.execute("INSERT INTO yt_ledger(delta, reason) VALUES(?,?)",
                 (add, f"шаг #{box_id}" + (" 🎲 бонус" if bonus else "")))
    log_event(conn, "yt_earn", id=box_id, minutes=add, bonus=bonus)
    return add, bonus


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
        if "elapsed_seconds_total" not in cols:
            # агрегат таймер-сессий по задаче — сколько секунд на неё потрачено
            conn.execute("ALTER TABLE boxes ADD COLUMN elapsed_seconds_total INTEGER NOT NULL DEFAULT 0")
        if "starred_at" not in cols:
            # когда поставлена звезда — показывать только с нового игрового дня
            conn.execute("ALTER TABLE boxes ADD COLUMN starred_at TEXT")
        if "ai_note" not in cols:
            # отчёт ИИ-работника: агент сделал 🤖-задачу и написал результат;
            # коробку НЕ закрывает - «Сделал» жмёт человек, глянув отчёт (19.07)
            conn.execute("ALTER TABLE boxes ADD COLUMN ai_note TEXT")
        if "ai_ok" not in cols:
            # разметка «Робот/Не робот» с конвейера /robot: NULL = ещё не решено,
            # 1 = задача для ИИ, 0 = человеческая. Ставится только человеком (19.07)
            conn.execute("ALTER TABLE boxes ADD COLUMN ai_ok INTEGER")
        if "tech_checked" not in cols:
            # разборник «по проектам» (/route): 1 = человек решил «не проектная»,
            # больше в конвейер распределения не едет. Отправленные в проект
            # коробки уезжают в done - им флаг не нужен (19.07)
            conn.execute("ALTER TABLE boxes ADD COLUMN tech_checked INTEGER")
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(pallets)")}
        if "note_path" not in pcols:
            conn.execute("ALTER TABLE pallets ADD COLUMN note_path TEXT")
        if "creative" not in pcols:
            # творческий проект = вне экономики перекусов (overjustification effect:
            # внешняя награда за интересное убивает внутренний интерес)
            conn.execute("ALTER TABLE pallets ADD COLUMN creative INTEGER NOT NULL DEFAULT 0")
        if "purpose" not in pcols:
            # Natural Planning Model (Аллен): недостающие фазы "зачем" и "по каким
            # правилам" - необязательные, чтобы не перегружать оформление (13.07)
            conn.execute("ALTER TABLE pallets ADD COLUMN purpose TEXT")
        if "principles" not in pcols:
            conn.execute("ALTER TABLE pallets ADD COLUMN principles TEXT")
        if "breadcrumb" not in pcols:
            # «на чём встал / почему» — одна опциональная строка для дешёвого возврата
            # на плохую голову; НЕ журнал, не путать с note_append (Obsidian) (14.07)
            conn.execute("ALTER TABLE pallets ADD COLUMN breadcrumb TEXT")


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


@app.get("/storozh.js")
def storozh_js():
    return FileResponse(Path(__file__).parent / "storozh.js", media_type="text/javascript")


@app.get("/juice.js")
def juice_js():
    return FileResponse(Path(__file__).parent / "juice.js", media_type="text/javascript")


@app.get("/i18n.js")
def i18n_js():
    return FileResponse(Path(__file__).parent / "i18n.js", media_type="text/javascript")


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
                     (max(0, min(5, t.p)), t.id))
    return {"ok": True}


# ─── Роутинг в технические проекты (из инбокса юзера в dev-warehouse проекта) ──
TECH_REGISTRY = Path("~/.config/dev-warehouse/projects.json").expanduser()
# systemd-сервис живёт без пользовательского PATH — бинарь ищем сами
DEV_WAREHOUSE_BIN = shutil.which("dev-warehouse") or str(Path("~/bin/dev-warehouse").expanduser())


def _load_tech_projects():
    """-> [(name, path, [aliases]), ...]"""
    try:
        raw = json.loads(TECH_REGISTRY.read_text())
        return [(p["name"], p["path"], [a.lower() for a in p.get("aliases", [])])
                for p in raw.get("projects", [])]
    except Exception:
        return []


@app.get("/tech_projects")
def tech_projects():
    """Полный список техпроектов для ручного выбора."""
    return {"projects": [{"name": name, "path": path}
                          for name, path, _ in _load_tech_projects()]}


class TechRouteSuggest(BaseModel):
    id: int
    text: str


@app.post("/tech_route_suggest")
def tech_route_suggest(r: TechRouteSuggest):
    """AI-матчинг текста коробки по реестру техпроектов. Возвращает топа кандидатов."""
    projects = _load_tech_projects()
    if not projects:
        return {"candidates": []}
    text_lower = r.text.lower()
    # 1. точный матч по алиасам
    scored = []
    for name, path, aliases in projects:
        score = 0
        match_alias = ""
        for a in aliases:
            if a in text_lower:
                alen = len(a.split())
                score = max(score, 0.5 + 0.1 * alen)
                match_alias = a
        # 2. fuzzy поверх точного: похожие слова
        words = set(text_lower.split())
        for a in aliases:
            aw = set(a.split())
            common = words & aw
            if common:
                score = max(score, 0.3 + 0.15 * len(common))
        if score:
            scored.append((score, name, path, match_alias))
    scored.sort(reverse=True)
    # если есть лидер с отрывом >= 0.3 — единственный кандидат
    candidates = []
    for i, (s, name, path, ma) in enumerate(scored[:3]):
        conf = "high" if i == 0 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.3) else "medium"
        candidates.append({"name": name, "path": path,
                           "matched_alias": ma or None, "confidence": conf})
    if not candidates:
        # ничего не нашли — предложим все проекты для ручного выбора
        for name, path, _ in projects:
            candidates.append({"name": name, "path": path,
                               "matched_alias": None, "confidence": "none"})
    return {"candidates": candidates[:5]}


class TechRouteConfirm(BaseModel):
    id: int
    text: str
    project_name: str
    project_path: str


@app.post("/tech_route_confirm")
def tech_route_confirm(c: TechRouteConfirm):
    """Подтвердить роутинг: задача уезжает в инбокс техпроекта, у себя - в done.

    Канал: dev-warehouse, если у проекта живая .dev/warehouse.db; иначе fallback -
    строка в INBOX.md в корне проекта (агентские сессии его видят)."""
    ppath = Path(c.project_path).expanduser()
    if not ppath.is_dir():
        raise HTTPException(400, f"папки проекта {c.project_name} нет: {ppath}")
    db_path = ppath / ".dev" / "warehouse.db"
    if db_path.exists():
        channel = "dev-warehouse"
        r = subprocess.run(
            [DEV_WAREHOUSE_BIN, "--db", str(db_path), "add", c.text,
             "--source", "warehouse-inbox"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise HTTPException(500, f"dev-warehouse add failed: {r.stderr[:300]}")
    else:
        channel = "INBOX.md"
        inbox = ppath / "INBOX.md"
        line = f"- [ ] {date.today().isoformat()} (со склада): {c.text.strip()}\n"
        header = "" if inbox.exists() else "# INBOX - задачи со склада\n\n"
        with inbox.open("a", encoding="utf-8") as f:
            f.write(header + line)
    # у себя — в done (коробка может ехать не только с инбокса: разборник
    # «по проектам» шерстит все живые полки)
    with db() as conn:
        row = conn.execute("SELECT shelf FROM boxes WHERE id=?", (c.id,)).fetchone()
        from_shelf = row["shelf"] if row else "inbox"
        conn.execute("UPDATE boxes SET shelf='done' WHERE id=?", (c.id,))
        conn.execute(
            "INSERT INTO moves(box_id, from_shelf, to_shelf) VALUES(?, ?, 'done')",
            (c.id, from_shelf))
        log_event(conn, "tech_route", id=c.id, project=c.project_name,
                  channel=channel, text=c.text[:200])
    return {"ok": True, "project": c.project_name, "channel": channel}


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
            if shelf == "rack":
                entered = conn.execute(
                    "SELECT MAX(at) m FROM moves WHERE box_id=? AND to_shelf='rack'",
                    (r["id"],)).fetchone()["m"]
                d["entered_at"] = entered or d["born_at"]
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


def _export_mind(box_id: int, text: str) -> str | None:
    """Мысль с разбора → отдельная md-заметка в Obsidian. Ошибка ФС не должна
    ронять сам move: мысль в любом случае остаётся на полке mind."""
    try:
        OBSIDIAN_MIND.mkdir(parents=True, exist_ok=True)
        first = text.strip().splitlines()[0] if text.strip() else ""
        # только заведомо безопасные символы: буквы/цифры/пробел и мягкая пунктуация
        # (запретные для ФС и Obsidian, управляющие, эмодзи - всё выпадает)
        safe = "".join(ch if ch.isalnum() or ch in " -_,.!’'()+№" else " " for ch in first)
        safe = " ".join(safe.split()).strip(" .")[:60].strip(" .")
        p = OBSIDIAN_MIND / f"{safe or 'мысль-' + str(box_id)}.md"
        n = 1
        while p.exists():
            n += 1
            p = OBSIDIAN_MIND / f"{safe or 'мысль-' + str(box_id)} ({n}).md"
        p.write_text(f"{text.strip()}\n\n> 💭 со склада, коробка #{box_id}, "
                     f"{game_today().isoformat()}\n")
        return str(p)
    except OSError:
        return None


@app.post("/move")
def move_box(m: Move):
    if m.to not in SHELVES:
        raise HTTPException(400, f"unknown shelf: {m.to}")
    with db() as conn:
        row = conn.execute("SELECT shelf, pallet_id, street, raw_text FROM boxes WHERE id=?", (m.id,)).fetchone()
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
            conn.execute("UPDATE boxes SET starred=0, starred_at=NULL WHERE id=?", (m.id,))
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
                if get_flag(conn, "last_ritual") != game_today().isoformat():
                    pts += RITUAL_POINTS
                    award(conn, RITUAL_POINTS, "инбокс разобран до нуля")
                    set_flag(conn, "last_ritual", game_today().isoformat())
        elif row["shelf"] != "inbox" and m.to == "inbox":
            # откат решения: очко триажа возвращается в кассу
            pts -= TRIAGE_POINTS
            award(conn, -TRIAGE_POINTS, f"откат на приёмку #{m.id}")
        if row["shelf"] in ("done", "archived") and m.to not in ("done", "archived"):
            # возврат из сделанного: списать очки и перекус-кредит (антидюп)
            pts -= DONE_POINTS
            award(conn, -DONE_POINTS, f"вернул из сделанного #{m.id}")
            earned = conn.execute(
                "SELECT COALESCE(SUM(delta),0) s FROM yt_ledger "
                "WHERE reason LIKE ? AND delta>0", (f"шаг #{m.id}%",)).fetchone()["s"]
            refunded = conn.execute(
                "SELECT COALESCE(SUM(ABS(delta)),0) s FROM yt_ledger "
                "WHERE reason=? AND delta<0",
                (f"возврат шага #{m.id}",)).fetchone()["s"]
            to_claw = max(0, earned - refunded)
            if to_claw > 0:
                conn.execute("INSERT INTO yt_ledger(delta, reason) VALUES(?,?)",
                             (-to_claw, f"возврат шага #{m.id}"))
        yt_min, yt_bonus = 0, False
        if m.to == "done":
            pts += DONE_POINTS
            award(conn, DONE_POINTS, f"сделано #{m.id}")
            yt_min, yt_bonus = yt_earn(conn, m.id, row["pallet_id"])
            if row["pallet_id"]:
                prev = conn.execute(
                    f"SELECT COUNT(*) c FROM moves mv JOIN boxes b ON b.id=mv.box_id "
                    f"WHERE mv.to_shelf='done' AND date(mv.at)={SQL_TODAY} "
                    "AND b.pallet_id=? AND mv.box_id!=?",
                    (row["pallet_id"], m.id)).fetchone()["c"]
                if prev:
                    pts += SERIES_POINTS
                    award(conn, SERIES_POINTS, f"серия паллеты #{row['pallet_id']}")
        if m.to == "mind" and row["shelf"] != "mind":
            note = _export_mind(m.id, row["raw_text"])
            if note:
                log_event(conn, "mind_export", id=m.id, note=note)
        log_event(conn, "move", id=m.id, frm=row["shelf"], to=m.to,
                  context=m.context, points=pts)
        # шарнир done→next: закрыл шаг проекта и очередь+фокус опустели → зовём
        # задать следующий, пока голова ещё в контексте (ловим ровно тот зазор,
        # где многоходовые задачи умирают)
        pallet_dry = None
        if m.to == "done" and row["pallet_id"]:
            charged = conn.execute(
                "SELECT COUNT(*) c FROM boxes WHERE pallet_id=? "
                "AND shelf IN ('pallet_step','focus')", (row["pallet_id"],)).fetchone()["c"]
            pr = conn.execute("SELECT title, frozen FROM pallets WHERE id=?",
                              (row["pallet_id"],)).fetchone()
            if not charged and pr and not pr["frozen"]:
                pallet_dry = {"id": row["pallet_id"], "title": pr["title"]}
    return {"ok": True, "points": pts, "yt": yt_min, "yt_bonus": yt_bonus,
            "pallet_dry": pallet_dry}


def _add_days(n):
    from datetime import timedelta
    return (date.today() + timedelta(days=n)).isoformat()


def _close_timer(conn, sess, elapsed, note):
    """Закрыть таймер-сессию: stopped_at, elapsed, обновить агрегат boxes.
    Возвращает total_seconds по этой задаче."""
    now = datetime.now()
    conn.execute(
        "UPDATE timer_sessions SET stopped_at=?, elapsed_seconds=?, note=? WHERE id=?",
        (now.isoformat(timespec="seconds"), elapsed, note, sess["id"]))
    total = conn.execute(
        "SELECT COALESCE(SUM(elapsed_seconds),0) s FROM timer_sessions "
        "WHERE box_id=? AND elapsed_seconds IS NOT NULL",
        (sess["box_id"],)).fetchone()["s"]
    conn.execute("UPDATE boxes SET elapsed_seconds_total=? WHERE id=?",
                 (total, sess["box_id"]))
    return total


# ─── ⏱ Timer API ──────────────────────────────────────────────────────────────


class TimerStart(BaseModel):
    box_id: int


class TimerStop(BaseModel):
    box_id: int
    note: str | None = None


@app.post("/timer/start")
def timer_start(t: TimerStart):
    """Запустить таймер на задаче. Если уже тикает — 409."""
    with db() as conn:
        row = conn.execute("SELECT id FROM boxes WHERE id=?", (t.box_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such box")
        running = conn.execute(
            "SELECT id FROM timer_sessions WHERE box_id=? AND stopped_at IS NULL",
            (t.box_id,)).fetchone()
        if running:
            raise HTTPException(409, "timer already running for this box")
        # проверить, нет ли другого активного таймера вообще (один таймер на весь склад)
        any_running = conn.execute(
            "SELECT id FROM timer_sessions WHERE stopped_at IS NULL LIMIT 1").fetchone()
        if any_running:
            raise HTTPException(409, "another timer is already running, stop it first")
        cur = conn.execute(
            "INSERT INTO timer_sessions(box_id) VALUES(?)", (t.box_id,))
        sess_id = cur.lastrowid
        row = conn.execute("SELECT id, started_at FROM timer_sessions WHERE id=?",
                           (sess_id,)).fetchone()
        log_event(conn, "timer_start", box_id=t.box_id, session_id=sess_id)
    return {"ok": True, "session_id": row["id"], "started_at": row["started_at"]}


@app.post("/timer/stop")
def timer_stop(t: TimerStop):
    """Остановить таймер на задаче. Вычисляет elapsed."""
    with db() as conn:
        sess = conn.execute(
            "SELECT id, started_at FROM timer_sessions "
            "WHERE box_id=? AND stopped_at IS NULL ORDER BY id DESC LIMIT 1",
            (t.box_id,)).fetchone()
        if not sess:
            raise HTTPException(404, "no running timer for this box")
        now = datetime.now()
        started = datetime.fromisoformat(sess["started_at"])
        elapsed = int((now - started).total_seconds())
        total = _close_timer(conn, sess, elapsed, t.note)
        log_event(conn, "timer_stop", box_id=t.box_id, session_id=sess["id"],
                  elapsed_seconds=elapsed)
    return {"ok": True, "session_id": sess["id"], "elapsed_seconds": elapsed,
            "total_seconds": total}


@app.get("/timer/history")
def timer_history(box_id: int):
    """История таймер-сессий по задаче."""
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM timer_sessions WHERE box_id=? ORDER BY started_at DESC",
            (box_id,))]
    return rows


@app.get("/timer/active")
def timer_active():
    """Какая сессия сейчас тикает (для восстановления UI после reload)."""
    with db() as conn:
        sess = conn.execute(
            "SELECT ts.*, b.raw_text FROM timer_sessions ts "
            "JOIN boxes b ON b.id=ts.box_id "
            "WHERE ts.stopped_at IS NULL LIMIT 1").fetchone()
    if not sess:
        return {"active": False}
    return {"active": True, "session": dict(sess)}


@app.get("/timer/stats")
def timer_stats(period: str = "day"):
    """Статистика таймера за период.

    period: day | week | month
    Возвращает total_seconds по проектам (паллетам), по контекстам,
    общее количество задач с таймером, среднее время на задачу.
    """
    import calendar
    today = game_today()
    if period == "week":
        start = today - timedelta(days=today.weekday())  # пн
    elif period == "month":
        start = today.replace(day=1)
    else:
        start = today
    start_str = start.isoformat()
    with db() as conn:
        # все сессии за период
        rows = conn.execute(
            "SELECT ts.*, b.pallet_id, b.context FROM timer_sessions ts "
            "JOIN boxes b ON b.id=ts.box_id "
            "WHERE ts.stopped_at IS NOT NULL AND date(ts.started_at)>=?",
            (start_str,)).fetchall()
        total = sum(r["elapsed_seconds"] or 0 for r in rows)
        task_ids = {r["box_id"] for r in rows}
        task_count = len(task_ids)
        avg = total // task_count if task_count else 0
        # группировка по проектам
        projects = {}
        for r in rows:
            pid = r["pallet_id"]
            key = str(pid) if pid else "null"
            if key not in projects:
                if pid:
                    title = conn.execute("SELECT title FROM pallets WHERE id=?",
                                         (pid,)).fetchone()
                    projects[key] = {"pallet_id": pid,
                                     "title": title["title"] if title else "Без проекта",
                                     "seconds": 0, "tasks": set()}
                else:
                    projects[key] = {"pallet_id": None, "title": "Без проекта",
                                     "seconds": 0, "tasks": set()}
            projects[key]["seconds"] += r["elapsed_seconds"] or 0
            projects[key]["tasks"].add(r["box_id"])
        for p in projects.values():
            p["task_count"] = len(p["tasks"])
            del p["tasks"]
        # группировка по контекстам
        contexts = {}
        for r in rows:
            ctx = r["context"] or "Без контекста"
            if ctx not in contexts:
                contexts[ctx] = {"seconds": 0, "tasks": set()}
            contexts[ctx]["seconds"] += r["elapsed_seconds"] or 0
            contexts[ctx]["tasks"].add(r["box_id"])
        for c in contexts.values():
            c["task_count"] = len(c["tasks"])
            del c["tasks"]
    return {
        "period": period,
        "start_date": start_str,
        "total_seconds": total,
        "task_count": task_count,
        "avg_seconds_per_task": avg,
        "by_project": dict(projects),
        "by_context": dict(contexts),
    }


@app.get("/timer/computer_time")
def timer_computer_time(period: str = "day"):
    """Общее время за компьютером из ActivityWatch за период."""
    from datetime import datetime, timedelta
    today = game_today()
    if period == "week":
        start = today - timedelta(days=today.weekday())
    elif period == "month":
        start = today.replace(day=1)
    else:
        start = today
    start_str = start.isoformat()
    end_str = today.isoformat()
    try:
        win_b, afk_b = _aw_find_buckets()
        if not (win_b and afk_b):
            return {"error": "ActivityWatch buckets not found", "total_seconds": None}
        # суммарное время AFK за период
        afk_data = _aw_get(f"buckets/{afk_b}/events?start={start_str}T00:00:00&end={end_str}T23:59:59&limit=5000")
        afk_sec = sum(
            e["duration"] for e in afk_data
            if isinstance(e.get("data"), dict) and e["data"].get("status") in ("afk", "unknown"))
        # общее время окна
        win_data = _aw_get(f"buckets/{win_b}/events?start={start_str}T00:00:00&end={end_str}T23:59:59&limit=5000")
        win_sec = sum(e["duration"] for e in win_data)
        awake_sec = max(0, int(win_sec - afk_sec))
        return {"total_seconds": awake_sec, "period": period, "start_date": start_str}
    except Exception as e:
        return {"error": str(e), "total_seconds": None}


@app.get("/timer_dashboard")
def timer_dashboard():
    return FileResponse(Path(__file__).parent / "timer_dashboard.html")


# ─── 🔔 Glow (ожидание) ────────────────────────────────────────────────────────


@app.get("/glow")
def glow():
    """Загоревшиеся товары ожидания: пора чекнуть."""
    today = game_today().isoformat()
    out = []
    with db() as conn:
        for r in conn.execute("SELECT * FROM boxes WHERE shelf='waiting'"):
            t = json.loads(r["glow_timer"] or "{}")
            if t.get("next") and t["next"] <= today:
                out.append(dict(r))
    return out


class WaitCheck(BaseModel):
    id: int
    action: str  # done | wait | focus | delete


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
    target = {"done": "done", "delete": "trash", "focus": "focus"}.get(w.action)
    if not target:
        raise HTTPException(400, "action: done|wait|focus|delete")
    return move_box(Move(id=w.id, to=target))


class PalletNew(BaseModel):
    title: str
    done_criteria: str = ""
    from_box: int | None = None   # мутный товар, из которого родилась
    first_step: str = ""          # одношаговый шаг → сразу в фокус
    purpose: str = ""             # Natural Planning Model: "зачем" (необязательно)
    principles: str = ""          # "по каким правилам" (необязательно)


def _check_blocked(conn):
    if get_flag(conn, "blocked"):
        raise HTTPException(423, "склад стоит: сначала разбери инбокс")


@app.post("/pallet")
def pallet_new(p: PalletNew):
    with db() as conn:
        _check_blocked(conn)
        cur = conn.execute(
            "INSERT INTO pallets(title, done_criteria, purpose, principles) VALUES(?,?,?,?)",
            (p.title.strip(), p.done_criteria.strip(), p.purpose.strip(), p.principles.strip()))
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
        # шаг рождается в ОЧЕРЕДИ проекта (полка pallet_step с pallet_id), не в
        # фокусе: доение наполняет очередь, фокус тянет из неё по одному (stepFocus)
        cur = conn.execute(
            "INSERT INTO boxes(raw_text, source, shelf, pallet_id) "
            "VALUES(?, 'pallet', 'pallet_step', ?)", (s.text.strip(), s.pallet_id))
        conn.execute("INSERT INTO moves(box_id, from_shelf, to_shelf) "
                     "VALUES(?, NULL, 'pallet_step')", (cur.lastrowid,))
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
            # авто-«что было»: последний сделанный шаг — для дешёвого возврата в проект
            # на плохую голову (журнал руками не веду, всё уже лежит в moves)
            ld = conn.execute(
                "SELECT b.raw_text t, MAX(mv.at) at FROM moves mv JOIN boxes b ON b.id=mv.box_id "
                "WHERE b.pallet_id=? AND mv.to_shelf='done'", (p["id"],)).fetchone()
            p["last_done"] = ({"text": ld["t"],
                               "days": (today - date.fromisoformat(ld["at"][:10])).days}
                              if ld and ld["at"] else None)
            p["done_today"] = conn.execute(
                f"SELECT COUNT(*) c FROM moves mv JOIN boxes b ON b.id=mv.box_id "
                f"WHERE mv.to_shelf='done' AND date(mv.at)={SQL_TODAY} "
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


class PalletCreative(BaseModel):
    id: int
    creative: bool


@app.post("/pallet/creative")
def pallet_creative(c: PalletCreative):
    """🎨 творческий проект: шаги вне экономики перекусов (интерес - сам себе награда)."""
    with db() as conn:
        conn.execute("UPDATE pallets SET creative=? WHERE id=?", (int(c.creative), c.id))
        log_event(conn, "pallet_creative", pid=c.id, creative=c.creative)
    return {"ok": True}


class PalletPurpose(BaseModel):
    id: int
    purpose: str = ""
    principles: str = ""


@app.post("/pallet/purpose")
def pallet_purpose(pp: PalletPurpose):
    """Правка "зачем"/"по каким правилам" после оформления (необязательные поля)."""
    with db() as conn:
        conn.execute("UPDATE pallets SET purpose=?, principles=? WHERE id=?",
                     (pp.purpose.strip(), pp.principles.strip(), pp.id))
        log_event(conn, "pallet_purpose", pid=pp.id)
    return {"ok": True}


class PalletBreadcrumb(BaseModel):
    id: int
    text: str = ""


@app.post("/pallet/breadcrumb")
def pallet_breadcrumb(b: PalletBreadcrumb):
    """🔖 «на чём встал» — тонкая крошка контекста в карточке (пусто = стереть).
    Не Obsidian-заметка: для взгляда на плохую голову, а не для архива."""
    with db() as conn:
        conn.execute("UPDATE pallets SET breadcrumb=? WHERE id=?", (b.text.strip(), b.id))
        log_event(conn, "pallet_breadcrumb", pid=b.id)
    return {"ok": True}


def _dry_pallets(conn):
    """Проекты без заряженного шага (пусто и в очереди, и в фокусе) — их пора доить.
    Замороженные молчат: заморозка = «пока не трогаем» (ею же глушат завершённый)."""
    rows = conn.execute(
        "SELECT p.id, p.title FROM pallets p WHERE p.frozen=0 "
        "AND NOT EXISTS (SELECT 1 FROM boxes b WHERE b.pallet_id=p.id "
        "AND b.shelf IN ('pallet_step','focus')) ORDER BY p.created_at DESC").fetchall()
    return [{"id": r["id"], "title": r["title"]} for r in rows]


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
    """⭐ frontloading (канон Санга): вечером отметить главное на завтра.

    Утром решений ноль - звезда уже горит. Максимум 4 (узкая окрестность внимания).
    Звезда видна только на следующий игровой день (после 3:00).
    """
    with db() as conn:
        row = conn.execute("SELECT shelf FROM boxes WHERE id=?", (s.id,)).fetchone()
        if not row or row["shelf"] != "focus":
            raise HTTPException(400, "звезда ставится только на товар в фокусе")
        if s.on:
            tomorrow = (game_today() + timedelta(days=1)).isoformat()
            n = conn.execute(
                "SELECT COUNT(*) c FROM boxes WHERE shelf='focus' AND starred=1 AND id!=?"
                " AND starred_at=?",
                (s.id, tomorrow)).fetchone()["c"]
            if n >= 4:
                raise HTTPException(409, "уже 4 ⭐ - больше окрестность внимания не вместит")
            conn.execute("UPDATE boxes SET starred=1, starred_at=? WHERE id=?", (tomorrow, s.id))
        else:
            conn.execute("UPDATE boxes SET starred=0, starred_at=NULL WHERE id=?", (s.id,))
        log_event(conn, "star", id=s.id, on=s.on)
    return {"ok": True, "starred": s.on}


class AiNote(BaseModel):
    id: int
    text: str


@app.post("/box/ai_note")
def box_ai_note(n: AiNote):
    """Отчёт ИИ-работника в коробку: агент сделал 🤖-задачу и пишет результат.

    Коробку не двигает и очков не даёт - «Сделал» жмёт человек, увидев отчёт
    на карточке фокуса. Пустой текст стирает отчёт (агент передумал)."""
    text = n.text.strip()
    with db() as conn:
        row = conn.execute("SELECT shelf FROM boxes WHERE id=?", (n.id,)).fetchone()
        if not row:
            raise HTTPException(404, "нет такой коробки")
        conn.execute("UPDATE boxes SET ai_note=? WHERE id=?", (text or None, n.id))
        log_event(conn, "ai_report", id=n.id, note=text)
    return {"ok": True}


# живые полки: здесь лежат ещё-актуальные дела - их и размечаем для робота
AI_SHELVES = ("focus", "rack", "pallet_step", "waiting")
AI_WINDOW = 5  # окрестность внимания ИИ: окно, а не цунами (канон = фокус ≤5)
# целые проекты в разметку не едут: ИИ делегируются шаги, не проекты.
# Отсекаем неоформленные проекты (pallet_step без паллеты) и коробки-обложки
AI_NOT_PROJECT = ("NOT (shelf='pallet_step' AND pallet_id IS NULL) "
                  "AND raw_text NOT LIKE '📁 Проект%'")


@app.get("/robot_page")
def robot_page():
    return FileResponse(Path(__file__).parent / "robot.html")


@app.get("/route_page")
def route_page():
    return FileResponse(Path(__file__).parent / "route.html")


@app.get("/route_triage")
def route_triage():
    """Разборник «по проектам»: живые коробки, по которым не решено, чьи они.
    Кандидатов подсказывает тот же матчер, что в разборе инбокса."""
    ph = ",".join("?" * len(AI_SHELVES))
    projects = _load_tech_projects()
    with db() as conn:
        rows = conn.execute(
            f"SELECT id, raw_text, shelf, context, pallet_id, born_at FROM boxes "
            f"WHERE shelf IN ({ph}) AND tech_checked IS NULL AND {AI_NOT_PROJECT} "
            "ORDER BY id", AI_SHELVES).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        text_lower = r["raw_text"].lower()
        words = set(text_lower.split())
        scored = []
        for name, path, aliases in projects:
            score = 0
            for a in aliases:
                if a in text_lower:
                    score = max(score, 0.5 + 0.1 * len(a.split()))
                common = words & set(a.split())
                if common:
                    score = max(score, 0.3 + 0.15 * len(common))
            if score:
                scored.append((score, name, path))
        scored.sort(reverse=True)
        d["candidates"] = [{"name": n, "path": p} for _, n, p in scored[:3]]
        items.append(d)
    # коробки с кандидатами первыми: решения по ним почти бесплатные
    items.sort(key=lambda d: (not d["candidates"], d["id"]))
    return {"items": items}


class RouteSkip(BaseModel):
    id: int


@app.post("/route_skip")
def route_skip(s: RouteSkip):
    """«Не проектная»: коробка остаётся где была и больше в разборник не едет."""
    with db() as conn:
        if not conn.execute("SELECT 1 FROM boxes WHERE id=?", (s.id,)).fetchone():
            raise HTTPException(404, "нет такой коробки")
        conn.execute("UPDATE boxes SET tech_checked=1 WHERE id=?", (s.id,))
        log_event(conn, "route_skip", id=s.id)
    return {"ok": True}


@app.get("/robot_triage")
def robot_triage():
    """Конвейер разметки: живые коробки, по которым ещё не решено Робот/Не робот."""
    ph = ",".join("?" * len(AI_SHELVES))
    with db() as conn:
        items = [dict(r) for r in conn.execute(
            f"SELECT id, raw_text, shelf, context, pallet_id, born_at FROM boxes "
            f"WHERE shelf IN ({ph}) AND ai_ok IS NULL AND {AI_NOT_PROJECT} "
            "ORDER BY id", AI_SHELVES)]
        marked = conn.execute(
            f"SELECT COUNT(*) c FROM boxes WHERE shelf IN ({ph}) "
            "AND ai_ok IS NOT NULL", AI_SHELVES).fetchone()["c"]
    return {"items": items, "marked": marked}


class RobotMark(BaseModel):
    id: int
    ok: bool


@app.post("/robot_mark")
def robot_mark(m: RobotMark):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM boxes WHERE id=?", (m.id,)).fetchone():
            raise HTTPException(404, "нет такой коробки")
        conn.execute("UPDATE boxes SET ai_ok=? WHERE id=?", (1 if m.ok else 0, m.id))
        log_event(conn, "robot_mark", id=m.id, ok=m.ok)
    return {"ok": True}


@app.get("/robot_queue")
def robot_queue():
    """Окно ИИ-работника: первые 5 робот-задач без отчёта. Скользит само:
    агент написал отчёт (ai_note) - коробка выпала из окна, втекла следующая.
    Порядок: сначала фокус человека, потом старые. Никакого состояния - чистая
    арифметика поверх разметки."""
    ph = ",".join("?" * len(AI_SHELVES))
    with db() as conn:
        items = [dict(r) for r in conn.execute(
            f"SELECT id, raw_text, shelf, context, pallet_id, born_at FROM boxes "
            f"WHERE shelf IN ({ph}) AND ai_ok=1 AND ai_note IS NULL "
            f"AND {AI_NOT_PROJECT} "
            "ORDER BY (shelf='focus') DESC, id LIMIT ?", (*AI_SHELVES, AI_WINDOW))]
        total = conn.execute(
            f"SELECT COUNT(*) c FROM boxes WHERE shelf IN ({ph}) AND ai_ok=1 "
            f"AND ai_note IS NULL AND {AI_NOT_PROJECT}", AI_SHELVES).fetchone()["c"]
        reported = conn.execute(
            f"SELECT COUNT(*) c FROM boxes WHERE shelf IN ({ph}) "
            "AND ai_note IS NOT NULL", AI_SHELVES).fetchone()["c"]
    return {"items": items, "total": total, "reported": reported,
            "window": AI_WINDOW}


FOCUS_TRIM_AT = 5  # канон: фокус ≤5; толще - очередь «что сейчас» зовёт проредить


@app.get("/focus_triage_page")
def focus_triage_page():
    return FileResponse(Path(__file__).parent / "focus_triage.html")


@app.get("/focus_triage")
def focus_triage():
    """Разбор фокуса: коробки с «домом» - откуда пришла (по журналу перемещений).
    «Вернуть где был» едет именно туда; самые залежавшиеся - первыми."""
    today = date.today()
    with db() as conn:
        rows = conn.execute("SELECT * FROM boxes WHERE shelf='focus' ORDER BY id").fetchall()
        items = []
        for r in rows:
            d = dict(r)
            last = conn.execute("SELECT MAX(at) m FROM moves WHERE box_id=?",
                                (r["id"],)).fetchone()["m"]
            d["idle_days"] = (today - date.fromisoformat(last[:10])).days if last else 0
            origin = conn.execute(
                "SELECT from_shelf FROM moves WHERE box_id=? AND to_shelf='focus'"
                " AND from_shelf IS NOT NULL ORDER BY id DESC LIMIT 1",
                (r["id"],)).fetchone()
            o = origin["from_shelf"] if origin else None
            # дом = откуда пришла, если туда осмысленно возвращаться; шаг проекта
            # без паллеты не бывает; инбокс/прочее → стеллаж
            if o == "pallet_step" and not r["pallet_id"]:
                o = "rack"
            if o not in ("pallet_step", "rack", "waiting"):
                o = "rack"
            d["origin"] = o
            if r["pallet_id"]:
                p = conn.execute("SELECT title FROM pallets WHERE id=?",
                                 (r["pallet_id"],)).fetchone()
                d["pallet_title"] = p["title"] if p else None
            else:
                d["pallet_title"] = None
            items.append(d)
    items.sort(key=lambda d: -d["idle_days"])
    return {"items": items, "trim_at": FOCUS_TRIM_AT}


@app.get("/focus")
def focus_list():
    """Фокус с ПДВ: награда видна ДО действия + возраст без движения. ⭐ первыми."""
    today = date.today()
    game_today_str = game_today().isoformat()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM boxes WHERE shelf='focus'"
            " AND (starred=0 OR starred_at IS NULL OR starred_at<=?)"
            " ORDER BY starred DESC, id", (game_today_str,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            last = conn.execute("SELECT MAX(at) m FROM moves WHERE box_id=?",
                                (r["id"],)).fetchone()["m"]
            d["idle_days"] = (today - date.fromisoformat(last[:10])).days if last else 0
            d["reward"] = DONE_POINTS
            d["series"] = 0
            d["pallet_title"] = None
            d["yt"] = YT_PER_DONE  # предвкушение: перекус виден ДО действия
            if r["pallet_id"]:
                p = conn.execute("SELECT title, creative FROM pallets WHERE id=?",
                                 (r["pallet_id"],)).fetchone()
                d["pallet_title"] = p["title"] if p else None
                if p and p["creative"]:
                    d["yt"] = 0  # творческий проект: интерес - сам себе награда
                done_today = conn.execute(
                    f"SELECT COUNT(*) c FROM moves mv JOIN boxes b ON b.id=mv.box_id "
                    f"WHERE mv.to_shelf='done' AND date(mv.at)={SQL_TODAY} "
                    "AND b.pallet_id=?", (r["pallet_id"],)).fetchone()["c"]
                if done_today:
                    d["series"] = SERIES_POINTS
            out.append(d)
        # сколько звёзд скрыто до завтра
        hidden_rows = conn.execute(
            "SELECT id FROM boxes WHERE shelf='focus' AND starred=1"
            " AND starred_at IS NOT NULL AND starred_at>?", (game_today_str,)).fetchall()
        hidden_stars = len(hidden_rows)
        hidden_star_ids = [r["id"] for r in hidden_rows]
        dry = _dry_pallets(conn)
    return {"items": out, "stale_days": STALE_FOCUS_DAYS, "dry_pallets": dry,
            "hidden_stars": hidden_stars, "hidden_star_ids": hidden_star_ids}


@app.get("/pallets_page")
def pallets_page():
    return FileResponse(Path(__file__).parent / "pallets.html")


@app.get("/mirror")
def mirror():
    """Зеркало дня: лента коробок, рождённых сегодня + очки за сегодня."""
    today = game_today().isoformat()
    with db() as conn:
        boxes = [dict(r) for r in conn.execute(
            "SELECT * FROM boxes WHERE date(born_at)=? ORDER BY born_at", (today,))]
        pts = [dict(r) for r in conn.execute(
            "SELECT at, amount, reason FROM points WHERE date(at)=? ORDER BY at", (today,))]
        total = sum(p["amount"] for p in pts)
    return {"date": today, "boxes": boxes, "points": pts, "points_total": total}


def _review_due(conn):
    last = get_flag(conn, "last_general_review")
    if last:
        return (game_today() - date.fromisoformat(last)).days >= REVIEW_DAYS
    return (game_today() - date(2025, 1, 1)).days % REVIEW_DAYS == 0


@app.get("/whatnow")
def whatnow():
    """Очередь «что сейчас»: все ждущие разборы по приоритету, одним списком.

    Узкая окрестность внимания: хаб и мир показывают верхний пункт - сделал один
    разбор, на его месте сам всплывает следующий, глазами бегать не надо."""
    today = game_today().isoformat()
    hour = datetime.now().hour
    ph = ",".join("?" * len(AI_SHELVES))
    with db() as conn:
        counts = dict(conn.execute(
            "SELECT shelf, COUNT(*) FROM boxes GROUP BY shelf").fetchall())
        blocked = bool(get_flag(conn, "blocked"))
        review = _review_due(conn)
        glow_n = sum(1 for r in conn.execute("SELECT glow_timer FROM boxes WHERE shelf='waiting'")
                     if (json.loads(r["glow_timer"] or "{}").get("next") or "~") <= today)
        robot_n = conn.execute(
            f"SELECT COUNT(*) c FROM boxes WHERE shelf IN ({ph}) AND ai_ok IS NULL "
            f"AND {AI_NOT_PROJECT}", AI_SHELVES).fetchone()["c"]
        route_n = conn.execute(
            f"SELECT COUNT(*) c FROM boxes WHERE shelf IN ({ph}) "
            f"AND tech_checked IS NULL AND {AI_NOT_PROJECT}", AI_SHELVES).fetchone()["c"]
        dry = _dry_pallets(conn)
        unformed_n = conn.execute(
            "SELECT COUNT(*) c FROM boxes WHERE shelf='pallet_step' "
            "AND pallet_id IS NULL").fetchone()["c"]
        stars = [r["raw_text"] for r in conn.execute(
            "SELECT raw_text FROM boxes WHERE shelf='focus' AND starred=1"
            " AND starred_at IS NOT NULL AND starred_at<=? ORDER BY id", (today,))]
    q = []
    if blocked:
        q.append({"act": "⛔ Разблокировать склад: разобрать входящие",
                  "why": "физика мира: пока входящие не разобраны, всё стоит",
                  "url": "/terminal"})
    if counts.get("inbox"):
        q.append({"act": f"📥 Разобрать входящие ({counts['inbox']})",
                  "why": "один вопрос на экран, вопросы сами разложат всё по полкам",
                  "url": "/terminal"})
    if glow_n:
        q.append({"act": f"✨ Проверить ожидание ({glow_n})",
                  "why": "коробки загорелись: пора чекнуть, дождался ли",
                  "url": "/terminal#glow"})
    if review:
        q.append({"act": "🧰 Общая пересборка · +25 ⭐",
                  "why": "столп №2: без неё система разваливается",
                  "url": "/review"})
    focus_n = counts.get("focus", 0)
    trim = {"act": f"✂ Проредить фокус ({focus_n} → {FOCUS_TRIM_AT})",
            "why": "фокус толще канона: «делаю сейчас» превратился в стеллаж",
            "url": "/focus_triage_page"}
    if focus_n > 2 * FOCUS_TRIM_AT:
        q.append(trim)  # совсем разжирел - зовём сразу после разборов-стопоров
    if robot_n:
        q.append({"act": f"🤖 Разметить для робота ({robot_n})",
                  "why": "две кнопки на коробку - и ИИ сможет брать твои задачи",
                  "url": "/robot_page"})
    if route_n:
        q.append({"act": f"📡 Разобрать по проектам ({route_n})",
                  "why": "проектные задачи - в инбоксы своих проектов, не в общую кучу",
                  "url": "/route_page"})
    if counts.get("mind"):
        q.append({"act": f"💭 Разобрать мысли ({counts['mind']})",
                  "why": "перенеси в Obsidian или выкини - полка не резиновая",
                  "url": "/mind_page"})
    if dry:
        q.append({"act": f"🥛 Подоить проекты ({len(dry)})",
                  "why": "сухие проекты: сними следующий шаг, чтобы фокус не пустел",
                  "url": "/pallets_page"})
    if unformed_n:
        q.append({"act": f"🧱 Оформить проекты ({unformed_n})",
                  "why": "идеи ждут оформления: цель «готово, когда...» + первый шаг",
                  "url": "/pallets_page"})
    if FOCUS_TRIM_AT < focus_n <= 2 * FOCUS_TRIM_AT:
        q.append(trim)  # слегка толще канона - мягкое напоминание в хвосте разборов
    if hour >= 20 and not stars and counts.get("focus"):
        q.append({"act": "⭐ Отметить звезду на завтра",
                  "why": "две минуты: выбери главное — утром думать не придётся",
                  "url": "/focus_page"})
    if stars:
        q.append({"act": "★ Делать звезду: " + stars[0][:60],
                  "why": "решение принято ещё вчера — просто начни",
                  "url": "/focus_page"})
    if counts.get("focus"):
        q.append({"act": f"🎯 Работать фокус ({counts['focus']})",
                  "why": "одна задача на экране — её и делай",
                  "url": "/focus_page"})
    q.append({"act": "☕ Всё разобрано — отдых тоже работа",
              "why": "можно закинуть что-то во входящие или просто выдохнуть",
              "url": "/focus_page", "calm": True})
    return {"queue": q}


@app.get("/state")
def state():
    with db() as conn:
        counts = dict(conn.execute(
            "SELECT shelf, COUNT(*) FROM boxes GROUP BY shelf").fetchall())
        blocked = get_flag(conn, "blocked")
        total = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM points").fetchone()["s"]
        review_due = _review_due(conn)
        game_today_str = game_today().isoformat()
        stars = [{"id": r["id"], "text": r["raw_text"]} for r in conn.execute(
            "SELECT id, raw_text FROM boxes WHERE shelf='focus' AND starred=1"
            " AND starred_at IS NOT NULL AND starred_at<=? ORDER BY id",
            (game_today_str,))]
        rest_until = get_flag(conn, "rest_until")
        vacation_until = get_flag(conn, "vacation_until")
        on_vacation = bool(vacation_until) and game_today().isoformat() <= vacation_until
        dry = _dry_pallets(conn)
    return {"counts": counts, "blocked": bool(blocked), "blocked_since": blocked or None,
            "points_total": total, "call_at": CALL_AT, "truck_at": TRUCK_AT,
            "review_due": review_due, "stars": stars, "rest_until": rest_until or None,
            "vacation_until": vacation_until or None,
            "on_vacation": on_vacation, "dry_pallets": dry}


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
        return {"act": "🧰 Общая пересборка · +25 ⭐",
                "why": "столп №2: без неё система разваливается", "url": "/review"}
    if hour >= 20 and not stars and c.get("focus"):
        return {"act": "⭐ Отметить звезду на завтра",
                "why": "две минуты: выбери главное — утром думать не придётся", "url": "/focus_page"}
    if stars:
        return {"act": "★ Делать звезду: " + stars[0]["text"][:60],
                "why": "решение принято ещё вчера — просто начни", "url": "/focus_page"}
    if c.get("focus"):
        return {"act": f"🎯 Работать фокус ({c['focus']})",
                "why": "одна задача на экране — её и делай", "url": "/focus_page"}
    dry = st.get("dry_pallets") or []
    if dry:
        # тяга: фокус пуст → не «всё разобрано», а вытяни следующий шаг из проекта
        d0 = dry[0]
        return {"act": f"🥛 Подоить проект: {d0['title'][:50]}",
                "why": "фокус пуст — сними с проекта следующий конкретный шаг, "
                       f"и он поедет в работу{'' if len(dry)==1 else f' (ждут доения: {len(dry)})'}",
                "url": f"/pallets_page#p{d0['id']}"}
    return {"act": "☕ Всё разобрано — отдых тоже работа",
            "why": "можно закинуть что-то во входящие или просто выдохнуть",
            "url": "/focus_page", "calm": True}


def _brief_text():
    """Текстовый дайджест склада — рабочая память для ИИ-мостиков (сторож-чат, журнал).
    Только сегодняшнее состояние, никакой истории: раздутый контекст = глупее ответ."""
    now = datetime.now()
    wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][now.weekday()]
    today = date.today()
    L = []
    with db() as conn:
        counts = dict(conn.execute(
            "SELECT shelf, COUNT(*) FROM boxes GROUP BY shelf").fetchall())
        total = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM points").fetchone()["s"]
        pts_today = conn.execute(
            f"SELECT COALESCE(SUM(amount),0) s FROM points "
            f"WHERE date(at)={SQL_TODAY}").fetchone()["s"]
        L.append(f"Склад, {wd} {now:%d.%m %H:%M}. Зов {CALL_AT}, фура {TRUCK_AT}. "
                 f"⭐ {total} (сегодня +{pts_today}). 🎬 {yt_balance(conn):.0f} мин перекуса.")
        status = []
        if get_flag(conn, "blocked"):
            status.append("⛔ СТОПОР: склад стоит, пока входящие не разобраны")
        if get_flag(conn, "rest_until"):
            status.append("☕ хозяин сейчас отдыхает (кофейня)")
        if _review_due(conn):
            status.append("🧰 пора делать недельную пересборку (+25 ⭐)")
        if status:
            L.append(" · ".join(status))

        rows = conn.execute(
            "SELECT * FROM boxes WHERE shelf='focus' ORDER BY starred DESC, id").fetchall()
        L.append(f"\n🎯 Фокус (текущие дела) {len(rows)}/5:" if rows
                 else "\n🎯 Фокус (текущие дела): пусто.")
        for r in rows:
            last = conn.execute("SELECT MAX(at) m FROM moves WHERE box_id=?",
                                (r["id"],)).fetchone()["m"]
            idle = (today - date.fromisoformat(last[:10])).days if last else 0
            tags = []
            if r["starred"]:
                tags.append("⭐ звезда — главное на сегодня/завтра")
            if r["pallet_id"]:
                p = conn.execute("SELECT title FROM pallets WHERE id=?",
                                 (r["pallet_id"],)).fetchone()
                if p:
                    tags.append(f"шаг проекта «{p['title']}»")
            if idle >= STALE_FOCUS_DAYS:
                tags.append(f"🕸 без движения {idle} дн")
            L.append(f"  - {r['raw_text']}" + (f" [{', '.join(tags)}]" if tags else ""))

        n_inbox = counts.get("inbox", 0)
        if n_inbox:
            L.append(f"\n📥 Входящие (инбокс) {n_inbox}, свежие первыми:")
            L += [f"  - {r['raw_text']}" for r in conn.execute(
                "SELECT raw_text FROM boxes WHERE shelf='inbox' ORDER BY id DESC LIMIT 10")]
            if n_inbox > 10:
                L.append(f"  … и ещё {n_inbox - 10}")
        else:
            L.append("\n📥 Входящие (инбокс): пусто.")

        lit = [r for r in conn.execute("SELECT * FROM boxes WHERE shelf='waiting'")
               if (json.loads(r["glow_timer"] or "{}").get("next") or "9999") <= today.isoformat()]
        if lit:
            L.append("\n✨ Загорелись (ожидание, пора проверить): "
                     + " · ".join(f"«{r['raw_text']}»" for r in lit))

        pallets = [dict(r) for r in conn.execute(
            "SELECT * FROM pallets WHERE frozen=0 ORDER BY created_at DESC")]
        frozen_n = conn.execute("SELECT COUNT(*) c FROM pallets WHERE frozen=1").fetchone()["c"]
        unformed_n = conn.execute(
            "SELECT COUNT(*) c FROM boxes WHERE shelf='pallet_step' AND pallet_id IS NULL"
        ).fetchone()["c"]
        if pallets or frozen_n or unformed_n:
            L.append(f"\n🧱 Проекты (паллеты): живых {len(pallets)}"
                     + (f", ❄ заморожено {frozen_n}" if frozen_n else "")
                     + (f", неоформленных {unformed_n}" if unformed_n else "") + ":")
            for p in pallets:
                idle = (today - date.fromisoformat(_pallet_last_move(conn, p))).days
                step = conn.execute(
                    "SELECT raw_text, shelf FROM boxes WHERE pallet_id=? "
                    "AND shelf IN ('focus','pallet_step') "
                    "ORDER BY CASE shelf WHEN 'focus' THEN 0 ELSE 1 END, id LIMIT 1",
                    (p["id"],)).fetchone()
                if step:
                    where = "в фокусе" if step["shelf"] == "focus" else "заряжен"
                    tail = f"след. шаг {where}: «{step['raw_text']}»"
                else:
                    tail = "🥛 шаг НЕ назначен — подои проект"
                idle_s = f", без движения {idle} дн" if idle >= 3 else ""
                L.append(f"  - «{p['title']}» — {tail}{idle_s}")

        n_wait = counts.get("waiting", 0)
        if n_wait:
            nxt = sorted(filter(None, (json.loads(r["glow_timer"] or "{}").get("next")
                          for r in conn.execute("SELECT glow_timer FROM boxes WHERE shelf='waiting'"))))
            L.append(f"\n⏳ Ожидание (ждёшь ответа): {n_wait}"
                     + (f", ближайшая проверка {nxt[0]}" if nxt else ""))
        n_rack = counts.get("rack", 0)
        if n_rack:
            ctx = conn.execute(
                "SELECT COALESCE(context,'без контекста') c, COUNT(*) n FROM boxes "
                "WHERE shelf='rack' GROUP BY context ORDER BY n DESC").fetchall()
            L.append(f"🗄 Стеллажи (отложенное): {n_rack} — "
                     + " · ".join(f"{r['c']} {r['n']}" for r in ctx))
        if counts.get("mind"):
            L.append(f"💭 Мысли (непрочитанные): {counts['mind']}")
        done = conn.execute("SELECT raw_text FROM boxes WHERE shelf='done'").fetchall()
        if done:
            L.append(f"\n✅ Сделано сегодня {len(done)}: "
                     + " · ".join(f"«{r['raw_text']}»" for r in done))
    return "\n".join(L)


@app.get("/brief")
def brief():
    return PlainTextResponse(_brief_text())


# ── Сторож-чат: мозг живёт здесь, входы - TG-воркер и веб-виджет (theme.js).
#    LLM = рассказчик, не решатель: read-only, коробки не двигает.
LLM_CFG_PATH = Path.home() / ".config/warehouse/llm.json"
CHAT_HIST_PATH = Path.home() / ".local/share/warehouse/chat_history.json"
CHAT_KEEP = 24          # реплик в памяти (12 пар), сброс в новый день

STORO_PROMPT = """Ты — сторож склада продуктивности. Склад = таск-система хозяина: \
задачи-«коробки» лежат на полках (фокус, входящие, стеллажи, проекты-паллеты, ожидание).
Ты собеседник для рефлексии: с тобой можно просто поговорить о жизни. Состояние склада \
тебе дано ниже — вплетай его, когда уместно, но не пихай в каждую реплику.
Твои законы:
1. Ты read-only: двигать коробки не умеешь. Если в разговоре всплыло дело или мысль, \
которую жалко потерять — предложи хозяину записать её во входящие (в Telegram: обычное \
сообщение боту; на компьютере: Super+I).
2. Никогда не упрекаешь. Пыль и простой — факт, не вина. «Сил нет» — уважительная причина.
3. Отвечай коротко: 2-5 предложений, это чат. Без канцелярита, без списков без нужды.
4. Складские термины поясняй обычным языком: «паллета (проект)», «стеллаж (отложенное)».
Сегодняшнее состояние склада:
"""


def _chat_hist_load():
    try:
        h = json.loads(CHAT_HIST_PATH.read_text())
        if h.get("date") == game_today().isoformat():
            return h["msgs"]
    except Exception:
        pass
    return []


def _chat_hist_save(msgs):
    CHAT_HIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHAT_HIST_PATH.write_text(json.dumps(
        {"date": game_today().isoformat(), "msgs": msgs[-CHAT_KEEP:]},
        ensure_ascii=False))


def _llm_zen(cfg, messages):
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps({"model": cfg["model"],
                         "max_tokens": cfg.get("max_tokens", 4000),
                         "messages": messages}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg.get('key', '')}",
                 "User-Agent": "curl/8.5.0"})  # Cloudflare Zen: 403 на Python-urllib
    with urllib.request.urlopen(req, timeout=90) as r:
        resp = json.load(r)
    return (resp["choices"][0]["message"]["content"] or "").strip()


def _llm_gmn(messages):
    """Фолбэк: gmn stateless, историю разворачиваем в один промпт."""
    tag = {"system": "", "user": "Хозяин: ", "assistant": "Сторож: "}
    prompt = "\n".join(tag[m["role"]] + m["content"] for m in messages) + "\nСторож:"
    out = subprocess.run(["gmn", "ask", prompt],
                         capture_output=True, text=True, timeout=120)
    return out.stdout.strip() if out.returncode == 0 else ""


CHAT_NOTE_STOP = {"сделать", "проект", "проекты", "проекта", "время", "нужно",
                  "чтобы", "может", "давай", "такое", "просто", "сейчас"}


def _chat_project_notes(q_texts):
    """Заметки паллет, чьи названия упомянуты в разговоре. Урок первого диалога
    12.07: без этого сторож врёт вслепую («в системе ни слова» про то, что лежит
    в заметке). Матч: пересечение 5-буквенных основ слов (сцены/сцена → «сцен»).
    q_texts в порядке приоритета: текущий вопрос первым, затем история — если
    слотов (2) не хватает, побеждает то, о чём спрашивают СЕЙЧАС."""
    def stems(t):
        return {w[:5] for w in _norm(t).split()
                if len(w) >= 5 and w not in CHAT_NOTE_STOP}
    with db() as conn:
        pallets = [dict(r) for r in conn.execute(
            "SELECT title, note_path FROM pallets WHERE note_path IS NOT NULL")]
    for p in pallets:
        p["stems"] = stems(p["title"])
    parts, titles = [], []
    for t in q_texts:
        qs = stems(t)
        for p in pallets:
            if len(titles) >= 2:   # больше двух заметок за ход - раздутый контекст
                break
            if p["title"] in titles or not (p["stems"] & qs):
                continue
            np = Path(p["note_path"])
            nf = _note_file(p["note_path"])
            chunk = nf.read_text(errors="replace")[:3500] if nf.is_file() else ""
            if np.is_dir():
                files = ", ".join(f.name for f in sorted(np.iterdir())
                                  if not f.name.startswith("."))
                chunk += f"\n(файлы папки проекта: {files})"
            if chunk.strip():
                parts.append(f"\n--- Заметка проекта «{p['title']}» ---\n{chunk}")
                titles.append(p["title"])
    if not parts:
        return "", []
    head = ("\n\nЗаметки упомянутых проектов — ЧИТАЙ ВНИМАТЕЛЬНО: это свежая "
            "истина из Obsidian, она главнее того, что ты говорил в диалоге "
            "раньше (тогда ты заметок не видел).")
    return head + "".join(parts), titles


class ChatMsg(BaseModel):
    text: str


@app.post("/chat")
def chat(m: ChatMsg):
    q = m.text.strip()
    if not q:
        raise HTTPException(400, "empty text")
    hist = _chat_hist_load()
    recent_user = [h["content"] for h in hist[-4:] if h["role"] == "user"]
    notes, note_titles = _chat_project_notes([q] + recent_user[::-1])
    messages = ([{"role": "system", "content": STORO_PROMPT + _brief_text() + notes}]
                + hist + [{"role": "user", "content": q}])
    answer = ""
    try:
        cfg = json.loads(LLM_CFG_PATH.read_text())
        answer = _llm_zen(cfg, messages)
    except Exception as e:
        print(f"[chat] primary: {e}", flush=True)
    if not answer:
        try:
            answer = _llm_gmn(messages)
        except Exception as e:
            print(f"[chat] gmn: {e}", flush=True)
    if not answer:
        return {"answer": None}   # оба мозга лежат: клиент покажет «сторож спит»
    _chat_hist_save(hist + [{"role": "user", "content": q},
                            {"role": "assistant", "content": answer}])
    with db() as conn:
        log_event(conn, "chat_q", text=q[:500], notes=note_titles)
        log_event(conn, "chat_a", text=answer[:500])
    return {"answer": answer}


@app.get("/chat_history")
def chat_history():
    return {"msgs": _chat_hist_load()}


@app.get("/peek")
def peek():
    """Ambient peek: та же рекомендация, что в hub.html, без открытия окна (для tray/notify-send)."""
    st = state()
    now = _pick_now(st, glow())
    return {"now": now, "points_total": st["points_total"]}


@app.get("/review_data")
def review_data():
    """Итоги периода + очереди для пересборки: стеллажи, паллеты, заморозка."""
    with db() as conn:
        period_pts = conn.execute(
            f"SELECT COALESCE(SUM(amount),0) s FROM points "
            f"WHERE date(at) >= date({SQL_TODAY}, '-{REVIEW_DAYS} days')").fetchone()["s"]
        shipped = conn.execute(
            f"SELECT COUNT(DISTINCT box_id) c FROM moves WHERE to_shelf='done' "
            f"AND date(at) >= date({SQL_TODAY}, '-{REVIEW_DAYS} days')").fetchone()["c"]
        born = conn.execute(
            f"SELECT COUNT(*) c FROM boxes "
            f"WHERE date(born_at) >= date({SQL_TODAY}, '-{REVIEW_DAYS} days')").fetchone()["c"]
        # каждая коробка имеет свой cool-down: решена = не показывается 3 дня
        today = game_today()
        reviewed_raw = get_flag(conn, "review_rack_done") or ""
        reviewed_pairs = []
        if reviewed_raw:
            for part in reviewed_raw.split(","):
                if ":" not in part: continue
                bid, bdate = part.split(":", 1)
                try:
                    d = date.fromisoformat(bdate)
                    if (today - d).days < REVIEW_DAYS:
                        reviewed_pairs.append((bid, bdate))
                except ValueError:
                    continue
        decided_ids = {p[0] for p in reviewed_pairs}
        # сохранить обратно (почистил устаревшие)
        cleaned = ",".join(f"{bid}:{bdate}" for bid, bdate in reviewed_pairs)
        set_flag(conn, "review_rack_done", cleaned)
        all_racks = [dict(r) for r in conn.execute(
            "SELECT * FROM boxes WHERE shelf='rack' ORDER BY context, id")]
        racks = [r for r in all_racks if str(r["id"]) not in decided_ids]
        pallets = [dict(r) for r in conn.execute(
            "SELECT * FROM pallets WHERE frozen=0 ORDER BY created_at")]
        for p in pallets:
            p["steps"] = [dict(r) for r in conn.execute(
                "SELECT * FROM boxes WHERE pallet_id=? ORDER BY id", (p["id"],))]
            p["idle_days"] = (date.today()
                              - date.fromisoformat(_pallet_last_move(conn, p))).days
        frozen = [dict(r) for r in conn.execute(
            "SELECT * FROM pallets WHERE frozen=1 ORDER BY created_at")]
        # фокус переполнен: включить разгрузку в пересборку
        focus_count = dict(conn.execute(
            "SELECT shelf, COUNT(*) FROM boxes GROUP BY shelf")).get("focus", 0)
        focus_review = focus_count > 10
        focus_items = []
        if focus_review:
            g_today_str = today.isoformat()
            for r in conn.execute(
                "SELECT * FROM boxes WHERE shelf='focus' ORDER BY id"):
                d = dict(r)
                last = conn.execute("SELECT MAX(at) m FROM moves WHERE box_id=?",
                                    (r["id"],)).fetchone()["m"]
                d["idle_days"] = (date.today() - date.fromisoformat(last[:10])).days if last else 0
                d["starred"] = 1 if (d.get("starred") and d.get("starred_at")
                                     and d["starred_at"] <= g_today_str) else 0
                focus_items.append(d)
            focus_items.sort(key=lambda x: (-x["starred"], -x["idle_days"]))
            focus_items = focus_items[:20]
    return {"period_points": period_pts, "shipped": shipped, "born": born,
            "racks": racks, "pallets": pallets, "frozen": frozen,
            "total_racks": len(all_racks), "decided_racks": len(decided_ids),
            "reward": REVIEW_POINTS, "focus_review": focus_review,
            "focus_items": focus_items, "focus_count": focus_count}


class ReviewRackDecide(BaseModel):
    box_id: int
    action: str = ""  # focus | null | trash | done


@app.post("/review_rack_undo")
def review_rack_undo(m: ReviewRackDecide):
    """Отменить решение по коробке в пересборке: вернуть на стеллаж, снять кулдаун."""
    with db() as conn:
        existing = get_flag(conn, "review_rack_done") or ""
        pairs = [p for p in existing.split(",") if p]
        keep = [p for p in pairs if not p.startswith(f"{m.box_id}:")]
        set_flag(conn, "review_rack_done", ",".join(keep))
        row = conn.execute("SELECT shelf FROM boxes WHERE id=?", (m.box_id,)).fetchone()
        if row and row["shelf"] != "rack":
            conn.execute("UPDATE boxes SET shelf='rack' WHERE id=?", (m.box_id,))
            conn.execute(
                "INSERT INTO moves(box_id, from_shelf, to_shelf) VALUES(?, 'review_undo', 'rack')",
                (m.box_id,))
        log_event(conn, "review_rack_undo", box_id=m.box_id, action=m.action)
    return {"ok": True}


@app.post("/review_rack_decide")
def review_rack_decide(m: ReviewRackDecide):
    """Запомнить, что коробка решена сегодня → не покажется 3 дня."""
    with db() as conn:
        today = game_today().isoformat()
        existing = get_flag(conn, "review_rack_done") or ""
        ids = set(existing.split(",")) if existing else set()
        ids.add(f"{m.box_id}:{today}")
        set_flag(conn, "review_rack_done", ",".join(sorted(ids)))
        action_label = {"focus": "в фокус", "null": "лежит дальше", "trash": "мусор", "done": "выполнено"}
        al = action_label.get(m.action, m.action)
        log_event(conn, "review_rack_decide", box_id=m.box_id, action=m.action, label=al)
    return {"ok": True}


@app.post("/review_finish")
def review_finish():
    with db() as conn:
        _check_blocked(conn)  # пересборка — тоже мыслительная работа: при стопоре стоит
        today = game_today().isoformat()
        if get_flag(conn, "last_general_review") == today:
            return {"ok": True, "points": 0}  # уже пересобран сегодня
        if not _review_due(conn):
            return {"ok": True, "points": 0}  # ещё не прошло REVIEW_DAYS — награда не положена
        award(conn, REVIEW_POINTS, "общая пересборка")
        set_flag(conn, "last_general_review", today)
        log_event(conn, "review_finish", points=REVIEW_POINTS)
    return {"ok": True, "points": REVIEW_POINTS}


@app.get("/rack_review_status")
def rack_review_status():
    """Приманка для ревизии стеллажа: сколько лежит, давно ли смотрели, готова ли награда."""
    with db() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) c, "
            f"CAST(MAX(julianday('now') - julianday(COALESCE("
            f"  (SELECT MAX(at) FROM moves WHERE box_id=boxes.id AND to_shelf='rack'),"
            f"  born_at))) AS INT) oldest "
            f"FROM boxes WHERE shelf='rack'").fetchone()
        last = get_flag(conn, "last_rack_review")
        blocked = get_flag(conn, "blocked")
    today = game_today().isoformat()
    return {"count": row["c"], "oldest_days": row["oldest"] or 0,
            "last": last or None, "reward_ready": last != today and row["c"] > 0,
            "reward": RACK_REVIEW_POINTS, "blocked": bool(blocked)}


class RackReviewFinish(BaseModel):
    taken: int = 0    # ушло в фокус
    trashed: int = 0  # «пофиг»
    kept: int = 0     # осталось лежать


@app.post("/rack_review_finish")
def rack_review_finish(m: RackReviewFinish):
    with db() as conn:
        today = game_today().isoformat()
        pts = 0
        if get_flag(conn, "last_rack_review") != today:
            if m.taken + m.trashed >= 1:
                pts = RACK_REVIEW_POINTS
                award(conn, pts, "ревизия стеллажа")
            set_flag(conn, "last_rack_review", today)
        log_event(conn, "rack_review_finish",
                  taken=m.taken, trashed=m.trashed, kept=m.kept, points=pts)
    return {"ok": True, "points": pts}


# ─── 🏆 Сундук (конвертация ⭐ в награды) ──────────────────────────────────────


class ChestAdd(BaseModel):
    title: str
    cost: int


class ChestBuy(BaseModel):
    id: int


class ChestDelete(BaseModel):
    id: int


@app.get("/chest")
def chest_list():
    with db() as conn:
        total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM points").fetchone()["s"]
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM chest ORDER BY bought_at IS NOT NULL, created_at DESC")]
    return {"items": items, "points_total": total}


@app.post("/chest_add")
def chest_add(c: ChestAdd):
    title = c.title.strip()
    if not title or c.cost < 1:
        raise HTTPException(400, "title required, cost >= 1")
    with db() as conn:
        cur = conn.execute("INSERT INTO chest(title, cost) VALUES(?,?)", (title, c.cost))
        log_event(conn, "chest_add", chest_id=cur.lastrowid, title=title, cost=c.cost)
    return {"ok": True, "id": cur.lastrowid}


@app.post("/chest_buy")
def chest_buy(c: ChestBuy):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM chest WHERE id=? AND bought_at IS NULL", (c.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such chest item or already bought")
        total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM points").fetchone()["s"]
        if total < row["cost"]:
            raise HTTPException(402, f"not enough points: need {row['cost']}, have {total}")
        award(conn, -row["cost"], f"сундук: {row['title']}")
        conn.execute("UPDATE chest SET bought_at=datetime('now','localtime') WHERE id=?",
                     (c.id,))
        log_event(conn, "chest_buy", chest_id=c.id, title=row["title"], cost=row["cost"])
    return {"ok": True}


@app.post("/chest_delete")
def chest_delete(c: ChestDelete):
    with db() as conn:
        conn.execute("DELETE FROM chest WHERE id=?", (c.id,))
        log_event(conn, "chest_delete", chest_id=c.id)
    return {"ok": True}


@app.get("/chest_page")
def chest_page():
    return FileResponse(Path(__file__).parent / "chest.html")


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


# ─── 🍺 Таверна (отпуск) ──────────────────────────────────────────────────────


@app.post("/vacation")
def vacation_start(body: dict):
    """Уйти в отпуск на N дней. Снимает стопор-уведомления фуры."""
    days = int(body.get("days", 1))
    if days < 1:
        raise HTTPException(400, "days must be >= 1")
    until = (game_today() + timedelta(days=days)).isoformat()
    with db() as conn:
        set_flag(conn, "vacation_until", until)
        log_event(conn, "vacation_start", days=days, until=until)
    return {"ok": True, "until": until}


@app.post("/vacation_end")
def vacation_end():
    """Прервать отпуск досрочно."""
    with db() as conn:
        set_flag(conn, "vacation_until", "")
        log_event(conn, "vacation_end")
    return {"ok": True}


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


@app.get("/yt")
def yt_state():
    """🎬 кассовый аппарат перекусов: баланс + сегодняшний оборот."""
    today = game_today().isoformat()
    with db() as conn:
        bal = yt_balance(conn)
        earned = conn.execute(
            "SELECT COALESCE(SUM(delta),0) s FROM yt_ledger "
            "WHERE delta>0 AND date(at)=?", (today,)).fetchone()["s"]
        spent = conn.execute(
            "SELECT COALESCE(SUM(-delta),0) s FROM yt_ledger "
            "WHERE delta<0 AND date(at)=?", (today,)).fetchone()["s"]
    return {"balance": round(bal, 1), "per_done": YT_PER_DONE, "cap": YT_CAP,
            "today_earned": round(earned, 1), "today_spent": round(spent, 1),
            "watching": _yt_watching}


@app.post("/heartbeat")
def heartbeat():
    with _heartbeat_lock:
        global _last_heartbeat
        _last_heartbeat = time.time()
    return {"ok": True}


@app.get("/health")
def health():
    with db() as conn:
        counts = dict(conn.execute(
            "SELECT shelf, COUNT(*) FROM boxes GROUP BY shelf").fetchall())
    return {"ok": True, "db": str(DB_PATH), "counts": counts}


_HUSHED_NOTIFY = {"⛔ Склад встал", "📦 Склад зовёт"}


def notify(title, body, urgent=False):
    if title in _HUSHED_NOTIFY and _browser_active():
        return
    cmd = ["notify-send", title, body]
    if urgent:
        cmd[1:1] = ["-u", "critical"]
    env = dict(os.environ,
               DISPLAY=os.environ.get("DISPLAY", ":0"),
               DBUS_SESSION_BUS_ADDRESS=os.environ.get(
                   "DBUS_SESSION_BUS_ADDRESS",
                   f"unix:path=/run/user/{os.getuid()}/bus"))
    subprocess.run(cmd, env=env, timeout=5, check=False)


_yt_watching = False  # для /yt: смотрит ли прямо сейчас (обновляет watcher)

# Heartbeat: когда браузер открыт на любой странице склада (кроме capture),
# storozh.js шлёт POST /heartbeat раз в минуту. Используем чтобы не слать
# notify-send, когда пользователь уже в приложении.
_last_heartbeat = 0.0
_heartbeat_lock = threading.Lock()

def _browser_active(timeout_s=300):
    """Браузер склада открыт (heartbeat < timeout_s назад)?"""
    with _heartbeat_lock:
        if not _last_heartbeat:
            return False
        return time.time() - _last_heartbeat < timeout_s


def _aw_get(path):
    with urllib.request.urlopen(f"{AW_URL}/{path}", timeout=5) as r:
        return json.load(r)


def _aw_find_buckets():
    """ID бакетов окна и AFK - имена включают hostname, ищем по типу."""
    win = afk = None
    for bid, b in _aw_get("buckets/").items():
        if b.get("type") == "currentwindow":
            win = bid
        elif b.get("type") == "afkstatus":
            afk = bid
    return win, afk


def yt_watcher():
    """Списание перекус-кредита по факту: ActivityWatch видит YouTube в активном
    окне (и человек не AFK) → минус минута. При нуле - нудж, НЕ блок (канон:
    трение вместо стены, реактивность от жёстких блоков хоронит систему).
    ActivityWatch недоступен → экономика просто замирает, склад живёт дальше."""
    tick = 60
    win_b = afk_b = None
    last_nudge = 0.0
    global _yt_watching
    while True:
        time.sleep(tick)
        try:
            if not (win_b and afk_b):
                win_b, afk_b = _aw_find_buckets()
                if not (win_b and afk_b):
                    continue
            wev = _aw_get(f"buckets/{win_b}/events?limit=1")
            aev = _aw_get(f"buckets/{afk_b}/events?limit=1")
            if not (wev and aev):
                _yt_watching = False
                continue
            # свежесть: heartbeat должен покрывать «сейчас» (иначе AW спит/стоит)
            ts = datetime.fromisoformat(wev[0]["timestamp"].replace("Z", "+00:00"))
            age = (datetime.now(ts.tzinfo) - ts).total_seconds() - wev[0]["duration"]
            active = (age < 3 * tick
                      and aev[0]["data"].get("status") == "not-afk"
                      and YT_TITLE_RE.search(wev[0]["data"].get("title", "")))
            _yt_watching = bool(active)
            if not active:
                continue
            with db() as conn:
                bal = yt_balance(conn)
                if bal > 0:
                    spend = min(tick / 60, bal)
                    conn.execute("INSERT INTO yt_ledger(delta, reason) VALUES(?,?)",
                                 (-spend, "просмотр"))
                elif time.time() - last_nudge >= YT_NUDGE_COOLDOWN:
                    last_nudge = time.time()
                    focus_txt = conn.execute(
                        "SELECT raw_text FROM boxes WHERE shelf='focus' "
                        "ORDER BY starred DESC, id LIMIT 1").fetchone()
                    hint = (f"один шаг вернёт (+{YT_PER_DONE}м): "
                            + focus_txt["raw_text"][:60]) if focus_txt \
                        else f"любой шаг из фокуса вернёт +{YT_PER_DONE}м"
                    log_event(conn, "yt_nudge")
                    notify("🎬 Перекус-кредит кончился", hint)
        except Exception:
            win_b = afk_b = None  # AW перезапустился/недоступен - переоткроем бакеты
            _yt_watching = False


def scheduler():
    """Зов в CALL_AT; фура + стопор-чек в TRUCK_AT. Тикает раз в 30 сек."""
    fired = set()
    while True:
        now = datetime.now()
        hm = now.strftime("%H:%M")
        today = game_today().isoformat()
        _on_vacation = False
        with db() as conn:
            vac = get_flag(conn, "vacation_until")
            if vac:
                if today <= vac:
                    _on_vacation = True
                else:
                    set_flag(conn, "vacation_until", "")
                    log_event(conn, "vacation_expired")
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
        # ⏱ таймер-автостоп: забытые сессии
        with db() as conn:
            # снять просроченный отпуск
            vac = get_flag(conn, "vacation_until")
            if vac and game_today().isoformat() > vac:
                set_flag(conn, "vacation_until", "")
                log_event(conn, "vacation_expired")
            running = conn.execute(
                "SELECT ts.*, b.raw_text FROM timer_sessions ts "
                "JOIN boxes b ON b.id=ts.box_id "
                "WHERE ts.stopped_at IS NULL LIMIT 1").fetchone()
        if running:
            started = datetime.fromisoformat(running["started_at"])
            age = (now - started).total_seconds()
            was_afk = False
            if age > TIMER_AFK_STOP_S:
                try:
                    win_b, afk_b = _aw_find_buckets()
                    if win_b and afk_b:
                        afk_data = _aw_get(
                            f"buckets/{afk_b}/events?limit=10")
                        for e in reversed(afk_data):
                            dur = e.get("duration", 0)
                            if (isinstance(e.get("data"), dict)
                                    and e["data"].get("status") == "afk"
                                    and dur >= TIMER_AFK_STOP_S):
                                ts = datetime.fromisoformat(
                                    e["timestamp"].replace("Z", "+00:00"))
                                with db() as inner:
                                    elapsed = int((ts - started).total_seconds())
                                    _close_timer(inner, running, elapsed,
                                                 "auto-stop: afk")
                                    log_event(inner, "timer_autostop",
                                              session_id=running["id"],
                                              reason="afk", elapsed=elapsed)
                                notify("⏱ Таймер остановлен",
                                       f"«{running['raw_text'][:50]}» — AFK {int(dur//60)} мин")
                                was_afk = True
                                break
                except Exception:
                    pass
            if not was_afk and age > TIMER_MAX_S:
                with db() as conn:
                    elapsed = TIMER_MAX_S
                    _close_timer(conn, running, elapsed, "auto-stop: cap")
                    log_event(conn, "timer_autostop",
                              session_id=running["id"],
                              reason="cap", elapsed=elapsed)
                notify("⏱ Таймер остановлен",
                       f"«{running['raw_text'][:50]}» — лимит {TIMER_MAX_S//3600}ч")
        if not _on_vacation:
            if hm == MORNING_AT and ("morning", today) not in fired:
                fired.add(("morning", today))
                with db() as conn:
                    stars = [r["raw_text"] for r in conn.execute(
                        "SELECT raw_text FROM boxes WHERE shelf='focus' AND starred=1"
                        " AND starred_at IS NOT NULL AND starred_at<=?",
                        (game_today().isoformat(),))]
                if stars:  # утром решений ноль: звезда выбрана с вечера
                    notify("⭐ Твоя звезда на сегодня", " · ".join(stars))
                    with db() as conn:
                        log_event(conn, "sched_morning", stars=len(stars))
            if hm == CALL_AT and ("call", today) not in fired:
                fired.add(("call", today))
                with db() as conn:
                    n = conn.execute("SELECT COUNT(*) c FROM boxes WHERE shelf='inbox'").fetchone()["c"]
                    has_star = conn.execute(
                        "SELECT COUNT(*) c FROM boxes WHERE shelf='focus' AND starred=1"
                        " AND starred_at IS NOT NULL AND starred_at<=?",
                        (game_today().isoformat(),)).fetchone()["c"]
                if n:
                    notify("📦 Склад зовёт", f"В инбоксе {n} коробок. Вечерний разбор: http://127.0.0.1:{PORT}/terminal")
                elif not has_star:
                    notify("⭐ Две минуты", f"Отметь звезду на завтра: http://127.0.0.1:{PORT}/focus_page")
            if hm == CALL_AT and ("review", today) not in fired:
                fired.add(("review", today))
                with db() as conn:
                    due = _review_due(conn)
                if due:
                    notify("🧰 Общая пересборка", f"Столп №2: пересобери склад. http://127.0.0.1:{PORT}/review")
        # страховочная автоконтекстуализация: пользователь (или событие-триггер)
        # за день так и не пересобрал стеллаж - вечером делаем сами, но только
        # если есть зачем: бесконтекстные коробки, свежие приезды или прошлый фейл
        if hm == TRUCK_AT and ("recontext", today) not in fired:
            fired.add(("recontext", today))
            n_rack = null_ctx = 0
            last_arrival = None
            st = {}
            if not _on_vacation:
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
            rows = []
            with db() as conn:
                rows = conn.execute("SELECT id FROM boxes WHERE shelf='done'").fetchall()
                for r in rows:
                    conn.execute("UPDATE boxes SET shelf='archived' WHERE id=?", (r["id"],))
                    conn.execute("INSERT INTO moves(box_id, from_shelf, to_shelf) "
                                 "VALUES(?, 'done', 'archived')", (r["id"],))
                n_inbox = conn.execute("SELECT COUNT(*) c FROM boxes WHERE shelf='inbox'").fetchone()["c"]
                if not _on_vacation and n_inbox and get_flag(conn, "last_ritual") != today:
                    set_flag(conn, "blocked", today)
                log_event(conn, "sched_truck", archived=len(rows), inbox_left=n_inbox,
                          stopper=bool(not _on_vacation and n_inbox and get_flag(conn, "last_ritual") != today))
            frozen_now = []
            if not _on_vacation:
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
            if frozen_now and not _on_vacation:
                notify("🧊 Заморозка", "Месяц без движения: " + ", ".join(frozen_now))
            if not _on_vacation and n_inbox and get_flag_standalone("last_ritual") != today:
                notify("⛔ Склад встал", f"Инбокс не разобран ({n_inbox}). Работа стоит до разбора.", urgent=True)
        if now.hour == DAY_START_H and now.minute == 0:
            fired = {k for k in fired if k[1] == today}
        time.sleep(30)


def get_flag_standalone(key):
    with db() as conn:
        return get_flag(conn, key)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=yt_watcher, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
