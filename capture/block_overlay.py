#!/usr/bin/env python3
"""Оверлей-стена (эмуляция Cold Turkey без extension, 21.07).

Следит за активным окном. Если идёт блок (гейт закрыт - расписание/кредит/остыв.)
И активное окно = браузер И он показывает/озвучивает залип-сайт → рисует стену
поверх окна браузера (следует за геометрией секунда-в-секунду) и глушит звук
браузера. Стена держится, пока вкладка на залип-сайте активна. Кнопка «закрыть
вкладку» шлёт Ctrl+W в браузер. Сеть режет sing-box - это визуальный+звуковой слой.

Запуск: DISPLAY=:0 python3 block_overlay.py  (systemd-юнит warehouse-block-overlay).
"""
import re
import json
import subprocess
import tkinter as tk
import urllib.request

SERVER = "http://127.0.0.1:8091"
BROWSER_WM = re.compile(r"firefox|navigator|google-chrome|chromium|chrome", re.I)
DISTRACT = re.compile(r"youtube|twitch|telegram|rutube|vk видео|vkvideo|порн|porn", re.I)
AUDIO_YT = re.compile(r"youtube|twitch", re.I)
TICK_MS = 120           # частота слежения за геометрией (плавно при ресайзе)
POLL_STATE_EVERY = 16   # гейт спрашиваем реже (раз в ~2с), не каждый тик


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def active_window():
    wid = sh(["xdotool", "getactivewindow"]).strip()
    if not wid:
        return None
    cls = sh(["xprop", "-id", wid, "WM_CLASS"])
    if not BROWSER_WM.search(cls):
        return None
    title = sh(["xdotool", "getwindowname", wid]).strip()
    # xwininfo даёт АБСОЛЮТНЫЕ координаты (xdotool getwindowgeometry врёт офсетом
    # при reparenting WM - оверлей уезжал вправо). "Absolute upper-left X/Y".
    info = sh(["xwininfo", "-id", wid])
    geo = {}
    for line in info.splitlines():
        s = line.strip()
        if s.startswith("Absolute upper-left X:"):
            geo["x"] = int(s.split(":")[1])
        elif s.startswith("Absolute upper-left Y:"):
            geo["y"] = int(s.split(":")[1])
        elif s.startswith("Width:"):
            geo["w"] = int(s.split(":")[1])
        elif s.startswith("Height:"):
            geo["h"] = int(s.split(":")[1])
    if all(k in geo for k in ("x", "y", "w", "h")):
        return {"id": wid, "title": title, **geo}
    return None


def browser_audio_distraction():
    """Играет ли браузер звук залип-сайта (media.name). LC_ALL=C для рус. локали."""
    import os
    out = sh(["env", "LC_ALL=C", "pactl", "list", "sink-inputs"])
    ids, cur, is_browser = [], None, False
    for line in out.splitlines():
        m = re.match(r"Sink Input #(\d+)", line)
        if m:
            cur, is_browser = m.group(1), False
        elif "application.name" in line and BROWSER_WM.search(line):
            is_browser = True
        elif "media.name" in line:
            mm = re.search(r'media\.name = "(.*)"', line)
            if mm and AUDIO_YT.search(mm.group(1)):
                ids.append(cur)
    return ids


def mute_browser(mute):
    import os
    env = {**os.environ, "LC_ALL": "C"}
    out = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True,
                         text=True, env=env, timeout=3).stdout
    cur = None
    for line in out.splitlines():
        m = re.match(r"Sink Input #(\d+)", line)
        if m:
            cur = m.group(1)
        elif "application.name" in line and BROWSER_WM.search(line) and cur:
            subprocess.run(["pactl", "set-sink-input-mute", cur, "1" if mute else "0"],
                           timeout=3, check=False)
            cur = None


class Wall:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#0d0d12")
        self.cv = tk.Frame(self.root, bg="#0d0d12")
        self.cv.pack(fill="both", expand=True)
        self.title = tk.Label(self.cv, text="🔒 ЗАБЛОКИРОВАНО", fg="#ff5a5a",
                              bg="#0d0d12", font=("sans", 34, "bold"))
        self.title.pack(pady=(0, 6), expand=True)
        self.sub = tk.Label(self.cv, text="", fg="#e0a0a0", bg="#0d0d12",
                            font=("sans", 15), wraplength=700, justify="center")
        self.sub.pack(pady=4)
        self.thought = tk.Label(self.cv, text="Вернись к делу. Окно закроется, когда закроешь вкладку.",
                                fg="#8a8aa0", bg="#0d0d12",
                                font=("sans", 13), wraplength=640, justify="center")
        self.thought.pack(pady=10)
        self.btn = tk.Button(self.cv, text="✕ Закрыть эту вкладку", fg="#fff",
                             bg="#c0392b", font=("sans", 13, "bold"), relief="flat",
                             padx=18, pady=8, command=self.close_tab)
        self.btn.pack(pady=8)
        self.shown = False
        self.cur_wid = None
        self.state = {"active": False}
        self.tick_n = 0

    def close_tab(self):
        if self.cur_wid:
            subprocess.run(["xdotool", "key", "--window", self.cur_wid, "ctrl+w"],
                           timeout=3, check=False)

    def show_over(self, win):
        self.cur_wid = win["id"]
        self.root.geometry(f"{win['w']}x{win['h']}+{win['x']}+{win['y']}")
        self.sub.config(text=f"{self.state.get('reason_ru','')}"
                             + (f" · до {str(self.state.get('until'))[11:16]}"
                                if self.state.get('until') and ':' in str(self.state.get('until')) else ""))
        if not self.shown:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.shown = True

    def hide_wall(self):
        if self.shown:
            self.root.withdraw()
            self.shown = False

    def loop(self):
        self.tick_n += 1
        if self.tick_n % POLL_STATE_EVERY == 1:
            try:
                self.state = json.load(urllib.request.urlopen(SERVER + "/block_wall", timeout=3))
            except Exception:
                self.state = {"active": False}
        win = active_window()
        blocked = self.state.get("active")
        # СТЕНА - только над окном, чей ЗАГОЛОВОК = залип-сайт (то, на что смотришь).
        # Не по звуку: ютуб мог играть в фоне в другом окне - стена не туда.
        wall = bool(blocked and win and DISTRACT.search(win["title"]))
        if wall:
            self.show_over(win)
        else:
            self.hide_wall()
        # ЗВУК - глушим отдельно: залип-звук в браузере при блоке (даже фоновый),
        # снимаем глушение, когда блок кончился.
        if blocked and browser_audio_distraction():
            mute_browser(True)
        elif not blocked:
            mute_browser(False)
        self.root.after(TICK_MS, self.loop)

    def run(self):
        self.root.after(TICK_MS, self.loop)
        self.root.mainloop()


if __name__ == "__main__":
    Wall().run()
