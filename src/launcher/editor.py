"""Creating and editing profiles in a window.

Two surfaces live here. The **wizard** turns a handful of answers — a preset,
some apps, some pages, what to close first — into a working profile, because
"here is an empty YAML file, good luck" was the weakest part of the launcher.
The **editor** then edits any profile step by step.

Both operate on the raw YAML mapping rather than on the parsed `Profile`, so
keys they don't know about survive a save untouched. Comments do not — the
editor rewrites the file — which is why it says so out loud. Every step is
validated through the real loader before anything is written, so neither can
produce a profile that `palaunch validate` would reject.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any

import yaml

from launcher import scaffold, theme, ui
from launcher.config import (
    KNOWN_FILE_ACTIONS,
    KNOWN_STEP_TYPES,
    Profile,
    _coerce_step,
    _read_raw,
    profiles_dir,
)
from launcher.executor import describe_step
from launcher.theme import SIZE_BODY, SIZE_SMALL, SIZE_TINY, SIZE_TITLE
from launcher.ui import Kit

# Per-type form layout: (yaml key, label, kind).
# kind: str | text | bool | number | list | dict | choice:<options>
_COMMON_TAIL = [
    ("enabled", "Enabled", "bool"),
    ("optional", "Optional (failure ignored)", "bool"),
    ("parallel", "Run in parallel with neighbours", "bool"),
    ("timeout", "Timeout (s)", "number"),
    ("retries", "Retries", "number"),
    ("retry_delay", "Retry delay (s)", "number"),
    ("depends_on", "Depends on (step ids)", "list"),
]

FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "app": [
        ("path", "Executable path", "str"),
        ("args", "Arguments (comma sep.)", "list"),
        ("cwd", "Working directory", "str"),
        ("detach", "Detach (launch and forget)", "bool"),
        ("track", "Track pid for `palaunch stop`", "bool"),
    ],
    "command": [
        ("run", "Command (comma sep. argv)", "list"),
        ("cwd", "Working directory", "str"),
        ("detach", "Detach (don't wait)", "bool"),
        ("track", "Track pid for `palaunch stop`", "bool"),
    ],
    "script": [
        ("script", "Script body", "text"),
        ("shell", "Shell", "choice:auto,bash,sh,zsh,powershell,cmd"),
        ("cwd", "Working directory", "str"),
    ],
    "url": [("url", "URL", "str")],
    "env": [
        ("set", "Set (KEY=VALUE per line)", "dict"),
        ("unset", "Unset (comma separated)", "list"),
    ],
    "kill": [("process", "Process name", "str")],
    "wait": [("seconds", "Seconds", "number")],
    "wait_for": [
        ("url", "URL (tcp:// or http://)", "str"),
        ("run", "…or command (argv)", "list"),
        ("interval", "Poll interval (s)", "number"),
    ],
    "profile": [("profile", "Profile name", "str")],
    "notify": [("title", "Title", "str"), ("message", "Message", "str")],
    "http": [
        ("url", "URL", "str"),
        ("method", "Method", "choice:GET,POST,PUT,PATCH,DELETE,HEAD"),
        ("headers", "Headers (KEY=VALUE per line)", "dict"),
        ("body", "Body", "text"),
        ("expect_status", "Expected status codes", "list"),
    ],
    "plugin": [
        ("plugin", "Executable", "str"),
        ("args", "Arguments (comma sep.)", "list"),
        ("config", "Config (KEY=VALUE per line)", "dict"),
    ],
    "file": [
        ("action", "Action", "choice:" + ",".join(sorted(KNOWN_FILE_ACTIONS))),
        ("src", "Source", "str"),
        ("dest", "Destination", "str"),
        ("content", "Content (write/append)", "text"),
    ],
    "rsync": [
        ("src", "Source directory", "str"),
        ("dest", "Destination (path or user@host:/path)", "str"),
        ("delete", "Delete files the source no longer has", "bool"),
        ("exclude", "Exclude (globs)", "list"),
        ("backend", "Backend", "choice:auto,rsync,builtin"),
    ],
}

# Step booleans the loader defaults to true when the key is absent.
DEFAULT_TRUE = ("enabled", "detach", "track")

PROFILE_FIELDS = [
    ("name", "Name", "str"),
    ("description", "Description", "str"),
    ("icon", "Icon (emoji)", "str"),
    ("tags", "Tags (comma separated)", "list"),
    ("hotkey", "Hotkey (e.g. <ctrl>+<alt>+d)", "str"),
    ("default", "Default profile", "bool"),
    ("autostart", "Run at login (tray)", "bool"),
]


def _split_list(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_pairs(text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key.strip():
            pairs[key.strip()] = value.strip()
    return pairs


def _format_pairs(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return "\n".join(f"{k}={v}" for k, v in value.items())


def _format_list(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)


class _Base:
    """Shared plumbing: the kit, the window, and modal bookkeeping."""

    def __init__(self, parent: tk.Misc | None, title: str, geometry: str) -> None:
        self.kit = Kit()
        self.pal = self.kit.pal
        self.m = theme.METRICS
        self.parent = parent
        self.root = ui.new_window(parent)
        self.kit.chrome(self.root, title)
        self.root.geometry(geometry)

    def run(self) -> None:
        ui.show_window(self.root, self.parent)


# ── the new-profile wizard ───────────────────────────────────────────────


class _Wizard(_Base):
    """Name it, pick what it opens, get a profile that already works."""

    def __init__(self, parent: tk.Misc | None = None) -> None:
        super().__init__(parent, "New profile", "780x740")
        self.result: Path | None = None
        self.chosen_apps: list[str] = []
        self.matches: list[str] = []
        self._build()

    def _build(self) -> None:
        kit, pal = self.kit, self.pal
        # Buttons are packed before the body so a tall form never pushes them
        # off the bottom of the window.
        buttons = kit.frame(self.root, padx=self.m.gap_xl, pady=self.m.gap)
        buttons.pack(fill="x", side="bottom")
        kit.button(buttons, "Create", lambda: self._create(edit=False), kind="primary")
        kit.button(buttons, "Create and edit steps", lambda: self._create(edit=True))
        kit.button(buttons, "Cancel", self.root.destroy, kind="quiet")

        self.error = kit.label(
            self.root, "", fg=self.pal.err, size=SIZE_SMALL, anchor="w", padx=self.m.gap_xl
        )
        self.error.pack(fill="x", side="bottom")

        outer = kit.frame(self.root, padx=self.m.gap_xl, pady=self.m.gap_lg)
        outer.pack(fill="both", expand=True)

        kit.label(outer, "New profile", size=SIZE_TITLE, bold=True).pack(anchor="w")
        kit.label(
            outer,
            "A profile opens a whole context at once. Start from a preset, then say what it opens.",
            fg=pal.muted,
            size=SIZE_SMALL,
        ).pack(anchor="w", pady=(2, self.m.gap))

        presets = kit.frame(outer)
        presets.pack(fill="x", pady=(0, self.m.gap))
        for item in scaffold.PRESETS:
            kit.button(
                presets,
                f"{item.icon}  {item.title}",
                lambda p=item: self._apply_preset(p),
                kind="quiet",
            )

        form = kit.frame(outer)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.fields: dict[str, tk.Entry] = {}
        for row, (key, label) in enumerate(
            (
                ("name", "Name"),
                ("description", "Description"),
                ("icon", "Icon (emoji)"),
                ("tags", "Tags (comma separated)"),
                ("hotkey", "Hotkey (optional)"),
            )
        ):
            kit.label(form, label, size=SIZE_SMALL, width=22, anchor="w").grid(
                row=row, column=0, sticky="w", pady=3
            )
            entry = kit.entry(form, "", width=46, mono=False)
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            self.fields[key] = entry
        self.fields["name"].focus_set()

        apps_card = kit.card(outer)
        apps_card.pack(fill="both", expand=True, pady=(self.m.gap, 0))
        apps_box = apps_card.inner
        kit.label(apps_box, "Applications to open", bg=pal.panel, size=SIZE_BODY, bold=True).pack(
            anchor="w"
        )
        search = tk.Frame(apps_box, bg=pal.panel)
        search.pack(fill="x", pady=(self.m.gap_sm, 0))
        self.app_query = kit.entry(search, "", width=32, mono=False)
        self.app_query.pack(side="left", ipady=3)
        self.app_query.bind("<KeyRelease>", lambda _e: self._search_apps())
        self.app_query.bind("<Return>", lambda _e: self._add_app())
        kit.button(search, "Add", self._add_app, kind="primary", padx=(self.m.gap_sm, 6))
        kit.button(search, "Remove", self._remove_app, kind="quiet")

        columns = tk.Frame(apps_box, bg=pal.panel)
        columns.pack(fill="both", expand=True, pady=(self.m.gap_sm, 0))
        left = tk.Frame(columns, bg=pal.panel)
        left.pack(side="left", fill="both", expand=True)
        kit.label(left, "installed", bg=pal.panel, fg=pal.faint, size=SIZE_TINY, anchor="w").pack(
            fill="x"
        )
        self.match_list = kit.listbox(left, height=6)
        self.match_list.pack(fill="both", expand=True)
        self.match_list.bind("<Double-Button-1>", lambda _e: self._add_app())
        right = tk.Frame(columns, bg=pal.panel)
        right.pack(side="left", fill="both", expand=True, padx=(self.m.gap_sm, 0))
        kit.label(
            right, "this profile opens", bg=pal.panel, fg=pal.faint, size=SIZE_TINY, anchor="w"
        ).pack(fill="x")
        self.chosen_list = kit.listbox(right, height=6)
        self.chosen_list.pack(fill="both", expand=True)

        extras = kit.frame(outer)
        extras.pack(fill="x", pady=(self.m.gap, 0))
        extras.columnconfigure(1, weight=1)
        kit.label(extras, "Pages to open (one per line)", size=SIZE_SMALL, anchor="nw").grid(
            row=0, column=0, sticky="nw", pady=3
        )
        self.urls = tk.Text(
            extras,
            height=3,
            bg=pal.field_bg,
            fg=pal.fg,
            insertbackground=pal.accent,
            relief="flat",
            font=kit.fm(SIZE_SMALL),
            highlightthickness=1,
            highlightbackground=pal.field_border,
        )
        self.urls.grid(row=0, column=1, sticky="ew", pady=3)
        kit.label(extras, "Close first (comma separated)", size=SIZE_SMALL, anchor="w").grid(
            row=1, column=0, sticky="w", pady=3
        )
        self.close = kit.entry(extras, "", width=46, mono=False)
        self.close.grid(row=1, column=1, sticky="ew", pady=3)

        toggles = kit.frame(outer)
        toggles.pack(fill="x", pady=(self.m.gap_sm, 0))
        self.tile = kit.checkbox(toggles, "Tile the first two windows side by side", value=True)
        self.tile.pack(anchor="w")
        self.make_default = kit.checkbox(toggles, "Make this the default profile", value=False)
        self.make_default.pack(anchor="w")
        self.notify_stop = kit.checkbox(toggles, "Notify when it is stopped", value=False)
        self.notify_stop.pack(anchor="w")

        self.root.bind("<Escape>", lambda _e: self.root.destroy())

    # ── preset and app pickers ───────────────────────────────────────────
    def _apply_preset(self, item: scaffold.Preset) -> None:
        if not self.fields["name"].var.get().strip():
            self.fields["name"].var.set(item.title)
        self.fields["icon"].var.set(item.icon)
        self.fields["description"].var.set(item.description)
        self.fields["tags"].var.set(", ".join(item.tags))
        self.close.var.set(", ".join(item.close))

    def _search_apps(self) -> None:
        from launcher import apps

        query = self.app_query.var.get().strip()
        self.matches = [app.name for app in apps.search(query, limit=12)] if query else []
        self.match_list.delete(0, "end")
        for name in self.matches:
            self.match_list.insert("end", f"  {name}")
        if not self.matches:
            self.match_list.insert("end", "  type to find installed applications")

    def _add_app(self) -> None:
        selection = self.match_list.curselection()
        if selection and self.matches:
            name = self.matches[selection[0]]
        else:
            name = self.app_query.var.get().strip()
        if not name:
            return
        if name not in self.chosen_apps:
            self.chosen_apps.append(name)
            self.chosen_list.insert("end", f"  {name}")
        self.app_query.var.set("")
        self._search_apps()

    def _remove_app(self) -> None:
        selection = self.chosen_list.curselection()
        if not selection:
            return
        index = selection[0]
        self.chosen_list.delete(index)
        del self.chosen_apps[index]

    # ── creation ─────────────────────────────────────────────────────────
    def draft(self) -> scaffold.Draft:
        return scaffold.Draft(
            name=self.fields["name"].var.get().strip(),
            description=self.fields["description"].var.get().strip(),
            icon=self.fields["icon"].var.get().strip(),
            tags=_split_list(self.fields["tags"].var.get()),
            hotkey=self.fields["hotkey"].var.get().strip(),
            apps=list(self.chosen_apps),
            urls=_split_lines(self.urls.get("1.0", "end")),
            close=_split_list(self.close.var.get()),
            tile=bool(self.tile.var.get()),
            default=bool(self.make_default.var.get()),
            notify_on_stop=bool(self.notify_stop.var.get()),
        )

    def _create(self, edit: bool) -> None:
        draft = self.draft()
        if not draft.name:
            self.error.configure(text="A profile needs a name.")
            return
        try:
            path = scaffold.write(draft)
        except FileExistsError:
            if not messagebox.askyesno(
                "Profile exists",
                f"{scaffold.profile_path(draft.name).name} already exists. Replace it?",
                parent=self.root,
            ):
                return
            path = scaffold.write(draft, overwrite=True)
        except (ValueError, OSError) as exc:
            self.error.configure(text=str(exc))
            return
        self.result = path
        self.root.destroy()
        if edit:
            open_editor(path, parent=self.parent)


def new_profile_dialog(parent: tk.Misc | None = None) -> Path | None:
    """Run the wizard. Returns the new profile's path, or None if cancelled."""
    wizard = _Wizard(parent)
    wizard.run()
    return wizard.result


