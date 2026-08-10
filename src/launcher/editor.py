"""Graphical profile editor (Tkinter).

Operates on the raw YAML mapping rather than on the parsed `Profile`, so keys
the editor doesn't know about survive a save untouched. Comments do not — the
editor rewrites the file — which is why the save dialog says so out loud.

Every step is validated through the real loader before anything is written, so
the editor cannot produce a profile that `palaunch validate` would reject.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any

import yaml

from launcher import theme
from launcher.config import (
    KNOWN_FILE_ACTIONS,
    KNOWN_STEP_TYPES,
    Profile,
    _coerce_step,
    _read_raw,
    profiles_dir,
)
from launcher.executor import describe_step

# Per-type form layout: (yaml key, label, kind).
# kind: str | text | bool | number | list | dict | choice:<options>
_COMMON_TAIL = [
    ("enabled", "Enabled", "bool"),
    ("optional", "Optional (failure ignored)", "bool"),
    ("parallel", "Run in parallel with neighbours", "bool"),
    ("timeout", "Timeout (s)", "number"),
    ("retries", "Retries", "number"),
    ("retry_delay", "Retry delay (s)", "number"),
    ("depends_on", "Depends on (ids, comma separated)", "list"),
]

FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "app": [
        ("path", "Executable path", "str"),
        ("args", "Arguments (comma separated)", "list"),
        ("cwd", "Working directory", "str"),
        ("detach", "Detach (launch and forget)", "bool"),
        ("track", "Track pid for `palaunch stop`", "bool"),
    ],
    "command": [
        ("run", "Command (comma separated argv)", "list"),
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
        ("run", "…or command (comma separated argv)", "list"),
        ("interval", "Poll interval (s)", "number"),
    ],
    "profile": [("profile", "Profile name", "str")],
    "notify": [("title", "Title", "str"), ("message", "Message", "str")],
    "http": [
        ("url", "URL", "str"),
        ("method", "Method", "choice:GET,POST,PUT,PATCH,DELETE,HEAD"),
        ("headers", "Headers (KEY=VALUE per line)", "dict"),
        ("body", "Body", "text"),
        ("expect_status", "Expected status codes (comma separated)", "list"),
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
        ("content", "Content (write/append)", "text"),
    ],
}

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


class _Editor:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.pal = theme.palette()
        self.font = theme.font_family()
        self.mono = theme.mono_family()
        self.data: dict[str, Any] = _read_raw(path) if path.is_file() else {"steps": []}
        self.data.setdefault("steps", [])
        self.section = "steps"  # steps | teardown
        self.selected = 0
        self.widgets: dict[str, Any] = {}
        self.saved = False

        self.root = tk.Tk()
        self.root.title(f"Edit profile — {path.name}")
        self.root.configure(bg=self.pal.bg)
        self.root.geometry("980x620")
        self._build()
        self._refresh_list()

    # ── layout ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        pal = self.pal
        top = tk.Frame(self.root, bg=pal.bg, padx=14, pady=10)
        top.pack(fill="x")
        tk.Label(top, text=self.path.name, bg=pal.bg, fg=pal.fg, font=(self.font, 13, "bold")).pack(
            side="left"
        )
        tk.Button(
            top,
            text="Save",
            command=self._save,
            bg=pal.accent,
            fg="#ffffff",
            relief="flat",
            font=(self.font, 10, "bold"),
            padx=14,
        ).pack(side="right")
        tk.Button(
            top,
            text="Close",
            command=self.root.destroy,
            bg=pal.panel,
            fg=pal.fg,
            relief="flat",
            font=(self.font, 10),
            padx=10,
        ).pack(side="right", padx=(0, 8))

        self.section_var = tk.StringVar(value="steps")
        toggle = tk.Frame(top, bg=pal.bg)
        toggle.pack(side="right", padx=16)
        for label, value in (("Steps", "steps"), ("Teardown", "teardown")):
            tk.Radiobutton(
                toggle,
                text=label,
                variable=self.section_var,
                value=value,
                command=self._switch_section,
                bg=pal.bg,
                fg=pal.fg,
                selectcolor=pal.panel,
                activebackground=pal.bg,
                activeforeground=pal.fg,
                font=(self.font, 10),
                relief="flat",
                highlightthickness=0,
                borderwidth=0,
            ).pack(side="left")

        columns = tk.Frame(self.root, bg=pal.bg, padx=14, pady=6)
        columns.pack(fill="both", expand=True)

        left = tk.Frame(columns, bg=pal.bg, width=320)
        left.pack(side="left", fill="both")
        left.pack_propagate(False)

        self.listbox = tk.Listbox(
            left,
            bg=pal.panel,
            fg=pal.fg,
            selectbackground=pal.panel_selected,
            selectforeground=pal.fg,
            relief="flat",
            font=(self.mono, 9),
            highlightthickness=0,
            activestyle="none",
        )
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        buttons = tk.Frame(left, bg=pal.bg, pady=8)
        buttons.pack(fill="x")
        for label, command in (
            ("+ Add", self._add_step),
            ("− Remove", self._remove_step),
            ("↑", lambda: self._move_step(-1)),
            ("↓", lambda: self._move_step(1)),
        ):
            tk.Button(
                buttons,
                text=label,
                command=command,
                bg=pal.panel,
                fg=pal.fg,
                relief="flat",
                font=(self.font, 9),
                padx=8,
            ).pack(side="left", padx=2)

        right = tk.Frame(columns, bg=pal.bg, padx=14)
        right.pack(side="left", fill="both", expand=True)

        self.profile_box = tk.Frame(right, bg=pal.bg)
        self.profile_box.pack(fill="x", pady=(0, 10))
        self._build_profile_fields()

        tk.Frame(right, bg=pal.border, height=1).pack(fill="x", pady=6)
        self.form = tk.Frame(right, bg=pal.bg)
        self.form.pack(fill="both", expand=True)

        self.hint = tk.Label(
            self.root,
            text="Saving rewrites the file — YAML comments are not preserved.",
            bg=pal.bg,
            fg=pal.muted,
            font=(self.font, 9),
            anchor="w",
            padx=14,
            pady=6,
        )
        self.hint.pack(fill="x")

    def _build_profile_fields(self) -> None:
        pal = self.pal
        tk.Label(
            self.profile_box,
            text="Profile",
            bg=pal.bg,
            fg=pal.muted,
            font=(self.font, 9, "bold"),
            anchor="w",
        ).pack(fill="x")
        grid = tk.Frame(self.profile_box, bg=pal.bg)
        grid.pack(fill="x")
        self.profile_widgets: dict[str, Any] = {}
        for row, (key, label, kind) in enumerate(PROFILE_FIELDS):
            tk.Label(
                grid, text=label, bg=pal.bg, fg=pal.fg, font=(self.font, 9), anchor="w", width=22
            ).grid(row=row, column=0, sticky="w", pady=1)
            widget = self._make_widget(grid, kind, self.data.get(key))
            widget.grid(row=row, column=1, sticky="ew", pady=1)
            self.profile_widgets[key] = (widget, kind)
        grid.columnconfigure(1, weight=1)

    def _make_widget(self, parent: tk.Widget, kind: str, value: Any) -> tk.Widget:
        pal = self.pal
        if kind == "bool":
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
        if kind.startswith("choice:"):
            options = kind.split(":", 1)[1].split(",")
            var = tk.StringVar(value=str(value) if value else options[0])
            widget = tk.OptionMenu(parent, var, *options)
            widget.configure(
                bg=pal.panel,
                fg=pal.fg,
                relief="flat",
                highlightthickness=0,
                font=(self.font, 9),
                activebackground=pal.panel_hover,
            )
            widget.var = var  # type: ignore[attr-defined]
            return widget
        if kind in ("text", "dict"):
            widget = tk.Text(
                parent,
                height=5,
                bg=pal.field_bg,
                fg=pal.fg,
                relief="flat",
                insertbackground=pal.accent,
                font=(self.mono, 9),
                highlightthickness=1,
                highlightbackground=pal.border,
            )
            content = (
                _format_pairs(value) if kind == "dict" else ("" if value is None else str(value))
            )
            widget.insert("1.0", content)
            return widget
        var = tk.StringVar(
            value=_format_list(value) if kind == "list" else ("" if value is None else str(value))
        )
        widget = tk.Entry(
            parent,
            textvariable=var,
            bg=pal.field_bg,
            fg=pal.fg,
            relief="flat",
            insertbackground=pal.accent,
            font=(self.mono, 9),
            highlightthickness=1,
            highlightbackground=pal.border,
        )
        widget.var = var  # type: ignore[attr-defined]
        return widget

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
        pal = self.pal
        for child in self.form.winfo_children():
            child.destroy()
        self.widgets.clear()
        if not self.steps:
            tk.Label(
                self.form,
                text="No steps yet — press “+ Add”.",
                bg=pal.bg,
                fg=pal.muted,
                font=(self.font, 10),
            ).pack(pady=20)
            return

        step = self.steps[self.selected]
        step_type = str(step.get("type", "app"))

        head = tk.Frame(self.form, bg=pal.bg)
        head.pack(fill="x", pady=(0, 6))
        tk.Label(
            head, text="Type", bg=pal.bg, fg=pal.fg, font=(self.font, 9), width=22, anchor="w"
        ).pack(side="left")
        self.type_var = tk.StringVar(value=step_type)
        type_menu = tk.OptionMenu(
            head, self.type_var, *sorted(KNOWN_STEP_TYPES), command=self._change_type
        )
        type_menu.configure(
            bg=pal.panel,
            fg=pal.fg,
            relief="flat",
            highlightthickness=0,
            font=(self.font, 9),
            activebackground=pal.panel_hover,
        )
        type_menu.pack(side="left")

        rows = [("name", "Name", "str"), ("id", "Id (for depends_on)", "str")]
        rows += FIELDS.get(step_type, [])
        rows += _COMMON_TAIL

        grid = tk.Frame(self.form, bg=pal.bg)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(1, weight=1)
        for row, (key, label, kind) in enumerate(rows):
            tk.Label(
                grid,
                text=label,
                bg=pal.bg,
                fg=pal.fg,
                font=(self.font, 9),
                anchor="nw",
                width=22,
            ).grid(row=row, column=0, sticky="nw", pady=2)
            widget = self._make_widget(grid, kind, step.get(key))
            widget.grid(row=row, column=1, sticky="ew", pady=2)
            self.widgets[key] = (widget, kind)

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
                default = key in ("enabled", "detach", "track")
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

    def run(self) -> bool:
        self.root.mainloop()
        return self.saved


def open_editor(path: Path) -> bool:
    """Edit the profile file at `path`. Returns True when it was saved."""
    return _Editor(path).run()


def edit_profile(profile: Profile) -> bool:
    if profile.source_path is None:
        raise ValueError(f"profile '{profile.name}' has no file on disk")
    return open_editor(profile.source_path)


def new_profile(name: str) -> Path:
    """Create an empty profile file and open the editor on it."""
    slug = name.lower().replace(" ", "-")
    path = profiles_dir() / f"{slug}.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"name": name, "steps": []}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    open_editor(path)
    return path
