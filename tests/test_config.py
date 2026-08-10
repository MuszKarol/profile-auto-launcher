from __future__ import annotations

import pytest

from launcher import config
from launcher.config import (
    Step,
    _coerce_step,
    discover_profiles,
    load_profile,
    parse_duration,
)


def test_step_requires_type():
    with pytest.raises(ValueError, match="unknown step type"):
        _coerce_step({"type": "teleport"})


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"type": "app"}, "requires 'path'"),
        ({"type": "command"}, "requires 'run'"),
        ({"type": "url"}, "requires 'url'"),
        ({"type": "kill"}, "requires 'process'"),
        ({"type": "wait_for"}, "requires 'url' or 'run'"),
        ({"type": "file"}, "requires 'action'"),
    ],
)
def test_required_fields_are_checked_at_load_time(raw, message):
    with pytest.raises(ValueError, match=message):
        _coerce_step(raw)


def test_unknown_when_key_is_rejected():
    with pytest.raises(ValueError, match="unknown 'when' keys"):
        _coerce_step({"type": "wait", "when": {"phase_of_moon": "full"}})


def test_unknown_window_key_is_rejected():
    with pytest.raises(ValueError, match="unknown 'window' keys"):
        _coerce_step({"type": "app", "path": "x", "window": {"positon": "left-half"}})


def test_platform_keyed_values_resolve(monkeypatch):
    monkeypatch.setattr(config, "PLATFORM", "linux")
    step = _coerce_step({"type": "app", "path": {"windows": "w.exe", "linux": "/usr/bin/l"}})
    assert step.path == "/usr/bin/l"


def test_platform_keyed_values_fall_back_to_default(monkeypatch):
    monkeypatch.setattr(config, "PLATFORM", "linux")
    step = _coerce_step({"type": "app", "path": {"windows": "w.exe", "default": "/fallback"}})
    assert step.path == "/fallback"


def test_file_action_is_validated():
    with pytest.raises(ValueError, match="unknown file action"):
        _coerce_step({"type": "file", "action": "shred", "dest": "/tmp/x"})


def test_unknown_profile_key_is_rejected(write_profile):
    path = write_profile("bad", "name: Bad\nstpes: []\n")
    with pytest.raises(ValueError, match="unknown profile keys"):
        load_profile(path)


def test_depends_on_must_reference_an_existing_id(write_profile):
    path = write_profile(
        "dep",
        """
name: Dep
steps:
  - {type: wait, seconds: 0, depends_on: [ghost]}
""",
    )
    with pytest.raises(ValueError, match="unknown id 'ghost'"):
        load_profile(path)


def test_duplicate_step_ids_are_rejected(write_profile):
    path = write_profile(
        "dup",
        """
name: Dup
steps:
  - {type: wait, seconds: 0, id: a}
  - {type: wait, seconds: 0, id: a}
""",
    )
    with pytest.raises(ValueError, match="duplicate step id"):
        load_profile(path)


def test_extends_merges_steps_parent_first(write_profile):
    write_profile("base", "name: Base\nsteps:\n  - {type: wait, seconds: 1}\n")
    child = write_profile(
        "child", "name: Child\nextends: base\nsteps:\n  - {type: wait, seconds: 2}\n"
    )
    profile = load_profile(child)
    assert [s.seconds for s in profile.steps] == [1.0, 2.0]


def test_extends_does_not_inherit_default_autostart_or_hotkey(write_profile):
    write_profile(
        "base",
        "name: Base\ndefault: true\nautostart: true\nhotkey: <ctrl>+<alt>+b\nsteps: []\n",
    )
    child = write_profile("child", "name: Child\nextends: base\nsteps: []\n")
    profile = load_profile(child)
    assert (profile.default, profile.autostart, profile.hotkey) == (False, False, "")
    assert profile.name == "Child"


def test_extends_merges_vars_with_child_winning(write_profile):
    write_profile("base", "name: Base\nvars: {a: '1', b: '2'}\nsteps: []\n")
    child = write_profile("child", "name: Child\nextends: base\nvars: {b: '9'}\nsteps: []\n")
    assert load_profile(child).vars == {"a": "1", "b": "9"}


def test_extends_cycle_is_detected(write_profile):
    write_profile("one", "name: One\nextends: two\nsteps: []\n")
    two = write_profile("two", "name: Two\nextends: one\nsteps: []\n")
    with pytest.raises(ValueError, match="cycle detected"):
        load_profile(two)


def test_teardown_steps_are_parsed(write_profile):
    path = write_profile(
        "svc",
        """
name: Svc
steps: []
teardown:
  - {type: notify, message: bye}
""",
    )
    profile = load_profile(path)
    assert len(profile.teardown) == 1 and profile.teardown[0].type == "notify"


def test_triggers_are_parsed_and_validated(write_profile):
    path = write_profile(
        "t",
        """
name: T
triggers:
  - {at: "09:30", weekday: [monday, tue]}
  - {every: 2h}
steps: []
""",
    )
    profile = load_profile(path)
    assert profile.triggers[0].at == "09:30"
    assert profile.triggers[0].weekday == ["mon", "tue"]
    assert profile.triggers[1].every == "2h"


def test_trigger_needs_at_or_every(write_profile):
    path = write_profile("t", "name: T\ntriggers:\n  - {weekday: [mon]}\nsteps: []\n")
    with pytest.raises(ValueError, match="needs 'at:' or 'every:'"):
        load_profile(path)


def test_trigger_rejects_malformed_clock(write_profile):
    path = write_profile("t", 'name: T\ntriggers:\n  - {at: "25:99"}\nsteps: []\n')
    with pytest.raises(ValueError, match="must be HH:MM"):
        load_profile(path)


@pytest.mark.parametrize(
    "text, expected",
    [("45s", 45), ("30m", 1800), ("2h", 7200), ("1d", 86400), ("90", 90)],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "soon", "-5m", "0s"])
def test_parse_duration_rejects_nonsense(text):
    assert parse_duration(text) is None


def test_discover_skips_broken_files_without_dying(write_profile, capsys):
    write_profile("good", "name: Good\nsteps: []\n")
    write_profile("broken", "name: Broken\nsteps:\n  - {type: nope}\n")
    names = [p.name for p in discover_profiles()]
    assert names == ["Good"]
    assert "failed to load" in capsys.readouterr().err


def test_cwd_profiles_are_ignored_unless_opted_in(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "evil.yaml").write_text("name: Evil\nsteps: []\n")
    assert discover_profiles() == []
    monkeypatch.setenv("PAL_INCLUDE_CWD", "1")
    assert [p.name for p in discover_profiles()] == ["Evil"]


def test_step_label_prefers_name_then_id_then_type():
    assert Step(type="wait", name="nap", id="n").label == "nap"
    assert Step(type="wait", id="n").label == "n"
    assert Step(type="wait").label == "wait"
