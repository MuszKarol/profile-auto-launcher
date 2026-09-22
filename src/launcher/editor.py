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
from launcher.theme import SIZE_SMALL, SIZE_TINY, SIZE_TITLE
from launcher.ui import Kit

# Per-type form layout: (yaml key, label, kind).
# kind: str | text | bool | number | list | dict | choice:<options>
_COMMON_TAIL = [
    ("enabled", "Enabled", "bool"),
    ("optional", "Optional (a failure does not fail the run)", "bool"),
    ("parallel", "Parallel (runs alongside its neighbours)", "bool"),
    ("timeout", "Timeout (s)", "number"),
    ("retries", "Retries", "number"),
    ("retry_delay", "Retry delay (s)", "number"),
    ("depends_on", "Depends on (step ids, comma separated)", "list"),
]

FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "app": [
        ("path", "Executable path", "str"),
        ("args", "Arguments (comma separated)", "list"),
        ("cwd", "Working directory", "str"),
        ("detach", "Detach (launch and forget)", "bool"),
        ("track", "Track (so `palaunch stop` can close it)", "bool"),
    ],
    "command": [
        ("run", "Command (argv, comma separated)", "list"),
        ("cwd", "Working directory", "str"),
        ("detach", "Detach (don't wait)", "bool"),
        ("track", "Track (so `palaunch stop` can close it)", "bool"),
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
        ("url", "URL (tcp://host:port or http://…)", "str"),
        ("run", "Or a command (argv, comma separated)", "list"),
        ("interval", "Poll interval (s)", "number"),
    ],
    "profile": [("profile", "Profile name", "str")],
    "notify": [("title", "Title", "str"), ("message", "Message", "str")],
    "http": [
        ("url", "URL", "str"),
        ("method", "Method", "choice:GET,POST,PUT,PATCH,DELETE,HEAD"),
        ("headers", "Headers (KEY=VALUE per line)", "dict"),
        ("body", "Body", "text"),
        ("expect_status", "Expected status (e.g. 200, 204)", "list"),
    ],
    "plugin": [
        ("plugin", "Executable", "str"),
        ("args", "Arguments (comma separated)", "list"),
        ("config", "Config (KEY=VALUE per line)", "dict"),
    ],
    "file": [
        ("action", "Action", "choice:" + ",".join(sorted(KNOWN_FILE_ACTIONS))),
        ("src", "Source", "str"),
        ("dest", "Destination", "str"),
        ("content", "Content", "text"),
    ],
    "rsync": [
        ("src", "Source directory", "str"),
        ("dest", "Destination (path or user@host:/path)", "str"),
        ("delete", "Delete files the source no longer has", "bool"),
        ("exclude", "Exclude (globs, comma separated)", "list"),
        ("backend", "Backend", "choice:auto,rsync,builtin"),
    ],
}

# Step booleans the loader defaults to true when the key is absent.
DEFAULT_TRUE = ("enabled", "detach", "track")

