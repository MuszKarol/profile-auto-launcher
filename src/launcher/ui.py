"""The widget kit every window is built from.

Tk's stock widgets look like 1995 and, worse, ignore colours inconsistently
across platforms: a `tk.Button` on macOS keeps its native grey whatever `bg`
says, and a `tk.Checkbutton` on X11 draws a bevelled box from another decade.
So buttons, checkboxes, menus, scrollbars and lists here are drawn from
frames, labels and small canvases with their own hover and press states. They
behave identically on all three platforms and let the palette in
`launcher.theme` actually decide what the app looks like.

The kit is deliberately short on decoration. There is one accent, one primary
button per view, and icons only where `launcher.icons` is asked for them:
navigation entries and the buttons whose verb has a well-known shape.

Every control shares one height — a line of small text plus
`METRICS.control_pad_y` above and below, plus a one-pixel outline — so a
field, a menu and a button placed side by side line up without adjustment.

Nothing in here knows about profiles, settings or steps — it is presentation
only, shared by the launcher window, the manager and the profile editor.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from tkinter import font as tkfont

from launcher import branding, icons, theme
from launcher.theme import SIZE_BODY, SIZE_SMALL, SIZE_TINY, SIZE_TITLE

BUTTON_KINDS = ("primary", "ghost", "quiet", "danger")

WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")

# Pixels of text inset inside a field. Tk draws a flat border in the field's
# own colour, so a wide border is the only padding an Entry can have.
FIELD_INSET = 6


def new_window(parent: tk.Misc | None) -> tk.Misc:
    """A Toplevel when a window already exists, a root when none does.

    Tk allows one root per process and dislikes being asked for a second —
    on macOS a fresh root after an earlier one has been destroyed can take the
    whole interpreter down. Every window here therefore takes an optional
    parent, so a process that already has a window nests inside it.
    """
    return tk.Toplevel(parent) if parent is not None else tk.Tk()


def show_window(window: tk.Misc, parent: tk.Misc | None) -> None:
    """Block until the window closes: its own loop, or the parent's."""
    if parent is None:
        window.mainloop()
        return
    window.transient(parent)  # type: ignore[arg-type]
    window.grab_set()
    window.wait_window()


def ground_of(widget: tk.Misc, fallback: str = "") -> str:
    """The background a child of `widget` should blend into."""
    try:
        return str(widget.cget("bg"))
    except tk.TclError:
        return fallback


def wheel_steps(event: tk.Event) -> int:
    """Units to scroll for one wheel event, on X11, Windows and macOS alike."""
    button = getattr(event, "num", 0)
    if button == 4:
        return -1
    if button == 5:
        return 1
    delta = getattr(event, "delta", 0) or 0
    return -1 if delta > 0 else 1 if delta < 0 else 0


def split_hint(label: str) -> tuple[str, str]:
    """'Tags (comma separated)' → ('Tags', 'comma separated').

    Form labels used to carry their format in brackets. The bracket reads as
    clutter next to a field; the same words as a placeholder inside it read
    as help.
    """
    if label.endswith(")") and " (" in label:
        head, _, tail = label.rpartition(" (")
        return head, tail[:-1]
    return label, ""


