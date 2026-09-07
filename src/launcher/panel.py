"""The manager window — everything the CLI can do, with a mouse.

Five pages, not seven: **Launch** (run a profile or one app by name),
**Profiles** (create, edit, record, validate), **Sync** (git and rsync),
**Activity** (run history, step timings and the log) and **Settings** (every
option, the credential store and the paths). Secrets and paths used to be
pages of their own; they are configuration, so they live with the settings.

Long-running actions (running a profile, syncing, recording) happen on a
worker thread and stream into the console at the bottom — Tk is
single-threaded, so workers only ever push text onto a queue that the UI
thread drains.
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

# The only icons in the window: one per navigation entry, repeated on that
# page's header. Rows, buttons and fields carry text alone.
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
        kit, pal = self.kit, self.pal
        # Bottom-anchored chrome is packed first: whatever is packed last gets
        # squeezed off the window when a page is taller than the frame.
        self.status = tk.Label(
            self.root,
            text="Ready.",
            bg=pal.panel,
            fg=pal.muted,
            font=kit.f(SIZE_TINY),
            anchor="w",
            padx=self.m.gap_xl,
            pady=7,
        )
        self.status.pack(fill="x", side="bottom")

        console_box = tk.Frame(self.root, bg=pal.bg, padx=self.m.gap_xl)
        console_box.pack(fill="x", side="bottom")
        kit.divider(console_box, pady=(0, self.m.gap_sm))
        kit.section_label(console_box, "console")
        self.console = kit.textbox(console_box, height=6)
        self.console.pack(fill="x", pady=(0, self.m.gap_sm))

        body = kit.frame(self.root)
        body.pack(fill="both", expand=True)

        nav = tk.Frame(body, bg=pal.panel, width=196, padx=self.m.gap, pady=self.m.gap_lg)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)

        brand = tk.Frame(nav, bg=pal.panel)
        brand.pack(fill="x", pady=(0, self.m.gap_lg))
        kit.brand(brand, 22, bg=pal.panel).pack(side="left", padx=(0, self.m.gap_sm))
        kit.label(brand, "palaunch", bg=pal.panel, size=SIZE_BODY, bold=True).pack(side="left")

        self.nav_buttons: dict[str, tuple[tk.Frame, tk.Canvas, tk.Label]] = {}
        for name in SECTIONS:
            item = tk.Frame(nav, bg=pal.panel, padx=self.m.gap_sm, pady=8, cursor="hand2")
            item.pack(fill="x", pady=1)
            glyph = kit.icon(item, NAV_ICONS.get(name, ""), 17, pal.muted, pal.panel)
            glyph.pack(side="left", padx=(2, self.m.gap_sm))
            label = kit.label(
                item, name, bg=pal.panel, fg=pal.muted, size=SIZE_SMALL, bold=True, anchor="w"
            )
            label.pack(side="left")
            for widget in (item, glyph, label):
                widget.bind("<Button-1>", lambda _e, n=name: self._show(n))
                widget.bind("<Enter>", lambda _e, n=name: self._hover_nav(n, True))
                widget.bind("<Leave>", lambda _e, n=name: self._hover_nav(n, False))
            self.nav_buttons[name] = (item, glyph, label)

        footer = tk.Frame(nav, bg=pal.panel)
        footer.pack(side="bottom", fill="x")
        kit.button(footer, "Open launcher", self._open_launcher, kind="ghost", pack=False).pack(
            fill="x"
        )
        kit.label(
            footer, f"version {model.version()}", bg=pal.panel, fg=pal.faint, size=SIZE_TINY
        ).pack(anchor="w", pady=(self.m.gap_sm, 0))

        self.content = tk.Frame(body, bg=pal.bg, padx=self.m.gap_xl, pady=self.m.gap_lg)
        self.content.pack(side="left", fill="both", expand=True)

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
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
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
        kit, pal = self.kit, self.pal
        kit.heading(
            self.content,
            "Launch",
            "Type a profile name to run the whole context, or an app name to open just that one.",
            icon=NAV_ICONS["Launch"],
        )

        search = kit.frame(self.content)
        search.pack(fill="x")
        self.launch_query = kit.entry(search, "", width=42, mono=False)
        self.launch_query.pack(side="left", ipady=5)
        self.launch_query.bind("<KeyRelease>", lambda _e: self._refresh_launch())
        self.launch_query.bind("<Return>", lambda _e: self._launch_selected())
        kit.button(search, "Launch", self._launch_selected, kind="primary", padx=(self.m.gap, 8))
        kit.button(search, "Dry run", lambda: self._launch_selected(dry=True))
        kit.button(search, "Refresh apps", self._refresh_apps, kind="quiet")

        self.launch_list = kit.listbox(self.content, height=12)
        self.launch_list.pack(fill="both", expand=True, pady=(self.m.gap, 0))
        self.launch_list.bind("<Double-Button-1>", lambda _e: self._launch_selected())

        running = kit.frame(self.content)
        running.pack(fill="x", pady=(self.m.gap, 0))
        kit.section_label(running, "running now")
        self.running_label = kit.label(running, "", fg=pal.muted, size=SIZE_SMALL, anchor="w")
        self.running_label.pack(fill="x")
        buttons = kit.frame(running)
        buttons.pack(fill="x", pady=(self.m.gap_sm, 0))
        kit.button(buttons, "Stop selected", self._stop_launch_selection, kind="danger")
        kit.button(buttons, "Stop everything", self._stop_everything, kind="quiet")
        self._refresh_launch()

    def _refresh_launch(self) -> None:
        query = self.launch_query.var.get().strip()
        self.launch_rows = model.launch_rows(query)
        self.launch_list.delete(0, "end")
        for row in self.launch_rows:
            kind = "profile" if row["kind"] == "profile" else "app"
            self.launch_list.insert(
                "end", f"  {kind:<8} {row['name'][:26]:<26} {row['detail'][:58]}"
            )
        if self.launch_rows:
            self.launch_list.selection_set(0)
        else:
            self.launch_list.insert("end", "  nothing matches — try part of a name")
        self._refresh_running()

    def _refresh_running(self) -> None:
        lines = model.running_rows()
        self.running_label.configure(
            text="   ".join(lines) if lines else "nothing the launcher started is running"
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
        kit = self.kit
        kit.heading(
            self.content,
            "Profiles",
            "Everything `palaunch list / run / stop / edit` does.",
            icon=NAV_ICONS["Profiles"],
        )
        self.profile_list = kit.listbox(self.content, height=13)
        self.profile_list.pack(fill="both", expand=True)
        self.profile_list.bind("<Double-Button-1>", lambda _e: self._edit_selected())

        row1 = kit.frame(self.content, pady=self.m.gap)
        row1.pack(fill="x")
        kit.button(row1, "Run", lambda: self._run_selected(dry=False), kind="primary")
        kit.button(row1, "Dry run", lambda: self._run_selected(dry=True))
        kit.button(row1, "Stop", self._stop_selected, kind="danger")
        kit.button(row1, "Switch to", self._switch_selected)

        row2 = kit.frame(self.content)
        row2.pack(fill="x")
        kit.button(row2, "New…", self._new_profile, kind="primary")
        kit.button(row2, "Edit…", self._edit_selected)
        kit.button(row2, "Record…", self._record_profile)
        kit.button(row2, "Validate all", self._validate_all, kind="quiet")
        kit.button(row2, "Open folder", self._open_profiles_dir, kind="quiet")
        kit.button(row2, "Refresh", self._refresh_profiles, kind="quiet")
        self._refresh_profiles()

    def _refresh_profiles(self) -> None:
        self.profile_rows = model.profile_rows()
        self.profile_list.delete(0, "end")
        for row in self.profile_rows:
            marker = "default" if row["default"] else ""
            running = f"  {row['running']} running" if row["running"] else ""
            hotkey = f"  [{row['hotkey']}]" if row["hotkey"] else ""
            self.profile_list.insert(
                "end",
                f"  {row['name']:<18} {row['steps']:>2} steps  "
                f"{row['description'][:34]:<34}{marker:<8}{hotkey}{running}",
            )
        if self.profile_rows:
            self.profile_list.selection_set(0)
        else:
            self.profile_list.insert("end", "  no profiles yet — press New")

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
        kit = self.kit
        kit.heading(
            self.content,
            "Sync",
            "Two ways to carry the profiles directory between machines.",
            icon=NAV_ICONS["Sync"],
        )

        git_card = kit.card(self.content)
        git_card.pack(fill="x")
        git = git_card.inner
        kit.label(git, "Git repository", bg=self.pal.panel, size=SIZE_BODY, bold=True).pack(
            anchor="w", pady=(0, self.m.gap_sm)
        )
        form = tk.Frame(git, bg=self.pal.panel)
        form.pack(fill="x")
        self.sync_remote = self._labelled_entry(
            form, 0, "Remote URL", model.settings.load().sync_remote, width=52
        )
        self.sync_message = self._labelled_entry(form, 1, "Commit message", "", width=52)
        self.sync_push = kit.checkbox(form, "Push after committing", value=True)
        self.sync_push.configure(bg=self.pal.panel, activebackground=self.pal.panel)
        self.sync_push.grid(row=2, column=1, sticky="w", pady=3)

        git_buttons = tk.Frame(git, bg=self.pal.panel)
        git_buttons.pack(fill="x", pady=(self.m.gap, 0))
        kit.button(git_buttons, "Sync now", self._sync_now, kind="primary")
        kit.button(git_buttons, "Initialise repo", self._sync_init)
        kit.button(git_buttons, "Status", self._sync_status, kind="quiet")

        mirror_card = kit.card(self.content)
        mirror_card.pack(fill="x", pady=(self.m.gap, 0))
        mirror = mirror_card.inner
        kit.label(mirror, "rsync mirror", bg=self.pal.panel, size=SIZE_BODY, bold=True).pack(
            anchor="w"
        )
        kit.label(
            mirror,
            "A folder, a USB stick or user@host:/path. Uses rsync when installed, "
            "and a built-in mirror for local paths when it is not.",
            bg=self.pal.panel,
            fg=self.pal.muted,
            size=SIZE_TINY,
            anchor="w",
        ).pack(fill="x", pady=(2, self.m.gap_sm))
        mirror_form = tk.Frame(mirror, bg=self.pal.panel)
        mirror_form.pack(fill="x")
        self.mirror_target = self._labelled_entry(
            mirror_form, 0, "Mirror target", model.settings.load().sync_mirror, width=52
        )
        options = tk.Frame(mirror_form, bg=self.pal.panel)
        options.grid(row=1, column=1, sticky="w", pady=3)
        self.mirror_delete = kit.checkbox(options, "Delete extra files", value=True)
        self.mirror_delete.configure(bg=self.pal.panel, activebackground=self.pal.panel)
        self.mirror_delete.pack(side="left", padx=(0, self.m.gap))
        self.mirror_dry = kit.checkbox(options, "Dry run", value=False)
        self.mirror_dry.configure(bg=self.pal.panel, activebackground=self.pal.panel)
        self.mirror_dry.pack(side="left")

        mirror_buttons = tk.Frame(mirror, bg=self.pal.panel)
        mirror_buttons.pack(fill="x", pady=(self.m.gap, 0))
        kit.button(mirror_buttons, "Push →", lambda: self._mirror(pull=False), kind="primary")
        kit.button(mirror_buttons, "← Pull", lambda: self._mirror(pull=True))
        kit.button(mirror_buttons, "Remember target", self._remember_mirror, kind="quiet")

        self.sync_output = kit.textbox(self.content, height=8)
        self.sync_output.pack(fill="both", expand=True, pady=(self.m.gap, 0))
        self._sync_status()

    def _labelled_entry(
        self, parent: tk.Widget, row: int, label: str, value: str, width: int = 40
    ) -> tk.Entry:
        self.kit.label(
            parent, label, bg=self.pal.panel, size=SIZE_SMALL, width=16, anchor="w"
        ).grid(row=row, column=0, sticky="w", pady=3)
        entry = self.kit.entry(parent, value, width=width)
        entry.grid(row=row, column=1, sticky="w", pady=3)
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
        self._set_status(f"Mirror target saved: {target or '(none)'}", self.pal.ok)

    # ── Activity ─────────────────────────────────────────────────────────
    def _build_activity(self) -> None:
        kit = self.kit
        kit.heading(
            self.content,
            "Activity",
            "What ran, how long it took, and what the log says.",
            icon=NAV_ICONS["Activity"],
        )
        controls = kit.frame(self.content)
        controls.pack(fill="x", pady=(0, self.m.gap_sm))
        self.activity_view = tk.StringVar(value="runs")
        kit.segmented(
            controls,
            (("Runs", "runs"), ("Step stats", "stats"), ("Log", "log")),
            self.activity_view,
            self._refresh_activity,
        ).pack(side="left")
        kit.label(controls, "  limit", fg=self.pal.muted, size=SIZE_SMALL).pack(side="left")
        self.activity_limit = kit.entry(controls, "30", width=6)
        self.activity_limit.pack(side="left", padx=self.m.gap_sm)
        kit.button(controls, "Refresh", self._refresh_activity, kind="quiet")
        kit.button(controls, "Clear history", self._clear_history, kind="quiet")
        kit.button(controls, "Open log folder", self._open_log_dir, kind="quiet")

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
        kit, pal = self.kit, self.pal
        kit.heading(
            self.content,
            "Settings",
            "Written to settings.yaml — the same keys `palaunch config set` writes.",
            icon=NAV_ICONS["Settings"],
        )
        overridden = model.env_overrides()
        values = model.current_values()

        buttons = kit.frame(self.content, pady=self.m.gap)
        buttons.pack(fill="x", side="bottom")
        kit.button(buttons, "Save", self._save_settings, kind="primary")
        kit.button(buttons, "Restore defaults", self._restore_defaults)
        kit.button(buttons, "Reload from file", lambda: self._show("Settings"), kind="quiet")

        form = kit.scrollable(self.content, self.root)
        for group in model.GROUPS:
            kit.section_label(form, group.title)
            grid = kit.frame(form)
            grid.pack(fill="x")
            grid.columnconfigure(2, weight=1)
            for row, field in enumerate(group.fields):
                kit.label(grid, field.label, size=SIZE_SMALL, anchor="w", width=26).grid(
                    row=row, column=0, sticky="w", pady=3
                )
                widget = self._field_widget(grid, field, values[field.key])
                widget.grid(row=row, column=1, sticky="w", pady=3)
                self.widgets[field.key] = (widget, field)
                note = field.help
                if field.key in overridden:
                    note = (
                        f"forced by ${overridden[field.key]} — the file is ignored while it is set"
                    )
                if note:
                    kit.label(
                        grid,
                        note,
                        fg=pal.warn if field.key in overridden else pal.faint,
                        size=SIZE_TINY,
                        anchor="w",
                    ).grid(row=row, column=2, sticky="w", padx=(self.m.gap, 0))

        self._build_secrets(form)
        self._build_paths(form)

    def _field_widget(self, parent: tk.Widget, field: model.Field, value: Any) -> tk.Widget:
        if field.kind == "bool":
            widget = self.kit.checkbox(parent, value=bool(value))
            return widget
        if field.kind == "choice":
            return self.kit.choice(parent, str(value), field.choices)
        return self.kit.entry(parent, "" if value is None else str(value), width=34)

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
        kit = self.kit
        kit.section_label(parent, "secrets")
        from launcher import secrets

        if not secrets.available():
            kit.label(
                parent,
                "keyring is not installed — run `pip install profile-auto-launcher[secrets]`",
                fg=self.pal.warn,
                size=SIZE_SMALL,
                anchor="w",
            ).pack(fill="x")
            return
        kit.label(
            parent,
            "Stored in the OS credential store; used as {{ secret.NAME }} in a profile.",
            fg=self.pal.faint,
            size=SIZE_TINY,
            anchor="w",
        ).pack(fill="x", pady=(0, self.m.gap_sm))
        self.secret_list = kit.listbox(parent, height=5)
        self.secret_list.pack(fill="x")
        buttons = kit.frame(parent, pady=self.m.gap_sm)
        buttons.pack(fill="x")
        kit.button(buttons, "Add…", self._add_secret)
        kit.button(buttons, "Remove", self._remove_secret, kind="danger")
        kit.button(buttons, "Refresh", self._refresh_secrets, kind="quiet")
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

    # ── Settings ▸ paths ─────────────────────────────────────────────────
    def _build_paths(self, parent: tk.Widget) -> None:
        kit = self.kit
        kit.section_label(parent, "paths and hotkeys")
        grid = kit.frame(parent)
        grid.pack(fill="x")
        for row, (label, value) in enumerate(model.paths_report()):
            kit.label(grid, label, fg=self.pal.muted, size=SIZE_TINY, width=18, anchor="w").grid(
                row=row, column=0, sticky="w", pady=2
            )
            entry = kit.entry(grid, value, width=64)
            entry.configure(state="readonly")
            entry.grid(row=row, column=1, sticky="w", pady=2)

        hotkeys = kit.frame(parent)
        hotkeys.pack(fill="x", pady=(self.m.gap_sm, 0))
        for line in model.hotkey_rows():
            kit.label(hotkeys, f"  {line}", mono=True, size=SIZE_TINY, anchor="w").pack(fill="x")

        buttons = kit.frame(parent, pady=self.m.gap)
        buttons.pack(fill="x")
        kit.button(buttons, "Write JSON Schema", self._write_schema)
        kit.button(buttons, "Open config folder", self._open_config_dir, kind="quiet")
        kit.button(buttons, "Install / autostart…", self._open_installer, kind="quiet")

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
