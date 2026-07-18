"""HUD-style profile picker built on Tkinter (stdlib, always available).

Keyboard-first: type to filter (fuzzy), ↑/↓ to move, Enter to run, Esc to
cancel. After picking, the HUD stays open and streams per-step results live,
so you see exactly which steps succeeded — then auto-closes on success.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Callable

from launcher.config import Profile
from launcher.executor import StepResult, format_result, run_profile
from launcher.state import load_state

# ── palette ──────────────────────────────────────────────────────────────
BG = "#0e1117"          # window background
PANEL = "#161b26"       # row background
PANEL_HOVER = "#1b2231"
PANEL_SELECTED = "#20293d"
FG = "#e6e9ef"
MUTED = "#7b8496"
ACCENT = "#7aa2f7"
BORDER = "#2a3247"
OK = "#7ee2a8"
ERR = "#f38ba8"

FONT = "Segoe UI"


def _fuzzy_score(q: str, text: str) -> int | None:
    """Score a match: prefix > word-start > substring > subsequence > miss."""
    text = text.lower()
    if not q:
        return 0
    if text.startswith(q):
        return 100
    if any(word.startswith(q) for word in text.split()):
        return 80
    if q in text:
        return 60
    it = iter(text)
    if all(ch in it for ch in q):
        return 30
    return None


def _match(q: str, p: Profile) -> int | None:
    scores = [
        _fuzzy_score(q, p.name),
        _fuzzy_score(q, p.description),
        *(_fuzzy_score(q, t) for t in p.tags),
    ]
    hits = [s for s in scores if s is not None]
    return max(hits) if hits else None


class _Hud:
    def __init__(self, profiles: list[Profile], execute: bool) -> None:
        self.profiles = profiles
        self.execute = execute
        self.filtered: list[Profile] = list(profiles)
        self.index = 0
        self.rows: list[tk.Frame] = []
        self.chosen: Profile | None = None
        self.exit_code = 0
        self.failed_count = 0
        self.running = False
        state = load_state()
        self.last_profile: str | None = state.get("last_profile")
        self.run_counts: dict[str, int] = state.get("run_counts", {})
        # Cross-thread close request (e.g. the global hotkey toggling the HUD
        # from the pynput listener thread — Tk itself is not thread-safe, so
        # the flag is polled from inside the Tk loop instead).
        self._close_event = threading.Event()
        self.results_q: "queue.Queue[StepResult | None]" = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Profile Auto Launcher")
        self.root.configure(bg=BORDER)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.root.overrideredirect(True)

        w, h = 620, 420
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")

        # 1px accent-ish border via outer frame
        self.outer = tk.Frame(self.root, bg=BG, padx=18, pady=16)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_picker()
        self.root.after(100, self._watch_close)
        self.root.deiconify()

    def request_close(self) -> None:
        """Thread-safe close request; honoured within ~100 ms."""
        self._close_event.set()

    def _watch_close(self) -> None:
        if self._close_event.is_set():
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            return
        if self.root.winfo_exists():
            self.root.after(100, self._watch_close)

    # ── picker view ──────────────────────────────────────────────────────
    def _build_picker(self) -> None:
        header = tk.Frame(self.outer, bg=BG)
        header.pack(fill="x")
        tk.Label(
            header, text="◈", bg=BG, fg=ACCENT, font=(FONT, 14, "bold")
        ).pack(side="left", padx=(0, 8))

        self.query = tk.StringVar()
        self.entry = tk.Entry(
            header, textvariable=self.query, bg=BG, fg=FG, insertbackground=ACCENT,
            relief="flat", font=(FONT, 15), highlightthickness=0,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.focus_set()

        self.placeholder = tk.Label(
            header, text="Search profiles…", bg=BG, fg=MUTED, font=(FONT, 15)
        )
        self.placeholder.place(in_=self.entry, x=2, y=6)
        self.placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())

        sep = tk.Frame(self.outer, bg=BORDER, height=1)
        sep.pack(fill="x", pady=(10, 12))

        self.list_frame = tk.Frame(self.outer, bg=BG)
        self.list_frame.pack(fill="both", expand=True)

        self.footer = tk.Label(
            self.outer,
            text="↑↓ navigate    ⏎ run    esc close",
            bg=BG, fg=MUTED, font=(FONT, 9),
        )
        self.footer.pack(anchor="w", pady=(10, 0))

        self.query.trace_add("write", lambda *_: self._redraw())
        self.root.bind("<Return>", self._commit)
        self.root.bind("<Escape>", self._cancel)
        self.root.bind("<Up>", lambda _e: self._move(-1))
        self.root.bind("<Down>", lambda _e: self._move(1))
        self.root.bind("<FocusOut>", self._maybe_close_on_blur)
        self._redraw()

    def _maybe_close_on_blur(self, _event: tk.Event) -> None:
        # close when the whole app loses focus (Spotlight-like behaviour),
        # but never mid-run — the user wants to watch the progress view
        if self.running:
            return
        self.root.after(120, self._close_if_unfocused)

    def _close_if_unfocused(self) -> None:
        if not self.running and self.root.winfo_exists() and self.root.focus_get() is None:
            self._cancel()

    def _redraw(self) -> None:
        q = self.query.get().lower().strip()
        if self.query.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(in_=self.entry, x=2, y=6)

        scored = []
        for p in self.profiles:
            s = _match(q, p)
            if s is not None:
                scored.append((s, p))
        scored.sort(
            key=lambda sp: (
                -sp[0],
                sp[1].name != self.last_profile,  # last-run profile floats up
                -self.run_counts.get(sp[1].name, 0),
                sp[1].name.lower(),
            )
        )
        self.filtered = [p for _, p in scored]
        self.index = min(self.index, max(len(self.filtered) - 1, 0))

        for row in self.rows:
            row.destroy()
        self.rows.clear()

        if not self.filtered:
            empty = tk.Frame(self.list_frame, bg=BG)
            tk.Label(
                empty, text="No matching profiles", bg=BG, fg=MUTED, font=(FONT, 11)
            ).pack(pady=24)
            empty.pack(fill="x")
            self.rows.append(empty)
            return

        for i, p in enumerate(self.filtered):
            self.rows.append(self._make_row(i, p))
        self._highlight()

    def _make_row(self, i: int, p: Profile) -> tk.Frame:
        row = tk.Frame(self.list_frame, bg=PANEL, padx=12, pady=8)
        row.pack(fill="x", pady=3)

        bar = tk.Frame(row, bg=PANEL, width=3)
        bar.pack(side="left", fill="y", padx=(0, 10))
        row.accent_bar = bar  # type: ignore[attr-defined]

        icon = p.icon or "▣"
        tk.Label(row, text=icon, bg=PANEL, fg=ACCENT, font=(FONT, 13)).pack(side="left", padx=(0, 10))

        text = tk.Frame(row, bg=PANEL)
        text.pack(side="left", fill="x", expand=True)
        title = p.name + ("  ★" if p.default else "")
        tk.Label(text, text=title, bg=PANEL, fg=FG, font=(FONT, 12, "bold"), anchor="w").pack(fill="x")
        if p.description:
            tk.Label(text, text=p.description, bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")

        tk.Label(row, text=f"{len(p.steps)} steps", bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(side="right")
        if p.name == self.last_profile:
            tk.Label(row, text="↺ recent", bg=PANEL, fg=ACCENT, font=(FONT, 9)).pack(side="right", padx=(0, 10))

        def set_bg(color: str) -> None:
            for widget in (row, text, *row.winfo_children(), *text.winfo_children()):
                if widget is not bar:
                    widget.configure(bg=color)

        row.set_bg = set_bg  # type: ignore[attr-defined]

        def on_enter(_e: tk.Event, idx: int = i) -> None:
            self.index = idx
            self._highlight()

        for widget in (row, text, *row.winfo_children(), *text.winfo_children()):
            widget.bind("<Motion>", on_enter)
            widget.bind("<Button-1>", self._commit)
        return row

    def _highlight(self) -> None:
        for i, row in enumerate(self.rows):
            selected = i == self.index
            if hasattr(row, "set_bg"):
                row.set_bg(PANEL_SELECTED if selected else PANEL)
                row.accent_bar.configure(bg=ACCENT if selected else PANEL)

    def _move(self, delta: int) -> None:
        if not self.filtered:
            return
        self.index = (self.index + delta) % len(self.filtered)
        self._highlight()

    def _cancel(self, _event=None) -> None:
        self.chosen = None
        self.root.destroy()

    # ── execution view ───────────────────────────────────────────────────
    def _commit(self, _event=None) -> None:
        if self.running or not self.filtered:
            return
        self.chosen = self.filtered[self.index]
        if not self.execute:
            self.root.destroy()
            return
        self.running = True
        self._build_progress(self.chosen)
        threading.Thread(target=self._worker, args=(self.chosen,), daemon=True).start()
        self.root.after(60, self._poll_results)

    def _build_progress(self, profile: Profile) -> None:
        for child in self.outer.winfo_children():
            child.destroy()
        self.rows.clear()

        header = tk.Frame(self.outer, bg=BG)
        header.pack(fill="x")
        tk.Label(
            header, text=profile.icon or "◈", bg=BG, fg=ACCENT, font=(FONT, 14)
        ).pack(side="left", padx=(0, 8))
        tk.Label(
            header, text=f"Running {profile.name}…", bg=BG, fg=FG, font=(FONT, 14, "bold")
        ).pack(side="left")

        tk.Frame(self.outer, bg=BORDER, height=1).pack(fill="x", pady=(10, 12))

        self.progress_frame = tk.Frame(self.outer, bg=BG)
        self.progress_frame.pack(fill="both", expand=True)

        self.status = tk.Label(
            self.outer, text="esc hide window (steps keep running)",
            bg=BG, fg=MUTED, font=(FONT, 9),
        )
        self.status.pack(anchor="w", pady=(10, 0))
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.bind("<Return>", lambda _e: None)

    def _worker(self, profile: Profile) -> None:
        try:
            run_profile(profile, on_result=self.results_q.put)
        finally:
            self.results_q.put(None)  # sentinel: done

    def _poll_results(self) -> None:
        if not self.root.winfo_exists():
            return
        done = False
        while True:
            try:
                res = self.results_q.get_nowait()
            except queue.Empty:
                break
            if res is None:
                done = True
                break
            self._add_result_row(res)
            print(format_result(res))
        if done:
            self._finish()
        else:
            self.root.after(60, self._poll_results)

    def _add_result_row(self, res: StepResult) -> None:
        if res.counts_as_failure:
            self.failed_count += 1
        row = tk.Frame(self.progress_frame, bg=BG)
        row.pack(fill="x", pady=2)
        if res.skipped:
            glyph, color = "○", MUTED
        elif res.ok:
            glyph, color = "✓", OK
        else:
            glyph, color = "✗", ERR
        tk.Label(row, text=glyph, bg=BG, fg=color, font=(FONT, 11, "bold"), width=2).pack(side="left")
        label = res.step.name or res.step.type
        tk.Label(row, text=label, bg=BG, fg=FG, font=(FONT, 10), anchor="w").pack(side="left")
        detail_fg = ERR if (not res.ok and not res.step.optional) else MUTED
        tk.Label(
            row, text=res.detail, bg=BG, fg=detail_fg, font=(FONT, 9), anchor="e"
        ).pack(side="right")

    def _finish(self) -> None:
        failed = self.failed_count
        if failed == 0:
            self.status.configure(text="✓ all steps finished — closing…", fg=OK)
            self.exit_code = 0
            self.root.after(1400, self.root.destroy)
        else:
            self.status.configure(
                text=f"✗ {failed} step(s) failed — esc to close", fg=ERR
            )
            self.exit_code = 1

    def run(self) -> None:
        self.root.mainloop()


_active_lock = threading.Lock()
_active_hud: _Hud | None = None


def pick_and_run(profiles: list[Profile]) -> int:
    """Open the HUD; on Enter, execute the profile with live step feedback.

    Returns a process exit code (0 = success / cancelled, 1 = failed steps).
    """
    global _active_hud
    if not profiles:
        return 1
    hud = _Hud(profiles, execute=True)
    with _active_lock:
        _active_hud = hud
    try:
        hud.run()
    finally:
        with _active_lock:
            _active_hud = None
    return hud.exit_code


def toggle_pick_and_run(profiles: list[Profile]) -> int:
    """Hotkey entry point: open the HUD, or close it if it's already open."""
    with _active_lock:
        hud = _active_hud
    if hud is not None:
        hud.request_close()
        return 0
    return pick_and_run(profiles)


def pick_profile(
    profiles: list[Profile], on_pick: Callable[[Profile], None] | None = None
) -> Profile | None:
    """Pick a profile without executing it (legacy API)."""
    if not profiles:
        return None
    hud = _Hud(profiles, execute=False)
    hud.run()
    if hud.chosen and on_pick:
        on_pick(hud.chosen)
    return hud.chosen
