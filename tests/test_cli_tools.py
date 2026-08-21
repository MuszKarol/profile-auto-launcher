from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from launcher import record, schema, secrets, sync
from launcher.cli import main

# ── CLI wiring ───────────────────────────────────────────────────────────


def test_list_reports_when_there_are_no_profiles(capsys):
    assert main(["list"]) == 1
    assert "No profiles found" in capsys.readouterr().out


def test_list_shows_profiles_and_triggers(write_profile, capsys):
    write_profile(
        "dev",
        """
name: Dev
description: dev things
default: true
hotkey: <ctrl>+<alt>+d
triggers:
  - {at: "09:00"}
steps:
  - {type: wait, seconds: 0}
""",
    )
    assert main(["list", "--triggers"]) == 0
    out = capsys.readouterr().out
    assert "★" in out and "Dev" in out
    assert "hotkey: <ctrl>+<alt>+d" in out
    assert "daily at 09:00" in out


def test_validate_flags_a_broken_profile(write_profile, capsys):
    write_profile("ok", "name: Ok\nsteps: []\n")
    write_profile("bad", "name: Bad\nsteps:\n  - {type: app}\n")
    assert main(["validate"]) == 1
    out = capsys.readouterr().out
    assert "✓ ok.yaml" in out and "✗ bad.yaml" in out
    assert "1/2 profiles valid" in out


def test_run_reports_a_missing_profile(capsys):
    assert main(["run", "Ghost"]) == 2
    assert "not found" in capsys.readouterr().err


def test_dry_run_does_not_execute(write_profile, tmp_path, capsys):
    target = tmp_path / "created.txt"
    write_profile(
        "f",
        f"name: F\nsteps:\n  - {{type: file, action: write, dest: {str(target)!r}, content: x}}\n",
    )
    assert main(["run", "F", "--dry-run"]) == 0
    assert not target.exists()
    assert "dry run" in capsys.readouterr().out


def test_run_returns_nonzero_when_a_step_fails(write_profile, no_notifications):
    write_profile(
        "f",
        f"name: F\nsteps:\n  - {{type: command, detach: false, run: [{sys.executable!r}, '-c', 'raise SystemExit(2)']}}\n",
    )
    assert main(["run", "F"]) == 1


def test_new_scaffolds_a_valid_profile(profiles_dir, capsys):
    assert main(["new", "My Setup"]) == 0
    path = profiles_dir / "my-setup.yaml"
    assert path.is_file()
    assert "yaml-language-server" in path.read_text()
    assert main(["validate"]) == 0


