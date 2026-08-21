"""The panel and the HUD driven as real Tk windows.

Skipped when no display can be opened; CI runs the Linux jobs under Xvfb so
these do execute there. What they check is the wiring the model cannot see:
that every page builds, that the widgets round-trip a value into
`settings.yaml`, and that the HUD's Settings row leads to the panel.
"""

from __future__ import annotations

import pytest

from launcher import panel_model as model
from launcher import settings

# ── the panel ────────────────────────────────────────────────────────────


def test_every_section_builds(panel):
    from launcher.panel import SECTIONS

    for section in SECTIONS:
        panel._show(section)
        panel.root.update()
        assert panel.section == section
        assert panel.content.winfo_children()


def test_the_settings_form_shows_the_stored_values(panel):
    settings.save({"theme": "light", "max_parallel": 2})
    panel._show("Settings")
    assert panel.submitted()["theme"] == "light"
    assert panel.submitted()["max_parallel"] == "2"


def test_saving_the_form_writes_settings_yaml(panel):
    panel.widgets["theme"][0].var.set("dark")
    panel.widgets["hotkey"][0].var.set("<ctrl>+<space>")
    panel._save_settings()

    from launcher.config import settings_path

    assert "theme: dark" in settings_path().read_text(encoding="utf-8")
    assert settings.load().hotkey == "<ctrl>+<space>"
    assert "Saved 2 change(s)" in panel.status.cget("text")


def test_saving_an_invalid_value_reports_it_and_writes_nothing(panel, monkeypatch):
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: None)
    panel.widgets["max_parallel"][0].var.set("lots")
    panel._save_settings()

    from launcher.config import settings_path

    assert not settings_path().exists()
    assert "whole number" in panel.status.cget("text")


def test_saving_nothing_says_so(panel):
    panel._save_settings()
    assert "No changes" in panel.status.cget("text")


def test_restore_defaults_fills_the_form_without_saving(panel):
    settings.save({"theme": "light"})
    panel._show("Settings")
    panel._restore_defaults()

    from launcher.config import settings_path

    assert panel.submitted()["theme"] == settings.Settings().theme
    assert "theme: light" in settings_path().read_text(encoding="utf-8")


def test_a_setting_forced_by_the_environment_is_called_out(gui, monkeypatch):
    monkeypatch.setenv("PAL_THEME", "dark")
    settings.reload()
    from launcher.panel import _Panel

    window = gui.build(_Panel)
    try:
        window.root.update()
        notes = [
            child.cget("text")
            for parent in window.content.winfo_children()
            for child in _descendants(parent)
            if hasattr(child, "cget") and "text" in child.configure()
        ]
        assert any("PAL_THEME" in str(note) for note in notes)
    finally:
        window.root.destroy()


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def test_the_profiles_page_lists_and_dry_runs_a_profile(panel, write_profile):
    write_profile("dev", "name: Dev\nsteps:\n  - {type: wait, name: pause, seconds: 0}\n")
    panel._show("Profiles")
    assert "Dev" in panel.profile_list.get(0)

    panel.profile_list.selection_set(0)
    panel._run_selected(dry=True)
    _settle(panel)
    assert "Dry run Dev" in panel.status.cget("text")
    assert "pause" in _console(panel)


def test_the_profiles_page_reports_a_failing_action(panel, monkeypatch):
    ghost = {
        "name": "Ghost",
        "icon": "",
        "description": "went away",
        "steps": 1,
        "default": False,
        "hotkey": "",
        "running": 0,
        "path": "",
    }
    monkeypatch.setattr(model, "profile_rows", lambda: [ghost])
    panel._show("Profiles")
    panel.profile_list.selection_set(0)
    panel._run_selected(dry=False)
    _settle(panel)
    assert "failed" in panel.status.cget("text")
    assert "disappeared" in _console(panel)


def test_actions_need_a_selection(panel):
    panel._show("Profiles")
    panel.profile_list.selection_clear(0, "end")
    panel._run_selected(dry=True)
    assert "Select a profile first" in panel.status.cget("text")


def test_the_history_page_renders_a_run(panel, write_profile):
    from launcher.cli import main

    write_profile("dev", "name: Dev\nsteps:\n  - {type: wait, name: pause, seconds: 0}\n")
    main(["run", "Dev"])
    panel._show("History")
    assert "Dev" in panel.history_output.get("1.0", "end")

    panel.history_view.set("stats")
    panel._refresh_history()
    assert "pause" in panel.history_output.get("1.0", "end")


def test_the_history_page_survives_a_nonsense_limit(panel):
    panel._show("History")
    panel.history_limit.var.set("many")
    panel._refresh_history()
    assert "No history yet" in panel.history_output.get("1.0", "end")


def test_the_logs_page_shows_what_was_logged(panel):
    from launcher.logging_setup import get_logger, setup

    setup()
    get_logger("test").warning("a line worth finding")
    panel._show("Logs")
    panel._refresh_logs()
    assert "a line worth finding" in panel.log_output.get("1.0", "end")


