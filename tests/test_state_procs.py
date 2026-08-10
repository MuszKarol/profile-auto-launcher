from __future__ import annotations

import os
import subprocess
import sys

import pytest

from launcher import procs, settings
from launcher.config import Step
from launcher.executor import StepResult
from launcher.state import (
    clear_history,
    load_history,
    load_state,
    record_run,
    step_stats,
)


def _result(name: str, ok: bool = True, duration: float = 0.1, **kwargs) -> StepResult:
    return StepResult(Step(type="wait", name=name, **kwargs), ok, "detail", duration=duration)


# ── state / history ──────────────────────────────────────────────────────


def test_record_run_updates_hot_state():
    record_run("Dev", [_result("a")], 1.0)
    record_run("Dev", [_result("a")], 1.0)
    state = load_state()
    assert state["last_profile"] == "Dev"
    assert state["run_counts"]["Dev"] == 2


def test_teardown_runs_do_not_become_the_last_profile():
    record_run("Dev", [_result("a")], 1.0)
    record_run("Other", [_result("b")], 1.0, kind="teardown")
    assert load_state()["last_profile"] == "Dev"


def test_history_is_newest_first_and_filterable():
    record_run("A", [_result("x")], 1.0)
    record_run("B", [_result("y")], 2.0)
    assert [r["profile"] for r in load_history()] == ["B", "A"]
    assert [r["profile"] for r in load_history(profile="a")] == ["A"]


def test_history_counts_optional_failures_separately():
    results = [
        _result("hard", ok=False),
        _result("soft", ok=False, optional=True),
        _result("skipped"),
    ]
    results[2].skipped = True
    record_run("P", results, 1.0)
    record = load_history()[0]
    assert (record["ok"], record["failed"], record["skipped"]) == (0, 1, 1)


def test_history_is_trimmed_to_the_configured_limit(monkeypatch):
    settings._cache = settings.Settings(history_limit=10)
    for index in range(25):
        record_run(f"P{index}", [_result("x")], 0.1)
    assert len(load_history(limit=0)) == 10


def test_step_stats_rank_flaky_steps_first():
    record_run("P", [_result("flaky", ok=False), _result("solid")], 1.0)
    record_run("P", [_result("flaky", ok=True), _result("solid")], 1.0)
    stats = step_stats()
    assert stats[0]["step"] == "flaky"
    assert stats[0]["runs"] == 2
    assert stats[0]["failure_rate"] == 0.5


def test_step_stats_ignore_skipped_steps():
    skipped = _result("never")
    skipped.skipped = True
    record_run("P", [skipped], 1.0)
    assert step_stats() == [] or step_stats()[0]["runs"] == 0


def test_clear_history_is_idempotent():
    record_run("P", [_result("x")], 1.0)
    clear_history()
    clear_history()
    assert load_history() == []


def test_corrupt_history_lines_are_skipped():
    from launcher.state import history_path

    record_run("Good", [_result("x")], 1.0)
    with history_path().open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
    assert [r["profile"] for r in load_history()] == ["Good"]


def test_unreadable_state_file_falls_back_to_empty():
    from launcher.state import state_path

    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text("{not json", encoding="utf-8")
    assert load_state() == {}


# ── process registry ─────────────────────────────────────────────────────


def test_is_alive_for_the_current_process():
    assert procs.is_alive(os.getpid())


def test_is_alive_rejects_bogus_pids():
    assert not procs.is_alive(0)
    assert not procs.is_alive(-1)


def test_registry_roundtrip():
    procs.record("Dev", os.getpid(), "self", "python")
    tracked = procs.tracked("Dev")
    assert [p.pid for p in tracked] == [os.getpid()]
    assert tracked[0].label == "self"


def test_active_profiles_only_lists_living_processes():
    procs.record("Dev", os.getpid(), "self", "python")
    procs.record("Ghost", 999_999, "dead", "nothing")
    active = procs.active_profiles()
    assert "Dev" in active and "Ghost" not in active


def test_prune_drops_dead_entries():
    procs.record("Ghost", 999_999, "dead", "nothing")
    procs.prune()
    assert procs.tracked("Ghost") == []


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX sleep binary")
def test_stop_profile_terminates_tracked_processes():
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        procs.record("Temp", child.pid, "sleeper", "python")
        stopped, stubborn = procs.stop_profile("Temp", grace=3.0)
        assert (stopped, stubborn) == (1, 0)
        assert not procs.is_alive(child.pid)
        assert procs.tracked("Temp") == []
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_stop_profile_with_nothing_tracked_is_a_noop():
    assert procs.stop_profile("Unknown") == (0, 0)


@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="zombies are a POSIX concept")
def test_a_zombie_child_does_not_count_as_alive():
    # The tray spawns children and never waits on them, so a terminated child
    # lingers unreaped and signal-0 keeps reporting it as alive.
    import time

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        # No poll()/wait() here: those reap the child. Left alone it stays a
        # zombie, which is exactly the state signal-0 misreports as running.
        time.sleep(0.6)
        assert not procs.is_alive(child.pid)
    finally:
        child.wait()


# ── settings ─────────────────────────────────────────────────────────────


def test_settings_defaults_apply_without_a_file():
    conf = settings.load()
    assert conf.hotkey == "<alt>+<space>"
    assert conf.theme == "auto"


def test_settings_are_read_from_the_file():
    settings.save({"theme": "light", "max_parallel": 2})
    assert settings.load().theme == "light"
    assert settings.load().max_parallel == 2


def test_env_overrides_the_file(monkeypatch):
    settings.save({"theme": "light"})
    monkeypatch.setenv("PAL_THEME", "dark")
    assert settings.reload().theme == "dark"


def test_pal_notify_env_still_disables_notifications(monkeypatch):
    monkeypatch.setenv("PAL_NOTIFY", "0")
    assert settings.reload().notifications is False


def test_unknown_keys_survive_a_save():
    settings.save({"experimental_thing": "yes"})
    settings.save({"theme": "dark"})
    assert settings.load().extra["experimental_thing"] == "yes"
    assert settings.load().theme == "dark"


def test_malformed_settings_file_falls_back_to_defaults():
    from launcher.config import settings_path

    settings_path().write_text("this: [is not: valid", encoding="utf-8")
    assert settings.reload().theme == "auto"
