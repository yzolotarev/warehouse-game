#!/usr/bin/env python3
"""Маленькое всплывающее окно захвата в инбокс. Enter = отправить, Esc = закрыть."""
import json
import tkinter as tk
import urllib.request

BG, CARD, LINE, TXT, DIM, AMBER = "#1e1e24", "#2a2a32", "#44444e", "#e8e8ee", "#8a8a94", "#ffb84d"


def send(text):
    req = urllib.request.Request(
        "http://127.0.0.1:8091/inbox",
        data=json.dumps({"text": text, "source": "pc"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg=LINE)

W, H = 480, 92
x = (root.winfo_screenwidth() - W) // 2
y = int(root.winfo_screenheight() * 0.28)
root.geometry(f"{W}x{H}+{x}+{y}")

card = tk.Frame(root, bg=BG)
card.place(x=1, y=1, width=W - 2, height=H - 2)

label = tk.Label(card, text="📦 в инбокс", bg=BG, fg=AMBER,
                 font=("sans-serif", 10), anchor="w")
label.pack(fill="x", padx=14, pady=(10, 2))

entry = tk.Entry(card, bg=CARD, fg=TXT, insertbackground=AMBER,
                 relief="flat", font=("sans-serif", 13))
entry.pack(fill="x", padx=14, ipady=7)
entry.focus_force()


def on_enter(_=None):
    text = entry.get().strip()
    if not text:
        root.destroy()
        return
    try:
        data = send(text)
        sim = data.get("similar_trashed")
        if sim:
            label.config(text=f"⚠ #{data['id']} принято, похоже на затрешенное: {sim['text'][:40]}",
                        fg=AMBER)
            entry.delete(0, "end")
            root.after(1800, root.destroy)
        else:
            label.config(text=f"✅ #{data['id']} принято", fg="#8dff9e")
            entry.delete(0, "end")
            root.after(450, root.destroy)
    except OSError:
        label.config(text="⚠ склад не отвечает - текст НЕ сохранён", fg="#ff8d8d")


entry.bind("<Return>", on_enter)
root.bind("<Escape>", lambda e: root.destroy())
root.after(200, lambda: entry.focus_force())
root.mainloop()