def test_new_refuses_to_overwrite(profiles_dir, capsys):
    main(["new", "Dup"])
    assert main(["new", "Dup"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_a_closed_pipe_does_not_traceback(write_profile, monkeypatch, capsys):
    # `palaunch list | head -2` closes stdout early; a CLI must exit quietly.
    write_profile("p", "name: P\nsteps: []\n")

    def explode(*_args, **_kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr("builtins.print", explode)
    assert main(["list"]) == 0


def test_interrupt_reports_the_conventional_exit_code(monkeypatch):
    monkeypatch.setattr(
        "launcher.cli._cmd_status",
        lambda _args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert main(["status"]) == 130


def test_status_with_nothing_running(capsys):
    assert main(["status"]) == 0
    assert "No profile is currently running" in capsys.readouterr().out


def test_where_lists_the_paths_and_hotkeys(capsys):
    assert main(["where"]) == 0
    out = capsys.readouterr().out
    for label in ("config dir", "profiles dir", "settings", "log file", "history", "schema"):
        assert label in out
    assert "<alt>+<space>" in out


def test_history_is_empty_at_first(capsys):
    assert main(["history"]) == 1
    assert "No history yet" in capsys.readouterr().out


def test_history_shows_a_run(write_profile, no_notifications, capsys):
    write_profile("p", "name: P\nsteps:\n  - {type: wait, seconds: 0}\n")
    main(["run", "P"])
    capsys.readouterr()
    assert main(["history"]) == 0
    assert "P" in capsys.readouterr().out


def test_logs_before_anything_ran(capsys):
    assert main(["logs"]) == 1
    assert "No log file yet" in capsys.readouterr().err


def test_config_get_set_and_list(capsys):
    assert main(["config", "set", "theme", "light"]) == 0
    capsys.readouterr()
    assert main(["config", "get", "theme"]) == 0
    assert capsys.readouterr().out.strip() == "light"
    assert main(["config", "list"]) == 0
    assert "max_parallel" in capsys.readouterr().out


def test_config_rejects_unknown_keys(capsys):
    assert main(["config", "set", "nonsense", "1"]) == 2
    assert "Unknown setting" in capsys.readouterr().err


def test_config_coerces_booleans_and_integers(capsys):
    from launcher import settings

    main(["config", "set", "notifications", "false"])
    main(["config", "set", "max_parallel", "3"])
    conf = settings.reload()
    assert conf.notifications is False and conf.max_parallel == 3


def test_config_rejects_a_non_integer(capsys):
    assert main(["config", "set", "max_parallel", "loads"]) == 2
    assert "expects an integer" in capsys.readouterr().err


def test_stop_without_anything_running(write_profile, capsys):
    write_profile("p", "name: P\nsteps: []\n")
    assert main(["stop", "--all"]) == 0
    assert "Nothing is running" in capsys.readouterr().out


def test_schema_command_writes_a_file(capsys):
    assert main(["schema"]) == 0
    assert schema.schema_path().is_file()
    assert "yaml-language-server" in capsys.readouterr().out


def test_schema_stdout_is_valid_json(capsys):
    assert main(["schema", "--stdout"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["title"].startswith("Profile Auto Launcher")


# ── schema ───────────────────────────────────────────────────────────────


def test_schema_lists_every_step_type():
    from launcher.config import KNOWN_STEP_TYPES

    built = schema.build()
    assert set(built["$defs"]["step"]["properties"]["type"]["enum"]) == set(KNOWN_STEP_TYPES)


def test_schema_covers_every_profile_key():
    from launcher.config import KNOWN_PROFILE_KEYS

    built = schema.build()
    assert set(built["properties"]) == set(KNOWN_PROFILE_KEYS)


def test_schema_modeline_points_at_the_file(tmp_path):
    written = schema.write(tmp_path / "s.json")
    assert schema.modeline(written).startswith("# yaml-language-server: $schema=file://")


# ── recorder ─────────────────────────────────────────────────────────────


def test_recorder_filters_out_helper_processes():
    assert record._is_noise("chrome", "/opt/chrome --type=renderer")
    assert record._is_noise("Code Helper", "/Applications/Code Helper")
    assert not record._is_noise("code", "/usr/bin/code")


def test_recorder_filters_platform_noise(monkeypatch):
    monkeypatch.setattr(record, "PLATFORM", "linux")
    assert record._is_noise("bash", "/bin/bash")
    assert not record._is_noise("obsidian", "/usr/bin/obsidian")


def test_recorded_yaml_is_a_loadable_profile(tmp_path):
    from launcher.config import load_profile

    candidates = [record.Candidate(name="Code", exe="/usr/bin/code", pid=1, seen_at=0.0)]
    path = tmp_path / "rec.yaml"
    record.write_profile("Rec", candidates, path)
    profile = load_profile(path)
    assert profile.name == "Rec"
    assert profile.steps[0].path == "/usr/bin/code"
    assert profile.steps[0].optional and profile.steps[0].parallel


def test_empty_recording_still_writes_an_explanatory_file(tmp_path):
    path = tmp_path / "empty.yaml"
    record.write_profile("Empty", [], path)
    assert "Nothing was recorded" in path.read_text()


def test_observe_captures_a_process_that_starts_during_the_window(monkeypatch):
    import threading
    import time

    # The test spawns a Python child, which the real noise list filters out on
    # purpose; empty it so this test covers the observation loop itself.
    monkeypatch.setattr(record, "_NOISE", {})
    child: list[subprocess.Popen] = []

    def spawn() -> None:
        time.sleep(0.8)
        child.append(subprocess.Popen([sys.executable, "-c", "import time;time.sleep(20)"]))

    threading.Thread(target=spawn).start()
    try:
        candidates = record.observe(duration=3.0, poll=0.5)
        assert any(str(child[0].pid) == str(c.pid) for c in candidates)
    finally:
        if child:
            child[0].kill()
            child[0].wait()


# ── secrets ──────────────────────────────────────────────────────────────


def test_secret_index_roundtrip(monkeypatch):
    stored: dict[str, str] = {}

    class FakeKeyring:
        @staticmethod
        def set_password(_service, name, value):
            stored[name] = value

        @staticmethod
        def get_password(_service, name):
            return stored.get(name)

        @staticmethod
        def delete_password(_service, name):
            stored.pop(name, None)

    monkeypatch.setattr(secrets, "_backend", lambda: FakeKeyring)
    secrets.set_secret("token", "abc")
    assert secrets.get("token") == "abc"
    assert secrets.list_names() == ["token"]
    secrets.delete("token")
    assert secrets.list_names() == []


def test_missing_secret_raises(monkeypatch):
    class FakeKeyring:
        @staticmethod
        def get_password(_service, _name):
            return None

    monkeypatch.setattr(secrets, "_backend", lambda: FakeKeyring)
    with pytest.raises(secrets.SecretError, match="is not set"):
        secrets.get("ghost")


def test_absent_backend_reports_how_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)
    with pytest.raises(secrets.SecretError, match="pip install"):
        secrets.get("anything")


# ── git sync ─────────────────────────────────────────────────────────────

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@needs_git
def test_sync_reports_a_missing_repo():
    assert "not a git repository" in sync.status()


@needs_git
def test_sync_init_creates_a_repo(profiles_dir):
    sync.init()
    assert sync.is_repo()
    assert (profiles_dir / ".gitignore").is_file()


@needs_git
def test_sync_commits_local_changes(profiles_dir, monkeypatch):
    sync.init()
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=profiles_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=profiles_dir, check=True)
    (profiles_dir / "dev.yaml").write_text("name: Dev\nsteps: []\n")
    summary = sync.sync(message="add dev", push=False)
    assert "committed local changes" in summary
    assert "no remote configured" in summary
    assert sync.sync(push=False).startswith("nothing to commit")


@needs_git
def test_sync_status_shows_the_branch(profiles_dir):
    sync.init("https://example.com/profiles.git")
    assert "https://example.com/profiles.git" in sync.status()


# ── settings panel wiring ────────────────────────────────────────────────


def test_settings_opens_the_panel(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "launcher.panel", _FakePanel(opened))
    assert main(["settings"]) == 0
    assert opened == ["Settings"]


def test_settings_can_open_a_named_section(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "launcher.panel", _FakePanel(opened))
    assert main(["settings", "History"]) == 0
    assert opened == ["History"]


def test_settings_rejects_an_unknown_section():
    with pytest.raises(SystemExit):
        main(["settings", "Nonsense"])


def test_config_gui_opens_the_panel(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "launcher.panel", _FakePanel(opened))
    assert main(["config", "gui"]) == 0
    assert opened == ["Settings"]


def test_settings_explains_a_missing_tkinter(monkeypatch, capsys):
    import builtins

    real_import = builtins.__import__

    def no_tkinter(name, *args, **kwargs):
        if name in ("launcher.panel", "tkinter"):
            raise ImportError("No module named 'tkinter'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "launcher.panel", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_tkinter)
    assert main(["settings"]) == 1
    err = capsys.readouterr().err
    assert "needs tkinter" in err
    assert "python3-tk" in err
    assert "palaunch config set" in err


class _FakePanel:
    """Stands in for `launcher.panel` so the CLI wiring is testable headless."""

    SECTIONS = ("Settings", "Profiles", "Secrets", "Sync", "History", "Logs", "Paths")

    def __init__(self, sink: list) -> None:
        self._sink = sink

    def open_panel(self, section: str = "Settings") -> int:
        self._sink.append(section)
        return 0
