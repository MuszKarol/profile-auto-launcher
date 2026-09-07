"""The widget kit every window is built from.

Tk's stock widgets look like 1995 and, worse, ignore colours inconsistently
across platforms: a `tk.Button` on macOS keeps its native grey whatever `bg`
says. So buttons, chips and tabs here are labels with their own hover states,
which behave identically on all three platforms and let the palette in
`launcher.theme` actually decide what the app looks like.

Nothing in here knows about profiles, settings or steps — it is presentation
only, shared by the launcher HUD, the manager window and the profile editor.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable, Sequence

from launcher import branding, theme
from launcher.theme import SIZE_BODY, SIZE_SMALL, SIZE_TINY, SIZE_TITLE

BUTTON_KINDS = ("primary", "ghost", "quiet", "danger")


class Kit:
    """Palette, fonts and widget factories for one window."""

    def __init__(self, palette: theme.Palette | None = None) -> None:
        self.pal = palette or theme.palette()
        self.font = theme.font_family()
        self.mono = theme.mono_family()
        self.m = theme.METRICS

    # ── fonts ────────────────────────────────────────────────────────────
    def f(self, size: int = SIZE_BODY, bold: bool = False) -> tuple:
        return (self.font, size, "bold") if bold else (self.font, size)

    def fm(self, size: int = SIZE_SMALL) -> tuple:
        return (self.mono, size)

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

    def heading(self, parent: tk.Misc, text: str, hint: str = "", bg: str | None = None) -> None:
        ground = bg or self.pal.bg
        self.label(parent, text, size=SIZE_TITLE, bold=True, bg=ground).pack(anchor="w")
        if hint:
            self.label(parent, hint, size=SIZE_SMALL, fg=self.pal.muted, bg=ground).pack(
                anchor="w", pady=(2, self.m.gap)
            )
        else:
            self.frame(parent, bg=ground, height=self.m.gap).pack()

    def section_label(self, parent: tk.Misc, text: str, bg: str | None = None) -> tk.Label:
        widget = self.label(
            parent,
            text.upper(),
            size=SIZE_TINY,
            bold=True,
            fg=self.pal.faint,
            bg=bg or self.pal.bg,
        )
        widget.pack(anchor="w", pady=(self.m.gap, self.m.gap_xs))
        return widget

    # ── buttons ──────────────────────────────────────────────────────────
    def button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        kind: str = "ghost",
        pack: bool = True,
        **pack_kwargs,
    ) -> tk.Label:
        """A label styled as a button, with its own hover and press states."""
        pal = self.pal
        colours = {
            "primary": (pal.accent, pal.on_accent, pal.accent_hover),
            "ghost": (pal.panel_hover, pal.fg, pal.panel_selected),
            "quiet": (pal.panel, pal.muted, pal.panel_hover),
            "danger": (pal.panel_hover, pal.err, pal.panel_selected),
        }[kind if kind in BUTTON_KINDS else "ghost"]
        background, foreground, hover = colours
        widget = tk.Label(
            parent,
            text=text,
            bg=background,
            fg=foreground,
            font=self.f(SIZE_SMALL, bold=kind == "primary"),
            padx=self.m.gap,
            pady=6,
            cursor="hand2",
        )
        widget.bind("<Enter>", lambda _e: widget.configure(bg=hover))
        widget.bind("<Leave>", lambda _e: widget.configure(bg=background))
        widget.bind("<Button-1>", lambda _e: command())
        if pack:
            options = {"side": "left", "padx": (0, self.m.gap_sm)}
            options.update(pack_kwargs)
            widget.pack(**options)
        return widget

    # ── inputs ───────────────────────────────────────────────────────────
    def entry(
        self,
        parent: tk.Misc,
        value: str = "",
        width: int = 24,
        mono: bool = True,
        show: str | None = None,
    ) -> tk.Entry:
        var = tk.StringVar(value=value)
        widget = tk.Entry(
            parent,
            textvariable=var,
            bg=self.pal.field_bg,
            fg=self.pal.fg,
            insertbackground=self.pal.accent,
            disabledbackground=self.pal.field_bg,
            readonlybackground=self.pal.field_bg,
            disabledforeground=self.pal.muted,
            relief="flat",
            font=self.fm(SIZE_SMALL) if mono else self.f(SIZE_SMALL),
            highlightthickness=1,
            highlightbackground=self.pal.field_border,
            highlightcolor=self.pal.accent,
            width=width,
            show=show or "",
        )
        widget.var = var  # type: ignore[attr-defined]
        return widget

    def checkbox(self, parent: tk.Misc, text: str = "", value: bool = False) -> tk.Checkbutton:
        var = tk.BooleanVar(value=value)
        widget = tk.Checkbutton(
            parent,
            text=text,
            variable=var,
            bg=self.pal.bg,
            fg=self.pal.fg,
            selectcolor=self.pal.field_bg,
            activebackground=self.pal.bg,
            activeforeground=self.pal.fg,
            font=self.f(SIZE_SMALL),
            highlightthickness=0,
            borderwidth=0,
            anchor="w",
        )
        widget.var = var  # type: ignore[attr-defined]
        return widget

    def choice(
        self, parent: tk.Misc, value: str, options: Sequence[str], **kwargs
    ) -> tk.OptionMenu:
        var = tk.StringVar(value=value)
        widget = tk.OptionMenu(parent, var, *(options or [value]), **kwargs)
        widget.configure(
            bg=self.pal.panel_hover,
            fg=self.pal.fg,
            activebackground=self.pal.panel_selected,
            activeforeground=self.pal.fg,
            relief="flat",
            highlightthickness=0,
            font=self.f(SIZE_SMALL),
            indicatoron=False,
            padx=self.m.gap_sm,
            pady=4,
            anchor="w",
        )
        widget["menu"].configure(
            bg=self.pal.elevated,
            fg=self.pal.fg,
            activebackground=self.pal.accent,
            activeforeground=self.pal.on_accent,
            font=self.f(SIZE_SMALL),
            borderwidth=0,
        )
        widget.var = var  # type: ignore[attr-defined]
        return widget

    def segmented(
        self,
        parent: tk.Misc,
        options: Sequence[tuple[str, str]],
        variable: tk.StringVar,
        command: Callable[[], None] | None = None,
    ) -> tk.Frame:
        """A row of pill tabs — the modern shape of a radio group."""
        holder = tk.Frame(parent, bg=self.pal.panel, padx=2, pady=2)
        buttons: dict[str, tk.Label] = {}

        def select(value: str, notify: bool = True) -> None:
            variable.set(value)
            for key, button in buttons.items():
                chosen = key == value
                button.configure(
                    bg=self.pal.accent if chosen else self.pal.panel,
                    fg=self.pal.on_accent if chosen else self.pal.muted,
                )
            # The initial paint must not fire the callback: the page is still
            # being built, and the widgets it reads may not exist yet.
            if command is not None and notify:
                command()

        for label, value in options:
            button = tk.Label(
                holder,
                text=label,
                bg=self.pal.panel,
                fg=self.pal.muted,
                font=self.f(SIZE_SMALL, bold=True),
                padx=self.m.gap,
                pady=5,
                cursor="hand2",
            )
            button.pack(side="left")
            button.bind("<Button-1>", lambda _e, v=value: select(v))
            buttons[value] = button
        holder.select = select  # type: ignore[attr-defined]
        select(variable.get() or options[0][1], notify=False)
        return holder

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
        return tk.Text(
            parent,
            height=height,
            bg=self.pal.field_bg,
            fg=self.pal.fg,
            insertbackground=self.pal.accent,
            relief="flat",
            font=self.fm(SIZE_SMALL),
            highlightthickness=1,
            highlightbackground=self.pal.border,
            wrap="none",
            state="disabled",
            padx=self.m.gap_sm,
            pady=self.m.gap_sm,
        )

    def scrollbar(self, parent: tk.Misc, command) -> tk.Scrollbar:
        """A scrollbar that belongs to the palette. Tk's default is a light
        grey slab that ruins an otherwise dark window."""
        return tk.Scrollbar(
            parent,
            orient="vertical",
            command=command,
            bg=self.pal.panel_hover,
            activebackground=self.pal.accent,
            troughcolor=self.pal.bg,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            width=10,
        )

    def scrollable(self, parent: tk.Misc, root: tk.Misc | None = None) -> tk.Frame:
        """A vertically scrolling frame; returns the frame to fill."""
        canvas = tk.Canvas(parent, bg=self.pal.bg, highlightthickness=0)
        bar = self.scrollbar(parent, canvas.yview)
        inner = tk.Frame(canvas, bg=self.pal.bg)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def wheel(event: tk.Event) -> None:
            button = getattr(event, "num", 0)
            step = 1 if button == 5 else -1 if button == 4 else (-1 if event.delta > 0 else 1)
            canvas.yview_scroll(step, "units")

        # bind_all so the wheel works over the labels inside; callers clear it
        # again when another view takes over the content area.
        binder = root or parent.winfo_toplevel()
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            binder.bind_all(sequence, wheel)
        return inner

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

    def brand(self, parent: tk.Misc, size: int = 22, bg: str | None = None) -> tk.Label:
        """The app mark, falling back to a text glyph if the image cannot load."""
        ground = bg or self.pal.bg
        try:
            image = branding.photo_image(size, master=parent)
            widget = tk.Label(parent, image=image, bg=ground)
            branding.hold(widget, image)
            return widget
        except Exception:
            return self.label(
                parent, "›_", bg=ground, fg=self.pal.accent, size=SIZE_TITLE, bold=True
            )