def test_the_paths_page_writes_the_schema(panel):
    from launcher import schema

    panel._show("Paths")
    panel._write_schema()
    _settle(panel)
    assert schema.schema_path().is_file()


def test_the_console_only_ever_draws_from_the_ui_thread(panel):
    panel.log("hello from a worker")
    assert "hello" not in _console(panel)  # queued, not yet drawn
    panel._drain()
    assert "hello from a worker" in _console(panel)


def test_a_second_action_is_refused_while_one_is_running(panel):
    panel.busy = True
    panel._work("Second", lambda: "ran")
    assert "still running" in _console(panel) or "still running" in "".join(_drain_queue(panel))


# ── the HUD's Settings row ───────────────────────────────────────────────


def test_the_hud_offers_settings_below_the_profiles(gui, write_profile):
    from launcher.config import discover_profiles
    from launcher.hud import _Hud

    write_profile("dev", "name: Dev\nsteps: []\n")
    hud = gui.build(_Hud, discover_profiles(), execute=False)
    try:
        hud.root.update()
        assert [entry.name for entry in hud.filtered] == ["Dev", "Settings"]
    finally:
        hud.root.destroy()


def test_searching_for_settings_puts_it_first(gui, write_profile):
    from launcher.config import discover_profiles
    from launcher.hud import _Hud

    write_profile("dev", "name: Dev\nsteps: []\n")
    hud = gui.build(_Hud, discover_profiles(), execute=False)
    try:
        hud.root.update()
        hud.query.set("sett")
        hud.root.update()
        assert [entry.name for entry in hud.filtered] == ["Settings"]
    finally:
        hud.root.destroy()


def test_picking_the_settings_row_opens_the_panel(gui):
    from launcher.hud import Action, _Hud

    opened = []
    actions = [Action("Settings", "Configure", lambda: opened.append(True))]
    hud = gui.build(_Hud, [], execute=True, actions=actions)
    hud.root.update()
    hud.index = 0
    hud._commit()
    assert opened == [True]
    assert hud.chosen is None  # an action is not a profile to run


def test_control_comma_opens_the_panel_from_anywhere(gui, write_profile):
    from launcher.config import discover_profiles
    from launcher.hud import Action, _Hud

    write_profile("dev", "name: Dev\nsteps: []\n")
    opened = []
    actions = [Action("Settings", "Configure", lambda: opened.append(True))]
    hud = gui.build(_Hud, discover_profiles(), execute=True, actions=actions)
    hud.root.update()
    hud.index = 0  # sitting on Dev, not on the Settings row
    hud._open_settings()
    assert opened == [True]


def test_edit_and_stop_ignore_an_action(gui, monkeypatch):
    from launcher.hud import Action, _Hud

    monkeypatch.setattr("launcher.editor.edit_profile", lambda _p: pytest.fail("edited an action"))
    hud = gui.build(_Hud, [], execute=True, actions=[Action("Settings", "Configure", lambda: None)])
    try:
        hud.root.update()
        assert hud._edit_selected() == "break"
        assert hud._stop_selected() == "break"
        assert hud.running is False
    finally:
        hud.root.destroy()


def test_the_hud_opens_with_no_profiles_at_all(gui):
    from launcher.hud import _Hud

    hud = gui.build(_Hud, [], execute=True)
    try:
        hud.root.update()
        assert [entry.name for entry in hud.filtered] == ["Settings"]
    finally:
        hud.root.destroy()


def test_the_default_settings_action_reaches_open_panel():
    pytest.importorskip("tkinter")
    from launcher.hud import default_actions

    (action,) = default_actions()
    assert action.name == "Settings"
    assert "config" in action.tags


def test_a_broken_tk_skips_instead_of_erroring(gui):
    """Tk that imports but cannot open a window must not fail the suite.

    The Windows CI runners ship a Python whose Tcl/Tk data files are missing:
    `import tkinter` works and every `Tk()` raises. That is the environment's
    problem, not this project's, so it skips.
    """

    def explode():
        raise gui.TclError("Can't find a usable init.tcl")

    with pytest.raises(pytest.skip.Exception) as caught:
        gui.build(explode)
    assert "Tk cannot open a window here" in str(caught.value)
    assert "init.tcl" in str(caught.value)


# ── helpers ──────────────────────────────────────────────────────────────


def _console(panel) -> str:
    return panel.console.get("1.0", "end")


def _drain_queue(panel) -> list[str]:
    import queue

    out = []
    while True:
        try:
            out.append(panel.messages.get_nowait())
        except queue.Empty:
            return out


def _settle(panel, timeout: float = 10.0) -> None:
    """Pump the Tk loop until the panel's worker thread has reported back."""
    import time

    deadline = time.monotonic() + timeout
    while panel.busy and time.monotonic() < deadline:
        panel.root.update()
        time.sleep(0.02)
    panel.root.update()
    panel._drain()