PROFILE_FIELDS = [
    ("name", "Name", "str"),
    ("description", "Description", "str"),
    ("tags", "Tags (comma separated)", "list"),
    ("hotkey", "Hotkey (e.g. <ctrl>+<alt>+d)", "str"),
    ("default", "Default profile", "bool"),
    ("autostart", "Run at login (while the tray is running)", "bool"),
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


# Width of the wizard's label column, shared by its two form grids so their
# fields line up.
LABEL_COLUMN = 104


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
        super().__init__(parent, "New profile", "760x800")
        self.root.minsize(640, 640)
        self.result: Path | None = None
        self.chosen_apps: list[str] = []
        self.matches: list[str] = []
        self._auto_name = ""
        self._build()

    def _build(self) -> None:
        kit, pal, m = self.kit, self.pal, self.m
        # Buttons are packed before the body so a tall form never pushes them
        # off the bottom of the window.
        buttons = kit.frame(self.root, padx=m.gap_xl, pady=m.gap)
        buttons.pack(fill="x", side="bottom")
        kit.button(
            buttons,
            "Create",
            lambda: self._create(edit=False),
            kind="primary",
            side="right",
            padx=0,
        )
        kit.button(
            buttons,
            "Create and edit steps",
            lambda: self._create(edit=True),
            side="right",
            padx=(0, m.gap_sm),
        )
        kit.button(
            buttons, "Cancel", self.root.destroy, kind="quiet", side="right", padx=(0, m.gap_sm)
        )
        self.error = kit.label(buttons, "", fg=pal.err, size=SIZE_SMALL, bold=True, anchor="w")
        self.error.pack(side="left", fill="x", expand=True)
        tk.Frame(self.root, bg=pal.border, height=1).pack(fill="x", side="bottom")

        outer = kit.frame(self.root, padx=m.gap_xl, pady=m.gap_lg)
        outer.pack(fill="both", expand=True)

        kit.label(outer, "New profile", size=SIZE_TITLE, bold=True, anchor="w").pack(fill="x")
        kit.label(
            outer,
            "A profile opens a whole working context at once. "
            "Start from a preset, then say what it opens.",
            fg=pal.muted,
            size=SIZE_SMALL,
            anchor="w",
        ).pack(fill="x", pady=(2, m.gap_lg))

        self.preset_var = tk.StringVar(value=scaffold.PRESETS[0].key)
        kit.segmented(
            outer,
            [(item.title, item.key) for item in scaffold.PRESETS],
            self.preset_var,
            lambda: self._apply_preset(scaffold.preset(self.preset_var.get())),
        ).pack(anchor="w", pady=(0, m.gap_lg))

        form = kit.frame(outer)
        form.pack(fill="x")
        form.columnconfigure(0, minsize=LABEL_COLUMN)
        form.columnconfigure(1, weight=1)
        self.fields: dict[str, tk.Entry] = {}
        for row, (key, label, example) in enumerate(
            (
                ("name", "Name", "Deep work"),
                ("description", "Description", "What this context is for"),
                ("tags", "Tags", "comma separated, e.g. code, focus"),
                ("hotkey", "Hotkey", "optional, e.g. <ctrl>+<alt>+d"),
            )
        ):
            kit.label(form, label, size=SIZE_SMALL, fg=pal.muted, anchor="w").grid(
                row=row, column=0, sticky="w", pady=m.gap_xs, padx=(0, m.gap_lg)
            )
            entry = kit.entry(form, "", width=40, mono=key == "hotkey", placeholder=example)
            entry.grid(row=row, column=1, sticky="ew", pady=m.gap_xs)
            self.fields[key] = entry
        self.fields["name"].focus_set()

        extras = kit.frame(outer)
        extras.columnconfigure(0, minsize=LABEL_COLUMN)
        extras.columnconfigure(1, weight=1)
        kit.label(extras, "Pages", size=SIZE_SMALL, fg=pal.muted, anchor="nw").grid(
            row=0, column=0, sticky="nw", pady=(m.gap_xs + 4, m.gap_xs), padx=(0, m.gap_lg)
        )
        self.urls = kit.text_field(extras, height=2)
        self.urls.grid(row=0, column=1, sticky="ew", pady=m.gap_xs)
        kit.label(extras, "One address per line.", fg=pal.faint, size=SIZE_TINY, anchor="w").grid(
            row=1, column=1, sticky="w"
        )
        kit.label(extras, "Close first", size=SIZE_SMALL, fg=pal.muted, anchor="w").grid(
            row=2, column=0, sticky="w", pady=m.gap_xs, padx=(0, m.gap_lg)
        )
        self.close = kit.entry(extras, "", width=40, placeholder="process names, e.g. slack, steam")
        self.close.grid(row=2, column=1, sticky="ew", pady=(m.gap_sm, m.gap_xs))

        toggles = kit.frame(outer)
        # The fixed-height parts are packed from the bottom up before the
        # applications card, which takes whatever height is left.
        toggles.pack(fill="x", side="bottom", pady=(m.gap, 0))
        extras.pack(fill="x", side="bottom", pady=(m.gap_lg, 0))
        self.tile = kit.checkbox(toggles, "Tile the first two windows side by side", value=True)
        self.tile.pack(anchor="w", pady=1)
        self.make_default = kit.checkbox(toggles, "Make this the default profile", value=False)
        self.make_default.pack(anchor="w", pady=1)
        self.notify_stop = kit.checkbox(toggles, "Notify me when it is stopped", value=False)
        self.notify_stop.pack(anchor="w", pady=1)

        apps_box = kit.frame(outer)
        apps_box.pack(fill="both", expand=True)
        kit.section_label(apps_box, "Applications it opens")
        search = kit.frame(apps_box)
        search.pack(fill="x")
        kit.button(
            search,
            "Add",
            self._add_app,
            kind="primary",
            icon="plus",
            side="right",
            padx=(m.gap_sm, 0),
        )
        self.app_query = kit.search_field(search, "Search, or type a command")
        self.app_query.field.pack(side="left", fill="x", expand=True)  # type: ignore[attr-defined]
        self.app_query.bind("<KeyRelease>", self._on_app_key)
        self.app_query.bind("<Return>", lambda _e: self._add_app())
        self.app_query.bind("<Down>", lambda _e: self.match_list.move(1))
        self.app_query.bind("<Up>", lambda _e: self.match_list.move(-1))

        columns = kit.frame(apps_box)
        columns.pack(fill="both", expand=True, pady=(m.gap_sm, 0))
        columns.columnconfigure((0, 1), weight=1, uniform="apps")
        columns.rowconfigure(1, weight=1)
        kit.label(columns, "Found", fg=pal.faint, size=SIZE_TINY, anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, m.gap_xs)
        )
        opens = kit.frame(columns)
        opens.grid(row=0, column=1, sticky="ew", padx=(m.gap, 0), pady=(0, m.gap_xs))
        kit.label(opens, "Added", fg=pal.faint, size=SIZE_TINY, anchor="w").pack(side="left")
        self.match_list = kit.rows(
            columns,
            empty="Type above to search.",
            on_activate=lambda _i: self._add_app(),
            height=110,
        )
        self.match_list.grid(row=1, column=0, sticky="nsew")
        chosen = kit.frame(columns)
        chosen.grid(row=1, column=1, sticky="nsew", padx=(m.gap, 0))
        self.chosen_list = kit.rows(
            chosen,
            empty="Nothing added yet.",
            on_activate=lambda _i: self._remove_app(),
            height=110,
        )
        self.chosen_list.pack(fill="both", expand=True)
        kit.button(
            opens,
            "",
            self._remove_app,
            kind="quiet",
            icon="trash",
            tip="Remove the selected app",
            side="right",
            padx=0,
        )

        self.root.bind("<Escape>", lambda _e: self.root.destroy())

    # ── preset and app pickers ───────────────────────────────────────────
    def _apply_preset(self, item: scaffold.Preset) -> None:
        """Fill the form from a preset. A name the user typed is kept; one a
        previous preset filled in is replaced with this one's."""
        blank = item.key == scaffold.PRESETS[0].key
        name = self.fields["name"].var.get().strip()
        if not name or name == self._auto_name:
            self._auto_name = "" if blank else item.title
            self.fields["name"].var.set(self._auto_name)
        self.fields["description"].var.set("" if blank else item.description)
        self.fields["tags"].var.set(", ".join(item.tags))
        self.close.var.set(", ".join(item.close))

    def _on_app_key(self, event: tk.Event) -> None:
        if event.keysym not in ("Up", "Down", "Return"):
            self._search_apps()

    def _search_apps(self) -> None:
        from launcher import apps

        query = self.app_query.var.get().strip()
        found = apps.search(query, limit=12) if query else []
        self.matches = [app.name for app in found]
        self.match_list.set_rows(
            [ui.ListRow(title=app.name, detail=apps.describe(app)) for app in found]
        )

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
            self._show_chosen(len(self.chosen_apps) - 1)
        self.app_query.var.set("")
        self._search_apps()

    def _remove_app(self) -> None:
        selection = self.chosen_list.curselection()
        if not selection:
            return
        index = selection[0]
        del self.chosen_apps[index]
        self._show_chosen(index)

    def _show_chosen(self, select: int) -> None:
        self.chosen_list.set_rows(
            [ui.ListRow(title=name) for name in self.chosen_apps], select=select
        )

    # ── creation ─────────────────────────────────────────────────────────
    def draft(self) -> scaffold.Draft:
        return scaffold.Draft(
            name=self.fields["name"].var.get().strip(),
            description=self.fields["description"].var.get().strip(),
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
        kit, pal, m = self.kit, self.pal, self.m
        top = kit.frame(self.root, padx=m.gap_xl, pady=m.gap_lg)
        top.pack(fill="x")
        kit.button(top, "Save", self._save, kind="primary", side="right", padx=0)
        kit.button(top, "Close", self.root.destroy, kind="quiet", side="right", padx=(0, m.gap_sm))
        title = kit.frame(top)
        title.pack(side="left", fill="x", expand=True)
        kit.label(
            title,
            str(self.data.get("name") or self.path.stem),
            size=SIZE_TITLE,
            bold=True,
            anchor="w",
        ).pack(fill="x")
        location = kit.label(title, "", fg=pal.faint, size=SIZE_TINY, mono=True, anchor="w")
        location.pack(fill="x", pady=(2, 0))
        kit.elide(location, str(self.path))
        tk.Frame(self.root, bg=pal.border, height=1).pack(fill="x")

        footer = kit.frame(self.root, padx=m.gap_xl, pady=m.gap_sm)
        footer.pack(fill="x", side="bottom")
        self.hint = kit.label(
            footer,
            "Saving rewrites the file, so YAML comments are not kept.",
            fg=pal.faint,
            size=SIZE_TINY,
            anchor="w",
        )
        self.hint.pack(fill="x")
        tk.Frame(self.root, bg=pal.border, height=1).pack(fill="x", side="bottom")

        columns = kit.frame(self.root)
        columns.pack(fill="both", expand=True)

        left = tk.Frame(columns, bg=pal.panel, width=340, padx=m.gap_lg, pady=m.gap_lg)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Frame(columns, bg=pal.border, width=1).pack(side="left", fill="y")

        self.section_var = tk.StringVar(value="steps")
        kit.segmented(
            left,
            (("Steps", "steps"), ("Teardown", "teardown")),
            self.section_var,
            self._switch_section,
        ).pack(anchor="w", pady=(0, m.gap))

        buttons = tk.Frame(left, bg=pal.panel)
        buttons.pack(fill="x", side="bottom", pady=(m.gap, 0))
        kit.button(buttons, "Add step", self._add_step, icon="plus")
        kit.button(
            buttons,
            "",
            self._remove_step,
            kind="quiet",
            icon="trash",
            tip="Remove this step",
            side="right",
            padx=0,
        )
        kit.button(
            buttons,
            "",
            lambda: self._move_step(1),
            kind="quiet",
            icon="arrow-down",
            tip="Move down",
            side="right",
            padx=(0, m.gap_xs),
        )
        kit.button(
            buttons,
            "",
            lambda: self._move_step(-1),
            kind="quiet",
            icon="arrow-up",
            tip="Move up",
            side="right",
            padx=(0, m.gap_xs),
        )

        self.listbox = kit.rows(
            left, empty="No steps yet.", on_select=self._on_select, mono_detail=True
        )
        self.listbox.pack(fill="both", expand=True)

        holder = kit.frame(columns, padx=m.gap_xl, pady=m.gap_lg)
        holder.pack(side="left", fill="both", expand=True)
        # The form outgrows the window on a step with every option set, so the
        # right-hand column scrolls rather than losing its last rows.
        right = kit.scrollable(holder, self.root)

        self.profile_box = kit.frame(right)
        self.profile_box.pack(fill="x")
        self._build_profile_fields()

        self.form = kit.frame(right)
        self.form.pack(fill="both", expand=True)

    def _build_profile_fields(self) -> None:
        kit = self.kit
        label = kit.section_label(self.profile_box, "Profile")
        label.pack_configure(pady=(0, self.m.gap_sm))
        grid = kit.frame(self.profile_box)
        grid.pack(fill="x")
        self.profile_widgets: dict[str, Any] = {}
        for row, (key, text, kind) in enumerate(PROFILE_FIELDS):
            widget = self._form_row(grid, row, text, kind, self.data.get(key))
            self.profile_widgets[key] = (widget, kind)

    def _form_row(self, grid: tk.Widget, row: int, text: str, kind: str, value: Any) -> tk.Widget:
        """One labelled field. A bracketed note in the label becomes the
        field's placeholder, or — for a checkbox — quiet text beside it."""
        kit, pal, m = self.kit, self.pal, self.m
        grid.columnconfigure(1, weight=1)
        name, note = ui.split_hint(text)
        if kind == "bool":
            holder = kit.frame(grid)
            holder.grid(row=row, column=1, sticky="w", pady=m.gap_xs)
            widget = kit.checkbox(holder, name, value=bool(value))
            widget.pack(side="left")
            if note:
                kit.label(holder, note, fg=pal.faint, size=SIZE_TINY).pack(
                    side="left", padx=(m.gap_sm, 0)
                )
            return widget
        multiline = kind in ("text", "dict")
        kit.label(grid, name, fg=pal.muted, size=SIZE_SMALL, anchor="nw", width=18).grid(
            row=row, column=0, sticky="nw", pady=(m.gap_xs + 7, m.gap_xs), padx=(0, m.gap)
        )
        widget = self._make_widget(grid, kind, value, note)
        widget.grid(
            row=row, column=1, sticky="ew" if multiline or kind != "choice" else "w", pady=m.gap_xs
        )
        return widget

    def _make_widget(self, parent: tk.Widget, kind: str, value: Any, note: str = "") -> tk.Widget:
        kit = self.kit
        if kind == "bool":
            return kit.checkbox(parent, value=bool(value))
        if kind.startswith("choice:"):
            options = kind.split(":", 1)[1].split(",")
            return kit.choice(parent, str(value) if value else options[0], options)
        if kind in ("text", "dict"):
            widget = kit.text_field(parent, height=5 if kind == "text" else 4)
            widget.configure(wrap="none")
            content = (
                _format_pairs(value) if kind == "dict" else ("" if value is None else str(value))
            )
            widget.insert("1.0", content)
            return widget
        text = _format_list(value) if kind == "list" else ("" if value is None else str(value))
        return kit.entry(parent, text, width=40, placeholder=note)

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
        rows = []
        for index, raw in enumerate(self.steps):
            try:
                summary = describe_step(_coerce_step(raw))
            except ValueError:
                summary = "invalid step"
            rows.append(
                ui.ListRow(
                    title=str(raw.get("name") or raw.get("type", "step")),
                    detail=summary,
                    lead=str(index + 1),
                    tags=("off",) if raw.get("enabled") is False else (),
                    dimmed=raw.get("enabled") is False,
                )
            )
        if self.steps:
            self.selected = min(self.selected, len(self.steps) - 1)
        self.listbox.set_rows(rows, select=self.selected if self.steps else None)
        self._build_form()

    def _on_select(self, index: int) -> None:
        if index == self.selected:
            return
        self._commit_form()
        self.selected = index
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
        kit, pal, m = self.kit, self.pal, self.m
        for child in self.form.winfo_children():
            child.destroy()
        self.widgets.clear()
        title = "Teardown step" if self.section == "teardown" else "Step"
        if not self.steps:
            kit.section_label(self.form, title)
            kit.label(
                self.form,
                "Nothing here yet. Add a step to start.",
                fg=pal.muted,
                size=SIZE_SMALL,
                anchor="w",
            ).pack(fill="x")
            return

        step = self.steps[self.selected]
        step_type = str(step.get("type", "app"))
        kit.section_label(self.form, f"{title} {self.selected + 1}")

        head = kit.frame(self.form)
        head.pack(fill="x", pady=(0, m.gap_xs))
        kit.label(head, "Type", fg=pal.muted, size=SIZE_SMALL, anchor="w", width=18).pack(
            side="left", padx=(0, m.gap)
        )
        menu = kit.choice(head, step_type, sorted(KNOWN_STEP_TYPES), command=self._change_type)
        self.type_var = menu.var  # type: ignore[attr-defined]
        menu.pack(side="left")
        if step_type == "app":
            kit.button(
                head,
                "Choose app",
                self._choose_app,
                kind="quiet",
                icon="search",
                padx=(m.gap_sm, 0),
            )

        rows = [("name", "Name", "str"), ("id", "Id (referenced by depends_on)", "str")]
        rows += FIELDS.get(step_type, [])
        rows += _COMMON_TAIL

        grid = kit.frame(self.form)
        grid.pack(fill="both", expand=True)
        for row, (key, label, kind) in enumerate(rows):
            widget = self._form_row(grid, row, label, kind, self._step_value(step, key, kind))
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
        self.hint.configure(text=f"Saved {self.path.name}", fg=self.pal.ok)
        self._refresh_list()


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
