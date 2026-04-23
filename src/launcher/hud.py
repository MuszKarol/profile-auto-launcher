"""Minimal HUD-style profile picker built on Tkinter (stdlib, always available).

Keyboard-first: type to filter, ↑/↓ to move, Enter to run, Esc to cancel.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from launcher.config import Profile


BG = "#101216"
FG = "#e6e6e6"
ACCENT = "#6aa9ff"
MUTED = "#8a8f98"
SELECTED = "#1f2a3a"


def pick_profile(profiles: list[Profile], on_pick: Callable[[Profile], None] | None = None) -> Profile | None:
    if not profiles:
        return None

    root = tk.Tk()
    root.title("Profile Auto Launcher")
    root.configure(bg=BG)
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    root.overrideredirect(True)

    w, h = 560, 380
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")

    selection: dict[str, Profile | None] = {"value": None}
    filtered = list(profiles)
    index = tk.IntVar(value=0)

    outer = tk.Frame(root, bg=BG, padx=16, pady=16)
    outer.pack(fill="both", expand=True)

    query = tk.StringVar()
    entry = tk.Entry(
        outer, textvariable=query, bg=BG, fg=FG, insertbackground=FG,
        relief="flat", font=("Segoe UI", 16), highlightthickness=1,
        highlightbackground=MUTED, highlightcolor=ACCENT,
    )
    entry.pack(fill="x", ipady=8)
    entry.focus_set()

    listbox = tk.Listbox(
        outer, bg=BG, fg=FG, selectbackground=SELECTED, selectforeground=ACCENT,
        relief="flat", highlightthickness=0, activestyle="none",
        font=("Segoe UI", 12),
    )
    listbox.pack(fill="both", expand=True, pady=(12, 8))

    hint = tk.Label(
        outer, text="↑↓ navigate · Enter run · Esc cancel",
        bg=BG, fg=MUTED, font=("Segoe UI", 9),
    )
    hint.pack(anchor="w")

    def redraw() -> None:
        listbox.delete(0, tk.END)
        q = query.get().lower().strip()
        filtered.clear()
        for p in profiles:
            if q and q not in p.name.lower() and q not in p.description.lower():
                continue
            filtered.append(p)
            suffix = "  ★" if p.default else ""
            listbox.insert(tk.END, f"  {p.name}{suffix}   —   {p.description}")
        if filtered:
            idx = min(index.get(), len(filtered) - 1)
            index.set(idx)
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(idx)
            listbox.see(idx)

    def commit(_event=None) -> None:
        if not filtered:
            return
        selection["value"] = filtered[index.get()]
        root.destroy()

    def cancel(_event=None) -> None:
        selection["value"] = None
        root.destroy()

    def move(delta: int) -> None:
        if not filtered:
            return
        index.set((index.get() + delta) % len(filtered))
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(index.get())
        listbox.see(index.get())

    query.trace_add("write", lambda *_: redraw())
    root.bind("<Return>", commit)
    root.bind("<Escape>", cancel)
    root.bind("<Up>", lambda _e: move(-1))
    root.bind("<Down>", lambda _e: move(1))
    listbox.bind("<Double-Button-1>", commit)

    redraw()
    root.mainloop()

    chosen = selection["value"]
    if chosen and on_pick:
        on_pick(chosen)
    return chosen
