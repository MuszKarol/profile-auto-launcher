"""The manager window — everything the CLI can do, with a mouse.

Five pages, not seven: **Launch** (run a profile or one app by name),
**Profiles** (create, edit, record, validate), **Sync** (git and rsync),
**Activity** (run history, step timings and the log) and **Settings** (every
option, the credential store and the paths). Secrets and paths used to be
pages of their own; they are configuration, so they live with the settings.

Long-running actions (running a profile, syncing, recording) happen on a
worker thread and stream into the output pane at the bottom, which opens by
itself when there is something to read — Tk is single-threaded, so workers
only ever push text onto a queue that the UI thread drains.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from typing import Any

from launcher import icons, theme, ui
from launcher import panel_model as model
from launcher.panel_model import SECTIONS
from launcher.theme import SIZE_BODY, SIZE_SMALL, SIZE_TINY
from launcher.ui import Kit

# One icon per navigation entry. Page headers carry none: the entry beside
# them already shows it.
NAV_ICONS = {
    "Launch": "play",
    "Profiles": "layers",
    "Sync": "sync",
    "Activity": "activity",
    "Settings": "sliders",
}


class _Panel:
    def __init__(self, parent: tk.Misc | None = None) -> None:
        self.kit = Kit()
        self.pal = self.kit.pal
        self.font = self.kit.font
        self.mono = self.kit.mono
        self.m = theme.METRICS
        self.messages: queue.Queue[str] = queue.Queue()
        self.finished: queue.Queue[tuple[str, str, Callable[[], None] | None]] = queue.Queue()
        self.widgets: dict[str, tuple[Any, model.Field]] = {}
        self.section = SECTIONS[0]
        self.busy = False

        self.parent = parent
        self.root = ui.new_window(parent)
        self.kit.chrome(self.root, "Profile Auto Launcher")
        self.root.geometry("1040x720")
        self.root.minsize(880, 600)

        self._build()
        self._show(self.section)
        self.root.after(80, self._drain)

    # ── chrome ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        kit, pal, m = self.kit, self.pal, self.m
        shell = kit.frame(self.root)
        shell.pack(fill="both", expand=True)

        nav = tk.Frame(shell, bg=pal.panel, width=208, padx=m.gap, pady=m.gap_lg)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Frame(shell, bg=pal.border, width=1).pack(side="left", fill="y")

        brand = tk.Frame(nav, bg=pal.panel, padx=m.gap_sm)
        brand.pack(fill="x", pady=(2, m.gap_xl))
        kit.brand(brand, 20, bg=pal.panel).pack(side="left", padx=(0, m.gap_sm + 2))
        kit.label(brand, "palaunch", bg=pal.panel, size=SIZE_BODY, bold=True).pack(side="left")

        self.nav_buttons: dict[str, tuple[tk.Frame, tk.Canvas, tk.Label]] = {}
        for name in SECTIONS:
            item = tk.Frame(nav, bg=pal.panel, padx=m.gap_sm + 2, pady=7, cursor="hand2")
            item.pack(fill="x", pady=1)
            glyph = kit.icon(item, NAV_ICONS.get(name, ""), 16, pal.muted, pal.panel)
            glyph.configure(cursor="hand2")
            glyph.pack(side="left", padx=(0, m.gap))
            label = kit.label(
                item, name, bg=pal.panel, fg=pal.muted, size=SIZE_SMALL, anchor="w", cursor="hand2"
            )
            label.pack(side="left")
            for widget in (item, glyph, label):
                widget.bind("<Button-1>", lambda _e, n=name: self._show(n))
                widget.bind("<Enter>", lambda _e, n=name: self._hover_nav(n, True))
                widget.bind("<Leave>", lambda _e, n=name: self._hover_nav(n, False))
            self.nav_buttons[name] = (item, glyph, label)

        footer = tk.Frame(nav, bg=pal.panel)
        footer.pack(side="bottom", fill="x")
        kit.button(footer, "Open launcher", self._open_launcher, icon="search", pack=False).pack(
            fill="x"
        )
        kit.label(
            footer, f"Version {model.version()}", bg=pal.panel, fg=pal.faint, size=SIZE_TINY
        ).pack(anchor="w", padx=m.gap_sm, pady=(m.gap, 0))

        main = kit.frame(shell)
        main.pack(side="left", fill="both", expand=True)

        # Bottom-anchored chrome is packed first: whatever is packed last gets
        # squeezed off the window when a page is taller than the frame.
        bar = kit.frame(main, padx=m.gap_xl, pady=m.gap_sm)
        bar.pack(fill="x", side="bottom")
        self.status = kit.label(bar, "Ready", fg=pal.muted, size=SIZE_TINY, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.console_toggle = kit.button(
            bar, "Show output", self._toggle_console, kind="quiet", pack=False
        )
        self.console_toggle.pack(side="right")
        kit.button(bar, "Clear", self._clear_console, kind="quiet", pack=False).pack(
            side="right", padx=(0, m.gap_xs)
        )
        tk.Frame(main, bg=pal.border, height=1).pack(fill="x", side="bottom")

        # The output pane opens by itself when something is written to it, and
        # otherwise stays out of the way of the page above.
        self.console_box = kit.frame(main, padx=m.gap_xl, pady=m.gap)
        self.console = kit.textbox(self.console_box, height=7)
        self.console.pack(fill="both", expand=True)
        self.console_open = False

        self.content = tk.Frame(main, bg=pal.bg, padx=m.gap_xl, pady=m.gap_xl)
        self.content.pack(side="top", fill="both", expand=True)

    def _toggle_console(self, show: bool | None = None) -> None:
        show = not self.console_open if show is None else show
        if show == self.console_open:
            return
        self.console_open = show
        if show:
            self.console_box.pack(fill="x", side="bottom", before=self.content)
        else:
            self.console_box.pack_forget()
        caption = self.console_toggle.caption  # type: ignore[attr-defined]
        caption.configure(text="Hide output" if show else "Show output")

    def _clear_console(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _hover_nav(self, name: str, entered: bool) -> None:
        """Lift an unselected entry on hover. The selected one never moves."""
        if name == self.section:
            return
        item, glyph, label = self.nav_buttons[name]
        ground = self.pal.panel_hover if entered else self.pal.panel
        ink = self.pal.fg if entered else self.pal.muted
        item.configure(bg=ground)
        label.configure(bg=ground, fg=ink)
        icons.recolour(glyph, ink, ground)

    def _show(self, name: str) -> None:
        self.section = name
        for key, (item, glyph, label) in self.nav_buttons.items():
            selected = key == name
            ground = self.pal.panel_selected if selected else self.pal.panel
            ink = self.pal.fg if selected else self.pal.muted
            item.configure(bg=ground)
            label.configure(bg=ground, fg=ink)
            icons.recolour(glyph, ink, ground)
        for child in self.content.winfo_children():
            child.destroy()
        for sequence in ui.WHEEL_EVENTS:
            self.root.unbind_all(sequence)
        self.widgets.clear()
        {
            "Launch": self._build_launch,
            "Profiles": self._build_profiles,
            "Sync": self._build_sync,
            "Activity": self._build_activity,
            "Settings": self._build_settings,
        }[name]()

    def _open_launcher(self) -> None:
        """Open the launcher as a child of this window, so the process keeps
        the one Tk root it already has."""
        from launcher.config import discover_profiles
        from launcher.hud import pick_and_run

        pick_and_run(discover_profiles(), parent=self.root)

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
            self._toggle_console(True)
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
                self.log(f"FAILED  {label}: {exc}")
                self.finished.put((f"{label} failed", self.pal.err, then))
                return
            if result:
                for line in str(result).splitlines():
                    self.log(line)
            self.finished.put((f"{label} finished", self.pal.ok, then))

        threading.Thread(target=worker, daemon=True, name="pal-panel").start()

    def _finish(self, text: str, colour: str, then: Callable[[], None] | None) -> None:
        self.busy = False
        self._set_status(text, colour)
        if then is not None and self.root.winfo_exists():
            then()

    # ── Launch ───────────────────────────────────────────────────────────
    def _build_launch(self) -> None:
        kit, pal, m = self.kit, self.pal, self.m
        actions = kit.heading(
            self.content,
            "Launch",
            "Run a whole profile, or type an application's name to open just that one.",
        )
        kit.button(
            actions,
            "",
            self._refresh_apps,
            kind="quiet",
            icon="refresh",
            tip="Scan for installed applications again",
            padx=0,
        )

        search = kit.frame(self.content)
        search.pack(fill="x")
        kit.button(
            search, "Dry run", lambda: self._launch_selected(dry=True), pack=True, side="right"
        )
        kit.button(
            search,
            "Launch",
            self._launch_selected,
            kind="primary",
            icon="run",
            side="right",
            padx=(m.gap_sm, m.gap_sm),
        )
        self.launch_query = kit.search_field(search, "Search profiles and applications")
        self.launch_query.field.pack(side="left", fill="x", expand=True)  # type: ignore[attr-defined]
        self.launch_query.bind("<KeyRelease>", self._on_launch_key)
        self.launch_query.bind("<Return>", lambda _e: self._launch_selected())
        self.launch_query.bind("<Down>", lambda _e: self.launch_list.move(1))
        self.launch_query.bind("<Up>", lambda _e: self.launch_list.move(-1))
        self.launch_query.focus_set()

        running = kit.frame(self.content)
        running.pack(fill="x", side="bottom", pady=(m.gap, 0))
        kit.button(running, "Stop all", self._stop_everything, kind="quiet", side="right", padx=0)
        kit.button(
            running,
            "Stop selected",
            self._stop_launch_selection,
            kind="danger",
            icon="stop",
            side="right",
            padx=(0, m.gap_sm),
        )
        kit.label(running, "Running", size=SIZE_SMALL, bold=True).pack(side="left")
        self.running_label = kit.label(running, "", fg=pal.muted, size=SIZE_SMALL, anchor="w")
        self.running_label.pack(side="left", fill="x", expand=True, padx=(m.gap, m.gap))

        self.launch_list = kit.rows(
            self.content,
            empty="Nothing matches. Try part of a profile or application name.",
            on_activate=lambda _i: self._launch_selected(),
        )
        self.launch_list.pack(fill="both", expand=True, pady=(m.gap, 0))
        self._refresh_launch()

    def _on_launch_key(self, event: tk.Event) -> None:
        if event.keysym not in ("Up", "Down", "Return", "Escape"):
            self._refresh_launch()

    def _refresh_launch(self) -> None:
        query = self.launch_query.var.get().strip()
        self.launch_rows = model.launch_rows(query)
        self.launch_list.set_rows(
            [
                ui.ListRow(
                    title=row["name"],
                    detail=row["detail"],
                    tags=() if row["kind"] == "profile" else ("app",),
                )
                for row in self.launch_rows
            ]
        )
        self._refresh_running()

    def _refresh_running(self) -> None:
        lines = model.running_rows()
        self.running_label.configure(
            text=", ".join(lines) if lines else "Nothing the launcher started is running."
        )

    def _refresh_apps(self) -> None:
        def job() -> str:
            from launcher import apps

            return f"indexed {len(apps.index(refresh=True))} applications"

        self._work("Refresh applications", job, self._refresh_launch)

    def _selected_launch(self) -> dict[str, Any] | None:
        selection = self.launch_list.curselection()
        if not selection or not self.launch_rows:
            self._set_status("Nothing selected.", self.pal.warn)
            return None
        return self.launch_rows[selection[0]]

    def _launch_selected(self, dry: bool = False) -> None:
        row = self._selected_launch()
        if row is None:
            return
        if row["kind"] == "profile":
            self._run_profile(row["name"], dry=dry, then=self._refresh_launch)
            return
        name = row["name"]

        def job() -> str:
            from launcher import apps

            return apps.launch(name)

        self._work(f"Launch {name}", job, self._refresh_launch)

    def _stop_launch_selection(self) -> None:
        row = self._selected_launch()
        if row is None:
            return
        if row["kind"] == "profile":
            self._stop_profile(row["name"], then=self._refresh_launch)
            return

        def job() -> str:
            from launcher import apps

            closed, survived = apps.stop_all()
            return f"{closed} app(s) closed, {survived} survived"

        self._work("Stop launched apps", job, self._refresh_launch)

    def _stop_everything(self) -> None:
        def job() -> str:
            from launcher import procs
            from launcher.config import find_profile
            from launcher.executor import stop_profile

            closed = 0
            for name in list(procs.active_profiles()):
                profile = find_profile(name)
                if profile is None:
                    stopped, _ = procs.stop_profile(name)
                else:
                    _results, stopped, _stubborn = stop_profile(profile)
                closed += stopped
                self.log(f"stopped {name}")
            return f"{closed} process(es) closed"

        self._work("Stop everything", job, self._refresh_launch)

    # ── Profiles ─────────────────────────────────────────────────────────
    def _build_profiles(self) -> None:
        kit, m = self.kit, self.m
        actions = kit.heading(
            self.content,
            "Profiles",
            "Run, stop and edit profiles, or record a new one from the apps you open.",
        )
        kit.button(
            actions,
            "New profile",
            self._new_profile,
            kind="primary",
            icon="plus",
            side="right",
            padx=0,
        )
        kit.button(
            actions,
            "Record",
            self._record_profile,
            icon="record",
            side="right",
            padx=(0, m.gap_sm),
        )

        bar = kit.frame(self.content)
        bar.pack(fill="x", side="bottom", pady=(m.gap, 0))
        kit.button(bar, "Run", lambda: self._run_selected(dry=False), kind="primary", icon="run")
        kit.button(bar, "Dry run", lambda: self._run_selected(dry=True))
        kit.button(bar, "Edit", self._edit_selected, icon="edit")
        kit.button(bar, "Switch to", self._switch_selected)
        kit.button(
            bar,
            "",
            self._refresh_profiles,
            kind="quiet",
            icon="refresh",
            tip="Reload profiles",
            side="right",
            padx=0,
        )
        kit.button(
            bar,
            "",
            self._open_profiles_dir,
            kind="quiet",
            icon="folder",
            tip="Open the profiles folder",
            side="right",
            padx=(0, m.gap_xs),
        )
        kit.button(
            bar,
            "",
            self._validate_all,
            kind="quiet",
            icon="check",
            tip="Validate every profile file",
            side="right",
            padx=(0, m.gap_xs),
        )
        kit.button(
            bar,
            "Stop",
            self._stop_selected,
            kind="danger",
            icon="stop",
            side="right",
            padx=(0, m.gap),
        )

        self.profile_list = kit.rows(
            self.content,
            empty="No profiles yet. Create one with New profile.",
            on_activate=lambda _i: self._edit_selected(),
        )
        self.profile_list.pack(fill="both", expand=True)
        self._refresh_profiles()

    def _refresh_profiles(self) -> None:
        self.profile_rows = model.profile_rows()
        rows = []
        for row in self.profile_rows:
            tags = []
            if row["running"]:
                tags.append(f"{row['running']} running")
            if row["default"]:
                tags.append("default")
            if row["hotkey"]:
                tags.append(ui.pretty_keys(row["hotkey"]))
            steps = row["steps"]
            rows.append(
                ui.ListRow(
                    title=row["name"],
                    detail=row["description"],
                    tags=tuple(tags),
                    meta=f"{steps} step" if steps == 1 else f"{steps} steps",
                )
            )
        self.profile_list.set_rows(rows)

    def _selected_profile(self) -> dict[str, Any] | None:
        selection = self.profile_list.curselection()
        if not selection or not self.profile_rows:
            self._set_status("Select a profile first.", self.pal.warn)
            return None
        return self.profile_rows[selection[0]]

    def _run_profile(self, name: str, dry: bool, then: Callable[[], None] | None = None) -> None:
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

        self._work(f"{'Dry run' if dry else 'Run'} {name}", job, then or self._refresh_profiles)

    def _run_selected(self, dry: bool) -> None:
        row = self._selected_profile()
        if row is not None:
            self._run_profile(row["name"], dry=dry)

    def _stop_profile(self, name: str, then: Callable[[], None] | None = None) -> None:
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

        self._work(f"Stop {name}", job, then or self._refresh_profiles)

    def _stop_selected(self) -> None:
        row = self._selected_profile()
        if row is not None:
            self._stop_profile(row["name"])

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
                self.log(f"stopped {other}")
            profile = find_profile(name)
            if profile is None:
                raise RuntimeError(f"profile '{name}' disappeared")
            results = run_profile(profile, on_result=lambda r: self.log(format_result(r)))
            failed = sum(1 for r in results if r.counts_as_failure)
            return f"{len(results) - failed} ok, {failed} failed"

        self._work(f"Switch to {name}", job, self._refresh_profiles)

    def _new_profile(self) -> None:
        from launcher.editor import new_profile_dialog

        path = new_profile_dialog(self.root)
        if path is None:
            return
        self.log(f"created {path}")
        self._refresh_profiles()

    def _edit_selected(self) -> None:
        row = self._selected_profile()
        if row is None or not row["path"]:
            return
        from pathlib import Path

        from launcher.editor import open_editor

        open_editor(Path(row["path"]), parent=self.root)
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

            self.log(f"recording for {seconds}s — open the apps you want in '{name}'")
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
                    self.log(f"  invalid  {path.name}: {exc}")
                    continue
                self.log(f"  valid    {path.name}: '{profile.name}' — {len(profile.steps)} steps")
            return f"{len(paths) - errors}/{len(paths)} profiles valid"

        self._work("Validate profiles", job)

    def _open_profiles_dir(self) -> None:
        from launcher.config import profiles_dir

        self._work("Open profiles folder", lambda: model.open_path(profiles_dir()))

    # ── Sync ─────────────────────────────────────────────────────────────
    def _build_sync(self) -> None:
        kit, pal, m = self.kit, self.pal, self.m
        kit.heading(
            self.content,
            "Sync",
            "Carry the profiles folder between machines, through git or as a plain copy.",
        )
        stored = model.settings.load()

        cards = kit.frame(self.content)
        cards.pack(fill="x")
        cards.columnconfigure((0, 1), weight=1, uniform="card")
        cards.rowconfigure(0, weight=1)

        git_card = kit.card(cards, pad=m.gap_lg)
        git_card.grid(row=0, column=0, sticky="nsew", padx=(0, m.gap_sm))
        git = git_card.inner
        kit.card_title(git, "Git repository", "Commit the folder and push it to a remote.")
        self.sync_remote = self._stacked_entry(
            git, "Remote URL", stored.sync_remote, "git@github.com:you/profiles.git"
        )
        self.sync_message = self._stacked_entry(git, "Commit message", "", "Update profiles")
        self.sync_push = kit.checkbox(git, "Push after committing", value=True, bg=pal.panel)
        self.sync_push.pack(anchor="w", pady=(m.gap_xs, 0))
        git_buttons = tk.Frame(git, bg=pal.panel)
        git_buttons.pack(fill="x", side="bottom", pady=(m.gap_lg, 0))
        kit.button(git_buttons, "Sync now", self._sync_now, kind="primary", icon="sync")
        kit.button(git_buttons, "Initialise", self._sync_init)

        mirror_card = kit.card(cards, pad=m.gap_lg)
        mirror_card.grid(row=0, column=1, sticky="nsew", padx=(m.gap_sm, 0))
        mirror = mirror_card.inner
        kit.card_title(mirror, "Mirror", "Copy to a folder, a drive or user@host:/path with rsync.")
        self.mirror_target = self._stacked_entry(
            mirror, "Target", stored.sync_mirror, "/media/usb/profiles"
        )
        options = tk.Frame(mirror, bg=pal.panel)
        options.pack(fill="x", pady=(m.gap_xs, 0))
        self.mirror_delete = kit.checkbox(options, "Delete extra files", value=True, bg=pal.panel)
        self.mirror_delete.pack(side="left", padx=(0, m.gap_lg))
        self.mirror_dry = kit.checkbox(options, "Dry run", value=False, bg=pal.panel)
        self.mirror_dry.pack(side="left")
        mirror_buttons = tk.Frame(mirror, bg=pal.panel)
        mirror_buttons.pack(fill="x", side="bottom", pady=(m.gap_lg, 0))
        kit.button(
            mirror_buttons, "Push", lambda: self._mirror(pull=False), kind="primary", icon="upload"
        )
        kit.button(mirror_buttons, "Pull", lambda: self._mirror(pull=True), icon="download")
        kit.button(
            mirror_buttons,
            "Remember target",
            self._remember_mirror,
            kind="quiet",
            side="right",
            padx=0,
        )

        status = kit.frame(self.content)
        status.pack(fill="x", pady=(m.gap_xl, m.gap_sm))
        kit.button(
            status,
            "",
            self._sync_status,
            kind="quiet",
            icon="refresh",
            tip="Check again",
            side="right",
            padx=0,
        )
        kit.label(status, "Repository status", size=SIZE_SMALL, bold=True).pack(side="left")
        self.sync_output = kit.textbox(self.content, height=6)
        self.sync_output.pack(fill="both", expand=True)
        self._sync_status()

    def _stacked_entry(
        self, parent: tk.Widget, label: str, value: str, placeholder: str = ""
    ) -> tk.Entry:
        """A label over a full-width field — the shape that fits a narrow card."""
        self.kit.label(
            parent, label, bg=ui.ground_of(parent), fg=self.pal.muted, size=SIZE_SMALL, anchor="w"
        ).pack(fill="x", pady=(0, self.m.gap_xs))
        entry = self.kit.entry(parent, value, width=10, placeholder=placeholder)
        entry.pack(fill="x", pady=(0, self.m.gap))
        return entry

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
        self.kit.fill(self.sync_output, text.splitlines())

    def _sync_now(self) -> None:
        message = self.sync_message.var.get().strip()
        push = bool(self.sync_push.var.get())

        def job() -> str:
            from launcher import sync

            return sync.sync(message=message, push=push)

        self._work("Sync profiles", job, self._sync_status)

    def _mirror(self, pull: bool) -> None:
        target = self.mirror_target.var.get().strip()
        delete = bool(self.mirror_delete.var.get())
        dry_run = bool(self.mirror_dry.var.get())

        def job() -> str:
            from launcher import sync

            return sync.mirror(target, pull=pull, delete=delete, dry_run=dry_run)

        self._work("Pull mirror" if pull else "Push mirror", job)

    def _remember_mirror(self) -> None:
        target = self.mirror_target.var.get().strip()
        model.settings.save({"sync_mirror": target})
        self._set_status(
            f"Mirror target saved: {target}" if target else "Mirror target cleared", self.pal.ok
        )

    # ── Activity ─────────────────────────────────────────────────────────
    def _build_activity(self) -> None:
        kit, m = self.kit, self.m
        actions = kit.heading(
            self.content, "Activity", "What ran, how long each step took, and what the log says."
        )
        kit.button(
            actions, "Clear history", self._clear_history, kind="quiet", side="right", padx=0
        )
        kit.button(
            actions,
            "",
            self._open_log_dir,
            kind="quiet",
            icon="folder",
            tip="Open the log folder",
            side="right",
            padx=(0, m.gap_xs),
        )

        controls = kit.frame(self.content)
        controls.pack(fill="x", pady=(0, m.gap))
        self.activity_view = tk.StringVar(value="runs")
        kit.segmented(
            controls,
            (("Runs", "runs"), ("Step timings", "stats"), ("Log", "log")),
            self.activity_view,
            self._refresh_activity,
        ).pack(side="left")
        kit.button(
            controls,
            "",
            self._refresh_activity,
            kind="quiet",
            icon="refresh",
            tip="Refresh",
            side="right",
            padx=0,
        )
        self.activity_limit = kit.entry(controls, "30", width=5)
        self.activity_limit.pack(side="right", padx=(0, m.gap_sm))
        self.activity_limit.bind("<Return>", lambda _e: self._refresh_activity())
        kit.label(controls, "Show last", fg=self.pal.muted, size=SIZE_SMALL).pack(
            side="right", padx=(0, m.gap_sm)
        )

        self.activity_output = kit.textbox(self.content, height=20)
        self.activity_output.pack(fill="both", expand=True)
        self._refresh_activity()

    def _refresh_activity(self) -> None:
        try:
            limit = max(1, int(self.activity_limit.var.get()))
        except ValueError:
            limit = 30
        view = self.activity_view.get()
        if view == "stats":
            lines, empty = model.stats_rows(limit=limit), "No history yet."
        elif view == "log":
            from launcher.logging_setup import tail

            lines, empty = tail(limit * 10), "Nothing logged yet."
        else:
            lines, empty = model.history_rows(limit=limit), "No history yet — run a profile first."
        self.kit.fill(self.activity_output, lines, empty)
        self.activity_output.see("end" if view == "log" else "1.0")

    def _clear_history(self) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno("Clear history", "Delete the run history?", parent=self.root):
            return
        from launcher.state import clear_history

        clear_history()
        self.log("history cleared")
        self._refresh_activity()

    def _open_log_dir(self) -> None:
        from launcher.logging_setup import log_dir

        log_dir().mkdir(parents=True, exist_ok=True)
        self._work("Open log folder", lambda: model.open_path(log_dir()))

    # ── Settings ─────────────────────────────────────────────────────────
    def _build_settings(self) -> None:
        kit, pal, m = self.kit, self.pal, self.m
        kit.heading(
            self.content,
            "Settings",
            "Saved to settings.yaml, the same keys `palaunch config set` writes.",
        )
        overridden = model.env_overrides()
        values = model.current_values()

        buttons = kit.frame(self.content)
        buttons.pack(fill="x", side="bottom", pady=(m.gap, 0))
        kit.button(buttons, "Save", self._save_settings, kind="primary", side="right", padx=0)
        kit.button(
            buttons, "Restore defaults", self._restore_defaults, side="right", padx=(0, m.gap_sm)
        )
        kit.button(buttons, "Reload from file", lambda: self._show("Settings"), kind="quiet")
        tk.Frame(self.content, bg=pal.border, height=1).pack(fill="x", side="bottom")

        form = kit.scrollable(self.content, self.root)
        for index, group in enumerate(model.GROUPS):
            label = kit.section_label(form, group.title)
            if index == 0:
                label.pack_configure(pady=(0, m.gap_sm))
            card = self._settings_card(form)
            for position, field in enumerate(group.fields):
                note, tone = field.help, pal.faint
                if field.key in overridden:
                    note = (
                        f"Set by ${overridden[field.key]}; the file value is ignored while it is."
                    )
                    tone = pal.warn
                row = self._settings_row(card, field.label, note, tone, first=position == 0)
                widget = self._field_widget(row, field, values[field.key])
                widget.grid(row=0, column=1, rowspan=2, sticky="e")
                self.widgets[field.key] = (widget, field)

        self._build_secrets(form)
        self._build_paths(form)
        tk.Frame(form, bg=pal.bg, height=m.gap).pack(fill="x")

    def _settings_card(self, parent: tk.Widget) -> tk.Frame:
        card = self.kit.card(parent, pad=0)
        card.pack(fill="x")
        return card.inner

    def _settings_row(
        self, card: tk.Widget, label: str, note: str = "", tone: str = "", first: bool = False
    ) -> tk.Frame:
        """One line of a settings card: the name and a line of help at the
        left, the control — gridded in by the caller at column 1 — at the right."""
        pal, m = self.pal, self.m
        if not first:
            tk.Frame(card, bg=pal.border, height=1).pack(fill="x", padx=m.pad)
        row = tk.Frame(card, bg=pal.panel, padx=m.pad, pady=m.gap - 1)
        row.pack(fill="x")
        row.columnconfigure(0, weight=1)
        self.kit.label(row, label, bg=pal.panel, size=SIZE_SMALL, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, m.gap_lg)
        )
        if note:
            self.kit.label(
                row,
                note,
                bg=pal.panel,
                fg=tone or pal.faint,
                size=SIZE_TINY,
                anchor="w",
                justify="left",
                wraplength=380,
            ).grid(row=1, column=0, sticky="w", padx=(0, m.gap_lg), pady=(2, 0))
        return row

    def _field_widget(self, parent: tk.Widget, field: model.Field, value: Any) -> tk.Widget:
        if field.kind == "bool":
            return self.kit.checkbox(parent, value=bool(value), bg=self.pal.panel)
        if field.kind == "choice":
            return self.kit.choice(parent, str(value), field.choices, width=14)
        width = 8 if field.kind == "int" else 26
        return self.kit.entry(parent, "" if value is None else str(value), width=width)

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
        if "theme" in diff:
            self.log("note: the new theme applies to windows opened from now on")

    def _restore_defaults(self) -> None:
        for key, value in model.defaults().items():
            widget, field = self.widgets[key]
            widget.var.set(value if field.kind == "bool" else str(value))
        self._set_status("Defaults filled in — press Save to write them.", self.pal.accent)

    # ── Settings ▸ secrets ───────────────────────────────────────────────
    def _build_secrets(self, parent: tk.Widget) -> None:
        kit, pal, m = self.kit, self.pal, self.m
        kit.section_label(parent, "Secrets")
        from launcher import secrets

        if not secrets.available():
            card = self._settings_card(parent)
            self._settings_row(
                card,
                "The keyring package is not installed",
                "Install it with `pip install profile-auto-launcher[secrets]` to keep "
                "passwords and tokens in the OS credential store.",
                first=True,
            )
            return
        kit.label(
            parent,
            "Kept in the OS credential store and used as {{ secret.NAME }} in a profile.",
            fg=pal.faint,
            size=SIZE_TINY,
            anchor="w",
        ).pack(fill="x", pady=(0, m.gap_sm))
        self.secret_list = kit.rows(parent, empty="No secrets stored yet.", height=120)
        self.secret_list.pack(fill="x")
        buttons = kit.frame(parent)
        buttons.pack(fill="x", pady=(m.gap_sm, 0))
        kit.button(buttons, "Add secret", self._add_secret, icon="plus")
        kit.button(buttons, "Remove", self._remove_secret, kind="danger", icon="trash")
        self._refresh_secrets()

    def _refresh_secrets(self) -> None:
        from launcher import secrets

        self.secret_names = secrets.list_names()
        self.secret_list.set_rows([ui.ListRow(title=name) for name in self.secret_names])

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

    # ── Settings ▸ paths ─────────────────────────────────────────────────
    def _build_paths(self, parent: tk.Widget) -> None:
        kit, pal, m = self.kit, self.pal, self.m
        kit.section_label(parent, "Hotkeys")
        card = self._settings_card(parent)
        for position, (combo, action) in enumerate(model.hotkey_pairs()):
            row = self._settings_row(card, action, first=position == 0)
            kit.keycap(row, ui.pretty_keys(combo), bg=pal.panel).grid(row=0, column=1, sticky="e")

        kit.section_label(parent, "Files")
        card = self._settings_card(parent)
        for position, (label, value) in enumerate(model.paths_report()):
            row = self._settings_row(card, label, first=position == 0)
            entry = kit.entry(row, value, width=52)
            entry.configure(state="readonly", fg=pal.muted)
            entry.grid(row=0, column=1, sticky="e")

        buttons = kit.frame(parent)
        buttons.pack(fill="x", pady=(m.gap, 0))
        kit.button(buttons, "Open config folder", self._open_config_dir, icon="folder")
        kit.button(buttons, "Write JSON Schema", self._write_schema)
        kit.button(buttons, "Install and autostart", self._open_installer, icon="package")

    def _write_schema(self) -> None:
        def job() -> str:
            from launcher import schema

            return f"wrote {schema.write()}"

        self._work("Write JSON Schema", job)

    def _open_config_dir(self) -> None:
        from launcher.config import config_dir

        config_dir().mkdir(parents=True, exist_ok=True)
        self._work("Open config folder", lambda: model.open_path(config_dir()))

    def _open_installer(self) -> None:
        from launcher.installer import open_installer

        open_installer(parent=self.root)

    # ── lifecycle ────────────────────────────────────────────────────────
    def run(self) -> None:
        ui.show_window(self.root, self.parent)


def open_panel(section: str = SECTIONS[0], parent: tk.Misc | None = None) -> int:
    """Open the manager window. Returns a process exit code."""
    try:
        panel = _Panel(parent)
    except tk.TclError as exc:
        print(f"Could not open the manager window: {exc}")
        return 1
    if section in SECTIONS:
        panel._show(section)
    panel.run()
    return 0