def pretty_keys(combo: str) -> str:
    """'<ctrl>+<alt>+d' → 'Ctrl+Alt+D': a hotkey the way a keyboard labels it."""
    names = {"cmd": "Cmd", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Super"}
    keys = []
    for part in combo.split("+"):
        key = part.strip().strip("<>")
        if not key:
            continue
        keys.append(names.get(key.lower(), key.upper() if len(key) == 1 else key.capitalize()))
    return "+".join(keys)


def _descendants(widget: tk.Misc) -> Iterable[tk.Misc]:
    yield widget
    for child in widget.winfo_children():
        yield from _descendants(child)


class Kit:
    """Palette, fonts and widget factories for one window."""

    def __init__(self, palette: theme.Palette | None = None) -> None:
        self.pal = palette or theme.palette()
        self.font = theme.font_family()
        self.mono = theme.mono_family()
        self.m = theme.METRICS
        self._fonts: dict[tuple, tkfont.Font] = {}

    # ── fonts ────────────────────────────────────────────────────────────
    def f(self, size: int = SIZE_BODY, bold: bool = False) -> tuple:
        return (self.font, size, "bold") if bold else (self.font, size)

    def fm(self, size: int = SIZE_SMALL) -> tuple:
        return (self.mono, size)

    def measure(self, spec: tuple | str, text: str) -> int:
        """Pixel width of `text` in the font `spec`."""
        key = spec if isinstance(spec, tuple) else (spec,)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(font=spec)
        return self._fonts[key].measure(text)

    def elide(self, label: tk.Label, text: str) -> None:
        """Keep `label` showing `text`, cut with an ellipsis to its width.

        Tk clips a label that is too narrow mid-glyph. A list row or a path
        that ends in "…" reads as deliberate; one that ends in half a letter
        reads as broken.
        """
        label.full_text = text  # type: ignore[attr-defined]

        def fit(_event: tk.Event | None = None) -> None:
            full = label.full_text  # type: ignore[attr-defined]
            room = label.winfo_width() - 2 * int(label.cget("padx"))
            if room <= 1:
                label.configure(text=full)
                return
            spec = label.cget("font")
            if self.measure(spec, full) <= room:
                label.configure(text=full)
                return
            low, high = 0, len(full)
            while low < high:
                middle = (low + high + 1) // 2
                if self.measure(spec, full[:middle].rstrip() + "…") <= room:
                    low = middle
                else:
                    high = middle - 1
            label.configure(text=full[:low].rstrip() + "…")

        label.configure(text=text)
        label.bind("<Configure>", fit, add="+")

    # ── containers ───────────────────────────────────────────────────────
    def frame(self, parent: tk.Misc, bg: str | None = None, **kwargs) -> tk.Frame:
        return tk.Frame(parent, bg=bg or self.pal.bg, **kwargs)

    def card(self, parent: tk.Misc, pad: int | None = None, bg: str | None = None) -> tk.Frame:
        """A raised block with a hairline border — the panel's grouping unit."""
        outer = tk.Frame(parent, bg=self.pal.border)
        inner = tk.Frame(
            outer,
            bg=bg or self.pal.panel,
            padx=self.m.pad if pad is None else pad,
            pady=self.m.pad if pad is None else pad,
        )
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        outer.inner = inner  # type: ignore[attr-defined]
        return outer

    def card_title(self, parent: tk.Misc, title: str, hint: str = "") -> tk.Frame:
        """A card's own title and one line about it; returns the title row so
        a button can sit at its right-hand end."""
        ground = ground_of(parent, self.pal.panel)
        row = tk.Frame(parent, bg=ground)
        row.pack(fill="x")
        self.label(row, title, bg=ground, size=SIZE_BODY, bold=True, anchor="w").pack(side="left")
        if hint:
            self.label(
                parent,
                hint,
                bg=ground,
                fg=self.pal.muted,
                size=SIZE_TINY,
                anchor="w",
                justify="left",
                wraplength=520,
            ).pack(fill="x", pady=(2, 0))
        tk.Frame(parent, bg=ground, height=self.m.gap).pack(fill="x")
        return row

    def divider(self, parent: tk.Misc, pady: tuple[int, int] = (0, 0)) -> tk.Frame:
        line = tk.Frame(parent, bg=self.pal.border, height=1)
        line.pack(fill="x", pady=pady)
        return line

    # ── text ─────────────────────────────────────────────────────────────
    def label(
        self,
        parent: tk.Misc,
        text: str,
        *,
        bg: str | None = None,
        fg: str | None = None,
        size: int = SIZE_BODY,
        bold: bool = False,
        mono: bool = False,
        **kwargs,
    ) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=bg or self.pal.bg,
            fg=fg or self.pal.fg,
            font=self.fm(size) if mono else self.f(size, bold),
            **kwargs,
        )

    def heading(
        self, parent: tk.Misc, text: str, hint: str = "", bg: str | None = None
    ) -> tk.Frame:
        """A page header: the title, one line about it, and a slot at the
        right for the page's own actions — which this returns.

        No icon: the navigation entry beside it already carries one, and the
        same glyph twice on one screen is decoration, not information.
        """
        ground = bg or self.pal.bg
        top = self.frame(parent, bg=ground)
        top.pack(fill="x", anchor="w", pady=(0, self.m.gap_lg))
        actions = self.frame(top, bg=ground)
        actions.pack(side="right", anchor="n")
        text_box = self.frame(top, bg=ground)
        text_box.pack(side="left", fill="x", expand=True)
        self.label(text_box, text, size=SIZE_TITLE, bold=True, bg=ground, anchor="w").pack(fill="x")
        if hint:
            self.label(
                text_box, hint, size=SIZE_SMALL, fg=self.pal.muted, bg=ground, anchor="w"
            ).pack(fill="x", pady=(2, 0))
        return actions

    def section_label(self, parent: tk.Misc, text: str, bg: str | None = None) -> tk.Label:
        widget = self.label(
            parent,
            text.upper(),
            size=SIZE_TINY,
            bold=True,
            fg=self.pal.faint,
            bg=bg or ground_of(parent, self.pal.bg),
            anchor="w",
        )
        widget.pack(fill="x", pady=(self.m.gap_lg, self.m.gap_sm))
        return widget

    def search_field(self, parent: tk.Misc, placeholder: str = "", width: int = 40) -> tk.Entry:
        """A field with a magnifier inside it. Returns the entry; its outline
        frame — the thing to pack — is `entry.field`."""
        pal = self.pal
        field = tk.Frame(
            parent,
            bg=pal.field_bg,
            highlightthickness=1,
            highlightbackground=pal.field_border,
            cursor="xterm",
        )
        glass = icons.draw(field, "search", 14, pal.faint, pal.field_bg)
        glass.pack(side="left", padx=(FIELD_INSET + 4, 0))
        entry = self.entry(field, "", width=width, mono=False, placeholder=placeholder)
        entry.configure(highlightthickness=0)
        entry.pack(side="left", fill="x", expand=True)

        def ring(colour: str) -> None:
            field.configure(highlightbackground=colour)

        entry.bind("<FocusIn>", lambda _e: ring(pal.muted), add="+")
        entry.bind("<FocusOut>", lambda _e: ring(pal.field_border), add="+")
        glass.bind("<Button-1>", lambda _e: entry.focus_set())
        field.bind("<Button-1>", lambda _e: entry.focus_set())
        entry.field = field  # type: ignore[attr-defined]
        return entry

    def keycap(self, parent: tk.Misc, text: str, bg: str | None = None) -> tk.Frame:
        """A key name in a hairline box — how shortcuts are shown."""
        ground = bg or ground_of(parent, self.pal.bg)
        edge = tk.Frame(parent, bg=self.pal.border)
        tk.Label(
            edge,
            text=text,
            bg=ground,
            fg=self.pal.muted,
            font=self.f(SIZE_TINY),
            padx=5,
            pady=0,
        ).pack(padx=1, pady=1)
        return edge

    # ── buttons ──────────────────────────────────────────────────────────
    def button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        kind: str = "ghost",
        pack: bool = True,
        icon: str = "",
        tip: str = "",
        **pack_kwargs,
    ) -> tk.Frame:
        """A button drawn from a frame and a label, with hover and press states.

        It fires on release, and only when the pointer is still over it — the
        way native buttons behave, so a press can be abandoned by dragging off.
        `icon` puts an outline glyph before the text; with no text the button
        is a square icon button, and `tip` says what it does on hover.
        """
        pal = self.pal
        ground = ground_of(parent, pal.bg)
        kind = kind if kind in BUTTON_KINDS else "ghost"
        # (fill, ink, edge) at rest and under the pointer
        rest, hover = {
            "primary": (
                (pal.accent, pal.on_accent, pal.accent),
                (pal.accent_hover, pal.on_accent, pal.accent_hover),
            ),
            "ghost": (
                (pal.panel_hover, pal.fg, pal.border),
                (pal.panel_selected, pal.fg, pal.border_strong),
            ),
            "quiet": ((ground, pal.muted, ground), (pal.panel_hover, pal.fg, pal.panel_hover)),
            "danger": (
                (pal.panel_hover, pal.err, pal.border),
                (pal.panel_selected, pal.err, pal.border_strong),
            ),
        }[kind]

        outer = tk.Frame(parent, bg=rest[2], cursor="hand2")
        inner = tk.Frame(
            outer,
            bg=rest[0],
            padx=7 if icon and not text else self.m.gap,
            pady=self.m.control_pad_y,
            cursor="hand2",
        )
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        glyph = None
        if icon:
            glyph = icons.draw(inner, icon, 14, rest[1], rest[0])
            glyph.configure(cursor="hand2")
            glyph.pack(side="left", padx=(0, 6) if text else 0)
        caption = None
        if text or not icon:
            caption = tk.Label(
                inner,
                text=text,
                bg=rest[0],
                fg=rest[1],
                font=self.f(SIZE_SMALL, bold=kind == "primary"),
                padx=0,
                pady=0,
                cursor="hand2",
            )
            caption.pack(side="left")
        parts = [w for w in (outer, inner, glyph, caption) if w is not None]

        def paint(style: tuple[str, str, str]) -> None:
            fill, ink, edge = style
            outer.configure(bg=edge)
            inner.configure(bg=fill)
            if caption is not None:
                caption.configure(bg=fill, fg=ink)
            if glyph is not None:
                icons.recolour(glyph, ink, fill)

        def pointer_inside() -> bool:
            try:
                x, y = outer.winfo_pointerxy()
                return outer.winfo_containing(x, y) in parts
            except (tk.TclError, KeyError):
                return False

        def on_leave(_event: tk.Event) -> None:
            if not pointer_inside():
                paint(rest)

        def on_release(_event: tk.Event) -> None:
            if pointer_inside():
                paint(hover)
                command()
            else:
                paint(rest)

        for widget in parts:
            widget.bind("<Enter>", lambda _e: paint(hover))
            widget.bind("<Leave>", on_leave)
            widget.bind("<ButtonRelease-1>", on_release)
        if tip:
            self.tooltip(parts, tip)
        outer.caption = caption  # type: ignore[attr-defined]
        if pack:
            options = {"side": "left", "padx": (0, self.m.gap_sm)}
            options.update(pack_kwargs)
            outer.pack(**options)
        return outer

    def tooltip(self, widgets: Sequence[tk.Misc], text: str) -> None:
        """A short label under the pointer after a moment's hover."""
        pal = self.pal
        state: dict[str, object] = {"job": None, "window": None}

        def hide(_event: tk.Event | None = None) -> None:
            job = state["job"]
            if job is not None:
                try:
                    widgets[0].after_cancel(job)  # type: ignore[arg-type]
                except tk.TclError:
                    pass
                state["job"] = None
            window = state["window"]
            if window is not None:
                try:
                    window.destroy()  # type: ignore[attr-defined]
                except tk.TclError:
                    pass
                state["window"] = None

        def show() -> None:
            state["job"] = None
            anchor = widgets[0]
            try:
                x = anchor.winfo_rootx()
                y = anchor.winfo_rooty() + anchor.winfo_height() + 6
                window = tk.Toplevel(anchor)
            except tk.TclError:
                return
            window.overrideredirect(True)
            try:
                window.attributes("-topmost", True)
            except tk.TclError:
                pass
            window.configure(bg=pal.border)
            tk.Label(
                window,
                text=text,
                bg=pal.elevated,
                fg=pal.fg,
                font=self.f(SIZE_TINY),
                padx=8,
                pady=4,
            ).pack(padx=1, pady=1)
            window.geometry(f"+{x}+{y}")
            state["window"] = window

        def schedule(_event: tk.Event) -> None:
            if state["job"] is None and state["window"] is None:
                state["job"] = widgets[0].after(550, show)

        for widget in widgets:
            widget.bind("<Enter>", schedule, add="+")
            widget.bind("<Leave>", hide, add="+")
            widget.bind("<ButtonPress-1>", hide, add="+")
            widget.bind("<Destroy>", hide, add="+")

    # ── inputs ───────────────────────────────────────────────────────────
    def _field_border(self, widget: tk.Widget) -> None:
        """Brighten a field's outline under the pointer; focus has its own."""

        def enter(_event: tk.Event) -> None:
            widget.configure(highlightbackground=self.pal.border_strong)

        def leave(_event: tk.Event) -> None:
            widget.configure(highlightbackground=self.pal.field_border)

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")

    def entry(
        self,
        parent: tk.Misc,
        value: str = "",
        width: int = 24,
        mono: bool = True,
        show: str | None = None,
        placeholder: str = "",
    ) -> tk.Entry:
        var = tk.StringVar(value=value)
        font = self.fm(SIZE_SMALL) if mono else self.f(SIZE_SMALL)
        widget = tk.Entry(
            parent,
            textvariable=var,
            bg=self.pal.field_bg,
            fg=self.pal.fg,
            insertbackground=self.pal.fg,
            insertwidth=1,
            disabledbackground=self.pal.field_bg,
            readonlybackground=self.pal.field_bg,
            disabledforeground=self.pal.muted,
            selectbackground=self.pal.panel_selected,
            selectforeground=self.pal.fg,
            relief="flat",
            borderwidth=FIELD_INSET,
            font=font,
            highlightthickness=1,
            highlightbackground=self.pal.field_border,
            highlightcolor=self.pal.muted,
            width=width,
            show=show or "",
        )
        widget.var = var  # type: ignore[attr-defined]
        self._field_border(widget)
        if placeholder:
            self.placeholder(widget, var, placeholder, font)
        return widget

    def placeholder(self, widget: tk.Widget, var: tk.StringVar, text: str, font: tuple) -> None:
        """Faint example text inside an empty field.

        It stays while the field is focused and empty, as placeholders do; it
        sits a hair right of the caret so the caret stays visible.
        """
        hint = tk.Label(
            widget,
            text=text,
            bg=self.pal.field_bg,
            fg=self.pal.faint,
            font=font,
            padx=0,
            pady=0,
            cursor="xterm",
        )
        hint.bind("<Button-1>", lambda _e: widget.focus_set())

        def sync(*_args: object) -> None:
            try:
                if var.get():
                    hint.place_forget()
                else:
                    hint.place(x=FIELD_INSET + 3, rely=0.5, anchor="w")
            except tk.TclError:
                pass

        var.trace_add("write", sync)
        sync()

    def text_field(self, parent: tk.Misc, height: int = 4, mono: bool = True) -> tk.Text:
        """A multi-line field styled like `entry`."""
        widget = tk.Text(
            parent,
            height=height,
            bg=self.pal.field_bg,
            fg=self.pal.fg,
            insertbackground=self.pal.fg,
            insertwidth=1,
            selectbackground=self.pal.panel_selected,
            selectforeground=self.pal.fg,
            relief="flat",
            font=self.fm(SIZE_SMALL) if mono else self.f(SIZE_SMALL),
            highlightthickness=1,
            highlightbackground=self.pal.field_border,
            highlightcolor=self.pal.muted,
            padx=FIELD_INSET,
            pady=FIELD_INSET,
            wrap="word",
            undo=True,
        )
        self._field_border(widget)
        return widget

    def checkbox(
        self,
        parent: tk.Misc,
        text: str = "",
        value: bool = False,
        bg: str | None = None,
        variable: tk.BooleanVar | None = None,
    ) -> tk.Frame:
        """A square check drawn in the palette, with its label beside it.

        Clicking the box or the text toggles it; so does Space when it has
        the keyboard focus, which Tab gives it like any native checkbox.
        """
        pal = self.pal
        ground = bg or ground_of(parent, pal.bg)
        var = variable if variable is not None else tk.BooleanVar(value=value)
        holder = tk.Frame(parent, bg=ground, cursor="hand2", takefocus=1, highlightthickness=0)
        size = 16
        box = tk.Canvas(
            holder,
            width=size,
            height=size,
            bg=ground,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
        )
        box.pack(side="left", pady=2)
        caption = None
        if text:
            caption = tk.Label(
                holder,
                text=text,
                bg=ground,
                fg=pal.fg,
                font=self.f(SIZE_SMALL),
                padx=0,
                pady=0,
                cursor="hand2",
            )
            caption.pack(side="left", padx=(self.m.gap_sm, 0))
        state = {"hover": False, "focus": False}

        def draw(*_args: object) -> None:
            try:
                box.delete("all")
                checked = bool(var.get())
            except tk.TclError:
                return
            if checked:
                edge = pal.accent_hover if state["hover"] else pal.accent
                box.create_rectangle(1, 1, size - 2, size - 2, fill=edge, outline=edge)
                box.create_line(
                    4,
                    8.5,
                    7,
                    11.5,
                    12,
                    5,
                    fill=pal.on_accent,
                    width=2,
                    capstyle="round",
                    joinstyle="round",
                )
            else:
                edge = (
                    pal.muted
                    if state["focus"]
                    else (pal.border_strong if state["hover"] else pal.field_border)
                )
                box.create_rectangle(1, 1, size - 2, size - 2, fill=pal.field_bg, outline=edge)
            if state["focus"] and checked:
                box.create_rectangle(0, 0, size - 1, size - 1, outline=pal.muted)

        def toggle(_event: tk.Event | None = None) -> str:
            var.set(not bool(var.get()))
            holder.focus_set()
            return "break"

        def hover(on: bool) -> None:
            state["hover"] = on
            draw()

        def focus(on: bool) -> None:
            state["focus"] = on
            draw()

        for widget in (holder, box, caption):
            if widget is None:
                continue
            widget.bind("<Button-1>", toggle)
            widget.bind("<Enter>", lambda _e: hover(True))
            widget.bind("<Leave>", lambda _e: hover(False))
        holder.bind("<space>", toggle)
        holder.bind("<FocusIn>", lambda _e: focus(True))
        holder.bind("<FocusOut>", lambda _e: focus(False))
        trace = var.trace_add("write", draw)
        holder.bind("<Destroy>", lambda _e: _untrace(var, trace), add="+")
        draw()
        holder.var = var  # type: ignore[attr-defined]
        return holder

    def choice(
        self,
        parent: tk.Misc,
        value: str,
        options: Sequence[str],
        command: Callable[[str], None] | None = None,
        width: int | None = None,
    ) -> tk.Frame:
        """A drop-down: the current value, a chevron, and a menu on click."""
        pal = self.pal
        options = list(options or [value])
        var = tk.StringVar(value=value)
        holder = tk.Frame(
            parent,
            bg=pal.field_bg,
            highlightthickness=1,
            highlightbackground=pal.field_border,
            highlightcolor=pal.muted,
            cursor="hand2",
            takefocus=1,
        )
        shown = tk.Label(
            holder,
            textvariable=var,
            bg=pal.field_bg,
            fg=pal.fg,
            font=self.f(SIZE_SMALL),
            anchor="w",
            width=width or max(len(option) for option in options) + 2,
            padx=FIELD_INSET + 2,
            pady=self.m.control_pad_y - 1,
            cursor="hand2",
        )
        shown.pack(side="left", fill="x", expand=True)
        chevron = icons.draw(holder, "chevron-down", 14, pal.muted, pal.field_bg)
        chevron.configure(cursor="hand2")
        chevron.pack(side="right", padx=(0, FIELD_INSET))

        menu = tk.Menu(
            holder,
            tearoff=0,
            bg=pal.elevated,
            fg=pal.fg,
            activebackground=pal.panel_selected,
            activeforeground=pal.fg,
            selectcolor=pal.fg,
            font=self.f(SIZE_SMALL),
            relief="flat",
            borderwidth=1,
            activeborderwidth=0,
        )

        def picked(option: str) -> None:
            if command is not None:
                command(option)

        for option in options:
            menu.add_radiobutton(
                label=option, value=option, variable=var, command=lambda o=option: picked(o)
            )

        def open_menu(_event: tk.Event | None = None) -> str:
            holder.focus_set()
            try:
                menu.tk_popup(holder.winfo_rootx(), holder.winfo_rooty() + holder.winfo_height())
            finally:
                menu.grab_release()
            return "break"

        for widget in (holder, shown, chevron):
            widget.bind("<Button-1>", open_menu)
        holder.bind("<space>", open_menu)
        holder.bind("<Return>", open_menu)
        self._field_border(holder)
        holder.var = var  # type: ignore[attr-defined]
        holder.menu = menu  # type: ignore[attr-defined]
        return holder

    def segmented(
        self,
        parent: tk.Misc,
        options: Sequence[tuple[str, str]],
        variable: tk.StringVar,
        command: Callable[[], None] | None = None,
    ) -> tk.Frame:
        """A row of tabs in one outlined track — the modern shape of a radio group."""
        pal = self.pal
        edge = tk.Frame(parent, bg=pal.border)
        holder = tk.Frame(edge, bg=pal.panel, padx=2, pady=2)
        holder.pack(padx=1, pady=1)
        buttons: dict[str, tk.Label] = {}

        def paint(value: str, key: str, hovering: bool = False) -> None:
            chosen = key == value
            buttons[key].configure(
                bg=pal.panel_selected if chosen else pal.panel,
                fg=pal.fg if chosen or hovering else pal.muted,
            )

        def select(value: str, notify: bool = True) -> None:
            variable.set(value)
            for key in buttons:
                paint(value, key)
            # The initial paint must not fire the callback: the page is still
            # being built, and the widgets it reads may not exist yet.
            if command is not None and notify:
                command()

        for label, value in options:
            button = tk.Label(
                holder,
                text=label,
                bg=pal.panel,
                fg=pal.muted,
                font=self.f(SIZE_SMALL),
                padx=self.m.gap,
                pady=self.m.control_pad_y - 2,
                cursor="hand2",
            )
            button.pack(side="left")
            button.bind("<Button-1>", lambda _e, v=value: select(v))
            button.bind("<Enter>", lambda _e, v=value: paint(variable.get(), v, True))
            button.bind("<Leave>", lambda _e, v=value: paint(variable.get(), v, False))
            buttons[value] = button
        edge.select = select  # type: ignore[attr-defined]
        select(variable.get() or options[0][1], notify=False)
        return edge

    # ── read-only surfaces ───────────────────────────────────────────────
    def listbox(self, parent: tk.Misc, height: int = 12) -> tk.Listbox:
        return tk.Listbox(
            parent,
            bg=self.pal.panel,
            fg=self.pal.fg,
            selectbackground=self.pal.panel_selected,
            selectforeground=self.pal.fg,
            relief="flat",
            font=self.fm(SIZE_SMALL),
            highlightthickness=0,
            activestyle="none",
            height=height,
        )

    def textbox(self, parent: tk.Misc, height: int = 12) -> tk.Text:
        """A read-only, monospaced output area."""
        return tk.Text(
            parent,
            height=height,
            bg=self.pal.panel,
            fg=self.pal.fg,
            insertbackground=self.pal.fg,
            selectbackground=self.pal.panel_selected,
            selectforeground=self.pal.fg,
            relief="flat",
            font=self.fm(SIZE_SMALL),
            highlightthickness=1,
            highlightbackground=self.pal.border,
            highlightcolor=self.pal.border,
            wrap="none",
            state="disabled",
            padx=self.m.gap,
            pady=self.m.gap_sm + 2,
            spacing1=1,
            spacing3=1,
        )

    def scrollbar(
        self, parent: tk.Misc, command: Callable[..., object], ground: str | None = None
    ) -> Scrollbar:
        """A thin scrollbar that belongs to the palette. Tk's default is a
        bevelled slab with arrow buttons that ruins an otherwise quiet window."""
        return Scrollbar(parent, command, self.pal, ground or ground_of(parent, self.pal.bg))

    def scrollable(self, parent: tk.Misc, root: tk.Misc | None = None) -> tk.Frame:
        """A vertically scrolling frame; returns the frame to fill."""
        ground = ground_of(parent, self.pal.bg)
        canvas = tk.Canvas(parent, bg=ground, highlightthickness=0, borderwidth=0)
        bar = self.scrollbar(parent, canvas.yview, ground)
        inner = tk.Frame(canvas, bg=ground)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y", padx=(self.m.gap_sm, 0))
        canvas.pack(side="left", fill="both", expand=True)

        def wheel(event: tk.Event) -> None:
            if bar.first > 0 or bar.last < 1:
                canvas.yview_scroll(wheel_steps(event), "units")

        # bind_all so the wheel works over the labels inside; callers clear it
        # again when another view takes over the content area.
        binder = root or parent.winfo_toplevel()
        for sequence in WHEEL_EVENTS:
            binder.bind_all(sequence, wheel)
        return inner

    def rows(self, parent: tk.Misc, **kwargs) -> RowList:
        """A selectable list of rows. See `RowList`."""
        return RowList(self, parent, **kwargs)

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def fill(widget: tk.Text, lines: Iterable[str], empty: str = "") -> None:
        text = "\n".join(lines)
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text or empty)
        widget.configure(state="disabled")

    def chrome(self, window: tk.Misc, title: str) -> None:
        """Window title, icon and ground colour — the three things every
        window gets wrong separately if they are set separately."""
        try:
            window.title(title)
        except tk.TclError:
            pass
        window.configure(bg=self.pal.bg)
        branding.apply_window_icon(window)

    def icon(
        self,
        parent: tk.Misc,
        name: str,
        size: int = 18,
        colour: str | None = None,
        bg: str | None = None,
    ) -> tk.Canvas:
        """One outline icon."""
        return icons.draw(
            parent, name, size, colour or self.pal.fg, bg or ground_of(parent, self.pal.bg)
        )

    def brand(self, parent: tk.Misc, size: int = 22, bg: str | None = None) -> tk.Widget:
        """The app mark: the same terminal prompt the OS icon carries, drawn
        as a stroke so it sits at the same weight as the rest of the chrome."""
        ground = bg or self.pal.bg
        try:
            return self.icon(parent, "terminal", size, self.pal.fg, ground)
        except Exception:
            return self.label(parent, "palaunch", bg=ground, size=SIZE_TITLE, bold=True)


