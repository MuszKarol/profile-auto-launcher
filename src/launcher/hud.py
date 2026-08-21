"""HUD-style profile picker built on Tkinter (stdlib, always available).

Keyboard-first: type to filter (fuzzy), ↑/↓ to move, → to preview the steps,
Enter to run, Ctrl+E to edit, Ctrl+K to stop a running profile, Esc to close.
After picking, the HUD streams per-step results live, so you see exactly which
steps succeeded — then auto-closes on success.

The list also carries `Action` entries — "Settings" opens the configuration
panel. They are filtered and picked exactly like profiles, so the launcher is
the single entry point to everything rather than one of two.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field

from launcher import theme
from launcher.config import Profile
from launcher.executor import (
    StepResult,
    describe_step,
    format_result,
    run_profile,
    stop_profile,
)
from launcher.state import load_state


@dataclass
class Action:
    """A non-profile row in the HUD. Duck-types the attributes rows read."""

    name: str
    description: str
    run: Callable[[], None]
    icon: str = "⚙"
    hint: str = ""
    tags: list[str] = field(default_factory=list)
    steps: tuple = ()
    default: bool = False
    hotkey: str = ""


def default_actions() -> list[Action]:
    """The actions every HUD offers. Kept a function so tests can substitute."""

    def open_settings() -> None:
        from launcher.panel import open_panel

        open_panel()

    return [
        Action(
            name="Settings",
            description="Configure the launcher, profiles, secrets and sync",
            run=open_settings,
            tags=["settings", "config", "preferences", "options"],
            hint="open panel",
        )
    ]


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


def _match(q: str, p: Profile | Action) -> int | None:
    """Best score across a row's name, description and tags — or None."""
    scores = [
        _fuzzy_score(q, p.name),
        _fuzzy_score(q, p.description),
        *(_fuzzy_score(q, t) for t in p.tags),
    ]
    hits = [s for s in scores if s is not None]
    return max(hits) if hits else None


def _active_names() -> set[str]:
    from launcher import procs

    try:
        return set(procs.active_profiles())
    except Exception:
        return set()


