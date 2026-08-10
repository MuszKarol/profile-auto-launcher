from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

from launcher import scheduler, windows
from launcher.config import Profile, Trigger
from launcher.scheduler import Scheduler, is_due
from launcher.windows import Rect, _dimension, _pick_monitor, _target_rect

MONDAY_9 = datetime(2026, 8, 10, 9, 0)
STARTED = datetime(2026, 8, 10, 8, 0)


# ── trigger scheduling ───────────────────────────────────────────────────


def test_daily_trigger_fires_at_its_time():
    assert is_due(Trigger(at="09:00"), MONDAY_9, None, STARTED)


def test_daily_trigger_does_not_fire_before_its_time():
    assert not is_due(Trigger(at="09:00"), MONDAY_9 - timedelta(minutes=1), None, STARTED)


def test_daily_trigger_is_not_replayed_after_the_catch_up_window():
    # Starting the tray in the evening must not replay the morning trigger.
    evening = MONDAY_9.replace(hour=18)
    assert not is_due(Trigger(at="09:00"), evening, None, STARTED)


def test_daily_trigger_still_fires_inside_the_catch_up_window():
    assert is_due(Trigger(at="09:00"), MONDAY_9 + timedelta(minutes=20), None, STARTED)


def test_daily_trigger_fires_only_once_per_day():
    already = MONDAY_9 + timedelta(minutes=1)
    assert not is_due(Trigger(at="09:00"), MONDAY_9 + timedelta(minutes=5), already, STARTED)


def test_daily_trigger_fires_again_the_next_day():
    yesterday = MONDAY_9 - timedelta(days=1)
    assert is_due(Trigger(at="09:00"), MONDAY_9, yesterday, STARTED)


def test_weekday_filter_blocks_other_days():
    saturday = datetime(2026, 8, 15, 9, 0)
    trigger = Trigger(at="09:00", weekday=["mon", "tue"])
    assert not is_due(trigger, saturday, None, STARTED)
    assert is_due(trigger, MONDAY_9, None, STARTED)


def test_interval_trigger_waits_for_the_first_interval():
    trigger = Trigger(every="2h")
    assert not is_due(trigger, STARTED + timedelta(minutes=30), None, STARTED)
    assert is_due(trigger, STARTED + timedelta(hours=2), None, STARTED)


def test_interval_trigger_measures_from_the_previous_fire():
    trigger = Trigger(every="30m")
    last = MONDAY_9
    assert not is_due(trigger, MONDAY_9 + timedelta(minutes=20), last, STARTED)
    assert is_due(trigger, MONDAY_9 + timedelta(minutes=31), last, STARTED)


def test_trigger_conditions_are_honoured(monkeypatch):
    from launcher import sysprobe

    monkeypatch.setattr(sysprobe, "on_battery", lambda: True)
    trigger = Trigger(at="09:00", when={"on_battery": False})
    assert not is_due(trigger, MONDAY_9, None, STARTED, {})


def test_malformed_interval_never_fires():
    assert not is_due(Trigger(every="soon"), MONDAY_9, None, STARTED)


def test_scheduler_fires_a_due_profile_once_and_records_it():
    profile = Profile(name="Timed", triggers=[Trigger(at="09:00")])
    fired: list[str] = []
    engine = Scheduler(lambda: [profile], lambda p: fired.append(p.name))
    engine._started = STARTED

    assert [p.name for p in engine.check_once(MONDAY_9)] == ["Timed"]
    # a second tick in the same window must not fire again
    assert engine.check_once(MONDAY_9 + timedelta(minutes=1)) == []


def test_scheduler_describe_reads_back_the_schedule():
    profile = Profile(
        name="T", triggers=[Trigger(at="09:00", weekday=["mon"]), Trigger(every="2h")]
    )
    lines = scheduler.describe(profile)
    assert "daily at 09:00" in lines[0] and "on mon" in lines[0]
    assert "every 2h" in lines[1]


# ── window geometry ──────────────────────────────────────────────────────

SCREEN = Rect(0, 0, 1920, 1080)


@pytest.mark.parametrize(
    "position, expected",
    [
        ("left-half", Rect(0, 0, 960, 1080)),
        ("right-half", Rect(960, 0, 960, 1080)),
        ("top-half", Rect(0, 0, 1920, 540)),
        ("bottom-half", Rect(0, 540, 1920, 540)),
        ("top-left", Rect(0, 0, 960, 540)),
        ("bottom-right", Rect(960, 540, 960, 540)),
        ("full", Rect(0, 0, 1920, 1080)),
        ("maximized", Rect(0, 0, 1920, 1080)),
    ],
)
def test_named_positions(position, expected):
    assert _target_rect({"position": position}, SCREEN) == expected


def test_unknown_position_yields_no_rect():
    assert _target_rect({"position": "diagonally"}, SCREEN) is None


def test_named_positions_are_offset_by_the_monitor_origin():
    second = Rect(1920, 0, 1280, 720)
    assert _target_rect({"position": "left-half"}, second) == Rect(1920, 0, 640, 720)


def test_explicit_coordinates_win_over_position():
    rect = _target_rect(
        {"position": "left-half", "x": 10, "y": 20, "width": 300, "height": 400}, SCREEN
    )
    assert rect == Rect(10, 20, 300, 400)


@pytest.mark.parametrize(
    "value, expected", [(800, 800), ("50%", 960), (0.25, 480), ("nonsense", 0)]
)
def test_dimension_accepts_pixels_percentages_and_fractions(value, expected):
    assert _dimension(value, 1920) == expected


def test_monitor_selection_is_one_based():
    monitors = [Rect(0, 0, 1920, 1080), Rect(1920, 0, 1280, 720)]
    assert _pick_monitor(monitors, {"monitor": 2}) == monitors[1]
    assert _pick_monitor(monitors, {}) == monitors[0]


def test_out_of_range_monitor_falls_back_to_the_primary():
    monitors = [Rect(0, 0, 1920, 1080)]
    assert _pick_monitor(monitors, {"monitor": 7}) == monitors[0]
    assert _pick_monitor([], {"monitor": 1}).width > 0


def test_window_management_can_be_disabled(monkeypatch):
    from launcher import settings

    settings._cache = settings.Settings(window_management=False)
    assert "disabled" in windows.apply({"position": "left-half"}, pid=1)


@pytest.mark.skipif(sys.platform == "win32", reason="checks the POSIX backends")
def test_missing_backend_raises_window_error(monkeypatch):
    from launcher import settings

    settings._cache = settings.Settings(window_management=True)
    monkeypatch.setattr(windows, "PLATFORM", "linux")
    monkeypatch.setattr(windows.shutil, "which", lambda _name: None)
    with pytest.raises(windows.WindowError, match="wmctrl"):
        windows.apply({"position": "left-half"}, pid=1)