def _untrace(var: tk.Variable, trace: str) -> None:
    try:
        var.trace_remove("write", trace)
    except (tk.TclError, ValueError):
        pass


class Scrollbar(tk.Canvas):
    """An eight-pixel track with a thumb and nothing else.

    Speaks the same protocol as `tk.Scrollbar` — `set(first, last)` from the
    scrolled widget, `command("moveto" | "scroll", …)` back to it — so it
    drops in wherever a scrollbar is expected. The thumb hides when there is
    nothing to scroll.
    """

    WIDTH = 8
    MIN_THUMB = 28

    def __init__(
        self, parent: tk.Misc, command: Callable[..., object], pal: theme.Palette, ground: str
    ) -> None:
        super().__init__(
            parent,
            width=self.WIDTH,
            bg=ground,
            highlightthickness=0,
            borderwidth=0,
            takefocus=0,
        )
        self.command = command
        self.pal = pal
        self.first, self.last = 0.0, 1.0
        self._grab: float | None = None
        self.thumb = self.create_rectangle(0, 0, 0, 0, fill=pal.border_strong, outline="")
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self.itemconfigure(self.thumb, fill=pal.faint))
        self.bind("<Leave>", lambda _e: self._rest())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)

    def set(self, first: str | float, last: str | float) -> None:  # type: ignore[override]
        self.first, self.last = float(first), float(last)
        self._draw()

    def _span(self) -> tuple[float, float]:
        height = max(self.winfo_height(), 1)
        top = self.first * height
        bottom = max(self.last * height, top + self.MIN_THUMB)
        if bottom > height:
            top, bottom = height - (bottom - top), height
        return top, bottom

    def _draw(self) -> None:
        if self.first <= 0 and self.last >= 1:
            self.coords(self.thumb, 0, 0, 0, 0)
            return
        top, bottom = self._span()
        self.coords(self.thumb, 1, top, self.WIDTH - 1, bottom)

    def _rest(self) -> None:
        if self._grab is None:
            self.itemconfigure(self.thumb, fill=self.pal.border_strong)

    def _press(self, event: tk.Event) -> None:
        if self.first <= 0 and self.last >= 1:
            return
        top, bottom = self._span()
        if top <= event.y <= bottom:
            self._grab = event.y - top
        else:
            self.command("scroll", 1 if event.y > bottom else -1, "pages")

    def _drag(self, event: tk.Event) -> None:
        if self._grab is None:
            return
        height = max(self.winfo_height(), 1)
        self.command("moveto", max(0.0, (event.y - self._grab) / height))

    def _release(self, _event: tk.Event) -> None:
        self._grab = None
        self._rest()