class _Hud:
    def __init__(
        self,
        profiles: list[Profile],
        execute: bool,
        actions: list[Action] | None = None,
    ) -> None:
        from launcher import settings

        self.conf = settings.load()
        self.pal = theme.palette()
        self.font = theme.font_family()
        self.mono = theme.mono_family()

        self.profiles = profiles
        self.actions = list(actions if actions is not None else default_actions())
        self.execute = execute
        self.filtered: list[Profile | Action] = [*profiles, *self.actions]
        self.index = 0
        self.rows: list[tk.Frame] = []
        self.chosen: Profile | None = None
        self.exit_code = 0
        self.failed_count = 0
        self.running = False
        self.preview_open = False
        state = load_state()
        self.last_profile: str | None = state.get("last_profile")
        self.run_counts: dict[str, int] = state.get("run_counts", {})
        self.active: set[str] = _active_names()
        # Cross-thread close request (e.g. the global hotkey toggling the HUD
        # from the pynput listener thread — Tk itself is not thread-safe, so
        # the flag is polled from inside the Tk loop instead).
        self._close_event = threading.Event()
        self.results_q: queue.Queue[StepResult | None] = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Profile Auto Launcher")
        self.root.configure(bg=self.pal.border)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.root.overrideredirect(True)

        self.base_width = max(420, self.conf.hud_width)
        self.height = max(300, self.conf.hud_height)
        self._resize(self.base_width)

        self.outer = tk.Frame(self.root, bg=self.pal.bg, padx=18, pady=16)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_picker()
        self.root.after(100, self._watch_close)
        self.root.deiconify()

    def _resize(self, width: int) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(width, screen_w - 40)
        self.root.geometry(
            f"{width}x{self.height}+{(screen_w - width) // 2}+{(screen_h - self.height) // 3}"
        )

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
        pal = self.pal
        header = tk.Frame(self.outer, bg=pal.bg)
        header.pack(fill="x")
        tk.Label(header, text="◈", bg=pal.bg, fg=pal.accent, font=(self.font, 14, "bold")).pack(
            side="left", padx=(0, 8)
        )

        self.query = tk.StringVar()
        self.entry = tk.Entry(
            header,
            textvariable=self.query,
            bg=pal.bg,
            fg=pal.fg,
            insertbackground=pal.accent,
            relief="flat",
            font=(self.font, 15),
            highlightthickness=0,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.focus_set()

        self.placeholder = tk.Label(
            header, text="Search profiles…", bg=pal.bg, fg=pal.muted, font=(self.font, 15)
        )
        self.placeholder.place(in_=self.entry, x=2, y=6)
        self.placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())

        tk.Frame(self.outer, bg=pal.border, height=1).pack(fill="x", pady=(10, 12))

        self.body = tk.Frame(self.outer, bg=pal.bg)
        self.body.pack(fill="both", expand=True)

        self.list_frame = tk.Frame(self.body, bg=pal.bg)
        self.list_frame.pack(side="left", fill="both", expand=True)

        self.preview_frame = tk.Frame(self.body, bg=pal.panel, padx=12, pady=10)

        self.footer = tk.Label(
            self.outer,
            text=(
                "↑↓ navigate    ⏎ run    → preview    ctrl+e edit    "
                "ctrl+k stop    ctrl+, settings    esc close"
            ),
            bg=pal.bg,
            fg=pal.muted,
            font=(self.font, 9),
        )
        self.footer.pack(anchor="w", pady=(10, 0))

        self.query.trace_add("write", lambda *_: self._redraw())
        self.root.bind("<Return>", self._commit)
        self.root.bind("<Escape>", self._cancel)
        self.root.bind("<Up>", lambda _e: self._move(-1))
        self.root.bind("<Down>", lambda _e: self._move(1))
        self.root.bind("<Right>", lambda _e: self._set_preview(True))
        self.root.bind("<Left>", lambda _e: self._set_preview(False))
        self.root.bind("<Tab>", lambda _e: self._set_preview(not self.preview_open))
        self.root.bind("<Control-e>", self._edit_selected)
        self.root.bind("<Control-k>", self._stop_selected)
        self.root.bind("<Control-comma>", self._open_settings)
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
        pal = self.pal
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
        # Actions sit below the profiles unless the query names one directly,
        # so typing a profile name never puts "Settings" under the cursor.
        actions = [(a, _match(q, a)) for a in self.actions]
        matched = [a for a, score in actions if score is not None]
        exact = [a for a, score in actions if score is not None and score >= 80]
        self.filtered = [*exact, *[p for _, p in scored], *[a for a in matched if a not in exact]]
        self.index = min(self.index, max(len(self.filtered) - 1, 0))

        for row in self.rows:
            row.destroy()
        self.rows.clear()

        if not self.filtered:
            empty = tk.Frame(self.list_frame, bg=pal.bg)
            tk.Label(
                empty,
                text="No matching profiles",
                bg=pal.bg,
                fg=pal.muted,
                font=(self.font, 11),
            ).pack(pady=24)
            empty.pack(fill="x")
            self.rows.append(empty)
            return

        for i, p in enumerate(self.filtered):
            self.rows.append(self._make_row(i, p))
        self._highlight()

    def _make_row(self, i: int, p: Profile) -> tk.Frame:
        pal = self.pal
        row = tk.Frame(self.list_frame, bg=pal.panel, padx=12, pady=8)
        row.pack(fill="x", pady=3)

        bar = tk.Frame(row, bg=pal.panel, width=3)
        bar.pack(side="left", fill="y", padx=(0, 10))
        row.accent_bar = bar  # type: ignore[attr-defined]

        icon = p.icon or "▣"
        tk.Label(row, text=icon, bg=pal.panel, fg=pal.accent, font=(self.font, 13)).pack(
            side="left", padx=(0, 10)
        )

        text = tk.Frame(row, bg=pal.panel)
        text.pack(side="left", fill="x", expand=True)
        title = p.name + ("  ★" if p.default else "")
        tk.Label(
            text, text=title, bg=pal.panel, fg=pal.fg, font=(self.font, 12, "bold"), anchor="w"
        ).pack(fill="x")
        if p.description:
            tk.Label(
                text,
                text=p.description,
                bg=pal.panel,
                fg=pal.muted,
                font=(self.font, 9),
                anchor="w",
            ).pack(fill="x")

        tail = p.hint if isinstance(p, Action) else f"{len(p.steps)} steps"
        tk.Label(row, text=tail, bg=pal.panel, fg=pal.muted, font=(self.font, 9)).pack(side="right")
        if isinstance(p, Action):
            badge = None
        elif p.name in self.active:
            badge = ("● running", pal.ok)
        elif p.name == self.last_profile:
            badge = ("↺ recent", pal.accent)
        else:
            badge = None
        if badge is not None:
            tk.Label(row, text=badge[0], bg=pal.panel, fg=badge[1], font=(self.font, 9)).pack(
                side="right", padx=(0, 10)
            )

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
                row.set_bg(self.pal.panel_selected if selected else self.pal.panel)
                row.accent_bar.configure(bg=self.pal.accent if selected else self.pal.panel)
        if self.preview_open:
            self._render_preview()

    def _move(self, delta: int) -> None:
        if not self.filtered:
            return
        self.index = (self.index + delta) % len(self.filtered)
        self._highlight()

    # ── step preview ─────────────────────────────────────────────────────
    def _set_preview(self, visible: bool) -> str:
        if self.running or visible == self.preview_open:
            return "break"
        self.preview_open = visible
        if visible:
            self._resize(int(self.base_width * 1.55))
            self.preview_frame.pack(side="right", fill="both", padx=(12, 0))
            self._render_preview()
        else:
            self.preview_frame.pack_forget()
            self._resize(self.base_width)
        return "break"  # keep Tab from moving focus out of the entry

    def _render_preview(self) -> None:
        pal = self.pal
        for child in self.preview_frame.winfo_children():
            child.destroy()
        if not self.filtered:
            return
        profile = self.filtered[self.index]
        if isinstance(profile, Action):
            tk.Label(
                self.preview_frame,
                text=profile.description,
                bg=pal.panel,
                fg=pal.muted,
                font=(self.font, 10),
                wraplength=240,
                justify="left",
            ).pack(fill="x")
            return
        tk.Label(
            self.preview_frame,
            text=profile.name,
            bg=pal.panel,
            fg=pal.fg,
            font=(self.font, 11, "bold"),
            anchor="w",
        ).pack(fill="x")
        if profile.tags:
            tk.Label(
                self.preview_frame,
                text=" · ".join(profile.tags),
                bg=pal.panel,
                fg=pal.muted,
                font=(self.font, 9),
                anchor="w",
            ).pack(fill="x", pady=(0, 6))

        shown = profile.steps[:14]
        for step in shown:
            line = tk.Frame(self.preview_frame, bg=pal.panel)
            line.pack(fill="x", pady=1)
            glyph = "○" if not step.enabled else ("⇉" if step.parallel else "→")
            tk.Label(
                line,
                text=glyph,
                bg=pal.panel,
                fg=pal.muted if not step.enabled else pal.accent,
                font=(self.font, 9),
                width=2,
            ).pack(side="left")
            tk.Label(
                line,
                text=describe_step(step)[:64],
                bg=pal.panel,
                fg=pal.muted if not step.enabled else pal.fg,
                font=(self.mono, 8),
                anchor="w",
                justify="left",
            ).pack(side="left", fill="x", expand=True)
        if len(profile.steps) > len(shown):
            tk.Label(
                self.preview_frame,
                text=f"… +{len(profile.steps) - len(shown)} more",
                bg=pal.panel,
                fg=pal.muted,
                font=(self.font, 9),
                anchor="w",
            ).pack(fill="x", pady=(4, 0))

    # ── secondary actions ────────────────────────────────────────────────
    def _edit_selected(self, _event=None) -> str:
        if self.running or not self.filtered:
            return "break"
        profile = self.filtered[self.index]
        if isinstance(profile, Action):
            return "break"
        self.chosen = None
        self.root.destroy()
        from launcher.editor import edit_profile

        edit_profile(profile)
        return "break"

    def _stop_selected(self, _event=None) -> str:
        if self.running or not self.filtered:
            return "break"
        profile = self.filtered[self.index]
        if isinstance(profile, Action):
            return "break"
        self.running = True
        self._build_progress(profile, verb="Stopping")

        def worker() -> None:
            try:
                results, stopped, stubborn = stop_profile(profile, self.results_q.put)
                self.results_q.put(
                    _synthetic(profile, f"{stopped} process(es) closed, {stubborn} left")
                )
            finally:
                self.results_q.put(None)

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(60, self._poll_results)
        return "break"

    def _open_settings(self, _event=None) -> str:
        """Ctrl+, — the same thing the Settings row does, without searching."""
        if self.running:
            return "break"
        action = next((a for a in self.actions if a.name == "Settings"), None)
        if action is None:
            return "break"
        self.chosen = None
        self.root.destroy()
        action.run()
        return "break"

    def _cancel(self, _event=None) -> None:
        self.chosen = None
        self.root.destroy()

    # ── execution view ───────────────────────────────────────────────────
    def _commit(self, _event=None) -> None:
        if self.running or not self.filtered:
            return
        selected = self.filtered[self.index]
        if isinstance(selected, Action):
            self.chosen = None
            self.root.destroy()
            selected.run()
            return
        self.chosen = selected
        if not self.execute:
            self.root.destroy()
            return
        self.running = True
        self._build_progress(self.chosen)
        threading.Thread(target=self._worker, args=(self.chosen,), daemon=True).start()
        self.root.after(60, self._poll_results)

    def _build_progress(self, profile: Profile, verb: str = "Running") -> None:
        pal = self.pal
        if self.preview_open:
            self.preview_frame.pack_forget()
            self.preview_open = False
            self._resize(self.base_width)
        for child in self.outer.winfo_children():
            child.destroy()
        self.rows.clear()

        header = tk.Frame(self.outer, bg=pal.bg)
        header.pack(fill="x")
        tk.Label(
            header, text=profile.icon or "◈", bg=pal.bg, fg=pal.accent, font=(self.font, 14)
        ).pack(side="left", padx=(0, 8))
        tk.Label(
            header,
            text=f"{verb} {profile.name}…",
            bg=pal.bg,
            fg=pal.fg,
            font=(self.font, 14, "bold"),
        ).pack(side="left")

        tk.Frame(self.outer, bg=pal.border, height=1).pack(fill="x", pady=(10, 12))

        self.progress_frame = tk.Frame(self.outer, bg=pal.bg)
        self.progress_frame.pack(fill="both", expand=True)

        self.status = tk.Label(
            self.outer,
            text="esc hide window (steps keep running)",
            bg=pal.bg,
            fg=pal.muted,
            font=(self.font, 9),
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
        pal = self.pal
        if res.counts_as_failure:
            self.failed_count += 1
        row = tk.Frame(self.progress_frame, bg=pal.bg)
        row.pack(fill="x", pady=2)
        if res.skipped:
            glyph, color = "○", pal.muted
        elif res.ok:
            glyph, color = "✓", pal.ok
        else:
            glyph, color = "✗", pal.err
        tk.Label(row, text=glyph, bg=pal.bg, fg=color, font=(self.font, 11, "bold"), width=2).pack(
            side="left"
        )
        tk.Label(
            row, text=res.step.label, bg=pal.bg, fg=pal.fg, font=(self.font, 10), anchor="w"
        ).pack(side="left")
        detail_fg = pal.err if (not res.ok and not res.step.optional) else pal.muted
        tk.Label(
            row, text=res.detail[:90], bg=pal.bg, fg=detail_fg, font=(self.font, 9), anchor="e"
        ).pack(side="right")

    def _finish(self) -> None:
        failed = self.failed_count
        if failed == 0:
            self.status.configure(text="✓ all steps finished — closing…", fg=self.pal.ok)
            self.exit_code = 0
            self.root.after(1400, self.root.destroy)
        else:
            self.status.configure(text=f"✗ {failed} step(s) failed — esc to close", fg=self.pal.err)
            self.exit_code = 1

    def run(self) -> None:
        self.root.mainloop()


def _synthetic(profile: Profile, detail: str) -> StepResult:
    """A result row that reports on the run itself rather than on a step."""
    from launcher.config import Step

    return StepResult(Step(type="kill", name=f"stop {profile.name}"), True, detail)


_active_lock = threading.Lock()
_active_hud: _Hud | None = None


def pick_and_run(profiles: list[Profile]) -> int:
    """Open the HUD; on Enter, execute the profile with live step feedback.

    Opens even with no profiles at all — the Settings row is how a first-time
    user gets to the panel that creates one.

    Returns a process exit code (0 = success / cancelled, 1 = failed steps).
    """
    global _active_hud
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