# ── the step editor ──────────────────────────────────────────────────────


class _Editor(_Base):
    def __init__(self, path: Path, parent: tk.Misc | None = None) -> None:
        super().__init__(parent, f"Edit profile — {path.name}", "1060x740")
        self.path = path
        self.data: dict[str, Any] = _read_raw(path) if path.is_file() else {"steps": []}
        self.data.setdefault("steps", [])
        self.section = "steps"  # steps | teardown
        self.selected = 0
        self.widgets: dict[str, Any] = {}
        self.saved = False
        self._build()
        self._refresh_list()

    # ── layout ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        kit, pal = self.kit, self.pal
        top = kit.frame(self.root, padx=self.m.gap_lg, pady=self.m.gap)
        top.pack(fill="x")
        kit.brand(top, 20).pack(side="left", padx=(0, self.m.gap_sm))
        kit.label(top, self.path.name, size=SIZE_TITLE, bold=True).pack(side="left")

        kit.button(top, "Close", self.root.destroy, kind="quiet", pack=False).pack(side="right")
        kit.button(top, "Save", self._save, kind="primary", pack=False).pack(
            side="right", padx=(0, self.m.gap_sm)
        )

        self.section_var = tk.StringVar(value="steps")
        kit.segmented(
            top,
            (("Steps", "steps"), ("Teardown", "teardown")),
            self.section_var,
            self._switch_section,
        ).pack(side="right", padx=self.m.gap_lg)

        self.hint = kit.label(
            self.root,
            "Saving rewrites the file — YAML comments are not preserved.",
            fg=pal.faint,
            size=SIZE_TINY,
            anchor="w",
            padx=self.m.gap_lg,
            pady=self.m.gap_sm,
        )
        self.hint.pack(fill="x", side="bottom")

        columns = kit.frame(self.root, padx=self.m.gap_lg, pady=self.m.gap_sm)
        columns.pack(fill="both", expand=True)

        left = kit.frame(columns, width=330)
        left.pack(side="left", fill="both")
        left.pack_propagate(False)

        self.listbox = kit.listbox(left, height=18)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        buttons = kit.frame(left, pady=self.m.gap_sm)
        buttons.pack(fill="x")
        kit.button(buttons, "+ Add", self._add_step, kind="primary")
        kit.button(buttons, "− Remove", self._remove_step, kind="danger")
        kit.button(buttons, "↑", lambda: self._move_step(-1), kind="quiet")
        kit.button(buttons, "↓", lambda: self._move_step(1), kind="quiet")

        holder = kit.frame(columns, padx=self.m.gap_lg)
        holder.pack(side="left", fill="both", expand=True)
        # The form outgrows the window on a step with every option set, so the
        # right-hand column scrolls rather than losing its last rows.
        right = kit.scrollable(holder, self.root)

        self.profile_box = kit.frame(right)
        self.profile_box.pack(fill="x", pady=(0, self.m.gap_sm))
        self._build_profile_fields()

        kit.divider(right, pady=(self.m.gap_sm, self.m.gap_sm))
        self.form = kit.frame(right)
        self.form.pack(fill="both", expand=True)

    def _build_profile_fields(self) -> None:
        kit = self.kit
        kit.section_label(self.profile_box, "profile")
        grid = kit.frame(self.profile_box)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        self.profile_widgets: dict[str, Any] = {}
        for row, (key, label, kind) in enumerate(PROFILE_FIELDS):
            kit.label(grid, label, size=SIZE_SMALL, anchor="w", width=26).grid(
                row=row, column=0, sticky="w", pady=1
            )
            widget = self._make_widget(grid, kind, self.data.get(key))
            widget.grid(row=row, column=1, sticky="ew", pady=1)
            self.profile_widgets[key] = (widget, kind)

    def _make_widget(self, parent: tk.Widget, kind: str, value: Any) -> tk.Widget:
        kit, pal = self.kit, self.pal
        if kind == "bool":
            return kit.checkbox(parent, value=bool(value))
        if kind.startswith("choice:"):
            options = kind.split(":", 1)[1].split(",")
            return kit.choice(parent, str(value) if value else options[0], options)
        if kind in ("text", "dict"):
            widget = tk.Text(
                parent,
                height=5,
                bg=pal.field_bg,
                fg=pal.fg,
                relief="flat",
                insertbackground=pal.accent,
                font=kit.fm(SIZE_SMALL),
                highlightthickness=1,
                highlightbackground=pal.field_border,
            )
            content = (
                _format_pairs(value) if kind == "dict" else ("" if value is None else str(value))
            )
            widget.insert("1.0", content)
            return widget
        text = _format_list(value) if kind == "list" else ("" if value is None else str(value))
        return kit.entry(parent, text, width=40)

    def _widget_value(self, widget: tk.Widget, kind: str) -> Any:
        if kind == "bool":
            return bool(widget.var.get())  # type: ignore[attr-defined]
        if kind.startswith("choice:"):
            return widget.var.get()  # type: ignore[attr-defined]
        if kind == "dict":
            return _parse_pairs(widget.get("1.0", "end"))
        if kind == "text":
            return widget.get("1.0", "end").rstrip("\n")
        raw = widget.var.get().strip()  # type: ignore[attr-defined]
        if kind == "list":
            return _split_list(raw)
        if kind == "number":
            if not raw:
                return None
            try:
                number = float(raw)
            except ValueError:
                return None
            return int(number) if number.is_integer() else number
        return raw

    # ── step list ────────────────────────────────────────────────────────
    @property
    def steps(self) -> list[dict[str, Any]]:
        return self.data.setdefault(self.section, [])

    def _switch_section(self) -> None:
        self._commit_form()
        self.section = self.section_var.get()
        self.data.setdefault(self.section, [])
        self.selected = 0
        self._refresh_list()

    def _refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        for index, raw in enumerate(self.steps):
            try:
                summary = describe_step(_coerce_step(raw))[:44]
            except ValueError:
                summary = "(invalid)"
            label = raw.get("name") or raw.get("type", "?")
            self.listbox.insert("end", f"{index + 1:>2}. {label[:18]:<18} {summary}")
        if self.steps:
            self.selected = min(self.selected, len(self.steps) - 1)
            self.listbox.selection_set(self.selected)
        self._build_form()

    def _on_select(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection or selection[0] == self.selected:
            return
        self._commit_form()
        self.selected = selection[0]
        self._build_form()

    def _add_step(self) -> None:
        self._commit_form()
        self.steps.append({"type": "app", "name": "new step"})
        self.selected = len(self.steps) - 1
        self._refresh_list()

    def _remove_step(self) -> None:
        if not self.steps:
            return
        self.steps.pop(self.selected)
        self.selected = max(0, self.selected - 1)
        self._refresh_list()

    def _move_step(self, delta: int) -> None:
        target = self.selected + delta
        if not self.steps or not 0 <= target < len(self.steps):
            return
        self._commit_form()
        self.steps[self.selected], self.steps[target] = (
            self.steps[target],
            self.steps[self.selected],
        )
        self.selected = target
        self._refresh_list()

    # ── step form ────────────────────────────────────────────────────────
    def _build_form(self) -> None:
        kit = self.kit
        for child in self.form.winfo_children():
            child.destroy()
        self.widgets.clear()
        if not self.steps:
            kit.label(
                self.form, "No steps yet — press “+ Add”.", fg=self.pal.muted, size=SIZE_BODY
            ).pack(pady=24)
            return

        step = self.steps[self.selected]
        step_type = str(step.get("type", "app"))

        head = kit.frame(self.form)
        head.pack(fill="x", pady=(0, self.m.gap_sm))
        kit.label(head, "Type", size=SIZE_SMALL, width=26, anchor="w").pack(side="left")
        self.type_var = tk.StringVar(value=step_type)
        menu = kit.choice(head, step_type, sorted(KNOWN_STEP_TYPES), command=self._change_type)
        self.type_var = menu.var  # type: ignore[attr-defined]
        menu.pack(side="left")
        if step_type == "app":
            kit.button(head, "Choose app…", self._choose_app, kind="quiet")

        rows = [("name", "Name", "str"), ("id", "Id (for depends_on)", "str")]
        rows += FIELDS.get(step_type, [])
        rows += _COMMON_TAIL

        grid = kit.frame(self.form)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(1, weight=1)
        for row, (key, label, kind) in enumerate(rows):
            kit.label(grid, label, size=SIZE_SMALL, anchor="nw", width=26).grid(
                row=row, column=0, sticky="nw", pady=2
            )
            widget = self._make_widget(grid, kind, self._step_value(step, key, kind))
            widget.grid(row=row, column=1, sticky="ew", pady=2)
            self.widgets[key] = (widget, kind)

    @staticmethod
    def _step_value(step: dict[str, Any], key: str, kind: str) -> Any:
        """A step's value for the form, defaults included.

        `enabled`, `detach` and `track` default to true in the schema and are
        usually absent from the file. Rendering an absent key as an unticked
        box and then saving the form is how a step silently disables itself.
        """
        if kind == "bool" and key not in step:
            return key in DEFAULT_TRUE
        return step.get(key)

    def _choose_app(self) -> None:
        """Fill `path` from the installed-application index."""
        from tkinter import simpledialog

        from launcher import apps

        query = simpledialog.askstring("Choose application", "Search for:", parent=self.root)
        if not query:
            return
        match = apps.find(query)
        if match is None:
            messagebox.showinfo("Not found", f"No application matches “{query}”.", parent=self.root)
            return
        widget, kind = self.widgets.get("path", (None, ""))
        if widget is not None and kind == "str":
            widget.var.set(match.argv[0])
        name_widget, _ = self.widgets.get("name", (None, ""))
        if name_widget is not None and not name_widget.var.get().strip().replace("new step", ""):
            name_widget.var.set(match.name)

    def _change_type(self, new_type: str) -> None:
        self._commit_form()
        step = self.steps[self.selected]
        # Keep only the keys the new type understands, so switching type does
        # not leave a stale `url:` on a `kill:` step for the loader to reject.
        keep = {"type", "name", "id", *(k for k, _l, _t in _COMMON_TAIL)}
        keep.update(k for k, _l, _t in FIELDS.get(new_type, []))
        self.steps[self.selected] = {k: v for k, v in step.items() if k in keep} | {
            "type": new_type
        }
        self._refresh_list()

    def _commit_form(self) -> None:
        for key, (widget, kind) in self.profile_widgets.items():
            value = self._widget_value(widget, kind)
            if value in (None, "", [], {}, False):
                self.data.pop(key, None)
            else:
                self.data[key] = value
        if not self.widgets or not self.steps:
            return
        step = self.steps[self.selected]
        step["type"] = self.type_var.get()
        for key, (widget, kind) in self.widgets.items():
            value = self._widget_value(widget, kind)
            if value in (None, "", [], {}):
                step.pop(key, None)
            elif kind == "bool":
                # Booleans have meaningful False values (detach, enabled), so
                # they are written whenever they differ from the schema default.
                default = key in DEFAULT_TRUE
                if value == default:
                    step.pop(key, None)
                else:
                    step[key] = value
            else:
                step[key] = value

    # ── persistence ──────────────────────────────────────────────────────
    def _save(self) -> None:
        self._commit_form()
        problems = []
        for section in ("steps", "teardown"):
            for index, raw in enumerate(self.data.get(section) or []):
                try:
                    _coerce_step(raw)
                except ValueError as exc:
                    problems.append(f"{section}[{index + 1}]: {exc}")
        if problems:
            messagebox.showerror("Invalid profile", "\n".join(problems[:8]), parent=self.root)
            return
        payload = {k: v for k, v in self.data.items() if v not in (None, "", [], {})}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self.root)
            return
        self.saved = True
        self.hint.configure(text=f"Saved {self.path}", fg=self.pal.ok)


def open_editor(path: Path, parent: tk.Misc | None = None) -> bool:
    """Edit the profile file at `path`. Returns True when it was saved."""
    editor = _Editor(path, parent)
    editor.run()
    return editor.saved


def edit_profile(profile: Profile, parent: tk.Misc | None = None) -> bool:
    if profile.source_path is None:
        raise ValueError(f"profile '{profile.name}' has no file on disk")
    return open_editor(profile.source_path, parent)


def new_profile(name: str) -> Path:
    """Create an empty profile file and open the editor on it."""
    path = profiles_dir() / f"{scaffold.slugify(name)}.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"name": name, "steps": []}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    open_editor(path)
    return path