@dataclass
class ListRow:
    """One line of a `RowList`."""

    title: str
    detail: str = ""  # a second, quieter line
    meta: str = ""  # right-aligned, e.g. "12 steps"
    tags: tuple[str, ...] = ()  # small outlined labels before the meta
    lead: str = ""  # a fixed-width prefix, e.g. a step number
    dimmed: bool = False  # drawn in the muted tone — a disabled step


class RowList(tk.Frame):
    """A selectable, scrolling list of rows — title, detail, tags and meta.

    It replaces `tk.Listbox`, which can only show one font in one colour and
    so turned every list into columns of padded monospace text. Keyboard use
    matches a listbox: click or Tab to focus, ↑/↓ to move, Enter or a double
    click to activate. The selection API — `curselection`, `selection_set`,
    `selection_clear`, `get` — is a listbox's too, so callers read the same.
    """

    def __init__(
        self,
        kit: Kit,
        parent: tk.Misc,
        *,
        empty: str = "Nothing here yet.",
        on_select: Callable[[int], None] | None = None,
        on_activate: Callable[[int], None] | None = None,
        mono_detail: bool = False,
        height: int = 200,
    ) -> None:
        pal = kit.pal
        super().__init__(parent, bg=pal.border)
        self.kit = kit
        self.pal = pal
        self.empty_text = empty
        self.on_select = on_select
        self.on_activate = on_activate
        self.mono_detail = mono_detail
        self.items: list[ListRow] = []
        self.row_widgets: list[tk.Frame] = []
        self.selected: int | None = None
        self.hovered: int | None = None
        self.focused = False

        body = tk.Frame(self, bg=pal.panel)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        self.canvas = tk.Canvas(
            body, bg=pal.panel, highlightthickness=0, borderwidth=0, height=height, takefocus=1
        )
        self.bar = kit.scrollbar(body, self.canvas.yview, pal.panel)
        self.bar.pack(side="right", fill="y", padx=(0, 3), pady=3)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=pal.panel, pady=4)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.inner.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width)
        )
        for sequence, handler in (
            ("<Up>", lambda _e: self.move(-1)),
            ("<Down>", lambda _e: self.move(1)),
            ("<Home>", lambda _e: self._select(0, notify=True) if self.items else None),
            ("<End>", lambda _e: self._select(len(self.items) - 1, notify=True)),
            ("<Return>", lambda _e: self._activate()),
            ("<FocusIn>", lambda _e: self._focus(True)),
            ("<FocusOut>", lambda _e: self._focus(False)),
        ):
            self.canvas.bind(sequence, handler)
        self._bind_wheel(self.canvas)
        self._bind_wheel(self.inner)
        self.set_rows([])

    # ── listbox-shaped API ───────────────────────────────────────────────
    def set_rows(self, rows: Sequence[ListRow], select: int | None = 0) -> None:
        """Replace every row. Selects `select` (clamped), or nothing if None."""
        for widget in self.row_widgets:
            widget.destroy()
        for child in self.inner.winfo_children():
            child.destroy()
        self.row_widgets = []
        self.items = list(rows)
        self.hovered = None
        if not self.items:
            self.selected = None
            note = tk.Label(
                self.inner,
                text=self.empty_text,
                bg=self.pal.panel,
                fg=self.pal.faint,
                font=self.kit.f(SIZE_SMALL),
                pady=self.m_gap * 2,
            )
            note.pack(fill="x")
            self._bind_wheel(note)
            self.canvas.yview_moveto(0)
            return
        for index, row in enumerate(self.items):
            self.row_widgets.append(self._build_row(index, row))
        self.selected = None
        if select is not None:
            self._select(min(max(select, 0), len(self.items) - 1), notify=False)
        self.canvas.yview_moveto(0)

    def curselection(self) -> tuple[int, ...]:
        return () if self.selected is None else (self.selected,)

    def selection_set(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self._select(index, notify=False)

    def selection_clear(self, first: int = 0, last: int | str | None = None) -> None:
        if self.selected is None:
            return
        end = len(self.items) - 1 if last in (None, "end") else int(last)  # type: ignore[arg-type]
        if first <= self.selected <= end:
            previous, self.selected = self.selected, None
            self._paint(previous)

    def get(self, index: int) -> str:
        row = self.items[index]
        return " ".join(part for part in (row.title, row.detail, row.meta) if part)

    def size(self) -> int:
        return len(self.items)

    def move(self, delta: int) -> str:
        if not self.items:
            return "break"
        current = self.selected if self.selected is not None else (-1 if delta > 0 else 0)
        self._select(max(0, min(len(self.items) - 1, current + delta)), notify=True)
        return "break"

    def see(self, index: int) -> None:
        if not 0 <= index < len(self.row_widgets):
            return
        self.update_idletasks()
        total = max(self.inner.winfo_height(), 1)
        view = self.canvas.winfo_height()
        widget = self.row_widgets[index]
        top, bottom = widget.winfo_y(), widget.winfo_y() + widget.winfo_height()
        first, last = self.canvas.yview()
        if top < first * total:
            self.canvas.yview_moveto(max(0.0, (top - 4) / total))
        elif bottom > last * total:
            self.canvas.yview_moveto(max(0.0, (bottom + 4 - view) / total))

    # ── internals ────────────────────────────────────────────────────────
    @property
    def m_gap(self) -> int:
        return self.kit.m.gap

    def _build_row(self, index: int, row: ListRow) -> tk.Frame:
        kit, pal = self.kit, self.pal
        frame = tk.Frame(self.inner, bg=pal.panel, padx=self.kit.m.gap, pady=7)
        frame.pack(fill="x", padx=4)
        bar = tk.Frame(frame, bg=pal.panel, width=2)
        bar.place(x=-self.kit.m.gap + 2, rely=0.18, relheight=0.64)
        frame.bar = bar  # type: ignore[attr-defined]
        frame.tags = []  # type: ignore[attr-defined]

        if row.lead:
            lead = tk.Label(
                frame,
                text=row.lead,
                bg=pal.panel,
                fg=pal.faint,
                font=kit.fm(SIZE_TINY),
                width=3,
                anchor="e",
                padx=0,
            )
            lead.pack(side="left", anchor="n" if row.detail else "center", padx=(0, 10))
        if row.meta:
            tk.Label(
                frame, text=row.meta, bg=pal.panel, fg=pal.faint, font=kit.f(SIZE_TINY), padx=0
            ).pack(side="right", padx=(self.kit.m.gap_sm, 0))
        for tag in reversed(row.tags):
            edge = tk.Frame(frame, bg=pal.border_strong)
            edge.pack(side="right", padx=(6, 0))
            chip = tk.Label(
                edge,
                text=tag,
                bg=pal.panel,
                fg=pal.muted,
                font=kit.f(SIZE_TINY),
                padx=5,
                pady=0,
            )
            chip.pack(padx=1, pady=1)
            frame.tags.append((edge, chip))  # type: ignore[attr-defined]

        text = tk.Frame(frame, bg=pal.panel)
        text.pack(side="left", fill="x", expand=True)
        title = tk.Label(
            text,
            bg=pal.panel,
            fg=pal.faint if row.dimmed else pal.fg,
            font=kit.f(SIZE_SMALL, bold=True),
            anchor="w",
            padx=0,
            pady=0,
        )
        title.pack(fill="x")
        kit.elide(title, row.title)
        if row.detail:
            detail = tk.Label(
                text,
                bg=pal.panel,
                fg=pal.faint if row.dimmed else pal.muted,
                font=kit.fm(SIZE_TINY) if self.mono_detail else kit.f(SIZE_TINY),
                anchor="w",
                padx=0,
                pady=0,
            )
            detail.pack(fill="x", pady=(1, 0))
            kit.elide(detail, row.detail)

        for widget in _descendants(frame):
            if widget is bar:
                continue
            widget.bind("<Enter>", lambda _e, i=index: self._hover(i))
            widget.bind("<Leave>", lambda _e, i=index: self._unhover(i))
            widget.bind("<Button-1>", lambda _e, i=index: self._click(i))
            widget.bind("<Double-Button-1>", lambda _e, i=index: self._activate(i))
            self._bind_wheel(widget)
        return frame

    def _bind_wheel(self, widget: tk.Misc) -> None:
        def wheel(event: tk.Event) -> str:
            if self.bar.first > 0 or self.bar.last < 1:
                self.canvas.yview_scroll(wheel_steps(event), "units")
            return "break"  # keep an enclosing scroll area still

        for sequence in WHEEL_EVENTS:
            widget.bind(sequence, wheel)

    def _paint(self, index: int | None) -> None:
        if index is None or not 0 <= index < len(self.row_widgets):
            return
        pal = self.pal
        frame = self.row_widgets[index]
        chosen = index == self.selected
        ground = (
            pal.panel_selected
            if chosen
            else pal.panel_hover
            if index == self.hovered
            else pal.panel
        )
        for widget in _descendants(frame):
            if widget is frame.bar:  # type: ignore[attr-defined]
                continue
            if any(widget is edge for edge, _chip in frame.tags):  # type: ignore[attr-defined]
                continue
            widget.configure(bg=ground)
        mark = pal.accent if chosen and self.focused else pal.border_strong if chosen else ground
        frame.bar.configure(bg=mark)  # type: ignore[attr-defined]

    def _select(self, index: int, notify: bool) -> None:
        previous, self.selected = self.selected, index
        self._paint(previous)
        self._paint(index)
        self.see(index)
        if notify and self.on_select is not None and previous != index:
            self.on_select(index)

    def _hover(self, index: int) -> None:
        previous, self.hovered = self.hovered, index
        if previous != index:
            self._paint(previous)
            self._paint(index)

    def _unhover(self, index: int) -> None:
        try:
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            under = None
        frame = self.row_widgets[index] if index < len(self.row_widgets) else None
        if frame is not None and under is not None and str(under).startswith(str(frame)):
            return
        if self.hovered == index:
            self.hovered = None
            self._paint(index)

    def _click(self, index: int) -> None:
        self.canvas.focus_set()
        self._select(index, notify=True)

    def _activate(self, index: int | None = None) -> str:
        if index is not None:
            self._select(index, notify=True)
        if self.selected is not None and self.on_activate is not None:
            self.on_activate(self.selected)
        return "break"

    def _focus(self, on: bool) -> None:
        self.focused = on
        self._paint(self.selected)
