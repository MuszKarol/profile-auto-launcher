"""The setup wizard — a real installer window, not a shell script.

Three pages: what will happen, what to include, and what happened. It runs the
same `launcher.install` operations the CLI does, on a worker thread, streaming
into the log so nothing looks frozen while files are copied.

Reachable as `palaunch install`, as the `palaunch-setup` entry point, and from
the manager's Settings page. `--uninstall` runs the same window in reverse.
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path

from launcher import install as ops
from launcher import panel_model, theme, ui
from launcher.theme import SIZE_BODY, SIZE_DISPLAY, SIZE_SMALL, SIZE_TINY
from launcher.ui import Kit


class _Installer:
    def __init__(self, parent: tk.Misc | None = None, uninstall: bool = False) -> None:
        self.kit = Kit()
        self.pal = self.kit.pal
        self.m = theme.METRICS
        self.parent = parent
        self.uninstall = uninstall
        self.messages: queue.Queue[str] = queue.Queue()
        self.done = threading.Event()
        self.notes: list[str] = []
        self.page = 0

        self.root = ui.new_window(parent)
        title = "Uninstall Profile Auto Launcher" if uninstall else "Install Profile Auto Launcher"
        self.kit.chrome(self.root, title)
        self.root.geometry("620x520")
        self.root.resizable(False, False)

        self.body = self.kit.frame(self.root, padx=self.m.gap_xl, pady=self.m.gap_xl)
        self.body.pack(fill="both", expand=True)
        self.footer = self.kit.frame(self.root, padx=self.m.gap_xl, pady=self.m.gap)
        self.footer.pack(fill="x", side="bottom")

        self.target = tk.StringVar(value=str(ops.default_target()))
        self.autostart = tk.BooleanVar(value=True)
        self.shortcut = tk.BooleanVar(value=True)
        self.samples = tk.BooleanVar(value=True)
        self._show_welcome()

    # ── page plumbing ────────────────────────────────────────────────────
    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        for child in self.footer.winfo_children():
            child.destroy()

    def _header(self, title: str, subtitle: str) -> None:
        kit = self.kit
        head = kit.frame(self.body)
        head.pack(fill="x", pady=(0, self.m.gap_lg))
        kit.brand(head, 40).pack(side="left", padx=(0, self.m.gap))
        text = kit.frame(head)
        text.pack(side="left", fill="x", expand=True)
        kit.label(text, title, size=SIZE_DISPLAY, bold=True, anchor="w").pack(fill="x")
        kit.label(text, subtitle, fg=self.pal.muted, size=SIZE_SMALL, anchor="w").pack(fill="x")

    # ── page 1: welcome ──────────────────────────────────────────────────
    def _show_welcome(self) -> None:
        self._clear()
        kit = self.kit
        if self.uninstall:
            self._header(
                "Uninstall", "Removes what setup created. Your profiles and settings stay."
            )
            card = kit.card(self.body)
            card.pack(fill="both", expand=True)
            entries = ops.installed_entries()
            lines = [str(path) for path in entries] or ["nothing to remove — setup never ran here"]
            for line in lines:
                kit.label(
                    card.inner,
                    f"·  {line}",
                    bg=self.pal.panel,
                    size=SIZE_SMALL,
                    anchor="w",
                    justify="left",
                    wraplength=520,
                ).pack(fill="x", pady=1)
            kit.button(self.footer, "Cancel", self.root.destroy, kind="quiet")
            kit.button(
                self.footer, "Uninstall", self._start, kind="danger", pack=True, side="right"
            )
            return

        self._header(
            f"Profile Auto Launcher {panel_model.version()}",
            "Open a whole working context — apps, tabs, services — with one keystroke.",
        )
        card = kit.card(self.body)
        card.pack(fill="both", expand=True)
        inner = card.inner
        kit.label(inner, "This will:", bg=self.pal.panel, size=SIZE_BODY, bold=True).pack(
            anchor="w", pady=(0, self.m.gap_sm)
        )
        for line in ops.plan(self._options()):
            kit.label(
                inner,
                f"·  {line}",
                bg=self.pal.panel,
                fg=self.pal.muted,
                size=SIZE_SMALL,
                anchor="w",
            ).pack(fill="x", pady=2)
        kit.label(
            inner,
            "\nNothing outside your user account is touched — no administrator rights needed.",
            bg=self.pal.panel,
            fg=self.pal.faint,
            size=SIZE_TINY,
            anchor="w",
            justify="left",
        ).pack(fill="x")

        kit.button(self.footer, "Cancel", self.root.destroy, kind="quiet")
        # side="right" stacks right-to-left, so the primary action is packed
        # first to end up in the far corner where it belongs.
        kit.button(self.footer, "Install", self._start, kind="primary", pack=True, side="right")
        kit.button(self.footer, "Options", self._show_options, pack=True, side="right")

    # ── page 2: options ──────────────────────────────────────────────────
    def _show_options(self) -> None:
        self._clear()
        kit = self.kit
        self._header("Options", "Everything here can be changed later from the manager.")
        card = kit.card(self.body)
        card.pack(fill="both", expand=True)
        inner = card.inner

        if ops.is_frozen():
            kit.label(inner, "Install location", bg=self.pal.panel, size=SIZE_SMALL).pack(
                anchor="w"
            )
            row = tk.Frame(inner, bg=self.pal.panel)
            row.pack(fill="x", pady=(2, self.m.gap))
            entry = kit.entry(row, self.target.get(), width=52)
            entry.pack(side="left")
            entry.var.trace_add("write", lambda *_: self.target.set(entry.var.get()))
            kit.button(row, "Browse…", lambda: self._browse(entry), kind="quiet")
        else:
            kit.label(
                inner,
                f"Using the installed palaunch at {ops.program()}",
                bg=self.pal.panel,
                fg=self.pal.muted,
                size=SIZE_SMALL,
                anchor="w",
            ).pack(fill="x", pady=(0, self.m.gap))

        for variable, label in (
            (self.autostart, "Start the tray when I log in"),
            (self.shortcut, "Add a menu shortcut"),
            (self.samples, "Copy the sample profiles"),
        ):
            box = tk.Checkbutton(
                inner,
                text=label,
                variable=variable,
                bg=self.pal.panel,
                fg=self.pal.fg,
                selectcolor=self.pal.field_bg,
                activebackground=self.pal.panel,
                activeforeground=self.pal.fg,
                font=kit.f(SIZE_SMALL),
                highlightthickness=0,
                borderwidth=0,
                anchor="w",
            )
            box.pack(fill="x", pady=3)

        kit.button(self.footer, "Back", self._show_welcome, kind="quiet")
        kit.button(self.footer, "Install", self._start, kind="primary", pack=True, side="right")

    def _browse(self, entry: tk.Entry) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(parent=self.root, initialdir=entry.var.get())
        if chosen:
            entry.var.set(chosen)
            self.target.set(chosen)

    # ── page 3: progress and result ──────────────────────────────────────
    def _options(self) -> ops.Options:
        return ops.Options(
            target=Path(self.target.get()) if self.target.get() else None,
            autostart=bool(self.autostart.get()),
            shortcut=bool(self.shortcut.get()),
            sample_profiles=bool(self.samples.get()),
        )

    def _start(self) -> None:
        self._clear()
        kit = self.kit
        self._header("Uninstalling…" if self.uninstall else "Installing…", "This takes a moment.")
        self.output = kit.textbox(self.body, height=14)
        self.output.pack(fill="both", expand=True)
        self.close_button = kit.button(
            self.footer, "Close", self.root.destroy, kind="quiet", pack=False
        )
        self.close_button.pack(side="right")

        options = self._options()
        uninstalling = self.uninstall

        def worker() -> None:
            try:
                if uninstalling:
                    ops.uninstall(self.messages.put)
                    self.notes = ["Removed. Your profiles and settings are untouched."]
                else:
                    self.notes = ops.install(options, self.messages.put)
            except Exception as exc:  # a failed install must still report
                self.messages.put(f"✗ {exc}")
                self.notes = [f"Setup did not finish: {exc}"]
            finally:
                self.done.set()

        threading.Thread(target=worker, daemon=True, name="pal-install").start()
        self.root.after(80, self._drain)

    def _drain(self) -> None:
        if not self.root.winfo_exists():
            return
        lines = []
        while True:
            try:
                lines.append(self.messages.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.output.configure(state="normal")
            self.output.insert("end", "\n".join(lines) + "\n")
            self.output.configure(state="disabled")
            self.output.see("end")
        if self.done.is_set() and not lines:
            self._show_done()
            return
        self.root.after(80, self._drain)

    def _show_done(self) -> None:
        kit = self.kit
        for child in self.footer.winfo_children():
            child.destroy()
        heading = kit.label(
            self.body,
            "✓ Done"
            if self.notes and "not finish" not in self.notes[0]
            else "Finished with errors",
            fg=self.pal.ok,
            size=SIZE_BODY,
            bold=True,
            anchor="w",
        )
        heading.pack(fill="x", pady=(self.m.gap, 0))
        for note in self.notes:
            kit.label(self.body, note, fg=self.pal.muted, size=SIZE_SMALL, anchor="w").pack(
                fill="x"
            )
        kit.button(self.footer, "Close", self.root.destroy, kind="quiet")
        if not self.uninstall:
            kit.button(self.footer, "Open the launcher", self._launch, kind="primary", side="right")

    def _launch(self) -> None:
        from launcher.executor import spawn_detached

        exe = ops.windowed_program(ops.program())
        try:
            spawn_detached([str(exe), "pick"])
        except OSError:
            pass
        self.root.destroy()

    # ── lifecycle ────────────────────────────────────────────────────────
    def run(self) -> None:
        ui.show_window(self.root, self.parent)


def open_installer(parent: tk.Misc | None = None, uninstall: bool = False) -> int:
    """Open the setup window. Returns a process exit code."""
    try:
        wizard = _Installer(parent, uninstall=uninstall)
    except tk.TclError as exc:
        print(f"Could not open the setup window: {exc}", file=sys.stderr)
        return 1
    wizard.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    """The `palaunch-setup` entry point."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    uninstall = "--uninstall" in arguments
    if "--cli" in arguments:
        if uninstall:
            ops.uninstall()
        else:
            for note in ops.install():
                print(note)
        return 0
    return open_installer(uninstall=uninstall)


if __name__ == "__main__":
    sys.exit(main())
