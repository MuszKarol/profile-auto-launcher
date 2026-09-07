"""The launcher window: one search box over profiles, apps and actions.

Keyboard-first, Spotlight-shaped. Type to filter, ↑/↓ to move, → to preview
what a profile will do, Enter to run it — and then watch each step report as
it finishes. Typing the name of an installed application launches just that
one program, which is the fast path a whole profile is too much for.

Everything the launcher can reach is a row: profiles, applications, and
actions such as opening the manager window. One list, one ranking, one Enter.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from launcher import fuzzy, theme, ui
from launcher.apps import App
from launcher.config import Profile
from launcher.executor import (
    StepResult,
    describe_step,
    format_result,
    run_profile,
    stop_profile,
)
from launcher.state import load_state
from launcher.theme import SIZE_BODY, SIZE_DISPLAY, SIZE_SMALL, SIZE_TINY
from launcher.ui import Kit

MAX_APP_RESULTS = 6

# Measured row geometry, used to size the window to its content. Tk packs
# children past the edge of a frame rather than scrolling them, so the window
# has to be told how tall its list actually is.
ROW_HEIGHT = 61
GROUP_HEIGHT = 25
CHROME_HEIGHT = 148


@dataclass
class Action:
    """A non-profile row in the HUD. Duck-types the attributes rows read."""

    name: str
    description: str
    run: Callable[[], None]
    hint: str = ""
    tags: list[str] = field(default_factory=list)
    steps: tuple = ()
    default: bool = False
    hotkey: str = ""


@dataclass
class Row:
    """One line in the list, whatever it stands for."""

    kind: str  # profile | app | action
    name: str
    description: str
    tail: str
    payload: Any
    badge: tuple[str, str] | None = None


def default_actions(hud: _Hud | None = None) -> list[Action]:
    """The actions every HUD offers. Kept a function so tests can substitute.

    Given the launcher it belongs to, the manager opens as a child of it —
    one Tk root per process, however many windows the user walks through.
    """

    def open_manager() -> None:
        from launcher.panel import open_panel

        open_panel(parent=hud.root if hud is not None else None)

    return [
        Action(
            name="Settings",
            description="Open the manager: profiles, apps, sync, settings",
            run=open_manager,
            tags=["settings", "config", "preferences", "options", "manager"],
            hint="open manager",
        )
    ]


def _match(query: str, entry: Profile | Action) -> int | None:
    """Best score across a row's name, description and tags — or None."""
    return fuzzy.best(query, [entry.name, entry.description, *entry.tags])


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
        parent: tk.Misc | None = None,
    ) -> None:
        from launcher import settings

        self.conf = settings.load()
        self.kit = Kit()
        self.pal = self.kit.pal
        self.font = self.kit.font
        self.mono = self.kit.mono

        self.profiles = profiles
        self.actions = list(actions if actions is not None else default_actions(self))
        self.execute = execute
        self.filtered: list[Row] = []
        self.index = 0
        self.rows: list[tk.Widget] = []
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

        self.parent = parent
        self.root = ui.new_window(parent)
        self.root.withdraw()
        self.kit.chrome(self.root, "Profile Auto Launcher")
        # The border is the window's only frame: overrideredirect removes the
        # title bar, so the ground colour has to draw the outline itself.
        self.root.configure(bg=self.pal.border)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.root.overrideredirect(True)

        self.base_width = max(480, self.conf.hud_width)
        self.max_height = max(320, self.conf.hud_height)
        self.height = self.max_height
        self.width = self.base_width
        self._resize(self.base_width)

        self.outer = tk.Frame(
            self.root, bg=self.pal.bg, padx=theme.METRICS.gap_lg, pady=theme.METRICS.pad
        )
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_picker()
        self._warm_app_index()
        self.root.after(100, self._watch_close)
        self.root.deiconify()

    # ── window ───────────────────────────────────────────────────────────
    def _resize(self, width: int, height: int | None = None) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.width = min(width, screen_w - 40)
        self.height = min(height or self.height, screen_h - 80)
        self.root.geometry(
            f"{self.width}x{self.height}"
            f"+{(screen_w - self.width) // 2}+{(screen_h - self.height) // 3}"
        )

    def _fit_to_content(self, groups: int) -> None:
        """Grow and shrink with the result list, the way a palette should."""
        if self.running:
            return
        needed = CHROME_HEIGHT + len(self.filtered) * ROW_HEIGHT + groups * GROUP_HEIGHT
        self._resize(self.width, min(self.max_height, max(220, needed)))

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

    def _warm_app_index(self) -> None:
        """Build the application index off the UI thread, so the first
        keystroke searches a warm cache instead of scanning `PATH`."""

        def warm() -> None:
            from launcher import apps

            try:
                apps.index()
            except Exception:  # an unusable index must not break the picker
                pass

        threading.Thread(target=warm, daemon=True, name="pal-app-index").start()

    # ── picker view ──────────────────────────────────────────────────────
    def _build_picker(self) -> None:
        kit, pal = self.kit, self.pal
        header = kit.frame(self.outer)
        header.pack(fill="x")
        kit.brand(header, 24).pack(side="left", padx=(0, theme.METRICS.gap))

        self.query = tk.StringVar()
        self.entry = tk.Entry(
            header,
            textvariable=self.query,
            bg=pal.bg,
            fg=pal.fg,
            insertbackground=pal.accent,
            relief="flat",
            font=kit.f(SIZE_DISPLAY),
            highlightthickness=0,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.focus_set()

        self.placeholder = kit.label(
            header, "Search profiles and apps…", fg=pal.faint, size=SIZE_DISPLAY
        )
        self.placeholder.place(in_=self.entry, x=2, y=4)
        self.placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())

        kit.divider(self.outer, pady=(theme.METRICS.gap, theme.METRICS.gap))

        self.body = kit.frame(self.outer)
        self.body.pack(fill="both", expand=True)

        self.list_frame = kit.frame(self.body)
        self.list_frame.pack(side="left", fill="both", expand=True)

        self.preview_frame = tk.Frame(
            self.body, bg=pal.panel, padx=theme.METRICS.gap, pady=theme.METRICS.gap
        )

        self.footer = kit.label(
            self.outer,
            "↑↓ move     ⏎ run     → preview     ^E edit     ^K stop     ^, manager     esc close",
            fg=pal.faint,
            size=SIZE_TINY,
        )
        self.footer.pack(anchor="w", pady=(theme.METRICS.gap, 0))

        self.query.trace_add("write", lambda *_: self._redraw())
        for sequence, handler in (
            ("<Return>", self._commit),
            ("<Escape>", self._cancel),
            ("<Control-e>", self._edit_selected),
            ("<Control-k>", self._stop_selected),
            ("<Control-comma>", self._open_settings),
        ):
            self.root.bind(sequence, handler)
        self.root.bind("<Up>", lambda _e: self._move(-1))
        self.root.bind("<Down>", lambda _e: self._move(1))
        self.root.bind("<Right>", lambda _e: self._set_preview(True))
        self.root.bind("<Left>", lambda _e: self._set_preview(False))
        self.root.bind("<Tab>", lambda _e: self._set_preview(not self.preview_open))
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

    # ── result set ───────────────────────────────────────────────────────
    def _profile_rows(self, query: str) -> list[Row]:
        scored = []
        for profile in self.profiles:
            rank = _match(query, profile)
            if rank is not None:
                scored.append((rank, profile))
        scored.sort(
            key=lambda pair: (
                -pair[0],
                pair[1].name != self.last_profile,  # last-run profile floats up
                -self.run_counts.get(pair[1].name, 0),
                pair[1].name.lower(),
            )
        )
        rows = []
        for _rank, profile in scored:
            if profile.name in self.active:
                badge = ("running", self.pal.fg)
            elif profile.name == self.last_profile:
                badge = ("recent", self.pal.muted)
            elif profile.default:
                badge = ("default", self.pal.faint)
            else:
                badge = None
            rows.append(
                Row(
                    kind="profile",
                    name=profile.name,
                    description=profile.description,
                    tail=f"{len(profile.steps)} steps",
                    payload=profile,
                    badge=badge,
                )
            )
        return rows

    def _app_rows(self, query: str, taken: set[str]) -> list[Row]:
        """Installed applications matching the query — the single-app path.

        Only from two characters: one letter matches most of `PATH`, and the
        profiles are what the launcher is for.
        """
        if len(query) < 2:
            return []
        from launcher import apps

        try:
            found = apps.search(query, limit=MAX_APP_RESULTS)
        except Exception:
            return []
        return [
            Row(
                kind="app",
                name=app.name,
                description=apps.describe(app),
                tail="launch",
                payload=app,
            )
            for app in found
            if app.name.lower() not in taken
        ]

    def _row_budget(self) -> int:
        """How many rows the tallest allowed window can show."""
        room = self.max_height - CHROME_HEIGHT - 3 * GROUP_HEIGHT
        return max(3, room // ROW_HEIGHT)

    def _redraw(self) -> None:
        query = self.query.get().lower().strip()
        if self.query.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(in_=self.entry, x=2, y=4)

        profile_rows = self._profile_rows(query)
        # Actions sit below the profiles unless the query names one directly,
        # so typing a profile name never puts "Settings" under the cursor.
        scored_actions = [(action, _match(query, action)) for action in self.actions]
        exact = [a for a, rank in scored_actions if rank is not None and rank >= fuzzy.WORD_START]
        rest = [a for a, rank in scored_actions if rank is not None and a not in exact]
        action_rows = [Row("action", a.name, a.description, a.hint, a) for a in [*exact, *rest]]
        exact_rows, weak_rows = action_rows[: len(exact)], action_rows[len(exact) :]
        taken = {row.name.lower() for row in profile_rows}
        # Order of intent: an action the query named, the profiles, a single
        # app, and only then an action the query merely brushed against.
        self.filtered = [
            *exact_rows,
            *profile_rows,
            *self._app_rows(query, taken),
            *weak_rows,
        ][: self._row_budget()]
        self.index = min(self.index, max(len(self.filtered) - 1, 0))

        for widget in self.rows:
            widget.destroy()
        self.rows.clear()

        if not self.filtered:
            empty = self.kit.frame(self.list_frame)
            self.kit.label(
                empty, f"Nothing matches “{query}”", fg=self.pal.muted, size=SIZE_BODY
            ).pack(pady=(28, 4))
            self.kit.label(
                empty,
                "Try part of a profile name, or an installed app",
                fg=self.pal.faint,
                size=SIZE_SMALL,
            ).pack()
            empty.pack(fill="x")
            self.rows.append(empty)
            self._fit_to_content(groups=0)
            return

        previous_kind = ""
        kinds = {row.kind for row in self.filtered}
        for position, row in enumerate(self.filtered):
            if len(kinds) > 1 and row.kind != previous_kind:
                header = self.kit.label(
                    self.list_frame,
                    {"profile": "profiles", "app": "applications", "action": "launcher"}[row.kind],
                    fg=self.pal.faint,
                    size=SIZE_TINY,
                    bold=True,
                    anchor="w",
                )
                header.pack(fill="x", pady=(theme.METRICS.gap_sm, 2))
                self.rows.append(header)
                previous_kind = row.kind
            self.rows.append(self._make_row(position, row))
        self._fit_to_content(groups=len(kinds) if len(kinds) > 1 else 0)
        self._highlight()

    def _make_row(self, position: int, entry: Row) -> tk.Frame:
        pal, kit = self.pal, self.kit
        row = tk.Frame(
            self.list_frame, bg=pal.panel, padx=theme.METRICS.gap, pady=theme.METRICS.gap_sm
        )
        row.pack(fill="x", pady=2)

        bar = tk.Frame(row, bg=pal.panel, width=3)
        bar.pack(side="left", fill="y", padx=(0, theme.METRICS.gap_sm))
        row.accent_bar = bar  # type: ignore[attr-defined]

        text = tk.Frame(row, bg=pal.panel)
        text.pack(side="left", fill="x", expand=True)
        kit.label(text, entry.name, bg=pal.panel, size=SIZE_BODY, bold=True, anchor="w").pack(
            fill="x"
        )
        if entry.description:
            kit.label(
                text,
                entry.description[:70],
                bg=pal.panel,
                fg=pal.muted,
                size=SIZE_TINY,
                anchor="w",
            ).pack(fill="x")

        if entry.tail:
            kit.label(row, entry.tail, bg=pal.panel, fg=pal.faint, size=SIZE_TINY).pack(
                side="right"
            )
        if entry.badge is not None:
            kit.label(row, entry.badge[0], bg=pal.panel, fg=entry.badge[1], size=SIZE_TINY).pack(
                side="right", padx=(0, theme.METRICS.gap_sm)
            )

        def set_bg(colour: str) -> None:
            for widget in (row, text, *row.winfo_children(), *text.winfo_children()):
                if widget is not bar:
                    widget.configure(bg=colour)

        row.set_bg = set_bg  # type: ignore[attr-defined]

        def on_enter(_event: tk.Event, index: int = position) -> None:
            self.index = index
            self._highlight()

        for widget in (row, text, *row.winfo_children(), *text.winfo_children()):
            widget.bind("<Motion>", on_enter)
            widget.bind("<Button-1>", self._commit)
        return row

    def _highlight(self) -> None:
        selectable = [widget for widget in self.rows if hasattr(widget, "set_bg")]
        for position, widget in enumerate(selectable):
            selected = position == self.index
            widget.set_bg(self.pal.panel_selected if selected else self.pal.panel)
            widget.accent_bar.configure(bg=self.pal.accent if selected else self.pal.panel)
        if self.preview_open:
            self._render_preview()

    def _move(self, delta: int) -> None:
        if not self.filtered:
            return
        self.index = (self.index + delta) % len(self.filtered)
        self._highlight()

    @property
    def selected(self) -> Row | None:
        if not self.filtered:
            return None
        return self.filtered[min(self.index, len(self.filtered) - 1)]

    # ── step preview ─────────────────────────────────────────────────────
    def _set_preview(self, visible: bool) -> str:
        if self.running or visible == self.preview_open:
            return "break"
        self.preview_open = visible
        if visible:
            self._resize(int(self.base_width * 1.55), self.max_height)
            self.preview_frame.pack(side="right", fill="both", padx=(theme.METRICS.gap, 0))
            self._render_preview()
        else:
            self.preview_frame.pack_forget()
            self._resize(self.base_width)
            self._redraw()
        return "break"  # keep Tab from moving focus out of the entry

    def _render_preview(self) -> None:
        pal, kit = self.pal, self.kit
        for child in self.preview_frame.winfo_children():
            child.destroy()
        entry = self.selected
        if entry is None:
            return
        kit.label(
            self.preview_frame, entry.name, bg=pal.panel, size=SIZE_BODY, bold=True, anchor="w"
        ).pack(fill="x")
        if entry.kind != "profile":
            kit.label(
                self.preview_frame,
                entry.description,
                bg=pal.panel,
                fg=pal.muted,
                size=SIZE_SMALL,
                wraplength=260,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(4, 0))
            return

        profile: Profile = entry.payload
        if profile.tags:
            kit.label(
                self.preview_frame,
                " · ".join(profile.tags),
                bg=pal.panel,
                fg=pal.faint,
                size=SIZE_TINY,
                anchor="w",
            ).pack(fill="x", pady=(0, theme.METRICS.gap_sm))

        shown = profile.steps[:14]
        for step in shown:
            line = tk.Frame(self.preview_frame, bg=pal.panel)
            line.pack(fill="x", pady=1)
            note = "" if step.enabled else "  (off)"
            if step.enabled and step.parallel:
                note = "  (parallel)"
            kit.label(
                line,
                describe_step(step)[:60] + note,
                bg=pal.panel,
                fg=pal.faint if not step.enabled else pal.fg,
                size=SIZE_TINY,
                mono=True,
                anchor="w",
                justify="left",
            ).pack(side="left", fill="x", expand=True)
        if len(profile.steps) > len(shown):
            kit.label(
                self.preview_frame,
                f"… +{len(profile.steps) - len(shown)} more",
                bg=pal.panel,
                fg=pal.faint,
                size=SIZE_TINY,
                anchor="w",
            ).pack(fill="x", pady=(4, 0))

    # ── secondary actions ────────────────────────────────────────────────
    def _selected_profile(self) -> Profile | None:
        entry = self.selected
        return entry.payload if entry is not None and entry.kind == "profile" else None

    def _edit_selected(self, _event=None) -> str:
        profile = self._selected_profile()
        if self.running or profile is None:
            return "break"
        from launcher.editor import edit_profile

        self._hand_over(lambda: edit_profile(profile, parent=self.root))
        return "break"

    def _hand_over(self, open_window: Callable[[], None]) -> None:
        """Give way to another window without closing first.

        Destroying the launcher and then opening the next window would ask Tk
        for a second root, which it tolerates badly and macOS not at all. The
        launcher hides instead, and closes once the other window is done.
        """
        self.chosen = None
        try:
            self.root.withdraw()
            open_window()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def _stop_selected(self, _event=None) -> str:
        profile = self._selected_profile()
        if self.running or profile is None:
            return "break"
        self.running = True
        self._build_progress(profile, verb="Stopping")

        def worker() -> None:
            try:
                _results, stopped, stubborn = stop_profile(profile, self.results_q.put)
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
        self._hand_over(action.run)
        return "break"

    def _cancel(self, _event=None) -> None:
        self.chosen = None
        self.root.destroy()

    # ── execution view ───────────────────────────────────────────────────
    def _commit(self, _event=None) -> None:
        entry = self.selected
        if self.running or entry is None:
            return
        if entry.kind == "action":
            self._hand_over(entry.payload.run)
            return
        if entry.kind == "app":
            self._launch_app(entry.payload)
            return
        self.chosen = entry.payload
        if not self.execute:
            self.root.destroy()
            return
        self.running = True
        self._build_progress(self.chosen)
        threading.Thread(target=self._worker, args=(self.chosen,), daemon=True).start()
        self.root.after(60, self._poll_results)

    def _launch_app(self, app: App) -> None:
        """Start one application and close — no progress view worth showing."""
        from launcher import apps

        if not self.execute:
            self.root.destroy()
            return
        try:
            detail = apps.launch(app)
            print(detail)
        except (LookupError, OSError) as exc:
            self.footer.configure(text=f"could not launch {app.name}: {exc}", fg=self.pal.err)
            self.exit_code = 1
            return
        self.root.destroy()

    def _build_progress(self, profile: Profile, verb: str = "Running") -> None:
        pal, kit = self.pal, self.kit
        if self.preview_open:
            self.preview_frame.pack_forget()
            self.preview_open = False
        self._resize(self.base_width, self.max_height)
        for child in self.outer.winfo_children():
            child.destroy()
        self.rows.clear()

        header = kit.frame(self.outer)
        header.pack(fill="x")
        kit.brand(header, 20).pack(side="left", padx=(0, theme.METRICS.gap_sm))
        kit.label(header, f"{verb} {profile.name}", size=SIZE_DISPLAY, bold=True).pack(side="left")

        kit.divider(self.outer, pady=(theme.METRICS.gap, theme.METRICS.gap))

        self.progress_frame = kit.frame(self.outer)
        self.progress_frame.pack(fill="both", expand=True)

        self.status = kit.label(
            self.outer,
            "esc hides the window — the steps keep running",
            fg=pal.faint,
            size=SIZE_TINY,
        )
        self.status.pack(anchor="w", pady=(theme.METRICS.gap, 0))
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
                result = self.results_q.get_nowait()
            except queue.Empty:
                break
            if result is None:
                done = True
                break
            self._add_result_row(result)
            print(format_result(result))
        if done:
            self._finish()
        else:
            self.root.after(60, self._poll_results)

    def _add_result_row(self, result: StepResult) -> None:
        pal, kit = self.pal, self.kit
        if result.counts_as_failure:
            self.failed_count += 1
        row = kit.frame(self.progress_frame)
        row.pack(fill="x", pady=2)
        if result.skipped:
            status, tone, weight = "skipped", pal.faint, False
        elif result.ok:
            status, tone, weight = "done", pal.muted, False
        else:
            status, tone, weight = "failed", pal.err, True
        kit.label(row, status, fg=tone, size=SIZE_TINY, bold=weight, width=8, anchor="w").pack(
            side="left"
        )
        kit.label(
            row,
            result.step.label,
            fg=pal.faint if result.skipped else pal.fg,
            size=SIZE_SMALL,
            anchor="w",
        ).pack(side="left")
        detail_fg = pal.err if (not result.ok and not result.step.optional) else pal.faint
        kit.label(row, result.detail[:90], fg=detail_fg, size=SIZE_TINY, anchor="e").pack(
            side="right"
        )

    def _finish(self) -> None:
        if self.failed_count == 0:
            self.status.configure(text="All steps finished — closing", fg=self.pal.fg)
            self.exit_code = 0
            self.root.after(1400, self.root.destroy)
        else:
            self.status.configure(
                text=f"{self.failed_count} step(s) failed — esc to close", fg=self.pal.err
            )
            self.exit_code = 1

    def run(self) -> None:
        ui.show_window(self.root, self.parent)


def _synthetic(profile: Profile, detail: str) -> StepResult:
    """A result row that reports on the run itself rather than on a step."""
    from launcher.config import Step

    return StepResult(Step(type="kill", name=f"stop {profile.name}"), True, detail)


_active_lock = threading.Lock()
_active_hud: _Hud | None = None


def pick_and_run(profiles: list[Profile], parent: tk.Misc | None = None) -> int:
    """Open the HUD; on Enter, execute the profile with live step feedback.

    Opens even with no profiles at all — the Settings row is how a first-time
    user gets to the manager that creates one.

    Returns a process exit code (0 = success / cancelled, 1 = failed steps).
    """
    global _active_hud
    hud = _Hud(profiles, execute=True, parent=parent)
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
