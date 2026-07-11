#!/usr/bin/env python3
"""Watchdog зависших паллет: раз в день проверяет проекты без движения N+ дней
(idle_days уже считает сам /pallets) и предлагает через web2api ОДИН конкретный
следующий шаг. Не блокирует, не давит - одна ненавязчивая нотификация на паллету,
повтор не чаще раза в RENOTIFY_DAYS."""
import json
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime

URL_BASE = "http://127.0.0.1:8091"
STALE_DAYS = 3
RENOTIFY_DAYS = 7
STATE_FILE = Path.home() / ".local/share/warehouse/watchdog_notified.json"


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def suggest_step(title, steps_text):
    body = "\n".join(f"- {s}" for s in steps_text) or "(шагов пока нет)"
    prompt = (f"Проект: «{title}»\nУже есть шаги:\n{body}\n\n"
              "Предложи ОДИН конкретный следующий шаг (1 короткое предложение, "
              "без вступлений и пояснений).")
    try:
        r = subprocess.run(
            ["llm-brains", "--backend", "web2api", "--max-tokens", "80", prompt],
            capture_output=True, text=True, timeout=200)  # web2api - веб-скрейпинг, не API: измерено 30-60с+ на реальных промптах
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return r.stdout.strip().splitlines()[0][:140]
    except (subprocess.SubprocessError, OSError):
        return None


def main():
    try:
        with urllib.request.urlopen(f"{URL_BASE}/pallets", timeout=5) as resp:
            data = json.load(resp)
    except (OSError, json.JSONDecodeError):
        return

    state = load_state()
    now = datetime.now().timestamp()
    for p in data.get("pallets", []):
        if p["idle_days"] < STALE_DAYS:
            continue
        pid = str(p["id"])
        last = state.get(pid)
        if last and (now - last) / 86400 < RENOTIFY_DAYS:
            continue
        steps = [s["raw_text"] for s in p["steps"] if s["shelf"] not in ("done", "trash")]
        step = suggest_step(p["title"], steps)
        if not step:
            continue
        state[pid] = now
        save_state(state)
        subprocess.run([
            "notify-send", "-a", "Склад",
            f"🧰 «{p['title']}» стоит {p['idle_days']} дн.",
            f"Предлагаю: {step}",
        ])


if __name__ == "__main__":
    main()
