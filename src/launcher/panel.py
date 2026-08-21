"""Graphical configuration panel (Tkinter).

One window for everything the CLI can configure: global settings, profiles,
secrets, git sync, run history, logs and paths. Opened from the HUD ("Settings"
row), from the tray, or with `palaunch settings`.

Long-running actions (running a profile, syncing, recording) happen on a worker
thread and stream into the console at the bottom — Tk is single-threaded, so
workers only ever push text onto a queue that the UI thread drains.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from typing import Any

from launcher import panel_model as model
from launcher import theme

SECTIONS = ("Settings", "Profiles", "Secrets", "Sync", "History", "Logs", "Paths")


class _Panel:
    def __init__(self) -> None:
        self.pal = theme.palette()
        self.font = theme.font_family()
        self.mono = theme.mono_family()
        self.messages: queue.Queue[str] = queue.Queue()
        self.finished: queue.Queue[tuple[str, str, Callable[[], None] | None]] = queue.Queue()
        self.widgets: dict[str, tuple[Any, model.Field]] = {}
        self.section = "Settings"
        self.busy = False

        self.root = tk.Tk()
        self.root.title("Profile Auto Launcher — Settings")
        self.root.geometry("1000x680")
        self.root.minsize(820, 560)
        self.root.configure(bg=self.pal.bg)

        self._build()
        self._show(self.section)
        self.root.after(80, self._drain)

    # ── chrome ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        pal = self.pal
        body = tk.Frame(self.root, bg=pal.bg)
        body.pack(fill="both", expand=True)

        nav = tk.Frame(body, bg=pal.panel, width=170, padx=10, pady=14)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Label(
            nav, text="◈  palaunch", bg=pal.panel, fg=pal.accent, font=(self.font, 12, "bold")
        ).pack(anchor="w", pady=(0, 14))

        self.nav_buttons: dict[str, tk.Label] = {}
        for name in SECTIONS:
            item = tk.Label(
                nav,
                text=name,
                bg=pal.panel,
                fg=pal.muted,
                font=(self.font, 10),
                anchor="w",
                padx=10,
                pady=7,
            )
            item.pack(fill="x", pady=1)
            item.bind("<Button-1>", lambda _e, n=name: self._show(n))
            self.nav_buttons[name] = item

        self.content = tk.Frame(body, bg=pal.bg, padx=18, pady=16)
        self.content.pack(side="left", fill="both", expand=True)

        console_box = tk.Frame(self.root, bg=pal.bg, padx=18)
        console_box.pack(fill="x")
        tk.Frame(console_box, bg=pal.border, height=1).pack(fill="x", pady=(0, 6))
        self.console = tk.Text(
            console_box,
            height=7,
            bg=pal.field_bg,
            fg=pal.muted,
            relief="flat",
            font=(self.mono, 9),
            highlightthickness=1,
            highlightbackground=pal.border,
            state="disabled",
            wrap="none",
        )
        self.console.pack(fill="x", pady=(0, 10))

        self.status = tk.Label(
            self.root,
            text="Ready.",
            bg=pal.panel,
            fg=pal.muted,
            font=(self.font, 9),
            anchor="w",
            padx=18,
            pady=6,
        )
        self.status.pack(fill="x", side="bottom")

    def _show(self, name: str) -> None:
        self.section = name
        for key, item in self.nav_buttons.items():
            selected = key == name
            item.configure(
                bg=self.pal.panel_selected if selected else self.pal.panel,
                fg=self.pal.fg if selected else self.pal.muted,
            )
        for child in self.content.winfo_children():
            child.destroy()
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.unbind_all(sequence)
        self.widgets.clear()
        builder = {
            "Settings": self._build_settings,
            "Profiles": self._build_profiles,
            "Secrets": self._build_secrets,
            "Sync": self._build_sync,
            "History": self._build_history,
            "Logs": self._build_logs,
            "Paths": self._build_paths,
        }[name]
        builder()

    # ── small widget helpers ─────────────────────────────────────────────
    def _heading(self, parent: tk.Widget, text: str, hint: str = "") -> None:
        tk.Label(
            parent, text=text, bg=self.pal.bg, fg=self.pal.fg, font=(self.font, 13, "bold")
        ).pack(anchor="w")
        if hint:
            tk.Label(
                parent, text=hint, bg=self.pal.bg, fg=self.pal.muted, font=(self.font, 9)
            ).pack(anchor="w", pady=(2, 10))
        else:
            tk.Frame(parent, bg=self.pal.bg, height=10).pack()

    def _button(
        self, parent: tk.Widget, text: str, command: Callable[[], None], primary: bool = False
    ) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.pal.accent if primary else self.pal.panel,
            fg="#ffffff" if primary else self.pal.fg,
            activebackground=self.pal.panel_hover,
            activeforeground=self.pal.fg,
            relief="flat",
            font=(self.font, 9, "bold" if primary else "normal"),
            padx=12,
            pady=4,
        )
        button.pack(side="left", padx=(0, 8))
        return button

    def _entry(self, parent: tk.Widget, value: str = "", width: int = 24, **kwargs) -> tk.Entry:
        var = tk.StringVar(value=value)
        entry = tk.Entry(
            parent,
            textvariable=var,
            bg=self.pal.field_bg,
            fg=self.pal.fg,
            insertbackground=self.pal.accent,
            relief="flat",
            font=(self.mono, 9),
            highlightthickness=1,
            highlightbackground=self.pal.border,
            width=width,
            **kwargs,
        )
        entry.var = var  # type: ignore[attr-defined]
        return entry

    def _listbox(self, parent: tk.Widget, height: int = 12) -> tk.Listbox:
        box = tk.Listbox(
            parent,
            bg=self.pal.panel,
            fg=self.pal.fg,
            selectbackground=self.pal.panel_selected,
            selectforeground=self.pal.fg,
            relief="flat",
            font=(self.mono, 9),
            highlightthickness=0,
            activestyle="none",
            height=height,
        )
        return box

    def _textbox(self, parent: tk.Widget, height: int = 16) -> tk.Text:
        text = tk.Text(
            parent,
            height=height,
            bg=self.pal.panel,
            fg=self.pal.fg,
            relief="flat",
            font=(self.mono, 9),
            highlightthickness=0,
            wrap="none",
            state="disabled",
        )
        return text

    def _scrollable(self, parent: tk.Widget) -> tk.Frame:
        """A vertically scrolling frame — the settings form outgrows short screens."""
        canvas = tk.Canvas(parent, bg=self.pal.bg, highlightthickness=0)
        bar = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=self.pal.bg)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def wheel(event: tk.Event) -> None:
            delta = (
                1 if getattr(event, "num", 0) == 5 else -1 if getattr(event, "num", 0) == 4 else 0
            )
            canvas.yview_scroll(delta or (-1 if event.delta > 0 else 1), "units")

        # bind_all so the wheel works over the labels inside; _show clears it
        # again when another section takes over the content area.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(sequence, wheel)
        return inner

    @staticmethod
    def _fill(text: tk.Text, lines: list[str], empty: str = "") -> None:
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", "\n".join(lines) if lines else empty)
        text.configure(state="disabled")

    # ── console / worker plumbing ────────────────────────────────────────
    def log(self, message: str) -> None:
        """Append to the console. Safe to call from a worker thread."""
        self.messages.put(message)

    def _drain(self) -> None:
        if not self.root.winfo_exists():
            return
        wrote = False
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            self.console.configure(state="normal")
            self.console.insert("end", message.rstrip() + "\n")
            self.console.configure(state="disabled")
            wrote = True
        if wrote:
            self.console.see("end")
        while True:
            try:
                text, colour, then = self.finished.get_nowait()
            except queue.Empty:
                break
            self._finish(text, colour, then)
        self.root.after(80, self._drain)

    def _set_status(self, text: str, colour: str | None = None) -> None:
        self.status.configure(text=text, fg=colour or self.pal.muted)

    def _work(
        self, label: str, job: Callable[[], Any], then: Callable[[], None] | None = None
    ) -> None:
        """Run `job` off the UI thread, reporting whatever it returns.

        The worker never touches a widget: it pushes text and a completion
        record onto queues that `_drain` empties on the UI thread. Calling into
        Tk from another thread is what makes long-running GUI code crash.
        """
        if self.busy:
            self.log("Another action is still running.")
            return
        self.busy = True
        self._set_status(f"{label}…", self.pal.accent)

        def worker() -> None:
            try:
                result = job()
            except Exception as exc:  # surfaced, never a traceback in the user's face
                self.log(f"✗ {label}: {exc}")
                self.finished.put((f"✗ {label} failed", self.pal.err, then))
                return
            if result:
                for line in str(result).splitlines():
                    self.log(line)
            self.finished.put((f"✓ {label}", self.pal.ok, then))

        threading.Thread(target=worker, daemon=True, name="pal-panel").start()

    def _finish(self, text: str, colour: str, then: Callable[[], None] | None) -> None:
        self.busy = False
        self._set_status(text, colour)
        if then is not None and self.root.winfo_exists():
            then()

    # ── Settings ─────────────────────────────────────────────────────────
    def _build_settings(self) -> None:
        pal = self.pal
        self._heading(
            self.content,
            "Settings",
            "Written to settings.yaml — the same keys `palaunch config set` writes.",
        )
        overridden = model.env_overrides()
        values = model.current_values()

        buttons = tk.Frame(self.content, bg=pal.bg, pady=14)
        buttons.pack(fill="x", side="bottom")
        self._button(buttons, "Save", self._save_settings, primary=True)
        self._button(buttons, "Restore defaults", self._restore_defaults)
        self._button(buttons, "Reload from file", lambda: self._show("Settings"))

        form = self._scrollable(self.content)
        for group in model.GROUPS:
            tk.Label(
                form,
                text=group.title.upper(),
                bg=pal.bg,
                fg=pal.accent,
                font=(self.font, 8, "bold"),
            ).pack(anchor="w", pady=(10, 4))
            grid = tk.Frame(form, bg=pal.bg)
            grid.pack(fill="x")
            grid.columnconfigure(2, weight=1)
            for row, field in enumerate(group.fields):
                tk.Label(
                    grid,
                    text=field.label,
                    bg=pal.bg,
                    fg=pal.fg,
                    font=(self.font, 9),
                    anchor="w",
                    width=26,
                ).grid(row=row, column=0, sticky="w", pady=2)
                widget = self._field_widget(grid, field, values[field.key])
                widget.grid(row=row, column=1, sticky="w", pady=2)
                self.widgets[field.key] = (widget, field)
                note = field.help
                if field.key in overridden:
                    note = (
                        f"forced by ${overridden[field.key]} — the file is ignored while it is set"
                    )
                if note:
                    tk.Label(
                        grid,
                        text=note,
                        bg=pal.bg,
                        fg=pal.warn if field.key in overridden else pal.muted,
                        font=(self.font, 8),
                        anchor="w",
                    ).grid(row=row, column=2, sticky="w", padx=(12, 0))

    def _field_widget(self, parent: tk.Widget, field: model.Field, value: Any) -> tk.Widget:
        pal = self.pal
        if field.kind == "bool":
            var = tk.BooleanVar(value=bool(value))
            widget = tk.Checkbutton(
                parent,
                variable=var,
                bg=pal.bg,
                fg=pal.fg,
                selectcolor=pal.panel,
                activebackground=pal.bg,
                highlightthickness=0,
                borderwidth=0,
            )
            widget.var = var  # type: ignore[attr-defined]
            return widget
        if field.kind == "choice":
            var = tk.StringVar(value=str(value))
            widget = tk.OptionMenu(parent, var, *field.choices)
            widget.configure(
                bg=pal.panel,
                fg=pal.fg,
                relief="flat",
                highlightthickness=0,
                font=(self.font, 9),
                activebackground=pal.panel_hover,
                width=12,
            )
            widget.var = var  # type: ignore[attr-defined]
            return widget
        return self._entry(parent, "" if value is None else str(value), width=34)

    def submitted(self) -> dict[str, Any]:
        """What the settings form currently holds, unvalidated."""
        return {key: widget.var.get() for key, (widget, _field) in self.widgets.items()}

    def _save_settings(self) -> None:
        from tkinter import messagebox

        try:
            diff = model.apply(self.submitted())
        except model.ValidationError as exc:
            messagebox.showerror("Invalid setting", str(exc), parent=self.root)
            self._set_status(str(exc), self.pal.err)
            return
        if not diff:
            self._set_status("No changes to save.", self.pal.muted)
            return
        for key, value in sorted(diff.items()):
            self.log(f"{key} = {value}")
        self._set_status(f"Saved {len(diff)} change(s).", self.pal.ok)
        ignored = sorted(set(diff) & set(model.env_overrides()))
        if ignored:
            self.log(f"note: {', '.join(ignored)} stays overridden by the environment")

    def _restore_defaults(self) -> None:
        for key, value in model.defaults().items():
            widget, field = self.widgets[key]
            widget.var.set(value if field.kind == "bool" else str(value))
        self._set_status("Defaults filled in — press Save to write them.", self.pal.accent)

    # ── Profiles ─────────────────────────────────────────────────────────
    def _build_profiles(self) -> None:
        pal = self.pal
        self._heading(
            self.content, "Profiles", "Everything `palaunch list / run / stop / edit` does."
        )
        self.profile_list = self._listbox(self.content, height=13)
        self.profile_list.pack(fill="both", expand=True)

        row1 = tk.Frame(self.content, bg=pal.bg, pady=10)
        row1.pack(fill="x")
        self._button(row1, "Run", lambda: self._run_selected(dry=False), primary=True)
        self._button(row1, "Dry run", lambda: self._run_selected(dry=True))
        self._button(row1, "Stop", self._stop_selected)
        self._button(row1, "Switch to", self._switch_selected)

        row2 = tk.Frame(self.content, bg=pal.bg)
        row2.pack(fill="x")
        self._button(row2, "New…", self._new_profile)
        self._button(row2, "Edit…", self._edit_selected)
        self._button(row2, "Record…", self._record_profile)
        self._button(row2, "Validate all", self._validate_all)
        self._button(row2, "Open folder", self._open_profiles_dir)
        self._button(row2, "Refresh", self._refresh_profiles)
        self._refresh_profiles()

    def _refresh_profiles(self) -> None:
        self.profile_rows = model.profile_rows()
        self.profile_list.delete(0, "end")
        for row in self.profile_rows:
            marker = "★" if row["default"] else " "
            running = f"  ● {row['running']} running" if row["running"] else ""
            hotkey = f"  [{row['hotkey']}]" if row["hotkey"] else ""
            self.profile_list.insert(
                "end",
                f"{marker} {row['name']:<18} {row['steps']:>2} steps  "
                f"{row['description'][:38]:<38}{hotkey}{running}",
            )
        if self.profile_rows:
            self.profile_list.selection_set(0)
        else:
            self.profile_list.insert("end", "  no profiles yet — press “New…”")

    def _selected_profile(self) -> dict[str, Any] | None:
        selection = self.profile_list.curselection()
        if not selection or not self.profile_rows:
            self._set_status("Select a profile first.", self.pal.warn)
            return None
        return self.profile_rows[selection[0]]

    def _run_selected(self, dry: bool) -> None:
        row = self._selected_profile()
        if row is None:
            return
        name = row["name"]

        def job() -> str:
            from launcher.config import find_profile
            from launcher.executor import dry_run_profile, format_result, run_profile

            profile = find_profile(name)
            if profile is None:
                raise RuntimeError(f"profile '{name}' disappeared")
            if dry:
                for result in dry_run_profile(profile):
                    self.log(format_result(result))
                return ""
            results = run_profile(profile, on_result=lambda r: self.log(format_result(r)))
            failed = sum(1 for r in results if r.counts_as_failure)
            return f"{len(results) - failed} ok, {failed} failed"

        self._work(f"{'Dry run' if dry else 'Run'} {name}", job, self._refresh_profiles)

    def _stop_selected(self) -> None:
        row = self._selected_profile()
        if row is None:
            return
        name = row["name"]
        if model.settings.load().confirm_stop:
            from tkinter import messagebox

            if not messagebox.askyesno("Stop profile", f"Stop {name}?", parent=self.root):
                return

        def job() -> str:
            from launcher import procs
            from launcher.config import find_profile
            from launcher.executor import format_result, stop_profile

            profile = find_profile(name)
            if profile is None:
                stopped, stubborn = procs.stop_profile(name)
            else:
                _results, stopped, stubborn = stop_profile(
                    profile, on_result=lambda r: self.log(format_result(r))
                )
            return f"{stopped} process(es) closed, {stubborn} survived"

        self._work(f"Stop {name}", job, self._refresh_profiles)

    def _switch_selected(self) -> None:
        row = self._selected_profile()
        if row is None:
            return
        name = row["name"]

        def job() -> str:
            from launcher import procs
            from launcher.config import find_profile
            from launcher.executor import format_result, run_profile, stop_profile

            for other in list(procs.active_profiles()):
                if other.lower() == name.lower():
                    continue
                running = find_profile(other)
                if running is None:
                    procs.stop_profile(other)
                else:
                    stop_profile(running)
                self.log(f"■ stopped {other}")
            profile = find_profile(name)
            if profile is None:
                raise RuntimeError(f"profile '{name}' disappeared")
            results = run_profile(profile, on_result=lambda r: self.log(format_result(r)))
            failed = sum(1 for r in results if r.counts_as_failure)
            return f"{len(results) - failed} ok, {failed} failed"

        self._work(f"Switch to {name}", job, self._refresh_profiles)

    def _new_profile(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring("New profile", "Profile name:", parent=self.root)
        if not name:
            return
        from launcher.editor import new_profile

        path = new_profile(name)
        self.log(f"created {path}")
        self._refresh_profiles()

    def _edit_selected(self) -> None:
        row = self._selected_profile()
        if row is None or not row["path"]:
            return
        from pathlib import Path

        from launcher.editor import open_editor

        open_editor(Path(row["path"]))
        self._refresh_profiles()

    def _record_profile(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring(
            "Record a profile", "Name for the new profile:", parent=self.root
        )
        if not name:
            return
        seconds = simpledialog.askinteger(
            "Record a profile",
            "Record for how many seconds?",
            parent=self.root,
            initialvalue=120,
            minvalue=5,
            maxvalue=3600,
        )
        if not seconds:
            return

        def job() -> str:
            from launcher import record
            from launcher.config import profiles_dir

            self.log(f"● recording for {seconds}s — open the apps you want in '{name}'")
            candidates = record.observe(
                duration=float(seconds),
                on_tick=lambda remaining, found: None,
            )
            for candidate in candidates:
                self.log(f"  + {candidate.name} ({candidate.exe})")
            path = profiles_dir() / f"{name.lower().replace(' ', '-')}.yaml"
            record.write_profile(name, candidates, path)
            return f"wrote {path}"

        self._work(f"Record {name}", job, self._refresh_profiles)

    def _validate_all(self) -> None:
        def job() -> str:
            from launcher.config import load_profile, profile_files

            paths = profile_files()
            if not paths:
                return "no profile files found"
            errors = 0
            for path in paths:
                try:
                    profile = load_profile(path)
                except Exception as exc:
                    errors += 1
                    self.log(f" ✗ {path.name}: {exc}")
                    continue
                self.log(f" ✓ {path.name}: '{profile.name}' — {len(profile.steps)} steps")
            return f"{len(paths) - errors}/{len(paths)} profiles valid"

        self._work("Validate profiles", job)

    def _open_profiles_dir(self) -> None:
        from launcher.config import profiles_dir

        self._work("Open profiles folder", lambda: model.open_path(profiles_dir()))

    # ── Secrets ──────────────────────────────────────────────────────────
    def _build_secrets(self) -> None:
        pal = self.pal
        self._heading(
            self.content,
            "Secrets",
            "Stored in the OS credential store; used as {{ secret.NAME }} in a profile.",
        )
        from launcher import secrets

        if not secrets.available():
            tk.Label(
                self.content,
                text="keyring is not installed — run `pip install profile-auto-launcher[secrets]`",
                bg=pal.bg,
                fg=pal.warn,
                font=(self.font, 10),
            ).pack(anchor="w", pady=20)
            return

        self.secret_list = self._listbox(self.content, height=12)
        self.secret_list.pack(fill="both", expand=True)

        buttons = tk.Frame(self.content, bg=pal.bg, pady=10)
        buttons.pack(fill="x")
        self._button(buttons, "Add…", self._add_secret, primary=True)
        self._button(buttons, "Remove", self._remove_secret)
        self._button(buttons, "Refresh", self._refresh_secrets)
        self._refresh_secrets()

    def _refresh_secrets(self) -> None:
        from launcher import secrets

        self.secret_names = secrets.list_names()
        self.secret_list.delete(0, "end")
        for name in self.secret_names:
            self.secret_list.insert("end", f"  {name}")
        if not self.secret_names:
            self.secret_list.insert("end", "  no secrets stored yet")

    def _add_secret(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring("Add secret", "Name:", parent=self.root)
        if not name:
            return
        value = simpledialog.askstring(
            "Add secret", f"Value for '{name}':", show="*", parent=self.root
        )
        if value is None:
            return
        from launcher import secrets

        try:
            secrets.set_secret(name, value)
        except secrets.SecretError as exc:
            self._set_status(str(exc), self.pal.err)
            return
        self.log(f"stored '{name}' — use it as {{{{ secret.{name} }}}}")
        self._refresh_secrets()

    def _remove_secret(self) -> None:
        selection = self.secret_list.curselection()
        if not selection or not self.secret_names:
            self._set_status("Select a secret first.", self.pal.warn)
            return
        name = self.secret_names[selection[0]]
        from launcher import secrets

        try:
            secrets.delete(name)
        except secrets.SecretError as exc:
            self._set_status(str(exc), self.pal.err)
            return
        self.log(f"removed '{name}'")
        self._refresh_secrets()

    # ── Sync ─────────────────────────────────────────────────────────────
    def _build_sync(self) -> None:
        pal = self.pal
        self._heading(
            self.content, "Sync", "Keeps the profiles directory in a git repo — `palaunch sync`."
        )
        form = tk.Frame(self.content, bg=pal.bg)
        form.pack(fill="x")
        tk.Label(
            form, text="Remote URL", bg=pal.bg, fg=pal.fg, font=(self.font, 9), width=14, anchor="w"
        ).grid(row=0, column=0, sticky="w", pady=3)
        self.sync_remote = self._entry(form, model.settings.load().sync_remote, width=52)
        self.sync_remote.grid(row=0, column=1, sticky="w", pady=3)

        tk.Label(
            form,
            text="Commit message",
            bg=pal.bg,
            fg=pal.fg,
            font=(self.font, 9),
            width=14,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=3)
        self.sync_message = self._entry(form, "", width=52)
        self.sync_message.grid(row=1, column=1, sticky="w", pady=3)

        self.sync_push = tk.BooleanVar(value=True)
        tk.Checkbutton(
            form,
            text="Push after committing",
            variable=self.sync_push,
            bg=pal.bg,
            fg=pal.fg,
            selectcolor=pal.panel,
            activebackground=pal.bg,
            font=(self.font, 9),
            highlightthickness=0,
            borderwidth=0,
        ).grid(row=2, column=1, sticky="w", pady=3)

        buttons = tk.Frame(self.content, bg=pal.bg, pady=12)
        buttons.pack(fill="x")
        self._button(buttons, "Sync now", self._sync_now, primary=True)
        self._button(buttons, "Initialise repo", self._sync_init)
        self._button(buttons, "Status", self._sync_status)

        self.sync_output = self._textbox(self.content, height=12)
        self.sync_output.pack(fill="both", expand=True)
        self._sync_status()

    def _sync_init(self) -> None:
        remote = self.sync_remote.var.get().strip()

        def job() -> str:
            from launcher import sync

            directory = sync.init(remote)
            if remote:
                model.settings.save({"sync_remote": remote})
            return f"initialised {directory}" + (f" -> {remote}" if remote else "")

        self._work("Initialise sync repo", job, self._sync_status)

    def _sync_status(self) -> None:
        from launcher import sync

        try:
            text = sync.status()
        except sync.SyncError as exc:
            text = str(exc)
        self._fill(self.sync_output, text.splitlines())

    def _sync_now(self) -> None:
        message = self.sync_message.var.get().strip()
        push = bool(self.sync_push.get())

        def job() -> str:
            from launcher import sync

            return sync.sync(message=message, push=push)

        self._work("Sync profiles", job, self._sync_status)

    # ── History ──────────────────────────────────────────────────────────
    def _build_history(self) -> None:
        pal = self.pal
        self._heading(
            self.content, "History", "Past runs and per-step timings — `palaunch history`."
        )
        controls = tk.Frame(self.content, bg=pal.bg)
        controls.pack(fill="x", pady=(0, 8))
        self.history_view = tk.StringVar(value="runs")
        for label, value in (("Runs", "runs"), ("Step stats", "stats")):
            tk.Radiobutton(
                controls,
                text=label,
                variable=self.history_view,
                value=value,
                command=self._refresh_history,
                bg=pal.bg,
                fg=pal.fg,
                selectcolor=pal.panel,
                activebackground=pal.bg,
                font=(self.font, 9),
                highlightthickness=0,
                borderwidth=0,
            ).pack(side="left", padx=(0, 10))
        tk.Label(controls, text="limit", bg=pal.bg, fg=pal.muted, font=(self.font, 9)).pack(
            side="left"
        )
        self.history_limit = self._entry(controls, "30", width=6)
        self.history_limit.pack(side="left", padx=6)
        self._button(controls, "Refresh", self._refresh_history)
        self._button(controls, "Clear history", self._clear_history)

        self.history_output = self._textbox(self.content, height=18)
        self.history_output.pack(fill="both", expand=True)
        self._refresh_history()

    def _refresh_history(self) -> None:
        try:
            limit = max(1, int(self.history_limit.var.get()))
        except ValueError:
            limit = 30
        if self.history_view.get() == "stats":
            lines = model.stats_rows(limit=limit)
            empty = "No history yet."
        else:
            lines = model.history_rows(limit=limit)
            empty = "No history yet — run a profile first."
        self._fill(self.history_output, lines, empty)

    def _clear_history(self) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno("Clear history", "Delete the run history?", parent=self.root):
            return
        from launcher.state import clear_history

        clear_history()
        self.log("history cleared")
        self._refresh_history()

    # ── Logs ─────────────────────────────────────────────────────────────
    def _build_logs(self) -> None:
        pal = self.pal
        self._heading(
            self.content, "Logs", "The rotating log every run writes to — `palaunch logs`."
        )
        controls = tk.Frame(self.content, bg=pal.bg)
        controls.pack(fill="x", pady=(0, 8))
        tk.Label(controls, text="lines", bg=pal.bg, fg=pal.muted, font=(self.font, 9)).pack(
            side="left"
        )
        self.log_lines = self._entry(controls, "200", width=6)
        self.log_lines.pack(side="left", padx=6)
        self._button(controls, "Refresh", self._refresh_logs)
        self._button(controls, "Open log folder", self._open_log_dir)

        self.log_output = self._textbox(self.content, height=20)
        self.log_output.pack(fill="both", expand=True)
        self._refresh_logs()

    def _refresh_logs(self) -> None:
        from launcher.logging_setup import tail

        try:
            lines = max(1, int(self.log_lines.var.get()))
        except ValueError:
            lines = 200
        self._fill(self.log_output, tail(lines), "Nothing logged yet.")
        self.log_output.see("end")

    def _open_log_dir(self) -> None:
        from launcher.logging_setup import log_dir

        log_dir().mkdir(parents=True, exist_ok=True)
        self._work("Open log folder", lambda: model.open_path(log_dir()))

    # ── Paths ────────────────────────────────────────────────────────────
    def _build_paths(self) -> None:
        pal = self.pal
        self._heading(self.content, "Paths", "Where everything lives — `palaunch where`.")
        grid = tk.Frame(self.content, bg=pal.bg)
        grid.pack(fill="x")
        for row, (label, value) in enumerate(model.paths_report()):
            tk.Label(
                grid, text=label, bg=pal.bg, fg=pal.muted, font=(self.font, 9), width=18, anchor="w"
            ).grid(row=row, column=0, sticky="w", pady=2)
            entry = self._entry(grid, value, width=70)
            entry.configure(state="readonly", readonlybackground=pal.field_bg)
            entry.grid(row=row, column=1, sticky="w", pady=2)

        tk.Label(
            self.content, text="HOTKEYS", bg=pal.bg, fg=pal.accent, font=(self.font, 8, "bold")
        ).pack(anchor="w", pady=(16, 4))
        for line in model.hotkey_rows():
            tk.Label(
                self.content,
                text=f"  {line}",
                bg=pal.bg,
                fg=pal.fg,
                font=(self.mono, 9),
                anchor="w",
            ).pack(fill="x")

        buttons = tk.Frame(self.content, bg=pal.bg, pady=14)
        buttons.pack(fill="x")
        self._button(buttons, "Write JSON Schema", self._write_schema, primary=True)
        self._button(buttons, "Open config folder", self._open_config_dir)

    def _write_schema(self) -> None:
        def job() -> str:
            from launcher import schema

            return f"wrote {schema.write()}"

        self._work("Write JSON Schema", job)

    def _open_config_dir(self) -> None:
        from launcher.config import config_dir

        config_dir().mkdir(parents=True, exist_ok=True)
        self._work("Open config folder", lambda: model.open_path(config_dir()))

    # ── lifecycle ────────────────────────────────────────────────────────
    def run(self) -> None:
        self.root.mainloop()


def open_panel(section: str = "Settings") -> int:
    """Open the configuration panel. Returns a process exit code."""
    try:
        panel = _Panel()
    except tk.TclError as exc:
        print(f"Could not open the settings window: {exc}")
        return 1
    if section in SECTIONS:
        panel._show(section)
    panel.run()
    return 0
