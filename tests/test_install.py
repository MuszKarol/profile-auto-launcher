"""What the setup wizard does to a machine — and undoes again."""

from __future__ import annotations

import sys

import pytest

from launcher import install as ops


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    return home


def test_the_plan_names_what_it_will_do(fake_home):
    lines = " ".join(ops.plan(ops.Options()))
    assert "sample profiles" in lines
    assert "login" in lines


def test_a_source_checkout_ships_the_sample_profiles():
    bundled = ops.bundled("profiles")
    assert bundled is not None and any(bundled.glob("*.yaml"))


def test_installing_copies_profiles_and_writes_the_schema(fake_home, profiles_dir):
    from launcher import schema

    notes = ops.install(
        ops.Options(autostart=False, shortcut=False, sample_profiles=True), log_line=lambda _: None
    )
    assert any(profiles_dir.glob("*.yaml"))
    assert schema.schema_path().is_file()
    assert any("Profiles live in" in note for note in notes)


def test_an_install_does_not_overwrite_an_existing_profile(fake_home, write_profile):
    path = write_profile("work", "name: Mine\nsteps: []\n")
    ops.install(ops.Options(autostart=False, shortcut=False), log_line=lambda _: None)
    assert "name: Mine" in path.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "linux", reason="XDG entries are Linux-shaped")
def test_autostart_and_menu_entries_are_created_and_removed(fake_home):
    ops.install(ops.Options(sample_profiles=False, schema=False), log_line=lambda _: None)
    autostart = ops.autostart_dir() / ops.DESKTOP_FILE
    menu = ops.menu_dir() / ops.DESKTOP_FILE
    assert autostart.is_file() and menu.is_file()
    assert "tray" in autostart.read_text(encoding="utf-8")
    assert ops.is_registered()

    removed = ops.uninstall(log_line=lambda _: None)
    assert not autostart.exists() and not menu.exists()
    assert len(removed) >= 2
    assert not ops.is_registered()


def test_uninstalling_keeps_the_profiles(fake_home, write_profile):
    path = write_profile("work", "name: Mine\nsteps: []\n")
    ops.install(ops.Options(sample_profiles=False, schema=False), log_line=lambda _: None)
    ops.uninstall(log_line=lambda _: None)
    assert path.is_file()


def test_the_icon_is_written_where_shortcuts_point(fake_home):
    ops.install(
        ops.Options(autostart=False, shortcut=False, sample_profiles=False, schema=False),
        log_line=lambda _: None,
    )
    assert ops.icon_path().is_file()
    assert ops.icon_path().stat().st_size > 0


def test_the_tray_command_uses_the_windowless_binary(tmp_path):
    exe = tmp_path / "palaunch"
    exe.write_text("", encoding="utf-8")
    (tmp_path / "palaunchw").write_text("", encoding="utf-8")
    assert ops.tray_command(exe) == [str(tmp_path / "palaunchw"), "tray"]


def test_the_cli_can_install_without_a_window(fake_home, profiles_dir):
    from launcher.cli import main

    assert main(["install", "--cli", "--no-autostart"]) == 0
    assert not ops.is_registered()
    assert main(["install", "--cli", "--uninstall"]) == 0


# ── the setup window ─────────────────────────────────────────────────────


def test_the_setup_window_lists_the_plan_and_installs(gui, monkeypatch, tmp_path):
    from launcher import install as ops
    from launcher.installer import _Installer

    calls = []
    monkeypatch.setattr(ops, "install", lambda options, log_line: calls.append(options) or ["done"])
    wizard = gui.build(_Installer)
    try:
        wizard.root.update()
        wizard._show_options()
        wizard.autostart.set(False)
        wizard._start()
        _pump(wizard.root, lambda: bool(calls))
        assert calls[0].autostart is False
    finally:
        wizard.root.destroy()


def _pump(root, ready, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while not ready() and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    root.update()
