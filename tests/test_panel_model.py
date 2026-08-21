"""The headless half of the configuration panel.

These run everywhere, display or not — they are what keeps the panel honest
about which settings exist and what it accepts for each of them.
"""

from __future__ import annotations

import pytest

from launcher import panel_model as model
from launcher import settings
from launcher.cli import PANEL_SECTIONS

# ── field coverage ───────────────────────────────────────────────────────


def test_the_panel_offers_every_setting():
    """A new key in `Settings` must show up in the panel, or this fails."""
    assert {f.key for f in model.all_fields()} == set(settings.known_keys())


def test_no_setting_is_offered_twice():
    keys = [f.key for f in model.all_fields()]
    assert len(keys) == len(set(keys))


def test_every_field_matches_the_type_of_its_default():
    blank = settings.Settings()
    for field in model.all_fields():
        default = getattr(blank, field.key)
        expected = {bool: "bool", int: "int"}.get(type(default), ("str", "choice"))
        assert field.kind in expected, f"{field.key} is a {type(default).__name__}"


def test_choice_fields_offer_their_current_default():
    blank = settings.Settings()
    for field in model.all_fields():
        if field.kind == "choice":
            assert getattr(blank, field.key) in field.choices


def test_panel_sections_match_the_cli_choices():
    pytest.importorskip("tkinter")
    from launcher.panel import SECTIONS

    assert tuple(PANEL_SECTIONS) == tuple(SECTIONS)


# ── coercion ─────────────────────────────────────────────────────────────


def test_int_fields_reject_words():
    field = model.field_for("max_parallel")
    with pytest.raises(model.ValidationError, match="whole number"):
        model.coerce(field, "eight")


def test_int_fields_enforce_their_bounds():
    field = model.field_for("hud_width")
    with pytest.raises(model.ValidationError, match="at least"):
        model.coerce(field, "10")
    with pytest.raises(model.ValidationError, match="at most"):
        model.coerce(field, "999999")
    assert model.coerce(field, " 900 ") == 900


def test_choice_fields_reject_anything_else():
    field = model.field_for("theme")
    with pytest.raises(model.ValidationError, match="choose one of"):
        model.coerce(field, "neon")
    assert model.coerce(field, "dark") == "dark"


def test_string_fields_are_stripped_and_bools_normalised():
    assert model.coerce(model.field_for("editor"), "  vim  ") == "vim"
    assert model.coerce(model.field_for("notifications"), 0) is False


# ── saving ───────────────────────────────────────────────────────────────


def test_changes_only_reports_what_differs():
    submitted = dict(model.current_values())
    assert model.changes(submitted) == {}
    submitted["theme"] = "light"
    assert model.changes(submitted) == {"theme": "light"}


def test_apply_writes_the_file_and_takes_effect():
    submitted = dict(model.current_values())
    submitted["theme"] = "light"
    submitted["max_parallel"] = "3"
    assert model.apply(submitted) == {"theme": "light", "max_parallel": 3}

    from launcher.config import settings_path

    assert "theme: light" in settings_path().read_text(encoding="utf-8")
    assert settings.load().max_parallel == 3


def test_apply_writes_nothing_when_a_value_is_invalid():
    from launcher.config import settings_path

    submitted = dict(model.current_values())
    submitted["theme"] = "light"
    submitted["log_backups"] = "-1"
    with pytest.raises(model.ValidationError):
        model.apply(submitted)
    assert not settings_path().exists()


def test_apply_leaves_unrelated_keys_in_the_file_alone():
    settings.save({"a_key_we_do_not_know": "keep me"})
    model.apply({**model.current_values(), "theme": "dark"})

    from launcher.config import settings_path

    text = settings_path().read_text(encoding="utf-8")
    assert "a_key_we_do_not_know: keep me" in text
    assert "theme: dark" in text


def test_env_overrides_are_reported(monkeypatch):
    assert model.env_overrides() == {}
    monkeypatch.setenv("PAL_THEME", "dark")
    monkeypatch.setenv("PAL_NOTIFY", "0")
    settings.reload()
    assert model.env_overrides() == {"theme": "PAL_THEME", "notifications": "PAL_NOTIFY"}


def test_defaults_are_the_dataclass_defaults():
    blank = settings.Settings()
    assert model.defaults()["hotkey"] == blank.hotkey
    assert model.defaults()["max_parallel"] == blank.max_parallel


# ── reports ──────────────────────────────────────────────────────────────


def test_paths_report_points_at_the_isolated_config(isolated_config):
    report = dict(model.paths_report())
    assert report["Config directory"] == str(isolated_config)
    assert report["Profiles"] == str(isolated_config / "profiles")
    assert report["Window backend"]


def test_profile_rows_describe_what_is_on_disk(write_profile):
    write_profile(
        "dev",
        """
name: Dev
description: dev things
default: true
icon: "D"
hotkey: <ctrl>+<alt>+d
steps:
  - {type: wait, seconds: 0}
  - {type: wait, seconds: 0}
""",
    )
    (row,) = model.profile_rows()
    assert row["name"] == "Dev"
    assert row["steps"] == 2
    assert row["default"] is True
    assert row["hotkey"] == "<ctrl>+<alt>+d"
    assert row["running"] == 0
    assert row["path"].endswith("dev.yaml")


def test_profile_rows_count_running_processes(write_profile, monkeypatch):
    write_profile("dev", "name: Dev\nsteps: []\n")
    monkeypatch.setattr("launcher.procs.active_profiles", lambda: {"Dev": [object(), object()]})
    assert model.profile_rows()[0]["running"] == 2


def test_profile_rows_survive_an_unreadable_process_registry(write_profile, monkeypatch):
    write_profile("dev", "name: Dev\nsteps: []\n")

    def boom():
        raise RuntimeError("registry is a directory")

    monkeypatch.setattr("launcher.procs.active_profiles", boom)
    assert [row["name"] for row in model.profile_rows()] == ["Dev"]


def test_history_and_stats_rows_read_the_history_file(write_profile):
    from launcher.cli import main

    write_profile("dev", "name: Dev\nsteps:\n  - {type: wait, name: pause, seconds: 0}\n")
    assert main(["run", "Dev"]) == 0

    (line,) = model.history_rows()
    assert line.startswith("✓") and "Dev" in line and "1 ok" in line

    header, *rows = model.stats_rows()
    assert "profile" in header and "fail%" in header
    assert any("pause" in row for row in rows)


def test_history_rows_are_empty_before_any_run():
    assert model.history_rows() == []
    assert model.stats_rows() == []


def test_hotkey_rows_always_mention_the_hud():
    assert any("HUD" in line for line in model.hotkey_rows())


# ── open_path ────────────────────────────────────────────────────────────


def test_open_path_prefers_the_configured_editor(monkeypatch, tmp_path):
    settings.save({"editor": "my-editor"})
    calls = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: calls.append(argv))
    assert "my-editor" in model.open_path(tmp_path)
    assert calls == [["my-editor", str(tmp_path)]]


def test_open_path_falls_back_to_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR", "env-editor")
    calls = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: calls.append(argv))
    model.open_path(tmp_path)
    assert calls == [["env-editor", str(tmp_path)]]
