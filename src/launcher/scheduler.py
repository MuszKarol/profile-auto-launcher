"""Time-based profile triggers, evaluated while the tray runs.

A profile declares when it should fire itself::

    triggers:
      - at: "09:00"
        weekday: [mon, tue, wed, thu, fri]
      - every: 2h
        when: {on_battery: false}

`at:` fires once per day, but only inside a catch-up window after the
scheduled minute — starting the tray at 18:00 must not replay the 09:00
trigger. `every:` measures from the previous fire, and the first interval is
counted from when the scheduler started rather than firing immediately.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from launcher import conditions
from launcher.config import Profile, Trigger, parse_duration
from launcher.logging_setup import get_logger
from launcher.state import load_state, save_state

log = get_logger("scheduler")

TICK_SECONDS = 20.0
CATCH_UP = timedelta(minutes=30)
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _key(profile: Profile, index: int, trigger: Trigger) -> str:
    return f"{profile.name}#{trigger.name or index}"


def _fires() -> dict[str, str]:
    return load_state().get("trigger_fires", {})


def _remember_fire(key: str, when: datetime) -> None:
    state = load_state()
    state.setdefault("trigger_fires", {})[key] = when.isoformat(timespec="seconds")
    save_state(state)


def _last_fire(key: str) -> datetime | None:
    raw = _fires().get(key)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _weekday_allows(trigger: Trigger, now: datetime) -> bool:
    if not trigger.weekday:
        return True
    return _WEEKDAYS[now.weekday()] in trigger.weekday


def is_due(
    trigger: Trigger,
    now: datetime,
    last: datetime | None,
    started: datetime,
    env: dict[str, str] | None = None,
) -> bool:
    """Pure decision function — unit-testable without a running scheduler."""
    if not _weekday_allows(trigger, now):
        return False

    if trigger.at:
        hour, _, minute = trigger.at.partition(":")
        scheduled = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if now < scheduled or now - scheduled > CATCH_UP:
            return False
        if last is not None and last >= scheduled:
            return False
    elif trigger.every:
        interval = parse_duration(trigger.every)
        if interval is None:
            return False
        reference = last or started
        if (now - reference).total_seconds() < interval:
            return False
    else:
        return False

    if trigger.when:
        return conditions.matches(trigger.when, env if env is not None else {})
    return True


class Scheduler:
    """Polls profile triggers on a background thread."""

    def __init__(
        self,
        get_profiles: Callable[[], list[Profile]],
        execute: Callable[[Profile], None],
        tick: float = TICK_SECONDS,
    ) -> None:
        self._get_profiles = get_profiles
        self._execute = execute
        self._tick = tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = datetime.now()

    def start(self) -> threading.Thread:
        self._started = datetime.now()
        thread = threading.Thread(target=self._loop, daemon=True, name="pal-scheduler")
        thread.start()
        self._thread = thread
        log.info("scheduler started")
        return thread

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            try:
                self.check_once()
            except Exception:  # a bad profile must not kill the scheduler
                log.exception("scheduler tick failed")

    def check_once(self, now: datetime | None = None) -> list[Profile]:
        """Fire every due trigger. Returns the profiles that were started."""
        moment = now or datetime.now()
        fired: list[Profile] = []
        import os

        env = os.environ.copy()
        for profile in self._get_profiles():
            for index, trigger in enumerate(profile.triggers):
                key = _key(profile, index, trigger)
                if not is_due(trigger, moment, _last_fire(key), self._started, env):
                    continue
                _remember_fire(key, moment)
                label = trigger.name or trigger.at or trigger.every
                log.info("trigger '%s' fired profile '%s'", label, profile.name)
                fired.append(profile)
                threading.Thread(
                    target=self._execute,
                    args=(profile,),
                    daemon=True,
                ).start()
                break  # one firing per profile per tick
        return fired


def describe(profile: Profile) -> list[str]:
    """Human-readable trigger lines for `palaunch list --triggers`."""
    lines = []
    for index, trigger in enumerate(profile.triggers):
        parts = []
        if trigger.at:
            parts.append(f"daily at {trigger.at}")
        if trigger.every:
            parts.append(f"every {trigger.every}")
        if trigger.weekday:
            parts.append("on " + ",".join(trigger.weekday))
        if trigger.when:
            parts.append(f"when {trigger.when}")
        last = _last_fire(_key(profile, index, trigger))
        if last:
            parts.append(f"(last: {last.isoformat(sep=' ', timespec='minutes')})")
        lines.append(" ".join(parts))
    return lines
