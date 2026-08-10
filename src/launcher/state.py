"""Persistent run state and history.

`state.json` holds the small hot facts the HUD reads on every open (last
profile, per-profile run counts). `history.jsonl` holds one JSON object per
run — appended, trimmed to a bounded number of lines, and never read on the
hot path.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from launcher.config import config_dir


def state_path():
    return config_dir() / "state.json"


def history_path():
    return config_dir() / "history.jsonl"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # history is best-effort; never fail a run over it


def record_run(
    profile_name: str,
    results: Iterable[Any] = (),
    duration: float = 0.0,
    kind: str = "run",
) -> None:
    """Update the hot state and append a history record."""
    state = load_state()
    if kind == "run":
        state["last_profile"] = profile_name
        state["last_run"] = datetime.now().isoformat(timespec="seconds")
        counts = state.setdefault("run_counts", {})
        counts[profile_name] = int(counts.get(profile_name, 0)) + 1
    save_state(state)
    _append_history(profile_name, results, duration, kind)


def _step_record(res: Any) -> dict[str, Any]:
    step = res.step
    return {
        "name": step.label,
        "type": step.type,
        "ok": bool(res.ok),
        "skipped": bool(res.skipped),
        "optional": bool(step.optional),
        "detail": str(res.detail)[:400],
        "duration": round(float(getattr(res, "duration", 0.0)), 3),
    }


def _append_history(
    profile_name: str, results: Iterable[Any], duration: float, kind: str
) -> None:
    from launcher import settings

    steps = [_step_record(r) for r in results]
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "profile": profile_name,
        "kind": kind,
        "duration": round(duration, 3),
        "ok": sum(1 for s in steps if s["ok"] and not s["skipped"]),
        "failed": sum(1 for s in steps if not s["ok"] and not s["optional"]),
        "skipped": sum(1 for s in steps if s["skipped"]),
        "steps": steps,
    }
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return
    _trim(max(10, settings.load().history_limit))


def _trim(limit: int) -> None:
    path = history_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= limit:
            return
        path.write_text("\n".join(lines[-limit:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_history(limit: int = 20, profile: str | None = None) -> list[dict[str, Any]]:
    """Most recent runs first."""
    try:
        lines = history_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if profile and str(record.get("profile", "")).lower() != profile.lower():
            continue
        records.append(record)
        if 0 < limit <= len(records):
            break
    return records


def step_stats(profile: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Per-step aggregates across recent runs — surfaces slow and flaky steps."""
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for record in load_history(limit=limit, profile=profile):
        for step in record.get("steps", []):
            key = (record.get("profile", ""), step.get("name", ""))
            bucket = buckets.setdefault(
                key,
                {
                    "profile": key[0], "step": key[1], "type": step.get("type", ""),
                    "runs": 0, "failures": 0, "total_duration": 0.0, "max_duration": 0.0,
                },
            )
            if step.get("skipped"):
                continue
            bucket["runs"] += 1
            bucket["failures"] += 0 if step.get("ok") else 1
            seconds = float(step.get("duration", 0.0))
            bucket["total_duration"] += seconds
            bucket["max_duration"] = max(bucket["max_duration"], seconds)
    stats = []
    for bucket in buckets.values():
        runs = bucket["runs"] or 1
        bucket["avg_duration"] = bucket["total_duration"] / runs
        bucket["failure_rate"] = bucket["failures"] / runs
        stats.append(bucket)
    stats.sort(key=lambda b: (-b["failure_rate"], -b["avg_duration"]))
    return stats


def clear_history() -> None:
    try:
        history_path().unlink()
    except OSError:
        pass
