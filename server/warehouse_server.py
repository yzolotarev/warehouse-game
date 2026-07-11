#!/usr/bin/env python3
"""Склад: сервер-демон (часть 1 - фундамент).

Владеет БД коробок. Единственный источник истины.
API: POST /inbox, GET /boxes, POST /move, GET /health
"""
import json
import os
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

CALL_AT = os.environ.get("WAREHOUSE_CALL", "21:30")     # зов
TRUCK_AT = os.environ.get("WAREHOUSE_TRUCK", "22:00")    # фура + стопор-чек
MORNING_AT = os.environ.get("WAREHOUSE_MORNING", "09:30")  # утро: напомнить ⭐

DB_PATH = Path(os.environ.get("WAREHOUSE_DB",
                              Path.home() / ".local/share/warehouse/warehouse.db"))
PORT = int(os.environ.get("WAREHOUSE_PORT", "8091"))

SHELVES = {"inbox", "focus", "rack", "pallet_step", "waiting", "done", "trash", "mind", "archived"}
TRIAGE_POINTS = 1     # за разобранную коробку
RITUAL_POINTS = 10    # за инбокс, разобранный до нуля
DONE_POINTS = 3       # за сделанную задачу
SERIES_POINTS = 2     # за каждый следующий шаг той же паллеты за день (батчинг)
REVIEW_POINTS = 25    # за недельную пересборку
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
"""


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


app = FastAPI(title="warehouse")


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


@app.post("/inbox")
def add_inbox(item: InboxItem):
    text = item.text.strip()
    if not text:
        raise HTTPException(400, "empty text")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO boxes(raw_text, source) VALUES(?, ?)",
            (text, item.source))
        box_id = cur.lastrowid
        conn.execute(
            "INSERT INTO moves(box_id, from_shelf, to_shelf) VALUES(?, NULL, 'inbox')",
            (box_id,))
    return {"id": box_id}


@app.get("/boxes")
def list_boxes(shelf: str = "inbox", limit: int = 200):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM boxes WHERE shelf=? ORDER BY born_at DESC LIMIT ?",
            (shelf, limit)).fetchall()
    return [dict(r) for r in rows]


@app.post("/move")
def move_box(m: Move):
    if m.to not in SHELVES:
        raise HTTPException(400, f"unknown shelf: {m.to}")
    with db() as conn:
        row = conn.execute("SELECT shelf, pallet_id FROM boxes WHERE id=?", (m.id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such box")
        # Физический стопор: пока инбокс не разобран, двигаются только коробки
        # ИЗ инбокса (разбор) и НА инбокс (откат решения)
        if get_flag(conn, "blocked") and row["shelf"] != "inbox" and m.to != "inbox":
            raise HTTPException(423, "склад стоит: сначала разбери инбокс")
        conn.execute(
            "UPDATE boxes SET shelf=?, context=COALESCE(?, context), grp=COALESCE(?, grp) WHERE id=?",
            (m.to, m.context, m.grp, m.id))
        if m.to != "focus":
            # звезда живёт только в фокусе: ушла коробка — погасла звезда
            conn.execute("UPDATE boxes SET starred=0 WHERE id=?", (m.id,))
        conn.execute(
            "INSERT INTO moves(box_id, from_shelf, to_shelf) VALUES(?, ?, ?)",
            (m.id, row["shelf"], m.to))
        if m.context:
            conn.execute("INSERT OR IGNORE INTO racks(context) VALUES(?)", (m.context,))
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
        if p.from_box:
            conn.execute("UPDATE boxes SET pallet_id=? WHERE id=?", (pid, p.from_box))
        step_id = None
        if p.first_step.strip():
            c2 = conn.execute(
                "INSERT INTO boxes(raw_text, source, shelf, pallet_id) "
                "VALUES(?, 'pallet', 'focus', ?)", (p.first_step.strip(), pid))
            step_id = c2.lastrowid
            conn.execute("INSERT INTO moves(box_id, from_shelf, to_shelf) "
                         "VALUES(?, NULL, 'focus')", (step_id,))
    return {"pallet_id": pid, "step_id": step_id}


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


class PalletFreeze(BaseModel):
    id: int
    frozen: bool


@app.post("/pallet/freeze")
def pallet_freeze(f: PalletFreeze):
    with db() as conn:
        conn.execute("UPDATE pallets SET frozen=? WHERE id=?", (int(f.frozen), f.id))
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
    return {"counts": counts, "blocked": bool(blocked), "blocked_since": blocked or None,
            "points_total": total, "call_at": CALL_AT, "truck_at": TRUCK_AT,
            "review_due": review_due, "stars": stars}


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
        today = date.today().isoformat()
        if get_flag(conn, "last_week_review") == today:
            return {"ok": True, "points": 0}  # уже пересобран сегодня
        award(conn, REVIEW_POINTS, "недельная пересборка")
        set_flag(conn, "last_week_review", today)
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


@app.get("/done_page")
def done_page():
    return FileResponse(Path(__file__).parent / "done.html")


@app.get("/review")
def review_page():
    return FileResponse(Path(__file__).parent / "review.html")


@app.get("/gemini_scene")
def gemini_scene_page():
    return FileResponse(Path(__file__).parent.parent / "proto/gemini-scene.html")


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
        if hm == MORNING_AT and ("morning", today) not in fired:
            fired.add(("morning", today))
            with db() as conn:
                stars = [r["raw_text"] for r in conn.execute(
                    "SELECT raw_text FROM boxes WHERE shelf='focus' AND starred=1")]
            if stars:  # утром решений ноль: звезда выбрана с вечера
                notify("⭐ Твоя звезда на сегодня", " · ".join(stars))
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
            frozen_now = []
            with db() as conn:
                for p in conn.execute("SELECT * FROM pallets WHERE frozen=0").fetchall():
                    idle = (date.today()
                            - date.fromisoformat(_pallet_last_move(conn, p))).days
                    if idle >= STALE_PALLET_DAYS:
                        conn.execute("UPDATE pallets SET frozen=1 WHERE id=?", (p["id"],))
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
